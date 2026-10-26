#!/usr/bin/env python3
"""End-to-end message flows between two real app controllers.

    xvfb-run -a python3 desktop/test_flows.py main.py

Alice and Bob are two FextApp instances (screens built, nothing run). The
relay is replaced by an in-memory stand-in implementing the same calls the
client makes — send, blob upload/download with expiry — and every envelope
is decrypted by the real CryptoEngine and ingested by the real SyncWorker.
What is checked is therefore what actually goes over the wire, including
the full media loop: upload, download, expiry, ask-to-resend, re-upload.
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
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-flows-")
os.environ["FEXT_SERVER_HOST"] = "127.0.0.1:1"

_spec = importlib.util.spec_from_file_location(
    "fext_flows", sys.argv[1] if len(sys.argv) > 1 else "main.py")
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_flows"] = M
_spec.loader.exec_module(M)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""))


class Relay:
    """In-memory relay: a mailbox per address and a blob store."""

    def __init__(self):
        self.mail, self.blobs, self.expired = [], {}, set()
        self.expects = {}         # v14: how many each upload said would fetch
        self.directory = {}       # address -> public key, as lookup gives it
        self._next = 1

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
        # v14: what was asked of the relay, and how it can misbehave.
        self.requests = []
        self.ticks = {}
        self.down = False         # out of reach
        self.media_full = False   # no room for media (507)
        self.refuse = set()       # members send_many does not store

    def features(self):
        return self._features

    def _reach(self, what):
        self.requests.append(what)
        if self.down:
            raise M.requests.ConnectionError("the relay is out of reach")

    def send_message(self, recipient, envelope, client_msg_id):
        self._reach(("send", recipient))
        return {"server_id": self.relay.post(self.keys.address, recipient,
                                             envelope, client_msg_id)}

    def send_many(self, items):
        self._reach("send_many")
        return [{"error": {"code": "mailbox_full", "message": "full"}}
                if item["recipient"] in self.refuse else
                {"server_id": self.relay.post(
                    self.keys.address, item["recipient"], item["envelope"],
                    item["client_msg_id"])} for item in items]

    def upload_blob(self, blob, expect=0):
        self._reach("upload")
        if self.media_full:
            raise M.ApiClient.ApiError(507, "relay_full", "no room for media")
        blob_id = uuid.uuid4().hex
        self.relay.blobs[blob_id] = blob
        self.relay.expects[blob_id] = expect
        return {"blob": blob_id}

    def lookup(self, username=None, address=None):
        if address not in self.relay.directory:
            raise M.ApiClient.ApiError(404, "not_found", "no such user")
        return {"address": address,
                "public_key": self.relay.directory[address]}

    def report(self, address, reason, note=""):
        self.requests.append(("report", address, reason, note))
        return {"report": 1}

    def download_blob(self, blob_id):
        if blob_id in self.relay.expired or blob_id not in self.relay.blobs:
            raise M.ApiClient.ApiError(410, "expired", "media expired")
        return self.relay.blobs[blob_id]

    def read(self, ids):
        return {"updated": len(ids)}

    def set_ticks(self, peer, enabled):
        self.ticks[peer] = enabled
        return {}


def make_party(label, relay):
    app = M.FextApp()
    # build() schedules the normal start-up (probe the relay, then activate
    # the identity, which creates a REAL ApiClient and SyncWorker). These
    # parties are wired to the in-memory relay by hand instead, so that
    # start-up must not run when the test ticks the clock.
    app._dismiss_splash = lambda *_a: None
    app._probe_transport = lambda: None
    app.build()
    priv, _p, _a, _n = M.mine_identity()
    address = app.keys.add_identity(priv, label)
    app.keys.activate(address)
    app.keys.set_username(label)
    relay.directory[address] = app.keys.public_key_hex
    app.crypto = M.CryptoEngine(app.keys)
    app.api = StubApi(relay, app.keys)
    app.store = M.LocalStore(app.keys.store_path())
    app.worker = M.SyncWorker(app)
    return app


def wait_for(cond, timeout=5.0):
    """Poll `cond`, ticking the Kivy clock meanwhile: work that hops back
    to the UI thread (run_on_ui) only runs when the clock does, and
    nothing is running the app's event loop in this test."""
    from kivy.clock import Clock
    end = time.time() + timeout
    while time.time() < end:
        Clock.tick()
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def deliver(relay, parties):
    """Hand every queued envelope to its recipient's sync worker."""
    delivered = 0
    while relay.mail:
        msg = relay.mail.pop(0)
        app = parties[msg["recipient"]]
        app.worker._ingest_message(msg)
        delivered += 1
    return delivered


def pump(app):
    """Run the app's UI-queue handler (what the Kivy clock does)."""
    app._drain_ui_queue(0)


relay = Relay()
alice, bob = make_party("alice", relay), make_party("bob", relay)
parties = {alice.keys.address: alice, bob.keys.address: bob}
for me, other in ((alice, bob), (bob, alice)):
    me.store.upsert_contact(other.keys.address, other.keys.username,
                            other.keys.public_key_hex)

print("\nText, replies, forwards")
alice.current_peer = bob.keys.address
first = alice.send_text(bob.keys.address, "hello bob")
wait_for(lambda: relay.mail)
deliver(relay, parties)
row = bob.store.messages_for(alice.keys.address)[-1]
check("a plain message arrives", row["body"] == "hello bob")
check("...and teaches Bob that Alice runs v9",
      bob.store.get_contact(alice.keys.address)["caps"] & M.LocalStore.CAP_V9)

