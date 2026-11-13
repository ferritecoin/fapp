#!/usr/bin/env python3
"""Screenshots of the real app, to look at a change before and after.

    xvfb-run -a -s "-screen 0 1280x1024x24" \\
        python3 desktop/capture.py main.py shots/after [--theme light]
            [--set overclock] [--lang zh-CN]

It fills a fresh profile with the same chats on every run (people, a
group, chats in Chinese, message requests, messages waiting to send),
starts the app with the network stubbed out, and walks it through SCREENS
in the language asked for (English unless --lang), saving each one as
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
import time
import random
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

#: Every screen, in the order they are captured.
SCREENS = ("chats", "chats_pressed", "menu", "chat", "call_refused", "chat_info", "group", "emoji", "verify",
           "verify_end", "unread", "notes", "chinese", "chinese_typing",
           "chinese_group", "rtl", "rtl_typing", "scripts", "requests",
           "request", "report", "outbox", "deleted_user",
           "deleted_user_info", "large_group", "mobile_data",
           "settings", "settings_privacy", "settings_downloads", "blocked",
           "keep_running", "keep_running_set",
           # 16.1: calls as sessions (their pictures are stand-ins here)
           "call_card_live", "call_card_ended", "call_card_chat",
           "call_toast", "call_lobby_off", "call_lobby_on", "call_grid",
           "call_focus_share", "call_chat", "call_leave_host",
           # 16.2
           "call_host_more", "call_aside", "settings_lock", "app_lock",
           "locked", "settings_backup", "about_update", "chat_editing",
           "group_mention", "app_lock_offer", "settings_experimental",
           # 17.0: video (its pictures are stand-ins here)
           "video_call", "video_call_mine", "call_recording",
           "settings_calls", "video_quality", "chats_mention", "delete_old",
           "auto_backup", "recordings",
           # 17.1: who called, the presenter's face, an incoming call's bar
           "call_cards_who", "call_cards_devices", "call_incoming_bar",
           "video_call_present",
           "call_focus_face", "settings_present",
           # 17.2: the meter, More, a floating call, call waiting, React,
           # identities that ring, Send later, a planned call
           "call_meter", "call_more", "call_float", "call_waiting",
           "call_react", "call_more_group", "call_meter_group", "identities",
           "send_later", "chat_scheduled", "plan_card",
           "settings_recordings_backup", "call_lobby_video",
           # 17.3: React in a 1-to-1 call, the relay operator's notice and
           # stop (relay 13.4), a call on Android 5
           "call_react_1to1", "notice", "group_stopped", "call_android5",
           # 17.4: what a group's members may do, a member restricted, two
           # calls going on in one chat
           "group_rules", "group_permissions", "group_restrict",
           "group_restricted", "group_calls",
           # 17.4: a group's topics: all of them, one, the admin's sheet,
           # and turned off
           "group_topics", "group_topic", "group_topics_sheet",
           "group_topics_off",
           # 18.0: a group's admins: the owner's page with one, a member's
           # menu, an admin's rights, the admin log, a co-admin's page, and
           # deleting a member's message
           "group_admins", "group_member_menu", "group_admin_rights",
           "group_admin_log", "group_coadmin", "group_delete_admin",
           # 18.0: a link for anyone (its sheet, one opened, who asks in by
           # it), the new chat sheet, a call's notes (its card, its thread),
           # Leave a note, and Add someone in a 1-to-1 call
           "join_link", "ask_to_join", "join_asks", "compose", "call_notes",
           "call_thread", "call_leave_note", "call_add_someone",
           # 18.1: away replies: the chat list's reminder, Settings where a
           # tap on it lands, the reply's words, and a chat with one each way
           "chats_away", "settings_away", "away_words", "chat_away",
           # 18.1: video messages: in a chat (one sent, one waiting for a
           # tap on mobile data), one playing, the camera key's choice, and
           # the recording sheet before and while it records, and stopped
           # (the app left the screen, a call began, the camera failed)
           "chat_video", "chat_video_playing", "video_menu", "video_sheet",
           "video_recording", "video_stopped",
           # 18.1: lists fade at their edges where there is more (the
           # owner's note on BUILT23): the chat list and a chat in their
           # middles, and the switch in Settings
           "chats_middle", "chat_middle", "settings_fade")

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("client", help="the main.py to capture")
parser.add_argument("outdir", help="where the PNGs go")
parser.add_argument("--theme", choices=("black", "dark", "light"),
                    default="black", help="black is dark with the black "
                    "background, the app's default on a dark phone")
parser.add_argument("--set", default="axiom",
                    help="the theme set (15.1): axiom, overclock or ferrite")
parser.add_argument("--lang", default="en",
                    help="the app's language (15.1): en or zh-CN")
parser.add_argument("--only", default="", help="comma-separated screens")
parser.add_argument("--desktop", action="store_true",
                    help="a computer's window (1180 x 760 dp), not a phone's")
parser.add_argument("--landscape", action="store_true",
                    help="a phone turned on its side (840 x 392 dp)")
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
    # keep_running_offered, app_lock_offered: the guide and the app
    # lock's offer open when asked, not by themselves.
    f.write('{"black": %s, "theme_set": "%s", "language": "%s", '
            '"keep_running_offered": true, "app_lock_offered": true}' % (
                "true" if ARGS.theme == "black" else "false", ARGS.set,
                ARGS.lang))

import importlib.util                                           # noqa: E402
import logging                                                  # noqa: E402
# Only the problems: the app's log and Kivy's start-up notes stay quiet
# (xvfb-run mixes them into the output).
logging.disable(logging.CRITICAL)
_spec = importlib.util.spec_from_file_location("fext_capture", ARGS.client)
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_capture"] = M
_spec.loader.exec_module(M)
M.IS_MOBILE = not ARGS.desktop        # --desktop: a computer's, all of it
if hasattr(M, "phone_maker"):         # 15.1: a phone that stops background
    M.phone_maker = lambda: "xiaomi"  # apps, for the keep-running guide
    M.battery_unrestricted = lambda: False
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
# a call's line, as each version leaves it (16.0: how it went, under it)
call_line = M.tr("Outgoing call · {time}", time="4:12")
if hasattr(M, "call_quality_line"):
    call_line += "\n" + M.call_quality_line(
        {"quality": "ultra", "kbps": 192, "path": "direct", "rtt": 38,
         "loss": 0.002})
store.add_event(alex, call_line, created_at=at(7))
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

# Someone the relay no longer knows (16.0): a reset relay refused the
# last message as "recipient not found", so it waits at its clock and
# they show as Deleted User.
finn = person("finn")
came(finn, "See you at the station", at(40, days=1))
store.add_out(finn, "I'm here — by the clock", at(55), uuid.uuid4().hex)
if hasattr(store, "set_gone"):
    store.set_gone(finn, True)

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

# Right to left (15.1): Arabic, Persian and Hebrew, with the numbers,
# brackets and Latin words written among them.
amira = person("amira")
came(amira, "مرحبا! الاجتماع غدا الساعة 10:30 مع John في المكتب "
     "(الطابق 3).", at(70))
went(amira, "Sounds good — أراك غدا 👍", at(71))
came(amira, "שלום! המחיר הוא 1,234.50 ₪ לשני אנשים, כולל ארוחת בוקר.",
     at(72))
came(amira, "سلام! فردا ساعت ۹ صبح می‌بینمت.", at(73))
came(amira, "السلام عليكم ورحمة الله، كيف حالك اليوم؟ أتمنى أن يكون كل "
     "شيء على ما يرام 🌙", at(74), unread=True)
# Every other script in everyday use.
meera = person("meera")
for i, text in enumerate((
        "Καλημέρα! Τα λέμε αύριο.",
        "ሰላም! ነገ እንገናኛለን።",
        "Բարեւ, վաղը կհանդիպենք։",
        "გამარჯობა! ხვალ შევხვდებით.",
        "ආයුබෝවන්! හෙට හමුවෙමු.",
        "မင်္ဂလာပါ၊ မနက်ဖြန် တွေ့ကြမယ်",
        "ខ្ញុំនឹងទៅដល់ឆាប់ៗ",
        "สวัสดีครับ พรุ่งนี้เจอกันที่สถานีรถไฟตอนแปดโมงเช้านะครับ",
        "வணக்கம்! நாளை சந்திப்போம்.",
        "আমি আসছি, দশ মিনিট লাগবে।",
        "नमस्ते! कल सुबह क्षेत्रीय कार्यालय में मिलते हैं।")):
    (went if i % 4 == 1 else came)(meera, text, at(75 + i / 4))
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
        # A bubble's author line ends in an ellipsis by design: a name
        # can be any length.
        if isinstance(w, M.CapsLabel) and w.width > 4 and \
                w.natural_width > w.width + 1 and \
                not (isinstance(w.parent, M.MessageBubble) and
                     w is w.parent.author):
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


def quiet():
    """Put away a note over the page (Toast, 16.0) before the next step."""
    note = getattr(getattr(M, "Toast", None), "current", None)
    if note is not None:
        note.dismiss(fade=False)


def close_sheets():
    for view in list(Window.children):
        if isinstance(view, M.ModalView):
            view.dismiss(animation=False)


def keep_running_set():
    """The guide once both settings are made (15.1)."""
    if not hasattr(M, "keep_running_left"):
        return
    M.battery_unrestricted = lambda: True
    M.save_pref("autostart_opened", True)
    M.KeepRunningSheet(app, "xiaomi").open()


def verify_sheet():
    """Verifying a contact (sam is not yet verified), with the scan rows a
    phone shows."""
    M.can_read_qr = lambda: True
    if_present(app, "open_verify", sam)


def scroll_sheet_to_end():
    """Scroll the open sheet to its last row."""
    for view in Window.children:
        if isinstance(view, M.ModalView):
            for w in walk(view):
                if isinstance(w, ScrollView):
                    w.scroll_y = 0


def scroll_settings_to(title):
    """Scroll Settings until the row titled `title` is in view."""
    title = getattr(M, "tr", str)(title)      # in the app's language (15.1)
    page = app.manager.get_screen("settings")
    for w in walk(page):
        if isinstance(w, Label) and title.lower() in (w.text or "").lower():
            view = next((a for a in ancestors(w)
                         if isinstance(a, ScrollView)), None)
            if view is not None:
                view.scroll_to(w, padding=dp(160), animate=False)
            return


def scroll_middle(view):
    """A list scrolled halfway, so there is more above and below it."""
    view.scroll_y = 0.5


def if_present(owner, name, *args):
    """Call owner.name(*args) if this version has it (v14's screens)."""
    method = getattr(owner, name, None)
    return method(*args) if method else None


def type_in(text):
    """Write `text` in the message field, as a person typing it would."""
    field = app.conversation_screen.entry.input
    field.text = text
    field.cursor = (len(text), 0)


def finger(action, x, y):
    """A finger as Android's SDL gives it to Kivy (a touch, no button)."""
    from kivy.core.window.window_sdl2 import SDL2MotionEventProvider
    w, h = Window.size
    SDL2MotionEventProvider.q.appendleft((action, 5, x / w, 1 - y / h, 1.0))


def press_chat(peer):
    """A finger resting on a chat in the list (16.0 lights it at once)."""
    row = next(w for w in app.chats_screen.list.walk()
               if isinstance(w, M.ConversationRow) and w.peer == peer)
    PRESS["at"] = row.to_window(row.center_x, row.center_y)
    finger("fingerdown", *PRESS["at"])


def slide_off():
    """The resting finger becomes a scroll and lifts: nothing opens."""
    x, y = PRESS["at"]
    for step in range(1, 9):
        finger("fingermotion", x, y + step * 15)
    finger("fingerup", x, y + 120)


PRESS = {}


def ask_mobile_data():
    M.on_metered_network = lambda: True
    if_present(app, "download_on_tap", pending_photo)



# ---- 16.1: calls as sessions --------------------------------------------------
# A camera's picture and a shared screen are stand-ins drawn here: these
# captures show the screens, not the media path, which comes later.

def frame_texture(img):
    from kivy.graphics.texture import Texture
    img = img.convert("RGB")
    tex = Texture.create(size=img.size, colorfmt="rgb")
    tex.blit_buffer(img.tobytes(), colorfmt="rgb", bufferfmt="ubyte")
    tex.flip_vertical()
    return tex


def camera_frame(back, shirt, size=(640, 480)):
    return frame_texture(camera_picture(back, shirt, size))


def camera_picture(back, shirt, size=(640, 480)):
    """Someone at their desk: a lit wall, a head and shoulders."""
    from PIL import ImageDraw
    w, h = size
    img = Image.new("RGB", size, back)
    d = ImageDraw.Draw(img)
    for y in range(h):                       # light from above
        k = 1 - 0.35 * y / h
        d.line([(0, y), (w, y)], fill=tuple(int(c * k) for c in back))
    d.ellipse([w * .30, h * .62, w * .70, h * 1.25], fill=shirt)
    d.ellipse([w * .39, h * .22, w * .61, h * .62], fill=(214, 172, 140))
    d.ellipse([w * .38, h * .16, w * .62, h * .38], fill=(58, 42, 34))
    return img


def screen_frame(size=(1280, 720)):
    """A shared slide: a title, three points and a chart."""
    from PIL import ImageDraw
    w, h = size
    img = Image.new("RGB", size, (250, 249, 245))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 92], fill=(36, 41, 56))
    d.rectangle([60, 28, 520, 64], fill=(236, 239, 245))
    for i, y in enumerate((170, 250, 330)):
        d.ellipse([70, y + 8, 90, y + 28], fill=(203, 167, 97))
        d.rectangle([110, y + 6, 110 + (380 - i * 70), y + 30],
                    fill=(150, 156, 170))
    for i, v in enumerate((180, 260, 210, 330, 380)):
        x = 700 + i * 100
        d.rectangle([x, 620 - v, x + 60, 620], fill=(70, 120, 180))
    d.line([(680, 620), (1220, 620)], fill=(120, 120, 120), width=3)
    return frame_texture(img)


