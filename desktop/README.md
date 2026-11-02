# Ferritext desktop client (Nuitka)

The desktop client is **`../main.py` — the exact same file the APK ships.**
There is no desktop fork. The client detects its platform at import time
(`IS_MOBILE`) and adapts where the platforms genuinely differ:

| | Android (APK) | Desktop |
| --- | --- | --- |
| Data directory | app-private storage (via `pyjnius`) | `~/.fext` |
| Request timeout | 20 s (mobile radios) | 12 s |
| Keyboard / window | Android IME, fullscreen | desktop window |
| Selecting messages | long-press | right-click (or long-press); drag inside a message to highlight part of it |
| Sharing | Android share sheet | copied to the clipboard |
| Photos | system photo picker | file chooser |
| Camera | the phone's camera app, writing straight into private storage | a webcam, through OpenCV when it is installed (`pip install opencv-python`) |
| Voice notes | recorded as AAC; played in the app | recorded as WAV — built in on Windows; `arecord`, `parec` or `ffmpeg` on Linux; `ffmpeg` on macOS; or the `sounddevice` package anywhere — and played in the system's audio player |
| Notifications | system notifications, plus a background service | status line while the app is open |

Everything else — the crypto core, the wire protocol, the relay discovery, the
local SQLite store — is byte-for-byte the same code, which is the point: a
desktop build can never drift from the phone build.

For distribution, every GitHub Release carries two desktop files. The
Windows executable, `Ferritext-<version>-windows.exe`, is this folder's
Nuitka build, made by CI on a Windows runner (below). And
`Ferritext-<version>-desktop.py`:
`main.py` at that release's tag with the fonts and window icons appended as
comment lines, so the one file is all a computer needs (Kivy draws each label
in a single font, so without the bundled serif there would be no emoji). On
first start they are unpacked into `~/.fext/assets`, and each is checked
against its SHA-256 on every start. To confirm a download is exactly the
source, run `python3 tools/bundle_desktop.py --verify Ferritext-<version>-desktop.py`
from a checkout of the tag.

## Building

```bash
python3 -m venv env && . env/bin/activate
pip install nuitka zstandard kivy requests urllib3 charset-normalizer idna \
            certifi websocket-client cryptography qrcode pillow
pip install sounddevice numpy av pyzbar pyogg   # optional: what they add
./build_desktop.sh              # -> desktop/dist/ferritext.dist/
ONEFILE=1 ./build_desktop.sh    # -> desktop/dist/fext   (single file)
python3 smoke_exe.py dist/fext  # start it twice in an empty home, as a person would
```

Linux also needs `gcc` and `patchelf`; Windows, Git Bash and Visual
Studio's C++ build tools (CI: `build-windows` in `build-apk.yml`, with
`ONEFILE=1 NAME=Ferritext-<version>-windows.exe`). Overrides: `PY=`,
`OUT=`, `NAME=`, `SRC=`.

A one-file build unpacks itself once per version into the user's cache
folder (`%LOCALAPPDATA%\Ferritext\<version>` on Windows) and starts from
there afterwards; it keeps its data in `~/.fext`, as the desktop file does.
With the optional packages it is about 76 MB on Windows and 95 MB on
Linux.

## Why the Nuitka flags are what they are

These are load-bearing; drop any one and you get a build that compiles
cleanly and then misbehaves at runtime, which is the worst failure mode to ship:

- `--include-package-data=certifi` — the client calls `certifi.where()` for
  both HTTPS and websocket TLS verification. Without the data file the binary
  builds and then cannot open a secure connection at all.
- `--include-package=websocket` — `websocket-client` is imported inside a
  `try/except` (the client degrades to HTTP polling without it). Nuitka honours
  that and silently drops it, so realtime push would just be *off* in the
  shipped build with no error anywhere.
- `--include-package=qrcode` + `--include-package=PIL` — `qrcode` picks its
  image backend by dynamic import at call time, invisible to static analysis.
  The Settings screen renders a QR code; miss these and that screen raises.
- Nuitka's Kivy plugin, always on, pulls in Kivy's providers, its data
  files and SDL's and GLEW's DLLs. It does not take ANGLE, whose DLLs sit
  outside the package: `nuitka-package.config.yml` does, and PyOgg's Opus
  DLL.
- `--nofollow-import-to=kivy_deps` — on Windows, Kivy imports each package
  in its `kivy_deps` namespace by its bare name, which a compiled package
  does not answer to, so `import kivy` fails and the app never starts.
  Those packages only put their DLL folders on the search path, and the
  build carries the DLLs beside the executable instead.
- The optional packages are imported inside functions, in `try/except`, so
  each module the app imports is named (`--include-module`) when it is
  installed; naming whole packages would bring their tests along.
- `--include-data-dir=…/licences=licences` — every licence of what the
  executable carries, collected from the build's environment by
  `collect_licences.py`, with where to find the source of the LGPL parts.

## Verification

Every build: CI starts the Windows executable twice with `smoke_exe.py`
(up, its window drawn, still running) and keeps its screenshots. With
Kivy 2.3.1, Nuitka 4.3.4 and Python 3.11, 15.1.1's was up in 6.2 s the
first time, which unpacks it, and in 2.0 s after; the Linux one-file
build passes the same 6 checks (about 3 s, then 2 s). On GitHub's 2-core
Windows runner a build takes about an hour with nothing cached, and 13
minutes with Nuitka's cache, which CI keeps.

Earlier, against Kivy 2.3.1 / cryptography 50.0.1, Nuitka 4.2.1, Python 3.11:

- `smoke_desktop.py main.py` — imports under real Kivy; desktop
  platform detected; `~/.fext` data dir; 12 s timeout; `seal()` round-trips on
  modern cryptography (the v8.0.1 HKDF-backend fix is version-agnostic —
  it works on both 2.8 and 50.x); `App.build()` constructs the UI; both screens
  registered; first-run path opens the welcome dialog.
- Nuitka standalone build succeeds; log confirms `certifi/cacert.pem` and the
  38 Kivy data files are included; the bundle carries `PIL`, `cryptography`,
  `_sqlite3.so`, `_ssl.so`, `libsqlite3.so.0`.
- Compiled binary launches, stays running, and creates `~/.fext`.
- **Networking from inside the executable**: pointed at a stub relay via
  `FEXT_SERVER_HOST=127.0.0.1:51866`, the binary probed `https://` → fell back
  to `http://`, logged the transport, and cached
  `{"base": "http://127.0.0.1:51866"}` to `~/.fext/transport.json`.

A `libmtdev.so.1` traceback at startup on Linux is benign — it is Kivy probing
for a multitouch device provider. Install `libmtdev1` to silence it.

## Environment variables

| Variable | Effect |
| --- | --- |
| `FEXT_SERVER_HOST` | Relay `host:port` (default `118.189.201.104:50607`). |
| `FEXT_SERVER_URL` | Full base URL; overrides scheme discovery entirely. |
| `FEXT_REQUIRE_TLS` | Set to `1` to fail closed instead of falling back to cleartext. |
