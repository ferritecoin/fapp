# Ferritext — Ferrite Text Messenger

An end-to-end encrypted messenger built with [Kivy](https://kivy.org). One
auditable file, `main.py`, is the whole client: it runs on Android (packaged
here into an `.apk` with [Buildozer](https://buildozer.readthedocs.io) /
[python-for-android](https://python-for-android.readthedocs.io)) and, unchanged,
on Windows, macOS and Linux.

Its security model is transparency: no plaintext ever leaves the device.
Messages are signed (secp256k1 ECDSA), sealed (ephemeral ECDH, with ML-KEM-768
once the recipient's post-quantum key is known → HKDF-SHA256 → AES-256-GCM),
and only opaque ciphertext is transmitted. Photos, voice notes and video
messages are sealed with a fresh key per file before they are uploaded.

The Android application id stays `org.fext.fext` (`package.domain` +
`package.name`), and the identity/QR payload keeps its original `FEXT` tag:
the brand changed, the protocol did not, so every release talks to every
earlier one, and installs as an *update* to it when both are signed with the
same key (see [Release signing](#release-signing)).

## Downloads

Every push to the default branch is tested and built, and each new version is
published to a GitHub Release, whose files never change afterwards. Open the
**Releases** page (or the **`v19.0.1`** tag) and take:

| Asset | For |
| --- | --- |
| `Ferritext-19.0.1.apk` | Android 5.0+ — sideload it via *Install unknown apps*, or `adb install -r`. Calls need Android 6 or later |
| `Ferritext-19.0.1-windows.exe` | Windows 10 and 11 (64-bit) — the same app in one file, with nothing to install. Windows may say that it does not know the publisher: choose *More info*, then *Run anyway*. |
| `Ferritext-19.0.1-desktop.py` | Windows / macOS / Linux — one file with its fonts and icons inside. Run it with Python 3.11+ after `pip install kivy requests websocket-client cryptography qrcode pillow`. |

Each Actions run keeps them for a week too: the APK and the desktop file as
the **`ferritext-release`** artifact, the executable as
**`ferritext-windows`**. Pushes to other branches are tested and built, but
publish nothing.

On a computer, each of these adds something when it is installed, and
nothing breaks without it. The Windows executable has them inside already
(pyzbar for QR codes):

| Install | Adds |
| --- | --- |
| Opus — `apt install libopus0`, `brew install opus`, or `pip install pyogg` (Windows) | calls in Opus: clearer, on about half the data |
| `pip install sounddevice` | the best call audio, and choosing the microphone and speaker |
| `pip install numpy` | voice notes at 1.5× and 2× |
| `pip install av` | video calls: the camera, sharing the screen, recording a call (17.0); recording video messages, and playing them in the chat (18.1); playing voice notes recorded on a phone (the `ffmpeg` program plays those too) |
| `pip install opencv-python` (or `pyzbar`) | reading a contact's QR code from a picture |
| `pip install coincurve` | sealing and opening each message several times faster (libsecp256k1, 16.0) |

**For China**, where GitHub's downloads are not dependable and WeChat
renames an APK so that it will not install, give people a download page
on your own host instead. `tools/download_page.py` makes one from a
release's APK: one page in Simplified Chinese and English, fetching
nothing from elsewhere, with the APK beside it, its size and SHA-256, a
QR code of the page's address, and the steps to install the app and keep
it running.

```bash
python3 tools/download_page.py Ferritext-19.0.1.apk site/ \
    --url https://your.host/ferritext/        # then serve site/ there
```

Relay 12.2.1 and later can serve the page too, at `/download/` on the
relay's own address: whoever can reach the relay can reach it.

## What's in 19.0.1

For a computer reached from afar for months, whose person cannot go back
to it: what is set up before leaving has to hold.

- **Before you leave**, in *Settings › Remote access*: each thing that
  lets the computer be reached for a long time, read from the computer
  and said with how to put it right. Ferritext starts when you sign in,
  keeps running and keeps the computer awake; Windows signs in by itself
  after a restart and its screen does not lock by itself; the firmware
  turns it on after a power cut (Ferritext cannot see that one); and two
  things to try while you are still there, from your phone on its mobile
  data: a session, and a restart from afar that comes back.
- **Ferritext keeps running.** While someone is listed, a small watcher
  starts Ferritext again if it closes, or stops answering for six
  minutes; closing its window brings it back, saying how to close it for
  good. One Ferritext runs at a time. Settings and identities are written
  so that a power cut halfway leaves the last good copy, and after a
  relay is reset the computer registers its name again by itself.
- **Keys** in a session from afar: Task Manager, the Start menu,
  switching windows, the desktop, closing a window, copy, paste, undo,
  select all, Esc and Delete. *Ctrl+Alt+Del* says what Windows keeps for
  itself and offers what can be done instead. *Programs* lists the
  windows open on the computer and closes one, or ends one that does not
  answer (not one run as administrator, nor Windows' own). *Restart it…*
  restarts the computer; Ferritext waits up to 20 minutes for it and
  connects again by itself.
- **A band under the call** says when their computer shows a Windows
  security screen (locked, a permission prompt, Ctrl+Alt+Del's) or an
  administrator's window, neither of which can be used from afar, and
  when your access ends within two weeks. The chat says so too, a week
  and a day before it does.
- **Two computers on one identity** are told apart: each has a name
  (*Remote access › This computer*), and *Connect to their computer*
  asks which.
- **Over mobile data** a shared screen sends nothing while it does not
  change: a picture when it changes, a sharper one a second later, and a
  key frame every two minutes, only if it changed. A picture waits behind
  a quarter of a second of bits, not a whole second's, and the rate steps
  down when pictures are lost or the delay grows. *Data saver* (in Keys;
  on by default on a phone) holds their screen to 1.5 Mbit/s. On a
  1080p desktop, an hour of a still screen cost 30 MB and now nothing;
  typing 33 MB, now 23; switching windows 142 MB, now 79 (64 with Data
  saver), and a new window arrives over a 2 Mbit/s link within 0.23 s
  (0.1 s with Data saver) where it took up to half a second.
- **Security.** What a relay answers is checked: a key that is not the
  address's own is refused, and a saved contact's key is never replaced
  by one that is not its address's. An offer from afar names the
  computer it is for and is signed with its time, so one the relay plays
  again, or sends to another computer, is set aside. With the app lock
  on, Ferritext locks itself as a session from afar starts: whoever
  connects needs its code to read your chats.
- For developers: `desktop/test_security.py` plays attacks on this
  client (a relay acting as the server, someone acting as someone else,
  crossing into another computer or identity, a power cut), each expected
  refused; `test_remote.py` and `test_video.py` have 19.0.1's sections,
  and `capture.py` the new screens.

## What's in 19.0.0

- **Control a computer from a call, or reach your own while away.** In
  a one-to-one call where a computer (Windows, or Linux on X11) shares
  its screen, the other side has *Control*; once the computer's person
  allows it, the pointer, a tap and the keys move it. A red bar on the
  computer says who is in control, with *Stop*.
- **Remote access** (*Settings › Remote access*, on a computer): the
  people listed there, each until a date and verified first, can choose
  *Connect to their computer* in your chat's menu. The computer answers
  by itself, without sound, through the relay only, shares its whole
  screen and lets them in; it says so on the screen and in a notice,
  and keeps a list of sessions. A session that drops is placed again
  by itself for ten minutes. To come back after a restart: *Start when
  you sign in* (Windows and Linux) and *Keep this computer awake*
  (Windows), with what the app cannot do for you said beside them.
- **Two panes on a computer.** A window 900 dp wide or wider shows your
  chats on the left and the chat, or any other page, on the right; the
  window opens where and as large as you left it.
- **Pinned messages.** *Pin* in a message's menu pins it for both of
  you (or the whole group). A bar under the chat's name walks through
  the pins, newest first; its list shows them all. In a group, its
  admins pin, and every member too once *What members can do › Pin
  messages* is on.
- **Video calls look better.** A camera is opened at the size and rate
  the call asks for (up to 1080 lines at 60 frames), a computer's
  graphics chip codes the picture where it can, and each step of the
  quality ladder has more bits (720p 2.5, 1080p 4.5, 1080p60 8 Mbit/s;
  two thirds of that in HEVC or AV1). A shared screen is read 30 times
  a second and coded for sharp text. The meter says what is really
  sent.
- 1080p60 is now sent in one-to-one calls when the other side shows
  your picture full size (it stopped at 30 frames before).
- For developers: `desktop/test_remote.py` (remote control, with X11's
  injector tried for real under Xvfb and Windows' on the Devices
  workflow); `playthrough.py --wide` walks the two panes; the walk now
  fails on an error the app carried on from.
- **Also in this release: 18.2.0**, which was not published on its own:
  everything a message can do on a tap, quoting part of a message, one
  text or code that carries both of an identity's keys to another
  device, much faster video on phones, *Start when Windows starts*, and
  an error in one screen that no longer closes the app (the README's
  *What's in 18.2.0*).

## What's in 18.2.0

- **Tap a message** for everything you can do with it. The quick
  reactions float over it as before, and beside it now a menu in
  words: *Reply*, *Copy*, *Copy the link*, *Forward*, *Edit*, *Star*,
  *Who has it*, *Select*, *Delete* (which asks: for you, or for
  everyone), and *Reactions* where it has any.
- **A reply's options**: tap what you are replying to, above the
  message field, to quote only a part of it or to send the reply, with
  its quote, in another chat.
- **A key for another device** (*My profile › Copy the key for another
  device*, or its code to scan): both of the identity's keys in one
  text, so a second device reads what is sealed to the post-quantum
  key too. *A new identity* takes it by paste or by its code. A
  private key alone (WIF or hex) imports as before, and the app says
  what it leaves behind.
- **Video calls on a phone draw much faster.** A picture now goes from
  the phone's decoder to the screen as the decoder's own rows, and the
  graphics chip makes the colours: on the test devices 4 to 8 ms a
  picture instead of 47 to 112. *Settings › Calls › Picture size on
  this phone* then offers 360, 480 or 720 lines of another's camera.
- **A call's meter says more**: the sound waiting to be played, a
  phone's speaker in its own account, and pictures that came too late
  to draw.
- **Start when Windows starts** (the EXE; *Settings › Notifications*):
  opens minimised when you sign in. Off until you turn it on; nothing
  runs before sign-in, and no administrator is asked.
- **An error in one screen no longer closes the app.** The app says so
  and carries on; if it happens again at once, it goes back to the
  chat list. The error is in the log in full.
- **Quicker to the touch.** The menu opens in a third of the time; a
  chat and the *New conversation* sheet open about a third sooner. A
  change of theme no longer stalls for a moment afterwards: memory is
  tidied when you are not touching the app and nothing is playing. A
  message that arrives alone is shown on the next frame instead of up
  to 150 ms later.
- A call that could not ring, because the other person has not accepted
  your chat request, leaves a line in your own chat saying so.
- On Windows, *Restore from a backup* also offers Windows' own dialog.
- The chat list's status line takes a second line instead of cutting
  its words on a narrow phone.
- For developers: `desktop/playthrough.py` walks through the app by
  touch, timing and picturing each step; CI runs it. See *Testing*.

## What's in 18.1.0

- **Video messages**: the camera key beside the message field offers
  *Record a video message*: up to a minute, with sound, sent sealed as
  a photo is and played in its bubble. Nothing is recorded out of
  sight: a recording stops where it is when you leave the app or a call
  begins, and waits for *Send* or *Delete*.
- **Away replies** (*Settings › Away*): someone who writes to you gets
  your words back, once a chat every four hours, never in a group.
  Nothing reads their message to answer it: the reply is a sentence of
  yours, sent by this device while it receives messages.
- **Where you are, in calls**: a phone that is called gives away no
  network address until it is answered, and *Settings › Privacy › Hide
  my location in calls* keeps every call on the relay.
- **Calls catch up sooner**: a call's first moments go through the
  relay, and its extra delay is now given back in the pauses between
  words, about four seconds sooner with a distant relay.
- Taking a photo asks for the camera permission first, as Android
  requires of an app that declares it: a phone that had never made a
  video call could not open its camera for a photo.
- A call that begins pauses a voice message you are recording, which
  then waits for *Send* or *Delete*: before, one recorded hands-free
  went on through the call and was sent by itself after five minutes.
- A call answered the moment it arrives is heard: the one called could
  be left silent, the microphone shown on but never opened.
- Going back and opening the same chat again at once no longer leaves
  an empty page.
- **Lists fade at their edges** where there is more to scroll to, in the
  chat list and in a chat; *Settings › Appearance › Fade the edges of
  lists* turns it off.
- **Quicker with many chats**: the chat list shows its first screenful at
  once and the rest just after, so the app opens and changes theme
  several times faster; a message arriving in a long chat no longer
  moves every message above it, and is drawn three times as fast.
- **A message that cannot be read no longer stops the ones behind it.**
  A malformed message could end the connection each time it arrived,
  so nothing after it came, in every version before this one. It is
  now shown as a message that could not be read, and the rest arrive.
  Updating is recommended for this alone.
- Text is shown as written: a group's name or a line in a call's chat
  that looked like formatting could close the app.
- **Back on a phone** goes back in one press when the keyboard has just
  closed; before, that press was spent on the message field.
- **The lock screen** no longer shows *Ready for new messages* all the
  time: the notification that keeps messages arriving is silent, and
  hidden while the phone is locked.
- **Video calls on a phone, the right way up**: the picture follows how
  the phone is held. Before, it could arrive on its side and squeezed.
- **Less delay in calls on a phone**: sound waits 40 ms to be played
  instead of all the phone would hold, and a picture that arrives late
  is skipped instead of queued. A phone asks for 360 lines of a camera.
- **A call to someone who has not accepted your chat request** shows
  them a missed call under *Message requests*. Nothing rings.
- **One identity on several devices**: each device rings, the one that
  answers has the call, and the others say *Answered on another
  device*. Set a second device up from a backup, not from the private
  key alone: a backup carries the identity's post-quantum key too.
- **On Windows**, a photo or a chat to import is chosen in Windows' own
  Open dialog.

## What's in 18.0.2

18.0.1's, and one more from the emulators:

- Sharing your screen from a slow phone no longer gives up while Android
  is still starting the share: the app waits up to 20 seconds for it,
  where it waited 6, which an emulator on a busy machine overran.

## What's in 18.0.1

18.0.0's features, with what the emulators (Android 5, 11 and 16) and the
Windows and Ubuntu runners found in it:

- Messages sent one after another, or several forwarded at once, arrive
  in the order you sent them; before, a quick burst could arrive
  shuffled. Edits, reactions and deletes keep their order too.
- A theme switch that cannot be read no longer changes the theme: a
  phone whose dark setting could not be read once switched to dark,
  rebuilt every screen and closed whatever sheet was open, then switched
  back four seconds later.
- On Windows, a setting changed while the app is busy is no longer lost:
  the app's own threads read the settings file as it was being
  replaced, which Windows refuses.
- The debug build's self-test skips a call's checks on Android 5, where
  calls are off, and the device checks wait out a just-booted emulator.

## What's in 18.0.0

- **A group's admins**: its owner can make other members admins, each
  with rights of their own (*Group › a member › Make … an admin*): to
  change the group's name, photo and timer, add members, remove them,
  set what members can do, manage topics, delete members' messages, and
  make admins of their own. The group's page marks the owner and each
  admin, and an admin sees which rights they have. Leaving, the owner
  hands the group to their first other admin.
- **The admin log** (*Group › Admin log*): who changed what, newest
  first, the last fifty changes. It travels in the group's own state,
  as its members do, so it needs no relay.
- **An admin deletes members' messages for everyone**, whatever their
  age (with that right); the admin log says who did.
- **A link for anyone** (*Group › Invite with a link*; in a call you host,
  *More › Invite with a link*): whoever opens it asks to join, as
  whichever of their identities they choose, and you let each one in or
  not (*Asking to join*). The link carries no key and no token: nobody
  gets in by holding it. *Stop the link* ends it. *New conversation ›
  Join with a link* opens one sent to you.
- **One more person into a 1-to-1 call** (*More › Add someone*): the call
  becomes a group call on the relay. The other side comes along by
  itself, and the one you add is asked in.
- **Notes on a call**: *Leave a note* on a 1-to-1 call nobody answered
  writes a reply to its card. A group call's card counts its notes and
  opens them as a thread of their own: what you write there goes to that
  call, and Back returns to the whole chat.
- **A phone's screen in a call** (Android, *More › Share your screen*),
  after Android's own question. Android shows that the screen is shared,
  and its own *Stop* ends it.
- Fixes found by the evaluation pass: a reply to a note stays in its
  thread; a full group says it is full when you let someone in; two
  admins changing a group at the same moment end the same on every phone,
  apps before 18.0 included; only the link's maker can answer your ask
  to join (anyone else holding the link could cancel it).

## What's in 17.4.0

- **What a group's members may do**, set by its admin (*Group › What
  members can do*): sending messages, sending photos and voice messages,
  and starting calls, each on or off for every member. The admin can
  always do all three. Anyone can still join a call, react, and delete
  their own messages.
- **Restrict one member** (*Group › a member › Restrict*): their
  messages, their photos and voice messages, or their new calls, for an
  hour, a day, a week or with no end. Their chat tells them what is
  stopped and until when; nobody else is told. The admin's page marks
  them *Restricted*, and *Lift restriction* ends it.
- These rules travel inside the group's own state, like its members and
  name, so they need no relay. The restricted member's app keeps to them:
  the composer gives way to a note saying until when, and the outbox
  waits. Every other member's app also holds back what is sent anyway (by
  an app older than 17.4, or before the rules arrived), judged by when the
  relay took it. A message the relay took before a stop still arrives.
- **Several calls in one group**: while calls go on, the call button
  offers to join each of them, or to start another beside them (unless
  the admin keeps new calls to themselves). The banner says how many are
  going on.
- **Topics** (*Group › Topics*): the admin divides a group into topics,
  each a thread of its own beside General. A strip under the group's
  name shows All, General and each topic, with a dot where
  something is unread: the chat marks read only the topic it shows. A
  message goes to the topic shown, or to its original's topic for a
  reply. Under *All*, each message names its topic beside its author. A
  closed topic takes new messages from the admin alone. **Turning topics
  off loses nothing**: every message shows in the one chat with its topic
  named, and turning them on again brings each topic back with its
  messages.
- An app before 17.4 ignores the rules and the topics. It sees one chat,
  writes to General, and is not stopped by the rules, but what it sends
  against them is held back by 17.4 apps.
- **ARCHITECTURE_client.md**: how the app is built, for whoever works on
  it next.

## What's in 17.3.0

- **Calls on Android 5.0 and 5.1 say why they cannot start**: the call
  and video buttons say calls need Android 6 or later, where 17.2 started
  a voice call that never connected and a video call that closed the
  app. Messages and the rest work there as before.
- **A phone's video coder that fails ends the video, not the app**: the
  camera stops, the call says why, and the call goes on. Before, a fault
  on the coder's own thread closed the app.
- **React in a 1-to-1 call**: *More › React*, as in a group's call. The
  emoji shows over your side of the call on both screens for four
  seconds. It goes as its own sealed packet, sent three times in case one
  is lost, and is shown once; an app before 17.3 ignores it.
- **A notice from the relay's operator** (relay 13.4) opens once, in a
  sheet of its own that says who it is from, and the app tells the relay
  it was shown. The operator still cannot read your messages.
- **What the relay's operator stopped, in words**: relay 13.4 can stop an
  identity's messages to groups, its calls, its media or all its messages
  for a time. The app says which and until when. A message waits in its
  chat, with its clock, and goes when the stop ends, as any unsent one
  does (by itself within a day, then at a tap); the outbox leaves it
  until then instead of asking every 30 s. A call ends at once, saying
  so. Messages to you still arrive.

## What's in 17.2.1

- **Windows: a setting saved at the wrong moment is no longer lost.**
  Windows refuses to put a file in place while anything has it open
  (another part of the app, or the virus scanner), and the app gave up at
  once. It now waits a moment and tries again: settings, profile
  pictures, backups (and automatic ones put in a folder), and its own
  fonts and icons. The new Windows test environment found it.
- **Android video: a picture the phone's decoder has no room for** counts
  as a damaged one, so the call asks for a fresh key frame at once; before,
  the pictures after it showed broken until the next key frame, up to
  10 s later.
- The emulators' self-test hands the camera's pictures to the decoder at
  a call's pace and waits for the last ones (on Android 11 it had counted
  12 of 24 back), and three in four must come back.
- Device checks: Android 5.0's install, Windows' slower starts, its
  smaller screen and its `bash`; a suite that stops early shows how in
  the job's log.
- **Known: calls do not work on Android 5.0 and 5.1** (a voice call
  cannot start; a video call closes the app). Messages and the rest do.
  The new Android 5 test environment found it; the next version
  addresses it.

## What's in 17.2.0

- **A call's controls in three groups** on a computer: the devices (the
  microphone and the camera, each with a ▾ for which one and its
  settings; the screen), the call's own (*Raise*, *React*, *Chat*,
  *More*), and *Leave* set apart, with a host's *End for everyone*. A
  phone has one row: Microphone, Camera, Raise, Chat, More, Leave.
  *More* holds the people, the view, recording, the call's details and
  its timer, and the call's settings, so nothing needs leaving the call.
- **A meter of the connection**: bars on the call's screen; a tap shows
  the numbers (the delay there and back, the packets' length, what is
  lost, the jitter, the picture's size and rate). Voice and video,
  1-to-1 and groups.
- **The best the connection allows, in group calls too**: while it is
  very good, a group call's sound goes in 5 ms packets, played from 15 ms
  in hand (a click through two apps: 36 ms, from 46), and the camera
  climbs back to *Settings › Calls › Video quality*; while it is poor,
  20 ms packets and a smaller picture.
- **The lobby offers each device**: a phone's front or back camera; a
  computer's cameras, microphones and speakers, each with an ⓘ that says
  what it does (a tap, or the pointer).
- **A call that floats**: leave the call's screen and it shrinks to a
  small window you can drag over any chat; a tap brings it back. On
  Android 8 and later, a video call goes into picture in picture when you
  leave the app. On a wide screen your chats stay beside the call.
- **React**: an emoji over your tile in a group call.
- **Call waiting**: a call while you are in one rings softly in a bar
  over your call: *Decline*, or *End and answer* (*Leave and answer* in a
  group's call). Unanswered, it is a missed call, as before.
- **Every identity rings**: a call for another identity on this device
  rings with its name ("for @kgt03"), and answering switches to it. Its
  bell in *Identities* keeps it quiet. Each one keeps its own connection
  to its relay.
- **A shared screen opens the focus view by itself**, unless you chose a
  layout in that call, and the gallery comes back when it stops.
- **Call links** for a group's members: *More › Copy the call's link*,
  sent in any chat, opens the call's lobby for any member. The call's
  key stays in the link's fragment, which the relay never sees.
- **Send later**: hold *Send* (or right-click it) and choose a time. The
  message waits in the chat with its clock and goes at that time, from
  the app or Android's background service; until then *Send now* or
  delete it.
- **Plan a call** (the chat's ⋮ menu): a card with its time for everyone
  in the chat, a reminder five minutes before, and *Join* on the card
  from ten minutes before.
- Recordings can go into backups: *Settings › Calls › Recordings in
  backups* (off unless turned on).
- A camera nobody shows is sent at its smallest size until it is shown;
  the call's words stay readable over a light shared screen.
- Android: the video decoder's class is found as a call opens, as 17.1
  did for the others (an emulator's self-test found it missing).
- Device checks: `.github/workflows/devices.yml` runs a debug APK's
  self-test on Android emulators (5.0, 11 and 16) and the desktop suites
  on Windows and Ubuntu, when started by hand (`tools/devices/`).

## What's in 17.1.0

- **Calls that ring.** A call's offer is timed by the relay's clock, which
  both devices share: a phone whose clock was a minute off made each call
  it placed a missed call on the other side, while it rang on for nothing.
  An offer that does arrive too late is a missed call there, and the
  caller's phone stops ringing at once. The relay's clock also signs this
  app's requests, so a device with a wrong clock still connects.
- **Sound and video on more phones.** A call's Android parts (CallAudio,
  VideoCall) are found as the call opens: looked for from a call's own
  thread, Android searched only the system's classes, and the call's sound
  failed ("JVM … org.fext.call.CallAudio"); a phone could also be told it
  "cannot code video". H.264 is always offered: every Android phone codes
  it.
- **Who called.** A 1-to-1 call's card sits on the caller's side of the
  chat, with the caller's picture: "alex is calling" or "Calling alex…",
  then "Missed voice call" in red, "Outgoing" or "Incoming". *Call back* (or
  *Call again*) under a call that was not answered. An offer that never
  rang here (this device in another call, or offline) leaves the same card.
- **A voice call stays a voice call**: no camera in its lobby or on its
  screen; a video call is chosen as one. A computer can still share its
  screen.
- **The presenter's face**: a shared screen shows its presenter's camera in
  a corner, in a 1-to-1 call and on a group's stage (*Settings › Calls ›
  Their face while they present*, on unless turned off).
- **An incoming call you set aside** stays under the app bar on every
  screen while it rings: tap to answer.
- **Clearer**: "You can call alex once they accept your chat request or
  write to you" (accepting a request now tells the other app, so calls
  open both ways at once); "alex needs a newer Ferritext to take calls".
  Calling another identity on this same device says why it cannot.
- **Deleted User sooner**: once connected, the relay is asked after the
  people of your newest chats, and after anyone whose chat you open (each
  at most every six hours), not only when a message to them fails.
- The app names its version to the relay ("Ferritext/17.1.0 (android;
  Python 3.11)"), so the relay's console says which versions connect;
  nothing that tells one person from another.

## What's in 17.0.0

- **Video calls.** A chat's header has a video button, left of the call
  button: its lobby opens with your camera on, showing what the others
  will see. A voice call can turn its camera on at any time. In a 1-to-1
  call their picture fills the screen and yours sits in a corner (on a
  phone, a tap on it turns to the other camera); the picture goes straight
  between the two devices when it can, as the sound does. In a group's call
  each camera is a tile of the gallery or the focus view.
- **Share your screen** from a computer, as a tile of its own. Phones come
  later; on Linux it needs X11 (Wayland lets no app read the screen).
- **A picture that fits.** *Settings › Calls › Video quality*: 360p to 1080p
  at 60 frames a second, 720p unless chosen; a computer also picks its
  camera there. A picture is never sent larger than the other side shows it,
  and steps down while packets are lost, its sound with it. In a group's
  call each camera is sent in three sizes, and each member is passed only
  the size its tile needs, nothing for a tile out of view or a call set
  aside (relay 13.2 or later; an older relay's calls stay voice only).
- **H.264 everywhere**; HEVC or AV1 when the sender codes it in hardware and
  everyone in the call decodes it.
- **Less delay:** *Settings › Calls › Prefer*. *Latency* (the default)
  sends the sound in packets of 5 or 10 ms, 20 only while the connection
  struggles; *Quality* sends 20 ms packets, which code speech best and use
  the least data. Group calls follow it too (20 ms while anyone in the call
  is on an app before 17.0).
- **Record a call**, off unless turned on in *Settings › Calls*: a Record
  button in calls; everyone in the call is told and sees a red mark while it
  runs. The recording (the picture on your stage and every voice) is sealed
  on this device as a backup is; *Settings › Calls › Recordings* plays it,
  saves a plain copy or deletes it. Computers only, for now.
- **Automatic backups**, off unless turned on in *Settings › Backup*: every
  day, week or month, with or without photos and voice notes, to a folder
  chosen once (on a phone, one the system's picker gives, a cloud drive
  too); the last three are kept. The passphrase is asked once: this
  device's keystore keeps the key, and each backup opens anywhere with the
  passphrase.
- **Delete for everyone** within three days of sending (editing stays 15
  minutes); an older message is deleted only for you.
- **An @ beside a group** in the chat list while a message that mentions
  you is unread.
- **While the app lock is on**, a notification says only the chat's name and
  *New message*.
- **Screen security on Windows** 10 (2004) and later, off unless turned on:
  the window stays out of screenshots and screen sharing.
- Video on a phone is coded by Android's own coders (`android/VideoCall.java`)
  and waits for a phone to prove it.

## What's in 16.2.0

- **Group calls.** A group's chat has a call button: *Start call* opens a
  lobby with your microphone and camera off, and nothing of yours is sent
  until you join. The call shows in the chat as a card, live while it
  lasts, then *Call ended* with its in-call chat folded under it; members
  see a banner at the top of the chat and join from it. In the call:
  gallery and focus views, raised hands in the order they went up, an
  in-call chat, and who is speaking ringed. Sound goes through the relay
  (relay 13.1 or later), sealed with a key only the call's members hold.
  Video and screen sharing come in a later update.
- **For the host:** *Mute everyone* and *Lower all hands* (each can turn
  theirs back on), and *End call for everyone*.
- **On a computer, keys as in Meet:** Ctrl+D the microphone, Ctrl+E the
  camera, Ctrl+Alt+H the hand. Back sets a call aside under a bar that
  says who is speaking, and leads back to it.
- **Edit a message** within 15 minutes of sending it: hold it, then
  *Edit*. It shows as edited on both sides (on apps from 16.2; older apps
  keep the first words, and you are told when one will).
- **Mentions in groups:** write @ and the start of a name. A muted group
  still tells you when it mentions you.
- **App lock:** *Settings › Privacy › App lock* asks for a code when the
  app opens and when it comes back after the time you choose; on a phone
  with one, a fingerprint or face opens it too. Phones offer it once
  (*Maybe later* says no for now). It locks the screen; a backup's
  passphrase seals the files.
- **Screen security** on phones, on unless turned off: chats stay out of
  screenshots, screen recordings and the app switcher.
- **Backup reminders:** *Settings › Backup*: never, daily, weekly or
  monthly.
- **Experimental**, off unless turned on: a clipboard that forgets what
  you copied a minute later, and a keyboard asked for no suggestions.
- **A new version is said once** when the relay names one, and
  *Settings › About* keeps saying it, with where to get it.
- **A theme change shows in about a third of the time**, and a group
  call's screen keeps up when the speaker changes often.

## What's in 16.0.0

- **Send and Call work on phones again.** A tap on Send was taken for the
  start of holding the microphone, and sent nothing; the call button did
  nothing at all. Both work by finger now, and holding the microphone
  still records. Each finger is also one touch, where Android had made it
  two.
- **What your finger is on lights up**, in lists too, the moment you touch
  it: release quickly, or hold, and it acts.
- **Calls at 48 kHz in stereo**, between two phones or computers on 16.0:
  *Settings › Calls › Call quality*, Standard, High or Ultra (96, 128 or
  192 kbit/s; a call sends no more than the other side chose). With
  *Adapt to the connection* on, packets shorten to 5 ms on a steady
  connection, for less delay, and the bitrate steps down while packets are
  lost. Each call's line in the chat now says how it went: its quality, the
  way it took, the round trip and what was lost, on your device alone.
  Calls with an older app are as before.
- **A call that cannot be made says why**, in the chat, not only on the
  chat list's line.
- **Deleted User.** When the relay no longer knows someone (it was reset,
  or they left it), their chat says so. What you write waits, with its
  clock, and goes by itself once they are back.
- **Backup and restore, tested end to end**, and three faults fixed: a
  backup whose database was damaged no longer adds its identity before the
  damage is found; on Android a large backup is read and written without
  freezing the app; and a file the phone cannot read is said to be so.
- **Messages sealed and opened several times faster** where libsecp256k1
  is installed (the Windows executable has it), and post-quantum keys
  about a quarter faster everywhere.
- **On computers:** text is firmer on ordinary screens, and file choosers
  fit a window made smaller. The Windows executable runs on Python 3.13,
  with every package at a pinned, current version (Pillow 13).
- **Pictures from elsewhere** are read as JPEG, PNG or WebP only, so no
  other picture reader ever sees what someone sends.

## What's in 15.1.1

- **Ferritext for Windows, in one file.** `Ferritext-15.1.1-windows.exe`
  is the same app with nothing to install: Python, and what the desktop
  file asks you to install for calls in Opus, phones' voice notes and the
  choice of microphone and speaker, are inside it. It keeps its data where
  the desktop file does (`%USERPROFILE%\.fext`), so you can move from one
  to the other and keep everything. Its first start after a download
  unpacks it, which takes a few seconds; later starts do not.
- **Two ticks once it is delivered.** When the other phone took a message
  within milliseconds of it reaching the relay (as phones that are online
  do, in groups most of all), the relay's word that it was delivered could
  arrive before the app had noted the message's number at the relay, and
  was lost: the message kept one tick until it was read, or for good where
  read receipts are off. The app now holds that word for its message.
- **Checked against every version still in use**: see *Mixed versions,
  and upgrading* under [Compatibility](#compatibility).

## What's in 15.1.0

- **Three themes.** Axiom, the app as it was; Overclock, phosphor on
  obsidian in a monospaced face; and Ferrite, graphite and cyan. Choose
  under *Settings › Appearance › Theme*; each has a light and a dark side.
- **Every script in everyday use.** Chinese, Japanese and Korean, Arabic,
  Hebrew, the scripts of India, Thai, Burmese, Khmer, Georgian, Armenian,
  Amharic and the rest showed as empty boxes. They are drawn now, letters
  joined and marks in place, and right to left where they are written so.
- **The app in Simplified Chinese**, by the phone's language or under
  *Settings › Language*. Chinese lines break by Chinese rules.
- **Keeps running on phones that stop it.** Where the maker stops apps in
  the background (Xiaomi, Huawei, Honor, OPPO, vivo and others), the app
  offers once to set itself up and opens each setting in turn. *Settings ›
  Notifications › Keep Ferritext running* shows what is done.
- **A download page** for people who cannot reach GitHub, such as in
  mainland China: see *Downloads* above.
- A new username asks for notifications and starts receiving at once,
  not at the app's next start.
- For developers: `desktop/capture.py` captures 28 screens, among them
  chats in other scripts and the verification sheet.
- **Coming from an earlier version?** Everything in 15.0.0 and before,
  below, is new to you too.

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
  data, and losses concealed. Between 16.0 apps, **48 kHz stereo** at the
  quality chosen in Settings (Standard, High or Ultra: 96, 128 or 192 kbit/s),
  with packets that **adapt to the connection** — down to 5 ms on a steady
  one — and a line under each call in the chat saying how it went, kept on
  the device alone. **Bluetooth** headsets and a choice of where the
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

**Mixed versions, and upgrading.** Before a release, every app release
since 9.1.2 is run as itself, side by side, through every relay release
since v8. Messages with both ticks, replies, reactions, photos, voice
notes, delete for both and groups pass between every pair, both ways. An
app upgraded in place — installed over the old one, or the new file run
on the same computer — keeps its identity and every message, file and
setting, and goes on talking to people who have not upgraded; on a
computer, the release before still opens what the newest wrote. A relay
upgraded in place delivers what was waiting for phones that were off, and
phones reconnect by themselves. The one difference found is the lost tick
that 15.1.1 fixes, which releases before it still show.

**Remote control and pinned messages (19.0).** An app before 19.0
ignores what it does not know: a remote session's offer rings there as
an ordinary voice call (its caller hangs up after 30 seconds without a
shared screen), control is not offered with it, and a pin changes
nothing on it. 19.0 says it shows pins in the features it sends (bit
64). Its local database is schema 14 (one new table); 18.x opens it as
before. Nothing needs a newer relay.

**Video messages (18.1).** An app before 18.1 shows one as a text
message with its words ("Video message (0:42)"), acknowledges it as
any message, and takes a reply to one as a reply. Its file needs
nothing new of the relay (a sealed blob, as a photo). 18.1 says it
plays them in the features it sends with every message (bit 32).

**With relay 13.4.** Its operator can send a notice and stop an
identity sending (see What's in 17.3.0). An app before 17.3 ignores the
notice (the relay keeps it until an app shows it: 17.3, once they
update), and shows a stopped message as not sent yet, trying it again
every 30 s until the stop ends. Nothing else changes for any app since
9.1.2, and 17.3 talks to every earlier relay as 17.2 does.

**With relay 13.5.** It changes only its operator's dashboard: every app
talks to it as to 13.4. **Between 17.4 and older apps**, a group's rules
and topics are ignored by the older app (see What's in 17.4.0); messages,
calls and everything else pass as before.

**With relay 13.6.** It adds its operator's explorer log and the relay's
resources to the dashboard: every app talks to it as to 13.5. **Between
18.0 and older apps**, an app before 18.0 follows the group's owner alone:
another admin's change reaches it when the owner's app sends it on, at
once. It does not take another admin's photo, timer or deletions. Links
for anyone, notes and Add someone need 18.0 on both sides (Add someone
says so); a note shows to an older app as a reply to the call.

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

**What members may do (17.4).** The group's state, signed by its admin,
also carries its rules: which of sending messages, sending photos and
voice messages, and starting calls every member may do, and any one
member's restriction with its end. Each app keeps to the rules for its own
messages, and holds back a member's message that broke them, judged by
the time the relay took it, so an older app cannot get round them.

**Topics (17.4).** The state can also list the group's topics. A message
names its topic inside its signed extension block; the relay still sees
ordinary one-to-one traffic. Turned off, the list stays in the state and
each message keeps its topic, so turning topics on again loses nothing.

## Type and themes

**Three themes** (Settings › Appearance › Theme), each in a light and a dark
side (Light or dark: system, light or dark):

| Theme | Its text | Its capitals | Its look |
|---|---|---|---|
| **Axiom** (the default) | EB Garamond | Titillium Web Light | warm paper and ink, gold marks; the dark side on black or a warm grey |
| **Overclock** | IBM Plex Mono | Silkscreen | obsidian and phosphor by night, violet and neon by day (a dark page too), square corners |
| **Ferrite** | Open Sans | Titillium Web Light | graphite with cyan by night, slate with teal by day |

Kivy draws a label with one font file and cannot fall back to another, so
the emoji must live in the same file as the text. Each theme's text face is
merged with **Noto Emoji** (*Ferritext Serif*, *Mono* and *Sans*), and the
emoji are drawn in colour, from **Twemoji**'s graphics, laid over Noto
Emoji's outlines as the font's colour layers (COLR/CPAL), which FreeType
draws at any size. Flags, skin tones, families and keycaps are joined into
one picture by the font itself. A system that cannot draw colour fonts draws
the outlines instead, in the colour of the text. Kivy neither slants nor
emboldens a face itself, so Mono and Sans, which ship in two styles, show
italic text upright.

**Chinese, Japanese and Korean** are in one face for every theme,
*Ferritext CJK*: Noto Sans SC with Noto Sans KR's Hangul, cut to the
characters of everyday text (GB2312, Big5's first level, JIS X 0208 and
KS X 1001: about 13,500), with the emoji. A label whose text has one of
them is drawn in it (`_resolve_font_name` in `main.py`), and so is a text
field holding one.

**Every other script in everyday use** is in a second face, *Ferritext
World*: Noto Sans (Latin, Greek, Cyrillic) with Noto's faces for the 21
scripts Unicode recommends for everyday use that no other face has (UAX
#31: Arabic, Hebrew, Thaana, Devanagari, Bengali, Gurmukhi, Gujarati,
Oriya, Tamil, Telugu, Kannada, Malayalam, Sinhala, Thai, Lao, Tibetan,
Myanmar, Khmer, Georgian, Armenian, Ethiopic), with the emoji. A label is
drawn in its theme's face when that has every character it shows, else in
the first of the CJK and World faces that does; each font's characters are
read from the font itself (`font_characters`). So Greek, which IBM Plex
Mono lacks, is drawn in the World face in Overclock only.

Those scripts need more than their letters. SDL_ttf shapes text with
HarfBuzz, which joins Arabic's letters, builds the clusters of the scripts
of India and stacks South-East Asia's marks, once told the script: each
label is told the first in its text that needs it (`shaping_script`).
Arabic, Hebrew and Thaana run right to left, with numbers and Latin words
in them left to right, and Kivy lays every line out left to right: so a
paragraph is broken into lines in reading order, and each line is then put
in the order it is shown by the Unicode bidirectional algorithm (UAX #9,
`bidi_levels` and `display_order`, which agree with Unicode's own test
file). A paragraph that begins right to left lines up on the right, and a
name or preview cut short loses the end it is read to. Thai, Lao, Khmer and
Burmese, whose words are not spaced, break between letters and never
between a letter and its vowel or mark (between words would take a
dictionary). A text field with right-to-left text lines up on the right
and puts its caret where the characters are shown; selecting part of such
text still follows the order it is stored in.

Capitals carry letter-spacing the app adds itself, since Kivy's text has
none. Every font here is under the SIL Open Font License 1.1, and Twemoji's
graphics under CC-BY 4.0; the licence texts ship beside the fonts and inside
the APK. `tools/build_fonts.py` rebuilds the merged faces from pinned,
checksummed upstream files.

The themes' colours are sets of tokens at the top of the client's design
system. `desktop/test_design.py` measures every text/ground pair each theme
uses against WCAG AA (4.5:1 for text, 3:1 for marks such as ticks).

Kivy has no screen-reader support yet. Every control nonetheless carries a
spoken label in the code, ready for when it does.

## Languages

The app is in English and Simplified Chinese (Settings › Language): the
system's language when it is one of them (Chinese in Traditional characters
is not taken for Simplified), or the one chosen there. A change rebuilds the
screens at once, as a new theme does. What people write is never translated.

Every phrase the app shows passes through `tr()` in `main.py`, which looks
it up in the language's pack, `data/lang/<code>.json`: each English phrase
and its translation, with the same `{fields}`. A phrase a pack lacks is
shown in English. `trn()` chooses by number (1 message, 2 messages), a
phrase kept in a table is marked `N_()` and translated where it is shown,
and `date_text()` writes dates. What goes to another phone stays in
English, as it always was: a control's text for apps that do not know the
control, and a photo's or a voice message's text, which the app shows in
its reader's language (`shown_body`).

```bash
python3 tools/phrases.py                         # every pack and its gaps
python3 tools/phrases.py --missing zh-CN > todo.json
python3 tools/phrases.py --merge zh-CN todo.json # the filled-in phrases
```

To add a language: give its code and own name in `LANGUAGES` (and add it to
`ONE_IS_SINGULAR` if, like English, it says "1 message" but "2 messages"),
translate its `--missing` phrases and `--merge` them, and teach
`pick_language` its system tags if it needs to. `test_design.py` fails
while any pack has a gap.

Kivy breaks a line only at a space, so it cut a Chinese sentence wherever
the room ran out, a full stop starting the next line. A wrapped label with
Chinese, Japanese or Korean is now broken into lines first (`break_lines`):
a full stop or comma never starts a line, an opening bracket never ends
one. Android's notifications get their words (Reply, Answer, the channels'
names) from the app (`android_notification_words`), and keep them for when
it is closed.

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

### Video messages (18.1)

The camera key beside the message field offers *Take a photo* or *Record
a video message*. The sheet shows the camera, as a mirror; *Record*
starts it, with sound, and *Send* sends it. A message is a minute at the
most, and is sent by itself when the minute is up. *Delete* keeps
nothing. Once it records, only *Delete* and *Send* close the sheet: a
tap beside it, or Back, loses no recording. In the chat it is a bubble
with its still picture, a play mark and its length: a tap plays it where
it is, a second tap stops it. A note left on a call (*Leave a note*) can
be one.

- **What is sent**: one MP4 (H.264 at 480 lines and 24 pictures a
  second, AAC sound: about 5.5 MB a minute), sealed and uploaded as a
  photo is, under the same 8 MB limit; a recording ends by itself before
  it would pass it. Where a relay takes smaller files, the sheet says
  how long a message may be there. Its still picture, a JPEG under
  16 kB, travels in the message, so the bubble shows before anything is
  downloaded.
- **Nothing is recorded unseen**: when the app leaves the screen, a call
  begins on this device (it rings, is placed or joined) or the camera
  fails or goes quiet, the recording stops where it is and the camera
  and the microphone are let go. What was recorded waits for *Send* or
  *Delete*. A call that begins also stops a message that was playing,
  and pauses a voice message being recorded, which then waits for
  *Send* or *Delete* too: it is never sent by itself (on Android 6,
  which cannot pause a recording, it is deleted, and the app says so).
- **Made the same way everywhere**: `main.py` writes and reads the MP4
  in plain Python (`mp4_write`, `mp4_read`), from the pictures of a
  call's camera and coder and the sound of the voice messages' recorder.
  No Java was added. A received file is read before it is kept: one that
  is not a video is not kept, and its bubble says so.
- **Downloads**: by itself on Wi-Fi; on mobile data it waits for a tap
  and says how large it is.
- **Older apps** show a video message as its words, "Video message
  (0:42)", and cannot play it. Until someone's app has shown it is 18.1,
  the recording sheet says so.
- **Where it runs**: recording needs what a video call needs (Android 6
  or later, or a computer with PyAV and a camera), and waits while a
  call is on. Where no video can be recorded the camera key takes a
  photo at once, as before. A computer without PyAV opens a received
  video in its own player. On a phone whose decoder does not open, the
  sound plays over the still picture.

Taking a photo now asks for the camera permission first. Since 17.0 the
app declares that permission (video calls), and Android refuses the
camera app to an app that declares it without holding it: on a phone
that had never made a video call, *Take a photo* could not open the
camera.

## Calls

A call is set up with end-to-end messages, like everything else: an offer,
the other app saying it rings, the answer, the end. Each side makes a key
pair for that call alone and the two agree a secret from them; the offer and
the reply are signed and sealed, so not even the relay can put its own key
in between, and from that secret comes one key for each direction.

The sound travels as small UDP packets, every one sealed with AES-256-GCM
and decodable on its own. Between two 16.0 apps (codec `opus48`) it is
**Opus at 48 kHz in stereo**, at the lower of the two sides' Call quality
(Standard 96, High 128, Ultra 192 kbit/s). The microphone is read in mono,
for the echo cancelling, and carried in both channels. Each side says, in
its answers to the other's pings, how many of the other's packets were lost
and how unevenly they came; with *Adapt to the connection* on (the default),
the sender then chooses every two seconds: packets of 5, 10, 20 or 40 ms —
5 ms straight to the other device on a steady connection, 10 at the
shortest through a relay that takes 150 packets a second from each side
(13.0), 20 over the WebSocket — and a lower bitrate while packets are being
lost, stepping back up once they are not. With an older app it is as
before: 20 ms of 16 kHz speech a packet, **Opus** at 24 kbit/s with in-band
FEC (a lost packet is rebuilt from the next one, or concealed), or else
(and with a 9.1.2 phone) IMA ADPCM, about 80 kbit/s each way. A short buffer,
counted in milliseconds of sound and growing only when the network is
uneven, smooths out arrival. Android's Opus is libopus, built by the local
recipe in `recipes/libopus`; a computer uses its system's.

Each device learns from the relay the public address it is seen from, offers
it to the other with its local ones, and both send to each other at once —
which opens a direct path through most home routers, the shortest there is.
Where that fails, packets go through the relay's call port, and where UDP is
blocked altogether, over the relay's WebSocket. Every packet that arrives
shows which way works; the most direct one heard from in the last few
seconds is used, and that changes as the network does. The call screen
shows which way the sound goes and the round-trip time.

A direct path shows each device the other's network address, which says
roughly where it is. A device that calls offers its addresses with the
call. A device that is called offers none, and sends nothing straight,
until the call is answered on it (18.1): what passes while it rings goes
through the relay, so a call that is not answered tells the caller nothing
of where that device is. With *Settings → Privacy → Hide my location in
calls* on, a device offers no address at all and its calls stay on the
relay, with a little more delay. A group's calls always go through the
relay.

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

**One identity on several devices (18.1).** Every device connected as you
rings. The one you answer on has the call; the others stop and say
*Answered on another device* (or *Declined call*). Messages are not shared
between devices: each gets what arrives while it is connected. Set a
second device up from a backup or with the key for another device
(18.2, *My profile*), not from the private key alone (see
*Post-quantum encryption*).

Calls need **relay v10**, which advertises them in `/api/health` and opens a
UDP port for them (by default the same number as its HTTP port). On an older
relay the call button says so. A phone needs **Android 6** or later: on
Android 5 the call buttons say so (17.3).

### Video (17.0)

Pictures are coded as **H.264**: by libx264 through PyAV on a computer
(the Windows EXE has it; `desktop/requirements-exe.txt`), by Android's own
hardware coder on a phone. HEVC or AV1 is used instead when the sender has a
hardware coder for it and every receiver decodes it (each says so: in a
group's sealed state, `vd`; in a 1-to-1 call, every second in its media
packet). Each picture is cut into pieces of at most 1,000 bytes, sealed as
the sound is; a picture that loses a piece is dropped, and a key frame asked
for (no piece is sent again: a key frame costs less than the wait).

A 1-to-1 call's picture goes the way its sound does (straight, else the
relay, else the WebSocket), at the rung of the ladder (360p24 at 0.5 Mbit/s
to 1080p60 at 8; 19.0) that Settings allows, the way can take, and the other side
shows it at; it steps down while pictures are lost and back up after six
clean seconds, and the sound steps down with it (each rung's pair). A
group's call sends each camera in three sizes (a quarter, half and the
whole of the top rung), and **relay 13.2** passes each member only the size
of the tile it shows, switching at a key frame it asks the sender for; a
sender codes only the sizes someone shows. On a computer the camera is read
by FFmpeg (`v4l2`, `dshow`, `avfoundation`) and the screen too (`x11grab`,
`gdigrab`, `avfoundation`); on a phone Camera2 draws, upright, into each size's coder.


### Remote control (19.0)

In a one-to-one call, a computer that shares its screen and can be
controlled (Windows through `SendInput`; Linux on X11 through XTEST, with
libXtst; not Wayland, not macOS yet) says so with its media state. The
other side's *Control* asks; the computer's person answers *Allow* or
*Not now*. While allowed, the pointer, the buttons, the wheel and the
keys go as small sealed packets: each event is numbered, and each packet
carries the newest pointer position and the last twelve other events
with the buttons and keys held, so a lost packet is made good by the
next and nothing stays held when control ends. Typed text is sent as
text, so the two keyboards' layouts need not match. *Stop* on the
computer, *Release* on the other side, the screen no longer shared or
the call's end each end control.

A **remote session** is a call marked as one: no sound on either side
(no microphone or speaker is needed), and only through the relay, as
with *Hide my location in calls*. Only a computer whose *Settings ›
Remote access* lists the caller answers it, by itself; any other device
of yours sets the offer aside without a word. A computer with several
identities answers for the one that was called, by that identity's own
list, and is switched to it. Every entry has an end date, and only
verified contacts can be added. Windows lets no program control its
sign-in or lock screen, or a program run as administrator. Whoever
holds a listed person's keys can reach the computer: list only people
you trust.

**For the long term (19.0.1).** *Before you leave* in Remote access
lists what lets the computer come back on its own, and while someone is
listed a watcher keeps Ferritext running. In a session, *Keys* sends
what a phone cannot type and what the controlling computer would keep
for itself, says what Ctrl+Alt+Del cannot do from afar, lists the
computer's programs and restarts it. A computer says what its screen
shows, so the other side knows when Windows has put up a screen nobody
can use from afar. A still screen costs no data.

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

## Away replies

*Settings → Away → Reply while away* (18.1) answers for you while you are
not there. Someone who writes to you gets your words back (*What the
reply says*, up to 200 characters), under the note *Automatic reply*, at
most once every four hours a chat. While it is on, the chat list says
*Away replies are on* at its top: a tap on that opens its switch.

- **Never** in a group, to someone you have not accepted (a reply would
  accept them), or to a message that is itself an automatic reply: two
  people who are both away do not answer each other for ever.
- **No AI, and nothing reads your messages.** The reply is your own
  sentence, written and sealed on your device like any message; the relay
  sees a message go out, as with any other.
- So a reply goes out only while Ferritext receives messages on that
  device (the app open, or its background service running). A message
  that waited at the relay is answered when it arrives.

## Background delivery

Android closes apps in the background. While Ferritext's own process is alive
it listens for messages itself; once Android has closed it, a small
foreground service (a separate process, started from `main.py` too) takes
over, and steps aside again when the app returns. The two coordinate through
heartbeat files so only one ever holds the connection. It can be switched off
under *Settings → Notifications*, at the cost of notifications only arriving
while the app is open.

Android requires a running service to show a notification. It is the
app's own (18.1): silent, with no badge, in the pulled-down shade only,
and not on a locked screen.

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

Some makers' phones stop background apps anyway, the service too, to save
battery (Xiaomi, Huawei, Honor, OPPO, vivo and their brands, Meizu, ASUS,
Samsung; dontkillmyapp.com keeps the list). Two settings keep it alive: the
maker's list of apps that may start by themselves, and Android's question
whether its battery use may go unrestricted
(`REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`). No app may make them itself, so
on those phones the guide (`KeepRunningSheet`) opens by itself once, as
soon as there is a username, and its *Set up* opens each page in turn: the
next as the app comes back from the last, or regains focus after Android's
dialog. Ticks show what is made, and *Settings → Notifications → Keep
Ferritext running* opens it again. The maker's pages are in `KEEP_RUNNING`
in `main.py`; one that a phone lacks gives way to the next, and the last
to the app's own page in Android's settings. Whether an app may start by
itself cannot be read back, so that one counts as made once its page has
opened.

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
- **Photos, voice notes and video messages** are sealed with their own
  AES-256 key, which travels inside the message, so they are covered
  with it.
- **A new install from the private key alone** (WIF) has a new ML-KEM key.
  A message sealed to the old one is asked for again, with the new key;
  the sender's app sends that message again once, if it is under a week
  old. A backup carries the key, so an identity restored from one keeps it,
  and so does the key for another device (18.2: *My profile › Copy the
  key for another device*, which holds both keys).
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
  download, and nothing rings: a call from them is shown there as a
  missed call (18.1), as yours to them is until they accept. Until you
  answer, the relay is told to send them no
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
- **Where you are.** A network address says roughly where a device is,
  and whatever a device connects to sees its address. The app connects to
  the relay; to a link's own site, only when you write a link with *Link
  previews* on (a preview you receive came inside the message, and nothing
  is fetched to show it; a link you open goes to your browser); and, in a
  call with one person, to their device where a direct path can be opened:
  not before you answer, and not at all with *Hide my location in calls*
  on (see [Calls](#calls)). It connects to nothing else.

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
| `ARCHITECTURE_client.md` | How the app is built: its layers, threads, a message end to end, groups, calls, storage, and where to change what (17.4). |
| `buildozer.spec` | Android packaging: requirements, permissions, service, icons. |
| `data/` | The Ferrite Text Messenger mark (`ferritext_logo_packet.svg`; `ferritext_logo.svg` is the supplied disc it came from) and the images rendered from it, including the desktop window icons (`icon_desktop.png`, `.ico`). |
| `data/fonts/` | The themes' faces (Ferritext Serif, Mono and Sans), the CJK and World faces, the capitals faces (`ui.ttf`, Silkscreen) and their licences. |
| `data/lang/` | Each language's phrases (`zh-CN.json`); English is the code's own. |
| `tools/make_logo.py` | Renders every image in `data/`, and the notification icon, from the SVG — no dependency beyond Pillow. |
| `tools/build_fonts.py` | Rebuilds the merged faces from their upstream fonts, Noto Emoji and Twemoji (needs fontTools, and uharfbuzz for the World face). |
| `tools/bundle_desktop.py` | Builds the single-file desktop release, and `--verify` checks one against the source. |
| `tools/phrases.py` | Lists the phrases the app shows and what each language's pack lacks, and merges translations in. |
| `tools/download_page.py` | Makes a release's download page, to host off GitHub (for China). |
| `tools/p4a_hook.py` | Build hook: the service types Android 14 requires, the notifications' receiver, and the camera's FileProvider. |
| `recipes/libopus/` | A python-for-android recipe for libopus, for calls in Opus. |
| `tools/apk-next/recipes/` | The next APK platform's own recipes (16.0, on trial): OpenSSL, cryptography, libjpeg-turbo, libpng, libwebp and Pillow, each pinned by SHA-256. |
| `tools/seed_download.sh` | Seeds a recipe's download from whichever of its official hosts answers with the pinned file (CI). |
| `tools/vuln_audit.py` | PyPI's advisories for every pinned package (CI); `desktop/requirements*.txt` hold the pins. |
| `android/` | What Android compiles or ships beside the Python, in one folder. Java: `CallAudio` (a call's microphone and speaker, on threads of their own, and where the sound goes), `Notifier` and `NotifyReceiver` (notifications, and what is done to them), `QrReader` (QR codes in photos). Resources: the cleartext policy for the relay, the status-bar icon, the camera's folder, the launch screen at night. |
| `CREDITS.md` | The open-source work Ferritext is made with, and its licences. |
| `tools/publish_release.sh` | Puts a build's files on its version's GitHub Release, once (run by CI). |
| `desktop/` | Desktop packaging (Nuitka: `build_desktop.sh`, which builds the Windows executable), the headless test suites, and the tools that capture and time the app. |
| `.github/workflows/build-apk.yml` | Tests → the signed APK and the Windows executable, side by side → a GitHub Release with the three deliverables. |
| `.github/workflows/apk-next.yml` | The APK on the next platform, a debug build for phones to try (16.0). |

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
xvfb-run -a python3 desktop/test_touch.py main.py
xvfb-run -a python3 desktop/test_privacy.py main.py
python3 desktop/test_backup.py main.py      # runs each device under xvfb-run itself
xvfb-run -a python3.13 desktop/test_video.py main.py   # needs PyAV (requirements-exe.txt)
xvfb-run -a python3 desktop/test_videomsg.py main.py   # more is checked where PyAV is
python3 desktop/smoke_exe.py desktop/dist/Ferritext-17.0.0-windows.exe  # a built executable
```

| Suite | Covers |
| --- | --- |
| `test_core.py` | Envelope extension (incl. replay/tamper refusal), wire compatibility with the previous release, migrations both ways, delete-for-both authorisation, tombstones, extension sanitising, group membership rules (who may invite, change and remove), per-member receipts, reactions, ML-KEM-768 against NIST's vectors and the `cryptography` library, post-quantum envelopes (tampering, keys this device does not hold), a video message (18.1) as the release before shows it |
| `test_flows.py` | Three real app controllers over an in-memory relay: replies, forwards, stars, delete for both, photos (EXIF stripping, rotation), expiry → ask again → re-upload, voice notes, notification rules, notes to self, groups (create, write, rename, remove, leave), reactions and taking them back, the reaction strip and emoji panel, switching theme, text size and language, post-quantum keys passing between people and groups, a new install asking for a message again, away replies (18.1): who gets one and how often, the mark it carries, its words, the chat list's reminder, the background service answering as the app does, video messages (18.1): sealed and uploaded, downloaded and read before they are kept, the reply they go with, a file that is no video refused |
| `test_design.py` | WCAG AA contrast of every text/ground pair in every theme, each theme set's faces and corners, Chinese, Japanese and Korean in their face, fonts registered and packaged with their licences, following the system theme, tracked capitals, stacking buttons, Back closing a sheet, every language's pack complete, the language chosen, Chinese line breaking, every other script (its face, its shaping, right to left as Unicode's tests have it, unspaced scripts' line breaks) |
| `test_composer.py` | Real mouse clicks reaching the message field and its keys, hidden parts never swallowing a touch, the composer's two states, recording through a capture command into WAV (held, locked, paused, and paused by a call that begins, never sent by itself), photos from a camera, icons drawn smooth |
| `test_bundle.py` | The single-file desktop release: built, verified, run from an empty folder (fonts and icons unpacked, checked, reused, repaired); the download page |
| `test_service.py` | Both services started from `main.py` as Android starts them, app ⇄ service hand-over, the manifest hook |
| `smoke_desktop.py` | Launch, branding and loading screen through the real `App.run()` path |
| `smoke_chat.py` | Bottom-anchored short chats, scrolling, text selection and copy |
| `test_navigation.py` | Pages in layers, a page changed again before the last change has settled (18.1: back, then the same chat at once, left an empty page), a long chat opening at once, the menu, back one step at a time, search and jumping into history, tap to react, holding chats (mute, pin, archive, unread, block, delete) |
| `test_pictures.py` | Making a picture (crop, size limit, nothing of the original file kept), refusing bad ones, versions, who is sent one and when, asking for a lost one, group pictures from the admin only, blocking, discs following a new picture, the framing sheet |
| `test_calls.py` | The codecs (ADPCM bit for bit audioop's; Opus with FEC and concealment; 16.0's 48 kHz stereo at each quality, in 5 to 40 ms packets), the packet seal (replay, tamper, direction), the jitter buffer, two call engines finding each other directly, through a relay and over the WebSocket, packets and bitrate adapting to each, and two apps calling: ring, answer, talk, mute, hang up, decline, busy, cancel, no answer, a stale offer, blocking, the line each call leaves, where a device is (18.1): no address offered and nothing sent straight while it rings, or at all with *Hide my location in calls* on, a call answered while it is still being prepared (the microphone as the lobby left it), and a call that begins stopping a message that was playing |
| `test_notify.py` | Notifications from the sync thread (a paused app still notifies), what they are made of, mute and its end, blocking that holds and releases, replying and marking read from a notification, drafts, a call while the app is away, the guide that keeps the app running |
| `test_gestures.py` | Real taps and swipes through the window: back, the menu, reply, taps that are not swipes, voice notes at speed and seeking |
| `test_touch.py` | A phone's finger (SDL's touch, no mouse button) through Kivy's input: Send, holding the microphone, a switch, one touch per finger on Android, a call's refusal said in the chat, the microphone permission |
| `test_privacy.py` | Disappearing messages, verifying a contact, links and where they lead, link previews (off by default; fetched only from public addresses), the *Hide my location in calls* switch, encrypted backup and restore |
| `test_backup.py` | Backup and restore end to end, each device a process of its own: a full phone restored through the screens (every table, its files, writing and reading again), beside another identity, Unicode passphrases, without media, a damaged database changing nothing, backups from 11.0, 13.0 and 15.1.1, a full disk, a large backup timed, Android's document copies off the UI thread, automatic backups (made with no one there, the last three kept, the newest restored elsewhere with the passphrase) |
| `test_video.py` | 17.0's pictures (PyAV): the ladder and a room's three sizes, pieces and how they are put back (losses, key frames), what a 1-to-1 picture may take through each relay, the pictures' thread when someone leaves, a camera coded at three sizes at once, decoding to a tile's size, a group's and a 1-to-1 call's engines trading sealed pictures (sizes asked for, key frames, the sound stepping down with the picture), recording a call and its seal |
| `test_videomsg.py` | 18.1's video messages. Without any video library, as a phone's Python runs them: NAL units, a picture's size from its parameter set (sets written bit by bit here, and x264's own), ADTS sound, the MP4 written and read back, 4,000 files with their tables changed at random (read with every picture inside the file, or refused), the recorder against stand-ins for a phone's camera coder and recorder (where a recording begins, what ends it, a camera that goes quiet, a relay that takes smaller files, the sound cut to the pictures, what is let go), the bubble and what it says while not here, the camera key, the recording sheet (stopped where it is when the app leaves the screen or a call begins; closed only by Delete and Send once it records), a phone's permissions, and taking a photo once Android gives the camera. With PyAV: a real recording opened by FFmpeg without a warning, a flash and a tone within a tenth of a second of each other, and one recorded, sent and played in the app |
| `smoke_exe.py` | A built executable started twice in an empty home, as a person would: up, its window drawn, no crash in its log; its size and both start times (CI runs it on Windows) |

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

- **`capture.py`** saves the screens in its `SCREENS` list
  (`shots/after/black_chat.png`, …; `--only chat,menu` saves a few), built
  from the same chats on every run. `--theme` picks `black` (the default),
  `dark` or `light`, `--set` the theme set: `axiom` (the default),
  `overclock` or `ferrite`, and `--lang` the language: `en` (the default)
  or `zh-CN`. It lists any layout problem it finds (text cut
  off or overflowing, parts outside their box, touch targets under 40 dp)
  and then exits 1. Compare the two folders screen by screen. Run one
  capture at a time: two at once slow each other, and the check can then
  disagree with itself about a label's height.
- **`bench.py`** times drawing text (Chinese too, and in each theme set's
  face), the store's reads on 50,000 messages, and sealing and opening a
  message, each the median of five runs. Compare the two files. A
  difference under a tenth is noise, and timings differ between machines,
  so compare only runs from the same one.
- **`playthrough.py`** (18.2) walks through the app as a person does, by
  the touches a finger makes: opens a chat, types, sends, taps a message,
  reacts, replies, flicks through history, opens the menu and Settings,
  changes the theme. `xvfb-run -a python3 desktop/playthrough.py main.py
  walk/` saves a picture after each step and a table of how long each
  took to answer, the longest frame while the page moved, and how much of
  it was Python's garbage collector. It is how the app's "feel" is
  compared between versions, and a test of whole flows (CI runs it): a
  step that is never answered fails it. `--profile STEP` prints where
  that step's time went.
- In the change's description, say which screens changed and which
  numbers moved. The pictures and numbers that go to review come from
  these tools, so anyone can make them again.

## Building it yourself

Requirements: Linux (or WSL) with Python 3, a JDK, and `git zip unzip gcc make
autoconf automake libtool libltdl-dev`. Buildozer downloads the Android SDK/NDK
on first run.

```bash
python3 -m pip install --user buildozer cython
buildozer android debug          # produces bin/fext-17.0.0-*-debug.apk
```

The next platform is on trial in the same spec, as its `next` profile
(python-for-android 2026.5.9 with our own builds of OpenSSL, cryptography,
libjpeg-turbo, libpng, libwebp and Pillow, `tools/apk-next/recipes`): `cp -r recipes/libopus
tools/apk-next/recipes/` then `buildozer --profile next android debug` with
Buildozer 1.6.0. `.github/workflows/apk-next.yml` builds it.

**The Windows executable** is built on Windows, as CI builds it: from Git
Bash, with Python 3.13 and Visual Studio's C++ build tools. Its packages are
pinned in `desktop/requirements-exe.txt` (the client's own in
`desktop/requirements.txt`, which the tests run with); raise a pin on purpose,
once the suites pass with the new version.

```bash
pip install -r desktop/requirements-exe.txt
ONEFILE=1 PY=python NAME=Ferritext-17.0.0-windows.exe bash desktop/build_desktop.sh
python desktop/smoke_exe.py desktop/dist/Ferritext-17.0.0-windows.exe
```

On Linux and macOS the same script builds that system's executable.

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