CALL = {}
# The camera and screen states are design captures, with the pictures
# below: calls send no video yet (main.py, call_camera_available).
if hasattr(M, "call_camera_available"):
    M.call_camera_available = lambda: True


def call_people(session, chosen):
    for user, state in chosen:
        session.update_member(dict({"userId": user}, **state))


def call_group_live():
    """A group's video call, 12 minutes in: its card and pinned banner."""
    if not hasattr(M, "CallSession"):
        return
    s = M.CallSession(group, me, group=True, video=True, host=alex,
                      started_at=time.time() - 754)
    call_people(s, [(alex, {"micEnabled": True, "cameraEnabled": True}),
                    (bea, {"cameraEnabled": True}), (chen, {})])
    app._call_sessions()[s.id] = s
    app.store.add_call_card(group, M.card_of_session(s))
    CALL["group"] = s
    app.open_conversation(group)
    app.show_call_banner(s)


def call_one_ended():
    """A 1-to-1 call that is over, with what was written during it."""
    if not hasattr(M, "CallSession"):
        return
    app.hide_call_banner()
    s = M.CallSession(alex, me, group=False, video=True, host=me,
                      started_at=time.time() - 3600)
    s.stage, s.ended_at = "ended", time.time() - 3600 + 724
    s.summary = "Outgoing call · 12:04\nHigh · Direct · 38 ms · 0.2% lost"
    chat = [(alex, "Here's the link: maps.example/q4", 2 * 60),
            (me, "Got it, thanks!", 3 * 60),
            (alex, "Slides are in the shared folder.", 9 * 60)]
    for seq, (who, text, after) in enumerate(chat, 1):
        app.store.add_call_chat(s.id, seq, s.started_at + after, who, text)
    s.chat_count = len(chat)
    app.store.add_call_card(alex, M.card_of_session(s))
    app.open_conversation(alex)


