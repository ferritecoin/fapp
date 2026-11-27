#!/usr/bin/env python3
"""Moving around the app (v9.1.2), against two real app controllers.

    xvfb-run -a python3 desktop/test_navigation.py main.py

  * screens move as layers, and the page being uncovered is drawn beneath;
  * a chat opens with its newest messages drawn at once and the rest of its
    first page built after, in order, with one heading per day;
  * the menu: what it holds, and that it closes;
  * back goes one level back, all the way out, and never quits the app;
  * search finds names and words in any case, and opens a chat at a found
    message however far back, following on to the end;
  * a held chat: mute (one: its opposite; several: what most need), archive,
    pin, mark unread and read, block (what they send is held, then
    arrives on unblocking),
    delete (a group is left first);
  * a tap on a message raises its reactions; a second tap puts them away;
  * an error while a screen is drawn is logged and said, and the app
    carries on; a second shows the chat list; a storm ends it (18.2).
"""
import importlib.util
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
if sys.platform.startswith("linux"):     # xvfb-run's X display
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-nav-")
os.environ["USERPROFILE"] = os.environ["HOME"]   # Path.home() on Windows
os.environ["FEXT_LANG"] = "en"          # the English the checks read
os.environ["FEXT_SERVER_HOST"] = "127.0.0.1:1"

_spec = importlib.util.spec_from_file_location(
    "fext_nav", sys.argv[1] if len(sys.argv) > 1 else "main.py")
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_nav"] = M
_spec.loader.exec_module(M)
from kivy.clock import Clock                                   # noqa: E402

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

    def features(self):
        return self._features

    def send_message(self, recipient, envelope, client_msg_id):
        return {"server_id": self.relay.post(self.keys.address, recipient,
                                             envelope, client_msg_id)}

    def read(self, ids):
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
    return app


def deliver(relay, parties):
    acked = []
    while relay.mail:
        msg = relay.mail.pop(0)
        acked.append(parties[msg["recipient"]].worker._ingest_message(msg))
    return acked


relay = Relay()
alice, bob = make_party("alice", relay), make_party("bob", relay)
parties = {alice.keys.address: alice, bob.keys.address: bob}
A, B = alice.keys.address, bob.keys.address
for me, other in ((alice, bob), (bob, alice)):
    me.store.upsert_contact(other.keys.address, other.keys.username,
                            other.keys.public_key_hex)
# a long history with Bob on Alice's phone, over several days
start = datetime.now(timezone.utc) - timedelta(days=4)
for i in range(130):
    ts = (start + timedelta(minutes=50 * i)).isoformat()
    body = "the needle is here" if i == 20 else f"message number {i}"
    if i % 2:
        alice.store.add_in(B, body, ts, 1000 + i, uuid.uuid4().hex,
                           unread=False)
    else:
        o = alice.store.add_out(B, body, ts, uuid.uuid4().hex)
        alice.store.mark_sent(o, 5000 + i)
ids = [m["id"] for m in alice.store.messages_for(B)]

print("\nLayers")
check("going forward, the new screen fades in over the old one",
      isinstance(M.LayerTransition(forward=True), M.TransitionBase))
alice._navigate("settings", "left")
tr = alice.manager.transition
check("...a layer transition, quick",
      isinstance(tr, M.LayerTransition) and tr.duration <= 0.2)
tick(0.4)
alice._navigate("chats", "right")
tr = alice.manager.transition
order = list(alice.manager.children)
check("going back, the uncovered page is drawn BENEATH the leaving one",
      len(order) == 2 and order[-1] is alice.chats_screen, str(order))
tick(0.4)
check("...and when it ends only the page uncovered is left",
      list(alice.manager.children) == [alice.chats_screen] and
      alice.chats_screen.opacity == 1 and alice.chats_screen.x == 0)

print("\nOpening a chat")
alice.open_conversation(B)
screen = alice.conversation_screen
check("it shows at once with the newest messages drawn",
      len(screen._bubbles) == M.FIRST_PAINT and
      max(screen._bubbles) == ids[-1], str(len(screen._bubbles)))
wait_for(lambda: len(screen._bubbles) >= M.CHAT_PAGE_SIZE and
         screen._backfill_ev is None, 10)
drawn = [w for w in reversed(screen.messages.children)
         if isinstance(w, M.BubbleRow)]
check("...then the rest of its first page, in order",
      len(screen._bubbles) == M.CHAT_PAGE_SIZE and
      [r.msg_id for r in drawn] == ids[-M.CHAT_PAGE_SIZE:])
days = [w for w in screen.messages.children if isinstance(w, M.DayDivider)]
expected_days = len({m["created_at"][:10] for m in
                     alice.store.messages_for(B)[-M.CHAT_PAGE_SIZE:]})
check("...with one heading per day, none repeated where pages join",
      len(days) == expected_days, f"{len(days)} vs {expected_days}")
check("...and the earlier history still offered",
      screen.has_older and screen._more_button.parent is screen.messages)
alice.load_older_messages()
wait_for(lambda: screen._backfill_ev is None and len(screen._bubbles) >
         M.CHAT_PAGE_SIZE, 10)
check("earlier messages join on above, never rebuilt",
      len(screen._bubbles) == min(130, 2 * M.CHAT_PAGE_SIZE) and
      screen._rendered_ids == ids[-len(screen._bubbles):])
alice.go_back_to_chats()
tick(0.3)

print("\nA page changed again before it has settled (18.1)")


def only(screen):
    """`screen` alone is on show, whole and in place."""
    return list(alice.manager.children) == [screen] and \
        screen.opacity == 1 and screen.x == 0


def settled():
    """Ticks until the page change under way has ended, however slow the
    frames (a chat drawn under Python's development mode can take 0.4 s),
    then a little more: a change left running from before would have
    ended too, and taken its page off."""
    wait_for(lambda: not alice.manager.transition.is_active, 5)
    tick(0.3)


alice.open_conversation(B)
tick(0.4)
alice.go_back_to_chats()
alice.open_conversation(B)          # at once: the way back is still moving
settled()
check("back, then the same chat again at once: the chat is on show, not "
      "an empty page (the change still under way took it off again as it "
      "ended)",
      alice.manager.current == "conversation" and
      only(alice.conversation_screen), str(list(alice.manager.children)))
alice.go_back_to_chats()
tick(0.05)
alice.open_conversation(B)          # ...and part of the way through it
settled()
check("...the same part of the way through the change",
      alice.manager.current == "conversation" and
      only(alice.conversation_screen), str(list(alice.manager.children)))
alice.go_back_to_chats()
alice._navigate("settings", "left")
alice._navigate("chats", "right")
settled()
check("three changes at once end on the last page asked for, alone",
      alice.manager.current == "chats" and only(alice.chats_screen),
      str(list(alice.manager.children)))

print("\nOpening where the unread begin (v12)")
# Laid out for real here: where a chat opens depends on the view's height.
top = alice.manager
while top.parent is not None and top.parent is not M.Window:
    top = top.parent