cid = row["client_msg_id"]
bob.current_peer = alice.keys.address
bob.send_text(alice.keys.address, "hi alice", reply={
    "cid": cid, "snip": "hello bob", "from": alice.keys.address,
    "kind": "text"})
wait_for(lambda: relay.mail)
deliver(relay, parties)
got = alice.store.messages_for(bob.keys.address)[-1]
quote = M.message_ext(got).get("reply", {})
check("a reply carries its quote to the other side",
      got["body"] == "hi alice" and quote.get("cid") == cid and
      quote.get("from") == alice.keys.address)

carol_priv, _p, carol, _n = M.mine_identity()
carol = M.address_from_priv(carol_priv)
carol_keys = M.IdentityManager()
carol_keys.add_identity(carol_priv, "carol")
carol_keys.activate(carol)
carol_app = type("Carol", (), {})()
alice.store.upsert_contact(carol, "carol", carol_keys.public_key_hex)
sent = alice.forward_messages([got["id"]], [carol])
wait_for(lambda: relay.mail)
fwd = relay.mail.pop()
payload = M.CryptoEngine(carol_keys).open_envelope(
    fwd["envelope"], alice.keys.address, cid=fwd["client_msg_id"])
check("forwarding sends a copy marked as forwarded",
      sent == 1 and payload["body"] == "hi alice" and
      payload["x"].get("fwd") == 1 and payload["x"]["to"] == carol)

print("\nStars")
alice.star_messages([first], True)
check("a star is local only (nothing sent)",
      not relay.mail and alice.store.message(first)["starred"] == 1)

print("\nDelete for both")
mine = alice.store.messages_for(bob.keys.address)[0]
before_bob = bob.store.message_count(alice.keys.address)
alice.delete_messages([mine["id"]], for_everyone=True)
wait_for(lambda: relay.mail)
ctl = relay.mail[0]
check("delete-for-both sends a control, not a message",
      bob.crypto.open_envelope(ctl["envelope"], alice.keys.address,
                               cid=ctl["client_msg_id"])["x"]["k"] == "ctl")
deliver(relay, parties)
check("...which removes Bob's copy",
      bob.store.message_count(alice.keys.address) == before_bob - 1 and
      bob.store.message_by_cid(alice.keys.address,
                               mine["client_msg_id"]) is None)
wait_for(lambda: alice.store.message(mine["id"]) is None)
check("...and Alice's own copy once the relay accepted it",
      alice.store.message(mine["id"]) is None)
theirs = alice.store.messages_for(bob.keys.address)[-1]
alice.delete_messages([theirs["id"]], for_everyone=True)
time.sleep(0.3)
check("Bob's message cannot be deleted for both by Alice",
      not relay.mail and alice.store.message(theirs["id"]) is not None)
alice.delete_messages([theirs["id"]], for_everyone=False)
check("...but Alice can delete her copy of it",
      alice.store.message(theirs["id"]) is None and
      bob.store.message_by_cid(alice.keys.address,
                               theirs["client_msg_id"]) is not None)

print("\nPhotos: upload, download, expiry, resend")
from PIL import Image                                          # noqa: E402
src = os.path.join(os.environ["HOME"], "camera.jpg")
image = Image.new("RGB", (3000, 2000), (200, 120, 40))
exif = Image.Exif()
exif[0x0112] = 6                    # Orientation: rotate 90 CW on display
exif[0x8825] = {2: (1, 2, 3)}       # a GPS block, which must not survive
image.save(src, "JPEG", exif=exif.tobytes())
alice._prepare_image(bob.keys.address, src, "sunset", None)
check("the photo is uploaded as an opaque blob",
      len(relay.blobs) == 1 and
      b"sunset" not in next(iter(relay.blobs.values())))
out = alice.store.messages_for(bob.keys.address)[-1]
media = M.message_ext(out)["media"]
check("it was shrunk to fit 1280px, honouring the camera rotation",
      max(media["w"], media["h"]) <= M.IMAGE_MAX_EDGE and
      media["h"] > media["w"])
deliver(relay, parties)
row = bob.store.messages_for(alice.keys.address)[-1]
check("Bob gets a photo message pending download",
      row["kind"] == "image" and row["media_state"] == "pending")
pump(bob)
wait_for(lambda: bob.store.message(row["id"])["media_state"] == "ready")
row = bob.store.message(row["id"])
data = open(row["media_path"], "rb").read() if row["media_path"] else b""
check("...downloads, verifies and decrypts it", data[:2] == b"\xff\xd8")
with Image.open(row["media_path"]) as received:
    has_exif = bool(received.getexif())
check("...with the location and camera metadata stripped", not has_exif)


def photo_shown(app, peer, local_id):
    """Is the photo bubble drawing a picture (not an empty white box)?"""
    app.refresh_chat(peer)
    bubble = app.conversation_screen._bubbles.get(local_id)
    pic = bubble.media.picture if bubble is not None and bubble.media else None
    return pic is not None and pic.texture is not None and \
        tuple(pic.texture.size) == (media["w"], media["h"])


alice.open_conversation(bob.keys.address)
bob.open_conversation(alice.keys.address)
shown = [photo_shown(alice, bob.keys.address, out["id"]),
         photo_shown(bob, alice.keys.address, row["id"])]