def call_open_drawer():
    row = next((w for w in walk(app.conversation_screen)
                if isinstance(w, getattr(M, "CallCard", ()))), None)
    if row is not None:
        row.toggle_drawer()


def call_toast():
    if not hasattr(M, "CallSession"):
        return
    app.go_back_to_chats()
    s = M.CallSession(sam, me, group=False, video=True, host=sam)
    app.show_call_toast(s)


class StandInCoder:
    """17.0: a camera that draws nothing of its own (the screens keep their
    stand-in picture), as the coders' five calls."""
    error, plan = "", []

    def set_plan(self, plan, codec):
        self.plan = plan

    def ask_key(self, layer=None):
        pass

    def preview(self, box):
        return None

    def poll(self, timeout):
        time.sleep(timeout)
        return []

    def close(self):
        pass


if hasattr(M, "make_video_coder"):
    M.make_video_coder = lambda track: StandInCoder()
    M.call_video_why_not = lambda track="v": ""


def call_lobby(camera):
    if not hasattr(M, "CallSession"):
        return
    app.hide_call_toast()
    if camera:
        app.__dict__.setdefault("_call_frames", {})[(me, "v")] = \
            camera_frame((96, 118, 140), (40, 60, 90))
        if not CALL["lobby"].session.me.camera:
            CALL["lobby"].toggle_camera()
        return
    close_sheets()
    # A voice call's lobby (17.0: a video call's opens with the camera on).
    s = M.CallSession(alex, me, group=False, video=False, host=me)
    CALL["lobby"] = app.preview_call(s)


def call_conference(view, chat=False, face=False):
    """A group's call; with `face`, dana shares her screen with her camera
    on (17.1: her face in the stage's corner)."""
    if not hasattr(M, "CallSession"):
        return
    close_sheets()
    app.__dict__.setdefault("_call_frames", {}).pop((me, "v"), None)
    frames = app.__dict__.setdefault("_call_frames", {})
    frames[(alex, "v")] = camera_frame((150, 128, 104), (120, 40, 40))
    frames[(bea, "v")] = camera_frame((90, 120, 110), (200, 180, 160))
    frames[(dana, "s")] = screen_frame()
    frames[(dana, "v")] = camera_frame((128, 112, 140), (60, 90, 70))
    s = M.CallSession(group, me, group=True, video=True, host=me,
                      started_at=time.time() - 1312)
    s.preview()
    s.set_mic(True)
    s.confirm_join()
    call_people(s, [(alex, {"micEnabled": True, "cameraEnabled": True}),
                    (bea, {"cameraEnabled": True, "handRaisedAt": 2000}),
                    (chen, {"micEnabled": True, "handRaisedAt": 1000}),
                    (dana, {"micEnabled": True, "cameraEnabled": face,
                            "screenSharing": view == "focus"})])
    s.speaking = alex
    s.view = view
    chat_lines = [{"name": "chen", "text": "Can everyone see the slides?",
                   "time": "15:21"},
                  {"name": "dana", "text": "Sharing now.", "time": "15:22"}]
    screen = app.join_call(s, None)
    for msg in chat_lines:
        screen.receive_chat(msg)
    if chat:
        screen.toggle_chat()
    CALL["screen"] = screen


def call_leave_choice():
    """The host leaving a call others are in: leave it to them, or end it
    for everyone."""
    screen = CALL.get("screen")
    if screen is not None:
        screen.on_end = lambda: None
        screen.leave()


def call_close():
    close_sheets()
    screen = CALL.pop("screen", None)
    if screen is not None:
        screen.close()


# ---- 16.2 ---------------------------------------------------------------------
def call_host_more():
    """The host's choices: Mute everyone, Lower all hands."""
    screen = CALL.get("screen")
    if screen is None or not hasattr(screen, "host_choices"):
        return
    if M.call_is_narrow():
        screen.more()
    else:
        M.MenuSheet(screen.host_choices()).open()


def call_aside():
    """The call set aside: the chat list, under the bar that leads back."""
    screen = CALL.get("screen")
    if screen is None or not hasattr(app, "set_aside_group_call"):
        return
    close_sheets()
    app.__dict__.update(_room_screen=screen, _room_call=type(
        "Call", (), {"session": screen.session, "engine": None,
                     "index": None})())
    app.go_back_to_chats()
    app.set_aside_group_call()


def call_aside_end():
    if hasattr(app, "_drop_room_pill"):
        app._drop_room_pill()
    app.__dict__.pop("_room_call", None)
    app.__dict__.pop("_room_screen", None)
    call_close()


def lock_sheet():
    if hasattr(M, "AppLockSheet"):
        sheet = M.AppLockSheet(app)
        sheet.open()
        sheet.code.text = sheet.again.text = "2580"


def lock_screen():
    close_sheets()
    if hasattr(app, "lock_now"):
        app.lock_now()


def unlock():
    if hasattr(app, "unlocked"):
        app.unlocked()


def about_update():
    if not hasattr(M, "app_latest_of"):
        return
    app.api._features = dict(getattr(app.api, "_features", None) or {},
                             app_latest=("16.3.0", "https://github.com/"
                                         "ferritecoin/fapp/releases/latest"))
    M.AboutSheet(app).open()


def chat_editing():
    """One of yours, just sent, being edited; the one before it, edited."""
    if not hasattr(M, "EDIT_WINDOW"):
        return
    close_sheets()
    app.api._features = {k: v for k, v in (getattr(app.api, "_features",
                                                   None) or {}).items()
                         if k != "app_latest"}
    app.open_conversation(alex)
    first = app.send_text(alex, "Meet at the north gate")
    second = app.send_text(alex, "or by the café, if it rains")
    for local_id, server_id in ((first, 9101), (second, 9102)):
        app.store.mark_sent(local_id, server_id)
    app.store.edit_message(alex, app.store.message(first)["client_msg_id"],
                           "out", "Meet at the south gate", M.now_iso())
    app.refresh_chat(alex)
    app.conversation_screen.start_edit(app.store.message(second))


# ---- 17.0 ---------------------------------------------------------------------
def video_call(mine=False):
    """A 1-to-1 video call, 4 minutes in: alex's camera over the screen;
    with `mine`, one's own in its corner."""
    if not hasattr(M, "CallPicture"):
        return
    close_sheets()
    frames = app.__dict__.setdefault("_call_frames", {})
    frames[(alex, "v")] = camera_frame((150, 128, 104), (120, 40, 40),
                                       size=(720, 1280) if not ARGS.desktop
                                       else (1280, 720))
    frames[(me, "v")] = camera_frame((96, 118, 140), (40, 60, 90),
                                     size=(480, 640) if not ARGS.desktop
                                     else (640, 360))
    if app.call is None:
        s = M.CallSession(alex, me, group=False, video=True, host=me,
                          started_at=time.time() - 241)
        s.preview()
        s.confirm_join()
        call = M.Call(alex, outgoing=True, call_id=s.id)
        call.session, call.state = s, "active"
        call.connected_at = time.monotonic() - 241
        call.path = "direct"
        app.call = call
        call_people(s, [(alex, {"micEnabled": True, "cameraEnabled": True})])
    app.call.session.me.camera = mine
    app.show_call()
    app.call_view = lambda tiles: None        # the stand-ins stay


def video_call_end():
    if app.call is not None:
        app._close_call_screen(app.call)
    app.__dict__.pop("call_view", None)


def call_recording():
    """A group's call that one of them records: the red mark."""
    call_conference("grid")
    screen = CALL.get("screen")
    if screen is not None and hasattr(screen, "show_recording"):
        screen.session.members[bea].recording = True
        screen.show_recording()


def video_quality():
    if hasattr(app.settings_screen, "_choose_video_quality"):
        app.settings_screen._choose_video_quality()


def chats_mention():
    """A group's message that names you, unread: the @ in the list."""
    close_sheets()
    app.store.add_in(group, "@" + "me, can you bring the projector?",
                     M.now_iso(), 99001, uuid.uuid4().hex, author=bea,
                     ext={"mn": [me]})
    app.go_back_to_chats()
    app.refresh_sidebar()


