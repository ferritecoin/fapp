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

The Android application id stays `org.fext.fext` (`package.domain` +
`package.name`), and the identity/QR payload keeps its original `FEXT` tag:
the brand changed, the protocol did not, so every release talks to every
earlier one, and installs as an *update* to it when both are signed with the
same key (see [Release signing](#release-signing)).

## Downloads

Every push is tested, built and published to a GitHub Release. Open the
**Releases** page (or the **`v9.1.0`** tag) and take:

| Asset | For |
| --- | --- |
| `Ferritext-9.1.0.apk` | Android 5.0+ — sideload it via *Install unknown apps*, or `adb install -r` |
| `Ferritext-9.1.0-desktop.zip` | Windows / macOS / Linux — unzip, then run `python3 ferritext.py` (Python 3.11+). It carries the fonts the client draws with. |
| `Ferritext-9.1.0-desktop.py` | The same client as a single file, to package with [`desktop/`](desktop/README.md). On its own it falls back to Kivy's font: no emoji, no serif. |

All three are also attached to each Actions run as the **`ferritext-release`**
artifact.

## What's in 9.1

- **A new look.** A serif with true italics for everything people write,
  spaced capitals for section heads, buttons and dates, thin rules instead of
  boxes, and colour only where it carries meaning — gold delivery ticks, red
  for what is unread or cannot be undone.
- **Dark and light, following the phone.** By default the app follows the
  system setting, changing with it while open. *Settings → Appearance* can
  fix either one.
- **Groups.** Create one from the compose button: name it, choose up to 31
  contacts. Messages show who wrote them; the admin renames, adds and removes;
  anyone can leave. A group's tick is its slowest member's: three ticks once
  everyone has read it. See [Groups](#groups).
- **Notes to self**, pinned at the top of the chat list: text, photos and
  forwarded messages that stay on this device.
- **Reactions.** Hold a message: a strip of reactions rises over it (+ for
  every emoji). One reaction per person per message; choose yours again to
  take it back. They appear under the message, with a small animation.
- **Emoji** in every message, from a panel beside the composer, and in the
  bundled font, so they render on every phone.
- **A more accessible interface.** Every dialog is a sheet rising from
  the bottom edge; every control is at least 48 dp to touch; text meets WCAG AA
  contrast in both themes; *Settings* offers four text sizes (on top of the
  system's) and *Reduce motion*. Settings and group information are full pages,
  and the identity switcher is a sheet from your initial on the chat list.

## What's in 9.0.1

The app is unchanged from 9.0.0. This release changes how it is built:

- **Signed with the project's own key** once the signing secrets are set (see
  [Release signing](#release-signing)), so later versions install as updates.
  Moving from 9.0.0 or earlier needs one uninstall, explained there.
- **The build checks the signing key first.** It fails within seconds, naming
  the secret to fix, rather than after a 20-minute build.
- **The README covers Windows**, including the PowerShell base64 step.

Relay v9, which stores the sealed photos and voice notes, is ready and has
been tested end to end against this app; media works once it is deployed.

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

**Groups and reactions with older phones.** Groups need no new relay: each
message is sealed separately for every member (see [Groups](#groups)), so a
member still on an older release still receives it.

| | 9.1 | 9.0.x | 8.x and earlier |
| --- | --- | --- | --- |
| A group message | in the group, under its author's name | as a direct message from its author | as a direct message from its author |
| Invitation, rename, add, remove, leave | updates the group | not shown (9.0 ignores instructions it does not know) | shown as text, e.g. "ann added you to the group …" |
| A reaction | under the message | not shown | shown as text, e.g. "Reacted 👍 to …" |

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

## Groups

A group is a list of members, each with their address and public key, kept
by every member's phone and changed only by its admin (its creator, or —
should the admin leave — the next member). Every change carries a version
number and the admin's signature, so a phone accepts an invitation only from
the admin it names, only if it includes that phone, and only if each address
really is the hash of its key; members cannot be slipped in by anyone else.

There is no group on the relay. A message to a group is sealed and sent once
per member, exactly as a direct message would be, with the group's id inside
the signed extension block — so the relay sees ordinary one-to-one traffic
and nothing about who is in which group. That is also why a group holds at
most 32 people: each message costs one relay request per member, and the
relay limits how many requests one connection may make a minute.

Each copy has its own delivery and read receipt. The sender's tick shows the
slowest member: two ticks once it is on every phone, three once everyone has
read it.

## Type and themes

The serif is **EB Garamond**, merged with **Noto Emoji** into the four-style
family *Ferritext Serif* (Kivy draws a label with one font file and cannot fall
back to another, so the emoji must live in the same file). Emoji are
monochrome, taking the colour of the text: Kivy cannot draw colour fonts.
Combined emoji (family groups, flags with modifiers) appear as their separate
parts, because Android's build of Kivy does not shape text.

Capitals are set in the supplied `data/fonts/ui.ttf` (Titillium Web Light),
with letter-spacing the app adds itself, since Kivy's text has none.

All three are under the SIL Open Font License 1.1; the licence texts ship
beside the fonts and inside the APK. `tools/build_fonts.py` rebuilds the merged
family from pinned, checksummed upstream files.

The two themes are sets of colour tokens at the top of the client's design
system. `desktop/test_design.py` measures every text/ground pair both themes
use against WCAG AA (4.5:1 for text, 3:1 for marks such as ticks).

Kivy has no screen-reader support yet. Every control nonetheless carries a
spoken label in the code, ready for when it does.

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
| `data/fonts/` | Ferritext Serif (four styles), the capitals face `ui.ttf`, and their licences. |
| `tools/make_logo.py` | Renders every PNG in `data/` from the SVG — no dependency beyond Pillow. |
| `tools/build_fonts.py` | Rebuilds Ferritext Serif from EB Garamond and Noto Emoji (needs fontTools). |
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
xvfb-run -a python3 desktop/test_design.py main.py
xvfb-run -a python3 desktop/smoke_desktop.py main.py
xvfb-run -a python3 desktop/smoke_chat.py main.py
```

| Suite | Covers |
| --- | --- |
| `test_core.py` | Envelope extension (incl. replay/tamper refusal), wire compatibility with the previous release, migrations both ways, delete-for-both authorisation, tombstones, extension sanitising, group membership rules (who may invite, change and remove), per-member receipts, reactions |
| `test_flows.py` | Three real app controllers over an in-memory relay: replies, forwards, stars, delete for both, photos (EXIF stripping, rotation), expiry → ask again → re-upload, voice notes, notification rules, notes to self, groups (create, write, rename, remove, leave), reactions and taking them back, the reaction strip and emoji panel, switching theme and text size |
| `test_design.py` | WCAG AA contrast of every text/ground pair in both themes, fonts registered and packaged with their licences, following the system theme, tracked capitals, stacking buttons, Back closing a sheet |
| `test_service.py` | Headless import as the service runs it, app ⇄ service hand-over, the Android 14 manifest hook |
| `smoke_desktop.py` | Launch, branding and loading screen through the real `App.run()` path |
| `smoke_chat.py` | Bottom-anchored short chats, scrolling, text selection and copy |

To get `old.py`: `git show v9.0.1:main.py > old.py` (or the latest tag before
the version under test).

## Building it yourself

Requirements: Linux (or WSL) with Python 3, a JDK, and `git zip unzip gcc make
autoconf automake libtool libltdl-dev`. Buildozer downloads the Android SDK/NDK
on first run.

```bash
python3 -m pip install --user buildozer cython
buildozer android debug          # produces bin/fext-9.1.0-*-debug.apk
```

The mark is regenerated with `python3 tools/make_logo.py` (Pillow only).

## Release signing

Android identifies an app by the key it is signed with: a new version
installs as an *update* only when it is signed with the same key as the one
already on the phone. So every release should be signed with one persistent
key, kept in repository secrets.

1. **Create the key once.** `keytool` comes with any JDK (Temurin, for
   example). The same command works in PowerShell on one line:

   ```bash
   keytool -genkeypair -v -keystore fext-release.keystore -alias fext \
     -keyalg RSA -keysize 2048 -validity 10000
   ```

   - Keep `-validity 10000` (about 27 years). Without it, keytool's
     certificate expires after 90 days.
   - keytool's default format, PKCS12, has a single password. If keytool asks
     for a separate key password, use the keystore password again.
   - `keytool -list -v -keystore fext-release.keystore` shows the alias, the
     validity (`Valid from … until …`) and the SHA-256 fingerprint.

2. **Encode it for the secret.**

   - Linux: `base64 -w0 fext-release.keystore`
   - macOS: `base64 -i fext-release.keystore`
   - Windows PowerShell, which has no `base64` command. This copies the result
     straight to the clipboard, so console line-wrapping cannot corrupt it:

     ```powershell
     [System.Convert]::ToBase64String([System.IO.File]::ReadAllBytes("$PWD\fext-release.keystore")) | Set-Clipboard
     ```

3. **Add four repository secrets** under **Settings → Secrets and variables →
   Actions**:

   | Secret | Value |
   | --- | --- |
   | `FEXT_KEYSTORE_B64` | The encoded keystore from step 2 |
   | `FEXT_KEYSTORE_PASSWORD` | The keystore password |
   | `FEXT_KEY_ALIAS` | The `-alias` from step 1 (`fext` in that command); `keytool -list` shows it |
   | `FEXT_KEY_PASSWORD` | The key password. For PKCS12, the keystore password |

The workflow checks the key before it spends 20 minutes building. If the
keystore will not open, or the key password is wrong, the build fails within
seconds and names the secret to fix. If the alias names no key, a keystore
with exactly one key is signed with that key and the build warns you to fix
the secret; with several keys, the build fails and lists them. The log then
shows the certificate's owner, validity and SHA-256 fingerprint, so you can
confirm the release was signed with your key. GitHub masks secret values in
logs, so an alias that matches a secret shows as `***`.

Keep the keystore file and its password safe, private and backed up. If they
are lost, no later build can install as an update, and every user would have
to uninstall, losing their chats.

**Moving to the persistent key.** Every build up to and including 9.0.0 was
signed with a throwaway key, so the first build signed with your key cannot
update them; uninstall the old app first. Before you do, open **Settings →
Private key** and use **Copy WIF** to save each identity's key somewhere safe.
After reinstalling, choose **import an existing private key** to keep the same
address and username. Chat history on that phone does not survive the
uninstall: chat exports are kept inside the app's own storage on Android, and
an uninstall deletes that storage. After this one change, updates install in
place.

**No secrets set.** The workflow then generates a throwaway keystore for each
build. That is still a valid, installable release APK, but every build's
signature differs, so no build can update another.

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