sid = alice.store.message(out["id"])["server_id"]
for status in ("delivered", "read"):        # each tick repaints the bubble
    alice.store.apply_receipt(sid, status)
    shown.append(photo_shown(alice, bob.keys.address, out["id"]))
bob.store.set_starred([row["id"]], True)    # so does a star
shown += [photo_shown(bob, alice.keys.address, row["id"]) for _ in range(3)]
shown.append(alice.store.message(out["id"])["status"] == 3)
check("the photo stays a picture through every repaint, on both sides "
      "(9.1.1 turned it white on the first)", all(shown), str(shown))

blob_id = media.get("blob") or M.message_ext(out)["media"]["blob"]
relay.expired.add(next(iter(relay.blobs)))
alice._prepare_image(bob.keys.address, src, "", None)
deliver(relay, parties)
late = bob.store.messages_for(alice.keys.address)[-1]
relay.expired.update(relay.blobs)          # the relay let everything lapse
pump(bob)
wait_for(lambda: bob.store.message(late["id"])["media_state"] == "expired")
check("an expired blob is reported as expired, not as broken",
      bob.store.message(late["id"])["media_state"] == "expired")
bob.request_resend(late["id"])
wait_for(lambda: relay.mail)
deliver(relay, parties)                       # Alice receives the request
relay.expired.clear()
pump(alice)                                   # Alice re-uploads + 'remedia'
wait_for(lambda: relay.mail)
deliver(relay, parties)                       # Bob receives the new ref
pump(bob)
wait_for(lambda: bob.store.message(late["id"])["media_state"] == "ready")
check("asking again makes the sender re-upload, and it downloads",
      bob.store.message(late["id"])["media_state"] == "ready")
check("...into the SAME message, not a duplicate",
      len([m for m in bob.store.messages_for(alice.keys.address)
           if m["kind"] == "image"]) == 2)

print("\nVoice notes")
rec = os.path.join(str(M.MEDIA_DIR), "rec_test.m4a")
os.makedirs(str(M.MEDIA_DIR), exist_ok=True)
open(rec, "wb").write(b"\x00\x00\x00\x18ftypM4A " + os.urandom(4000))
alice.conversation_screen.reply_to = None
alice.send_voice(rec, 4200, M.waveform_b64([10, 50, 90, 30]))
wait_for(lambda: relay.mail)
deliver(relay, parties)
voice = bob.store.messages_for(alice.keys.address)[-1]
check("a voice note arrives with its length and waveform",
      voice["kind"] == "voice" and voice["body"] == "Voice message (0:04)"
      and M.message_ext(voice)["media"]["dur"] == 4200 and
      M.message_ext(voice)["media"].get("wave"))
check("the recording file is removed after sending (a copy is kept)",
      not os.path.exists(rec))

print("\nNotifications")
seen = []
M.android_notify = lambda peer, title, text: seen.append((peer, title, text))
M.IS_ANDROID = True
bob.foreground, bob.current_peer = False, None
bob.manager.current = "chats"
bob.notify_incoming(alice.keys.address, "ping", "text")
check("a message in the background raises a notification",
      seen and seen[-1][1] == "alice" and seen[-1][2] == "ping")
bob.foreground, bob.current_peer = True, alice.keys.address
bob.manager.current = "conversation"
n = len(seen)
bob.notify_incoming(alice.keys.address, "pong", "text")
check("...but not while that very chat is on screen", len(seen) == n)
bob.foreground = False
bob.store.set_contact_setting(alice.keys.address, "notify", 0)
bob.notify_incoming(alice.keys.address, "muted?", "text")
check("...nor for a muted chat", len(seen) == n)
M.IS_ANDROID = False

print("\nv9.1: notes to self")
me = alice.keys.address
before = len(relay.mail)
note_id = alice.send_text(me, "buy stamps 📮")
time.sleep(0.2)
check("a note to self is kept and goes nowhere",
      note_id and len(relay.mail) == before and
      alice.store.message(note_id)["body"] == "buy stamps 📮")
kinds = {c["peer"]: c["kind"] for c in alice.store.conversations(me=me)}
check("...and is listed as the notes-to-self conversation",
      kinds.get(me) == "self")
alice.open_conversation(me)
screen = alice.conversation_screen
check("the screen knows it is notes to self, and shows no ticks",
      screen.kind == "self" and screen.peer_name.text == "Saved messages" and
      all(b.ticks.width == 0 for b in screen._bubbles.values()))

print("\nv9.1: three parties, a group, reactions")
cara = make_party("cara", relay)
parties[cara.keys.address] = cara
for x in (alice, bob, cara):
    for y in (alice, bob, cara):
        if x is not y:
            x.store.upsert_contact(y.keys.address, y.keys.username,
                                   y.keys.public_key_hex)
A, B, K = alice.keys.address, bob.keys.address, cara.keys.address
relay.mail.clear()
gpeer = alice.create_group("The Reading Circle", [B, K])
gid = M.gid_of(gpeer)
wait_for(lambda: len(relay.mail) >= 2)
check("creating a group invites each member with one sealed message",
      sorted(m["recipient"] for m in relay.mail) == sorted([B, K]))
deliver(relay, parties)
for party in (bob, cara):
    group = party.store.group(gid) or {}
    pump(party)
