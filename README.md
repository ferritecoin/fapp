# FEXT Android client

An end-to-end encrypted messenger built with [Kivy](https://kivy.org). This
repository packages the single-file FEXT client (`main.py`) into an installable
Android `.apk` using [Buildozer](https://buildozer.readthedocs.io) /
[python-for-android](https://python-for-android.readthedocs.io).

The whole client is one auditable file. Its security model is transparency: no
plaintext ever leaves the process — messages are signed (secp256k1 ECDSA),
sealed (ephemeral ECDH → HKDF-SHA256 → AES-256-GCM), and only opaque ciphertext
is transmitted.

## What's in here

| File | Purpose |
| --- | --- |
| `main.py` | The complete FEXT client (Buildozer's entry point **must** be `main.py`). |
| `buildozer.spec` | Android packaging configuration (requirements, permissions, ABIs, API levels). |
| `.github/workflows/build-apk.yml` | CI that builds the APK and uploads it as a downloadable artifact. |

## Getting the APK

### Option A — download it from CI (no local setup)

Every push builds the APK on GitHub Actions:

1. Open the **Actions** tab → the latest **Build Android APK** run.
2. Download the **`fext-debug-apk`** artifact from the run summary.
3. Unzip it — inside is `fext-3.0.0-*-debug.apk`.

### Option B — build it locally

Requirements: Linux (or WSL) with Python 3, a JDK, and the usual build
toolchain (`git`, `zip`, `unzip`, `gcc`, `make`, `autoconf`, `libtool`).
Buildozer downloads the Android SDK/NDK automatically on first run.

```bash
python3 -m pip install --user buildozer cython
buildozer android debug          # produces bin/fext-3.0.0-*-debug.apk
```

The first build takes a while (it downloads the SDK/NDK and compiles the
native requirements such as `cryptography` and `pillow`). Subsequent builds are
much faster.

For a signed release build, configure signing and run `buildozer android
release` — see the [Buildozer docs](https://buildozer.readthedocs.io/en/latest/).

## Installing on a phone

1. Copy the `.apk` to your Android device (USB, cloud, etc.).
2. Enable **Install unknown apps** for the app you open the file with.
3. Tap the `.apk` and install.

Or install over ADB from a computer:

```bash
adb install -r bin/fext-3.0.0-*-debug.apk
```

## Configuration notes

- **Requirements** (from `buildozer.spec`): `kivy`, `pillow`, `requests`,
  `websocket-client`, `cryptography`, `qrcode`, plus `pyjnius`/`android` for the
  app-private storage path. `sqlite3` ships with python-for-android.
- **Permissions**: `INTERNET` and `ACCESS_NETWORK_STATE` — the app is a
  networked messenger and shows an online/offline status ribbon.
- **ABIs**: `arm64-v8a` (all modern phones) and `armeabi-v7a` (older/32-bit).
- The relay server address is pinned in `main.py` (`SERVER_URL`).