def delete_old():
    """A message of yours from last week: deleted only for you (F11)."""
    if not hasattr(M, "DELETE_WINDOW"):
        return
    app.open_conversation(alex)
    old = next(m for m in app.store.messages_for(alex)
               if m["direction"] == "out")
    M.DeleteModal(app, [dict(old, created_at=(
        datetime.now(timezone.utc) - timedelta(days=7)).isoformat())]).open()


def auto_backup():
    if not hasattr(M, "AutoBackupSheet"):
        return
    M.DeviceKeystore.available = classmethod(lambda cls: True)
    sheet = M.AutoBackupSheet(app)
    sheet.open()
    sheet.pass1.text = sheet.pass2.text = "correct horse battery"


def recordings():
    """Two calls recorded on this device (only their headers are real)."""
    if not hasattr(M, "RecordingsSheet"):
        return
    close_sheets()
    folder = M.recordings_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for days, title, seconds in ((1, "Allotment committee", 2712),
                                 (6, "alex", 724)):
        made = (datetime.now(timezone.utc) -
                timedelta(days=days)).isoformat(timespec="seconds")
        head = M.json.dumps({"v": 1, "made": made, "title": title,
                             "seconds": seconds}).encode()
        (folder / f"call-{days}{M.RECORDING_SUFFIX}").write_bytes(
            M.RECORDING_MAGIC + len(head).to_bytes(4, "big") + head +
            os.urandom(4096))
    M.RecordingsSheet(app, lambda: None).open()


# ---- 17.1 ----------------------------------------------------------------------
def call_cards_who():
    """Three of alex's 1-to-1 calls: one missed from alex, one made here
    and not answered, one answered (17.1: each on its caller's side)."""
    if not hasattr(M, "CallSession"):
        return
    close_sheets()
    now = time.time()
    for host, video, outcome, summary, ago, length in (
            (alex, False, "missed", "Missed call", 50, 0),
            (me, False, "unanswered", "No answer", 40, 0),
            (alex, True, "answered", "Incoming call · 12:04\nHigh · "
             "Direct · 38 ms · 0.2% lost", 30, 724)):
        s = M.CallSession(alex, me, group=False, video=video, host=host,
                          started_at=now - ago * 60)
        s.stage, s.ended_at = "ended", now - ago * 60 + length
        s.summary, s.outcome = summary, outcome
        when = datetime.fromtimestamp(now - ago * 60, timezone.utc)
        app.store.add_call_card(alex, M.card_of_session(s), when.isoformat())
    app.open_conversation(alex)


def call_cards_devices():
    """18.1: a call from alex that another device of this identity took,
    one declined there, and one missed here."""
    if "elsewhere" not in getattr(M, "CALL_OUTCOMES", ()):
        return
    close_sheets()
    now = time.time()
    for ago, outcome in ((25, "elsewhere"), (15, "declined"), (5, "missed")):
        when = datetime.fromtimestamp(now - ago * 60, timezone.utc)
        app.store.add_call_card(alex, M.missed_call_card(
            alex, me, {"id": uuid.uuid4().hex, "v": outcome == "missed"},
            outcome), when.isoformat())
    app.open_conversation(alex)


def call_incoming_bar():
    """sam calls; the toast is closed: the bar under the app bar, there on
    every screen while it rings (17.1)."""
    if not hasattr(M, "CallSession"):
        return
    close_sheets()
    app.go_back_to_chats()
    s = M.CallSession(sam, me, group=False, video=False, host=sam)
    call = M.Call(sam, outgoing=False, call_id=s.id)
    call.session, call.state = s, "incoming"
    app.call = call
    app.show_call_toast(s)
    toast = app.__dict__.get("_call_toast")
    next(w for w in toast.children if isinstance(w, M.IconButton)) \
        .dispatch("on_release")


def call_incoming_end():
    if app._call_pill is not None:
        app._call_pill.remove()
        app._call_pill = None
    app.call = None


def video_call_present():
    """alex shares the screen with the camera on (17.1: the face in a
    corner, across from one's own)."""
    video_call(True)
    if app.call is None:
        return
    app.__dict__["_call_frames"][(alex, "s")] = screen_frame()
    call_people(app.call.session, [(alex, {
        "micEnabled": True, "cameraEnabled": True, "screenSharing": True})])
    app.show_call()


# ---- 17.2 ----------------------------------------------------------------------
class StandInEngine:
    """A call's numbers, as its engine tells the meter (17.2): a good way."""
    way, rtt_ms, quiet = "fine", 38.0, False

    def meter(self):
        return {"way": self.way, "rtt": self.rtt_ms, "packet_ms": 5,
                "kbps": 128, "loss": 0.002, "jitter": 2.0}

    def set_media(self, *_a, **_k):
        pass

    def react(self, _emoji):
        pass


def call_meter():
    """A 1-to-1 video call's meter: its bars, and their numbers."""
    video_call(False)
    if app.call is None or not hasattr(app, "show_call_meter"):
        return
    app.call.engine = StandInEngine()
    app.show_call()
    app.show_call_meter()


def call_more():
    """A 1-to-1 call's More, over its screen."""
    if hasattr(app, "call_more") and app.call is not None:
        app.show_call()
        app.call_more()


def call_float():
    """The call set aside: it floats, small, over alex's chat."""
    if app.call is None:
        return
    close_sheets()
    app.hide_call()
    app.open_conversation(alex)


def call_waiting():
    """sam calls during the call with alex: the waiting bar over it."""
    if app.call is None or not hasattr(app, "_wait_call"):
        return
    app.show_call()
    app.__dict__["_waiting_tone"] = lambda on: None       # no sound here
    app._wait_call(sam, {"id": uuid.uuid4().hex, "v": False,
                         "t": time.time()})


def call_waiting_end():
    if hasattr(app, "_drop_waiting"):
        app._drop_waiting()


def call_react():
    """A reaction over your tile in a group's call."""
    screen = CALL.get("screen")
    if screen is not None and hasattr(screen, "react"):
        screen.react("\U0001F389")


def call_more_group():
    """A group call's More, with its link (the call has a room)."""
    screen = CALL.get("screen")
    if screen is None:
        return
    screen.session.room = "5c0ffee5" * 4
    screen.more()


def call_meter_group():
    """A group call's meter: the bars in its head, and their numbers."""
    screen = CALL.get("screen")
    if screen is None or not hasattr(app, "show_call_meter"):
        return
    for view in list(Window.children):         # the menus, not the call
        if isinstance(view, M.Sheet):
            view.dismiss(animation=False)
    app.__dict__["_room_call"] = type("Call", (), {
        "engine": StandInEngine(), "session": screen.session,
        "index": None})()
    if hasattr(screen, "refresh"):
        screen.refresh()                  # the bars, from the numbers
    app.show_call_meter()


def call_meter_group_end():
    app.__dict__.pop("_room_call", None)


def identities():
    """A second identity here, with a name on the relay: its bell."""
    if not hasattr(M, "IdentitySheet"):
        return
    close_sheets()
    other, *_ = M.mine_identity()
    pub = M.pub_to_compressed(M.priv_to_pub(other))
    app.keys.restore_identity({
        "private_key_hex": "%064x" % other, "label": "Work",
        "address": M.address_from_pub_bytes(pub), "username": "kgtwork"})
    M.IdentitySheet(app).open()


def send_later():
    """Send held: when shall it go?"""
    close_sheets()
    app.open_conversation(alex)
    screen = app.conversation_screen
    if hasattr(screen, "send_later_menu"):
        screen.entry.text = "Happy birthday! Have a lovely day"
        screen.send_later_menu()


