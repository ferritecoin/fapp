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
| `.github/workflows/build-apk.yml` | CI that builds a signed release APK and publishes it to a GitHub Release. |

## Getting the APK

### Option A — download the release from GitHub (no local setup)

Every push builds a **signed release APK** on GitHub Actions and publishes it to
a GitHub Release:

1. Open the repository's **Releases** page (or the **`v3.0.0`** tag).
2. Download `fext-3.0.0-*-release.apk` from the release assets.

The same APK is also attached to each Actions run as the **`fext-release-apk`**
artifact (Actions tab → latest run → Artifacts).

### Option B — build it locally

Requirements: Linux (or WSL) with Python 3, a JDK, and the usual build
toolchain (`git`, `zip`, `unzip`, `gcc`, `make`, `autoconf`, `automake`,
`libtool`, `libltdl-dev`). Buildozer downloads the Android SDK/NDK automatically
on first run.

```bash
python3 -m pip install --user buildozer cython
buildozer android debug          # produces bin/fext-3.0.0-*-debug.apk
```

The first build takes a while (it downloads the SDK/NDK and compiles the
native requirements such as `cryptography` and `pillow`). Subsequent builds are
much faster.

## Release signing

The CI publishes a properly release-signed, installable APK. How it's signed
depends on whether you've provided a keystore:

- **Persistent key (recommended for real distribution).** Generate a keystore
  once and add it as repository secrets so every build is signed with the *same*
  key — this is what lets future versions install as an update over an existing
  install (and is required if you ever move to the Play Store):

  ```bash
  keytool -genkeypair -v -keystore fext-release.keystore -alias fext \
    -keyalg RSA -keysize 2048 -validity 10000
  base64 -w0 fext-release.keystore    # value for the FEXT_KEYSTORE_B64 secret
  ```

  Then, under **Settings → Secrets and variables → Actions**, add:
  `FEXT_KEYSTORE_B64`, `FEXT_KEYSTORE_PASSWORD`, `FEXT_KEY_ALIAS`,
  `FEXT_KEY_PASSWORD`. Keep the keystore file itself somewhere safe and private
  — losing it means you can never ship an update that installs over this one.

- **No secrets set.** The workflow generates a throwaway keystore per build, so
  you still get a valid, installable release APK — but each build's signature
  differs, so a later build won't update-install over an earlier one. Fine for
  first tests; switch to a persistent key before distributing widely.

A signing key is **never committed to this repository.**

## Installing on a phone

1. Copy the `.apk` to your Android device (USB, cloud, etc.).
2. Enable **Install unknown apps** for the app you open the file with.
3. Tap the `.apk` and install.

Or install over ADB from a computer:

```bash
adb install -r fext-3.0.0-*-release.apk
```

## Configuration notes

- **Requirements** (from `buildozer.spec`): `kivy`, `pillow`, `requests`,
  `websocket-client`, `cryptography`, `qrcode`, plus `pyjnius`/`android` for the
  app-private storage path. `sqlite3` ships with python-for-android.
- **Permissions**: `INTERNET` and `ACCESS_NETWORK_STATE` — the app is a
  networked messenger and shows an online/offline status ribbon.
- **ABIs**: `arm64-v8a` (all modern phones) and `armeabi-v7a` (older/32-bit).
- The relay server address is pinned in `main.py` (`SERVER_URL`).
