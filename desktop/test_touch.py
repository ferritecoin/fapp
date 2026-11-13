#!/usr/bin/env python3
"""A phone's touches (16.0), against a real app controller and Kivy's own
input pipeline: a finger as Android's SDL delivers it, a touch with no
mouse button, where the other suites click with a mouse.

    xvfb-run -a python3 desktop/test_touch.py main.py

  * a tap on Send sends what is written (15.1.1 took every tap on it for
    a hold of the microphone, and sent nothing);
  * holding the microphone records while held, and letting go sends it;
  * a switch flips once per tap;
  * on Android, one touch per finger: SDL's mouse copy of a finger off,
    and Kivy's mouse input dropped (one_touch_per_finger);
  * a call that cannot be made says why in the chat (a Toast), not only
    on the chat list's line; a refused microphone says so; a given one
    places the call;
  * a finger resting in a list lights what is under it at once (Kivy's
    ScrollView showed nothing for its first 250 ms), a tap acts as the
    finger lifts, and a scroll lights nothing;
  * the connection's own state stays on the chat list's line;
  * a theme change, tapped in a frame, holds the garbage collector until
    the new theme is drawn, then collects once (16.2).
"""
import gc
import importlib.util
import os
import sys
import tempfile
import time

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
if sys.platform.startswith("linux"):     # xvfb-run's X display
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-touch-")
os.environ["USERPROFILE"] = os.environ["HOME"]   # Path.home() on Windows
os.environ["FEXT_LANG"] = "en"          # the English the checks read
os.environ["FEXT_THEME"] = "dark"        # the system's, whatever the machine
#                                     says (Ubuntu 22.04's GNOME: light)
os.environ["FEXT_SERVER_HOST"] = "127.0.0.1:1"

_spec = importlib.util.spec_from_file_location(
    "fext_touch", sys.argv[1] if len(sys.argv) > 1 else "main.py")
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_touch"] = M
_spec.loader.exec_module(M)
from kivy.base import EventLoop                                # noqa: E402
from kivy.config import ConfigParser                           # noqa: E402
from kivy.core.window import Window                            # noqa: E402
from kivy.core.window.window_sdl2 import SDL2MotionEventProvider  # noqa
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


# ---- a finger, as SDL hands it to Kivy on Android ---------------------------
def finger(action, widget):
    """SDL's finger event at the widget's centre: normalised, y from the
    top, as Kivy's SDL provider reads it (its touches have no button)."""
    x, y = widget.to_window(widget.center_x, widget.center_y)
    w, h = Window.size
    SDL2MotionEventProvider.q.appendleft((action, 7, x / w, 1 - y / h, 1.0))


def tap(widget, hold=0.08):
    finger("fingerdown", widget)
    pump(hold)
    finger("fingerup", widget)
    pump(0.3)


class FakeRecorder:
    """PcmRecorder's interface, without a microphone."""
    can_pause = paused = failed = False
    max_seconds = 60
    started = 0

    def __init__(self):
        FakeRecorder.started += 1
        self.levels, self._t0 = [], time.monotonic()

    @property
    def elapsed_ms(self):
        return int((time.monotonic() - self._t0) * 1000)

    def stop(self):
        return "voice.wav", max(1500, self.elapsed_ms), ""

    def cancel(self):
        pass


class StubApi:
    def __init__(self):
        self._features = {"blobs": True, "calls": True, "call_udp": 9}

    def features(self):
        return self._features

    def send_message(self, recipient, envelope, client_msg_id):
        return {"server_id": 1}


app = M.FextApp()
app._dismiss_splash = lambda *_a: None
app._probe_transport = lambda: None
root = app.build()
Window.add_widget(root)
Window.size = (dp(400), dp(760))
EventLoop.start()
priv, _p, _a, _n = M.mine_identity()
me = app.keys.add_identity(priv, "ann")
app.keys.activate(me)
app.keys.set_username("ann")
app.crypto = M.CryptoEngine(app.keys)
app.api = StubApi()
app.store = M.LocalStore(app.keys.store_path())
app.worker = M.SyncWorker(app)
app.worker.beat_owner = None
_op, other_pub, other, _n = M.mine_identity()
app.store.upsert_contact(other, "ben", other_pub.hex())
M.ConversationScreen._can_record = True    # as on a phone: voice notes
M.PcmRecorder = FakeRecorder
VOICE = []
app.send_voice = lambda path, dur, wave_b64: VOICE.append(dur)
app.refresh_sidebar()
pump(0.3)
app.open_conversation(other)
pump(0.5)
screen = app.conversation_screen

print("\nSend and the microphone, by finger")
screen.entry.input.focus = True
screen.entry.input.insert_text("hello there")
pump(0.3)
before = app.store.message_count(other)
tap(screen.action_button)
check("a tap on Send sends what is written",
      app.store.message_count(other) == before + 1 and
      screen.entry.input.text == "",
      f"{app.store.message_count(other) - before} sent")
check("...and records nothing", FakeRecorder.started == 0 and
      screen.recorder is None)
pump(0.3)
finger("fingerdown", screen.action_button)
pump(0.6)
check("holding the microphone records while it is held",
      screen.recorder is not None and not screen._rec_locked and
      screen.action_button._holding)
finger("fingerup", screen.action_button)
pump(0.3)
check("...letting go sends the voice note", len(VOICE) == 1 and
      VOICE[0] >= 1500 and screen.recorder is None, VOICE)

print("\nA switch")
switch = M.ToggleSwitch(False, size_hint=(None, None))
flips = []
switch._on_change = flips.append
Window.add_widget(switch)
switch.pos = (dp(40), dp(300))
pump(0.1)
tap(switch)
check("a tap flips it once", switch.active and flips == [True], flips)
Window.remove_widget(switch)