M.Window.size = (M.dp(400), M.dp(720))
M.Window.add_widget(top)
tick(0.5)
pk, *_ = M.mine_identity()
D = M.address_from_priv(pk)
alice.store.upsert_contact(D, "dora", M.pub_to_compressed(
    M.priv_to_pub(pk)).hex())
t0 = datetime.now(timezone.utc) - timedelta(hours=3)
for i in range(30):
    alice.store.add_in(D, f"old news {i}", (t0 + timedelta(minutes=i))
                       .isoformat(), 7000 + i, uuid.uuid4().hex, unread=False)
for i in range(5):
    alice.store.add_in(D, f"fresh {i}", (t0 + timedelta(minutes=60 + i))
                       .isoformat(), 7100 + i, uuid.uuid4().hex)
first = alice.store.unread_for(D)[0][0]
alice.open_conversation(D)
tick(0.6)
line = screen._new_line
order = [w for w in reversed(screen.messages.children)
         if isinstance(w, (M.BubbleRow, M.NewMessagesLine))]
at = order.index(line) if line in order else -1
check("a 'New messages' line goes above the first unread, with the count",
      line is not None and 0 <= at < len(order) - 1 and
      order[at + 1].msg_id == first and
      line.children[1].text.lower() == "5 new messages")
check("...all of them in view: the chat opens at its end",
      screen.at_bottom())
alice.go_back_to_chats()
tick(0.3)
long = "and a line long enough to wrap " * 4
for i in range(25):
    alice.store.add_in(D, f"later {i} {long}", (t0 + timedelta(
        minutes=90 + i)).isoformat(), 7200 + i, uuid.uuid4().hex)
first = alice.store.unread_for(D)[0][0]
alice.open_conversation(D)
tick(0.8)
line = screen._new_line
drawn = sorted(screen._bubbles)
check("more than a screen of them: the chat opens at the line, not the end",
      line is not None and not screen.at_bottom() and
      line.to_window(*line.pos)[1] <= screen.scroll.to_window(
          *screen.scroll.pos)[1] + screen.scroll.height and
      line.to_window(*line.pos)[1] >= screen.scroll.to_window(
          *screen.scroll.pos)[1])
check("...three messages of what was read before it, for context",
      len([m for m in drawn if m < first]) == 3)
alice.go_back_to_chats()
tick(0.3)
alice.open_conversation(D)
tick(0.6)
check("opened again with nothing unread: no line, and the end of the chat",
      screen._new_line is None and screen.at_bottom())
alice.go_back_to_chats()
tick(0.3)
M.Window.remove_widget(top)

print("\nThe menu")
alice.open_drawer()
tick(0.4)
drawer = alice.drawer
words = {w.title.text for w in drawer.column.children
         if isinstance(w, M.DrawerRow)}
check("it holds the identity and its sections",
      {"New identity", "My profile", "New group", "Saved messages",
       "Settings", "About"} <= words, str(words))
check("...over 80% of the screen, from the left",
      abs(drawer.panel.width - min(drawer.width * .8, drawer.MAX_WIDTH)) < 2
      and abs(drawer.panel.x - drawer.x) < 2)
alice._on_keyboard(None, 27)
tick(0.4)
check("back closes it", not drawer.is_open and drawer.parent is None)

print("\nBack, one level at a time")
alice.open_settings()
tick(0.3)


def settings_rows(w):
    for c in w.children:
        if hasattr(c, "icon") and isinstance(c, M.BoxLayout):
            yield c
        yield from settings_rows(c)


marked = list(settings_rows(alice.settings_screen.body))
check("every row in Settings has its icon, in one column",
      len(marked) >= 6 and all(r.icon for r in marked) and
      {"theme", "text_size", "moon", "motion"} <=
      {r.icon for r in marked}, str([r.icon for r in marked]))


def row_of(name):
    return next(r for r in settings_rows(alice.settings_screen.body)
                if r.icon == name)


def control_in(row, kind):
    return next(w for w in row.walk() if isinstance(w, kind))


if M.APPEARANCE.reduce_motion:
    control_in(row_of("motion"), M.ToggleSwitch).flip()
    tick(0.6)
switch = control_in(row_of("motion"), M.ToggleSwitch)
before = row_of("motion").glyph.state
switch.flip()                                  # motion reduced: at once
at_once = row_of("motion").glyph.state
switch.flip()                                  # and back: the ball sets off
# The move begins on the frame after the screen is built again, which a
# busy machine's frame can delay past a fixed wait: wait for it.
wait_for(lambda: row_of("motion").glyph.state < 1.0, 2)
moving = row_of("motion").glyph.state
tick(0.6)
check("a Settings icon shows its setting: reduced, the ball is still at "
      "once; back on, it moves off again",
      before == 0.0 and at_once == 1.0 and 0.0 < moving < 1.0 and
      row_of("motion").glyph.state == 0.0, (before, at_once, moving))
alice._on_keyboard(None, 27)
tick(0.3)
check("settings -> the list", alice.manager.current == "chats")
alice.open_conversation(B)
tick(0.3)
alice.open_chat_info()
tick(0.3)
alice._on_keyboard(None, 27)
tick(0.3)
check("chat info -> the chat", alice.manager.current == "conversation")
alice._on_keyboard(None, 27)
tick(0.3)
check("the chat -> the list", alice.manager.current == "chats")
alice.open_search()
tick(0.2)
alice._on_keyboard(None, 27)
check("search -> the list", not alice.chats_screen.searching)
check("the outermost page: back is taken, the app is not quit",
      alice._on_keyboard(None, 27) is True and
      alice.manager.current == "chats")


def back_key(app=None):
    """Back as Kivy's window delivers it: on_key_down first, and
    on_keyboard only when nothing took that. `app`: the one of this
    suite's two apps that is to hear it (both listen to the one window)."""
    if not M.Window.dispatch("on_key_down", 27, 41, None, []):
        if app is None:
            M.Window.dispatch("on_keyboard", 27, 41, None, [])
        else:
            app._on_keyboard(M.Window, 27)
    tick(0.3)


# 18.1: a phone's keyboard takes Back first, to hide itself; the next
# Back finds the message field still focused.
field = alice.conversation_screen.entry.input
alice.open_conversation(B)
settled()
field.focus = True
tick(0.2)
back_key(alice)
check("a computer's Esc in the message field is the field's: the focus "
      "goes, the chat stays (the check's own ground)",
      not field.focus and alice.manager.current == "conversation")
field.focus = True
tick(0.2)
M.Window.bind(on_key_down=alice._back_past_a_field)      # as on a phone
back_key(alice)
M.Window.unbind(on_key_down=alice._back_past_a_field)
check("a phone's Back there is not spent on the field: the focus goes "
      "and the chat is left, in the one press",
      not field.focus and alice.manager.current == "chats",
      f"focus {field.focus}, page {alice.manager.current}")

# The self-test's Back check (debug builds, on the emulators): a sheet,
# and what the real key did, said once it has arrived.
said = []
M._selftest_back(lambda *words: said.append(words))
tick(0.5)
check("the self-test leaves a sheet open, and says nothing until Back "
      "is pressed", not said and
      isinstance(M.Window.children[0], M.Sheet))