check("both members joined, with alice as admin and all three listed",
      all((p.store.group(gid) or {}).get("state") == "active" and
          (p.store.group(gid) or {}).get("admin") == A and
          len((p.store.group(gid) or {}).get("members", [])) == 3
          for p in (bob, cara)))
check("...and the group is in their chat lists",
      any(c["peer"] == gpeer and c["kind"] == "group"
          for c in bob.store.conversations(me=B)))

bob.current_peer = gpeer
gm = bob.send_text(gpeer, "Chapter four next week? 📖")
wait_for(lambda: len(relay.mail) >= 2)
check("a group message goes to every other member, one copy each",
      sorted(m["recipient"] for m in relay.mail) == sorted([A, K]))
deliver(relay, parties)
row = cara.store.messages_for(gpeer)[-1]
check("it arrives in the group, signed by its author",
      row["body"] == "Chapter four next week? 📖" and row["author"] == B)
check("bob's copy tracks delivery per member",
      len(bob.store.group_sends(gm)) == 2)
# v12 (S9): where it has got to, member by member, from its ticks.
for member, tier in ((A, 3), (K, 2)):
    bob.store._db.execute("UPDATE group_sends SET status = ? WHERE "
                          "local_id = ? AND member = ?", (tier, gm, member))
bob.store._db.commit()
bob.open_conversation(gpeer)
pump(bob)
bscreen = bob.conversation_screen
bbubble = bscreen._bubbles[gm]
wait_for(lambda: bbubble.meta_row.width > 0, 1.0)


class Tap:
    pos = bbubble.meta_row.center


bscreen.on_bubble_tap(gm, Tap())
infos = [w for w in M.Window.children if isinstance(w, M.MessageInfoSheet)]
check("in a group, the time and ticks of your message show where it has "
      "got to: read by alice, delivered to cara (S9)",
      infos and infos[0].counts == {3: 1, 2: 1, 1: 0, 0: 0},
      str(infos[0].counts if infos else None))
for sheet in infos:
    sheet.dismiss(animation=False)


class Elsewhere:
    pos = (bbubble.body.center_x, bbubble.body.center_y)


bscreen.on_bubble_tap(gm, Elsewhere())
check("...while a tap anywhere else on it still raises the reactions",
      bscreen._strip_for == gm and not [
          w for w in M.Window.children
          if isinstance(w, M.MessageInfoSheet)])
bscreen.close_strip()
title, text, muted = M.notification_text(cara.store, gpeer, row["body"],
                                         "text", row["author"])
check("a group notification is titled with the group and names the writer",
      title == "The Reading Circle" and text.startswith("bob: ") and
      not muted)

cara.current_peer = gpeer
cara.react(row["id"], "👍")
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
cid = row["client_msg_id"]
check("a reaction reaches everyone in the group",
      all((p.store.reactions_for(gpeer, [cid]).get(cid) or [[None]])[0][:2]
          == ("👍", K) for p in (alice, bob)))
alice.open_conversation(gpeer)
pump(alice)
bubble = next(b for b in alice.conversation_screen._bubbles.values()
              if b.msg.get("client_msg_id") == cid)
check("...and shows under the message, with the author's name above it",
      [c.emoji for c in bubble.reactions.chips] == ["👍"] and
      bubble.author is not None and bubble.author.text == "BOB")
cara.react(row["id"], "👍")
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
pump(alice)
alice.refresh_chat(gpeer)
bubble = next(b for b in alice.conversation_screen._bubbles.values()
              if b.msg.get("client_msg_id") == cid)
check("reacting the same again takes it back, everywhere",
      not bob.store.reactions_for(gpeer, [cid]) and not bubble.reactions.chips)

screen = alice.conversation_screen
screen.start_selection(bubble.msg["id"])
check("holding a message raises the reaction strip",
      not screen.strip.disabled and
      wait_for(lambda: screen.strip.opacity == 1, 1.0))
screen._strip_pick("🙏")
wait_for(lambda: len(relay.mail) >= 2)
check("...and picking from it reacts and closes the strip",
      alice.store.reaction_of(gpeer, cid, A) == "🙏" and
      screen.strip.disabled and not screen.selecting)
deliver(relay, parties)

# v12: several reactions each, up to three; a fourth replaces the oldest.
for emoji in ("❤️", "😂"):
    alice.react(bubble.msg["id"], emoji)
    wait_for(lambda: len(relay.mail) >= 2)
    to_bob = [m for m in relay.mail if m["recipient"] == B]
    deliver(relay, parties)
check("several reactions from one person reach everyone, in order (v12)",
      all(p.store.reactions_of(gpeer, cid, A) == ["🙏", "❤️", "😂"]
          for p in (alice, bob, cara)))
ctl = bob.crypto.open_envelope(to_bob[-1]["envelope"], A,
                               cid=to_bob[-1]["client_msg_id"])["x"]["ctl"]
check("...each 'react' carries all of them, and the latest alone for older "
      "apps", ctl.get("es") == ["🙏", "❤️", "😂"] and ctl.get("e") == "😂",
      str(ctl))
alice.refresh_chat(gpeer)
pump(alice)
bubble = next(b for b in alice.conversation_screen._bubbles.values()
              if b.msg.get("client_msg_id") == cid)
check("...each shows under the message as a chip of its own",
      [c.emoji for c in bubble.reactions.chips] == ["🙏", "❤️", "😂"])