def chat_scheduled():
    """That message, waiting in the chat with its time."""
    close_sheets()
    screen = app.conversation_screen
    screen.entry.text = ""
    tomorrow = (datetime.now() + timedelta(days=1)).replace(
        hour=9, minute=0, second=0, microsecond=0)
    if hasattr(M, "MSG_SCHEDULED"):
        app.send_text(alex, "Happy birthday! Have a lovely day",
                      send_at=tomorrow.timestamp())
    app.open_conversation(alex)


def plan_card():
    """A video call planned with alex for this evening: its card, under
    the message waiting for tomorrow."""
    if hasattr(app, "plan_call"):
        evening = datetime.now().replace(hour=19, minute=30, second=0,
                                         microsecond=0)
        if evening < datetime.now() + timedelta(minutes=30):
            evening += timedelta(days=1)
        app.plan_call(alex, evening.timestamp(), True)
    app.open_conversation(alex)


def call_lobby_video():
    """A video call's lobby: a phone's camera row (front or back), a
    computer's cameras, microphones and speakers, each with its ⓘ."""
    close_sheets()
    s = M.CallSession(alex, me, group=False, video=True, host=me)
    CALL["lobby"] = app.preview_call(s)


def group_mention():
    if not hasattr(M, "MentionStrip"):
        return
    close_sheets()
    app.conversation_screen.cancel_reply()
    app.open_conversation(group)
    screen = app.conversation_screen
    screen.entry.text = "Thanks — @"
    screen.entry.input.cursor = screen.entry.input.get_cursor_from_index(10)
    screen._sync_mentions()

# ---- 17.3 ----------------------------------------------------------------------
def call_react_1to1():
    """React in a 1-to-1 call: alex's over his picture, yours over your
    corner."""
    if not hasattr(app, "react_in_call"):
        return
    video_call(True)
    if app.call is None:
        return
    app.call.engine = StandInEngine()
    app.react_in_call("\U0001F44D")
    app._heard_reaction(app.call, "\U0001F602")


def notice():
    """A notice from the relay's operator (relay 13.4), in its sheet."""
    if hasattr(app, "_on_relay_notice"):
        app._on_relay_notice({
            "type": "notice", "id": 1, "at": time.time() - 600,
            "text": "People have reported spam from you. This relay's rules "
                    "do not allow it: please stop."})


def group_stopped():
    """A message to the group that the relay's operator stopped: the chat
    says what, and until when; the message waits at its clock."""
    if not hasattr(M, "restricted"):
        return
    local = app.store.add_out(group, "Who's bringing the flask?", at(90),
                              uuid.uuid4().hex, recipients=[alex, chen, eve])
    app.open_conversation(group)
    app._not_sent(alex, local, "refused", "", M.ApiClient.ApiError(
        403, "restricted", "stopped",
        {"what": "groups", "until": time.time() + 5 * 3600}))


def call_android5():
    """Call, tapped on Android 5: calls need Android 6."""
    if not hasattr(M, "android_calls_blocker"):
        return
    real = M.android_calls_blocker
    M.android_calls_blocker = lambda: M.tr(
        "Calls need Android 6 or later; this phone has Android 5")
    try:
        app.open_conversation(alex)
        app.start_call(alex)
    finally:
        M.android_calls_blocker = real


# ---- 17.4 ----------------------------------------------------------------------
def group_rules():
    """The group's page as its admin sees it once new calls are theirs
    alone and chen's messages are stopped for a day (before 17.4: the page
    as it was)."""
    close_sheets()
    quiet()
    if hasattr(app, "restrict_member"):
        now, gid = app.api.relay_now(), M.gid_of(group)
        app.store.save_group(dict(app._group_state(gid), r={
            "deny": {"calls": now - 3600},
            "lim": {chen: {"deny": ["send"], "at": now - 600,
                           "until": now + 86400}}}), "active")
    app.open_conversation(group)
    app.open_chat_info()
    scroll = app.group_info_screen.scroll           # down to its members
    Clock.schedule_once(lambda _dt: setattr(scroll, "scroll_y", 0), 0.4)


def group_permissions():
    """...its switches for what every member may do."""
    if not hasattr(M, "GroupRightsSheet"):
        return
    gid = M.gid_of(group)
    M.GroupRightsSheet(app, gid, lambda: None,
                       deny=app.store.group(gid)["rules"]["deny"]).open()


def group_restrict():
    """...and chen's restriction: what he may do here, and how long."""
    if not hasattr(M, "RestrictSheet"):
        return
    close_sheets()
    M.RestrictSheet(app, M.gid_of(group), chen, "chen", lambda: None).open()


def group_restricted():
    """A member whose messages the admin stopped until tomorrow: the chat
    says so, and the composer gives way to when."""
    if not hasattr(app, "group_rule"):
        return
    close_sheets()
    now, gid = app.api.relay_now(), "cd" * 16
    app.store.save_group(dict(app._group_state(gid), r={"lim": {me: {
        "deny": ["send"], "at": now - 60, "until": now + 20 * 3600}}}),
        "active")
    app.store.add_event(big, M.tr(
        "{who} stopped your {what} here until {when}", who="alex",
        what=M.tr("messages"), when=M.when_text(time.time() + 20 * 3600)))
    app.open_conversation(big)


def group_calls():
    """Two calls going on in one group's chat: the banner says so, and the
    call button offers each, or another (before 17.4: what the call button
    did with a call going on)."""
    if not hasattr(app, "_call_sessions"):
        return
    close_sheets()
    M.ApiClient.features = lambda self: {"blobs": True, "rooms": True}
    for host, video, minutes in ((alex, True, 12), (lin, False, 3)):
        s = M.CallSession(hikers, me, group=True, video=video, host=host,
                          started_at=time.time() - minutes * 60)
        s.room, s.token, s.key = os.urandom(8).hex(), \
            os.urandom(32).hex(), M.b64e(os.urandom(32))
        app._call_sessions()[s.id] = s
        app.__dict__.setdefault("_watched", {})[s.id] = time.monotonic()
        app.store.add_call_card(hikers, M.card_of_session(s))
    app.open_conversation(hikers)
    app.show_call_banner(s)
    app.start_group_call(hikers)


ROUTE, KIT = "0a1b2c3d", "1b2c3d4e"


def group_topics():
    """A group with topics: the strip under its name; all of them shown,
    each message naming its topic beside its author."""
    if not hasattr(app, "change_topics"):
        return
    close_sheets()
    gid = M.gid_of(group)
    app.store.save_group(dict(app._group_state(gid), tp={"on": True, "list": [
        {"id": ROUTE, "n": "Route", "c": False},
        {"id": KIT, "n": "Kit", "c": False}]}), "active")
    live = app.store                     # the profile's own is closed now
    for i, (text, who, topic) in enumerate((
            ("Ridge path or the valley?", alex, ROUTE),
            ("Valley: the ridge is closed until May", chen, ROUTE),
            # a long name gives way before its topic
            ("Who has the spare stove?", bea, KIT))):
        _sid[0] += 1
        live.add_in(group, text, at(150 + i), _sid[0], uuid.uuid4().hex,
                    unread=topic == KIT, author=who, ext={"tp": topic})
    _sid[0] += 1
    mine = live.add_out(group, "I'll bring it, and the first-aid kit",
                        at(153), uuid.uuid4().hex, ext={"tp": KIT},
                        recipients=[alex, chen, eve])
    live.mark_sent(mine, _sid[0])
    live.apply_receipt(_sid[0], "read")
    app.open_conversation(group)


def group_topic():
    """...one topic chosen: its messages alone; another topic's new message
    leaves a dot on its chip."""
    if hasattr(M, "TopicStrip"):
        app.conversation_screen.choose_topic(ROUTE)
        _sid[0] += 1
        app.store.add_in(group, "And a pot big enough for four?", at(154),
                         _sid[0], uuid.uuid4().hex, unread=True,
                         author=alex, ext={"tp": KIT})
        app.refresh_chat(group)


def group_topics_sheet():
    """...and the admin's sheet of them."""
    if hasattr(M, "TopicsSheet"):
        M.TopicsSheet(app, M.gid_of(group), lambda: None).open()


