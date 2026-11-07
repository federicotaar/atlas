"""E2E WebKitGTK reale: flusso signup ATLAS nel motore WebKit2GTK.

Carica la build frontend (vite preview :4173) dentro una WebView WebKitGTK vera
e guida il flusso completo: scelta registrazione -> email -> challenge Turnstile
(dummy key esplicita da build E2E) -> codice -> password -> chat.

Prova che in WebKitGTK il widget Turnstile COMPLETA la challenge (il bottone
"Invia codice" si abilita solo dopo il callback col token), non solo che carica.
Uso: dbus-run-session + X server attivo, mock API su :8000, preview su :4173.
"""

import json
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gtk, WebKit2  # noqa: E402

BASE = "http://127.0.0.1:4173/"
TIMEOUT = 120

Gtk.init([])
win = Gtk.Window()
win.set_default_size(1100, 760)
view = WebKit2.WebView()
win.add(view)
win.show_all()


def run_js(script: str) -> str:
    out = {}
    loop = GLib.MainLoop()

    def cb(v, res):
        try:
            r = v.run_javascript_finish(res)
            out["v"] = r.get_js_value().to_string()
        except Exception as e:  # noqa: BLE001 - vogliamo il messaggio nel log
            out["v"] = f"__error__:{e}"
        loop.quit()

    view.run_javascript(script, None, cb)
    loop.run()
    return out.get("v", "")


PROBE = r"""
(() => {
  const btns = [...document.querySelectorAll('button')].map(b => ({t: b.textContent.trim(), d: b.disabled}));
  const inputs = [...document.querySelectorAll('input')].map(i => ({p: i.placeholder, dis: i.disabled}));
  return JSON.stringify({
    text: document.body.innerText.slice(0, 300),
    btns, inputs,
    tsIframe: !!document.querySelector('iframe[src*="challenges.cloudflare.com"]'),
  });
})()
"""


def probe() -> dict:
    return json.loads(run_js(PROBE))


def js_click(text: str) -> None:
    run_js(
        "(() => { const b = [...document.querySelectorAll('button')]"
        f".find(b => b.textContent.trim() === {json.dumps(text)} && !b.disabled);"
        " if (b) { b.click(); return 'ok'; } return 'missing'; })()"
    )


def js_set_input(placeholder: str, value: str) -> None:
    run_js(
        f"(() => {{ const i = [...document.querySelectorAll('input')].find(i => i.placeholder === {json.dumps(placeholder)});"
        " if (!i) return 'missing';"
        " const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;"
        f" s.call(i, {json.dumps(value)}); i.dispatchEvent(new Event('input', {{ bubbles: true }}));"
        " return 'ok'; })()"
    )


steps = []


def wait_for(desc, cond, timeout=TIMEOUT):
    t0 = time.time()
    st = {}
    while time.time() - t0 < timeout:
        st = probe()
        if cond(st):
            steps.append(f"OK   {desc} ({time.time() - t0:.1f}s)")
            return st
        time.sleep(0.5)
    steps.append(f"FAIL {desc}: {json.dumps(st)[:500]}")
    print("\n".join(steps))
    sys.exit(1)


view.load_uri(BASE)

wait_for("chooser caricato", lambda st: any(b["t"] == "Non ho un account" for b in st["btns"]))
js_click("Non ho un account")
wait_for("form registrazione", lambda st: any(i["p"] == "email" for i in st["inputs"]))
js_set_input("email", "webkit.e2e@example.com")
wait_for("turnstile iframe presente", lambda st: st["tsIframe"])
wait_for(
    "turnstile COMPLETATO in WebKitGTK (Invia codice abilitato dal token)",
    lambda st: any(b["t"] == "Invia codice" and not b["d"] for b in st["btns"]),
)
js_click("Invia codice")
wait_for(
    "codice inviato (campo codice attivo)",
    lambda st: any(i["p"] == "codice a 6 cifre" and not i["dis"] for i in st["inputs"]),
)
js_set_input("codice a 6 cifre", "123456")
js_click("Verifica codice")
wait_for("password step (email verificata)", lambda st: "Email verificata" in st["text"])
js_set_input("scegli una password (min 8 caratteri)", "password-segreta-1")
js_click("Crea account")
wait_for("registrazione completata: chat visibile", lambda st: "Come posso aiutarti" in st["text"], timeout=60)

print("\n".join(steps))
print("WEBKITGTK-E2E: PASS")
sys.exit(0)