screen.start_selection(bubble.msg["id"])
wait_for(lambda: False, 0.4)
# Placed over another message, the strip moves before its keys are laid out
# again: the rings must follow the keys (they were left behind, before 12).
screen.strip.pos = (screen.strip.x + M.dp(30), screen.strip.y + M.dp(40))
wait_for(lambda: False, 0.4)
check("...and the strip rings each one already given",
      screen.strip.current == ["🙏", "❤️", "😂"] and
      sorted(r.pos for c, r in screen.strip._mine if c.a > 0) ==
      sorted(((k.x + k.width / 2) - M.dp(20), (k.y + k.height / 2) - M.dp(20))
             for e, k in screen.strip.keys.items() if e in ("🙏", "❤️", "😂")),
      f"{screen.strip.current} {[(r.pos, c.a) for c, r in screen.strip._mine]}")
screen.clear_selection()
screen.strip.show([])
check("...and none is left over when the strip next rises where you have "
      "given none", not any(c.a > 0 for c, _r in screen.strip._mine))
screen.strip.hide()
alice.react(bubble.msg["id"], "😮")
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("a fourth replaces the oldest of the three",
      bob.store.reactions_of(gpeer, cid, A) == ["❤️", "😂", "😮"])
alice.react(bubble.msg["id"], "😂")
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("choosing one of yours again takes back that one only",
      cara.store.reactions_of(gpeer, cid, A) == ["❤️", "😮"] and
      cara.store.reaction_of(gpeer, cid, A) == "😮")

# v12 (S8): the reactions you give most come first in the strip.
for n, emojis in enumerate((["🍝"], ["🍝", "🎉"], ["🍝"])):
    alice.store.set_reactions(gpeer, f"fav{n}" * 8, A, emojis)
screen.start_selection(bubble.msg["id"])
offered = list(screen.strip.keys)
check("the reactions you give most come first in the strip (S8)",
      offered[0] == "🍝" and offered[1] in ("🎉", "❤️", "😮") and
      len(offered) == 6 and "🎉" in offered, str(offered))
favourites = alice.store.favourite_reactions(A)
rest = [e for e in offered if e not in favourites]
check("...the usual ones fill the rest, in their order",
      offered[:len(offered) - len(rest)] == favourites[:6 - len(rest)] and
      rest == [e for e in M.QUICK_REACTIONS if e not in favourites][
          :len(rest)], f"{offered} {favourites}")
screen.strip.keys["🍝"].dispatch("on_release")
wait_for(lambda: len(relay.mail) >= 2)
check("...and a key of the strip so made reacts as any other",
      "🍝" in alice.store.reactions_of(gpeer, cid, A))
deliver(relay, parties)
screen.open_emoji()
screen.entry.text = ""
screen.emoji_panel._pick("🙂")
check("the emoji panel writes into the message",
      screen.entry.text == "🙂" and screen.emoji_panel.is_open)
screen.close_emoji()
screen.entry.text = ""

alice.rename_group(gid, "The Saturday Walkers")
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("the admin renames the group for everyone",
      all(p.store.group(gid)["name"] == "The Saturday Walkers"
          for p in (bob, cara)))
alice.remove_group_member(gid, K)
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("removing a member tells them and the others",
      cara.store.group(gid)["state"] == "removed" and
      [m["a"] for m in bob.store.group(gid)["members"]] == [A, B])
cara.open_conversation(gpeer)
check("...and their copy of the group becomes read-only",
      cara.conversation_screen.closed_note.parent is not None and
      cara.send_text(gpeer, "still here?") is None)
bob.leave_group(gid)
wait_for(lambda: relay.mail)
deliver(relay, parties)
check("leaving tells the rest of the group",
      [m["a"] for m in alice.store.group(gid)["members"]] == [A] and
      bob.store.group(gid)["state"] == "left")

print("\nv9.1: a reaction in a direct chat, and the themes")
relay.mail.clear()
direct = bob.store.messages_for(A)[-1]
bob.react(direct["id"], "❤️")
wait_for(lambda: relay.mail)
deliver(relay, parties)
dcid = direct["client_msg_id"]
check("a direct chat's reaction arrives",
      alice.store.reaction_of(B, dcid, B) == "❤️")
alice.open_conversation(B)
alice.conversation_screen.entry.text = "half-written"
alice.set_appearance(theme="light")
check("switching theme rebuilds the app in the light theme",
      M.T.name == "light" and alice.conversation_screen.peer == B)
check("...keeping the open chat and its draft",
      alice.manager.current == "conversation" and
      alice.conversation_screen.entry.text == "half-written")
alice.set_appearance(text_size="largest")
check("a larger text size scales every font",
      abs(M.APPEARANCE.scale - 1.3) < 1e-9 and
      abs(M.fs(10) - M.sp(13)) < 0.01)
alice.set_appearance(theme="dark", text_size="default")
check("...and back (dark, on black by default)",
      M.T.name == "black" and M.T.dark and M.APPEARANCE.scale == 1.0)

print("\nv14: the outbox (B15), a full relay (B10), counted uploads (B2)")
api = alice.api
relay.mail.clear()
first_photo = M.message_ext(alice.store.message(out["id"]))["media"]
check("an upload says how many will fetch it: one, in a direct chat",
      relay.expects.get(first_photo["blob"]) == 1)


def attempts(who):
    return api.requests.count(("send", who))


def settled(local_id):
    return local_id not in alice._sends()[1]


