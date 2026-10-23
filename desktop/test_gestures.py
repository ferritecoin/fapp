#!/usr/bin/env python3
"""Swipes, search in a chat, voice notes at speed, shortcuts (v11), against
a real app controller, its real input pipeline and its screens.

    xvfb-run -a python3 desktop/test_gestures.py main.py

  * a swipe to the right goes back, the page beneath showing as the page
    follows the finger; let go early and it settles back; on the chat list
    the menu comes in with the finger;
  * a swipe to the left on a message replies to it; a swipe is never also
    a tap, and a vertical drag is a scroll, not a swipe;
  * real taps through the input pipeline: a message, a message with a
    reaction, elsewhere with reactions up (9.1.2 crashed on the last two);
  * holding a reaction (on a computer, a right-click) lists who gave it
    and lets you take yours back; a tap on it still gives or takes back
    yours, and a hold elsewhere still selects the message (v13);
  * a mouse drag on a computer is not a swipe (it selects text);
  * search in a chat counts its matches and steps through them, back in
    time and forward, to messages far beyond the ones drawn;
  * voice notes play in the app on a computer, at 1×, 1.5× or 2× (the
    choice remembered), from wherever the waveform is touched;
  * Ctrl+F, Ctrl+N and Ctrl+Shift+M; a pasted or dropped image goes to the
    photo preview; the caller hears a ringback while it rings; on a phone
    the call service starts as soon as one dials.
"""
import array
import importlib.util
import math
import os
import sys
import tempfile
import threading
import time
import uuid
import wave
from datetime import datetime, timedelta, timezone

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-gest-")
os.environ["FEXT_SERVER_HOST"] = "127.0.0.1:1"

_spec = importlib.util.spec_from_file_location(
    "fext_gest", sys.argv[1] if len(sys.argv) > 1 else "main.py")
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_gest"] = M
_spec.loader.exec_module(M)
from kivy.base import EventLoop                                # noqa: E402
from kivy.clock import Clock                                   # noqa: E402
from kivy.core.window import Window                            # noqa: E402
from kivy.metrics import dp                                    # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""))


def pump(seconds=0.05):
    end = time.time() + seconds
    while True:
        EventLoop.idle()
        if time.time() >= end:
            break
        time.sleep(0.005)


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        EventLoop.idle()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# ---- a finger, through Kivy's own mouse provider --------------------------------
FINGER = {}


def down(x, y):
    h = Window.system_size[1]
    Window.dispatch("on_mouse_down", x, h - y, "left", [])
    FINGER.update(x=x, y=y)
    pump(0.03)


def move_to(x, y, steps=8, each=0.012):
    h = Window.system_size[1]
    x0, y0 = FINGER["x"], FINGER["y"]
    for i in range(1, steps + 1):
        Window.dispatch("on_mouse_move", x0 + (x - x0) * i / steps,
                        h - (y0 + (y - y0) * i / steps), [])
        pump(each)
    FINGER.update(x=x, y=y)


def up():
    h = Window.system_size[1]
    Window.dispatch("on_mouse_up", FINGER["x"], h - FINGER["y"], "left", [])
    pump(0.05)


def swipe(x, y, dx, dy=0, steps=8, each=0.012):
    down(x, y)
    move_to(x + dx, y + dy, steps, each)
    up()


def centre(widget):
    return widget.to_window(widget.center_x, widget.center_y)


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
        self._features = {"blobs": True, "calls": True}

    def features(self):
        return self._features

    def send_message(self, recipient, envelope, client_msg_id):
        return {"server_id": self.relay.post(self.keys.address, recipient,
                                             envelope, client_msg_id)}

    def read(self, ids):
        return {"updated": len(ids)}

    def set_ticks(self, peer, enabled):
        return {}