back_key()
tick(0.8)
check("...pressed, it says the key arrived as Back and closed the "
      "sheet",
      [w[:2] for w in said] == [("back", "PASS")] and
      not any(isinstance(w, M.Sheet) for w in M.Window.children), said)
# A slow device draws the sheet's closing in its own time (Devices run
# 26, Android 16: "still open" 0.6 s after the key, which had arrived).
said.clear()
M._selftest_back(lambda *words: said.append(words))
tick(0.5)
sheet = M.Window.children[0]
close = sheet.dismiss
sheet.dismiss = lambda *a, **k: M.Clock.schedule_once(lambda _dt: close(), 1.0)
back_key()                              # 0.3 s pass in it
tick(0.4)
early = list(said)                      # 0.7 s after the key: still open
tick(1.2)
check("a sheet that takes a second to close is waited for, not failed "
      "at 0.6 s", not early and [w[:2] for w in said] == [("back", "PASS")],
      (early, said))
said.clear()
M._selftest_back(lambda *words: said.append(words), wait=0.6)
tick(0.5)
sheet = M.Window.children[0]
close, sheet.dismiss = sheet.dismiss, lambda *a, **k: None
back_key()
tick(1.4)
check("...and one that never closes fails, once, saying so",
      [w[:2] for w in said] == [("back", "FAIL")] and
      "still open" in said[0][2], said)
close()
tick(0.6)
# 21.1: on a phone, then a Back that no view took: the self-test gives the
# activity's own onBackPressed one, which BACK_TO_APP hands to the app as
# the key (here: the key, as that patched handler sends it).
said.clear()
real_activity = M._back_to_activity
M._back_to_activity = lambda: M.Window.dispatch("on_keyboard", 27, 41, None,
                                                [])
M.IS_ANDROID = True
try:
    M._selftest_back(lambda *words: said.append(words))
    tick(0.5)
    back_key()
    tick(2.0)
finally:
    M.IS_ANDROID, M._back_to_activity = False, real_activity
check("21.1: on a phone, then a second sheet, and a Back given to the "
      "activity itself (back-activity) closes it too, not the app",
      [w[:2] for w in said] == [("back", "PASS"), ("back-activity", "PASS")]
      and not any(isinstance(w, M.Sheet) for w in M.Window.children), said)

print("\nSearch")
alice.open_search()
alice.chats_screen.search_field.text = "NEEDLE"
alice.chats_screen._show_results("NEEDLE")
hits = [w for w in alice.chats_screen.list.children
        if isinstance(w, M.SearchHit)]
check("words are found in any case", len(hits) == 1)
alice.chats_screen.search_field.text = "bob"
alice.chats_screen._show_results("bob")
rows = [w for w in alice.chats_screen.list.children
        if isinstance(w, M.PersonRow)]
check("names are found too", len(rows) == 1)
check("% and _ are taken literally",
      alice.store.search_messages("50%") == [] and
      alice.store.search_messages("number_1") == [])
needle = ids[20]
alice.chats_screen.exit_search()
alice.open_conversation_at(B, needle)
tick(0.4)
check("a found message far back opens the chat at it",
      needle in screen._bubbles and alice._chat_ceiling is not None and
      ids[-1] not in screen._bubbles)
# The button fades in on the frames after the window is laid out, and a
# busy machine's frame can outlast tick(0.4) above: wait for it.
check("...with the way back to the end offered",
      screen._newer > 0 and
      wait_for(lambda: screen.jump_button.opacity > 0, 3))
before = len(screen._bubbles)
alice.load_newer_messages()
tick(0.2)
check("scrolling down brings the next messages in below",
      len(screen._bubbles) > before)
screen.jump_to_latest()
tick(0.3)
check("...and the jump button returns to the end, following it again",
      alice._chat_ceiling is None and ids[-1] in screen._bubbles)

print("\nTap a message")
mid = ids[-2]
screen.on_bubble_tap(mid)
check("a tap raises the reactions over it",
      screen._strip_for == mid and not screen.strip.concealed and
      not screen.selected)
screen.on_bubble_tap(mid)
check("...a second tap puts them away",
      screen._strip_for is None and screen.strip.concealed)

print("\nA tapped message's menu (18.2)")


def menu_names():
    return [row.label.text for row in screen.menu.rows]


def press(name):
    next(r for r in screen.menu.rows
         if r.label.text == name).dispatch("on_release")
    tick(0.2)


def inside(part):
    return (screen.stack.x <= part.x and part.right <= screen.stack.right
            and screen.stack.y <= part.y and part.top <= screen.stack.top)


screen.on_bubble_tap(mid)
tick(0.2)
names = menu_names()
check("a tap also says what can be done with it, in words",
      not screen.menu.concealed and names[0] == "Reply" and
      names[-1] == "Delete" and {"Copy", "Forward", "Star", "Select"}
      <= set(names), str(names))
check("...Edit only where the message can still be edited",
      ("Edit" in names) == alice.can_edit(screen._bubbles[mid].msg),
      str(names))
menu, strip = screen.menu, screen.strip
check("...the menu inside the view, clear of the reactions",
      inside(menu) and inside(strip) and
      (menu.top <= strip.y or menu.y >= strip.top),
      (menu.pos, menu.size, strip.pos, strip.size, tuple(screen.stack.pos),
       tuple(screen.stack.size)))
# 19.0: where the menu fits neither under the message nor over it and its
# reactions (Windows' runner: it covered them), laid out for real.


def apart(a, b):
    return a.right <= b.x or b.right <= a.x or a.top <= b.y or b.top <= a.y


def tapped_at(width, height):
    screen.on_bubble_tap(mid)                   # the menu put away
    M.Window.size = (M.dp(width), M.dp(height))
    if top.parent is None:
        M.Window.add_widget(top)
    tick(0.5)
    screen.on_bubble_tap(mid)
    tick(0.2)
    return (menu.pos, menu.size, strip.pos, strip.size,
            tuple(screen.stack.pos), tuple(screen.stack.size))


shape = tapped_at(400, 560)
check("...a short window: the reactions at the view's top, the menu just "
      "under them, both whole", inside(menu) and inside(strip) and
      apart(menu, strip) and strip.top >= screen.stack.top - M.dp(8), shape)
shape = tapped_at(840, 392)
check("...a phone on its side (a menu taller than the view): the reactions "
      "beside it, whole", inside(strip) and apart(menu, strip), shape)
# 20.0 (FIXES27): ...and the menu itself, whose first rows were cut off.
first = menu.rows[0]
check("...and the menu within the view, its first row (Reply) on show, the "
      "rest reached by scrolling (it was taller than the view: Reply and "
      "Copy were cut off)",
      inside(menu) and menu.height < menu.natural and
      menu.scroll.scroll_y == 1 and
      first.to_window(0, first.top)[1] <= menu.top + 1 and
      menu.list.height > menu.scroll.height,
      (shape, menu.natural, first.to_window(0, first.top)))
menu.scroll.scroll_y = 0
tick(0.1)
last = menu.rows[-1]
check("...scrolled to its end, its last row (Delete) is on show",
      last.label.text == "Delete" and
      last.to_window(0, last.y)[1] >= menu.y - 1,
      (last.to_window(0, last.y), menu.y))
