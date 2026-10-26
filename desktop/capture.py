#!/usr/bin/env python3
"""Screenshots of the real app, to look at a change before and after.

    xvfb-run -a -s "-screen 0 1280x1024x24" \\
        python3 desktop/capture.py main.py shots/after [--theme light]

It fills a fresh profile with the same chats on every run (people, a group,
chats in Chinese, message requests, messages waiting to send), starts the app with the network
stubbed out, and walks it through SCREENS, saving each one as
OUTDIR/<theme>_<screen>.png at 392 x 840 dp, a common phone size. Each
screen is also checked for layout problems: text that overflows or is cut
off, parts that escape their box, and touch targets under 40 dp. Any problem
is printed, and the exit code is then 1.

To compare a change, capture the version before it from a git worktree. A
worktree has its own data/ beside its main.py, so it draws in its own fonts:

    git worktree add ../before HEAD~1
    xvfb-run -a python3 desktop/capture.py ../before/main.py shots/before
    xvfb-run -a python3 desktop/capture.py main.py shots/after
    git worktree remove ../before

An older version that lacks a screen is captured showing whatever it shows
at that step.
"""
import argparse
import os
import random
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

#: Every screen, in the order they are captured.
SCREENS = ("chats", "menu", "chat", "chat_info", "group", "emoji", "unread",
           "notes", "chinese", "chinese_typing", "chinese_group", "requests",
           "request", "report", "outbox", "large_group", "mobile_data",
           "settings", "settings_privacy", "settings_downloads", "blocked")

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("client", help="the main.py to capture")
parser.add_argument("outdir", help="where the PNGs go")
parser.add_argument("--theme", choices=("black", "dark", "light"),
                    default="black", help="black is dark with the black "
                    "background, the app's default on a dark phone")
parser.add_argument("--only", default="", help="comma-separated screens")
ARGS = parser.parse_args()
ONLY = set(filter(None, ARGS.only.split(",")))
os.makedirs(ARGS.outdir, exist_ok=True)

os.environ.update(KIVY_NO_ARGS="1", KIVY_NO_CONSOLELOG="1",
                  SDL_VIDEODRIVER="x11", KIVY_METRICS_DENSITY="2",
                  FEXT_THEME="light" if ARGS.theme == "light" else "dark",
                  FEXT_SERVER_HOST="127.0.0.1:1",
                  # Settings as a phone shows them.
                  FEXT_SHOW_ANDROID_SETTINGS="1")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-capture-")
os.makedirs(os.path.join(os.environ["HOME"], ".fext"))
with open(os.path.join(os.environ["HOME"], ".fext", "prefs.json"), "w") as f:
    f.write('{"black": %s}' % ("true" if ARGS.theme == "black" else "false"))

import importlib.util                                           # noqa: E402
import logging                                                  # noqa: E402
# Only the problems: the app's log and Kivy's start-up notes stay quiet
# (xvfb-run mixes them into the output).
logging.disable(logging.CRITICAL)
_spec = importlib.util.spec_from_file_location("fext_capture", ARGS.client)
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_capture"] = M
_spec.loader.exec_module(M)
M.IS_MOBILE = True
from kivy.clock import Clock                                    # noqa: E402
from kivy.core.window import Window                             # noqa: E402
from kivy.metrics import dp                                     # noqa: E402
from kivy.uix.behaviors import ButtonBehavior                   # noqa: E402
from kivy.uix.label import Label                                # noqa: E402
from kivy.uix.scrollview import ScrollView                      # noqa: E402
from PIL import Image, ImageDraw                                # noqa: E402


# ---- the chats ----------------------------------------------------------------
# Identities come from a seeded generator, so every run (and both versions
# in a comparison) gets the same addresses, and so the same disc colours.
_rng = random.Random(1210)
_real_mine = M.mine_identity


