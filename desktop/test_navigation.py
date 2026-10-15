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
  * a tap on a message raises its reactions; a second tap puts them away.
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
os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-nav-")
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
check("...with the way back to the end offered",
      screen._newer > 0 and screen.jump_button.opacity > 0)
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

for app in (alice, bob):
    app.store.close()
passed = sum(1 for _n, c in RESULTS if c)
print(f"\n  {passed}/{len(RESULTS)} passed")
os._exit(0 if passed == len(RESULTS) else 1)
