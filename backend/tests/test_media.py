"""Stage 6: unit test di app.channels.media - nessuna rete (post iniettata)."""
from app.channels.media import GROQ_TRANSCRIBE_URL, Attachment, MediaProcessor, _safe_name, save_inbound


def test_safe_name_strips_pathological_chars():
    assert _safe_name("../../etc/passwd") == "etc_passwd"
    assert _safe_name("nota vocale (1).ogg") == "nota_vocale_1_.ogg"
    assert _safe_name("") == "allegato.bin"
    assert _safe_name("...") == "allegato.bin"


def test_save_inbound_sanitizes_and_dedupes(tmp_path):
    a = save_inbound(tmp_path, "telegram", "111", "nota vocale.ogg", b"uno")
    b = save_inbound(tmp_path, "telegram", "111", "nota vocale.ogg", b"due")
    assert a != b and a.read_bytes() == b"uno" and b.read_bytes() == b"due"
    assert " " not in a.name
    # resta dentro la sandbox del mittente
    assert a.parent == tmp_path / "data" / "inbound" / "telegram" / "111"


def test_transcribe_http_error_returns_empty(tmp_path):
    media = MediaProcessor(tmp_path, groq_key="k",
                           post=lambda url, **kw: (500, {"error": "giu'"}))
    note = media.process(Attachment("voice", "n.ogg", "audio/ogg", b"d"),
                         "telegram", "111").note
    assert "trascrizione non disponibile" in note


def test_transcribe_exception_returns_empty(tmp_path):
    def giu(url, **kw):
        raise ConnectionError("rete giu'")

    media = MediaProcessor(tmp_path, groq_key="k", post=giu)
    note = media.process(Attachment("voice", "n.ogg", "audio/ogg", b"d"),
                         "telegram", "111").note
    assert "trascrizione non disponibile" in note


def test_transcribe_sends_model_and_file(tmp_path):
    seen = {}

    def post(url, *, headers=None, files=None, data=None, json=None,
             timeout=60.0):
        seen.update(url=url, headers=headers, files=files, data=data)
        return 200, {"text": "  trascrizione  "}

    media = MediaProcessor(tmp_path, groq_key="k", post=post)
    note = media.process(Attachment("voice", "n.ogg", "audio/ogg", b"d"),
                         "telegram", "111").note
    assert '"trascrizione"' in note  # strip degli spazi
    assert seen["url"] == GROQ_TRANSCRIBE_URL
    assert seen["data"]["model"].startswith("whisper-")
    assert seen["files"]["file"][1] == b"d"


def test_inline_text_size_limit(tmp_path):
    media = MediaProcessor(tmp_path, inline_max=10)
    big = Attachment("file", "n.txt", "text/plain", b"x" * 11)
    note = media.process(big, "telegram", "111").note
    assert "contenuto:" not in note and "salvato in" in note
    small = Attachment("file", "n.txt", "text/plain", b"x" * 10)
    assert "contenuto:" in media.process(small, "telegram", "111").note


def test_describe_bad_payload_returns_empty(tmp_path):
    media = MediaProcessor(tmp_path, gemini_key="k",
                           post=lambda url, **kw: (200, {"strano": True}))
    note = media.process(Attachment("photo", "f.jpg", "image/jpeg", b"d"),
                         "telegram", "111").note
    assert "descrizione non disponibile" in note