def _same_mine(*a, **k):
    real = M._os.urandom
    M._os.urandom = lambda n: _rng.getrandbits(8 * n).to_bytes(n, "big")
    try:
        return _real_mine(*a, **k)
    finally:
        M._os.urandom = real


M.mine_identity = _same_mine
keys = M.IdentityManager()
priv, *_ = M.mine_identity()
me = keys.add_identity(priv, "Personal")
keys.activate(me)
keys.set_username("kgt03")
store = M.LocalStore(keys.store_path())
pubs = {}


def person(name, listed=True):
    pk, *_ = M.mine_identity()
    address = M.address_from_priv(pk)
    pubs[address] = M.pub_to_compressed(M.priv_to_pub(pk)).hex()
    store.upsert_contact(address, name, pubs[address], listed=listed)
    return address


alex, bea, chen, dana = (person(n) for n in (
    "alex", "beatrice_with_a_much_longer_username", "chen", "dana"))
eve = person("eve", listed=False)          # known only from the group
store.add_caps(alex, M.LocalStore.CAP_V9)

# A fixed clock, today at 16:00 UTC: before and after show the same times.
BASE = datetime.now(timezone.utc).replace(hour=16, minute=0, second=0,
                                          microsecond=0)


def at(minutes, days=0):
    return (BASE + timedelta(minutes=minutes - 90, days=-days)).isoformat()


_sid = [100]


def came(peer, text, t, unread=False, author=None, **kw):
    """A message received, as the relay's id `_sid` numbers it."""
    _sid[0] += 1
    cid = uuid.uuid4().hex
    store.add_in(peer, text, t, _sid[0], cid, unread=unread, author=author,
                 **kw)
    return cid


def went(peer, text, t, status="read", **kw):
    """A message sent, taken by the relay and then `status`."""
    _sid[0] += 1
    cid = uuid.uuid4().hex
    local = store.add_out(peer, text, t, cid, **kw)
    store.mark_sent(local, _sid[0])
    store.apply_receipt(_sid[0], status)
    return cid


