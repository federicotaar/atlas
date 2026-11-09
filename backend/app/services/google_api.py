"""Chiamate HTTP verso le API Google (OAuth2, Calendar, Gmail).

Modulo sottile e sincrono: i test lo sostituiscono con monkeypatch, cosi le
verifiche non toccano mai un account reale. I messaggi d'errore non includono
mai token ne corpi di risposta."""
import base64

import httpx

from ..errors import AtlasError

TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
GMAIL_DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
GMAIL_MESSAGES_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"

_TIMEOUT = 20.0


def _post(url: str, **kwargs) -> dict:
    try:
        resp = httpx.post(url, timeout=_TIMEOUT, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise AtlasError("google_api_error",
                         f"Chiamata Google fallita ({type(exc).__name__}).",
                         status=502) from exc


def exchange_code(code: str, client_id: str, client_secret: str,
                  redirect_uri: str) -> dict:
    """Scambia il codice OAuth con access+refresh token."""
    return _post(TOKEN_URL, data={
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })


def refresh_access_token(refresh_token: str, client_id: str,
                         client_secret: str) -> dict:
    return _post(TOKEN_URL, data={
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
    })


def _get(url: str, access_token: str, **kwargs) -> dict:
    try:
        resp = httpx.get(url, headers={"Authorization": f"Bearer {access_token}"},
                         timeout=_TIMEOUT, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise AtlasError("google_api_error",
                         f"Chiamata Google fallita ({type(exc).__name__}).",
                         status=502) from exc


def calendar_list_events(access_token: str, time_min_iso: str,
                         max_results: int = 20) -> dict:
    """Sola lettura: scope calendar.readonly."""
    return _get(CALENDAR_EVENTS_URL, access_token, params={
        "timeMin": time_min_iso,
        "maxResults": max_results,
        "singleEvents": "true",
        "orderBy": "startTime",
    })


def gmail_create_draft(access_token: str, raw_b64: str) -> dict:
    """Crea una BOZZA (scope gmail.compose). Una bozza non e un invio:
    non esiste alcuna chiamata a messages.send/drafts.send."""
    return _post(GMAIL_DRAFTS_URL,
                 headers={"Authorization": f"Bearer {access_token}"},
                 json={"message": {"raw": raw_b64}})


def gmail_delete_draft(access_token: str, draft_id: str) -> None:
    try:
        resp = httpx.delete(f"{GMAIL_DRAFTS_URL}/{draft_id}",
                            headers={"Authorization": f"Bearer {access_token}"},
                            timeout=_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise AtlasError("google_api_error",
                         f"Eliminazione bozza fallita ({type(exc).__name__}).",
                         status=502) from exc


def gmail_list_messages(access_token: str, query: str = "",
                        max_results: int = 10) -> dict:
    """Sola lettura della inbox (scope gmail.readonly): id, mittente,
    oggetto, data e snippet dei messaggi piu' recenti. Nessuna lettura del
    corpo: per quello serve gmail_get_message sul singolo id."""
    params: dict = {"maxResults": max_results}
    if query:
        params["q"] = query
    data = _get(GMAIL_MESSAGES_URL, access_token, params=params)
    messages = []
    for stub in data.get("messages", [])[:max_results]:
        meta = _get(f"{GMAIL_MESSAGES_URL}/{stub['id']}", access_token,
                    params={"format": "metadata",
                            "metadataHeaders": ["From", "Subject", "Date"]})
        headers = {h.get("name", "").lower(): h.get("value", "")
                   for h in meta.get("payload", {}).get("headers", [])}
        messages.append({
            "id": stub.get("id", ""),
            "thread_id": stub.get("threadId", ""),
            "from": headers.get("from", ""),
            "subject": headers.get("subject", ""),
            "date": headers.get("date", ""),
            "snippet": meta.get("snippet", ""),
        })
    return {"messages": messages}


def gmail_get_message(access_token: str, message_id: str) -> dict:
    """Sola lettura (scope gmail.readonly): corpo testuale completo di un
    messaggio. Estrae la prima parte text/plain, altrimenti text/html
    grezza; allegati mai scaricati."""
    data = _get(f"{GMAIL_MESSAGES_URL}/{message_id}", access_token,
                params={"format": "full"})
    payload = data.get("payload", {})
    headers = {h.get("name", "").lower(): h.get("value", "")
               for h in payload.get("headers", [])}

    def _b64(part: dict) -> str:
        raw = part.get("body", {}).get("data", "")
        if not raw:
            return ""
        try:
            return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode(
                "utf-8", errors="replace")
        except (ValueError, TypeError):
            return ""

    body = ""
    parts = payload.get("parts") or []
    for wanted in ("text/plain", "text/html"):
        candidates = ([p for p in parts if p.get("mimeType") == wanted]
                      or ([payload] if payload.get("mimeType") == wanted else []))
        for part in candidates:
            body = _b64(part)
            if body:
                break
        if body:
            break
    return {
        "id": data.get("id", message_id),
        "thread_id": data.get("threadId", ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "body": body,
    }
