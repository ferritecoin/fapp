# Ferritext — Android client

**Ferrite Text Messenger** — an end-to-end encrypted messenger built with
[Kivy](https://kivy.org). This repository packages the single-file client
(`main.py`) into an installable Android `.apk` using
[Buildozer](https://buildozer.readthedocs.io) /
[python-for-android](https://python-for-android.readthedocs.io).

The launcher name is **Ferritext**; the Android application id stays
`org.fext` so this release installs as an *update* over earlier ones rather
than alongside them. The identity/QR payload likewise keeps its original
`FEXT` tag — the brand changed, the protocol did not.

The whole client is one auditable file. Its security model is transparency: no
plaintext ever leaves the process — messages are signed (secp256k1 ECDSA),
sealed (ephemeral ECDH → HKDF-SHA256 → AES-256-GCM), and only opaque ciphertext
is transmitted.

## What's in here

| File | Purpose |
| --- | --- |
| `main.py` | The complete FEXT client (Buildozer's entry point **must** be `main.py`). |
| `buildozer.spec` | Android packaging configuration (requirements, permissions, ABIs, API levels). |
| `data/` | The Ferritext mark: in-app logo, launcher icon, Android presplash. |
| `tools/make_logo.py` | Draws the mark and regenerates everything in `data/`. |
| `.github/workflows/build-apk.yml` | CI that builds a signed release APK and publishes it to a GitHub Release. |
| `desktop/` | Nuitka packaging for the **desktop** build of the same `main.py` (see `desktop/README.md`). |
| `desktop/smoke_chat.py` | Conversation-layout + copy/paste regression test (headless, real Kivy). |

## Getting the APK

### Option A — download the release from GitHub (no local setup)

Every push builds a **signed release APK** on GitHub Actions and publishes it to
a GitHub Release:

1. Open the repository's **Releases** page (or the **`v8.0.2`** tag).
2. Download `fext-8.0.2-*-release.apk` from the release assets.

The same APK is also attached to each Actions run as the **`fext-release-apk`**
artifact (Actions tab → latest run → Artifacts).

### Option B — build it locally

Requirements: Linux (or WSL) with Python 3, a JDK, and the usual build
toolchain (`git`, `zip`, `unzip`, `gcc`, `make`, `autoconf`, `automake`,
`libtool`, `libltdl-dev`). Buildozer downloads the Android SDK/NDK automatically
on first run.

```bash
python3 -m pip install --user buildozer cython
buildozer android debug          # produces bin/fext-8.0.2-*-debug.apk
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
adb install -r fext-8.0.2-*-release.apk
```

## Configuration notes

- **Requirements** (from `buildozer.spec`): `kivy`, `pillow`, `requests`,
  `websocket-client`, `cryptography`, `qrcode`, plus `pyjnius`/`android` for the
  app-private storage path. `sqlite3` ships with python-for-android.
- **Permissions**: `INTERNET` and `ACCESS_NETWORK_STATE` — the app is a
  networked messenger and shows an online/offline status ribbon.
- **ABIs**: `arm64-v8a` (all modern phones) and `armeabi-v7a` (older/32-bit).
- The relay host is pinned in `main.py` (`SERVER_HOST`). Since v7.1 the client
  *discovers* the scheme: it probes `https://` first and falls back to `http://`,
  caching the winner in `transport.json` and re-probing when it stops working.
- **Cleartext is scoped, not blanket.** Android blocks cleartext by default
  (API 28+), which would defeat that fallback before any Python runs, so the
  build ships `src/android/res/xml/network_security_config.xml` permitting
  cleartext for the **relay host only** — every other destination stays blocked.
  It is wired up via `android.add_resources` plus a `p4a.extra_args` entry in
  `buildozer.spec` (**not** `android.extra_manifest_application_arguments` —
  buildozer 1.5.0 mangles the quotes in that option, producing an
  AndroidManifest.xml that fails to parse; see the note in the spec).
  Once every relay serves HTTPS: delete that file, remove those two spec keys,
  and set `FEXT_REQUIRE_TLS=1` to fail closed.

## Desktop build

The same `main.py` also runs on desktop — it detects the platform at import
time, so there is no separate desktop source. `desktop/build_desktop.sh`
compiles it into a distributable executable with Nuitka:

```bash
cd desktop && ./build_desktop.sh            # standalone folder
cd desktop && ONEFILE=1 ./build_desktop.sh  # single executable
```

See `desktop/README.md` for prerequisites, why each Nuitka flag is required,
and the verification results.

## The mark

The logo is a laurel wreath around a wire-frame globe, with a **ferrite
bead** — a rectangle threaded by a wire, exactly as it is drawn on a
schematic — as the central glyph. It is *generated*, not stored as an opaque
binary, so the geometry stays editable:

```bash
python3 tools/make_logo.py      # rewrites data/{ferritext_logo,icon,presplash}.png
```

Needs Pillow. The in-app loading screen shows the mark over *Ferrite Text /
Messenger / by koh-gt* and fades out in 100 ms; Android shows
`data/presplash.png` on the same cream before that, so the handover between
the two is invisible.

## Testing

Both suites run headless against real Kivy and exit non-zero on failure:

```bash
xvfb-run -a python3 desktop/smoke_desktop.py main.py   # launch + crypto + branding
xvfb-run -a python3 desktop/smoke_chat.py   main.py    # chat layout + copy/paste
```

`smoke_chat.py` is the regression guard for two things that are easy to
break and awkward to notice:

- **Short conversations stay on the composer.** Kivy lays a ScrollView's
  content out from the *top* when it is shorter than the viewport, and with
  `always_overscroll` on (the default) the same branch runs for any
  `scroll_y` above zero — so a two-message chat could be lifted up under the
  app bar by a stray touch, with a screen of emptiness beneath it. The chat
  screen now pads the top until the content is at least viewport-tall, which
  makes the short case impossible while leaving a conversation that really
  does overflow completely untouched.
- **Message text is selectable.** The body of each bubble is a read-only
  `TextInput`, so you can drag-select and Ctrl+C on a desktop and long-press
  for handles and a Copy bubble on a phone.