def picture(colour, size=(1600, 1200)):
    image = Image.new("RGB", size, colour)
    draw = ImageDraw.Draw(image)
    for x in range(0, size[0], 80):
        draw.rectangle([x, size[1] * 3 // 4 - x // 3, x + 60, size[1]],
                       fill=(80, 100, 70))
    draw.ellipse([size[0] - 450, 150, size[0] - 200, 400],
                 fill=(240, 210, 140))
    return image


M.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
picture((120, 140, 160)).save(M.MEDIA_DIR / "source.png")
jpeg, w, h, thumb = M.compress_image(str(M.MEDIA_DIR / "source.png"))
photo = M.MEDIA_DIR / "photo.jpg"
photo.write_bytes(jpeg)
media = {"kind": "image", "mime": "image/jpeg", "size": len(jpeg), "w": w,
         "h": h, "thumb": thumb, "blob": "b" * 32,
         "key": M.b64e(os.urandom(32)), "sha": "0" * 64}

# alex: replies, a photo, a voice note, reactions, a link preview.
c1 = came(alex, "Did you see the trail map I sent? The ridge path closes "
          "at dusk.", at(1))
c2 = went(alex, "I did — the dog asleep on the summit is my favourite. 🐕",
          at(2))
came(alex, "Send me the photo when you can?", at(3),
     ext={"reply": {"cid": c2, "snip": "I did — the dog asleep on the summit",
                    "from": me, "kind": "text"}})
c4 = went(alex, "The view from the top", at(4), kind="image",
          ext={"media": dict(media, cap="The view from the top")},
          media_path=str(photo), media_state="ready", status="delivered")
voice = came(alex, "Voice message (0:12)", at(5), kind="voice",
             media_state="ready",
     ext={"media": {"kind": "voice", "mime": "audio/mp4", "size": 30000,
                    "dur": 12000, "blob": "c" * 32,
                    "key": M.b64e(os.urandom(32)), "sha": "1" * 64,
                    "wave": M.waveform_b64([(i * 37) % 100 + 5
                                            for i in range(120)])}})
store.set_media(store.message_by_cid(alex, voice)["id"], path=str(photo))
came(alex, "This is the path I meant: https://en.wikipedia.org/wiki/"
     "Ridgeway_(path)", at(6),
     ext={"lp": {"u": "https://en.wikipedia.org/wiki/Ridgeway_(path)",
                 "t": "The Ridgeway — Britain's oldest road",
                 "s": "Wikipedia"}})
came(alex, "Beautiful 😍", at(9), unread=True)
store.set_reaction(alex, c4, alex, "❤️")
store.set_reaction(alex, c1, me, "👍")
store.set_verified(alex, True)
if hasattr(store, "set_pq_key"):          # 15.0: post-quantum, both ways
    ek, _dk = M.mlkem_keygen(bytes(64))
    store.set_pq_key(alex, ek, M.mlkem_kid(ek))
    store.set_pq_mine(alex, keys.pq_kid)

# The list's other states: a long name, muted, a draft, pinned, archived.
came(bea, "A preview long enough that it must be shortened with an "
     "ellipsis rather than overflowing the row", at(10), unread=True)
for i in range(3):
    came(chen, f"msg {i}", at(11 + i), unread=True)
store.set_mute(chen, BASE.timestamp() + 3 * 86400)
store.set_draft(chen, "Remember to bring the map")
store.set_chat_flag([bea], "marked_unread", 1)
store.set_chat_flag([dana], "pinned", 1000.0)
frank = person("frank_archived")
came(frank, "An old thread, archived", at(2, days=3), unread=True)
store.set_chat_flag([frank], "archived", 1)

# A group: me (admin), alex, chen and eve, with a run from one author.
gid = "ab" * 16
group = M.group_peer(gid)
store.save_group({"id": gid, "name": "Saturday Walkers", "v": 3, "admin": me,
                  "members": [{"a": a, "k": pubs.get(a, keys.public_key_hex),
                               "u": u} for a, u in ((me, "kgt03"),
                              (alex, "alex"), (chen, "chen"), (eve, "eve"))]},
                 "active")
store.add_event(group, "You created “Saturday Walkers”", created_at=at(19))
g1 = came(group, "Rain forecast for the morning", at(20), author=alex)
came(group, "Clearing by eleven though", at(21), author=alex)
came(group, "Shall we start at noon?", at(22), author=alex)
came(group, "Noon works 👍", at(23), author=chen)
went(group, "Noon it is.", at(24), recipients=[alex, chen, eve])
store.set_reaction(group, g1, chen, "👍")

# Colour emoji, and a chat that opens at its unread messages.
sam = person("sam")
s1 = came(sam, "Happy birthday!! 🎂🎈🥳", at(40))
went(sam, "Thank you!! ❤️", at(41))
came(sam, "🥳", at(42))
came(sam, "Photos from the trip: 🇬🇧 🇫🇷 🇮🇹 and the best was the pasta 🍝",
     at(43))
went(sam, "Next year: 🏔️🚲☀️ with the whole family 👨‍👩‍👧", at(44))
store.set_reaction(sam, s1, me, "🎉")
noor = person("noor")
for i in range(12):
    (came if i % 3 else went)(noor, f"Earlier, about the allotment: note "
                              f"{i + 1} of the week's jobs.", at(i * 7, days=1))
for i, text in enumerate(("Back from the allotment!",
                          "The beans have finally climbed the poles",
                          "What do I do with all the courgettes?",
                          "Also: the shed door is fixed 🔧")):
    came(noor, text, at(60 + i), unread=True)
store.add_out(me, "Buy stamps and a birthday card. 📚", at(30),
              uuid.uuid4().hex)

# Chinese, alone and among Latin text: names, the list, the message field.
lin = person("lin")
came(lin, "你好！明天下午三点在老地方见，好吗？", at(80))
went(lin, "好的，明天见 👍", at(81))
came(lin, "我们这次走西山那条路线，大概需要四个小时。记得带水和外套，"
     "山顶风很大。如果下雨的话，我们就改到下周六。", at(82))
came(lin, "The 8:12 train 从北京南站出发，platform 4。", at(83))
went(lin, "收到！我会提前十分钟到。", at(84))
came(lin, "太好了 😊", at(85), unread=True)
hikers = M.group_peer("ef" * 16)
store.save_group({"id": "ef" * 16, "name": "周末爬山群", "v": 1, "admin": me,
                  "members": [{"a": a, "k": pubs.get(a, keys.public_key_hex),
                               "u": u} for a, u in ((me, "kgt03"),
                              (lin, "lin"), (alex, "alex"))]}, "active")
store.add_event(hikers, "You created “周末爬山群”", created_at=at(86))
came(hikers, "大家周六早上八点在地铁站集合。", at(87), author=lin)
came(hikers, "Sounds good — 我带地图。", at(88), author=alex)
went(hikers, "我带零食 🍎", at(89), recipients=[lin, alex])

# Two message requests (v14): people who wrote first, not yet accepted.
REQUESTS = "accepted" in [row[1] for row in store._db.execute(
    "PRAGMA table_info(contacts)")]


def stranger(name, lines):
    address = person(name)
    for i, text in enumerate(lines):
        came(address, text, at(50 + i), unread=True)
    if REQUESTS:
        with store._lock:
            store._db.execute("UPDATE contacts SET accepted = 0, "
                              "ticks_enabled = 0 WHERE address = ?",
                              (address,))
            store._db.commit()
    return address


ravi = stranger("ravi_k", ["Hi — we met at the book fair on Saturday.",
                           "Is this the right Ferritext? I wanted to send "
                           "you the list of titles."])
stranger("best_deals_2026", ["Cheap watches, all brands, today only!!"])
gus = person("gus_blocked")
came(gus, "spam spam spam", at(1, days=2))
store.set_chat_flag([gus], "blocked", 1)

# The outbox (v14), in a disappearing chat: yesterday's message the relay
# never took, one waiting now, and a photo waiting for room.
store.set_chat_timer(dana, 86400, BASE.timestamp())
store.add_out(dana, "Can you send me the address for Saturday?",
              (BASE - timedelta(hours=25)).isoformat(), uuid.uuid4().hex)
store.add_out(dana, "And the time we said?", at(70), uuid.uuid4().hex)
waiting = {k: v for k, v in media.items() if k not in ("blob", "key", "sha")}
store.add_out(dana, "Photo", at(71), uuid.uuid4().hex, kind="image",
              ext={"media": waiting}, media_path=str(photo),
              media_state="waiting")

# A group of 24, where a photo waits for a tap (v14's download rules).
big = M.group_peer("cd" * 16)
crowd = [{"a": me, "k": keys.public_key_hex, "u": "kgt03"},
         {"a": alex, "k": pubs[alex], "u": "alex"}]
for i in range(22):
    neighbour = person(f"neighbour{i}", listed=False)
    crowd.append({"a": neighbour, "k": pubs[neighbour], "u": f"neighbour{i}"})
store.save_group({"id": "cd" * 16, "name": "Riverside Allotments", "v": 1,
                  "admin": alex, "members": crowd}, "active")
came(big, "Who left the gate open?", at(60), author=alex)
came(big, "The new raised beds", at(61), author=alex, kind="image",
     ext={"media": dict(media, cap="The new raised beds", size=184_320)},
     media_state="pending")
pending_photo = store.message_by_cid(
    big, store.messages_for(big)[-1]["client_msg_id"])
store.close()


# ---- the app, with the network stubbed out ------------------------------------
M.FextApp._start_worker = lambda self: setattr(self, "worker", None)
M.ApiClient.features = lambda self: setattr(
    self, "_features", {"blobs": True}) or self._features
M.SplashOverlay.HOLD = 1.2
M.system_reduces_motion = lambda: True       # no animation in the pictures
app = M.FextApp()
PROBLEMS = []


def walk(w):
    for child in w.children:
        yield child
        yield from walk(child)


def ancestors(w):
    w = w.parent
    while w is not None and w is not Window:
        yield w
        w = w.parent


def shown(w):
    """Drawn and in reach: no ancestor faded out, folded away or put by."""
    return all(getattr(a, "opacity", 1) != 0 and getattr(a, "height", 1) > 0
               and not (isinstance(a, M.ReactionStrip) and a.disabled)
               for a in [w] + list(ancestors(w)))


def check_layout(screen):
    """Record what looks broken on screen: the checks a person would make."""
    found = set()
    for w in walk(Window):
        if w.width <= 1 or not shown(w):
            continue
        if isinstance(w, Label) and w.text and \
                not isinstance(w.parent, M.SelectableText):
            w.texture_update()
            tw, th = w.texture_size
            if w.text_size[0] and tw > w.text_size[0] + 2:
                found.add(f"text overflows its width: {w.text[:40]!r}")
            elif w.text_size[0] is None and w.size_hint_x is not None and \
                    tw > w.width + 2 and not getattr(w, "shorten", False):
                found.add(f"label wider than its box: {w.text[:40]!r}")
            if w.size_hint_y is None and w.text_size[1] is None and \
                    th > w.height + 3 and not isinstance(w, M.EmojiKey):
                found.add(f"text cut off below: {w.text[:40]!r}")
        if isinstance(w, M.CapsLabel) and w.width > 4 and \
                w.natural_width > w.width + 1:
            found.add(f"capitals truncated: {w.text[:30]!r}")
        if isinstance(w, ButtonBehavior) and \
                not isinstance(w, (M.ReactionChip, M.QuoteBlock,
                                   M.SelectableText)) and \
                not (isinstance(w, M.ToggleSwitch) and
                     any(isinstance(a, M.ListRow) for a in ancestors(w))) \
                and (w.width < dp(40) or w.height < dp(40)):
            found.add(f"touch target under 40 dp: {type(w).__name__}")
        parent = w.parent
        if parent is None or parent is Window or \
                any(isinstance(a, ScrollView) for a in ancestors(w)) or \
                isinstance(parent, (ScrollView, M.Avatar, M.Scatter)) or \
                (isinstance(parent, M.FloatLayout) and
                 not isinstance(parent, M.ImageContent)):
            continue
        if w.right > parent.right + 1.5 or w.x < parent.x - 1.5 or \
                w.top > parent.top + 1.5 or w.y < parent.y - 1.5:
            found.add(f"{type(w).__name__} escapes its "
                      f"{type(parent).__name__}")
    PROBLEMS.extend(f"{screen}: {problem}" for problem in sorted(found))


def close_sheets():
    for view in list(Window.children):
        if isinstance(view, M.ModalView):
            view.dismiss(animation=False)


def scroll_settings_to(title):
    """Scroll Settings until the row titled `title` is in view."""
    page = app.manager.get_screen("settings")
    for w in walk(page):
        if isinstance(w, Label) and title.lower() in (w.text or "").lower():
            view = next((a for a in ancestors(w)
                         if isinstance(a, ScrollView)), None)
            if view is not None:
                view.scroll_to(w, padding=dp(160), animate=False)
            return


def if_present(owner, name, *args):
    """Call owner.name(*args) if this version has it (v14's screens)."""
    method = getattr(owner, name, None)
    return method(*args) if method else None


def type_in(text):
    """Write `text` in the message field, as a person typing it would."""
    field = app.conversation_screen.entry.input
    field.text = text
    field.cursor = (len(text), 0)


def ask_mobile_data():
    M.on_metered_network = lambda: True
    if_present(app, "download_on_tap", pending_photo)


#: (what to do, seconds to let it settle, the screen to capture after it)
STEPS = [
    (lambda: None, 1.5, "chats"),
    (app.open_drawer, 1.0, "menu"),
    (app.close_drawer, 0.4, None),
    (lambda: app.open_conversation(alex), 1.0, "chat"),
    (app.open_chat_info, 0.8, "chat_info"),
    (app.go_back_to_chats, 0.4, None),
    (lambda: app.open_conversation(group), 1.0, "group"),
    (app.go_back_to_chats, 0.4, None),
    (lambda: app.open_conversation(sam), 1.0, "emoji"),
    (app.go_back_to_chats, 0.4, None),
    (lambda: app.open_conversation(noor), 1.0, "unread"),
    (app.go_back_to_chats, 0.4, None),
    (lambda: app.open_conversation(me), 1.0, "notes"),
    (app.go_back_to_chats, 0.4, None),
    (lambda: app.open_conversation(lin), 1.0, "chinese"),
    (lambda: type_in("我在路上了，大概二十分钟后到"), 0.6, "chinese_typing"),
    (lambda: type_in(""), 0.3, None),
    (app.go_back_to_chats, 0.4, None),
    (lambda: app.open_conversation(hikers), 1.0, "chinese_group"),
    (app.go_back_to_chats, 0.4, None),
    (lambda: if_present(app.chats_screen, "show_view", "requests"), 0.6,
     "requests"),
    (lambda: if_present(app.chats_screen, "show_view", "chats"), 0.4, None),
    (lambda: app.open_conversation(ravi), 1.0, "request"),
    (lambda: M.ReportSheet(app, ravi).open()
     if hasattr(M, "ReportSheet") else None, 0.8, "report"),
    (close_sheets, 0.3, None),
    (app.go_back_to_chats, 0.4, None),
    (lambda: app.open_conversation(dana), 1.0, "outbox"),
    (app.go_back_to_chats, 0.4, None),
    (lambda: app.open_conversation(big), 1.0, "large_group"),
    (ask_mobile_data, 0.8, "mobile_data"),
    (close_sheets, 0.3, None),
    (app.go_back_to_chats, 0.4, None),
    (app.open_settings, 0.8, "settings"),
    (lambda: scroll_settings_to("Link previews"), 0.5, "settings_privacy"),
    (lambda: scroll_settings_to("Ask before using mobile data"), 0.5,
     "settings_downloads"),
    (lambda: M.BlockedSheet(app).open()
     if hasattr(M, "BlockedSheet") else None, 0.8, "blocked"),
    (close_sheets, 0.3, None),
]
assert [s for *_, s in STEPS if s] == list(SCREENS)


def run(i=0):
    if i == len(STEPS):
        print(f"{len(PROBLEMS)} layout problems")
        for problem in PROBLEMS:
            print("  " + problem)
        app.stop()
        return
    action, settle, screen = STEPS[i]
    try:
        action()
    except Exception as exc:                 # an older version, most likely
        PROBLEMS.append(f"step {i} ({screen or 'between screens'}) raised "
                        f"{type(exc).__name__}: {exc}")

    def capture(_dt):
        if screen and (not ONLY or screen in ONLY):
            check_layout(screen)
            Window.screenshot(name=os.path.join(
                ARGS.outdir, f"{ARGS.theme}_{screen}.png"))
        run(i + 1)
    Clock.schedule_once(capture, settle)


def start(_dt):
    Window.size = (dp(392), dp(840))
    Clock.schedule_once(lambda _dt: run(), 0.3)


Clock.schedule_once(start, 0)
app.run()
# Kivy's screenshot adds a counter to each name; take it off again.
for name in os.listdir(ARGS.outdir):
    if name.endswith("0001.png"):
        os.replace(os.path.join(ARGS.outdir, name),
                   os.path.join(ARGS.outdir, name[:-8] + ".png"))
sys.exit(1 if PROBLEMS else 0)