def group_topics_off():
    """Topics turned off: one chat again, each message's topic named."""
    if not hasattr(app, "change_topics"):
        return
    close_sheets()
    gid = M.gid_of(group)
    state = app._group_state(gid)
    app.store.save_group(dict(state, tp=dict(state["tp"], on=False)),
                         "active")
    app.conversation_screen.choose_topic(None)
    app.refresh_chat(group)


# ---- 18.0 ----------------------------------------------------------------------
def group_admins():
    """The group's page as its owner sees it, alex an admin who renamed it
    and deleted one of chen's messages (before 18.0: one admin, the page as
    it was)."""
    close_sheets()
    quiet()
    gid = M.gid_of(group)
    if hasattr(M, "AdminLogSheet"):
        now, state = app.api.relay_now(), app._group_state(gid)
        v = state["v"]
        app.store.save_group(dict(state, ad={
            alex: {"r": ["info", "add", "delete"], "by": me}}, lg=[
            {"v": v - 2, "a": me, "e": "admin", "t": alex, "at": now - 7200},
            {"v": v - 1, "a": alex, "e": "name", "t": "Saturday Walkers",
             "at": now - 3600},
            {"v": v, "a": alex, "e": "delete", "t": chen,
             "at": now - 600}]), "active")
    app.open_conversation(group)
    app.open_chat_info()


def group_member_menu():
    """...a member's menu: making them an admin, beside the rest."""
    if hasattr(app.group_info_screen, "_member_menu"):
        app.group_info_screen._member_menu(chen, "chen")


def group_admin_rights():
    """...alex's rights as an admin, as the owner sets them."""
    close_sheets()
    if hasattr(M, "AdminRightsSheet"):
        M.AdminRightsSheet(app, M.gid_of(group), alex, "alex",
                           lambda: None).open()


def group_admin_log():
    """...and the admin log: who did what, newest first."""
    close_sheets()
    if hasattr(M, "AdminLogSheet"):
        M.AdminLogSheet(app, M.gid_of(group)).open()


def group_coadmin():
    """A group I am an admin of, not its owner: its page offers what my
    rights allow, and shows them."""
    close_sheets()
    gid = "cd" * 16
    if hasattr(M, "AdminLogSheet"):
        state = app._group_state(gid)
        app.store.save_group(dict(state, r={}, ad={me: {
            "r": ["info", "add", "delete"], "by": alex}}), "active")
    app.open_conversation(big)
    app.open_chat_info()
    scroll = app.group_info_screen.scroll           # its top, not members
    scroll.scroll_y = 1
    Clock.schedule_once(lambda _dt: setattr(scroll, "scroll_y", 1), 0.8)


def group_delete_admin():
    """Deleting a member's message as an admin: for everyone, whatever its
    age (before 18.0: for me only)."""
    close_sheets()
    rows = [m for m in app.store.messages_for(group)
            if m.get("author") == chen]
    if rows:
        M.DeleteModal(app, rows[-1:]).open()


def join_link():
    """The group's link for anyone: its QR code, to copy, share or stop."""
    close_sheets()
    if hasattr(M, "JoinLinkSheet"):
        app.open_conversation(group)
        M.JoinLinkSheet(app, "group", M.gid_of(group)).open()


def ask_to_join():
    """alex's link to a group I am not in, opened: asking in, as one of my
    identities."""
    close_sheets()
    if hasattr(M, "AskToJoinSheet"):
        url = M.join_link("https://relay.example", alex, "5a" * 8, "group",
                          "ef" * 16, "Book club")
        M.AskToJoinSheet(app, M.parse_join_link(url)).open()


def join_asks():
    """Two people asking in by the group's link."""
    close_sheets()
    if not hasattr(M, "JoinAsksSheet"):
        return
    gid = M.gid_of(group)
    link = app.store.open_join_link("group", gid) or \
        app.store.add_join_link("group", gid)
    for who in (ravi, dana):
        app.store.add_join_ask(link, who)
    M.JoinAsksSheet.show(app, "group", gid)


def compose():
    """The new chat sheet (18.0: Join with a link, too)."""
    close_sheets()
    app.go_back_to_chats()
    M.ComposeSheet(app).open()


def call_notes():
    """A group's call that is over, with two notes on it: its card says
    how many."""
    close_sheets()
    if not hasattr(M, "ThreadBar"):
        return
    s = M.new_group_call(group, me, False)
    s.room, s.stage = "ab" * 8, "ended"
    s.started_at, s.ended_at = time.time() - 3300, time.time() - 1800
    app.store.add_call_card(group, M.card_of_session(s))
    reply = {"snip": "Group voice call", "from": me, "kind": "call",
             "cid": s.id}
    for i, (who, words) in enumerate(((chen, "Minutes are in the folder"),
                                      (bea, "Next one Friday, same time?"))):
        _sid[0] += 1
        app.store.add_in(group, words, at(170 + i), _sid[0],
                         uuid.uuid4().hex, author=who, ext={"reply": reply})
    _sid[0] += 1
    app.store.add_in(group, "Who has the spare stove?", at(172), _sid[0],
                     uuid.uuid4().hex, author=alex)
    CALL["notes"] = s
    app.open_conversation(group)


def call_thread():
    """...tapped: its notes alone, under a bar back to the whole chat."""
    s = CALL.get("notes")
    if s is not None:
        app.conversation_screen.note_on_call(
            s, app.store.message_by_cid(group, s.id))


def call_leave_note():
    """A 1-to-1 call sam did not answer: Leave a note, on its card."""
    close_sheets()
    if not hasattr(M, "ThreadBar"):
        return
    app.conversation_screen.close_thread()
    s = M.CallSession(sam, me, group=False, video=False, host=me)
    s.stage, s.outcome, s.ended_at = "ended", "unanswered", time.time() - 300
    app.store.add_call_card(sam, M.card_of_session(s))
    app.open_conversation(sam)


def call_add_someone():
    """A 1-to-1 call's More › Add someone: which of my contacts."""
    close_sheets()
    if hasattr(app, "choose_call_addition"):
        video_call(False)
        app.choose_call_addition()


AWAY = {}


def away_on():
    """Settings › Away is on (18.1): the chat list says so, above noor's
    chat, which has an automatic reply each way."""
    close_sheets()
    app.go_back_to_chats()
    pk, *_ = M.mine_identity()
    noor = AWAY["noor"] = M.address_from_priv(pk)
    app.store.upsert_contact(noor, "noor",
                             M.pub_to_compressed(M.priv_to_pub(pk)).hex())
    usual = "I am away and will reply when I am back."
    mine = getattr(M, "away_words", lambda: usual)()
    for i, (out, text, ext) in enumerate((
            (False, "Are you free on Thursday?", None),
            (True, mine, {"auto": 1}),
            (True, "Back now. Thursday works!", None),
            (False, "On a flight until tonight.", {"auto": 1}))):
        _sid[0] += 1
        cid = uuid.uuid4().hex
        if out:
            local = app.store.add_out(noor, text, at(180 + i), cid, ext=ext)
            app.store.mark_sent(local, _sid[0])
            app.store.apply_receipt(_sid[0], "read")
        else:
            app.store.add_in(noor, text, at(180 + i), _sid[0], cid,
                             unread=False, ext=ext)
    M.save_pref("away_since", time.time())
    app.refresh_sidebar()


def away_settings():
    """...and a tap on that: Settings, at its Away section."""
    if hasattr(M, "away_since"):
        app.open_settings(away=True)
    else:
        app.open_settings()


def away_chat():
    """noor's chat: which of its messages are automatic replies."""
    close_sheets()
    app.close_settings()
    app.open_conversation(AWAY["noor"])


# ---- 18.1: video messages ---------------------------------------------------
# Their pictures are stand-ins, as a call's are: someone drawn here.
VIDEO = {}


def video_pixels(back, shirt, size=(480, 640)):
    """A picture as a coder or a decoder hands it over."""
    return (camera_picture(back, shirt, size).tobytes(),) + size + ("rgb",)