api.down = True
tried = attempts(B)
waiting = alice.send_text(B, "are you there?")
wait_for(lambda: attempts(B) > tried and settled(waiting))
pump(alice)
check("a message the relay does not take stays here at its clock, not "
      "'Send failed'",
      alice.store.message(waiting)["status"] == 0 and not relay.mail and
      "Not sent yet" in alice.chats_screen.status_label.text,
      alice.chats_screen.status_label.text)
api.down = False
check("...and the outbox sends it once the relay answers",
      alice.flush_outbox() == 1 and
      alice.store.message(waiting)["status"] == 1 and
      [m["recipient"] for m in relay.mail] == [B])
deliver(relay, parties)
check("...arriving once",
      sum(m["body"] == "are you there?"
          for m in bob.store.messages_for(A)) == 1)

api.down = True
tried = attempts(B)
old = alice.send_text(B, "from yesterday")
wait_for(lambda: attempts(B) > tried and settled(old))
yesterday = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
alice.store._db.execute("UPDATE messages SET created_at = ? WHERE id = ?",
                        (yesterday, old))
alice.store._db.commit()
api.down = False
check("after a day it no longer goes by itself",
      alice.flush_outbox() == 0 and alice.store.message(old)["status"] == 0)
alice.open_conversation(B)
wait_for(lambda: old in alice.conversation_screen._bubbles)
bubble = alice.conversation_screen._bubbles.get(old)
check("...its bubble says 'Not sent · tap to try again'",
      bubble is not None and "Not sent · tap to try again" in bubble.meta.text,
      repr(bubble and bubble.meta.text))
alice.conversation_screen.on_bubble_tap(old)
wait_for(lambda: alice.store.message(old)["status"] == 1)
check("...and a tap sends it", alice.store.message(old)["status"] == 1)
deliver(relay, parties)
alice.go_back_to_chats()

alice.set_chat_timer(B, 3600)
wait_for(lambda: relay.mail)
deliver(relay, parties)
api.media_full = True
alice._prepare_image(B, src, "when there is room", None)
pic = alice.store.messages_for(B)[-1]
pump(alice)
check("a photo the relay has no room for waits on the phone (B10)",
      pic["media_state"] == "waiting" and not relay.mail and
      "full" in alice.chats_screen.status_label.text)
api.media_full = False
alice.flush_outbox()
pic = alice.store.message(pic["id"])
check("...and goes once there is room",
      pic["media_state"] == "ready" and len(relay.mail) == 1)
deliver(relay, parties)
got = bob.store.messages_for(A)[-1]
check("a photo in a disappearing chat carries its timer (a fix: 13.0's "
      "did not)", got["kind"] == "image" and got["ttl"] == 3600)
alice.set_chat_timer(B, 0)
wait_for(lambda: relay.mail)
deliver(relay, parties)

print("\nv14: a group message in one request (B1)")
for party in (alice, bob, cara):
    party.api._features = {"blobs": True, "send_many": True}
relay.mail.clear()
g14 = alice.create_group("Fourteen", [B, K])
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
for party in (bob, cara):
    pump(party)
api.requests.clear()
one = alice.send_text(g14, "all of you at once")
wait_for(lambda: len(relay.mail) >= 2)
check("a group message goes in one request, a copy for each member",
      api.requests.count("send_many") == 1 and
      not any(isinstance(r, tuple) and r[0] == "send"
              for r in api.requests) and
      sorted(m["recipient"] for m in relay.mail) == sorted([B, K]),
      str(api.requests))
check("...each member's copy recorded",
      all(row["server_id"] for row in alice.store.group_sends(one)))
deliver(relay, parties)
api.refuse = {K}
two = alice.send_text(g14, "and again")
wait_for(lambda: relay.mail and settled(two))
check("a member the relay refused keeps the message at its clock",
      alice.store.message(two)["status"] == 0 and
      [m["recipient"] for m in relay.mail] == [B])
deliver(relay, parties)
api.refuse = set()
alice.flush_outbox()
check("...and the outbox sends only that member's copy",
      [m["recipient"] for m in relay.mail] == [K])
deliver(relay, parties)
check("...so each has it once",
      [m["body"] for m in cara.store.messages_for(g14)].count("and again")
      == 1 and [m["body"] for m in bob.store.messages_for(g14)].count(
          "and again") == 1)
alice._prepare_image(g14, src, "for the group", None)
gpic = M.message_ext(alice.store.messages_for(g14)[-1])["media"]
check("an upload for a group counts its other members (B2)",
      relay.expects.get(gpic["blob"]) == 2)
deliver(relay, parties)

print("\nv14: download rules (B14)")
big = uuid.uuid4().hex
bob.store.save_group({"id": big, "name": "Big", "admin": A, "v": 1,
                      "members": [{"a": f"Fec{i:030d}", "k": ""}
                                  for i in range(M.LARGE_GROUP)] +
                      [{"a": B, "k": ""}]})
photo_in = {"peer": M.group_peer(big), "kind": "image"}
check("in a group of more than LARGE_GROUP, a photo waits for a tap",
      not M.downloads_by_itself(bob.store, photo_in))
on_data = M.on_metered_network
M.on_metered_network = lambda: True
M.save_pref("dl_photos_metered", False)
check("on mobile data photos wait when told to, voice notes come",
      not M.downloads_by_itself(bob.store, {"peer": A, "kind": "image"}) and
      M.downloads_by_itself(bob.store, {"peer": A, "kind": "voice"}))