app = M.FextApp()
app._dismiss_splash = lambda *_a: None
app._probe_transport = lambda: None
root = app.build()
Window.add_widget(root)
Window.size = (dp(400), dp(760))
# What App.run would do: Kivy's own mouse input, and its event loop running.
from kivy.input.providers.mouse import MouseMotionEventProvider  # noqa: E402
EventLoop.add_input_provider(MouseMotionEventProvider("mouse", ""))
EventLoop.start()
priv, _p, _a, _n = M.mine_identity()
me = app.keys.add_identity(priv, "ann")
app.keys.activate(me)
app.keys.set_username("ann")
app.crypto = M.CryptoEngine(app.keys)
relay = Relay()
app.api = StubApi(relay, app.keys)
app.store = M.LocalStore(app.keys.store_path())
app.worker = M.SyncWorker(app)
app.worker.beat_owner = None
_other_priv, other_pub, other, _n = M.mine_identity()
app.store.upsert_contact(other, "ben", other_pub.hex())
start = datetime.now(timezone.utc) - timedelta(days=2)
for i in range(160):
    ts = (start + timedelta(minutes=15 * i)).isoformat()
    body = "where is the lighthouse" if i in (5, 70, 150) else f"line {i}"
    if i % 2:
        app.store.add_in(other, body, ts, 2000 + i, uuid.uuid4().hex,
                         unread=False)
    else:
        app.store.mark_sent(app.store.add_out(other, body, ts,
                                              uuid.uuid4().hex), 6000 + i)
app.refresh_sidebar()
pump(0.3)
M.SWIPE_MOUSE = True

print("\nSwipe back")
app.open_conversation(other)
pump(0.5)
screen = app.conversation_screen
sx, sy = centre(screen.scroll)
down(sx - dp(80), sy)
move_to(sx + dp(30), sy, steps=6)
check("a swipe to the right takes the page with the finger...",
      screen.x > dp(60) and app.manager.current == "conversation",
      f"x={screen.x:.0f}")
check("...the chats showing beneath it",
      app.chats_screen.parent is app.manager and
      list(app.manager.children)[-1] is app.chats_screen)
move_to(sx + dp(200), sy, steps=6)
up()
pump(0.4)
check("let go past a third of the way: back to the chats",
      app.manager.current == "chats" and screen.x == 0 and
      screen.opacity == 1)
app.open_conversation(other)
pump(0.5)
down(sx - dp(80), sy)
move_to(sx - dp(80) + dp(60), sy, steps=6, each=0.03)
up()
pump(0.4)
check("let go early and it settles back, the chats taken away again",
      app.manager.current == "conversation" and screen.x == 0 and
      app.chats_screen.parent is None)
swipe(sx, sy, 0, -dp(160))
pump(0.3)
check("a drag up and down is a scroll, never a swipe",
      app.manager.current == "conversation" and screen.x == 0)
M.SWIPE_MOUSE = False
swipe(sx - dp(80), sy, dp(260))
pump(0.3)
check("on a computer a mouse drag is no swipe (it selects text instead)",
      app.manager.current == "conversation")
M.SWIPE_MOUSE = True
ex, ey = centre(screen.entry)
swipe(ex - dp(60), ey, dp(260))
pump(0.3)
check("...and none starts in the composer",
      app.manager.current == "conversation")

print("\nSwipe to reply")
screen.scroll_to_bottom(animate=False)
pump(0.3)
view = screen.scroll
low = view.to_window(view.x, view.y)[1]
high = view.to_window(view.x, view.top)[1]
rows = [r for r in screen._rows.values() if r.parent is not None and
        low + dp(20) < centre(r.bubble)[1] < high - dp(20)]
row = min(rows, key=lambda r: centre(r.bubble)[1])     # the newest shown
msg = screen._bubbles[row.msg_id].msg
rx, ry = centre(row.bubble)
down(rx + dp(20), ry)
move_to(rx + dp(20) - dp(40), ry, steps=5)
check("a swipe to the left slides the message, its reply mark showing",
      row.shift < -dp(20) and len(row._cue.children) > 0)
move_to(rx + dp(20) - dp(110), ry, steps=5)
up()
pump(0.4)
check("let go past the mark: a reply to that very message",
      screen.reply_to is not None and
      screen.reply_to.get("cid") == msg["client_msg_id"])
