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
import json
import os
import sys
import tempfile
import time
import uuid

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

    def features(self):
        return self._features

    def send_message(self, recipient, envelope, client_msg_id):
        return {"server_id": self.relay.post(self.keys.address, recipient,
                                             envelope, client_msg_id)}

    def upload_blob(self, blob):
        blob_id = uuid.uuid4().hex
        self.relay.blobs[blob_id] = blob
        return {"blob": blob_id}

    def download_blob(self, blob_id):
        if blob_id in self.relay.expired or blob_id not in self.relay.blobs:
            raise M.ApiClient.ApiError(410, "expired", "media expired")
        return self.relay.blobs[blob_id]

    def read(self, ids):
        return {"updated": len(ids)}

    def set_ticks(self, peer, enabled):
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
bob._notify_incoming(alice.keys.address, "ping", "text")
check("a message in the background raises a notification",
      seen and seen[-1][1] == "alice" and seen[-1][2] == "ping")
bob.foreground, bob.current_peer = True, alice.keys.address
bob.manager.current = "conversation"
n = len(seen)
bob._notify_incoming(alice.keys.address, "pong", "text")
check("...but not while that very chat is on screen", len(seen) == n)
bob.foreground = False
bob.store.set_contact_setting(alice.keys.address, "notify", 0)
bob._notify_incoming(alice.keys.address, "muted?", "text")
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
      screen.kind == "self" and screen.peer_name.text == "Notes to self" and
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
check("...and back", M.T.name == "dark" and M.APPEARANCE.scale == 1.0)

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