M.Window.size = (M.dp(400), M.dp(720))
tick(0.5)                       # laid out again at the test's own size
M.Window.remove_widget(top)
screen.on_bubble_tap(mid)
check("...and a second tap puts both away",
      screen.menu.concealed and screen.strip.concealed)
copied = []
alice.copy_text = copied.append
screen.on_bubble_tap(mid)
press("Copy")
check("Copy takes its words, and the menu goes",
      copied == [screen._bubbles[mid].msg["body"]] and
      screen.menu.concealed and not screen.selected, str(copied))
screen.on_bubble_tap(mid)
press("Reply")
check("Reply begins a reply to it",
      screen.reply_to is not None and
      screen.reply_to.get("cid") ==
      screen._bubbles[mid].msg["client_msg_id"] and not screen.selected)

print("\nThe reply bar's options (18.2)")


def rows_of(sheet):
    """Its rows' words, from the top one down."""
    found, queue = [], [sheet]
    while queue:
        w = queue.pop()
        if isinstance(w, M.ListRow):
            found.append(w)
        queue.extend(w.children)
    return [w.title.text for w in
            sorted(found, key=lambda w: -w.to_window(0, w.y)[1])]


def top_sheet():
    return next((w for w in M.Window.children if isinstance(w, M.Sheet)),
                None)


words = screen._bubbles[mid].msg["body"]            # "message number 128"
screen.reply_options()
tick(0.3)
check("a tap on what is replied to offers: a part of it, another chat, "
      "the message, or to give it up",
      rows_of(top_sheet()) == ["Quote a part of it", "Reply in another chat",
                               "Show the message", "Cancel the reply"],
      str(rows_of(top_sheet())))
top_sheet().dismiss(animation=False)
tick(0.2)
quoted = []
sheet = M.QuoteSheet(words, quoted.append)
sheet.open(animation=False)
tick(0.2)
check("the part to quote is marked in the message's own words; nothing "
      "marked, nothing to quote",
      sheet.field.text == words and sheet.button.disabled)
sheet.field.select_text(8, 14)
tick(0.2)
sheet.button.dispatch("on_release")
tick(0.2)
check("...the marked words are what is quoted", quoted == ["number"],
      str(quoted))
actions = screen.message_actions(screen._bubbles[mid].msg)
names = [text for _i, text, *_r in actions]
check("21.0 (SELECT29): a message's menu offers Select text, after Copy",
      "Select text" in names and
      names.index("Select text") == names.index("Copy") + 1, names)
marked, real_copy = [], alice.copy_text
alice.copy_text = marked.append
next(run for _i, text, run, *_r in actions if text == "Select text")()
tick(0.3)
sheet = top_sheet()
check("…which opens the message's words in the sheet a quote uses, its "
      "button Copy the marked words, off until words are marked",
      isinstance(sheet, M.QuoteSheet) and sheet.field.text == words and
      sheet.button.text.lower() == "copy the marked words" and
      sheet.button.disabled,
      (type(sheet).__name__, getattr(getattr(sheet, "button", None),
                                     "text", None)))
sheet.field.select_text(8, 14)
tick(0.2)
sheet.button.dispatch("on_release")
tick(0.3)
check("…and copies exactly the marked words", marked == ["number"], marked)
alice.copy_text = real_copy

print("\n21.0 (POLL29): a poll in the chat")
poll = {"q": "Which day?", "o": ["Friday", "Saturday", "Sunday"],
        "m": False, "u": 0}
alice.send_poll(B, poll)
tick(0.6)
alice.refresh_chat(B)
tick(0.3)
row = next(m for m in alice.store.messages_for(B)
           if (M.message_ext(m).get("poll") or {}).get("q") == "Which day?")
card = screen._bubbles[row["id"]].card
check("its bubble is the poll: the question, a row for each choice, no "
      "votes yet; not its words for older apps",
      isinstance(card, M.PollCard) and card.question.text == "Which day?"
      and [c.label.text for c in card.choices] == poll["o"] and
      card.footer.text.startswith("0 votes") and
      screen._bubbles[row["id"]].body is None,
      (type(card).__name__, getattr(card, "footer", None) and
       card.footer.text))


rings = [(c.ring_at[0], c.mark.center_x, c.x) for c in card.choices]
check("...each choice's ring drawn on its mark, inside its row (it was "
      "drawn at the screen's edge, before the row had placed its mark)",
      all(abs(r - mark) < 1 and r > x for r, mark, x in rings), rings)
card.choices[1].dispatch("on_release")
tick(0.6)
cid = row["client_msg_id"]
card = screen._bubbles[row["id"]].card
check("a tap on a choice votes: counted, marked, its bar full",
      alice.store.poll_votes(B, cid) == {A: [1]} and
      card.choices[1].picked and card.choices[1].share == 1.0 and
      card.choices[1].number.text == "1" and
      card.footer.text.startswith("1 vote"),
      (alice.store.poll_votes(B, cid), card.footer.text))
card.choices[1].dispatch("on_release")
tick(0.6)
check("…a tap on yours again takes the vote back",
      alice.store.poll_votes(B, cid) == {})
card = screen._bubbles[row["id"]].card
card.choices[2].dispatch("on_release")
tick(0.6)
names = [text for _i, text, *_r in screen.message_actions(
    alice.store.message_by_cid(B, cid))]
check("its menu: who voted, and End the poll for its asker",
      "Votes" in names and "End the poll" in names, names)
alice.end_poll(B, cid)
tick(0.6)
card = screen._bubbles[row["id"]].card
check("ended: it says so, a tap on a choice votes no more",
      not card.live and "Ended" in card.footer.text and
      (card.choices[0].dispatch("on_release") or True) and
      alice.store.poll_votes(B, cid) == {A: [2]} and "End the poll" not in [
          text for _i, text, *_r in screen.message_actions(
              alice.store.message_by_cid(B, cid))], card.footer.text)
tick(0.5)
relay.mail.clear()                # the poll and its votes, sent to no one

print("\n21.0 (MAP29): a place in the chat")


class FakePlace:
    """Where a phone is, as the suites give it (M.LOCATION_SOURCE)."""

    def __init__(self, point=None):
        self.point, self.followers = point, []

    def last(self):
        return self.point

    def follow(self, on_point):
        self.followers.append(on_point)

    def unfollow(self, on_point):
        self.followers.remove(on_point)

    def move(self, point):
        self.point = point
        for on_point in list(self.followers):
            on_point(point)


def texts_in(widget):
    """Its buttons' words (a button shows them in capitals)."""
    return [w.text.lower() for w in widget.walk() if isinstance(w, M.Btn)]


def newest_place():
    row = [m for m in alice.store.messages_for(B)
           if M.message_ext(m).get("loc")][-1]
    return row, screen._bubbles[row["id"]].card


spot = {"lat": 51.5007, "lon": -0.1246, "acc": 12, "at": int(time.time())}
sheet = M.LocationSheet(alice, B, live=True)
sheet.open(animation=False)
tick(0.2)
check("on a computer, the place is typed: Send this place is off until "
      "it is a place, and nothing is shared live",
      sheet.field is not None and sheet.send.disabled and
      sheet.live is None)
