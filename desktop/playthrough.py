#!/usr/bin/env python3
"""A walk through the real app by touch, timed and pictured: its "feel".

    xvfb-run -a -s "-screen 0 1280x1024x24" \\
        python3 desktop/playthrough.py main.py [OUTDIR] [--theme light]
            [--profile STEP] [--wide]

A phone (392 x 840 dp; --wide: a computer's window, 1180 x 760, the
list beside the chat) with 40 chats, one of 400 messages and a group; the
network stubbed out (what is sent is taken as delivered). Each step is
what a person does, by the same touches a finger makes (a tap inside a
list waits the 55 ms a list waits to tell a tap from a scroll, as on a
phone), and is timed twice:

  * answered: from the finger lifting to the first frame that shows the
    answer (the chat open, the bubble there), in ms;
  * worst frame: the longest any one frame took to make, from then
    until the page has stood still for a quarter of a second (a move
    that stutters: 16 ms is a frame at 60 a second);
  * collector: how much of the step Python's garbage collector took (its
    pauses are the usual cause of one long frame among short ones).

Frames are not held to 60 a second, so a frame's time is its work. OUTDIR
(a temporary folder, if none is named) gets a picture of the screen after
each step (NN_step.png), the numbers (playthrough.json) and both as a
table (playthrough.md). Read the table
beside the pictures: under 100 ms an answer feels immediate; past 300 ms
a person notices the wait. The times are this machine's: a phone's are
several times longer, so compare versions here, as desktop/bench.py says.

It is also a test of whole flows, and CI runs it as one: a step whose
answer never comes in ten seconds, or that raises, fails the run (exit 1;
its last line counts the steps answered, as the suites count checks).

--profile STEP prints where the screen's own thread spent that step
(cProfile, the 25 costliest functions), from its touch until the page
rests: the first thing to read when a step is slow.
"""
import argparse
import cProfile
import gc
import json
import os
import pstats
import random
import statistics
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("client", help="the main.py to walk through")
parser.add_argument("outdir", nargs="?", default="",
                    help="where the pictures and numbers go")
parser.add_argument("--theme", choices=("dark", "light"), default="dark")
parser.add_argument("--wide", action="store_true",
                    help="a computer's window, 1180 x 760: two panes (19.0)")
parser.add_argument("--profile", default="", metavar="STEP",
                    help="print where this step's time went")
ARGS = parser.parse_args()
ARGS.outdir = ARGS.outdir or tempfile.mkdtemp(prefix="fext-walk-out-")
os.makedirs(ARGS.outdir, exist_ok=True)

os.environ.update(KIVY_NO_ARGS="1", KIVY_NO_CONSOLELOG="1",
                  KIVY_METRICS_DENSITY="2",
                  FEXT_THEME=ARGS.theme, FEXT_SERVER_HOST="127.0.0.1:1",
                  FEXT_LANG="en", KCFG_GRAPHICS_MAXFPS="0")
if sys.platform.startswith("linux"):     # xvfb-run's X display; Windows
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")     # has none
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-walk-")
os.environ["USERPROFILE"] = os.environ["HOME"]
os.makedirs(os.path.join(os.environ["HOME"], ".fext"))
with open(os.path.join(os.environ["HOME"], ".fext", "prefs.json"), "w") as f:
    # The guides that open by themselves on a first day stay closed.
    f.write('{"keep_running_offered": true, "app_lock_offered": true}')

import importlib.util                                           # noqa: E402
import logging                                                  # noqa: E402
logging.disable(logging.CRITICAL)
_spec = importlib.util.spec_from_file_location("fext_walk", ARGS.client)
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_walk"] = M
_spec.loader.exec_module(M)
M.IS_MOBILE = True
from kivy.clock import Clock                                    # noqa: E402
from kivy.core.window import Window                             # noqa: E402
from kivy.metrics import dp                                     # noqa: E402

# ---- the phone: who is on it --------------------------------------------------
rng = random.Random(1820)
keys = M.IdentityManager()
ME = keys.add_identity(M.mine_identity()[0], "Personal")
keys.activate(ME)
keys.set_username("kit")
store = M.LocalStore(keys.store_path())
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def person(name, real=False):
    """A contact: with a real key where the walk writes to them."""
    if real:
        priv = M.mine_identity()[0]
        address = M.address_from_priv(priv)
        key = M.pub_to_compressed(M.priv_to_pub(priv)).hex()
    else:
        address = "Fec" + "".join(rng.choice(B58) for _ in range(31))
        key = "02" + "".join(rng.choice("0123456789abcdef")
                             for _ in range(64))
    store.upsert_contact(address, name, key)
    return address


