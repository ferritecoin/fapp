#!/usr/bin/env bash
# Build the FEXT desktop client into a distributable executable with Nuitka.
#
#   ./build_desktop.sh            -> standalone folder  (dist/fext_client_<ver>.dist/fext)
#   ONEFILE=1 ./build_desktop.sh  -> single executable  (dist/fext)
#
# The desktop client IS ../main.py — the same single file the APK ships. It
# detects the platform at import time (IS_MOBILE), so no desktop-specific fork
# exists or is needed. This script copies it under a versioned name purely so
# the Nuitka output directory is named for the release.
#
# Prerequisites (in a venv):
#   pip install nuitka kivy requests urllib3 charset-normalizer idna certifi \
#               websocket-client cryptography qrcode pillow
#   Linux also needs a C compiler and patchelf (and libmtdev1 to silence a
#   harmless Kivy touch-provider probe at startup).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PY="${PY:-python3}"
OUT="${OUT:-$HERE/dist}"
NAME="${NAME:-fext}"

VER="$(grep -E '^version' "$ROOT/buildozer.spec" | head -1 | sed -E 's/.*=[[:space:]]*//')"
SRC="${SRC:-}"
if [ -z "$SRC" ]; then
  SRC="$HERE/build/fext_client_v${VER}.py"
  mkdir -p "$HERE/build"
  cp "$ROOT/main.py" "$SRC"
fi

MODE=(--standalone)
[ "${ONEFILE:-0}" = "1" ] && MODE=(--onefile)

exec "$PY" -m nuitka \
  "${MODE[@]}" \
  --enable-plugin=kivy \
  \
  `# certifi ships a data file (cacert.pem). The client calls certifi.where()` \
  `# for BOTH https and the websocket TLS verification, so without this the app` \
  `# builds fine and then cannot open a secure connection at runtime.` \
  --include-package-data=certifi \
  \
  `# qrcode picks its image backend by dynamic import at call time, so` \
  `# Nuitka cannot see it from static analysis. The Settings screen renders` \
  `# QR codes; miss this and that screen raises instead of drawing.` \
  --include-package=qrcode \
  --include-package=PIL \
  \
  `# websocket-client is imported in a try/except (the client degrades to` \
  `# HTTP polling without it). Nuitka honours that and would silently drop` \
  `# it, turning realtime push off in the shipped build.` \
  --include-package=websocket \
  \
  --include-package=requests \
  --include-package=urllib3 \
  --include-package=cryptography \
  \
  --output-dir="$OUT" \
  --output-filename="$NAME" \
  --company-name=FEXT \
  --product-name="FEXT Messenger" \
  --product-version="$VER" \
  --file-description="FEXT end-to-end encrypted messenger" \
  --assume-yes-for-downloads \
  --remove-output \
  "$SRC"