check("...the message slides back, and no reactions rose (no tap)",
      row.shift == 0 and screen._strip_for is None)
screen.cancel_reply()
down(rx + dp(20), ry)
move_to(rx + dp(20) - dp(30), ry, steps=5)
up()
pump(0.4)
check("a short slide replies to nothing", screen.reply_to is None and
      row.shift == 0)

print("\nReal taps (9.1.2 crashed on these)")
screen.close_strip()
rx, ry = centre(row.bubble)
down(rx, ry)
up()
pump(0.3)
check("a tap on a message raises its reactions", screen._strip_for ==
      row.msg_id)
ex, ey = centre(screen.entry)
try:
    down(ex, ey)
    up()
    pump(0.3)
    crashed = None
except Exception as exc:                    # pragma: no cover - the bug
    crashed = exc
check("...a tap elsewhere puts them away — and does not crash the app",
      crashed is None and screen._strip_for is None, repr(crashed))
app.store.set_reaction(msg["peer"], msg["client_msg_id"], other,
                       "\U0001F44D")
app.refresh_chat(other)
pump(0.4)
row = screen._rows[row.msg_id]
try:
    down(*centre(row.bubble))
    up()
    pump(0.3)
    crashed = None
except Exception as exc:                    # pragma: no cover - the bug
    crashed = exc
check("a tap on a message that has a reaction does not crash it either",
      crashed is None, repr(crashed))
screen.close_strip()

print("\nWho reacted (v13)")
peer, cid = msg["peer"], msg["client_msg_id"]
THUMBS, HEART = "\U0001F44D", "\u2764\ufe0f"
app.store.set_reactions(peer, cid, me, [])      # the tap above gave one
app.react(row.msg_id, HEART)
pump(0.4)


def reactors_sheets():
    return [w for w in Window.children if isinstance(w, M.ReactorsSheet)]


def listed(sheet):
    return [(r.emoji, r.reactor) for r in sheet.rows]


row = screen._rows[row.msg_id]
down(*centre(row.bubble.reactions.chip_for(THUMBS)))
pump(M.BubbleRow.LONG_PRESS + 0.25)
up()
pump(0.4)
sheet = (reactors_sheets() or [None])[0]
check("holding a reaction shows who gave it: no selection, and nothing "
      "reacted on letting go", sheet is not None and not screen.selecting
      and app.store.reactions_of(peer, cid, me) == [HEART])
check("...those of the one held listed, the others there to choose from",
      sheet is not None and sheet.only == THUMBS and
      listed(sheet) == [(THUMBS, other)] and
      [c.emoji for c in reversed(sheet.kinds_row.children)] ==
      ["All", THUMBS, HEART], sheet and listed(sheet))
everyone = next(c for c in sheet.kinds_row.children if c.emoji == "All")
down(*centre(everyone))
up()
pump(0.3)
check("...'All' lists every one, the newest first, yours said to be yours",
      listed(sheet) == [(HEART, me), (THUMBS, other)] and
      sheet.rows[0].children[1].children[-1].text == "You",
      listed(sheet))
down(*centre(sheet.rows[0]))
up()
pump(0.3)
check("...a tap on yours takes it back, and the list follows",
      app.store.reactions_of(peer, cid, me) == [] and
      listed(sheet) == [(THUMBS, other)] and sheet.kinds.parent is None,
      listed(sheet))
sheet.dismiss(animation=False)
pump(0.3)
row = screen._rows[row.msg_id]
down(*centre(row.bubble.reactions.chip_for(THUMBS)))
up()
pump(0.4)
check("a tap on a reaction still gives yours", app.store.reactions_of(
    peer, cid, me) == [THUMBS] and not reactors_sheets())


class RightClick:
    """A right-click as Kivy's mouse input hands it to a widget."""
    button, is_mouse_scrolling = "right", False

    def __init__(self, widget, at):
        self.ud = {}
        self.pos = widget.to_widget(*at)
        self.x, self.y = self.pos


row = screen._rows[row.msg_id]
row.on_touch_down(RightClick(row, centre(
    row.bubble.reactions.chip_for(THUMBS))))