sheet.field.text = "the lake"
tick(0.1)
off = sheet.send.disabled
sheet.field.text = "51.5007, -0.1246"
tick(0.1)
check("…a place's coordinates turn it on", off and not sheet.send.disabled)
sheet.send.dispatch("on_release")
tick(0.6)
alice.refresh_chat(B)
tick(0.3)
row, card = newest_place()
marked, real_copy = [], alice.copy_text
alice.copy_text = marked.append
check("sent: its bubble is the place (a pin, Location, its coordinates), "
      "not its words for older apps; a computer copies its coordinates",
      isinstance(card, M.LocationCard) and card.title.text == "Location" and
      "51.50070, -0.12460" in card.words.text and
      screen._bubbles[row["id"]].body is None and
      texts_in(card.buttons) == ["copy the coordinates"] and
      (card.buttons.children[0].dispatch("on_release") or True) and
      marked == ["51.500700, -0.124600"],
      (type(card).__name__, getattr(getattr(card, "words", None), "text",
                                    None), marked))
names = [text for _i, text, *_r in screen.message_actions(row)]
check("…its menu: Copy the coordinates, not the words kept for older apps",
      "Copy the coordinates" in names and "Open in maps" not in names and
      "Copy" not in names and "Select text" not in names, names)
M.OPENS_MAPS = True                                 # as on a phone
opened, real_open = [], alice.open_place
alice.open_place = opened.append
card = M.LocationCard(row, spot, screen)
card.buttons.children[0].dispatch("on_release")
actions = screen.message_actions(row)
check("on a phone, Open in maps hands its point to the phone's maps app, "
      "in the card and the menu",
      texts_in(card.buttons) == ["open in maps"] and len(opened) == 1 and
      (opened[0]["lat"], opened[0]["lon"]) == (51.5007, -0.1246) and
      "Open in maps" in [text for _i, text, *_r in actions], opened)
alice.copy_text = real_copy
alice._here = ({"lat": 51.5007, "lon": -0.1246 + 0.03, "acc": 20,
                "at": int(time.time())}, time.monotonic())
theirs = M.LocationCard({"peer": B, "client_msg_id": "c" * 32,
                         "direction": "in"}, spot, screen)
check("a place sent to you says how far it is and which way, where this "
      "device knows where it is",
      theirs.words.text.splitlines()[0] == "2.1 km west of you",
      theirs.words.text)
alice._here = (None, -1e9)
M.LOCATION_SOURCE = FakePlace()
real_battery, M.battery_percent = M.battery_percent, lambda: 10
sheet = M.LocationSheet(alice, B, live=True)
M.battery_percent = real_battery
sheet.open(animation=False)
tick(0.2)
said = [w.text for w in sheet.body.walk() if isinstance(w, M.InfoLabel)]
check("on a phone with little battery left, the sheet says a long share "
      "may stop before its time",
      "Your battery is at 10%: a long share may stop before its time." in
      said, said)
finding = (sheet.where.text, sheet.send.disabled, sheet.live.disabled)
M.LOCATION_SOURCE.move(spot)
tick(0.1)
check("on a phone it finds where it is: until then nothing can be sent; "
      "then it says where",
      finding == ("Finding where you are…", True, True) and
      sheet.where.text == "Here: 51.50070, -0.12460 (±12 m)" and
      not sheet.send.disabled and not sheet.live.disabled,
      (finding, sheet.where.text))
spans = [seg.caps.text.lower() for seg in sheet.span.segments.values()]
check("…it offers a live share for 15 minutes, an hour or 8 hours",
      spans == ["15 minutes", "1 hour", "8 hours"] and
      sheet.span.value == "15", spans)
sheet.live.dispatch("on_release")
tick(0.6)
alice.refresh_chat(B)
tick(0.3)
row, card = newest_place()
check("shared live: Live location, until when, Stop sharing on yours; "
      "the sheet no longer follows the phone",
      card.title.text == "Live location" and card.running and
      "Live until " in card.words.text and
      texts_in(card.buttons) == ["open in maps", "stop sharing"] and
      M.LOCATION_SOURCE.followers == [alice._on_live_point] and
      alice._live_redraw is not None,
      (card.words.text, texts_in(card.buttons)))
next(b for b in card.buttons.children
     if b.text.lower() == "stop sharing").dispatch("on_release")
tick(0.6)
row, card = newest_place()
check("…stopped: it says so, keeps no point, offers nothing",
      not card.running and card.point is None and
      card.words.text == "Live location ended" and
      not card.buttons.children and not M.LOCATION_SOURCE.followers,
      card.words.text)
alice.open_place = real_open
M.LOCATION_SOURCE, M.OPENS_MAPS = None, False
tick(0.5)
relay.mail.clear()                # the places, sent to no one
screen._quote_part("  number \n 128 ")
check("the reply then quotes that part alone, of the same message",
      screen.reply_to["snip"] == "number 128" and
      screen.reply_to["cid"] == screen._bubbles[mid].msg["client_msg_id"]
      and "number 128" in screen.reply_snip.text, str(screen.reply_to))
screen._reply_elsewhere()
tick(0.3)
sheet = top_sheet()
check("another chat is chosen from yours, this one left out",
      isinstance(sheet, M.ChatPickSheet) and B not in sheet.peers and
      A in sheet.peers, str(getattr(sheet, "peers", None)))
sheet.take(A)
tick(0.6)
carried = screen.take_reply()
check("...and the reply goes there with its quote",
      alice.current_peer == A and carried is not None and
      carried["snip"] == "number 128" and carried["from"] == A and
      carried["cid"] == alice.store.message(mid)["client_msg_id"],
      (alice.current_peer == A, carried))
alice.go_back_to_chats()
tick(0.3)
alice.open_conversation(B)
tick(0.4)
wait_for(lambda: mid in screen._bubbles, 5)
screen.start_edit(screen._bubbles[mid].msg)
before = top_sheet()
screen.reply_options()
tick(0.2)
check("a message being edited is not a reply: the bar offers nothing",
      top_sheet() is before)
screen.cancel_reply()
screen.entry.text = ""
screen.on_bubble_tap(mid)
press("Select")
check("Select holds it, for more to be chosen",
      list(screen.selected) == [mid] and screen.menu.concealed and
      screen.select_bar.parent is not None)
screen.clear_selection()
screen.on_bubble_tap(mid)
press("Delete")
sheet = M.Window.children[0]
check("Delete asks first: for me, or for everyone",
      isinstance(sheet, M.DeleteModal) and
      alice.store.message(mid) is not None)
sheet.dismiss(animation=False)
screen.clear_selection()
tick(0.2)
alice.store.add_in(
    B, "see https://example.org/a and the rest",
    (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), 9001,
    uuid.uuid4().hex, unread=False)
linked = alice.store.messages_for(B)[-1]["id"]
alice.refresh_chat(B)
wait_for(lambda: linked in screen._bubbles, 5)
screen.on_bubble_tap(linked)
check("a message with a link offers the link alone",
      "Copy the link" in menu_names(), str(menu_names()))
