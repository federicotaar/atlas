"""Impostazioni modello per utente (BYOK): scelta provider/modello, chiave
API dell'utente cifrata in locale, consenso cloud esplicito.

La chiave non viene MAI restituita (solo has_key) e non finisce nei log:
il body della richiesta non viene loggato e l'endpoint risponde solo con
metadati."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import User
from ..security import get_current_user
from ..services import llm_providers

router = APIRouter(tags=["settings"])


class ModelSettingsIn(BaseModel):
    provider: str
    model: str = ""
    base_url: str = ""
    api_key: str | None = None  # write-only: mai restituita
    consent_cloud: bool = False


class ModelSettingsOut(BaseModel):
    provider: str
    model: str
    base_url: str
    has_key: bool
    consent_cloud: bool
    provider_kind: str
    needs_key: bool
    providers: list[dict]


def _out(pref) -> ModelSettingsOut:
    meta = llm_providers.PROVIDERS[pref.provider]
    return ModelSettingsOut(
        provider=pref.provider,
        model=pref.model or "",
        base_url=pref.base_url or "",
        has_key=bool(pref.api_key_enc),
        consent_cloud=bool(pref.consent_cloud),
        provider_kind=meta.kind,
        needs_key=meta.needs_key,
        providers=[
            {"id": m.id, "label": m.label, "kind": m.kind, "needs_key": m.needs_key,
             "needs_base_url": m.needs_base_url, "default_model": m.default_model}
            for m in llm_providers.PROVIDERS.values()
        ],
    )


@router.get("/settings/model", response_model=ModelSettingsOut)
def get_model_settings(db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)) -> ModelSettingsOut:
    pref = llm_providers.get_pref(db, user.id)
    if pref is None:
        # default di fabbrica: estrattivo locale, niente chiavi (istanza transitoria, non salvata)
        from ..models import UserModelSetting
        pref = UserModelSetting(user_id=user.id, provider="extractive")
    return _out(pref)


@router.put("/settings/model", response_model=ModelSettingsOut)
def put_model_settings(body: ModelSettingsIn, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)) -> ModelSettingsOut:
    pref = llm_providers.save_pref(db, get_settings(), user.id, body.provider, body.model,
                                   body.base_url, body.api_key, body.consent_cloud)
    return _out(pref)


class ModelsOut(BaseModel):
    models: list[str]


@router.get("/settings/models", response_model=ModelsOut)
def list_provider_models(provider: str, db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)) -> ModelsOut:
    settings = get_settings()
    pref = llm_providers.get_pref(db, user.id)
    api_key = ""
    base_url = ""
    if pref is not None and pref.provider == provider:
        base_url = pref.base_url
        if pref.api_key_enc:
            from ..services.credentials import decrypt_secret
            api_key = decrypt_secret(pref.api_key_enc, settings.credentials_key)
    return ModelsOut(models=llm_providers.list_models(provider, base_url, api_key, settings))
