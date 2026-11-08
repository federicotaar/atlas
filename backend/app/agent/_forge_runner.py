"""Runner dei tool auto-generati: gira DENTRO bwrap, mai nell'agente.

Uso: python /runner.py inspect <nome>   -> stampa JSON con l'interfaccia
     python /runner.py invoke  <nome>   -> legge args JSON da stdin,
                                           stampa JSON {"ok", "result"}
Vede solo /work (tools_generated): niente rete, segreti o file protetti.
"""
from __future__ import annotations

import importlib
import json
import sys


def _load(name: str):
    sys.path.insert(0, "/work")
    return importlib.import_module(name)


def main() -> None:
    mode, name = sys.argv[1], sys.argv[2]
    try:
        mod = _load(name)
    except Exception as exc:  # noqa: BLE001 - riportato al chiamante
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return
    if mode == "inspect":
        out = {}
        for attr in ("name", "description", "parameters"):
            if hasattr(mod, attr):
                out[attr] = getattr(mod, attr)
        if hasattr(mod, "run"):
            out["run"] = True
        print(json.dumps(out, ensure_ascii=False, default=repr))
        return
    args = json.loads(sys.stdin.read() or "{}")
    try:
        result = mod.run(args, None)  # niente contesto dell'agente
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False,
                          "error": f"{type(exc).__name__}: {exc}"}))
        return
    print(json.dumps({"ok": True, "result": result},
                     ensure_ascii=False, default=repr))


if __name__ == "__main__":
    main()