press("Copy the link")
check("...and copies it", copied[-1] == "https://example.org/a",
      str(copied[-1:]))
screen.on_bubble_tap(linked)
tick(0.2)
check("the newest message, with nothing under it: the menu sits over it "
      "and its reactions, still inside the view",
      inside(screen.menu) and screen.menu.y >= screen.strip.top,
      (screen.menu.pos, screen.strip.pos))
screen.close_strip()
alice.go_back_to_chats()
tick(0.3)

print("\nA held chat")
cara_priv, *_ = M.mine_identity()
C = M.address_from_priv(cara_priv)
alice.store.upsert_contact(C, "cara", M.pub_to_compressed(
    M.priv_to_pub(cara_priv)).hex())
alice.store.add_in(C, "hi from cara", M.now_iso(), 9000, uuid.uuid4().hex)
alice.refresh_sidebar()
lst = alice.chats_screen
convs = {c["peer"]: c for c in alice.store.conversations(me=A)}
check("one unmuted chat offers Mute",
      lst.mute_choice([convs[B]]) is True)
alice.set_mute([B], M.LocalStore.MUTE_FOREVER)
convs = {c["peer"]: c for c in alice.store.conversations(me=A)}
check("...one muted, Unmute", lst.mute_choice([convs[B]]) is False)
check("several, half muted: Mute all",
      lst.mute_choice([convs[B], convs[C]]) is True)
alice.set_mute([C], M.LocalStore.MUTE_FOREVER)
convs = {c["peer"]: c for c in alice.store.conversations(me=A)}
check("...all muted: Unmute all",
      lst.mute_choice([convs[B], convs[C]]) is False)
lst.start_selection(B)
lst.toggle_selection(C)
check("holding chats selects them, and the head turns into their actions",
      lst.selected == [B, C] and lst.select_head.parent is lst.head_box and
      lst.fab_holder.parent is None)
lst.clear_selection()
check("...cleared, the list is as it was",
      lst.head.parent is lst.head_box and lst.fab_holder.parent is not None)
alice.set_chats_pinned([C], True)
order = [p for p in lst._rows]
check("a pinned chat goes to the top (after saved messages)",
      order[:2] == [A, C], str(order[:3]))
alice.set_chats_archived([C], True)
order = [p for p in lst._rows]
arch = [w for w in lst.list.children if isinstance(w, M.ArchiveRow)]
check("archived, it leaves the list for the archive row",
      C not in order and len(arch) == 1)
lst.show_archive(True)
check("...where it is found", list(lst._rows) == [C])
lst.show_archive(False)
alice.set_chats_archived([C], False)
alice.mark_chats_unread([B])
convs = {c["peer"]: c for c in alice.store.conversations(me=A)}
check("marked unread by hand", convs[B]["marked_unread"] == 1)
alice.open_conversation(B)
alice.go_back_to_chats()
convs = {c["peer"]: c for c in alice.store.conversations(me=A)}
check("...and opening it clears the mark", convs[B]["marked_unread"] == 0)

print("\nBlocking")
bob.set_blocked([A], True)
alice.current_peer = B
alice.send_text(B, "are you there?")
wait_for(lambda: relay.mail)
before = len(bob.store.messages_for(A))
acked = deliver(relay, parties)
check("a blocked contact's message is held unseen, and acknowledged",
      len(bob.store.messages_for(A)) == before and acked and acked[0] and
      bob.store.held_count(A) == 1)
bob.open_conversation(A)
check("...their chat offers Unblock instead of the composer",
      bob.conversation_screen.blocked_note.parent is not None)
bob.set_blocked([A], False)
check("...unblocked, the composer is back",
      bob.conversation_screen.blocked_note.parent is None and
      bob.conversation_screen.composer.children)
check("...and what they sent meanwhile arrives",
      wait_for(lambda: bob.store.held_count(A) == 0 and
               bob.store.messages_for(A)[-1]["body"] == "are you there?"))
alice.send_text(B, "now?")
wait_for(lambda: relay.mail)
deliver(relay, parties)
check("...and their messages arrive again",
      bob.store.messages_for(A)[-1]["body"] == "now?")
bob.go_back_to_chats()

print("\nDeleting")
alice.delete_chats([C])
convs = {c["peer"]: c for c in alice.store.conversations(me=A)}
check("a deleted chat's messages are gone, and so is its row",
      not alice.store.messages_for(C) and C not in convs)
alice.store.add_in(C, "me again", M.now_iso(), 9001, uuid.uuid4().hex)
convs = {c["peer"]: c for c in alice.store.conversations(me=A)}
check("...until something new arrives", C in convs)

print("\nA long chat list (18.1): its first screenful at once, the rest after")
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
now = datetime.now(timezone.utc)
for i in range(70):
    peer = "Fec" + "".join(B58[(i * 7 + j * 13) % 58] for j in range(31))
    alice.store.upsert_contact(peer, f"person{i:02d}", "02" + "ab" * 32)
    alice.store.add_in(peer, f"hello {i}", (now - timedelta(
        minutes=i)).isoformat(), 70000 + i, uuid.uuid4().hex, unread=False)
alice.go_back_to_chats()
tick(0.3)
chats = alice.chats_screen


def listed():
    return [w.peer for w in reversed(chats.list.children)
            if isinstance(w, M.ConversationRow)]


def afresh():
    """The list built again from nothing, as at start."""
    chats._rows = {}
    alice.refresh_sidebar()


first_paint = M.LIST_FIRST_PAINT
M.LIST_FIRST_PAINT = 10_000                 # every row at once, as before
afresh()
want = listed()
M.LIST_FIRST_PAINT = first_paint
afresh()
first = len(listed())
whole = wait_for(lambda: len(listed()) == len(want), 5)
check("74 chats: the first screenful drawn at once, the rest filled in "
      "after it, in the same order", first == M.LIST_FIRST_PAINT and whole
      and listed() == want, f"{first} at once, then {len(listed())}")
afresh()
tick(0.05)
alice.refresh_sidebar()                     # again, while it fills
wait_for(lambda: len(listed()) >= len(want), 5)
tick(0.2)
check("...a refresh while it fills starts over, nothing twice",
      listed() == want, f"{len(listed())} rows")
afresh()
alice.open_conversation(B)
tick(1.0)
away = len(listed())
alice.go_back_to_chats()
wait_for(lambda: len(listed()) == len(want), 5)
check("...out of sight it stops (a chunk at most once the chat opens), "
      "and is finished when the list shows again",
      away <= M.LIST_FIRST_PAINT + M.LIST_CHUNK and listed() == want,
      f"{away} while away, then {len(listed())}")
kept, moved = list(chats.list.children), []
for row in kept:
    row.bind(parent=lambda w, _v: moved.append(w))
alice.refresh_sidebar()
check("...and a list whose chats are as they were is left as it is: its "
      "rows are not taken off and put back", not moved and
      all(a is b for a, b in zip(kept, chats.list.children)),
      f"{len(moved)} moved")

