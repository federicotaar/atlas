#!/usr/bin/env bash
# Build del sidecar backend per Tauri (PyInstaller onefile).
# Uso: backend/scripts/build_sidecar.sh  (dalla root del repo)
# Produce: desktop/bin/atlas-backend-<target-triple>[.exe]
set -euo pipefail
cd "$(dirname "$0")/.."
pip install -e ".[dev]" pyinstaller >/dev/null
if command -v rustc >/dev/null 2>&1; then
  TARGET="$(rustc -vV | sed -n 's/^host: //p')"
else
  case "$(uname -sm)" in
    "Linux x86_64") TARGET="x86_64-unknown-linux-gnu";;
    "Linux aarch64") TARGET="aarch64-unknown-linux-gnu";;
    "Darwin arm64") TARGET="aarch64-apple-darwin";;
    "Darwin x86_64") TARGET="x86_64-apple-darwin";;
    *) echo "rustc assente e piattaforma non mappata: installa Rust" >&2; exit 1;;
  esac
fi
pyinstaller --onefile --name "atlas-backend-${TARGET}" \
  --paths . \
  --hidden-import sqlite_vec \
  --collect-all sqlite_vec \
  desktop_main.py
mkdir -p ../desktop/bin
mv "dist/atlas-backend-${TARGET}"* ../desktop/bin/
echo "Sidecar in desktop/bin/atlas-backend-${TARGET}"
