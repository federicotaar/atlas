#!/usr/bin/env python3
"""ATLAS - agente locale. Avvio CLI: `python main.py` (spec sez. 11).

Setup completo da checkout pulito: vedi README (sezione "Agente locale").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app.agent_cli import main  # noqa: E402

if __name__ == "__main__":
    main()