print("\nA lone arrival is shown on the next frame (18.2)")
shown, real_status = [], alice._set_status
alice._set_status = lambda text, *a, **k: shown.append(text)
tick(M.UI_TICK + 0.1)
alice.ui_queue.put(("status", "one", M.T.ok))
Clock.tick()
check("a worker's event is taken on the next frame, not at the next tick",
      shown == ["one"], shown)
# "Taken a moment ago" is held there, however slow the machine: with four
# suites at once more than a tick went by between two lines here, the
# frame took the two, rightly, and this failed (round 25's last run). The
# wake is called as the next frame calls it, but alone: on a loaded
# machine a frame came after a whole tick, and the tick's own drain took
# them, rightly again (19.0, VERIFY26).
alice._drained_at = time.monotonic() + 60
alice.ui_queue.put(("status", "two", M.T.ok))
alice.ui_queue.put(("status", "three", M.T.ok))
alice._drain_soon(0)
check("...those that follow within a tick wait for it, and are taken "
      "together (a backlog is one repaint a tick, as before)",
      shown == ["one"], shown)
alice._drain_ui_queue(0)
check("...by the tick", shown == ["one", "two", "three"], shown)
alice._set_status = real_status

print("\nThe collector's long look waits for a quiet moment (18.2)")
import gc                                                     # noqa: E402
import weakref                                                # noqa: E402
before = gc.get_threshold()
# What Python itself set aside as it started: none, or a few hundred
# (Ubuntu 24.04's Python 3.12: Devices run 32).
aside = gc.get_freeze_count()
sounding = [False]
quiet = M.QuietCollector(lambda: sounding[0])
check("Python no longer takes the long look when it chooses; the short "
      "ones stay its own", gc.get_freeze_count() == aside and
      gc.get_threshold() == (before[0], before[1], M.QuietCollector.NEVER),
      (aside, gc.get_freeze_count(), gc.get_threshold()))
check("the first quiet moment looks through what start-up made, and sets "
      "it aside: later looks go through what has been made since",
      quiet.check(now=quiet.touched + 1) == "" and
      quiet.check(now=quiet.touched + quiet.QUIET + 1) == "deep" and
      gc.get_freeze_count() > 10000, gc.get_freeze_count())


def churn(looks=3):
    """Objects made and dropped, in rings, until `looks` short looks
    have gone by since the last long one."""
    kept = []
    while gc.get_count()[2] < looks:
        ring = []
        ring.append(ring)
        kept.append(ring)
        if len(kept) > 2000:
            kept.clear()


churn()
at = quiet.touched
check("a hand at work is not interrupted: no long look while it touches",
      quiet.check(now=at + quiet.QUIET - 0.5) == "" and
      quiet.check(now=at + quiet.EVERY + 60) == "long" and
      gc.get_count()[2] == 0)
churn()
sounding[0] = True
check("...nor while something sounds (a call, a message playing or "
      "being recorded), however quiet the hand",
      quiet.check(now=time.monotonic() + quiet.EVERY + 60) == "")
sounding[0] = False
M.Window.dispatch("on_key_down", 97, 0, "a", [])
check("...and a key is a hand too",
      quiet.check(now=time.monotonic() + quiet.QUIET - 1) == "" and
      quiet.check(now=time.monotonic() + quiet.EVERY + 60) == "long")
churn()
quiet.touched -= 60
M.Window.dispatch("on_textinput", "a")
check("...as is a letter from a phone's keyboard, which sends no key",
      quiet.check(now=time.monotonic() + quiet.QUIET - 1) == "")
quiet.look(deep=False)
churn()
real_pending, quiet.PENDING = quiet.PENDING, 3
quiet.touched = time.monotonic()
check("a hand that never rests gets one all the same once enough short "
      "looks have gone by: memory cannot grow without end",
      quiet.check(now=quiet.touched + 0.1) == "long")
quiet.PENDING = real_pending
quiet.owes_deep = True
frozen = gc.get_freeze_count()
check("a deep look, through start-up's objects too, when one is owed (a "
      "theme built every page again), at a quiet moment; then they are "
      "set aside again",
      quiet.check(now=time.monotonic() + 1) == "" and
      quiet.check(now=time.monotonic() + quiet.QUIET + 1) == "deep" and
      not quiet.owes_deep and gc.get_freeze_count() > 10000, frozen)
alice.collector = quiet
gc.disable()


class Made:
    """Something a rebuild made and dropped: it holds itself."""


made = Made()
made.me = made
dropped = weakref.ref(made)
del made
alice._collect_after_rebuild(0)
check("a theme's rebuild no longer takes the long look a moment after it "
      "shows: it owes a deep one to the next quiet moment",
      gc.isenabled() and quiet.owes_deep)
check("...but what the rebuild itself made is looked through there and "
      "then, while the screen is still, not at the next touch",
      dropped() is None and gc.get_count()[1] == 0, gc.get_count())
sounding[0] = True
real_sounding, alice.sounding = alice.sounding, lambda: sounding[0]
alice._went_away()
owed = quiet.owes_deep
sounding[0] = False
alice._went_away()
check("the app leaving the screen is a quiet moment, unless a call goes "
      "on behind it", owed and not quiet.owes_deep)
alice.sounding, alice.collector = real_sounding, None
quiet.stop()
check("stopped, the collector is Python's own again",
      gc.get_threshold() == before and gc.get_freeze_count() == 0)

print("\nAn error while a screen is drawn (18.2): the app carries on")
net = next(h for h in M.ExceptionManager.handlers
           if isinstance(h, M.CrashNet))
M.CrashNet.install()
check("the net is put up when an app is built, once however many are",
      sum(isinstance(h, M.CrashNet)
          for h in M.ExceptionManager.handlers) == 1)
M.App._running_app = alice               # the app this window is
alice.open_conversation(B)
tick(0.3)
alice.open_contact_settings()
tick(0.3)


def raised(_dt):
    raise ValueError("a screen's own fault")


Clock.schedule_once(raised, 0)
escaped = None
try:
    Clock.tick()                # a frame: Kivy's clock asks the net itself
except ValueError as exc:
    escaped = exc
said = M.Toast.current.text if M.Toast.current else ""
check("an error in a frame is passed over, and said",
      escaped is None and len(net._recent) == 1 and "went wrong" in said,
      (escaped, said))
check("...the page and its sheet left as they were: one error is not a "
      "broken screen", alice.manager.current == "conversation" and
      any(isinstance(w, M.Sheet) for w in M.Window.children))
verdict = M.ExceptionManager.handle_exception(ValueError("and again"))
tick(0.4)
check("a second within seconds closes the sheets and shows the chat list",
      verdict == M.ExceptionManager.PASS and
      alice.manager.current == "chats" and
      not any(isinstance(w, M.Sheet) for w in M.Window.children),
      alice.manager.current)
verdicts = [M.ExceptionManager.handle_exception(ValueError(str(n)))
            for n in range(M.CrashNet.STORM)]
check("an error every frame is not a working app: it ends, as before",
      verdicts[-1] == M.ExceptionManager.RAISE and
      verdicts[0] == M.ExceptionManager.PASS, str(verdicts))
