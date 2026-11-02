#!/usr/bin/env bash
# Build the Ferritext desktop client into an executable with Nuitka.
#
#   ./build_desktop.sh            -> standalone folder  (dist/ferritext.dist/)
#   ONEFILE=1 ./build_desktop.sh  -> one executable     (dist/fext, or NAME)
#
# CI builds the Windows release this way (.github/workflows/build-apk.yml):
#   ONEFILE=1 NAME=Ferritext-<ver>-windows.exe bash desktop/build_desktop.sh
#
# The desktop client IS ../main.py — the same single file the APK ships. It
# detects the platform at import time (IS_MOBILE), so no desktop-specific fork
# exists or is needed. This script copies it under a fixed name, so Nuitka's
# build folders keep their paths from one version to the next and its cache
# of compiled C (kept by CI) stays valid.
#
# Prerequisites (in a venv):
#   pip install nuitka zstandard kivy requests urllib3 charset-normalizer \
#               idna certifi websocket-client cryptography qrcode pillow
#   and, to carry what they add on a computer (README, Downloads), any of:
#   pip install sounddevice numpy av pyzbar pyogg
#   Linux also needs a C compiler and patchelf (and libmtdev1 to silence a
#   harmless Kivy touch-provider probe at startup); Windows, Visual Studio's
#   C++ build tools.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PY="${PY:-python3}"
OUT="${OUT:-$HERE/dist}"
NAME="${NAME:-fext}"

VER="$(grep -E '^version' "$ROOT/buildozer.spec" | head -1 | sed -E 's/.*=[[:space:]]*//')"
SRC="${SRC:-}"
if [ -z "$SRC" ]; then
  SRC="$HERE/build/ferritext.py"
  mkdir -p "$HERE/build"
  cp "$ROOT/main.py" "$SRC"
fi

MODE=(--mode=standalone)
if [ "${ONEFILE:-0}" = "1" ]; then
  # Unpacked once per version into the user's cache folder, then reused:
  # only the first start after an update waits for it.
  MODE=(--mode=onefile "--onefile-tempdir-spec={CACHE_DIR}/Ferritext/{VERSION}")
fi

# The Ferritext mark on the executable itself (Explorer, a pinned taskbar
# button, the Dock); the running window sets its own from data/ as well.
# On Windows, no console window opens beside the app.
PLATFORM=()
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) PLATFORM=(--windows-icon-from-ico="$ROOT/data/icon_desktop.ico"
                                  --windows-console-mode=disable) ;;
  Darwin)               PLATFORM=(--macos-app-icon="$ROOT/data/icon_desktop.png") ;;
esac

# What each adds on a computer is in the README; the app does without any
# of them. They are imported inside functions, in try/except, so each module the
# app imports is named here, and only when it is installed: the build takes
# what it is given. (The module, not its whole package: that would bring the
# package's tests along.)
EXTRAS=()
for module in sounddevice numpy av pyzbar.pyzbar pyogg; do
  if "$PY" -c "import importlib.util, sys; sys.exit(not importlib.util.find_spec('${module%%.*}'))"; then
    EXTRAS+=(--include-module="$module")
  fi
done

# The licences of everything it carries, inside it as licences/.
"$PY" "$HERE/collect_licences.py" "$HERE/build/licences"

exec "$PY" -m nuitka \
  "${MODE[@]}" \
  \
  `# certifi ships a data file (cacert.pem). The client calls certifi.where()` \
  `# for BOTH https and the websocket TLS verification, so without this the app` \
  `# builds fine and then cannot open a secure connection at runtime.` \
  --include-package-data=certifi \
  \
  `# The fonts (the serif carries the emoji), the mark and the window icons,` \
  `# which main.py finds in data/ next to __file__. Nuitka cannot see a path` \
  `# built at runtime, so the folder has to be named explicitly.` \
  --include-data-dir="$ROOT/data=data" \
  --include-data-dir="$HERE/build/licences=licences" \
  ${PLATFORM[@]+"${PLATFORM[@]}"} \
  \
  `# qrcode picks its image backend by dynamic import at call time, so` \
  `# Nuitka cannot see it from static analysis. The Settings screen renders` \
  `# QR codes; miss this and that screen raises instead of drawing.` \
  --include-package=qrcode \
  --include-package=PIL \
  `# ...but not Pillow's Tk viewer: the app has no Tk.` \
  --nofollow-import-to=tkinter \
  \
  `# On Windows, Kivy imports each package in its kivy_deps namespace by` \
  `# its bare name, which a compiled package does not answer to: import` \
  `# kivy fails. They only put their DLL folders on the search path; the` \
  `# build carries the DLLs beside the executable instead (Nuitka's Kivy` \
  `# plugin SDL's and GLEW's, nuitka-package.config.yml ANGLE's).` \
  --nofollow-import-to=kivy_deps \
  \
  `# websocket-client is imported in a try/except (the client degrades to` \
  `# HTTP polling without it). Nuitka honours that and would silently drop` \
  `# it, turning realtime push off in the shipped build.` \
  --include-package=websocket \
  \
  --include-package=requests \
  --include-package=urllib3 \
  --include-package=cryptography \
  ${EXTRAS[@]+"${EXTRAS[@]}"} \
  `# PyOgg's Opus DLL (calls in Opus) and ANGLE's, which Nuitka does not` \
  `# find by itself.` \
  --user-package-configuration-file="$HERE/nuitka-package.config.yml" \
  \
  --output-dir="$OUT" \
  --output-filename="$NAME" \
  --company-name=Ferritext \
  --product-name="Ferritext" \
  --file-version="$VER" \
  --product-version="$VER" \
  --file-description="Ferritext end-to-end encrypted messenger" \
  --assume-yes-for-downloads \
  --remove-output \
  "$SRC"