M.save_pref("dl_photos_metered", True)
check("...and come by themselves by default",
      M.downloads_by_itself(bob.store, {"peer": A, "kind": "image"}))
M.on_metered_network = on_data

print("\nv14: message requests (B16)")
dave = make_party("dave", relay)
D = dave.keys.address
parties[D] = dave
dave.store.upsert_contact(A, alice.keys.username, alice.keys.public_key_hex)
relay.mail.clear()
dave.send_text(A, "hello, we met at the fair")
wait_for(lambda: relay.mail)
deliver(relay, parties)
conv = next(c for c in alice.store.conversations(me=A) if c["peer"] == D)
check("a first message from someone new waits as a request",
      conv["request"] and alice.store.is_request(D))
check("...the relay was told to send them no receipts before it was "
      "acknowledged", api.ticks.get(D) is False and
      alice.store.get_contact(D)["ticks_enabled"] == 0)
check("...its notification says only that it is a request",
      M.notification_text(alice.store, D, "hello, we met at the fair",
                          "text")[1] == "Message request")
check("...and a call from them is refused",
      alice.worker._as_request(99, D, "dave", {"op": "call"}) == (True, 99))
alice.refresh_sidebar()
chats = alice.chats_screen
check("the list shows a way into requests, not the chat itself",
      any(isinstance(w, M.ArchiveRow) for w in chats.list.children) and
      D not in chats._rows)
chats.show_view("requests")
check("...which lists it", D in chats._rows)
chats.show_view("chats")
alice.open_conversation(D)
pump(alice)
screen = alice.conversation_screen
check("in the chat, Accept, Decline and Report stand where the composer "
      "was", screen.request_note.parent is not None)
alice.accept_request(D)
pump(alice)
check("accepting turns their receipts back on and gives the composer back",
      not alice.store.is_request(D) and api.ticks.get(D) is True and
      screen.request_note.parent is None)
alice.go_back_to_chats()

erin = make_party("erin", relay)
E = erin.keys.address
parties[E] = erin
erin.store.upsert_contact(A, alice.keys.username, alice.keys.public_key_hex)
erin.send_text(A, "a great offer")
wait_for(lambda: relay.mail)
deliver(relay, parties)
alice.decline_request(E)
check("declining removes the request without blocking them",
      not any(c["peer"] == E for c in alice.store.conversations(me=A)) and
      not alice.store.is_blocked(E) and alice.store.is_request(E))
erin.send_text(A, "a greater offer")
wait_for(lambda: relay.mail)
deliver(relay, parties)
check("...so if they write again, it is a request again",
      next(c for c in alice.store.conversations(me=A)
           if c["peer"] == E)["request"])
api._features["reports"] = True
alice.report_contact(E, "spam", "twice now", True)
wait_for(lambda: ("report", E, "spam", "twice now") in api.requests)
check("reporting tells the relay who and why, and blocks them",
      ("report", E, "spam", "twice now") in api.requests and
      alice.store.is_blocked(E))
check("...after which the request is gone from Requests",
      not any(c["peer"] == E and c.get("request") and not c.get("blocked")
              for c in alice.store.conversations(me=A)))
check("the blocked list has them, to unblock",
      [p["address"] for p in alice.store.blocked_contacts()] == [E])
alice.set_blocked([E], False)
check("...and unblocking takes them off it",
      alice.store.blocked_contacts() == [])

print("\nv14: a relay restarting (P3)")
worker = alice.worker
worker._handle_frame({"type": "restart", "seconds": 40})
check("the relay asks phones to come back later, and is obeyed",
      worker._restart_in == 40)
worker._handle_frame({"type": "restart", "seconds": 86400})
check("...for five minutes at most",
      worker._restart_in == M.RESTART_WAIT_MAX)
worker._restart_in = 0


class Paused:
    status_code = 503
    headers = {"Retry-After": "60"}

    @staticmethod
    def json():
        return {"error": {"code": "paused", "message": "paused"}}


try:
    M.ApiClient(alice.keys)._handle(Paused())
    asked = None
except M.ApiClient.ApiError as exc:
    asked = exc.detail.get("retry_after")
check("...and a paused relay's Retry-After is kept", asked == 60)

print("\nv15: post-quantum encryption")
fern, gus, hal = (make_party(n, relay) for n in ("fern", "gus", "hal"))
F, G, H = fern.keys.address, gus.keys.address, hal.keys.address
parties.update({F: fern, G: gus, H: hal})
for me, other in ((fern, gus), (gus, fern), (fern, hal), (hal, fern)):
    me.store.upsert_contact(other.keys.address, other.keys.username,
                            other.keys.public_key_hex)
relay.mail.clear()


def seen(msg):
    """(sealed post-quantum, to which key id, the x-block): a relayed
    envelope opened as its recipient would, nothing stored."""
    raw = M.b64d(msg["envelope"]["ciphertext"])
    payload = parties[msg["recipient"]].crypto.open_envelope(
        msg["envelope"], msg["sender"], cid=msg["client_msg_id"])
    pq = raw[:4] == M.PQ_MAGIC
    return pq, raw[4:12].hex() if pq else None, payload.get("x") or {}


def hint(app, peer):
    return app.chat_info_screen._pq_row(peer).hint.text


check("before anyone writes, chat info says it is off",
      hint(fern, G).startswith("Off"))