def video_chat():
    """ines's chat: a video message from her that waits for a tap (this
    phone is on mobile data), and one sent to her. An app before 18.1
    shows each as its words."""
    pk, *_ = M.mine_identity()
    ines = VIDEO["ines"] = M.address_from_priv(pk)
    app.store.upsert_contact(ines, "ines",
                             M.pub_to_compressed(M.priv_to_pub(pk)).hex())
    kept = M.MEDIA_DIR / "capture_video.mp4"
    M.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    kept.write_bytes(b"a stand-in file")
    poster = getattr(M, "poster_b64", lambda picture: "")
    rows = ((False, "The view from the top!", None, None),
            (False, "Video message (0:42)",
             video_pixels((120, 150, 190), (60, 90, 70)), 42000),
            (True, "Video message (0:17)",
             video_pixels((60, 70, 110), (150, 60, 50)), 17000),
            (True, "Wish you were here.", None, None))
    for i, (out, text, picture, length) in enumerate(rows):
        _sid[0] += 1
        cid, media = uuid.uuid4().hex, None
        if picture:
            media = {"kind": "video", "mime": "video/mp4", "dur": length,
                     "size": 5_400_000 if not out else 2_200_000,
                     "w": 480, "h": 640, "thumb": poster(picture)}
        if out:
            local = app.store.add_out(
                ines, text, at(200 + i), cid,
                kind="video" if media else "text",
                ext={"media": media} if media else None,
                media_path=str(kept) if media else None,
                media_state="ready" if media else "")
            app.store.mark_sent(local, _sid[0])
            app.store.apply_receipt(_sid[0], "read")
            if media:
                VIDEO["sent"] = local
        else:
            if media:
                media.update(blob="b" * 32, sha="0" * 64,
                             key=M.b64e(bytes(32)))
            app.store.add_in(ines, text, at(200 + i), _sid[0], cid,
                             unread=False,
                             kind="video" if media else "text",
                             ext={"media": media} if media else None,
                             media_state="pending" if media else "")
    M.on_metered_network = lambda: True
    app.fetch_media = lambda local_id: None
    app.open_conversation(ines)


def video_playing():
    """The one sent, playing: its pictures in the bubble, a line of
    progress under them."""
    bubble = app.conversation_screen._bubbles.get(VIDEO.get("sent"))
    if bubble is None or not hasattr(M, "VideoContent"):
        return
    msg = bubble.msg
    app.voice.path, app.voice._desk = msg["media_path"], object()
    bubble.media.refresh(msg)
    bubble.media.show(video_pixels((70, 80, 120), (150, 60, 50)))
    bubble.media.set_progress(0.4)


def video_playing_end():
    app.voice.path = app.voice._desk = None
    if hasattr(app.conversation_screen, "refresh_voice_buttons"):
        app.conversation_screen.refresh_voice_buttons()


class StandInRecorder:
    """A video message's recorder as its sheet uses it: a phone's (two
    cameras), showing someone drawn here."""
    max_seconds, ended, error = 60, False, ""

    def __init__(self, elapsed_ms=0):
        self.coder = type("Coder", (), {"error": "", "flip": None})()
        self.recording, self.elapsed_ms = bool(elapsed_ms), elapsed_ms
        self.serial = 0

    def preview(self, box):
        self.serial += 1
        return (self.serial,) + video_pixels((120, 150, 190), (60, 90, 70))

    def start(self):
        self.recording = True

    def flip(self):
        pass

    def stop(self):
        pass

    cancel = stop


def video_sheet(elapsed_ms=0, stopped=False):
    """The recording sheet: before Record, seven seconds in, and
    `stopped` where it was."""
    close_sheets()
    if not hasattr(M, "VideoNoteSheet"):
        return
    sheet = M.VideoNoteSheet(app, StandInRecorder(), "")
    sheet.open()
    if elapsed_ms:
        sheet._record()
        sheet.recorder.elapsed_ms = elapsed_ms
    if stopped and hasattr(sheet, "stop_here"):
        sheet.stop_here()