MINA = person("mina", real=True)
OTHERS = [person(n) for n in (
    "alex", "noor", "sam", "chen", "eve", "omar", "lena", "ravi", "ines",
    "tomas", "yuki", "zara", "ben", "aiko", "dmitri", "fatima", "george",
    "hana", "ivan", "june", "karim", "lucia", "mateo", "nadia", "oskar",
    "priya", "quinn", "rosa", "stefan", "tara", "ugo", "vera", "wei",
    "xavier", "yara", "zoltan", "amara", "bruno")]
WORDS = ("the ridge path closes at dusk so we should start by noon and "
         "bring water the map is in the shared folder see you at the gate "
         "photos from the trip are up").split()
NOW = datetime.now(timezone.utc)
SID = [1000]


def line(least=3, most=30):
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(least, most)))


rows = []
# The list shows the chat written to last first: Mina's, made last here.
for place, (peer, count) in enumerate(
        [(p, 12) for p in OTHERS[::-1]] + [(MINA, 400)]):
    for i in range(count):
        SID[0] += 1
        at = NOW - timedelta(minutes=(count - i) * 9 + rng.randint(0, 5)
                             + (len(OTHERS) - place) * 40)
        rows.append((uuid.uuid4().hex, peer, "in" if i % 3 else "out",
                     line(), 3, 1, at.isoformat(), SID[0]))