print("\nOne touch per finger on Android")
config = ConfigParser(name="fext-touch")
config.add_section("input")
config.set("input", "mouse", "mouse")
config.set("input", "%(name)s", "probesysfs")
env = {}
getattr(M, "one_touch_per_finger", lambda *_a: None)(config, env)
check("SDL's mouse copy of a finger off; Kivy's mouse input dropped",
      env.get("SDL_TOUCH_MOUSE_EVENTS") == "0" and
      dict(config.items("input")) == {"%(name)s": "probesysfs"},
      f"{env} {dict(config.items('input'))}")

print("\nCalls: why not, said in the chat")
tap(screen.call_button)
TOAST = getattr(M, "Toast", None)


def shown():
    return TOAST.current if TOAST is not None else None


note = shown()
check("a call that cannot be made says why, in the chat",
      app.call is None and note is not None and note.parent is Window and
      "accept your chat request" in note.text, note and note.text)
check("...over the page, taking no touches",
      note is not None and not note.on_touch_down(None))
app.store.add_caps(other, M.LocalStore.CAP_CALL | M.LocalStore.CAP_V9)
M.IS_ANDROID = True
asked = []
answer = [False]
M.android_has_permission = lambda p: False
M.android_request_permissions = lambda perms, cb: (asked.append(perms),
                                                   cb(perms, answer[:]))
M.android_call_service = lambda on: None
M.calls_possible_here = lambda: True
placed = []
app._place_call = lambda call: placed.append(call)


def start_from_lobby():
    """16.1: the call button opens the lobby; its Start call places the
    call, and that is when the microphone is asked for."""
    tap(screen.call_button)
    pump(0.2)
    lobby = app.__dict__.get("_lobby")
    if lobby is not None:
        tap(lobby.join_button)
        pump(0.2)
    return lobby


lobby = start_from_lobby()
note = shown()
check("the call button opens the lobby, which asks for nothing; its Start "
      "call asks for the microphone — refused, it says so, nothing rings",
      lobby is not None and asked == [["android.permission.RECORD_AUDIO"]]
      and app.call is None and note is not None and
      "microphone" in note.text, note and note.text)
answer[0] = True
start_from_lobby()
pump(0.1)
check("given, the call is placed", app.call is not None and
      len(placed) == 1 and len(asked) == 2)
app.call.ended = True
app._close_call_screen(app.call)
M.IS_ANDROID = False
pump(0.3)

print("\nTaps in a list")
app.go_back_to_chats()
pump(0.6)


def chat_row():
    return next((w for w in app.chats_screen.list.walk()
                 if isinstance(w, M.ConversationRow) and w.peer == other),
                None)


row = chat_row()
finger("fingerdown", row)
pump(0.15)
check("a finger held still on a chat lights it at once, before the list "
      "knows it is no scroll", row.state == "down" and
      app.manager.current == "chats")
finger("fingerup", row)
lifted = time.monotonic()
while time.monotonic() - lifted < 1.0 and app.manager.current == "chats":
    EventLoop.idle()
    time.sleep(0.002)
took = time.monotonic() - lifted
check("a tap on a chat opens it as the finger lifts",
      app.manager.current == "conversation" and took < 0.12,
      f"{took * 1000:.0f} ms")
app.go_back_to_chats()
pump(0.6)
row = chat_row()
x, y = row.to_window(row.center_x, row.center_y)
w, h = Window.size
SDL2MotionEventProvider.q.appendleft(("fingerdown", 9, x / w, 1 - y / h, 1))
pump(0.1)
for step in range(1, 9):
    SDL2MotionEventProvider.q.appendleft(
        ("fingermotion", 9, x / w, 1 - (y + step * dp(12)) / h, 1))
    pump(0.02)
SDL2MotionEventProvider.q.appendleft(
    ("fingerup", 9, x / w, 1 - (y + 8 * dp(12)) / h, 1))
pump(0.4)
check("...one that becomes a scroll goes dark again, and opens nothing",
      row.state == "normal" and app.manager.current == "chats")

print("\nThe connection's own state")
if shown() is not None:
    shown().dismiss(fade=False)
app.ui_queue.put(("link", "Reconnecting…", M.COL_TEXT_DIM))
app._drain_ui_queue(0)
pump(0.1)
check("stays on the chat list's line, with no note over the chat",
      app.chats_screen.status_label.text == "Reconnecting…" and
      TOAST is not None and shown() is None)

print("\nA theme change, tapped in a frame")
gc_runs = []


def _gc_watch(phase, _info):
    if phase == "stop":
        gc_runs.append(phase)


held = []
M.Clock.schedule_once(lambda _dt: (app.set_appearance(theme="light"),
                                   held.append(not gc.isenabled())), 0)
gc.callbacks.append(_gc_watch)
pump(0.01)
during = list(gc_runs)
pump(0.3)
moving = list(gc_runs)
pump(0.4)
gc.callbacks.remove(_gc_watch)
check("the collector waits while every screen is built and drawn, and "
      "the theme's icon moves, then collects once",
      held == [True] and during == moving == [] and len(gc_runs) >= 1 and
      gc.isenabled(), f"held {held}, {len(during)} in the frame, "
      f"{len(moving)} in the move, {len(gc_runs)} in all")
app.set_appearance(theme="dark")
pump(0.6)

passed = sum(1 for _n, ok in RESULTS if ok)
print(f"\n  {passed}/{len(RESULTS)} passed")
sys.exit(0 if passed == len(RESULTS) else 1)