fern.send_text(G, "first, as before")
wait_for(lambda: relay.mail)
pq, _kid, x = seen(relay.mail[0])
check("a first message is sealed as before, with the sender's "
      "post-quantum key in its signed block",
      not pq and x.get("pq") == fern.keys.pq_kid and
      M.b64d(x.get("pqe", "")) == fern.keys.pq_public)
deliver(relay, parties)
check("...which the recipient keeps",
      gus.store.pq_key(F) == (fern.keys.pq_public, fern.keys.pq_kid))
check("...so their chat info says theirs are sealed now",
      hint(gus, F) == "On for yours · theirs after your next message")
gus.send_text(F, "the reply")
wait_for(lambda: relay.mail)
pq, kid, x = seen(relay.mail[0])
check("the reply is post-quantum, sealed to that key, and brings the "
      "replier's", pq and kid == fern.keys.pq_kid and "pqe" in x)
deliver(relay, parties)
check("...and is shown as such: both ways are on",
      hint(fern, G) == "On, both ways" and
      fern.store.pq_mine(G) == fern.keys.pq_kid)
fern.send_text(G, "third")
wait_for(lambda: relay.mail)
pq, kid, x = seen(relay.mail[0])
check("after that, keys are not sent again",
      pq and kid == gus.keys.pq_kid and "pq" in x and "pqe" not in x)
deliver(relay, parties)
check("...and the receiving side agrees", hint(gus, F) == "On, both ways")

garden = fern.create_group("Allotment", [G, H])
wait_for(lambda: len(relay.mail) >= 2)
copies = {m["recipient"]: seen(m) for m in relay.mail}
check("in a group, each member's copy is sealed as their own chat is: "
      "post-quantum to one whose key is held, as before to a new one",
      copies[G][0] and not copies[H][0] and "pqe" in copies[H][2])
deliver(relay, parties)
pump(gus)
pump(hal)
hal.send_text(garden, "where are the spades?")
wait_for(lambda: len(relay.mail) >= 2)
copies = {m["recipient"]: seen(m) for m in relay.mail}
check("a member's first group message is post-quantum to the admin, whose "
      "key came with the invitation",
      copies[F][0] and not copies[G][0])
deliver(relay, parties)
check("...and members who met only in the group now hold each other's key",
      gus.store.pq_key(H) is not None)

# A new install of gus, from his WIF alone: no chats, and a new
# post-quantum key. Fern still holds the old one.
old_kid = gus.keys.pq_kid
gus.keys.find(G).pop("pq_seed_hex")
gus.keys._pq.clear()
gus.store = M.LocalStore(M.Path(tempfile.mkdtemp(prefix="fext-new-")) /
                         "store.db")
relay.mail.clear()
fern.send_text(G, "sealed to the old key")
wait_for(lambda: relay.mail)
first_copy = dict(relay.mail[0])
lost_cid = first_copy["client_msg_id"]
check("(fern's message is sealed to gus's old key)",
      M.b64d(first_copy["envelope"]["ciphertext"])[4:12].hex() == old_kid and
      gus.keys.pq_kid != old_kid)
relay.mail.clear()
acked = gus.worker._ingest_message(first_copy)
wait_for(lambda: relay.mail)
asked = relay.mail[0] if relay.mail else {}
_pq, _kid, x = seen(asked) if asked else (None, None, {})
check("the new install asks for it again, sending its new key",
      acked is None and asked.get("recipient") == F and
      x.get("ctl") == {"op": "reseal", "cids": [lost_cid]} and
      M.b64d(x.get("pqe", "")) == gus.keys.pq_public)
deliver(relay, parties)
wait_for(lambda: relay.mail)
again = relay.mail[0] if relay.mail else {}
check("...and the sender sends the same message again, sealed to it",
      again.get("client_msg_id") == lost_cid and
      seen(again)[:2] == (True, gus.keys.pq_kid))
deliver(relay, parties)
held = gus.store.message_by_cid(F, lost_cid)
check("...which the new install opens",
      held is not None and held["body"] == "sealed to the old key")
check("when the old copy comes round again it is let go, with no warning "
      "beside the message", gus.worker._ingest_message(first_copy) ==
      first_copy["id"] and [m["body"] for m in gus.store.messages_for(F)]
      == ["sealed to the old key"])
fern.reseal(G, [lost_cid])
fern.reseal(H, [lost_cid])
old = next(m for m in fern.store.messages_for(G)
           if m["body"] == "first, as before")["client_msg_id"]
with fern.store._lock:
    fern.store._db.execute(
        "UPDATE messages SET created_at = ? WHERE client_msg_id = ?",
        ((datetime.now(timezone.utc) - timedelta(days=8)).isoformat(), old))
    fern.store._db.commit()
fern.reseal(G, [old])
time.sleep(0.5)
check("a message is sent again once only, only to the person it was for, "
      "and only within a week", not relay.mail)

print("\nIdentity rename")
old_user = alice.keys.username
alice.keys.rename_identity(alice.keys.address, "Work")
reload = M.IdentityManager()
check("renaming changes the label and persists it, not the username",
      reload.find(alice.keys.address)["label"] == "Work" and
      reload.find(alice.keys.address).get("username") == old_user)

passed = sum(1 for _n, c in RESULTS if c)
print(f"\n  {passed}/{len(RESULTS)} passed")
os._exit(0 if passed == len(RESULTS) else 1)
