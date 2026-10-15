#!/usr/bin/env python3
"""Notifications, muting, blocking and drafts (v11), against two real app
controllers and their real sync workers.

    xvfb-run -a python3 desktop/test_notify.py main.py

  * a new message is announced from the sync thread itself — so an app
    that is paused (in the background, its screen stopped) still notifies;
    never for the chat in front of you, never for a muted one; on a
    computer, the system's notification while the window is elsewhere;
  * mute for 12 hours, 3 days or until turned back on; a muted chat still
    receives and shows everything; a mute's end turns notifications back
    on by itself; a release that knows nothing of ends still sees "muted";
  * block holds what they send — sealed, unshown, unannounced, once per
    frame — refuses their calls, and on unblocking it all arrives, in order;
  * what is done to a notification (reply, mark as read, decline a call)
    reaches the app as a small file the sync worker acts on — only the
    identity it was for;
  * drafts survive leaving the chat and restarting, and show in the list;
  * a call while the app is away rings through a notification, and a call
    given up on is a missed call; a call on while away keeps its limits.
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import time
import uuid

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-notify-")
os.environ["FEXT_SERVER_HOST"] = "127.0.0.1:1"

_spec = importlib.util.spec_from_file_location(
    "fext_notify", sys.argv[1] if len(sys.argv) > 1 else "main.py")
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_notify"] = M
_spec.loader.exec_module(M)
from kivy.clock import Clock                                   # noqa: E402
from kivy.core.window import Window                            # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""))


def tick(seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        Clock.tick()
        time.sleep(0.01)


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        Clock.tick()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class Relay:
    def __init__(self):
        self.mail, self._next = [], 1

    def post(self, sender, recipient, envelope, cid):
        sid = self._next
        self._next += 1
        self.mail.append({"id": sid, "sender": sender, "recipient": recipient,
                          "envelope": envelope, "client_msg_id": cid})
        return sid


class StubApi:
    def __init__(self, relay, keys):
        self.relay, self.keys = relay, keys
        self._features = {"blobs": True}
        self.read_ids = []

    def features(self):
        return self._features

    def send_message(self, recipient, envelope, client_msg_id):
        return {"server_id": self.relay.post(self.keys.address, recipient,
                                             envelope, client_msg_id)}

    def read(self, ids):
        self.read_ids.extend(ids)
        return {"updated": len(ids)}

    def set_ticks(self, peer, enabled):
        return {}


def make_party(label, relay):
    app = M.FextApp()
    app._dismiss_splash = lambda *_a: None
    app._probe_transport = lambda: None
    app.build()
    priv, _p, _a, _n = M.mine_identity()
    address = app.keys.add_identity(priv, label)
    app.keys.activate(address)
    app.keys.set_username(label)
    app.crypto = M.CryptoEngine(app.keys)
    app.api = StubApi(relay, app.keys)
    app.store = M.LocalStore(app.keys.store_path())
    app.worker = M.SyncWorker(app)
    app.worker.beat_owner = None
    return app


def deliver(relay, parties, keep=False):
    """Hand every waiting message to its recipient's worker; returns the
    acknowledgements (and the frames, with keep=True)."""
    acked, frames = [], []
    while relay.mail:
        msg = relay.mail.pop(0)
        frames.append(dict(msg))
        acked.append(parties[msg["recipient"]].worker._ingest_message(msg))
    return (acked, frames) if keep else acked


def drain(app):
    out = []
    while not app.ui_queue.empty():
        out.append(app.ui_queue.get_nowait())
    return out


relay = Relay()
alice, bob = make_party("alice", relay), make_party("bob", relay)
parties = {alice.keys.address: alice, bob.keys.address: bob}
A, B = alice.keys.address, bob.keys.address
for me, other in ((alice, bob), (bob, alice)):
    me.store.upsert_contact(other.keys.address, other.keys.username,
                            other.keys.public_key_hex)

seen = []
real_desktop_notify = M.desktop_notify
M.android_message_notification = lambda app, peer, title, fallback: \
    seen.append(("android", app.keys.address, peer, title, fallback))
M.desktop_notify = lambda title, text: seen.append(("desktop", title,
                                                    text)) or True
M.desktop_flash_window = lambda: None
focused = [True]
bob._window_focused = lambda: focused[0]


def say(text, frm=alice, to=B):
    frm.send_text(to, text)
    wait_for(lambda: relay.mail)
    return deliver(relay, parties)


print("\nNotifications, from the sync thread")
bob.foreground, bob.current_peer = True, None
bob.manager.current = "chats"
focused[0] = False
say("hello")
check("on a computer, the window elsewhere: the system's own notification",
      seen and seen[-1] == ("desktop", "alice", "hello"), str(seen[-1:]))
focused[0] = True
drain(bob)
n = len(seen)
say("again")
check("...the window in front, on another page: a line in the app instead",
      len(seen) == n and any(e[0] == "status" and "alice" in e[1]
                             for e in drain(bob)))
bob.open_conversation(A)
n = len(seen)
say("you are looking")
check("...and nothing for the chat in front of you", len(seen) == n)

print("\nA paused phone still notifies")
M.IS_ANDROID = M.IS_MOBILE = True
bob.foreground = False            # Android paused the app: its screen stops
n = len(seen)
drain(bob)
say("while you were away")
check("a message to a paused app is announced at once, from the sync "
      "thread — not left for the screen to find later",
      len(seen) == n + 1 and seen[-1][0] == "android" and
      seen[-1][2] == A and seen[-1][3] == "alice")
check("...even though its chat was the one last open",
      bob.current_peer == A and bob.manager.current == "conversation")
bob.foreground = True
n = len(seen)
say("back again")
check("...but not once you are back in that chat", len(seen) == n)
bob.go_back_to_chats()

# What the notifications themselves are made of — the parts the checks
# above stand in for (the phone's Java and the computer's own notifier).
say("first of two")
say("second of two")
lines = M.notification_lines(bob.store, A)
check("a phone's notification lists the unread messages, oldest first",
      [text for _who, text, _ms in lines][-2:] == ["first of two",
                                                    "second of two"] and
      all(isinstance(ms, int) and ms > 0 for _w, _t, ms in lines),
      str(lines))
long_line = M.notification_lines.__globals__["single_line_text"](
    "a\nb " + "x" * 400, 300)
check("...each on one line, cut short", "\n" not in long_line and
      len(long_line) == 300 and long_line.endswith("…"))
M.IS_ANDROID = M.IS_MOBILE = False
launched = []
real_popen, real_which = M.subprocess.Popen, M.shutil.which
real_system = M.platform.system
M.subprocess.Popen = lambda command, **_kw: launched.append(command)
M.shutil.which = lambda name: "/usr/bin/" + name
M.platform.system = lambda: "Linux"
shown = real_desktop_notify("alice\nwith a newline", "x" * 500)
check("on a computer, the system's notifier is asked, title and text on "
      "one line each",
      shown and launched and launched[-1][0] == "notify-send" and
      launched[-1][-2] == "alice with a newline" and
      len(launched[-1][-1]) == 240)
M.platform.system = lambda: "Darwin"
real_desktop_notify('say "hi"', "and \\ more")
check("...on a Mac, Notification Center, its quotes escaped",
      launched[-1][0] == "osascript" and
      '\\"hi\\"' in launched[-1][-1])
M.platform.system = lambda: "Linux"
M.shutil.which = lambda name: None
check("...and with no notifier at all, it says so",
      real_desktop_notify("t", "x") is False)
M.subprocess.Popen, M.shutil.which = real_popen, real_which
M.platform.system = real_system
M.IS_ANDROID = M.IS_MOBILE = True

print("\nMute: 12 hours, 3 days, until turned back on")
bob.choose_mute([A])
tick(0.3)
sheet = next((w for w in Window.children if isinstance(w, M.MuteSheet)),
             None)
labels = [r.title.text for r in sheet.body.walk()
          if isinstance(r, M.ListRow)] if sheet else []
check("muting offers 12 hours, 3 days and until turned back on",
      labels[:3] == ["For 12 hours", "For 3 days", "Until I turn it back on"],
      str(labels))
check("...and says messages keep coming",
      sheet is not None and "still arrive" in " ".join(
          w.text for w in sheet.walk() if isinstance(w, M.InfoLabel)))
pick = next(r for r in sheet.body.walk() if isinstance(r, M.ListRow)
            and r.title.text == "For 12 hours")
pick.dispatch("on_release")
tick(0.4)
muted, until = bob.store.mute_state(A)
check("For 12 hours mutes until twelve hours from now",
      muted and abs(until - (time.time() + 12 * 3600)) < 30, str(until))
n = len(seen)
before = len(bob.store.messages_for(A))
bob.foreground = False
say("quietly")
conv = {c["peer"]: c for c in bob.store.conversations(me=B)}[A]
check("a muted chat stays quiet...", len(seen) == n)
check("...but still receives the message and counts it unread",
      len(bob.store.messages_for(A)) == before + 1 and conv["unread"] >= 1
      and conv["muted"])
with sqlite3.connect(bob.keys.store_path()) as db:
    raw = db.execute("SELECT notify, mute_until FROM contacts WHERE "
                     "address = ?", (A,)).fetchone()
check("...and a release that knows nothing of ends still sees it muted",
      raw[0] == 0 and raw[1] > time.time())
bob.choose_mute([A])
tick(0.3)
sheet = [w for w in Window.children if isinstance(w, M.MuteSheet)][0]
labels = [r.title.text for r in sheet.body.walk()
          if isinstance(r, M.ListRow)]
check("a muted chat's choices end with Unmute", labels[-1] == "Unmute")
sheet.dismiss(animation=False)
tick(0.2)
bob.store.set_mute(A, time.time() - 5)        # the end has come
check("a mute whose end has passed reads as off, and is cleared",
      bob.store.mute_state(A) == (False, 0.0) and
      bob.store.get_contact(A)["notify"] == 1)
n = len(seen)
say("audible again")
check("...and the chat notifies again by itself", len(seen) == n + 1)
bob.set_mute([A], M.LocalStore.MUTE_FOREVER)
check("until turned back on: muted, with no end",
      bob.store.mute_state(A) == (True, 0.0) and
      M.mute_label(0) == "Muted until you turn it back on")
bob.store.set_mute(A, time.time() + 3 * 86400)
bob.store.end_finished_mutes(time.time() + 3 * 86400 + 1)
check("the sweep ends every mute whose time is up",
      not bob.store.mute_state(A)[0])
bob.set_mute([A], None)

print("\nBlock: held, sealed, until unblocked")
bob.foreground = False
bob.set_blocked([A], True)
n = len(seen)
before = len(bob.store.messages_for(A))
texts = ["the secret plan", "part two", "part three"]
acked, frames = [], []
for text in texts:
    alice.send_text(B, text)
    wait_for(lambda: relay.mail)
    a, f = deliver(relay, parties, keep=True)
    acked += a
    frames += f
check("what a blocked contact sends is acknowledged...",
      len(acked) == 3 and all(acked))
check("...shown nowhere and announced to nobody",
      len(bob.store.messages_for(A)) == before and len(seen) == n)
check("...but held, one row per frame", bob.store.held_count(A) == 3)
with sqlite3.connect(bob.keys.store_path()) as db:
    stored = " ".join(r[0] for r in db.execute("SELECT frame FROM held"))
check("...still sealed: the words are nowhere on this device",
      "envelope" in stored and "secret plan" not in stored)
bob.worker._ingest_message(dict(frames[0]))
check("a re-push of a held frame is not held twice",
      bob.store.held_count(A) == 3)
call_id = uuid.uuid4().hex
alice._send_control_to(B, {"op": "call", "id": call_id,
                           "tag": os.urandom(8).hex(),
                           "k": M.new_call_key()[1], "codecs": ["ima16"],
                           "c": [], "t": int(time.time())}, "Voice call")
drain(bob)
deliver(relay, parties)
check("their call is refused outright: neither held nor rung",
      bob.store.held_count(A) == 3 and not any(
          e[0] == "call_signal" for e in drain(bob)) and
      not (M.DATA_DIR / f"call_offer_{B}.json").exists())
bob.set_blocked([A], False)
check("unblocked, everything held arrives, in order",
      wait_for(lambda: bob.store.held_count(A) == 0, 10) and
      [m["body"] for m in bob.store.messages_for(A)][-3:] == texts)
check("...quietly: a flood is not a burst of notifications",
      len(seen) == n)
# The app's own clock may take the event off the queue before this test
# does, so what is checked is what it says.
check("...and the app says how many arrived",
      wait_for(lambda: (bob._pending_status or ("",))[0] ==
               "3 messages from alice arrived"))

print("\nWhat is done to a notification")
bob.foreground = False
M.COMMANDS_DIR.mkdir(parents=True, exist_ok=True)


def command(op, **fields):
    cmd = dict(op=op, peer=A, me=B, at=int(time.time() * 1000), **fields)
    path = M.COMMANDS_DIR / f"{time.time_ns()}-{op}.json"
    path.write_text(json.dumps(cmd), "utf-8")
    return path


say("reply to me")
check("(unread before the reply)", bob.store.unread_for(A))
path = command("reply", text="on my way")
other = M.COMMANDS_DIR / "other-identity.json"
other.write_text(json.dumps({"op": "read", "peer": A, "me": "Fsomeone-else",
                             "at": int(time.time() * 1000)}), "utf-8")
stale = M.COMMANDS_DIR / "stale-other.json"
stale.write_text(json.dumps({"op": "read", "peer": A, "me": "Fsomeone-else",
                             "at": int((time.time() - 3 * 86400) * 1000)}),
                 "utf-8")
bob.worker._chores(force=True)
check("a reply typed into the notification is sent",
      wait_for(lambda: relay.mail) and relay.mail[-1]["recipient"] == A)
deliver(relay, parties)
check("...arrives", alice.store.messages_for(B)[-1]["body"] == "on my way")
check("...and the chat is read", not bob.store.unread_for(A))
check("its file is gone once acted on", not path.exists())
check("another identity's waits for it; one days old is let go",
      other.exists() and not stale.exists())
other.unlink()
say("one more")
command("read")
bob.worker._chores(force=True)
check("Mark as read marks the chat read", not bob.store.unread_for(A))
offer = M.DATA_DIR / f"call_offer_{B}.json"
declined = uuid.uuid4().hex
offer.write_text(json.dumps({"from": A, "sig": {"id": declined}}), "utf-8")
command("decline", call=declined)
bob.worker._chores(force=True)
check("Decline tells the caller...",
      wait_for(lambda: relay.mail) and relay.mail[-1]["recipient"] == A)
relay.mail.clear()
check("...forgets the offer kept for the app, and says so in the chat",
      not offer.exists() and declined in bob._declined_calls and
      bob.store.messages_for(A)[-1]["body"] == "Declined call")

print("\nDrafts")
bob.foreground = True
bob.open_conversation(A)
screen = bob.conversation_screen
screen.entry.text = "half a thought"
screen.keep_draft()
check("unsent text is kept as the chat's draft",
      bob.store.draft(A) == "half a thought")
bob.go_back_to_chats()
tick(0.2)
row = bob.chats_screen._rows.get(A)
words = [w.text for w in row[1].walk() if hasattr(w, "text")] if row else []
check("...the list shows it, marked as a draft",
      "Draft:" in words and "half a thought" in words, str(words))
again = M.LocalStore(bob.keys.store_path())
check("...it survives a restart (it is in the database)",
      again.draft(A) == "half a thought")
again.close()
bob.open_conversation(A)
check("...and is back in the composer when the chat opens",
      screen.entry.text == "half a thought")
bob.send_current()
tick(1.5)
check("sending it clears the draft", bob.store.draft(A) == "")
relay.mail.clear()
bob.go_back_to_chats()

print("\nA call while the app is away")
M.IS_ANDROID = M.IS_MOBILE = False
rang = []
M.android_call_notification = lambda app, peer, cid, name: rang.append(
    (peer, cid, name)) or True
bob.foreground = False
cid = uuid.uuid4().hex
sig = {"op": "call", "id": cid, "tag": os.urandom(8).hex(),
       "k": M.new_call_key()[1], "codecs": ["ima16"], "c": [],
       "t": int(time.time())}
sig = M.sanitize_call_signal("call", sig)
n = len(seen)
bob.call_signal_arrived(A, sig)
check("an offer to an app that is away is kept for it...",
      json.loads(offer.read_text())["sig"]["id"] == cid)
check("...and rings through a notification, not the stopped screen",
      seen[-1] == ("desktop", "alice", "Incoming voice call") and
      not any(e[0] == "call_signal" for e in drain(bob)))
M.IS_ANDROID = True
offer.unlink()
bob.call_signal_arrived(A, sig)
check("...on a phone, the call's own notification, with Answer/Decline",
      rang and rang[-1] == (A, cid, "alice"))
M.IS_ANDROID = False
bob.call_signal_arrived(A, {"op": "end", "id": cid, "why": "cancelled"})
check("given up on before anyone answered, it is a missed call",
      not offer.exists() and
      bob.store.messages_for(A)[-1]["body"] == "Missed call")
bob._declined_calls.add(cid)
bob.call_signal_arrived(A, sig)
check("a call declined from its notification never rings again",
      not offer.exists())
bob.foreground = True
bob.call_signal_arrived(A, dict(sig, id=uuid.uuid4().hex))
check("with the app in front, call signals go to its call screen",
      any(e[0] == "call_signal" for e in drain(bob)))
# Answer pressed on the notification: the app answers as it rings.
answered = []
bob.answer_call = lambda: answered.append(bob.call.id)
bob._prepare_incoming = lambda call: None
bob._ring = lambda on: None
M.calls_possible_here = lambda: True
fresh = dict(sig, id=uuid.uuid4().hex, t=int(time.time()))
offer.write_text(json.dumps({"from": A, "sig": fresh}), "utf-8")
bob._call_from_notification("answer", fresh["id"])
check("Answer on the notification answers the call as soon as it rings",
      answered == [fresh["id"]] and bob.call is not None and
      bob.call.id == fresh["id"])
bob.call.ended = True
bob._close_call_screen(bob.call)
tick(0.2)
# A call of our own, ringing on with nobody there, while we are away.
relay.mail.clear()
alice.foreground = False
call = M.Call(B, outgoing=True)
call.created -= M.CALL_RING_SECONDS + 1
alice.call = call
(M.DATA_DIR / ".call_active").write_text("1")
alice._start_call_watchdog()
check("away from the app, an unanswered call still gives up on time",
      wait_for(lambda: getattr(call, "end_sent", False), 4) and
      wait_for(lambda: relay.mail, 2))
check("...says so to the other side, and ends the call service",
      relay.mail and relay.mail[-1]["recipient"] == B and
      not (M.DATA_DIR / ".call_active").exists())
relay.mail.clear()

passed = sum(1 for _n, ok in RESULTS if ok)
print(f"\n  {passed}/{len(RESULTS)} passed")
sys.exit(0 if passed == len(RESULTS) else 1)