#: (what to do, seconds to let it settle, the screen to capture after it)
STEPS = [
    (lambda: None, 1.5, "chats"),
    (lambda: press_chat(meera), 0.15, "chats_pressed"),
    (slide_off, 0.5, None),
    (app.open_drawer, 1.0, "menu"),
    (app.close_drawer, 0.4, None),
    (lambda: app.open_conversation(alex), 1.0, "chat"),
    # This relay carries no calls: a tap on Call says so in the chat (16.0).
    (lambda: app.start_call(alex), 0.6, "call_refused"),
    (quiet, 0.3, None),
    (app.open_chat_info, 0.8, "chat_info"),
    (app.go_back_to_chats, 0.4, None),
    (lambda: app.open_conversation(group), 1.0, "group"),
    (app.go_back_to_chats, 0.4, None),
    (lambda: app.open_conversation(sam), 1.0, "emoji"),
    (verify_sheet, 0.8, "verify"),
    (scroll_sheet_to_end, 0.5, "verify_end"),
    (close_sheets, 0.3, None),
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
    (lambda: app.open_conversation(amira), 1.0, "rtl"),
    (lambda: type_in("أراك غدا الساعة 10:30"), 0.6, "rtl_typing"),
    (lambda: app.open_conversation(meera), 1.0, "scripts"),
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
    (lambda: app.open_conversation(finn), 1.0, "deleted_user"),
    (app.open_chat_info, 0.8, "deleted_user_info"),
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
    (lambda: M.KeepRunningSheet(app, "xiaomi").open()
     if hasattr(M, "KeepRunningSheet") else None, 0.8, "keep_running"),
    (close_sheets, 0.3, None),
    (keep_running_set, 0.8, "keep_running_set"),
    (close_sheets, 0.3, None),
    (call_group_live, 1.2, "call_card_live"),
    (call_one_ended, 1.2, "call_card_ended"),
    (call_open_drawer, 0.6, "call_card_chat"),
    (call_toast, 0.8, "call_toast"),
    (lambda: call_lobby(False), 0.8, "call_lobby_off"),
    (lambda: call_lobby(True), 0.6, "call_lobby_on"),
    (lambda: call_conference("grid"), 1.0, "call_grid"),
    (call_close, 0.3, None),
    (lambda: call_conference("focus"), 1.0, "call_focus_share"),
    (call_close, 0.3, None),
    (lambda: call_conference("grid", chat=True), 1.0, "call_chat"),
    (call_close, 0.3, None),
    (lambda: call_conference("grid"), 1.0, None),
    (call_leave_choice, 0.8, "call_leave_host"),
    (call_close, 0.3, None),
    (lambda: call_conference("grid"), 1.0, None),
    (call_host_more, 0.8, "call_host_more"),
    (close_sheets, 0.3, None),
    (call_aside, 1.0, "call_aside"),
    (call_aside_end, 0.4, None),
    (app.open_settings, 0.8, None),
    (lambda: scroll_settings_to("App lock"), 0.5, "settings_lock"),
    (lock_sheet, 0.8, "app_lock"),
    (lock_screen, 0.8, "locked"),
    (unlock, 0.3, None),
    (lambda: scroll_settings_to("Restore from a backup"), 0.5,
     "settings_backup"),
    (about_update, 0.8, "about_update"),
    (close_sheets, 0.3, None),
    (chat_editing, 1.0, "chat_editing"),
    (group_mention, 1.0, "group_mention"),
    (lambda: M.AppLockOffer(app).open() if hasattr(M, "AppLockOffer")
     else None, 0.8, "app_lock_offer"),
    (close_sheets, 0.3, None),
    (app.open_settings, 0.8, None),
    (lambda: scroll_settings_to("Clipboard that forgets"), 0.5,
     "settings_experimental"),
    # 17.0
    (lambda: video_call(False), 1.0, "video_call"),
    (lambda: video_call(True), 0.8, "video_call_mine"),
    (video_call_end, 0.6, None),
    (call_recording, 1.0, "call_recording"),
    (call_close, 0.3, None),
    (app.open_settings, 0.8, None),
    (lambda: scroll_settings_to("Video quality"), 0.5, "settings_calls"),
    (video_quality, 0.8, "video_quality"),
    (close_sheets, 0.3, None),
    (chats_mention, 1.0, "chats_mention"),
    (delete_old, 0.8, "delete_old"),
    (close_sheets, 0.3, None),
    (auto_backup, 0.8, "auto_backup"),
    (recordings, 0.8, "recordings"),
    # 17.1
    (close_sheets, 0.3, None),
    (call_cards_who, 1.2, "call_cards_who"),
    (call_cards_devices, 1.2, "call_cards_devices"),
    (call_incoming_bar, 0.8, "call_incoming_bar"),
    (call_incoming_end, 0.3, None),
    (video_call_present, 1.0, "video_call_present"),
    (video_call_end, 0.6, None),
    (lambda: call_conference("focus", face=True), 1.0, "call_focus_face"),
    (call_close, 0.3, None),
    (app.open_settings, 0.8, None),
    (lambda: scroll_settings_to("Their face while they present"), 0.5,
     "settings_present"),
    # 17.2
    (close_sheets, 0.3, None),
    (quiet, 0.1, None),
    (call_meter, 1.0, "call_meter"),
    (close_sheets, 0.3, None),
    (call_more, 0.8, "call_more"),
    (close_sheets, 0.3, None),
    (call_float, 1.0, "call_float"),
    (call_waiting, 0.8, "call_waiting"),
    (call_waiting_end, 0.3, None),
    (video_call_end, 0.6, None),
    (lambda: call_conference("grid"), 1.0, None),
    (call_react, 0.8, "call_react"),
    (call_more_group, 0.8, "call_more_group"),
    (call_meter_group, 0.8, "call_meter_group"),
    (call_meter_group_end, 0.3, None),
    (call_close, 0.3, None),
    (identities, 0.8, "identities"),
    (send_later, 0.8, "send_later"),
    (chat_scheduled, 1.0, "chat_scheduled"),
    (plan_card, 1.0, "plan_card"),
    (quiet, 0.1, None),
    (app.open_settings, 0.8, None),
    (lambda: scroll_settings_to("Recordings in backups"), 0.5,
     "settings_recordings_backup"),
    (call_lobby_video, 0.8, "call_lobby_video"),
    # 17.3
    (close_sheets, 0.3, None),
    (call_react_1to1, 1.0, "call_react_1to1"),
    (video_call_end, 0.6, None),
    (notice, 0.8, "notice"),
    (close_sheets, 0.3, None),
    (group_stopped, 1.0, "group_stopped"),
    (app.go_back_to_chats, 0.4, None),
    (quiet, 0.1, None),
    (call_android5, 0.8, "call_android5"),
    # 17.4
    (close_sheets, 0.3, None),
    (app.go_back_to_chats, 0.4, None),
    (group_rules, 1.0, "group_rules"),
    (group_permissions, 0.8, "group_permissions"),
    (group_restrict, 0.8, "group_restrict"),
    (close_sheets, 0.3, None),
    (app.go_back_to_chats, 0.4, None),
    (group_restricted, 1.0, "group_restricted"),
    (app.go_back_to_chats, 0.4, None),
    (group_calls, 1.0, "group_calls"),
    (close_sheets, 0.3, None),
    (app.go_back_to_chats, 0.4, None),
    (group_topics, 1.2, "group_topics"),
    (group_topic, 1.0, "group_topic"),
    (group_topics_sheet, 0.8, "group_topics_sheet"),
    (group_topics_off, 1.2, "group_topics_off"),
    # 18.0
    (close_sheets, 0.3, None),
    (app.go_back_to_chats, 0.4, None),
    (group_admins, 1.0, "group_admins"),
    (group_member_menu, 0.8, "group_member_menu"),
    (group_admin_rights, 0.8, "group_admin_rights"),
    (group_admin_log, 0.8, "group_admin_log"),
    (close_sheets, 0.3, None),
    (app.go_back_to_chats, 0.4, None),
    (group_coadmin, 1.3, "group_coadmin"),
    (close_sheets, 0.3, None),
    (app.go_back_to_chats, 0.4, None),
    (lambda: app.open_conversation(group), 1.0, None),
    (group_delete_admin, 0.8, "group_delete_admin"),
    (join_link, 0.8, "join_link"),
    (ask_to_join, 0.8, "ask_to_join"),
    (join_asks, 0.8, "join_asks"),
    (compose, 0.8, "compose"),
    (call_notes, 1.2, "call_notes"),
    (call_thread, 1.0, "call_thread"),
    (call_leave_note, 1.0, "call_leave_note"),
    (call_add_someone, 0.8, "call_add_someone"),
    (close_sheets, 0.3, None),
    (video_call_end, 0.6, None),
    # 18.1
    (away_on, 1.0, "chats_away"),
    (away_settings, 0.8, "settings_away"),
    (lambda: if_present(app.settings_screen, "_edit_away"), 0.8,
     "away_words"),
    (away_chat, 1.0, "chat_away"),
    (lambda: M.save_pref("away_since", 0), 0.1, None),
    (app.go_back_to_chats, 0.4, None),
    (video_chat, 1.2, "chat_video"),
    (video_playing, 0.4, "chat_video_playing"),
    (video_playing_end, 0.3, None),
    (lambda: if_present(app.conversation_screen, "camera_menu"), 0.8,
     "video_menu"),
    (video_sheet, 1.0, "video_sheet"),
    (lambda: video_sheet(7000), 1.0, "video_recording"),
    (lambda: video_sheet(14000, True), 1.0, "video_stopped"),
    (close_sheets, 0.3, None),
    (app.go_back_to_chats, 0.4, None),
    (lambda: scroll_middle(app.chats_screen.scroll), 0.5, "chats_middle"),
    (lambda: app.open_conversation(alex), 1.0, None),
    (lambda: scroll_middle(app.conversation_screen.scroll), 0.5,
     "chat_middle"),
    (app.go_back_to_chats, 0.4, None),
    (app.open_settings, 0.8, None),
    (lambda: scroll_settings_to("Fade the edges of lists"), 0.5,
     "settings_fade"),
    (app.close_settings, 0.4, None),
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

    def capture(_dt, first=True):
        if screen and (not ONLY or screen in ONLY):
            try:
                if first:
                    check_layout(screen)
                name = os.path.join(ARGS.outdir, f"{ARGS.theme}_{screen}.png")
                Window.screenshot(name=name)
                # Under Xvfb a frame is now and then read back all black;
                # such a picture is taken again, after the window has been
                # asked to draw: a page that has stopped moving draws no
                # new frame by itself, and was read back black again
                # (18.1). (Its alpha is opaque, so only the colours tell.)
                taken = name[:-4] + "0001.png"
                if first and \
                        Image.open(taken).convert("RGB").getbbox() is None:
                    print(f"{screen}: read back all black, taken again")
                    os.remove(taken)
                    Window.canvas.ask_update()
                    Clock.schedule_once(lambda dt: capture(dt, False), 0.5)
                    return
            except Exception as exc:    # else Kivy ends the run, silently
                PROBLEMS.append(f"{screen}: capturing it raised "
                                f"{type(exc).__name__}: {exc}")
        run(i + 1)
    Clock.schedule_once(capture, settle)


def start(_dt):
    Window.size = (dp(1180), dp(760)) if ARGS.desktop else (
        (dp(840), dp(392)) if ARGS.landscape else (dp(392), dp(840)))
    Clock.schedule_once(lambda _dt: run(), 0.3)


Clock.schedule_once(start, 0)
app.run()
# Kivy's screenshot adds a counter to each name; take it off again.
for name in os.listdir(ARGS.outdir):
    if name.endswith("0001.png"):
        os.replace(os.path.join(ARGS.outdir, name),
                   os.path.join(ARGS.outdir, name[:-8] + ".png"))
sys.exit(1 if PROBLEMS else 0)
