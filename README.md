# Ferritext — Ferrite Text Messenger

An end-to-end encrypted messenger built with [Kivy](https://kivy.org). One
auditable file, `main.py`, is the whole client: it runs on Android (packaged
here into an `.apk` with [Buildozer](https://buildozer.readthedocs.io) /
[python-for-android](https://python-for-android.readthedocs.io)) and, unchanged,
on Windows, macOS and Linux.

Its security model is transparency: no plaintext ever leaves the device.
Messages are signed (secp256k1 ECDSA), sealed (ephemeral ECDH, with ML-KEM-768
once the recipient's post-quantum key is known → HKDF-SHA256 → AES-256-GCM),
and only opaque ciphertext is transmitted. Photos and voice notes
are sealed with a fresh key per file before they are uploaded.

The Android application id stays `org.fext.fext` (`package.domain` +
`package.name`), and the identity/QR payload keeps its original `FEXT` tag:
the brand changed, the protocol did not, so every release talks to every
earlier one, and installs as an *update* to it when both are signed with the
same key (see [Release signing](#release-signing)).

## Downloads

Every push to the default branch is tested and built, and each new version is
published to a GitHub Release, whose files never change afterwards. Open the
**Releases** page (or the **`v15.0.0`** tag) and take:

| Asset | For |
| --- | --- |
| `Ferritext-15.0.0.apk` | Android 5.0+ — sideload it via *Install unknown apps*, or `adb install -r` |
| `Ferritext-15.0.0-desktop.py` | Windows / macOS / Linux — one file with its fonts and icons inside. Run it with Python 3.11+ after `pip install kivy requests websocket-client cryptography qrcode pillow`. |

Both are also attached to each Actions run as the **`ferritext-release`**
artifact, kept for a week. Pushes to other branches are tested and built, but
publish nothing.

On a computer, each of these adds something when it is installed, and
nothing breaks without it:

| Install | Adds |
| --- | --- |
| Opus — `apt install libopus0`, `brew install opus`, or `pip install pyogg` (Windows) | calls in Opus: clearer, on about half the data |
| `pip install sounddevice` | the best call audio, and choosing the microphone and speaker |
| `pip install numpy` | voice notes at 1.5× and 2× |
| `pip install av` (or the `ffmpeg` program) | playing voice notes recorded on a phone |
| `pip install opencv-python` (or `pyzbar`) | reading a contact's QR code from a picture |

## What's in 15.0.0

15.0.0 carries 14.0's changes too: 14.0 was never published on its own.

- **Post-quantum encryption.** A message to someone whose post-quantum key
  your app holds is sealed with ML-KEM-768 as well as the usual key, so a
  recording made today cannot be read by breaking the usual key later. Keys
  pass in your messages: a chat is post-quantum both ways once each of you
  has written from 15.0, and *About this chat* shows which way it is on.
  Groups, calls, photos and voice notes are covered the same way; the
  README's *Post-quantum encryption* has more.
- **Copy WIF works with Ferrite Core.** It gives Ferrite Core's own key
  format, which its `importprivkey` takes. Keys copied from earlier versions
  still import into Ferritext.
- **Messages that wait.** A message the relay did not take stays on your
  phone at its clock and goes by itself when the relay answers; after a day
  it reads "Not sent · tap to try again". A photo waits the same way when
  the relay has no room for it.
- **Message requests.** The first messages from someone new wait under
  *Message requests*: no notification text, no download, no call, and they
  see one tick until you accept. *Decline* is not a block. *Report* tells
  the relay's operator who and why, never what they wrote, and can block
  them. *Settings → Privacy → Blocked* lists everyone blocked.
- **Download rules.** Under *Settings → Downloads*, photos and voice notes on
  mobile data, and photos in groups of more than 20, can wait for a tap; on
  mobile data the app can ask first.
- **Lighter on the relay.** With relay 12.2, a group message goes to every
  member in one request, an upload says how many people will fetch it so it
  is freed sooner, and a relay restarting for an update tells phones when to
  come back.
- **A faster chat list** on a phone with a long history.
- Fixed: photos and voice notes in a disappearing chat were sent without
  the chat's timer.
- For developers: `desktop/capture.py` captures the app's screens and
  `desktop/bench.py` times its work, to compare a change before and after.
- **Coming from an earlier version?** Everything in 13.0.0 and before,
  below, is new to you too.

## What's in 13.0.0

- **A new mark.** The bead on its wire now travels in a rounded
  parallelogram, a packet on its way: on the launcher, the loading screen,
  notifications and the window icons. On dark grounds it sits on a white
  plate of its own shape.
- **The loading screen follows dark mode**, Android's own launch screen
  included: black at night, with the mark on its plate.
- **Two ticks at most.** A clock while a message is still on your phone,
  one grey tick once the relay has it, two grey once it is on their phone,
  and two gold once it is read.
- **An icon for every setting**, showing what it is set to, that moves when
  you change it (it does not move when motion is reduced).
- **Who reacted.** Hold a reaction to see who gave it. In a group, the faces
  of up to three people sit beside it. Reactions are a soft fill now,
  without an outline.
- **Your most-used reactions come first** in the strip.
- **Where a group message has got to.** Hold its time to see who has it and
  who has read it.
- **The day floats over a long chat** as you scroll.
- **Quieter emoji colours**, to sit better with the text.
- Fixed: the loading screen could drop its second line, "Messenger".
- For developers: the app and both of its Android services start from
  `main.py`, and Android's Java code and resources sit in one folder,
  `android/`.
- **Coming from an earlier version?** Everything in 12.0.0 and 11.0.0,
  below, is new to you too.

## What's in 12.0.0

- **Emoji in colour**, everywhere: in messages, reactions, the chat list,
  the emoji panel and as you type. Flags, skin tones, families and keycaps
  are one picture each.
- **Big emoji.** A message of only one to three emoji is drawn large, on the
  page itself.
- **Runs of messages.** What one person sends within a few minutes reads as
  one run: closer together, with their name and the time once.
- **Open where you left off.** A chat with unread messages opens at the first
  of them, under a *New messages* line that counts them.
- **A black background** for the dark theme, now its default (*Settings →
  Appearance*; turned off, a warm dark grey).
- **Faces in groups.** The last message of each person's run has their
  picture beside it, or their initial until they have one; tap it to message
  them, or to add them to your contacts.
- **Up to three reactions each.** Choose another to add it, and one of yours
  again to take it back.
- **TLS by itself.** Where the relay serves TLS on a port of its own, the app
  moves there once the certificate checks out, and remembers it.
- **Your keys stay out of Android's cloud backup.** Android's automatic
  backup no longer copies the app's data; move phones with the app's own
  sealed backup (*Settings → Backup*).
- Fixed: a photo claiming enormous dimensions could fill a phone's memory;
  a link preview could be steered at your local network; restoring a backup
  could race the background service; a message whose time was not a time
  came back on every connect; backing up to a folder on a computer saved
  beside it; someone known only from a group appeared in your chat list
  once their picture arrived; the reaction strip marked your reaction in
  the wrong place.
- **Coming from 9.1.x?** Everything in 11.0.0, below, is new to you too.

## What's in 11.0.0

- **Notifications that arrive.** A new message is announced the moment it is
  received, even while Android has the app paused or closed, in a
  conversation-style notification you can **reply to** or **mark as read**
  from the shade. On a computer, the system's own notification appears while
  Ferritext's window is elsewhere.
- **Mute** a chat for 12 hours, 3 days, or until you turn it back on. A
  muted chat still receives and shows everything and counts what is unread;
  only its notifications stop, and they come back by themselves when the
  time is up.
- **Blocking holds, unblocking delivers.** What a blocked contact sends is
  kept on your device, still sealed, unseen and unannounced, and their calls
  are refused. Unblock them and it all arrives, in order.
- **Calls in Opus**, where both sides have it: clearer, on about half the
  data, and losses concealed. **Bluetooth** headsets and a choice of where the
  sound goes; an **incoming-call screen** over the lock screen with Answer
  and Decline; a ringing tone for the caller; on a computer, echo cancelled
  by the system, and the microphone and speaker chosen in Settings.
- **Disappearing messages**: from five minutes to four weeks, set from a
  chat's page by either of you (in a group, its admin).
- **Verify a contact**: compare the code on both phones — read it aloud, or
  scan the other phone's QR code — and the chat says *verified*.
- **Links** are underlined, and a tap first shows where one really leads.
  **Link previews** are off unless turned on in *Settings → Privacy*; then
  your device fetches the preview as you write and sends it with the
  message, and nobody reading it contacts the site.
- **Back up to a file** sealed with a passphrase — identities, chats,
  photos — and **restore** it on a new phone or computer.
- **Swipes**: to the right goes back a page (on the chat list, it opens the
  menu); to the left on a message replies to it.
- **Search in a chat**, **drafts** that survive a restart, **voice notes at
  1.5× and 2×** with a waveform to drag through, **keyboard shortcuts** on a
  computer, and **paste or drop a picture** to send it.
- **About** names the source code and the open-source work the app is made
  with ([CREDITS.md](CREDITS.md)).
- Fixed: tapping a message with a reaction, a quote or a photo could close
  the app; long links left a blank line under a message on a computer; a
  computer's file lists were invisible on the light theme.
- **Coming from 9.1.1?** Everything in 9.1.2, below, is new to you too:
  voice calls, profile and group pictures, the menu, search, holding chats,
  hold to record, and tap to react.

## What's in 9.1.2

- **Voice calls.** Call anyone in a direct chat from the phone button in its
  header, or from its page. The call is set up with sealed messages, and its
  sound is sealed with keys made for that call alone. It goes straight
  between the two devices whenever their networks allow, and otherwise
  through the relay, which forwards what it cannot read. Ringing with answer
  and decline, mute, the loudspeaker on a phone, and a line in the chat for
  every call: how long it lasted, or that it was missed. Calls need relay v10
  — see [Calls](#calls).
- **Profile and group pictures.** Choose or take a photo and frame it in a
  circle (drag to place, pinch or scroll to size); the people you write to
  see it. A group's admin can give the group one. Pictures appear wherever a
  disc does: the chat list, chats, pages, the menu, search, member lists.
- **The menu.** ☰ beside the title opens a menu from the left: your
  identities (switch, or add one), your profile, a new group, saved messages,
  settings and about. 🔍 at the right searches chats and messages.
- **Hold a chat** in the list to select it, then more with a tap: mute or
  unmute, archive, delete, or from ⋮ pin, mark as unread or block. Archived
  chats wait behind their own row at the top of the list.
- **Hold to record** a voice message and release to send; slide left to
  cancel; slide up to lock it and carry on hands-free, with pause, delete
  and send.
- **Tap a message** to react to it; hold it to select it, as before.
- **Back goes back one step** — out of a selection, a sheet or a page — and
  only leaves the app from the chat list.
- **Quicker.** Chats open at once, with the rest of a long history filling in
  behind; pages and sheets arrive in quick layers that fade, and motion is
  on unless *Settings → Reduce motion* asks for a cross-fade.
- The picture in a chat's header opens the chat's page, where the picture
  can be seen full size.
- Photos no longer turn white for the sender, or later for the recipient.

## What's in 9.1.1

- **The composer, redone.** With nothing written it shows who you are writing
  as (tap your initial to switch identity), the emoji key, the paperclip, a
  camera, and a microphone in place of the send button. Once you start
  typing, your initial and the camera fold away and the microphone turns
  into the send button.
- **Take a photo** from the composer. On Android the phone's camera app takes
  it and hands it straight to Ferritext's private storage — it never passes
  through the gallery. On a computer, a webcam through OpenCV when that is
  installed.
- **Voice notes from a computer**, as WAV: built in on Windows; on Linux and
  macOS through `arecord`, `parec`, `ffmpeg` or the `sounddevice` package.
  Recording shows the time and the microphone's level as it goes.
- **More detailed icons**, drawn finer and smoothed whatever the graphics
  card offers — so are the avatars, buttons and message shapes, which were
  jagged on computers without multisampling.
- **Clicking the message field works on a computer.** The closed emoji panel
  (and other hidden parts) no longer swallow clicks and taps meant for what
  lies underneath them.
- **The desktop download is one file** that carries its fonts — emoji and the
  serif now appear on a computer — and the Ferritext mark for the title bar,
  taskbar and dock, in place of Kivy's.
- A right-click on a computer no longer leaves a red dot behind.
- A photo you pick or take is deleted once its compressed copy is sent (or
  you decide not to send it), and received voice notes keep their own file
  type.

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

**With 14.0 and earlier.** Earlier releases read every 15.0 message: the
post-quantum key in its signed block is ignored. 15.0 seals a message
post-quantum only to someone whose key it holds, and only 15.0 sends one,
so nothing changes for anyone on an earlier release. Every relay carries
the larger envelopes. The local database gains three columns (schema 8),
which earlier releases ignore.

**With 13.0 and earlier.** Nothing in the messages changed in 14.0; what is
new is how the app uses the relay, and it asks the relay first.

| | Relay 12.2 | Relays before 12.2 |
| --- | --- | --- |
| A group message | one request for every member | one request a member, as before |
| An upload's count of readers | the file is freed once they have it | ignored: kept its usual time |
| A report | reaches the relay's operator | not offered: *Report* only blocks |
| Restarting for an update | phones wait as the relay asks | phones reconnect at once, as before |

Someone on 13.0 or earlier who writes to you first is a message request
like anyone else, and sees one tick until you accept. The local database
gains one column (schema 7), which earlier releases ignore.

**With 12.0 and earlier.** Nothing on the wire changed in 13.0. It talks to
every earlier release as 12.0 does, and to every relay 12.0 can use, relay
12.2 included. Its two ticks are drawn from the same three states the relay
reports.

**With 11.0 and earlier.** 12.0 talks to every earlier release.

| | 12.0 | 11.0 and earlier |
| --- | --- | --- |
| Reactions | up to three each | each person's latest |
| A reaction from them | replaces all of theirs (they have one) | — |
| The relay's own TLS port | used once its certificate checks out | not used: the relay keeps its usual port for them |

Emoji in colour, runs, faces in groups and the black background are drawn
by each app for itself; nothing about them is sent.

**With 9.1.2 and earlier.** 11.0 talks to every earlier release; what
they do not know, they ignore or show as text.

| | 11.0 | 9.1.2 | 9.1.1 and earlier |
| --- | --- | --- | --- |
| A call | Opus when both have it, otherwise ADPCM | ADPCM | not offered |
| Disappearing messages | deleted on time on both sides | kept: when the timer is set, 11.0 names whose app keeps them | kept |
| A link preview | shown, if previews are on | the message's text | the message's text |

Blocking, muting, drafts and the *verified* mark live on this device only;
nothing about them is sent.

**Pictures and calls with older phones.** Every 9.1.2 message says what its
app can do and which version of its author's picture is current; older
releases verify these fields and ignore them. Pictures go only to apps that
keep them, and only those can be called.

| | 9.1.2 | 9.1.1 and earlier |
| --- | --- | --- |
| Your picture | shown wherever your disc is | not sent (their app would not keep it) |
| A call | rings | not offered: the call button says they need 9.1.2 |

**Groups and reactions with older phones.** Groups need no new relay: each
message is sealed separately for every member (see [Groups](#groups)), so a
member still on an older release still receives it.

| | 9.1 | 9.0.x | 8.x and earlier |
| --- | --- | --- | --- |
| A group message | in the group, under its author's name | as a direct message from its author | as a direct message from its author |
| Invitation, rename, add, remove, leave | updates the group | not shown (9.0 ignores instructions it does not know) | shown as text, e.g. "ann added you to the group …" |
| A reaction | under the message | not shown | shown as text, e.g. "Reacted 👍 to …" |

**Voice notes recorded on a computer** are WAV. Every 9.x phone plays them;
on a computer, 9.1.0 and earlier save them under an `.m4a` name, which some
players refuse — 9.1.1 keeps each note's own file type.

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

Each copy has its own delivery and read receipt. The sender's ticks show the
slowest member: two grey ticks once it is on every phone, two gold once
everyone has read it.

## Type and themes

The serif is **EB Garamond**, merged with **Noto Emoji** into the four-style
family *Ferritext Serif* (Kivy draws a label with one font file and cannot fall
back to another, so the emoji must live in the same file). The emoji are drawn
in colour, from **Twemoji**'s graphics, laid over Noto Emoji's outlines as the
font's colour layers (COLR/CPAL), which FreeType draws at any size. Flags, skin
tones, families and keycaps are joined into one picture by the font itself. A
system that cannot draw colour fonts draws the outlines instead, in the colour
of the text.

Capitals are set in the supplied `data/fonts/ui.ttf` (Titillium Web Light),
with letter-spacing the app adds itself, since Kivy's text has none.

All three are under the SIL Open Font License 1.1, and Twemoji's graphics
under CC-BY 4.0; the licence texts ship beside the fonts and inside the APK. `tools/build_fonts.py` rebuilds the merged
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

Each upload says how many people will fetch it (`X-Fext-Expect`), and relay
12.2 frees the file an hour after the last of them has it. A relay with no
room left refuses the upload (507) instead of deleting anyone's media: the
file waits on the sender's phone, with its clock, and goes when there is
room.

**Download rules** (*Settings → Downloads*). A photo or voice note that
arrives downloads by itself, except:

- in a message request;
- a photo in a group of more than 20, which waits for a tap;
- on mobile data, whatever has been turned off there.

Its small preview shows at once. A tap on mobile data asks first, unless
*Ask before using mobile data* is off. Computers count as on Wi-Fi.

## Calls

A call is set up with end-to-end messages, like everything else: an offer,
the other app saying it rings, the answer, the end. Each side makes a key
pair for that call alone and the two agree a secret from them; the offer and
the reply are signed and sealed, so not even the relay can put its own key
in between, and from that secret comes one key for each direction.

The sound travels as small UDP packets, 20 ms of 16 kHz speech each, every
one sealed with AES-256-GCM and decodable on its own. Where both sides have
it the codec is **Opus** at 24 kbit/s, with in-band FEC: a lost packet is
rebuilt from the next one, or concealed. Otherwise (or with a 9.1.2 phone)
it is IMA ADPCM, about 80 kbit/s each way, where a lost packet costs 20 ms
and nothing more. A short buffer, growing only when the network is uneven,
smooths out arrival. Android's Opus is libopus, built by the local recipe in
`recipes/libopus`; a computer uses its system's.

Each device learns from the relay the public address it is seen from, offers
it to the other with its local ones, and both send to each other at once —
which opens a direct path through most home routers, the shortest there is.
Where that fails, packets go through the relay's call port, and where UDP is
blocked altogether, over the relay's WebSocket. Every packet that arrives
shows which way works; the most direct one heard from in the last few
seconds is used, and that changes as the network does. The call screen
shows which way the sound goes and the round-trip time.

On Android the phone's own call audio is used — its echo cancelling, the
earpiece, the loudspeaker, a wired or Bluetooth headset, chosen from the call
screen — the screen darkens against the ear, and a small service (started
from `main.py`, as the app is) keeps the microphone on while the app is off
screen, which Android allows only to a service of the *microphone* type. It
starts when you dial, so the caller's microphone works even if they leave the
app while it rings. An incoming call rings as a call notification with Answer
and Decline, full screen over the lock screen where Android allows it.

On a computer the sound goes through the `sounddevice` package where it is
installed (with the microphone and speaker chosen in Settings), Windows' own
audio otherwise, or `arecord`/`aplay` (or `parec`/`pacat`, or SoX) on Linux.
Echo is cancelled by the system: a headset or a speakerphone that cancels
it, or on Linux PulseAudio's or PipeWire's echo-cancel module, which is used
whenever it is loaded.

Calls need **relay v10**, which advertises them in `/api/health` and opens a
UDP port for them (by default the same number as its HTTP port). On an older
relay the call button says so.

## Sending, and the outbox

A message is stored on the phone first, at 0/3 (the clock), then sent. If
the relay does not take it — no network, the relay restarting or full — it
stays there, at its clock, and the **outbox** sends it by itself: when the
app next reaches the relay, and every 30 seconds while connected. Sending
twice is harmless: a receiving app keeps one copy per message id. After 24
hours the outbox stops trying, and the message reads *Not sent · tap to try
again*.

A group message goes to every member in **one request** (`send_many`, up to
32 members a request) on a relay that offers it (12.2), and one request a
member on older relays. A member whose copy the relay refused gets it from
the outbox later; the others are not sent it twice.

When a relay stops for an update it tells connected phones when to come
back, and they wait that long, plus a few seconds at random, so they do
not all return at once.

## Background delivery

Android closes apps in the background. While Ferritext's own process is alive
it listens for messages itself; once Android has closed it, a small
foreground service (a separate process, started from `main.py` too) takes
over, and steps aside again when the app returns. The two coordinate through
heartbeat files so only one ever holds the connection. It can be switched off
under *Settings → Notifications*, at the cost of notifications only arriving
while the app is open.

Whichever holds the connection announces a message the moment it arrives,
from the thread that received it — so a paused app, whose screen Android has
stopped, still notifies at once. What is done in the shade (a reply typed
into the notification, *Mark as read*, *Decline* on a call) is handed to the
app as a small file by a receiver (`org.fext.notify.NotifyReceiver`), and
acted on by whichever of the two is running.

Android 14 requires such a service to declare its type, which
python-for-android's manifest template cannot express;
`tools/p4a_hook.py` adds it during the build (and fails the build, rather than
the phone, if it cannot) — *dataSync* for this service, *microphone* for the
call service. The same hook declares the notifications' receiver and the
FileProvider through which the camera app hands over a photo (its one folder
is `android/fext_file_paths.xml`; the class comes from
`androidx.core`).

## Post-quantum encryption

What is sent today can be recorded and kept until a quantum computer can
break secp256k1 ("store now, decrypt later"). From 15.0, a message to
someone whose post-quantum key the app holds is sealed with both the usual
key agreement and ML-KEM-768 (FIPS 203): its AES-256-GCM key comes from the
two secrets together, so reading it takes breaking both.

- **Keys travel in messages.** Each identity has an ML-KEM key made from
  its own random seed, kept beside its private key and never derived from
  it. Every message's signed block names that key, and carries the key
  itself (1,184 bytes) until the other side shows it holds it by sealing a
  message to it. A chat is post-quantum both ways once each side has
  written from 15.0; *About this chat* shows which way it is on.
- **Groups** seal each member's copy as that member's own chat is sealed.
- **Calls** exchange their keys inside the call's own messages, so a call
  in a chat that is post-quantum both ways is too.
- **Photos and voice notes** are sealed with their own AES-256 key, which
  travels inside the message, so they are covered with it.
- **A new install from the private key alone** (WIF) has a new ML-KEM key.
  A message sealed to the old one is asked for again, with the new key;
  the sender's app sends that message again once, if it is under a week
  old. A backup carries the key, so an identity restored from one keeps it.
- **Not changed:** signatures are still ECDSA, since forging one takes a
  quantum computer at the time, not later. The relay connection is TLS as
  before, so what the relay sees (who writes to whom, and when) is not
  post-quantum.
- **Cost**, from `desktop/bench.py`: sealing a message 5.2 → 8.6 ms,
  opening one 5.1 → 9.3 ms, and an envelope 1,005 → 2,473 bytes (4,625
  while it carries our key).

The ML-KEM code is pure Python in `main.py`: the Android build's
cryptography library is too old to have it. `test_core.py` checks it
against NIST's test vectors (`desktop/mlkem768_vectors.json`) and, where it
is installed, against the `cryptography` library's own ML-KEM.

## Privacy

- **Message requests.** The first messages from someone you have never
  talked to wait under *Message requests*: no notification text, no
  download, no call. Until you answer, the relay is told to send them no
  receipts, so they see one tick. *Accept* turns receipts on and gives you
  the composer. *Decline* removes the request without blocking them: if
  they write again, it is a request again. *Report* tells the relay's
  operator who and why (never what they wrote), and can block them too.
  *Settings → Privacy → Blocked* lists everyone blocked, to unblock.
- **No typing indicators, no "online" or "last seen".** Nobody can be
  pressed into turning them on, because they do not exist.
- **Disappearing messages** are deleted by both apps, each copy that long
  after it was read (the sender's, after it was sent); a deleted message
  cannot be brought back by the relay sending it again.
- **Verifying** compares a code worked out from both public keys (twelve
  groups of five digits, the same on both phones only if each has the
  other's real key), so a relay that handed out a false address would be
  found out.
- **Link previews** are off unless turned on. Then the sender's device
  fetches them — only from public addresses, a little of each page, three
  redirects at most — and sends them inside the sealed message; a preview
  must be of a link the message holds, with a small JPEG. Every link, with
  or without one, first shows where it really leads: letters borrowed from
  other alphabets in their plain `xn--` form, and a note for bare numbers
  and unencrypted pages.

## Backup

*Settings → Backup* writes one file: every identity on the device (its key,
chats, contacts and groups), the pictures, and if you like the photos and
voice notes. It is a tar stream sealed in 1 MiB chunks with AES-256-GCM,
under a key that scrypt derives from your passphrase; each chunk's
associated data carries its number and whether it is the last, so a file cut
short, reordered or altered does not open. Without the passphrase nobody can
open it.

*Restore* (in the same place, or on first run) adds the identities in a
backup to the device, with their chats; one already there keeps its own,
unless it has none yet. Use each identity on one device at a time: the relay
delivers to whichever device is connected.

Android's own cloud backup does not copy Ferritext: its keys and chats leave
a phone only in a backup you make and seal here.

## What's in here

| Path | Purpose |
| --- | --- |
| `main.py` | The complete client (Buildozer's entry point **must** be `main.py`), and its two Android services: Android starts them from it too, the sync service and the call service that keeps a call's microphone on while the app is off screen. |
| `buildozer.spec` | Android packaging: requirements, permissions, service, icons. |
| `data/` | The Ferrite Text Messenger mark (`ferritext_logo_packet.svg`; `ferritext_logo.svg` is the supplied disc it came from) and the images rendered from it, including the desktop window icons (`icon_desktop.png`, `.ico`). |
| `data/fonts/` | Ferritext Serif (four styles), the capitals face `ui.ttf`, and their licences. |
| `tools/make_logo.py` | Renders every image in `data/`, and the notification icon, from the SVG — no dependency beyond Pillow. |
| `tools/build_fonts.py` | Rebuilds Ferritext Serif from EB Garamond, Noto Emoji and Twemoji (needs fontTools). |
| `tools/bundle_desktop.py` | Builds the single-file desktop release, and `--verify` checks one against the source. |
| `tools/p4a_hook.py` | Build hook: the service types Android 14 requires, the notifications' receiver, and the camera's FileProvider. |
| `recipes/libopus/` | A python-for-android recipe for libopus, for calls in Opus. |
| `android/` | What Android compiles or ships beside the Python, in one folder. Java: `CallAudio` (a call's microphone and speaker, on threads of their own, and where the sound goes), `Notifier` and `NotifyReceiver` (notifications, and what is done to them), `QrReader` (QR codes in photos). Resources: the cleartext policy for the relay, the status-bar icon, the camera's folder, the launch screen at night. |
| `CREDITS.md` | The open-source work Ferritext is made with, and its licences. |
| `desktop/` | Desktop packaging (Nuitka), the headless test suites, and the tools that capture and time the app. |
| `.github/workflows/build-apk.yml` | Tests → signed APK → GitHub Release with both deliverables. |

## Testing

Every suite runs headless against the real code and exits non-zero on failure;
CI runs them all before it will build an APK.

```bash
python3 desktop/test_service.py main.py                  # no display at all
xvfb-run -a python3 desktop/test_core.py  main.py old.py # old.py: previous release
xvfb-run -a python3 desktop/test_flows.py main.py
xvfb-run -a python3 desktop/test_design.py main.py
xvfb-run -a python3 desktop/test_composer.py main.py
xvfb-run -a python3 desktop/test_bundle.py main.py
xvfb-run -a python3 desktop/smoke_desktop.py main.py
xvfb-run -a python3 desktop/smoke_chat.py main.py
xvfb-run -a python3 desktop/test_navigation.py main.py
xvfb-run -a python3 desktop/test_pictures.py main.py
xvfb-run -a python3 desktop/test_calls.py main.py
xvfb-run -a python3 desktop/test_notify.py main.py
xvfb-run -a python3 desktop/test_gestures.py main.py
xvfb-run -a python3 desktop/test_privacy.py main.py
```

| Suite | Covers |
| --- | --- |
| `test_core.py` | Envelope extension (incl. replay/tamper refusal), wire compatibility with the previous release, migrations both ways, delete-for-both authorisation, tombstones, extension sanitising, group membership rules (who may invite, change and remove), per-member receipts, reactions, ML-KEM-768 against NIST's vectors and the `cryptography` library, post-quantum envelopes (tampering, keys this device does not hold) |
| `test_flows.py` | Three real app controllers over an in-memory relay: replies, forwards, stars, delete for both, photos (EXIF stripping, rotation), expiry → ask again → re-upload, voice notes, notification rules, notes to self, groups (create, write, rename, remove, leave), reactions and taking them back, the reaction strip and emoji panel, switching theme and text size, post-quantum keys passing between people and groups, a new install asking for a message again |
| `test_design.py` | WCAG AA contrast of every text/ground pair in both themes, fonts registered and packaged with their licences, following the system theme, tracked capitals, stacking buttons, Back closing a sheet |
| `test_composer.py` | Real mouse clicks reaching the message field and its keys, hidden parts never swallowing a touch, the composer's two states, recording through a capture command into WAV, photos from a camera, icons drawn smooth |
| `test_bundle.py` | The single-file desktop release: built, verified, run from an empty folder (fonts and icons unpacked, checked, reused, repaired) |
| `test_service.py` | Both services started from `main.py` as Android starts them, app ⇄ service hand-over, the manifest hook |
| `smoke_desktop.py` | Launch, branding and loading screen through the real `App.run()` path |
| `smoke_chat.py` | Bottom-anchored short chats, scrolling, text selection and copy |
| `test_navigation.py` | Pages in layers, a long chat opening at once, the menu, back one step at a time, search and jumping into history, tap to react, holding chats (mute, pin, archive, unread, block, delete) |
| `test_pictures.py` | Making a picture (crop, size limit, nothing of the original file kept), refusing bad ones, versions, who is sent one and when, asking for a lost one, group pictures from the admin only, blocking, discs following a new picture, the framing sheet |
| `test_calls.py` | The codecs (ADPCM bit for bit audioop's; Opus with FEC and concealment), the packet seal (replay, tamper, direction), the jitter buffer, two call engines finding each other directly, through a relay and over the WebSocket, and two apps calling: ring, answer, talk, mute, hang up, decline, busy, cancel, no answer, a stale offer, blocking |
| `test_notify.py` | Notifications from the sync thread (a paused app still notifies), what they are made of, mute and its end, blocking that holds and releases, replying and marking read from a notification, drafts, a call while the app is away |
| `test_gestures.py` | Real taps and swipes through the window: back, the menu, reply, taps that are not swipes, voice notes at speed and seeking |
| `test_privacy.py` | Disappearing messages, verifying a contact, links and where they lead, link previews (off by default; fetched only from public addresses), encrypted backup and restore |

To get `old.py`: `git show v9.1.0:main.py > old.py` (or the latest tag before
the version under test).

### Looking at a change, and measuring it

Capture every change that alters a screen before and after, and measure
every change that might cost time. Both tools run whichever `main.py` they
are given. The version before your change runs from a git worktree, which
has its own fonts in `data/`:

```bash
git worktree add ../before HEAD~1        # the version before your change
xvfb-run -a python3 desktop/capture.py ../before/main.py shots/before
xvfb-run -a python3 desktop/capture.py main.py shots/after
xvfb-run -a python3 desktop/bench.py ../before/main.py > before.json
xvfb-run -a python3 desktop/bench.py main.py > after.json
git worktree remove ../before
```

- **`capture.py`** saves 21 screens (`shots/after/black_chat.png`, …),
  built from the same chats on every run. `--theme` picks `black` (the
  default), `dark` or `light`. It lists any layout problem it finds (text
  cut off or overflowing, parts outside their box, touch targets under
  40 dp) and then exits 1. Compare the two folders screen by screen.
- **`bench.py`** times drawing text, the store's reads on 50,000
  messages, and sealing and opening a message, each the median of five
  runs. Compare the two files. A
  difference under a tenth is noise, and timings differ between machines,
  so compare only runs from the same one.
- In the change's description, say which screens changed and which
  numbers moved. The pictures and numbers that go to review come from
  these two tools, so anyone can make them again.

## Building it yourself

Requirements: Linux (or WSL) with Python 3, a JDK, and `git zip unzip gcc make
autoconf automake libtool libltdl-dev`. Buildozer downloads the Android SDK/NDK
on first run.

```bash
python3 -m pip install --user buildozer cython
buildozer android debug          # produces bin/fext-15.0.0-*-debug.apk
```

The mark and icons are regenerated with `python3 tools/make_logo.py` (Pillow
only).

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

4. **Pin the key** (optional). Under **Variables** on the same page, add
   `FEXT_KEY_SHA256` with the key's SHA-256 fingerprint as `keytool -list -v`
   prints it. A build whose keystore holds any other key then stops within
   seconds, before it makes an APK that could not update anyone's install.

**Publishing from more than one repository** (a mirror, or a copy you
release from): give each one the same four secrets and the same
`FEXT_KEY_SHA256`. GitHub cannot copy a secret or show it again, so the
values come from the keystore file and your password store.

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
signature differs, so no build can update another. Such a build is only the
run's artifact: the default branch publishes no Release signed with a
throwaway key, and its run fails, naming the secrets to add.

A signing key is **never committed to this repository.**

## Configuration notes

- **Relay**: pinned in `main.py` (`SERVER_HOST`, overridable with
  `FEXT_SERVER_HOST`). The client probes `https://` first and falls back to
  `http://`, caching the winner and re-probing when it stops working. A relay
  that serves TLS on a port of its own names it in `/api/health`
  (`tls_port`, from 12.0.0); reached over `http://`, the client tries that
  port and moves there once the certificate verifies against the roots it
  ships (certifi), and never accepts one that does not.
- **Cleartext is scoped, not blanket.** Android blocks cleartext by default, so
  `android/network_security_config.xml` permits it for the relay
  host only. It is wired up via `android.add_resources` plus a `p4a.extra_args`
  entry in `buildozer.spec` (**not** `android.extra_manifest_application_arguments`,
  which buildozer 1.5.0 mangles). Once the relay serves HTTPS everywhere,
  delete that file and those two keys, and set `FEXT_REQUIRE_TLS=1`.
- **Permissions**: internet and network state; notifications (asked on
  Android 13+); microphone (asked the first time you record); the foreground
  service and its data-sync type (background delivery). No camera
  permission: the phone's camera app takes the photo with its own.
