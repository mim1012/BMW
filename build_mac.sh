#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON="$ROOT/.venv/bin/python"
else
    PYTHON="${PYTHON:-python3}"
fi

if [[ -f "$ROOT/requirements.txt" ]]; then
    "$PYTHON" -m pip install -r "$ROOT/requirements.txt"
fi

if ! "$PYTHON" -c "import PyInstaller" >/dev/null 2>&1; then
    "$PYTHON" -m pip install pyinstaller
fi

add_data=()
for file in scanned_models.json form_fields.json; do
    path="$ROOT/$file"
    if [[ -f "$path" ]]; then
        add_data+=(--add-data "$path:.")
    fi
done

shopt -s nullglob
for path in "$ROOT"/form_fields_*.json; do
    add_data+=(--add-data "$path:.")
done
shopt -u nullglob

pyinstaller_args=(
    --noconfirm
    --clean
    --onedir
    --windowed
    --name "BMW-AutoBuyer"
)

if [[ -n "${PYINSTALLER_TARGET_ARCH:-}" ]]; then
    pyinstaller_args+=(--target-arch "$PYINSTALLER_TARGET_ARCH")
fi

"$PYTHON" -m PyInstaller \
    "${pyinstaller_args[@]}" \
    --collect-all playwright \
    --collect-all greenlet \
    --collect-all pyee \
    "${add_data[@]}" \
    "$ROOT/gui_app.py"

echo ""
echo "Build complete: $ROOT/dist/BMW-AutoBuyer.app"
echo "Note: target Mac must have Google Chrome installed."
echo "Optional: set PYINSTALLER_TARGET_ARCH=arm64, x86_64, or universal2 before running this script."