net._recent.clear()
check("Ctrl+C and a wanted exit are never passed over",
      M.ExceptionManager.handle_exception(KeyboardInterrupt()) ==
      M.ExceptionManager.RAISE and
      M.ExceptionManager.handle_exception(SystemExit()) ==
      M.ExceptionManager.RAISE)
net._recent.clear()
tick(0.3)

print("\nPinned messages: the bar and the list (19.0)")
while alice.manager.current != "chats":
    alice._on_keyboard(None, 27)
    settled()
dora = [m for m in alice.store.messages_for(D) if m.get("kind") != "event"]
picked = [dora[2], dora[10], dora[20]]
for n, msg in enumerate(picked):
    alice.store.set_pin(D, msg["client_msg_id"], D,
                        f"2026-11-20T10:00:0{n}+00:00", True)
alice.open_conversation(D)
settled()
pbar = alice.conversation_screen.pin_bar
check("a chat with pins shows the bar: the newest first, 3 of 3",
      pbar.opacity == 1 and pbar.title.text == "Pinned · 3 of 3" and
      pbar.line.text == M.pin_line(picked[-1]), (pbar.opacity,
                                                pbar.title.text))
shown = []
real_jump = alice.conversation_screen.jump_to_cid
alice.conversation_screen.pin_bar.on_show = shown.append
for _ in range(3):
    pbar.step()
check("each tap shows its message and walks back; after the oldest, the "
      "newest again", shown == [m["client_msg_id"] for m in
                               reversed(picked)] and
      pbar.title.text == "Pinned · 3 of 3", (shown, pbar.title.text))
pbar.on_show = real_jump
menu = [text for _i, text, *_r in alice.conversation_screen.message_actions(
    dict(picked[0], reactions=[]))]
check("a pinned message's menu offers Unpin; another's offers Pin",
      "Unpin" in menu and "Pin" in [text for _i, text, *_r in
                                    alice.conversation_screen.message_actions(
                                        dict(dora[5], reactions=[]))])
sheet = M.PinsSheet(alice, D)
rows = [w for w in sheet.walk() if isinstance(w, M.ListRow)]
check("the list: every pin, newest first", len(rows) == 3)
sheet._unpin(picked[-1]["client_msg_id"])
tick(0.2)
rows = [w for w in sheet.walk() if isinstance(w, M.ListRow)]
check("Unpin in the list: two left, the bar says so",
      len(rows) == 2 and pbar.title.text == "Pinned · 2 of 2",
      (len(rows), pbar.title.text))
sheet._unpin_all()
tick(0.3)
check("Unpin all: the bar goes with the last pin",
      pbar.opacity == 0 and alice.store.pins(D) == [])
alice.go_back_to_chats()
settled()

print("\nTwo panes on a wide window (19.0)")


def window_at(width, height):
    """The window at this size, and the panes laid out for it. On Windows
    Kivy takes a size the app sets over the next frames (the window, then
    its root), so this waits for the panes rather than a fixed time."""
    M.Window.size = (M.dp(width), M.dp(height))
    wait_for(lambda: alice.chats_screen.docked ==
             (width >= M.TWO_PANES_FROM), 3)
    tick(0.1)


while alice.manager.current != "chats":
    alice._on_keyboard(None, 27)
    settled()
chats = alice.chats_screen
if top.parent is None:                  # laid out for real, as above
    M.Window.add_widget(top)
check("narrow: one column as before, the list in its own screen",
      not chats.docked and chats._root.parent is chats and
      alice.side.width == 0)
window_at(1180, 760)
check("wide: the list docked at the left, the right side says Choose a chat",
      chats.docked and chats._root.parent is alice.side and
      alice.side.width == M.dp(M.SIDE_WIDTH) and
      alice.manager.current == "chats" and
      chats._choose_page.parent is chats,
      (chats.docked, alice.side.width, tuple(M.Window.size), M.dp(1)))
alice.open_conversation(B)
settled()
check("a chat opened: the list and the chat both show, its row marked",
      alice.manager.current == "conversation" and
      chats._root.get_root_window() is not None and
      chats._rows[B][1]._open)
alice.open_conversation(D)
settled()
check("another chat: the right side changes, the list stays, the mark moves",
      alice.current_peer == D and chats._rows[D][1]._open and
      not chats._rows[B][1]._open and chats._root.parent is alice.side)
before = chats._rows[B][1]
alice.store.add_in(B, "while two panes show", datetime.now(timezone.utc)
                   .isoformat(), 9900, uuid.uuid4().hex)
alice.refresh_sidebar()
tick(0.2)
check("a message for another chat while one is open: the list repaints at "
      "once (not left stale)", not getattr(alice, "_list_stale", False) and
      chats._rows[B][1] is not before)
alice.open_settings()
settled()
check("Settings opens on the right, the list stays",
      alice.manager.current == "settings" and
      chats._root.parent is alice.side)
alice._on_keyboard(None, 27)
settled()
alice.open_conversation(D)
settled()
alice._on_keyboard(None, 27)
settled()
check("Esc closes what is on the right, down to Choose a chat",
      alice.manager.current == "chats" and alice.current_peer is None and
      not chats._rows[D][1]._open)
alice.open_conversation(D)
settled()
window_at(400, 720)
check("narrowed with a chat open: one column, the chat still open",
      not chats.docked and alice.manager.current == "conversation" and
      alice.current_peer == D and chats._root.parent is chats)
window_at(1180, 760)
check("widened again: docked again, the chat still open beside it",
      chats.docked and alice.manager.current == "conversation" and
      chats._rows[D][1]._open)
tick(1.2)                               # kept once it settles
kept = M.load_prefs().get("window") or {}
check("the window's size is kept for the next start",
      kept.get("w") == int(M.Window.size[0]))
alice.go_back_to_chats()
window_at(400, 720)

print("\nA theme changed in Settings (last: it builds every screen again)")
alice.open_settings()
tick(0.3)
was = M.APPEARANCE.mode
start = row_of("theme").glyph.state
other = "light" if was != "light" else "dark"
control_in(row_of("theme"), M.Segmented).choose(other)
seen = []
for _ in range(60):                         # each frame of the move
    Clock.tick()
    time.sleep(0.01)
    seen.append(row_of("theme").glyph.state)
done = seen[-1]
check("the theme's disc moves to the new choice, though Settings is built "
      "again for it", any(0.0 < abs(v - start) < abs(done - start)
                          for v in seen)
      and done == {"light": 0.0, "dark": 1.0}[other] and
      alice.manager.current == "settings", (start, seen[:8], done))
window_at(1180, 760)
alice.set_appearance(theme=was if was in M.THEMES else "light")
tick(0.5)
check("a theme changed with two panes: the new list docked in the old "
      "one's place, alone", alice.chats_screen.docked and
      alice.chats_screen._root.parent is alice.side and
      len(alice.side.children) == 1 and alice.manager.current == "settings")

for app in (alice, bob):
    app.store.close()
passed = sum(1 for _n, c in RESULTS if c)
print(f"\n  {passed}/{len(RESULTS)} passed")
os._exit(0 if passed == len(RESULTS) else 1)