pump(0.4)
check("on a computer a right-click on a reaction shows who gave it too",
      len(reactors_sheets()) == 1 and not screen.selecting)
for each in reactors_sheets():
    each.dismiss(animation=False)
pump(0.3)
row.on_touch_down(RightClick(row, centre(row.bubble.body)))
pump(0.3)
check("...elsewhere on the message, it selects it as before",
      screen.selecting and not reactors_sheets())
for each in reactors_sheets():
    each.dismiss(animation=False)
screen.clear_selection()
pump(0.3)
row = screen._rows[row.msg_id]
down(*centre(row.bubble.body))
pump(M.BubbleRow.LONG_PRESS + 0.25)
up()
pump(0.3)
check("...and so does holding it there", screen.selecting and
      not reactors_sheets())
screen.clear_selection()
app.react(row.msg_id, THUMBS)
pump(0.3)

print("\nThe menu by swiping")
app.go_back_to_chats()
pump(0.4)
lx, ly = centre(app.chats_screen.scroll)
down(lx - dp(100), ly)
move_to(lx - dp(100) + dp(120), ly, steps=6)
drawer = app.drawer
check("on the chat list, the menu comes in with the finger",
      drawer is not None and drawer.is_open and 0.2 < drawer.progress < 1)
move_to(lx + dp(140), ly, steps=4)
up()
pump(0.5)
check("let go past half way: it opens the rest of the way",
      drawer.is_open and drawer.progress == 1)
app.close_drawer()
pump(0.5)
down(lx - dp(100), ly)
move_to(lx - dp(100) + dp(40), ly, steps=6, each=0.03)
up()
pump(0.5)
check("let go early: it goes away again", not drawer.is_open)

print("\nSwipe back from a page")
app.open_settings()
pump(0.5)
px, py = centre(app.settings_screen)
swipe(px - dp(120), py, dp(300))
pump(0.5)
check("a page goes back to where it was opened from",
      app.manager.current == "chats")

print("\nSearch in a chat")
app.open_conversation(other)
pump(0.5)
screen.open_search()
pump(0.2)
check("the chat's head becomes a search field",
      screen.searching and screen.search_bar.parent is screen.bar and
      screen.normal_bar.parent is None)
screen.search_field.text = "LIGHTHOUSE"
check("matches are found in any case, and counted",
      wait_for(lambda: screen.search_count.text == "1 of 3"),
      screen.search_count.text)
hits = [m["id"] for m in app.store.messages_for(other)
        if "lighthouse" in m["body"]]
check("...the newest first", screen._search_hits == hits[::-1])
screen.search_step(1)
screen.search_step(1)
pump(0.3)
check("stepping back reaches one far beyond those drawn, and shows it",
      screen.search_count.text == "3 of 3" and
      hits[0] in screen._rows and app._chat_ceiling is not None)
check("...no further back than the first", screen.search_up.disabled)
screen.search_step(-1)
pump(0.2)
check("and forward again", screen.search_count.text == "2 of 3" and
      not screen.search_down.disabled)
app._on_keyboard(None, 27)
pump(0.2)
check("back closes the search, the chat staying where it is",
      not screen.searching and screen.normal_bar.parent is screen.bar and
      app.manager.current == "conversation")
screen.search_field.text = ""

print("\nVoice notes at speed, from anywhere")
played = []


class FastRoute:
    """A speaker that takes frames as fast as it can (no real time)."""

    def __init__(self, pull):
        self.pull, self.on = pull, True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while self.on:
            frame = self.pull()
            if frame:
                played.append(len(frame))
            time.sleep(0.001)

    def stop(self):
        self.on = False