with store._lock:
    store._db.executemany(
        "INSERT INTO messages (client_msg_id, peer, direction, body, status, "
        "read, created_at, server_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    store._db.commit()
store.close()



def delivered(_api, _recipient, _envelope, _cid):
    """What is sent is taken as delivered: the relay is not there."""
    SID[0] += 1
    return {"server_id": SID[0]}


M.ApiClient.send_message = delivered

# ---- the walk's bookkeeping ---------------------------------------------------
PROFILER = cProfile.Profile()
PAUSES = {"began": 0.0, "spent": 0.0}       # the collector's, this step


def collecting(phase, _info):
    if phase == "start":
        PAUSES["began"] = time.perf_counter()
    else:
        PAUSES["spent"] += time.perf_counter() - PAUSES["began"]


gc.callbacks.append(collecting)
STEPS: list = []            # what was done: name, words, ms, worst frame
FAILED: list = []
STILL = 0.25                # this long without a new frame: it stands still
MOVING = 2.5                # ...or this long since the answer: a field's
#                             cursor blinks on, and that is not a move
PATIENCE = 10.0             # a step's answer must come within this
app = M.FextApp()
state = {"waiting": None, "turn": 0.0, "plan": [], "index": 0}


class Overlooked(M.ExceptionHandler):
    """An error the app's crash net passes over (CrashNet: the loop goes
    on) still fails the walk: without this, a step whose answer came was
    answered whatever was raised on the way (VERIFY26)."""

    def handle_exception(self, exc):
        if isinstance(exc, Exception):
            step = state["waiting"]
            FAILED.append(f"{step['name'] if step else 'between steps'}: "
                          f"{type(exc).__name__}: {exc} (passed over)")
        return M.ExceptionManager.RAISE      # the net's own answer stands


M.ExceptionManager.add_handler(Overlooked())


def chat():
    return app.conversation_screen


def finger(action, x, y):
    """A finger as Android's SDL gives it to Kivy (a touch, no button)."""
    from kivy.core.window.window_sdl2 import SDL2MotionEventProvider
    w, h = Window.size
    SDL2MotionEventProvider.q.appendleft((action, 5, x / w, 1 - y / h, 1.0))


def centre(widget):
    return widget.to_window(widget.center_x, widget.center_y)


TAP = 0.09                  # a finger rests this long in a tap


def tap(widget):
    """A finger down on the widget's middle and, TAP later, up again. The
    step's time runs from its lifting."""
    x, y = centre(widget)
    finger("fingerdown", x, y)

    def lift():
        finger("fingerup", x, y)
        if state["waiting"] is not None:
            state["waiting"]["began"] = time.perf_counter()
    later(lift, TAP)


def find(root, kind, said=None, **having):
    """The first widget of `kind` under `root` (the window: under any of
    its layers) whose label, title or text is `said`, or whose attributes
    are `having`."""
    if root is Window:
        for layer in Window.children:
            try:
                return find(layer, kind, said, **having)
            except LookupError:
                pass
        raise LookupError(f"no {kind.__name__} {said or having!r} in the "
                          "window")
    for w in root.walk(restrict=True):
        if not isinstance(w, kind) or w.width <= 0:
            continue
        words = getattr(w, "label", None)
        if hasattr(words, "text"):
            words = words.text
        title = getattr(getattr(w, "title", None), "text", None)
        if said is not None and said not in (words, title,
                                             getattr(w, "text", None)):
            continue
        if all(getattr(w, k, None) == v for k, v in having.items()):
            return w
    raise LookupError(f"no {kind.__name__} {said or having!r} on the page")


def do(name, words, action, done, settle=STILL):
    """A step: `action` now; answered at the first frame where `done()`
    holds; then watched until the page stands still."""
    PAUSES["spent"] = 0.0
    if name == ARGS.profile:
        PROFILER.enable()
    state["waiting"] = {"name": name, "words": words, "done": done,
                        "began": time.perf_counter(), "answered": None,
                        "frames": [], "settle": settle,
                        "limit": time.perf_counter() + PATIENCE}
    try:
        action()
    except Exception as exc:                            # noqa: BLE001
        fail(f"{type(exc).__name__}: {exc}")
        return
    Window.canvas.ask_update()


def later(job, wait=0.0):
    """`job` on a coming frame; should it raise (an older version without
    the part a step uses), the step fails and the walk goes on."""
    def run(_dt):
        try:
            job()
        except Exception as exc:                        # noqa: BLE001
            if state["waiting"] is not None:
                fail(f"{type(exc).__name__}: {exc}")
    Clock.schedule_once(run, wait)


def fail(why):
    step = state["waiting"]
    state["waiting"] = None
    FAILED.append(f"{step['name']}: {why}")
    STEPS.append({"step": step["name"], "does": step["words"],
                  "answered_ms": None, "worst_frame_ms": None, "failed": why})
    print(f"  {step['name']}: FAILED, {why}", flush=True)
    Clock.schedule_once(lambda _dt: advance(), 0.1)


def flipped(*_a):
    """Each frame drawn: is the step answered; has the page come to rest."""
    step, now = state["waiting"], time.perf_counter()
    if step is None:
        return
    if step["answered"] is None:
        try:
            answered = step["done"]()
        except Exception as exc:                        # noqa: BLE001
            fail(f"{type(exc).__name__}: {exc}")
            return
        if answered:
            step["answered"] = now
            step["last"] = now
        elif now > step["limit"]:
            fail(f"no answer in {PATIENCE:g} s (on {app.manager.current})")
        else:
            Window.canvas.ask_update()
        return
    step["frames"].append(now - state["turn"])    # this frame's own work
    step["last"] = now


def watch(_dt):
    """Ten times a second: a page that has drawn nothing for `settle` has
    come to rest; its picture is taken and the next step begins."""
    step, now = state["waiting"], time.perf_counter()
    if step is None:
        return
    if step["answered"] is None:
        if now > step["limit"]:
            fail(f"no answer in {PATIENCE:g} s (on {app.manager.current})")
        else:
            Window.canvas.ask_update()
        return
    if now - step["last"] < step["settle"] and \
            now - step["answered"] < MOVING:
        return
    state["waiting"] = None
    if step["name"] == ARGS.profile:
        PROFILER.disable()
    moves = step["frames"]
    STEPS.append({
        "step": step["name"], "does": step["words"],
        "answered_ms": round((step["answered"] - step["began"]) * 1000, 1),
        "worst_frame_ms": round(max(moves) * 1000, 1) if moves else 0.0,
        "frames": len(moves),
        "collector_ms": round(PAUSES["spent"] * 1000, 1)})
    print(f"  {step['name']}: {STEPS[-1]['answered_ms']:.0f} ms",
          flush=True)
    picture = os.path.join(ARGS.outdir,
                           f"{len(STEPS):02d}_{step['name']}.png")
    Window.screenshot(name=picture)
    Clock.schedule_once(lambda _dt: advance(), 0.15)


def advance():
    if state["index"] < len(state["plan"]):
        state["index"] += 1
        state["plan"][state["index"] - 1]()
    else:
        finish()


# ---- the steps ------------------------------------------------------------------
def chat_row(peer):
    return find(app.chats_screen.list, M.ConversationRow, peer=peer)


def open_mina():
    do("open_chat", "Tap a chat in the list",
       lambda: tap(chat_row(MINA)),
       lambda: app.manager.current == "conversation" and
       chat()._rendered_peer == MINA and len(chat()._bubbles) > 0,
       settle=0.6)                  # the rest of its first page follows


def type_a_line():
    """Twenty letters, one a frame, as a phone's keyboard sends them:
    the window is told of each, and gives it to the field that has the
    keyboard."""
    field, text = chat().entry.input, "See you at the gate!"
    left = list(text)

    def letter():
        Window.dispatch("on_textinput", left.pop(0))

    def press():
        field.focus = True
        letter()

    def typed():
        if left:
            letter()
            return False
        return field.text == text
    do("type", "Type a line of twenty letters", press, typed)


def send_it():
    before = len(chat()._bubbles)
    do("send", "Tap Send",
       lambda: tap(find(chat(), M.IconButton, "Send")),
       lambda: len(chat()._bubbles) > before and
       chat().entry.input.text == "")


def reply_arrives():
    before = len(chat()._bubbles)

    def arrive():
        SID[0] += 1
        app.store.add_in(MINA, "On my way. Ten minutes!", M.now_iso(),
                         SID[0], uuid.uuid4().hex)
        app.ui_queue.put(("message_stored", MINA))
    do("arrives", "A reply arrives while the chat is open", arrive,
       lambda: len(chat()._bubbles) > before)


def newest(direction):
    for msg_id in sorted(chat()._bubbles, reverse=True):
        if chat()._bubbles[msg_id].msg["direction"] == direction:
            return msg_id
    raise LookupError(f"no message that came {direction}")


def tap_message():
    target = newest("in")
    do("message_menu", "Tap the message that arrived",
       lambda: tap(chat()._bubbles[target]),
       lambda: chat()._strip_for == target and not chat().menu.concealed)


def react():
    target = chat()._strip_for
    key = next(iter(chat().strip.keys.values()))
    do("react", "Tap a reaction",
       lambda: tap(key),
       lambda: bool(chat()._bubbles[target].reactions and
                    chat()._bubbles[target].reactions.chips))


def reply_from_menu():
    target = newest("in")

    def press():
        chat().on_bubble_tap(target)
        later(lambda: tap(find(chat().menu, M.MenuRow, "Reply")))
    do("reply", "Tap the message, then Reply", press,
       lambda: chat().reply_to is not None and not chat().reply_bar.concealed)


def scroll_back():
    """A flick upward through the history, the finger moving 600 dp in
    twelve frames, then let go: the list coasts."""
    view = chat().scroll
    x, y = view.to_window(view.center_x, view.center_y - dp(220))
    moves = [y + dp(50) * n for n in range(1, 13)]
    start = view.scroll_y

    def flick():
        chat().cancel_reply()
        finger("fingerdown", x, y)

        def move():
            if moves:
                finger("fingermotion", x, moves.pop(0))
                later(move)
            else:
                finger("fingerup", x, y + dp(600))
        later(move)
    do("scroll", "Flick up through the chat's history", flick,
       lambda: not moves and view.scroll_y != start, settle=0.5)


def back_to_list():
    do("back", "Tap Back",
       lambda: tap(find(chat(), M.IconButton, "Back")),
       lambda: app.manager.current == "chats")


def open_menu():
    do("menu", "Open the menu",
       lambda: tap(find(app.chats_screen._root, M.IconButton, "Menu")),
       lambda: app.drawer is not None and app.drawer.parent is not None)


def to_settings():
    do("settings", "Tap Settings in the menu",
       lambda: tap(find(Window, M.DrawerRow, "Settings")),
       lambda: app.manager.current == "settings")


def change_theme():
    other = "light" if M.T.name != "light" else "dark"
    row = next(w for w in app.settings_screen.walk()
               if isinstance(w, M.Segmented))
    do("theme", "Choose the other theme (every page is made again)",
       lambda: row.choose(other),
       lambda: M.T.name == other and app.manager.current == "settings",
       settle=0.5)


def leave_settings():
    do("settings_back", "Tap Back",
       lambda: tap(find(app.settings_screen, M.IconButton, "Back")),
       lambda: app.manager.current == "chats")


def new_chat():
    do("compose", "Tap the New conversation button",
       lambda: tap(app.chats_screen.fab),
       lambda: any(isinstance(w, M.Sheet) for w in Window.children))


def close_sheet():
    def close():
        for sheet in [w for w in Window.children if isinstance(w, M.Sheet)]:
            sheet.dismiss()
    do("sheet_closed", "Close the sheet", close,
       lambda: not any(isinstance(w, M.Sheet) for w in Window.children))


state["plan"] = [open_mina, type_a_line, send_it, reply_arrives, tap_message,
                 react, reply_from_menu, scroll_back, back_to_list, open_menu,
                 to_settings, change_theme, leave_settings, new_chat,
                 close_sheet]


# ---- the report ---------------------------------------------------------------
def feel(ms):
    if ms is None:
        return "FAILED"
    return "at once" if ms <= 100 else "quick" if ms <= 300 else \
        "a wait" if ms <= 1000 else "slow"


def finish():
    for name in os.listdir(ARGS.outdir):    # Kivy numbers its screenshots
        if name.endswith("0001.png"):
            os.replace(os.path.join(ARGS.outdir, name),
                       os.path.join(ARGS.outdir, name[:-8] + ".png"))
    answered = [s["answered_ms"] for s in STEPS if s["answered_ms"]]
    summary = {
        "client": M.APP_VERSION, "theme": ARGS.theme, "steps": STEPS,
        "median_answer_ms": round(statistics.median(answered), 1)
        if answered else None,
        "slowest": max(STEPS, key=lambda s: s["answered_ms"] or 0)["step"]
        if STEPS else None, "failed": FAILED}
    with open(os.path.join(ARGS.outdir, "playthrough.json"), "w") as out:
        json.dump(summary, out, indent=1)
    table = [f"# A walk through Ferritext {M.APP_VERSION} ({ARGS.theme})", "",
             "| # | step | what a person does | answered | feels | "
             "worst frame | collector |", "|---|---|---|---|---|---|---|"]
    for n, s in enumerate(STEPS, 1):
        table.append(
            f"| {n} | `{s['step']}` | {s['does']} | "
            + (f"{s['answered_ms']:.0f} ms" if s["answered_ms"] is not None
               else s.get("failed", "")) + f" | {feel(s['answered_ms'])} | "
            + (f"{s['worst_frame_ms']:.0f} ms" if s["worst_frame_ms"]
               is not None else "") + " | "
            + (f"{s['collector_ms']:.0f} ms" if s.get("collector_ms")
               else "") + " |")
    with open(os.path.join(ARGS.outdir, "playthrough.md"), "w") as out:
        out.write("\n".join(table) + "\n")
    print("\n".join(table))
    for why in FAILED:
        print("FAILED " + why)
    print(f"\n  {len(STEPS) - len(FAILED)}/{len(STEPS)} passed "
          f"({ARGS.outdir})")
    if ARGS.profile:
        pstats.Stats(PROFILER).sort_stats("cumulative").print_stats(25)
    app.stop()


def started(_dt):
    Window.size = (dp(1180), dp(760)) if ARGS.wide else (dp(392), dp(840))
    Window.bind(on_flip=flipped)
    Clock.schedule_interval(watch, 0.1)
    # Each turn of the app's loop begins here; a frame's work runs from
    # this to its flip (a turn that draws nothing is no frame).
    Clock.schedule_interval(
        lambda _dt: state.__setitem__("turn", time.perf_counter()), 0)
    do("start", "Open the app", lambda: None,
       lambda: app.manager.current == "chats" and
       len(app.chats_screen.list.children) > 5, settle=1.0)
    state["waiting"]["began"] = BEGAN


BEGAN = time.perf_counter()
Clock.schedule_once(started, 0)
app.run()
os._exit(1 if FAILED else 0)
