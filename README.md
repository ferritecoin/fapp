# Ferritext — Ferrite Text Messenger

An end-to-end encrypted messenger built with [Kivy](https://kivy.org). One
auditable file, `main.py`, is the whole client: it runs on Android (packaged
here into an `.apk` with [Buildozer](https://buildozer.readthedocs.io) /
[python-for-android](https://python-for-android.readthedocs.io)) and, unchanged,
on Windows, macOS and Linux.

Its security model is transparency: no plaintext ever leaves the device.
Messages are signed (secp256k1 ECDSA), sealed (ephemeral ECDH → HKDF-SHA256 →
AES-256-GCM), and only opaque ciphertext is transmitted. Photos and voice notes
are sealed with a fresh key per file before they are uploaded.

The Android application id stays `org.fext`, and the identity/QR payload keeps
its original `FEXT` tag: the brand changed, the protocol did not, so every
release installs as an *update* and talks to every earlier one.

## Downloads

Every push is tested, built and published to a GitHub Release. Open the
**Releases** page (or the **`v9.0.0`** tag) and take:

| Asset | For |
| --- | --- |
| `Ferritext-9.0.0.apk` | Android 5.0+ — sideload it via *Install unknown apps*, or `adb install -r` |
| `Ferritext-9.0.0-desktop.py` | Windows / macOS / Linux — run with Python 3.11+, or package it with [`desktop/`](desktop/README.md) |

Both are also attached to each Actions run as the **`ferritext-release`**
artifact.

## What's in 9.0

- **Highlight mode.** Long-press a message (right-click on a desktop) to select
  it; tap more to add them. The app bar switches to **reply · copy · star ·
  forward · share · delete**, with back/✕ to leave.
- **Replies** quote the original, and tapping the quote scrolls to it.
- **Stars** are private bookmarks — they never leave the device.
- **Delete** for yourself, or — for your own messages only — for both of you.
  The other phone enforces that rule as well, so a modified client cannot
  delete what you wrote.
- **Forward** to any contact of the same identity; photos and voice notes are
  forwarded by reference, without uploading them again.
- **Share** to other apps through Android's share sheet (the clipboard on a
  desktop).
- **Photos** are resized, re-encoded and stripped of location and camera data
  before they leave the phone. **Voice notes** are recorded as low-bitrate AAC.
  Both need the v9 relay — see [Media](#media).
- **Notifications**, including when Android has closed the app (see
  [Background delivery](#background-delivery)), with a per-chat mute.
- **Chat info** (tap the name in a chat): notifications, delivery ticks,
  auto-read, starred messages, export/import. The ⋮ menu holds the same
  shortcuts.
- **Identities**: rename (pen) and delete (bin) from the identity list.
- A redesigned chat list and conversation screen in the Ferritext colours, and
  the Ferrite Text Messenger mark on the launcher icon and the loading screen.

## Compatibility

**On the wire.** v9 features travel in an extension block inside the
encrypted payload, with its own signature that also names the recipient and
the message — which closes a replay the older format allowed (anyone who
received a signed message could re-encrypt it to a third person). A pre-v9
client never looks at the block: it shows the message's readable text ("Photo",
"Voice message (0:07)", the reply's own words). v9 learns which contacts run
v9 from the blocks they send.

**On the device.** The local database is versioned (`PRAGMA user_version`) and
migrations are strictly additive — columns are only ever added, each with a
default, and the readable `body` is always filled in. A newer release opens
any older database, and an older release can still open a database a newer
one has migrated. `desktop/test_core.py` checks both directions against the
previous release's real code.

## Media

Photos and voice notes are uploaded to the relay as sealed blobs and
referenced from an ordinary end-to-end message carrying the key. The relay
keeps blobs only for a while; if one has expired before it was downloaded,
the recipient taps it to ask again, and the sender's device — which still has
the file — uploads it again and updates the original message in place.
Long-term storage is the phones', never the relay's.

This needs the **v9 relay** (`/api/blobs`, advertised in `/api/health`). On an
older relay the app says so instead of failing; text features all work.

## Background delivery

Android closes apps in the background. While Ferritext's own process is alive
it listens for messages itself; once Android has closed it, a small
foreground service (`service.py`, a separate process) takes over, and steps
aside again when the app returns. The two coordinate through heartbeat files
so only one ever holds the connection. It can be switched off under
*Settings → Notifications*, at the cost of notifications only arriving while
the app is open.

Android 14 requires such a service to declare its type, which
python-for-android's manifest template cannot express;
`tools/p4a_hook.py` adds it during the build (and fails the build, rather than
the phone, if it cannot).

## What's in here

| Path | Purpose |
| --- | --- |
| `main.py` | The complete client (Buildozer's entry point **must** be `main.py`). |
| `service.py` | Android background sync service (imports `main.py`, no UI). |
| `buildozer.spec` | Android packaging: requirements, permissions, service, icons. |
| `data/` | The Ferrite Text Messenger mark (`ferritext_logo.svg`, as supplied) and the PNGs rendered from it. |
| `tools/make_logo.py` | Renders every PNG in `data/` from the SVG — no dependency beyond Pillow. |
| `tools/p4a_hook.py` | Build hook declaring the service type Android 14 requires. |
| `src/android/res/` | Android resources: cleartext policy for the relay, status-bar icon. |
| `desktop/` | Desktop packaging (Nuitka) and the headless test suites. |
| `.github/workflows/build-apk.yml` | Tests → signed APK → GitHub Release with both deliverables. |

## Testing

Every suite runs headless against the real code and exits non-zero on failure;
CI runs them all before it will build an APK.

```bash
python3 desktop/test_service.py main.py                  # no display at all
xvfb-run -a python3 desktop/test_core.py  main.py old.py # old.py: previous release
xvfb-run -a python3 desktop/test_flows.py main.py
xvfb-run -a python3 desktop/smoke_desktop.py main.py
xvfb-run -a python3 desktop/smoke_chat.py main.py
```

| Suite | Covers |
| --- | --- |
| `test_core.py` | Envelope extension (incl. replay/tamper refusal), wire compatibility with the previous release, migrations both ways, delete-for-both authorisation, tombstones, extension sanitising |
| `test_flows.py` | Two real app controllers over an in-memory relay: replies, forwards, stars, delete for both, photos (EXIF stripping, rotation), expiry → ask again → re-upload, voice notes, notification rules |
| `test_service.py` | Headless import as the service runs it, app ⇄ service hand-over, the Android 14 manifest hook |
| `smoke_desktop.py` | Launch, branding and loading screen through the real `App.run()` path |
| `smoke_chat.py` | Bottom-anchored short chats, scrolling, text selection and copy |

To get `old.py`: `git show v8.0.2:main.py > old.py` (or the latest tag before
the version under test).

## Building it yourself

Requirements: Linux (or WSL) with Python 3, a JDK, and `git zip unzip gcc make
autoconf automake libtool libltdl-dev`. Buildozer downloads the Android SDK/NDK
on first run.

```bash
python3 -m pip install --user buildozer cython
buildozer android debug          # produces bin/fext-9.0.0-*-debug.apk
```

The mark is regenerated with `python3 tools/make_logo.py` (Pillow only).

## Release signing

- **Persistent key (recommended for real distribution).** Generate a keystore
  once and add it as repository secrets so every build is signed with the *same*
  key — this is what lets a new version install as an update over the previous
  one without uninstalling (which would delete local chats):

  ```bash
  keytool -genkeypair -v -keystore fext-release.keystore -alias fext \
    -keyalg RSA -keysize 2048 -validity 10000
  base64 -w0 fext-release.keystore    # value for the FEXT_KEYSTORE_B64 secret
  ```

  Then, under **Settings → Secrets and variables → Actions**, add:
  `FEXT_KEYSTORE_B64`, `FEXT_KEYSTORE_PASSWORD`, `FEXT_KEY_ALIAS`,
  `FEXT_KEY_PASSWORD`. Keep the keystore itself somewhere safe and private —
  losing it means you can never ship an update that installs over this one.

- **No secrets set.** The workflow generates a throwaway keystore per build:
  still a valid, installable release APK, but each build's signature differs,
  so a later build will not update-install over an earlier one.

A signing key is **never committed to this repository.**

## Configuration notes

- **Relay**: pinned in `main.py` (`SERVER_HOST`, overridable with
  `FEXT_SERVER_HOST`). The client probes `https://` first and falls back to
  `http://`, caching the winner and re-probing when it stops working.
- **Cleartext is scoped, not blanket.** Android blocks cleartext by default, so
  `src/android/res/xml/network_security_config.xml` permits it for the relay
  host only. It is wired up via `android.add_resources` plus a `p4a.extra_args`
  entry in `buildozer.spec` (**not** `android.extra_manifest_application_arguments`,
  which buildozer 1.5.0 mangles). Once the relay serves HTTPS everywhere,
  delete that file and those two keys, and set `FEXT_REQUIRE_TLS=1`.
- **Permissions**: internet and network state; notifications (asked on
  Android 13+); microphone (asked the first time you record); the foreground
  service and its data-sync type (background delivery).