M._playback_routes = lambda: [FastRoute]
note = os.path.join(os.environ["HOME"], "note.wav")
with wave.open(note, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(array.array("h", (int(6000 * math.sin(i / 8.0))
                                    for i in range(16000 * 4))).tobytes())
voice = app.voice
voice.set_speed(1.0)
voice.toggle(note)
check("a computer plays a voice note in the app", voice.playing and
      voice._desk is not None)
wait_for(lambda: not voice.playing, 15)
check("...to its end", not voice.playing and
      abs(sum(played) / 2 - 16000 * 4) < 16000 * 0.1,
      f"{sum(played) / 2:.0f} samples")
played.clear()
voice.set_speed(2.0)
check("2× is remembered", M.voice_speed() == 2.0)
voice.toggle(note)
# The 2× copy is worked out on the side (the note plays on at 1× for that
# moment); this speaker runs faster than real time, so count from the top
# once it is ready.
wait_for(lambda: voice._desk is not None and voice._desk._speed == 2.0, 10)
voice.seek(note, 0.0)
played.clear()
wait_for(lambda: not voice.playing, 15)
check("...and plays the note in about half the time, its pitch kept",
      abs(sum(played) / 2 - 16000 * 2) < 16000 * 0.15,
      f"{sum(played) / 2:.0f} samples")
played.clear()
voice.set_speed(1.0)
voice.toggle(note, at=0.75)
wait_for(lambda: not voice.playing, 15)
check("touched three quarters along, it plays from there",
      abs(sum(played) / 2 - 16000 * 1) < 16000 * 0.1,
      f"{sum(played) / 2:.0f} samples")
check("the speed shows on the note while it plays",
      M.speed_label(1.5) == "1.5×")

print("\nShortcuts, pasting, dropping")
app.go_back_to_chats()
pump(0.4)
app._on_key_down(Window, ord("f"), None, "f", ["ctrl"])
pump(0.2)
check("Ctrl+F searches everywhere from the chat list",
      app.chats_screen.searching)
app.chats_screen.exit_search()
app.open_conversation(other)
pump(0.4)
app._on_key_down(Window, ord("f"), None, "f", ["ctrl"])
pump(0.2)
check("...and in the chat that is open", screen.searching)
screen.close_search()
opened = []
M.ComposeSheet.open = lambda self, *a, **k: opened.append("compose")
app._on_key_down(Window, ord("n"), None, "n", ["ctrl"])
check("Ctrl+N starts a new chat", opened == ["compose"])
picked = []
screen._picked = lambda path, temporary=False: picked.append(
    (path, temporary))
png = os.path.join(os.environ["HOME"], "pasted.png")
from PIL import Image                                           # noqa: E402
Image.new("RGB", (40, 30), (200, 120, 40)).save(png)
M.clipboard_image = lambda: (png, True)
app._on_key_down(Window, ord("v"), None, "v", ["ctrl"])
check("Ctrl+V of an image opens it for sending", picked == [(png, True)])
M.clipboard_image = lambda: None
check("...text is left for the field to paste",
      app._on_key_down(Window, ord("v"), None, "v", ["ctrl"]) is False)
app._on_drop_file(Window, png.encode(), 10, 10)
check("a photo dropped on the window goes to the open chat",
      picked[-1] == (png, False))
app._on_drop_file(Window, note.encode(), 10, 10)
check("...anything else is turned away", len(picked) == 2)

print("\nCalls: ringback, the service from dialling")
tone = M.DesktopRingback()
frames = [tone._pull() for _ in range(250)]          # five seconds
ringing = [f for f in frames if f]
check("the caller's ringback: one tone, a second in every five",
      len(ringing) == 50 and len(ringing[0]) == M.CALL_FRAME_BYTES)
started = []
M.IS_ANDROID = True
M.android_call_service = lambda on: started.append(on)
M.android_has_permission = lambda p: True
M.calls_possible_here = lambda: True
app._place_call = lambda call: None
app.store.add_caps(other, M.LocalStore.CAP_CALL | M.LocalStore.CAP_V9)
app.start_call(other)
check("on a phone the call service starts the moment one dials",
      started == [True] and app._call_service_on)
app.call.ended = True
M.IS_ANDROID = False

passed = sum(1 for _n, ok in RESULTS if ok)
print(f"\n  {passed}/{len(RESULTS)} passed")
sys.exit(0 if passed == len(RESULTS) else 1)
