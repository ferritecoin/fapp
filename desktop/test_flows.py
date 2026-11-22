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
import threading
import time
from pathlib import Path
import uuid
from datetime import datetime, timedelta, timezone

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
if sys.platform.startswith("linux"):     # xvfb-run's X display
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-flows-")
os.environ["USERPROFILE"] = os.environ["HOME"]   # Path.home() on Windows
os.environ["FEXT_LANG"] = "en"          # the English the checks read
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
        self.forgotten = set()    # 16.0: no record of them (a reset relay)
        self._next = 1
        self._lock = threading.Lock()   # one number each, as a relay's are

    def post(self, sender, recipient, envelope, cid):
        with self._lock:
            sid = self._next
            self._next += 1
            self.mail.append({"id": sid, "sender": sender,
                              "recipient": recipient, "envelope": envelope,
                              "client_msg_id": cid,
                              "created_at": M.now_iso()})  # the relay's time
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

    clock_offset = 0.0              # the relay's clock less this device's

    def relay_now(self):
        return time.time() + self.clock_offset

    def local_time(self, at):
        return at - self.clock_offset

    def _reach(self, what):
        self.requests.append(what)
        if self.down:
            raise M.requests.ConnectionError("the relay is out of reach")

    def send_message(self, recipient, envelope, client_msg_id):
        self._reach(("send", recipient))
        if recipient in self.relay.forgotten:
            raise M.ApiClient.ApiError(404, "not_found",
                                       "recipient not found")
        return {"server_id": self.relay.post(self.keys.address, recipient,
                                             envelope, client_msg_id)}

    def send_many(self, items):
        self._reach("send_many")
        return [{"error": {"code": "mailbox_full", "message": "full"}}
                if item["recipient"] in self.refuse else
                {"error": {"code": "not_found",
                           "message": "recipient not found"}}
                if item["recipient"] in self.relay.forgotten else
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

    def room_get(self, what, params):       # no call rooms in this relay
        raise M.requests.ConnectionError("no rooms here")

    def room_post(self, what, body):
        raise M.requests.ConnectionError("no rooms here")

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

print("\n17.0: delete for everyone within three days (F11)")
alice.send_text(bob.keys.address, "an old note")
wait_for(lambda: relay.mail)
deliver(relay, parties)
old = alice.store.messages_for(bob.keys.address)[-1]
four_days = (M.datetime.now(M.timezone.utc) -
             M.timedelta(days=4)).isoformat(timespec="seconds")
for store, direction in ((alice.store, "out"), (bob.store, "in")):
    with store._lock:
        store._db.execute("UPDATE messages SET created_at = ? WHERE "
                          "client_msg_id = ? AND direction = ?",
                          (four_days, old["client_msg_id"], direction))
old = alice.store.message(old["id"])
sheet = M.DeleteModal(alice, [old])
labels = [w.text for w in sheet.walk() if isinstance(w, M.Label)]
check("older than three days: only for me, and the sheet says why",
      not any("everyone" in t or "Delete for me and" in t
              for t in labels) and
      any("older than three days" in t for t in labels), labels)
relay.mail.clear()
alice.delete_messages([old["id"]], for_everyone=True)
time.sleep(0.3)
check("...nor can it be sent for everyone from anywhere else",
      not relay.mail and alice.store.message(old["id"]) is not None)
alice._send_control(bob.keys.address, {"op": "delete",
                                       "cids": [old["client_msg_id"]]},
                    "Deleted a message")
wait_for(lambda: relay.mail)
deliver(relay, parties)
check("...and an app that sends it anyway has it ignored: Bob keeps his copy",
      bob.store.message_by_cid(alice.keys.address, old["client_msg_id"])
      is not None)
alice.delete_messages([old["id"]], for_everyone=False)

print("\n16.2: editing a message (I5)")
draft = alice.send_text(bob.keys.address, "see you at 6")
wait_for(lambda: relay.mail)
deliver(relay, parties)
wait_for(lambda: alice.store.message(draft)["status"] >= 1)
sent_row = alice.store.message(draft)
bobs = next(m for m in bob.store.messages_for(alice.keys.address)
            if m["direction"] == "out")              # "hi alice", his
check("your own text, taken by the relay, can be edited; theirs cannot",
      alice.can_edit(sent_row) and not alice.can_edit(dict(
          sent_row, direction="in")))
check("...and Bob learns Alice's app shows edits",
      bob.store.get_contact(alice.keys.address)["caps"] &
      M.LocalStore.CAP_EDIT)
alice.edit_message(draft, "see you at 7")
wait_for(lambda: relay.mail)
deliver(relay, parties)
wait_for(lambda: alice.store.message(draft)["body"] == "see you at 7")
bob_row = bob.store.message_by_cid(alice.keys.address,
                                   sent_row["client_msg_id"])
check("an edit goes as a control; both copies read the new words, marked "
      "edited", bob_row["body"] == "see you at 7" and
      M.message_ext(bob_row).get("ed") and
      M.message_ext(alice.store.message(draft)).get("ed"))
bob.worker._handle_control(1, alice.keys.address, M.uuid.uuid4().hex,
                           {"op": "edit", "cid": bobs["client_msg_id"],
                            "b": "not mine"}, "alice", sent_at=M.now_iso())
check("an edit of a message its sender did not write changes nothing",
      bob.store.message(bobs["id"])["body"] == bobs["body"])
late = M.datetime.fromisoformat(sent_row["created_at"]) + \
    M.timedelta(seconds=M.EDIT_WINDOW + 120)
bob.worker._handle_control(2, alice.keys.address, M.uuid.uuid4().hex,
                           {"op": "edit", "cid": sent_row["client_msg_id"],
                            "b": "too late"}, "alice",
                           sent_at=late.isoformat())
check("...nor one made after its fifteen minutes",
      bob.store.message_by_cid(alice.keys.address,
                               sent_row["client_msg_id"])["body"] ==
      "see you at 7")
old = dict(sent_row, created_at=(M.datetime.now(M.timezone.utc) -
                                 M.timedelta(minutes=16)).isoformat())
check("after fifteen minutes the app offers no edit", not alice.can_edit(old))
alice.open_conversation(bob.keys.address)
pump(alice)
screen = alice.conversation_screen
screen.entry.text = "half a thought"
screen.toggle_selection(draft)
pump(alice)
check("held, Edit takes Share's place in the bar",
      screen.actions["edit"].parent is screen.select_bar and
      screen.actions["share"].parent is None)
screen._act_edit()
check("Edit: the composer holds its words, the bar above says so",
      screen.entry.text == "see you at 7" and screen.editing is not None
      and screen.reply_author.text.lower() == "editing",
      (screen.entry.text, screen.editing is not None,
       screen.reply_author.text))
screen.cancel_reply()
check("...cancelled, what was being written comes back",
      screen.editing is None and screen.entry.text == "half a thought")
screen.start_edit(alice.store.message(draft))
screen.entry.text = "see you at 8"
alice.send_current()
wait_for(lambda: relay.mail)
deliver(relay, parties)
check("...sent: an edit, not a new message",
      wait_for(lambda: alice.store.message(draft)["body"] == "see you at 8")
      and bob.store.message_by_cid(alice.keys.address, sent_row[
          "client_msg_id"])["body"] == "see you at 8" and
      screen.entry.text == "" and screen.editing is None)
pump(alice)
shown = screen._bubbles.get(draft)
check("...and the chat on screen shows the new words, marked edited",
      shown is not None and "see you at 8" in shown.body.text and
      "edited" in shown.meta.text, shown.body.text if shown else "none")

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

print("\nVideo messages (18.1)")
import struct                                                  # noqa: E402
# x264's parameter sets for 480 × 640; the pictures are made-up bytes (a
# file's layout is what is checked here: desktop/test_videomsg.py plays).
SPS = bytes.fromhex("6742c01eda07814684000003000400000300c23c58ba80")
PPS = bytes.fromhex("68ce3c80")


class Recorded:
    """VideoNoteRecorder's stop, write and cancel: two seconds in a real
    file."""
    stopped = cancelled = False

    def stop(self):
        self.stopped = True

    def cancel(self):
        self.cancelled = True

    def write(self):
        pictures = [(i * 42, i % 24 == 0, struct.pack(">I", 300) + bytes(
            [0x65 if i % 24 == 0 else 0x41]) + os.urandom(299))
            for i in range(48)]
        path = os.path.join(str(M.MEDIA_DIR), "rec_video.mp4")
        length, w, h = M.mp4_write(path, SPS, PPS, pictures, 16000, 1,
                                   [os.urandom(60) for _ in range(31)])
        return path, length, w, h, M.poster_b64(
            (bytes([90, 140, 200]) * (32 * 24), 32, 24, "rgb"))


pk, *_rest = M.mine_identity()
dana = M.address_from_priv(pk)
alice.store.upsert_contact(dana, "dana", M.pub_to_compressed(
    M.priv_to_pub(pk)).hex(), listed=True)
check("until someone's app has shown it plays them, the recording sheet "
      "says what an older one shows; nothing is said of Bob, whose app "
      "has written from 18.1, or in a note to self",
      "dana" in alice.video_reach_note(dana) and
      "18.1" in alice.video_reach_note(dana) and
      alice.video_reach_note(bob.keys.address) == "" and
      alice.video_reach_note(alice.keys.address) == "" and
      alice.store.get_contact(bob.keys.address)["caps"] &
      M.LocalStore.CAP_VIDEO,
      (alice.video_reach_note(dana), alice.video_reach_note(
          bob.keys.address)))
blobs_before = len(relay.blobs)
quoted = alice.store.messages_for(bob.keys.address)[0]
alice.conversation_screen.reply_to = {
    "cid": quoted["client_msg_id"], "snip": "hello", "kind": "text",
    "from": alice.keys.address}
made = Recorded()
alice.send_video(made)
wait_for(lambda: relay.mail)
deliver(relay, parties)
sent = alice.store.messages_for(bob.keys.address)[-1]
video = bob.store.messages_for(alice.keys.address)[-1]
vmedia = M.message_ext(video).get("media") or {}
check("a video message is stopped, written, sealed and uploaded as one "
      "blob; its recording file is not kept",
      made.stopped and len(relay.blobs) == blobs_before + 1 and
      not os.path.exists(os.path.join(str(M.MEDIA_DIR), "rec_video.mp4")))
check("it arrives as a video with its length, size and still picture, "
      "and words an older app can show",
      video["kind"] == "video" and video["body"] == "Video message (0:02)"
      and vmedia.get("mime") == "video/mp4" and
      (vmedia.get("w"), vmedia.get("h")) == (480, 640) and
      1900 <= vmedia.get("dur", 0) <= 2100 and vmedia.get("thumb"),
      (video["kind"], video["body"], {k: v for k, v in vmedia.items()
                                      if k != "thumb"}))
check("...with the reply it was sent as (a note on a call goes this way)",
      (M.message_ext(video).get("reply") or {}).get("cid") ==
      quoted["client_msg_id"] and
      alice.conversation_screen.reply_to is None)
pump(bob)
wait_for(lambda: bob.store.message(video["id"])["media_state"] == "ready")
got = bob.store.message(video["id"])
check("...downloads, and is the file that was sent: read as pictures "
      "before it is kept",
      got["media_state"] == "ready" and got["media_path"].endswith(".mp4")
      and open(got["media_path"], "rb").read() ==
      open(sent["media_path"], "rb").read() and
      len(M.mp4_read(got["media_path"]).samples) == 48)
check("a preview, a notification and a quote name it in this phone's "
      "words",
      M.shown_body("video", video["body"]) == "Video message (0:02)" and
      M.message_text(video) == "" and
      M.quote_text({"kind": "video", "snip": ""}) == "Video message")


class Damaged(Recorded):
    """A file that says it is a video and is not one."""

    def write(self):
        path, length, w, h, poster = super().write()
        open(path, "wb").write(b"\x00\x00\x00\x18ftypisom" +
                              os.urandom(2000))
        return path, length, w, h, poster


alice.send_video(Damaged())
wait_for(lambda: relay.mail)
deliver(relay, parties)
bad = bob.store.messages_for(alice.keys.address)[-1]
pump(bob)
wait_for(lambda: bob.store.message(bad["id"])["media_state"] ==
         "unreadable")
check("a file that is no video is not kept, and its bubble says so",
      bob.store.message(bad["id"])["media_state"] == "unreadable" and
      not bob.store.message(bad["id"])["media_path"])
gone = Recorded()
alice.current_peer, held = None, alice.current_peer
alice.send_video(gone)
alice.current_peer = held
check("with no chat open, the recording is let go and nothing is sent",
      gone.cancelled)

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

print("\n16.2: mentions in a group (I7)")
relay.mail.clear()
gpeer = alice.create_group("Mentions", [B, K])
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
wait_for(lambda: (bob.store.group(M.gid_of(gpeer)) or {}).get("state") ==
         "active")
bob.open_conversation(gpeer)
pump(bob)
bscreen = bob.conversation_screen
bscreen.entry.text = "thoughts, @al"
bscreen.entry.input.cursor = bscreen.entry.input.get_cursor_from_index(
    len(bscreen.entry.text))
bscreen._sync_mentions()
offered = [name for name, _a in bscreen.mention_strip.found]
check("@ in a group offers the members whose names begin so (not you)",
      offered == ["alice"], str(offered))
bscreen.entry.text = "and @"
bscreen.entry.input.cursor = bscreen.entry.input.get_cursor_from_index(5)
bscreen._sync_mentions()
check("...an @ alone offers every other member",
      sorted(n for n, _a in bscreen.mention_strip.found) == ["alice",
                                                              "cara"])
bscreen.entry.text = "thoughts, @al"
bscreen.entry.input.cursor = bscreen.entry.input.get_cursor_from_index(
    len(bscreen.entry.text))
bscreen.pick_mention("alice", A)
check("...a tap writes the name, and the offer goes",
      bscreen.entry.text == "thoughts, @alice " and
      not bscreen.mention_strip.found)
alice.store.set_mute(gpeer, 0)                 # muted until turned on
relay.mail.clear()
bob.send_current()
wait_for(lambda: relay.mail)
deliver(relay, parties)
named = alice.store.messages_for(gpeer)[-1]
row = next(c for c in alice.store.conversations(me=A) if c["peer"] == gpeer)
item = M.ConversationRow(row, lambda _p: None)
check("17.0 (F12): an @ beside the group in her chat list, until read",
      row["mentioned"] and any(isinstance(w, M.UnreadBadge) and
                               w.text == "@" for w in item.walk()))


def alice_stamp(msg):
    alice.open_conversation(gpeer)
    pump(alice)
    bubble = alice.conversation_screen._bubbles.get(msg["id"])
    return bubble.meta.text if bubble is not None else ""


check("the message names her address, and her bubble says so",
      M.message_ext(named).get("mn") == [A] and
      M.message_ext(bob.store.messages_for(gpeer)[-1]).get("mn") == [A]
      and "mentions you" in alice_stamp(named),
      (named["body"], M.message_ext(named)))
check("...and once read, no @", not next(
    c for c in alice.store.conversations(me=A)
    if c["peer"] == gpeer)["mentioned"])
check("her muted group still tells her, as it does not for the rest",
      not M.notification_text(alice.store, gpeer, named["body"], "text", B,
                              mentioned=True)[2] and
      M.notification_text(alice.store, gpeer, named["body"], "text", B)[2])
check("a mention outside a group, or of no one real, is not kept",
      M.sanitize_mentions(["nope", A, A, 7]) == [A] and
      M.sanitize_ext({"mn": "x"}) == {})
alice.store.set_mute(gpeer, None)
alice.go_back_to_chats()
bob.go_back_to_chats()

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
alice.set_language("zh-CN")
check("Settings › Language: the app is built again in Chinese, keeping the "
      "open chat and its draft", M.LANG.code == "zh-CN" and
      alice.settings_screen.appbar.title.text == "设置" and
      alice.manager.current == "conversation" and
      alice.conversation_screen.entry.text == "half-written")
alice.set_language("system")
check("...and back to the system's language (English here)",
      M.LANG.code == "en" and
      alice.settings_screen.appbar.title.text == "Settings")

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
wait_for(lambda: len(relay.mail) >= 2 and settled(one))
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

print("\n16.0: a relay that no longer knows someone (Deleted User)")


def row_texts(app, peer):
    rows = [w for w in app.chats_screen.list.walk()
            if isinstance(w, M.ConversationRow) and w.peer == peer]
    return [w.text for w in (rows[0].walk() if rows else ())
            if isinstance(w, M.Label)]


relay.forgotten.add(B)                 # reset: Bob has not registered again
relay.mail.clear()
tried = attempts(B)
lost = alice.send_text(B, "still there?")
wait_for(lambda: attempts(B) > tried and settled(lost))
pump(alice)
alice.refresh_sidebar()
check("a send the relay refuses as 'recipient not found' waits at its "
      "clock", alice.store.message(lost)["status"] == 0 and not relay.mail)
check("...and its person is shown as Deleted User in the chat list",
      "Deleted User" in row_texts(alice, B) and
      "bob" not in row_texts(alice, B), row_texts(alice, B))
alice.open_conversation(B)
wait_for(lambda: alice.conversation_screen.peer == B)
head = alice.conversation_screen
check("...and over the chat, with who they were",
      head.peer_name.text == "Deleted User" and
      head.peer_sub.text.startswith("was @bob"), head.peer_sub.text)
check("...the outbox does not undo it", alice.flush_outbox() == 0 and
      alice.store.get_contact(B)["gone"] > 0)
relay.forgotten.discard(B)             # Bob registers again, and writes
bob.send_text(A, "back again")
wait_for(lambda: relay.mail)
deliver(relay, parties)
pump(alice)
alice.refresh_chat(B)
check("a message from them brings their name back",
      not alice.store.get_contact(B)["gone"] and
      head.peer_name.text == "bob", head.peer_name.text)
check("...and what waited goes", alice.flush_outbox() == 1 and
      alice.store.message(lost)["status"] == 1)
deliver(relay, parties)
relay.forgotten.add(K)
gone_too = alice.send_text(g14, "who is still here?")
wait_for(lambda: relay.mail and settled(gone_too))
check("in a group, a member the relay has no record of is a Deleted "
      "User; the others are not", alice.store.get_contact(K)["gone"] > 0 and
      not alice.store.get_contact(B)["gone"] and
      alice.conversation_screen.author_name(K) == "Deleted User")
relay.forgotten.discard(K)
alice.flush_outbox()
check("...and a copy the relay then takes undoes it",
      not alice.store.get_contact(K)["gone"])
deliver(relay, parties)

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
gus.store.close()                    # the old install's, closed as it goes
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
asks = list(relay.mail)            # only the ask: deliver() would hand the
relay.mail.clear()                 # copy it brings straight to gus as well
for ask in asks:
    parties[ask["recipient"]].worker._ingest_message(ask)
wait_for(lambda: any(m["client_msg_id"] == lost_cid for m in relay.mail))
again = next((m for m in relay.mail if m["client_msg_id"] == lost_cid), {})
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

print("\n17.4: what a group's members may do")
rules = M.sanitize_group_rules(
    {"deny": {"send": 100, "fly": 100, "calls": True},
     "lim": {B: {"deny": ["media", "dance"], "at": 50, "until": 0},
             K: {"deny": ["send"], "at": 50, "until": 40},
             A: {"deny": ["send"], "at": 50, "until": 0},
             "nobody": {"deny": ["send"], "at": 50, "until": 0}}}, {B, K})
check("rules keep known rights with good times, and only members' "
      "restrictions", rules == {"deny": {"send": 100.0},
                                "lim": {B: {"deny": ["media"], "at": 50.0,
                                            "until": 0.0}}}, rules)
check("a stop says which right, until when, and whose; photos and voice "
      "messages stop with messages",
      M.group_denied(rules, {A}, B, "media", 60) == ("media", 0.0, True)
      and M.group_denied(rules, {A}, K, "media", 150) == ("send", 0.0, False)
      and M.group_denied(rules, {A}, K, "send", 99) is None)
check("no admin is ever stopped",
      M.group_denied(rules, {A, B}, B, "media", 60) is None)

relay.mail.clear()
rpeer = alice.create_group("Seventeen Four", [B, K])
rgid = M.gid_of(rpeer)
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("a group without rules has a state as before, which older apps read",
      "r" not in alice._group_state(rgid) and "r" not in
      M.sanitize_group_state(alice._group_state(rgid)))


def events(app, peer=rpeer):
    return [m["body"] for m in app.store.messages_for(peer)
            if m["kind"] == "event"]


def bodies(app, peer=rpeer):
    return [m["body"] for m in app.store.messages_for(peer)
            if m["kind"] != "event"]


check("the admin turns off members' new calls: said in each chat",
      alice.set_group_permissions(rgid, ["calls"]) and
      events(alice)[-1] == "You turned off new calls for members")
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("...and the members' apps keep the rule",
      all("calls" in p.store.group(rgid)["rules"]["deny"]
          for p in (bob, cara)) and
      events(bob)[-1] == "alice turned off new calls for members")
check("a member may no longer start a call there, and is told why",
      bob.group_stop(rpeer, "calls") ==
      "Only the group's admins can start calls here" and
      not bob.group_stop(rpeer, "send") and not alice.group_stop(rpeer,
                                                                 "calls"))
check("a call's invite from a member is held by those who know; the "
      "admin's is not", cara.worker._group_held(
          rgid, B, {"k": "call"}, time.time()) == "calls" and
      cara.worker._group_held(rgid, A, {"k": "call"}, time.time()) == "")

bob.send_text(rpeer, "before the stop")
wait_for(lambda: len(relay.mail) >= 2)
early = list(relay.mail)
relay.mail.clear()
before = len(events(cara))
check("the admin restricts one member's messages for an hour",
      alice.restrict_member(rgid, B, ["send"], 3600) and
      events(alice)[-1].startswith("You stopped bob's messages until "))
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("...which their chat says, until when", events(bob)[-1].startswith(
    "alice stopped your messages here until "))
check("...and nobody else's does", len(events(cara)) == before)
check("their app keeps it, and stops photos and voice messages with it",
      bob.group_stop(rpeer, "send").startswith(
          "An admin has stopped your messages here until ") and
      bob.group_stop(rpeer, "media"))
relay.mail.extend(early)
deliver(relay, parties)
check("a message the relay took before the stop still arrives",
      "before the stop" in bodies(cara))
bob.open_conversation(rpeer)
pump(bob)
bscreen = bob.conversation_screen
check("their composer gives way to a note saying so",
      bscreen.closed_note.parent is not None and
      bscreen.closed_note.text.startswith(
          "An admin has stopped your messages here until "))
check("...and nothing they write goes",
      bob.send_text(rpeer, "let me in") is None and not relay.mail)
sent = next(m for m in bob.store.messages_for(rpeer)
            if m["body"] == "before the stop")
check("...nor an edit", not bob.can_edit(sent))
stop_words = bob.group_stop
bob.group_stop = lambda *a, **k: ""      # an app that knows nothing of it
bob.send_text(rpeer, "sent anyway")
wait_for(lambda: len(relay.mail) >= 2)
held = [m for m in relay.mail if m["recipient"] == K]
check("what an app sends anyway is acknowledged by those who know, and "
      "not shown", held and cara.worker._ingest_message(held[0]) ==
      held[0]["id"] and "sent anyway" not in bodies(cara))
relay.mail.clear()
bob._send_control(rpeer, {"op": "edit", "cid": sent["client_msg_id"],
                          "b": "changed my mind"}, "Edited a message")
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("...nor is an edit", "changed my mind" not in bodies(cara) and
      "before the stop" in bodies(cara))
bob.group_stop = stop_words
bob.api.down = True
bob.group_stop = lambda *a, **k: ""
tried = bob.api.requests.count("send_many")
queued = bob.send_text(rpeer, "written while away")
wait_for(lambda: bob.api.requests.count("send_many") > tried and
         queued not in bob._sends()[1])
bob.group_stop = stop_words
bob.api.down = False
check("the outbox leaves what the stop holds", bob.flush_outbox() == 0 and
      not relay.mail and bob.store.message(queued)["status"] == 0)

check("the admin lifts it", alice.restrict_member(rgid, B, [], 0) and
      events(alice)[-1] == "You lifted bob's restriction" and
      not alice.restrict_member(rgid, B, [], 0))
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
pump(bob)
bscreen._sync_closed()
check("...their chat says so, and the composer is back",
      events(bob)[-1] == "alice lifted your restriction here" and
      bscreen.closed_note.parent is None)
check("...and what waited goes", bob.flush_outbox() == 1)
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("...and arrives", "written while away" in bodies(cara))

alice.restrict_member(rgid, K, ["media"], 1)
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("a restriction with an end stops only until then",
      cara.group_stop(rpeer, "media") and not cara.group_stop(rpeer, "send"))
time.sleep(1.2)
check("...and then no more", not cara.group_stop(rpeer, "media") and
      alice.member_restriction(rgid, K) is None)
alice.restrict_member(rgid, K, [], 0)
deliver(relay, parties)

alice.set_group_permissions(rgid, ["send", "calls"])
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
cara.open_conversation(rpeer)
pump(cara)
check("with messages off for members, a member's composer says only the "
      "admin can write", cara.conversation_screen.closed_note.text ==
      "Only the group's admins can send messages here")
alice.open_conversation(rpeer)
pump(alice)
check("...and the admin's is as ever",
      alice.conversation_screen.closed_note.parent is None and
      alice.send_text(rpeer, "announcements only") is not None)
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("...whose messages arrive", "announcements only" in bodies(cara))
since = alice.store.group(rgid)["rules"]["deny"]["calls"]
alice.set_group_permissions(rgid, ["calls"])
check("turning one back on keeps when the others went off",
      alice.store.group(rgid)["rules"]["deny"] == {"calls": since})
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("...and is said", events(cara)[-1] ==
      "alice turned messages back on for members")

state = alice._group_state(rgid)
older = {k: v for k, v in state.items() if k != "r"}
older["v"] += 1
cara.worker._apply_group_state(A, M.sanitize_group_state(older))
check("a newer state without rules (an admin's app before 17.4) clears "
      "them", cara.store.group(rgid)["rules"] == {} and
      events(cara)[-1] == "alice turned new calls back on for members")
alice.restrict_member(rgid, B, ["calls"], 0)
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
with bob.store._lock:
    bob.store._db.execute("UPDATE groups SET version = version + 1 "
                          "WHERE gid = ?", (rgid,))
    bob.store._db.commit()
check("rules left behind by an older release's newer state are not used",
      bob.store.group(rgid)["rules"] == {} and
      M.LocalStore.SCHEMA_VERSION == 14)

print("\n17.4: a group's topics")
check("topics keep good ids and names, once each; none without a list",
      M.sanitize_group_topics({"on": True, "x": 1, "list": [
          {"id": "0a1b2c3d", "n": " Trip ", "c": True, "z": 2},
          {"id": "0a1b2c3d", "n": "again"}, {"id": "nope", "n": "bad"},
          {"id": "11223344", "n": ""}]}) ==
      {"on": True, "list": [{"id": "0a1b2c3d", "n": "Trip", "c": True}]} and
      M.sanitize_group_topics({"on": True}) == {} and
      M.sanitize_group_topics({"list": []}) == {})
relay.mail.clear()
tpeer = alice.create_group("Walks", [B, K])
tgid = M.gid_of(tpeer)
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("the admin turns topics on: said in each chat",
      alice.change_topics(tgid, on=True) == "" and
      events(alice, tpeer)[-1] == "You turned topics on")
trip = alice.change_topics(tgid, add="Trip")
books = alice.change_topics(tgid, add="Books")
wait_for(lambda: len(relay.mail) >= 6)
deliver(relay, parties)
check("...and adds two; each member's app has them, in order",
      [t["n"] for t in bob.store.group(tgid)["topics"]["list"]] ==
      ["Trip", "Books"] and events(bob, tpeer)[-1] ==
      "alice added the topic “Books”" and len({trip, books}) == 2)
bob.open_conversation(tpeer)
pump(bob)
bscreen = bob.conversation_screen
strip = bscreen.topic_strip
check("the chat shows its topics under its head: All, General and each",
      strip.opacity == 1 and len(strip.row.children) == 4 and
      bscreen.topic is None)
bscreen.choose_topic(trip)
check("a message written in a topic goes to it",
      bob.send_text(tpeer, "Train at nine") is not None)
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
row = next(m for m in cara.store.messages_for(tpeer)
           if m["body"] == "Train at nine")
check("...and arrives there", M.message_ext(row).get("tp") == trip)
bscreen.choose_topic(None)
bob.send_text(tpeer, "Hello all")
reply = {"cid": row["client_msg_id"], "snip": "Train at nine", "from": B}
bob.send_text(tpeer, "Which platform?", reply=reply)
wait_for(lambda: len(relay.mail) >= 4)
deliver(relay, parties)
got = {m["body"]: M.message_ext(m).get("tp", "") for m in
       cara.store.messages_for(tpeer) if m["kind"] != "event"}
check("from All, a message goes to General; a reply, to its message's "
      "topic", got.get("Hello all") == "" and
      got.get("Which platform?") == trip, got)
check("cara's chat marks the topic with something unread",
      trip in cara.store.unread_topics(tpeer))
cara.open_conversation(tpeer)
pump(cara)
cscreen = cara.conversation_screen
cscreen.choose_topic(trip)
pump(cara)
shown = sorted(b.msg["body"] for b in cscreen._bubbles.values())
check("a topic chosen, the chat shows its messages alone",
      shown == ["Train at nine", "Which platform?"], shown)
cscreen.choose_topic(None)
pump(cara)
labels = {b.msg["body"]: b.author.text.lower()
          for b in cscreen._bubbles.values() if b.author is not None}
check("all of them shown, each names its topic beside its author",
      labels.get("Train at nine", "").endswith("· trip") and
      "·" not in labels.get("Hello all", "·"), labels)
cscreen.choose_topic(books)
pump(cara)
bob.send_text(tpeer, "Lunch after?")
bscreen.choose_topic(books)
bob.send_text(tpeer, "Anyone read Dune?")
bscreen.choose_topic(None)
wait_for(lambda: len(relay.mail) >= 4)
deliver(relay, parties)
pump(cara)
unread = cara.store.unread_topics(tpeer)
chips = cscreen.topic_strip.row.children[::-1]   # All, General, ...
check("a topic shown, what comes to it is read; what comes to another "
      "stays unread, a dot on its chip", unread == {""} and
      len(chips[1].children) == 2, unread)
cscreen.choose_topic("")
pump(cara)
check("...and is read once its topic is shown",
      not cara.store.unread_topics(tpeer))
cscreen.choose_topic(None)

alice.change_topics(tgid, close=(books, True))
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
bscreen.choose_topic(books)
pump(bob)
check("a closed topic takes nothing from a member: the composer says so",
      bscreen.closed_note.parent is not None and
      bscreen.closed_note.text.startswith("This topic is closed") and
      bob.send_text(tpeer, "can I?") is None)
alice.open_conversation(tpeer)
pump(alice)
alice.conversation_screen.choose_topic(books)
check("...and the admin's own goes in",
      alice.send_text(tpeer, "Reading list is up") is not None)
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
bscreen.choose_topic(None)

alice.change_topics(tgid, on=False)
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
pump(bob)
bob.refresh_chat(tpeer)
labels = {b.msg["body"]: b.author.text.lower()
          for b in bscreen._bubbles.values() if b.author is not None}
check("topics turned off: the strip goes, every message stays in the one "
      "chat with its topic named", strip.opacity == 0 and
      bscreen.topic is None and labels.get("Reading list is up", "")
      .endswith("· books") and events(bob, tpeer)[-1] ==
      "alice turned topics off: every message shows in one chat", labels)
check("...and what is written then goes to the chat as a whole",
      bob.compose_topic(tpeer) == "")
alice.change_topics(tgid, on=True)
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
pump(bob)
bob.refresh_chat(tpeer)
bscreen.choose_topic(trip)
check("turned on again, each topic is back with its messages",
      strip.opacity == 1 and sorted(b.msg["body"] for b in
                                    bscreen._bubbles.values()) ==
      ["Train at nine", "Which platform?"])
bscreen.choose_topic(None)
alice.group_info_screen.show(tgid)
texts = [w.text for w in alice.group_info_screen.body.walk()
         if isinstance(w, M.Label)]
check("the group's page says so to its admin", "Topics" in texts and
      "On · 2 topics" in texts)

print("\n18.0: a group's admins")
check("a state's other admins: members only, not the owner, known rights "
      "in order; one made by someone gone is the owner's",
      M.sanitize_group_admins(
          {B: {"r": ["delete", "info", "fly"], "by": K},
           A: {"r": ["info"]}, K: {"r": []}, "nobody": {"r": ["add"]}},
          {A, B, K}, A) == {B: {"r": ["info", "delete"], "by": K}} and
      M.sanitize_group_admins({B: {"r": ["add"], "by": "gone"}}, {A, B},
                              A) == {B: {"r": ["add"], "by": A}})
good = {"v": 3, "a": B, "e": "name", "t": " Mods ", "at": 100.0}
check("an admin log keeps good entries only, the newest fifty",
      M.sanitize_admin_log([good, dict(good, a="bad"), dict(good, e="fly"),
                            dict(good, at=-1), "junk"]) ==
      [dict(good, t="Mods")] and
      len(M.sanitize_admin_log([good] * 80)) == M.ADMIN_LOG_MAX == 50)
held = {"version": 5, "admin": A, "by": A, "name": "G",
        "members": [{"a": A}, {"a": B}, {"a": K}],
        "admins": {B: {"r": ["info", "admins"], "by": A}},
        "rules": {}, "topics": {}}
same = {"v": 5, "name": "H", "admin": A, "members": held["members"],
        "ad": held["admins"]}
check("at the same version the owner's state wins, then the lower "
      "address's; a newer one always does",
      M.state_wins(dict(held, by=B), same, A) and
      not M.state_wins(held, same, B) and
      M.state_wins(held, dict(same, v=6), B))
allowed = M.admin_change_allowed
check("another admin may change what his rights cover, and step down",
      allowed(held, same, B) and allowed(held, dict(same, ad={}), B))
check("...not anything else: members, the owner, rules, more rights",
      not allowed(held, dict(same, members=held["members"][:2]), B) and
      not allowed(held, dict(same, admin=B), B) and
      not allowed(held, dict(same, r={"deny": {"send": 9.0}}), B) and
      not allowed(held, dict(same, ad={B: {"r": ["info", "add", "admins"],
                                           "by": A}}), B))
check("...and makes admins only with rights of his own, changing none "
      "he did not make",
      allowed(held, dict(same, ad=dict(held["admins"], **{
          K: {"r": ["info"], "by": B}})), B) and
      not allowed(held, dict(same, ad=dict(held["admins"], **{
          K: {"r": ["delete"], "by": B}})), B) and
      not allowed(dict(held, admins=dict(held["admins"], **{
          K: {"r": ["info"], "by": A}})), same, B))

relay.mail.clear()
apeer = alice.create_group("Moderators", [B, K])
agid = M.gid_of(apeer)
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
rights = ["info", "add", "remove", "delete", "admins"]
check("the owner makes bob an admin with some rights: said in her chat",
      alice.set_group_admin(agid, B, ["admins"] + rights + ["fly"]) and
      events(alice, apeer)[-1] == "You made bob an admin")
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("...each member's app holds it, says it, and logs who did it",
      all(p.store.group(agid)["admins"] == {B: {"r": rights, "by": A}}
          for p in (bob, cara)) and
      events(bob, apeer)[-1] == "alice made you an admin" and
      events(cara, apeer)[-1] == "alice made bob an admin" and
      [(e["a"], e["e"], e.get("t")) for e in cara.store.group(agid)["log"]]
      == [(A, "admin", B)])

with alice.store._lock:                  # as if cara's app were older
    alice.store._db.execute("UPDATE contacts SET caps = caps & ? WHERE "
                            "address = ?", (~M.LocalStore.CAP_V18, K))
    alice.store._db.commit()
check("bob, who may change its info, renames it",
      bob.rename_group(agid, "Mods"))
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("...alice and cara take it from him, and their logs say so",
      all(p.store.group(agid)["name"] == "Mods" and
          [e["e"] for e in p.store.group(agid)["log"]] == ["admin", "name"]
          for p in (alice, cara)) and
      events(cara, apeer)[-1] == "bob renamed the group “Mods”")
pump(alice)
wait_for(lambda: relay.mail)
fwd = list(relay.mail)
ctl = seen(fwd[0])[2].get("ctl", {}) if len(fwd) == 1 else {}
check("the owner sends his change on, as it is, to an app older than "
      "18.0 (one that follows her alone), marked as a copy",
      len(fwd) == 1 and fwd[0]["recipient"] == K and
      ctl.get("op") == "group" and "lg" not in ctl["g"] and
      ctl.get("fw") == 1 and
      ctl["g"]["v"] == alice.store.group(agid)["version"], fwd)
before = (len(events(cara, apeer)), cara.store.group(agid)["log"])
deliver(relay, parties)
check("...which an 18.0 app leaves: it has his own",
      (len(events(cara, apeer)), cara.store.group(agid)["log"]) == before)
alice.store.add_caps(K, M.LocalStore.CAP_V18)

check("bob may not set what members can do: that is not his right",
      not bob.set_group_permissions(agid, ["calls"]))
forged = bob._group_state(agid)
forged["v"] += 1
forged["r"] = {"deny": {"send": 100.0}}
version = cara.store.group(agid)["version"]
cara.worker._apply_group_state(B, M.sanitize_group_state(forged))
check("...nor can his app do it behind the screen: members refuse it",
      cara.store.group(agid)["rules"] == {} and
      cara.store.group(agid)["version"] == version)

bob.send_text(apeer, "buy cheap watches")
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
spam = next(m for m in alice.store.messages_for(apeer)
            if m["body"] == "buy cheap watches")
cara._send_control(apeer, {"op": "delete", "adm": 1,
                           "cids": [spam["client_msg_id"]]}, "x")
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("a member who is no admin cannot delete another's message",
      any(m["body"] == "buy cheap watches"
          for m in alice.store.messages_for(apeer)))
check("bob, an admin with the right, makes cara one with only rights of "
      "his own", bob.set_group_admin(agid, K, ["delete", "topics"]) and
      bob.store.group(agid)["admins"][K] == {"r": ["delete"], "by": B})
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
pump(alice)
check("...which alice and cara take", alice.store.group(agid)["admins"]
      .get(K) == {"r": ["delete"], "by": B} == cara.store.group(agid)
      ["admins"].get(K))
crow = next(m for m in cara.store.messages_for(apeer)
            if m["body"] == "buy cheap watches")
modal = M.DeleteModal(cara, [crow])
buttons = [w.text.lower() for w in modal.body.walk()
           if isinstance(w, M.Btn)]
check("cara's delete sheet offers deleting bob's message for everyone",
      "delete for everyone" in buttons, buttons)
cara.delete_messages([crow["id"]], True)
wait_for(lambda: len(relay.mail) >= 4)
deliver(relay, parties)
check("...and does: it is gone from every member's chat",
      not any(m["body"] == "buy cheap watches"
              for p in (alice, bob, cara)
              for m in p.store.messages_for(apeer)))
check("...and the log says who did it, and whose it was, not the words",
      alice.store.group(agid)["log"][-1]["e"] == "delete" and
      alice.store.group(agid)["log"][-1].get("t") == B and
      "watches" not in str(alice.store.group(agid)["log"]))

alice.group_info_screen.show(agid)
texts = [w.text.lower() for w in alice.group_info_screen.body.walk()
         if isinstance(w, (M.Label, M.CapsLabel))]
bob.group_info_screen.show(agid)
btexts = [w.text.lower() for w in bob.group_info_screen.body.walk()
          if isinstance(w, (M.Label, M.CapsLabel))]
check("the group's page tags its owner and admins",
      "owner" in texts and texts.count("admin") == 2, texts)
check("an admin's page offers what his rights allow, his rights, and the "
      "log; not what they do not",
      {"rename the group", "add members", "your rights as an admin",
       "admin log"} <= set(btexts) and "topics" not in btexts, btexts)
sheet = M.AdminRightsSheet(bob, agid, K, "cara", lambda: None)
check("making an admin, the rights he does not have cannot be given",
      sheet.switches["topics"].disabled and
      sheet.switches["rules"].disabled and
      not sheet.switches["delete"].disabled and
      sheet.switches["delete"].active)
check("the log says 'your' to the member it is about",
      M.admin_log_line({"v": 1, "a": A, "e": "rights", "t": B, "at": 1.0},
                       lambda a, start: "alice" if a == A else
                       "You" if start else "you", B) ==
      "alice changed your rights as an admin")
log = M.AdminLogSheet(alice, agid)
lines = [w.text for w in log.body.walk() if isinstance(w, M.Label)]
check("the admin log says what each admin did, newest first",
      lines.index("cara deleted bob's messages") <
      lines.index("bob renamed the group “Mods”") <
      lines.index("You made bob an admin"), lines)

relay.mail.clear()
v0 = alice.store.group(agid)["version"]
alice.rename_group(agid, "Alpha")
bob.rename_group(agid, "Beta")
wait_for(lambda: len(relay.mail) >= 4)
deliver(relay, parties)
check("two admins change it at once: every app keeps the owner's",
      all(p.store.group(agid)["name"] == "Alpha"
          for p in (alice, bob, cara)))
check("...bob is told his change gave way",
      events(bob, apeer)[-1].startswith("Your change crossed alice's"))
check("...and every log has her entry at that version, not his",
      all([(e["a"], e.get("t")) for e in p.store.group(agid)["log"]
           if e["v"] == v0 + 1] == [(A, "Alpha")]
          for p in (alice, bob, cara)))

check("cara steps down", cara.set_group_admin(agid, K, []) and
      events(cara, apeer)[-1] == "You're no longer an admin")
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("...which the others take",
      K not in alice.store.group(agid)["admins"] and
      events(alice, apeer)[-1] == "cara is no longer an admin",
      (alice.store.group(agid)["admins"], events(alice, apeer)[-3:]))
pump(alice)
relay.mail.clear()
alice.set_group_admin(agid, B, [])
alice.set_group_admin(agid, K, ["info"])
wait_for(lambda: len(relay.mail) >= 4)
deliver(relay, parties)
relay.mail.clear()
alice.leave_group(agid)
wait_for(lambda: len(relay.mail) >= 4)
sent = {}
for msg in relay.mail:
    sent.setdefault(msg["recipient"], []).append(
        seen(msg)[2].get("ctl", {}))
check("the owner leaving first names the next owner — the first other "
      "admin, where an older app would take the first member",
      all([c.get("op") for c in sent.get(a, [])] == ["group", "leave"] and
          sent[a][0]["g"]["admin"] == K for a in (B, K)), sent)
deliver(relay, parties)
pump(cara)
wait_for(lambda: relay.mail, 1.0)
deliver(relay, parties)
check("...so every app follows cara, who now owns the group",
      bob.store.group(agid)["admin"] == K == cara.store.group(agid)["admin"]
      and "cara now owns the group" in events(bob, apeer) and
      [m["a"] for m in bob.store.group(agid)["members"]] == [B, K])

for x, y in ((bob, dave), (dave, bob), (cara, dave), (dave, cara)):
    x.store.upsert_contact(y.keys.address, y.keys.username,
                           y.keys.public_key_hex)
relay.mail.clear()
cara.set_group_admin(agid, B, ["add"])
wait_for(lambda: relay.mail)
deliver(relay, parties)
check("an admin who may add members invites one",
      bob.add_group_members(agid, [D]) == 1)
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("...whose app takes the invitation from him, log and all",
      dave.store.group(agid) is not None and
      dave.store.group(agid)["state"] == "active" and
      dave.store.group(agid)["log"] == bob.store.group(agid)["log"] and
      [m["a"] for m in cara.store.group(agid)["members"]] == [B, K, D])
check("...but may not remove anyone: that is not his right",
      not bob.remove_group_member(agid, D) and
      not bob.may_manage(bob.store.group(agid), D, "remove"))
pump(cara)
relay.mail.clear()
cara.restrict_member(agid, D, ["send"], 0)
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("a restricted member made an admin is restricted no more",
      cara.set_group_admin(agid, D, ["info"]) and
      not cara.member_restriction(agid, D))
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
check("...in every app", dave.group_rule(M.group_peer(agid),
                                         "send") is None and
      D in bob.store.group(agid)["admins"])

print("\n18.0: joining by a link, let in by its maker (JOIN174)")


def drained(app):
    """What the app's UI queue holds, taken off it unhandled."""
    got = []
    while not app.ui_queue.empty():
        got.append(app.ui_queue.get_nowait())
    return got


url = M.join_link("https://relay.example", A, "0123456789abcdef", "group",
                  gid, "The Reading Circle")
raw = url.split("#" + M.JOIN_LINK_MARK)[1]
sent_fields = set(M.json.loads(M.base64.urlsafe_b64decode(
    raw + "=" * (-len(raw) % 4))))
check("a link for anyone says who lets people in, and to what; no token, "
      "no key", M.parse_join_link(url) == {
          "h": A, "l": "0123456789abcdef", "k": "group", "t": gid,
          "n": "The Reading Circle", "v": False} and
      sent_fields == {"h", "l", "k", "n", "g"})
check("...and a damaged one is no link",
      M.parse_join_link(url.replace("fx2.", "fx1.")) is None and
      M.parse_join_link(M.join_link("https://r", "nobody", "0" * 16,
                                    "group", gid, "x")) is None and
      M.parse_join_link(M.join_link("https://r", A, "short", "group",
                                    gid, "x")) is None and
      M.parse_join_link(M.join_link("https://r", A, "0" * 16, "dance",
                                    gid, "x")) is None)

relay.mail.clear()
glink = alice.join_link_for("group", gid)
gdata = M.parse_join_link(glink)
check("an admin who may add members makes the group's link; asked again, "
      "the same one", gdata is not None and
      alice.join_link_for("group", gid) == glink and
      gdata["n"] == alice.store.group(gid)["name"])
check("a member who may not add members has none",
      bob.join_link_for("group", gid) is None)
erin.ask_to_join(gdata)
wait_for(lambda: relay.mail)
knock = seen(relay.mail[0])[2].get("ctl") if relay.mail else None
check("erin asks in: one sealed knock, to alice alone",
      [m["recipient"] for m in relay.mail] == [A] and
      knock == {"op": "knock", "l": gdata["l"]}, relay.mail)
deliver(relay, parties)
erin.ask_to_join(gdata)
wait_for(lambda: relay.mail)
deliver(relay, parties)
check("alice's app keeps the ask, once, though erin is a message request "
      "there", [a["sender"] for a in alice.store.join_asks("group", gid)]
      == [E] and alice.store.is_request(E))
alice.group_info_screen.show(gid)
texts = [w.text for w in alice.group_info_screen.body.walk()
         if isinstance(w, M.Label)]
check("the group's page says someone is asking to join",
      "Asking to join" in texts and "1 person" in texts)
drained(alice)
room, M.MAX_GROUP_MEMBERS = M.MAX_GROUP_MEMBERS, len(
    alice.store.group(gid)["members"])                 # as if it were full
full = alice.answer_join_ask(gdata["l"], E, True)
M.MAX_GROUP_MEMBERS = room
check("a full group has no room for her: said so, and her ask is kept",
      "full" in full and [a["sender"] for a in alice.store.join_asks(
          "group", gid)] == [E], full)
check("alice lets her in: she is added as any new member is",
      alice.answer_join_ask(gdata["l"], E, True) == "")
wait_for(lambda: len(relay.mail) >= 3)
deliver(relay, parties)
check("...erin's app has the group, from alice, and nobody is left "
      "asking", (erin.store.group(gid) or {}).get("state") == "active" and
      [m["a"] for m in erin.store.group(gid)["members"]] ==
      [m["a"] for m in alice.store.group(gid)["members"]] and
      E in [m["a"] for m in alice.store.group(gid)["members"]] and
      not alice.store.join_asks("group", gid))
alice.stop_join_link("group", gid)
dave.ask_to_join(gdata)
wait_for(lambda: relay.mail)
deliver(relay, parties)
check("a stopped link takes no more asks",
      not alice.store.join_asks() and
      alice.join_link_for("group", gid) != glink)

hosted = M.new_group_call(gpeer, A, False)
hosted.room = "ab" * 8
hosted.preview()
hosted.confirm_join()
alice._call_sessions()[hosted.id] = hosted
cdata = M.parse_join_link(alice.join_link_for("call", hosted.id))
check("the host of a call makes its link, with their name on it",
      cdata["k"] == "call" and cdata["t"] == hosted.id and
      cdata["n"] == alice.keys.username)
check("one who is not its host cannot", bob.join_link_for("call",
                                                          hosted.id) is None)
relay.mail.clear()
dave.ask_to_join(cdata)
wait_for(lambda: relay.mail)
deliver(relay, parties)
pump(alice)
sheet = M.JoinAsksSheet.current
check("during the call, who is asking shows at once, with Let in and "
      "Decline", sheet is not None and sheet.kind == "call" and
      {"decline", "let in"} <= {w.text.lower() for w in sheet.body.walk()
                                if isinstance(w, M.Btn)})
sheet.dismiss()
relay.mail.clear()
cara._send_control_to(D, {"op": "admit", "l": cdata["l"], "no": 1}, "x",
                      pub=dave.keys.public_key_hex)
deliver(relay, parties)
pump(dave)
check("someone else's answer to dave's ask (the link is anyone's) is let "
      "go, and his ask stands", dave.store._db.execute(
          "SELECT COUNT(*) FROM join_asked WHERE link = ?",
          (cdata["l"],)).fetchone()[0] == 1 and
      "did not let you in" not in getattr(M.Toast.current, "text", ""))
check("let in: the call's invite goes to dave, sealed to him alone",
      alice.answer_join_ask(cdata["l"], D, True) == "")
wait_for(lambda: relay.mail)
admit = seen(relay.mail[0])[2].get("ctl", {}) if relay.mail else {}
check("...room, token and key in it",
      [m["recipient"] for m in relay.mail] == [D] and
      admit.get("op") == "admit" and
      admit.get("call", {}).get("t") == hosted.token)
deliver(relay, parties)
card = dave.store.call_card(A, hosted.id)
check("dave's app keeps the call's card in his chat with alice, now "
      "listed", card is not None and card["token"] == hosted.token and
      dave.store.get_contact(A)["listed"])
pump(dave)
lobby = dave.__dict__.get("_lobby")
check("...and opens its lobby", lobby is not None and
      lobby.session.id == hosted.id)
if lobby is not None:
    lobby.cancel()
alice._send_control_to(K, {"op": "admit", "l": "f" * 16,
                           "call": M.call_invite(hosted)}, "x")
deliver(relay, parties)
check("an admission nobody asked for is let go",
      cara.store.call_card(A, hosted.id) is None)
relay.mail.clear()
erin.ask_to_join(cdata)
wait_for(lambda: relay.mail)
deliver(relay, parties)
alice.answer_join_ask(cdata["l"], E, False)
wait_for(lambda: relay.mail)
deliver(relay, parties)
pump(erin)
check("declined: erin is told so, and has no card",
      erin.store.call_card(A, hosted.id) is None and
      M.Toast.current is not None and
      M.Toast.current.text == "alice did not let you in")
alice.store.end_join_links("call", hosted.id)
check("its call over, the link takes no asks", alice.store.join_link(
    cdata["l"]) is None and alice.join_link_for("call", "f" * 32) is None)
asked_as = []
ask_sheet = M.AskToJoinSheet(erin, cdata)
ask_sheet.mine = [{"address": "Fother", "username": "erin_work"}]
ask_sheet.chosen = "Fother"
erin._activate_identity = lambda address: asked_as.append(address)
erin.ask_to_join = lambda data: asked_as.append(data["l"])
ask_sheet._ask()
del erin._activate_identity, erin.ask_to_join
check("asking as another identity of one's own switches to it first "
      "(INVITEAS)", asked_as == ["Fother", cdata["l"]])

print("\n18.0: one more person into a 1-to-1 call (F19)")
grown = M.new_group_call(B, A, False)
grown.room, grown.grew_from = "cd" * 8, "e" * 32
check("a room grown from a 1-to-1 call says which call it grew from",
      M.call_invite(grown)["up"] == "e" * 32)
relay.mail.clear()
drained(bob)
alice.invite_to_room(grown, B)
deliver(relay, parties)
check("its invite lands in the 1-to-1 chat, as a card there",
      bob.store.call_card(A, grown.id) is not None)
check("...and the app is told to follow, if it was in that call",
      ("call_follow", A, grown.id, "e" * 32) in drained(bob))
joined = []
bob.join_call = lambda session, state: joined.append(
    (session.id, session.me.mic, state is not None))
bob.__dict__["_last_direct"] = ("e" * 32, A, time.time(), False)
bob.follow_call(A, grown.id, "e" * 32)
first = list(joined)
joined.clear()
bob.__dict__["_last_direct"] = ("e" * 32, A, time.time() - 600, False)
bob.follow_call(A, grown.id, "e" * 32)
bob.__dict__["_last_direct"] = ("f" * 32, A, time.time(), False)
bob.follow_call(A, grown.id, "e" * 32)
del bob.join_call
check("bob's app follows into it at once, the microphone as it was",
      first == [(grown.id, True, True)])
check("...but not into a room from another call, or long after",
      joined == [])
stranger = M.new_group_call(A, E, False)
stranger.room = "ef" * 8
erin.invite_to_room(stranger, A)
deliver(relay, parties)
check("a call's room offered by someone not yet accepted is let go",
      alice.store.call_card(E, stranger.id) is None)
direct = M.Call(B, outgoing=True, call_id=uuid.uuid4().hex)
direct.connected_at = time.time()
direct.session = M.CallSession(B, A, group=False, video=False, host=A,
                               session_id=direct.id)
alice.call, alice.api._features["rooms"] = direct, True


def menu_words():
    """The rows of the menu on top, as they read; then it goes."""
    sheets = [v for v in M.Window.children if isinstance(v, M.MenuSheet)]
    words = [w.text for w in sheets[0].walk()
             if isinstance(w, M.Label)] if sheets else []
    for view in sheets:
        view.dismiss(animation=False)
    return words


direct.state = "active"
alice.call_more()
check("the 1-to-1 call's More offers Add someone once it is answered (a "
      "1-to-1 call is never on the group call's screen)",
      "Add someone" in menu_words())
alice.set_blocked([D], True)
alice.choose_call_addition()
offered = menu_words()
alice.set_blocked([D], False)
check("...any of your contacts but the one on the call and the blocked",
      "cara" in offered and "bob" not in offered and "dave" not in offered,
      offered)
with alice.store._lock:
    alice.store._db.execute("UPDATE contacts SET caps = caps & ? WHERE "
                            "address = ?", (~M.LocalStore.CAP_V18, B))
    alice.store._db.commit()
check("adding someone to a call with an app older than 18.0 says why not",
      not alice.add_to_call(K) and not direct.ended)
alice.store.add_caps(B, M.LocalStore.CAP_V18)
moved = []
alice.join_call = lambda session, state: moved.append(session)
relay.mail.clear()
check("adding cara: the 1-to-1 call ends, and a room grown from it is "
      "joined, asking cara in", alice.add_to_call(K) and direct.ended and
      len(moved) == 1 and moved[0].grew_from == direct.id and
      moved[0].also == [K] and moved[0].joined)
wait_for(lambda: relay.mail)
end = seen(relay.mail[0])[2].get("ctl", {}) if relay.mail else {}
check("...the other side is told the call ended",
      end.get("op") == "end" and end.get("id") == direct.id)
del alice.join_call
alice.call = None
relay.mail.clear()

print("\n18.0: notes on calls")
over = M.CallSession(B, A, group=False, video=False, host=A)
over.stage, over.outcome, over.ended_at = "ended", "unanswered", time.time()
for party, chat in ((alice, B), (bob, A)):
    party.store.add_call_card(chat, M.card_of_session(over))
alice.open_conversation(B)
pump(alice)
ascreen = alice.conversation_screen
card = alice.__dict__["_cards"].get(over.id)
check("a 1-to-1 call nobody answered offers Leave a note on its card",
      card is not None and "leave a note" in [
          w.text.lower() for w in card.walk() if isinstance(w, M.Btn)])
row = alice.store.message_by_cid(B, over.id)
ascreen.note_on_call(over, row)
note = ascreen.take_reply()
check("...which writes a reply to the call, by the call's id",
      note == {"snip": "Voice call", "from": A, "kind": "call",
               "cid": over.id})
relay.mail.clear()
alice.send_text(B, "Call me when you land", reply=note)
wait_for(lambda: relay.mail)
deliver(relay, parties)
check("...and the other side finds it under the same call",
      [m["body"] for m in bob.store.replies_to(A, over.id)] ==
      ["Call me when you land"])
alice.go_back_to_chats()

met = M.new_group_call(gpeer, A, False)
met.room, met.stage, met.ended_at = "ab" * 8, "ended", time.time()
for party in (alice, bob):
    party.store.add_call_card(gpeer, M.card_of_session(met))
mrow = alice.store.message_by_cid(gpeer, met.id)
relay.mail.clear()
for text in ("Minutes are in the folder", "Next one Friday?"):
    alice.send_text(gpeer, text, reply=ascreen._call_reply(met, mrow))
alice.send_text(gpeer, "Unrelated chatter")
members = len(alice.store.group(M.gid_of(gpeer))["members"])
wait_for(lambda: len(relay.mail) >= 3 * (members - 1))   # each, to each
deliver(relay, parties)
check("a group call's notes are the replies to its card",
      [m["body"] for m in bob.store.replies_to(gpeer, met.id)] ==
      ["Minutes are in the folder", "Next one Friday?"])
bob.open_conversation(gpeer)
pump(bob)
bscreen = bob.conversation_screen
gcard = bob.__dict__["_cards"].get(met.id)
check("its card says how many, as a way into them",
      gcard is not None and "2 notes" in [w.text for w in gcard.walk()
                                          if isinstance(w, M.Label)])
brow = bob.store.message_by_cid(gpeer, met.id)
bscreen.note_on_call(met, brow)
pump(bob)
shown = sorted(b.msg["body"] for b in bscreen._bubbles.values())
check("opened, the chat shows the notes alone, under a bar back to it",
      shown == ["Minutes are in the folder", "Next one Friday?"] and
      bscreen.thread_bar.opacity == 1, shown)
check("...and what is written there replies to the call",
      bscreen.take_reply()["cid"] == met.id)
bscreen.set_reply(next(m for m in bob.store.replies_to(gpeer, met.id)
                       if m["body"] == "Next one Friday?"))
bob.send_text(gpeer, "Friday works", reply=bscreen.take_reply())
pump(bob)
shown = sorted(b.msg["body"] for b in bscreen._bubbles.values())
check("a note quoted there: the answer stays in the thread, one of the "
      "call's notes", "Friday works" in shown and
      len(bob.store.replies_to(gpeer, met.id)) == 3, shown)
# Back closes the sheet on top first, and alice's and bob's apps share one
# window here: what the join steps left open on hers goes now.
for view in [v for v in M.Window.children if isinstance(v, M.ModalView)]:
    view.dismiss(animation=False)
wait_for(lambda: not any(isinstance(v, M.ModalView)
                         for v in M.Window.children), 2)
bob._on_keyboard(None, 27)
pump(bob)
check("Back closes the thread, not the chat: the whole chat is back",
      "Unrelated chatter" in {b.msg["body"]
                              for b in bscreen._bubbles.values()} and
      bscreen.thread is None and bscreen.take_reply() is None and
      bob.manager.current == "conversation",
      (bscreen.thread is None, bob.manager.current,
       [type(v).__name__ for v in M.Window.children]))
bob.go_back_to_chats()

print("\n18.0.1: a chat's messages in their order")
# The relay numbers messages as they come, and the other side's chat shows
# them in that order: what is written one after another goes so.
words = [f"step {n} of 8" for n in range(1, 9)]
relay.mail.clear()
for text in words:
    alice.send_text(bob.keys.address, text)
wait_for(lambda: len(relay.mail) >= len(words))
deliver(relay, parties)
got = [r["body"] for r in bob.store.messages_for(alice.keys.address)][-8:]
check("messages written one after another are in that order on the other "
      "side", got == words, got)
mine = [r["id"] for r in alice.store.messages_for(bob.keys.address)][-8:]
alice.forward_messages(mine, [bob.keys.address])
wait_for(lambda: len(relay.mail) >= len(words))
deliver(relay, parties)
got = [r["body"] for r in bob.store.messages_for(alice.keys.address)][-8:]
check("...and so are several forwarded at once", got == words, got)
last = alice.store.messages_for(bob.keys.address)[-1]
for emoji in ("👍", "👍", "❤️"):          # given, taken back, another
    alice.react(last["id"], emoji)
wait_for(lambda: len(relay.mail) >= 3)
deliver(relay, parties)
given = bob.store.reactions_of(alice.keys.address, last["client_msg_id"],
                               alice.keys.address)
check("...and a chat's reactions: what was taken back at once stays so",
      given == ["❤️"], given)

print("\n18.1: away replies")
# Settings › Away: a message in a chat with one person is answered with a
# few words of the user's own. Every party here reads one prefs.json, so
# while Away is on they are all away: what two away devices do to each
# other is in every check below.
ivy, jon = make_party("ivy", relay), make_party("jon", relay)
I, J = ivy.keys.address, jon.keys.address
parties.update({I: ivy, J: jon})
ivy.store.upsert_contact(J, "jon", jon.keys.public_key_hex)
jon.store.upsert_contact(I, "ivy", ivy.keys.public_key_hex)


def autos(party, peer):
    """The automatic replies in a party's chat `peer`: (direction, words)."""
    return [(m["direction"], m["body"])
            for m in party.store.messages_for(peer)
            if M.message_ext(m).get("auto")]


def hand_over():
    """The relay's oldest message goes to the one it is for."""
    if not wait_for(lambda: relay.mail):
        return None
    msg = relay.mail.pop(0)
    parties[msg["recipient"]].worker._ingest_message(msg)
    return msg


def written(sender, to, text):
    """`sender` writes in chat `to`, and it arrives. An automatic reply to
    it arrives too: returned as the relay had it (None without one)."""
    me, there = sender.keys.address, parties[to]
    had = len(autos(there, me))
    relay.mail.clear()
    sender.send_text(to, text)
    hand_over()
    return hand_over() if len(autos(there, me)) > had else None


def away_row(app):
    """The chat list's reminder that Away is on, if it shows."""
    return next((w for w in app.chats_screen.list.children
                 if isinstance(w, M.ArchiveRow) and any(
                     getattr(c, "text", "") == "Away replies are on"
                     for c in w.children)), None)


def away_switch(app):
    """Settings › Away's switch, flipped as a tap does."""
    app.open_settings()
    next(w for w in app.settings_screen.away_card.walk(restrict=True)
         if isinstance(w, M.ToggleSwitch)).flip()
    app.close_settings()


def away_sheet(app):
    """Settings › Away › What the reply says, opened."""
    app.settings_screen._edit_away()
    return next(v for v in M.Window.children
                if isinstance(v, M.TextPromptSheet))


check("off: a message gets no reply",
      M.away_since() == 0 and written(jon, I, "Are you there?") is None
      and autos(ivy, J) == [])
ivy.go_back_to_chats()
away_switch(ivy)
check("Settings › Away turns it on, and the chat list says that it is on",
      M.away_since() > 0 and away_row(ivy) is not None)
away_row(ivy).dispatch("on_release")
check("...a tap on that opens Settings", ivy.manager.current == "settings")
ivy.close_settings()

usual = M.tr(M.AWAY_DEFAULT)
env = written(jon, I, "Hello Ivy")
check("on: a message is answered with the usual words",
      autos(ivy, J) == [("out", usual)], autos(ivy, J))
check("...the chat remembers when",
      M.away_since() <= ivy.store.get_contact(J)["away_at"] <= time.time())
check("...it goes out marked as an automatic reply",
      env is not None and seen(env)[2].get("k") == "text"
      and seen(env)[2].get("auto") == 1, env and seen(env)[2])
check("...and an automatic reply is not answered by one",
      autos(jon, I) == [("in", usual)], autos(jon, I))
check("a second message soon after gets none",
      written(jon, I, "One more thing") is None
      and len(autos(ivy, J)) == 1)

M.save_pref("away_since", time.time() - 2 * M.AWAY_EVERY)
ivy.store.set_away_at(J, time.time() - M.AWAY_EVERY + 60)
check("...nor one a minute short of four hours later",
      written(jon, I, "Hello?") is None)
ivy.store.set_away_at(J, time.time() - M.AWAY_EVERY - 1)
check("...and one after four hours does",
      written(jon, I, "Hello again") is not None
      and len(autos(ivy, J)) == 2 == len(autos(jon, I)))

ivy.open_settings()
was = M.away_since()
away_sheet(ivy)._save()
check("the words, saved as they were, change nothing",
      M.away_since() == was and written(jon, I, "Still there?") is None)
box = away_sheet(ivy)
box.entry.text = "  Back on Monday.  "
box._save()
check("new words are kept, and every chat is due them",
      M.away_words() == "Back on Monday."
      and M.away_since() > ivy.store.get_contact(J)["away_at"]
      and "Back on Monday." in [
          w.hint.text for w in ivy.settings_screen.away_card.children
          if isinstance(w, M.ListRow) and w.hint])
ivy.close_settings()
check("...the next message gets them",
      written(jon, I, "And now?") is not None
      and autos(jon, I)[-1] == ("in", "Back on Monday."), autos(jon, I))

ivy.store.set_away_at(J, 0)
relay.mail.clear()
club = jon.create_group("Away Club", [I])
hand_over()                                         # the invitation
jon.send_text(club, "Anyone about?")
hand_over()
check("a group's messages get none, nor does an invitation to one",
      ivy.store.messages_for(club)[-1]["body"] == "Anyone about?"
      and autos(ivy, club) == [] and len(autos(ivy, J)) == 3,
      (autos(ivy, club), autos(ivy, J)))

alice.store.upsert_contact(I, "ivy", ivy.keys.public_key_hex)
check("a request gets none: a reply would accept it",
      written(alice, I, "Hello, is this Ivy?") is None
      and ivy.store.is_request(A) and autos(ivy, A) == [])
relay.mail.clear()
ivy.accept_request(A)
hand_over()                                         # "accepted"
pump(ivy)
check("...and once accepted, it does",
      written(alice, I, "Thank you!") is not None
      and autos(alice, I) == [("in", "Back on Monday.")], autos(alice, I))
check("none to notes to self, nor to someone unknown",
      ivy.away_reply(I, None) is None and ivy.away_reply(B, None) is None)
check("the mark is kept only as a plain yes",
      M.sanitize_ext({"auto": 1}).get("auto") == 1
      == M.sanitize_ext({"auto": True}).get("auto")
      and not any("auto" in M.sanitize_ext({"auto": v})
                  for v in (0, 2, "1", [1], None)))
M.save_pref("away_text", 7)
check("words that are not words are not sent: the usual ones are",
      M.away_words() == usual)
M.save_pref("away_text", "y" * 300)
check("...and long ones are cut", M.away_words() == "y" * M.AWAY_MAX)
M.save_pref("away_text", "Back on Monday.")

notes = []
for app, peer in ((ivy, J), (jon, I)):
    app.open_conversation(peer)
    pump(app)
    wait_for(lambda: app.conversation_screen._bubbles)
    notes.append([getattr(b.fwd, "text", None)
                  for b in app.conversation_screen._bubbles.values()
                  if M.message_ext(b.msg).get("auto")])
    app.go_back_to_chats()
check("both sides' chats say which messages are automatic replies",
      all(n and set(n) == {"Automatic reply"} for n in notes), notes)

# The background service's app (the phone, with Ferritext closed) answers
# too, and the app sees that it has when it is opened again.
svc = M.HeadlessApp(ivy.keys)
svc.api = StubApi(relay, ivy.keys)
svc.worker = M.SyncWorker(svc)
parties[I] = svc
ivy.store.set_away_at(J, 0)
env = written(jon, I, "Is your phone on?")
parties[I] = ivy
check("the background service answers as the app does, once between them",
      env is not None and seen(env)[2].get("auto") == 1
      and written(jon, I, "Good.") is None)
svc.close()

ivy.store.set_away_at(J, 0)
away_switch(ivy)
check("off again: the reminder goes, and so do the replies",
      M.away_since() == 0 and away_row(ivy) is None
      and written(jon, I, "Welcome back") is None)
M.save_pref("away_text", "")

print("\n19.0: pinned messages (PINS26)")


def pin_cids(app, peer):
    return [m["client_msg_id"] for m in app.store.pins(peer)]


def said(app, peer, words):
    return any(m.get("kind") == "event" and words in (m.get("body") or "")
               for m in app.store.messages_for(peer))


def sent_and_delivered(action):
    relay.mail.clear()
    action()
    wait_for(lambda: relay.mail)
    deliver(relay, parties)
    for party in parties.values():
        pump(party)


sent_and_delivered(lambda: alice.send_text(B, "the plan for Saturday"))
sent_and_delivered(lambda: alice.send_text(B, "the address"))
plan, address = [m["client_msg_id"] for m in alice.store.messages_for(B)
                 if m.get("body") in ("the plan for Saturday",
                                      "the address")]
sent_and_delivered(lambda: alice.pin_message(B, plan, True))
check("pinned in a chat of two: both sides hold it, a line in each chat",
      pin_cids(alice, B) == [plan] and pin_cids(bob, A) == [plan] and
      said(alice, B, "You pinned a message") and
      said(bob, A, "alice pinned a message"))
time.sleep(0.01)
sent_and_delivered(lambda: bob.pin_message(A, address, True))
check("either of the two may pin; the newest pin first",
      pin_cids(alice, B) == [address, plan] and
      pin_cids(bob, A) == [address, plan])
sent_and_delivered(lambda: bob.pin_message(A, plan, False))
check("unpinned by either: gone on both sides, nothing said",
      pin_cids(alice, B) == [address] and pin_cids(bob, A) == [address])
check("a pin older than the unpin held changes nothing (newest wins)",
      not alice.store.set_pin(B, plan, B, "2000-01-01T00:00:00+00:00",
                              True) and pin_cids(alice, B) == [address])
time.sleep(0.01)
sent_and_delivered(lambda: alice.pin_message(B, plan, True))
sent_and_delivered(lambda: alice.unpin_all(B))
check("Unpin all: nothing pinned on either side",
      pin_cids(alice, B) == [] and pin_cids(bob, A) == [])
for n in range(M.PINS_MAX + 3):
    alice.store.set_pin(A, f"{n:032x}", A, f"2026-01-01T00:00:{n:02d}",
                        True)
held = alice.store._db.execute(
    "SELECT COUNT(*) FROM pins WHERE peer = ? AND on_ = 1", (A,)
).fetchone()[0]
check("a chat keeps at most fifty pins: the oldest go", held == M.PINS_MAX)

relay.mail.clear()
ppeer = alice.create_group("Pinboard", [B, K])
pgid = M.gid_of(ppeer)
wait_for(lambda: len(relay.mail) >= 2)
deliver(relay, parties)
for party in parties.values():
    pump(party)
sent_and_delivered(lambda: alice.send_text(ppeer, "rota for March"))
rota = next(m["client_msg_id"] for m in alice.store.messages_for(ppeer)
            if m.get("body") == "rota for March")
check("a member who is not an admin may not pin (by default)",
      not cara.can_pin(ppeer) and alice.can_pin(ppeer))
relay.mail.clear()
threading.Thread(target=lambda: cara._send_control(
    ppeer, {"op": "pin", "cid": rota, "on": True}, "x"), daemon=True).start()
wait_for(lambda: relay.mail)
deliver(relay, parties)
check("...and a pin sent all the same is not taken by the others",
      pin_cids(alice, ppeer) == [] and pin_cids(bob, ppeer) == [])
sent_and_delivered(lambda: alice.pin_message(ppeer, rota, True))
check("the group's owner pins: every member holds it",
      pin_cids(bob, ppeer) == [rota] and pin_cids(cara, ppeer) == [rota])
sent_and_delivered(lambda: alice.set_group_permissions(pgid, [],
                                                       members_pin=True))
check("the group's rule: members may pin, said in the chat",
      bob.can_pin(ppeer) and cara.can_pin(ppeer) and
      said(alice, ppeer, "You let members pin messages"))
sent_and_delivered(lambda: cara.send_text(ppeer, "who brings the urn?"))
urn = next(m["client_msg_id"] for m in cara.store.messages_for(ppeer)
           if m.get("body") == "who brings the urn?")
time.sleep(0.01)
sent_and_delivered(lambda: cara.pin_message(ppeer, urn, True))
check("...and a member's pin is taken by everyone",
      pin_cids(alice, ppeer) == [urn, rota] and
      pin_cids(bob, ppeer) == [urn, rota])
relay.mail.clear()
threading.Thread(target=lambda: alice._send_control(
    ppeer, {"op": "pin", "cid": "f" * 32, "on": True}, "x"),
    daemon=True).start()
wait_for(lambda: relay.mail)
deliver(relay, parties)
check("a pin of a message this device does not hold is kept, not shown",
      bob.store.pinned(ppeer, "f" * 32) and "f" * 32 not in
      pin_cids(bob, ppeer))
sent_and_delivered(lambda: alice.set_group_permissions(pgid, [],
                                                       members_pin=False))
check("the rule turned off: only admins again",
      not cara.can_pin(ppeer) and alice.can_pin(ppeer) and
      said(alice, ppeer, "Only admins can pin messages now"))
check("an older app's reading of the state keeps nothing new: the rule "
      "rides in the rules, and only 19.0 reads it",
      "pin" not in M.sanitize_group_rules({"pin": "yes"}, {B, K}) and
      M.sanitize_group_rules({"pin": True}, {B, K}) == {"pin": True})
probe_db = os.path.join(os.environ["HOME"], "pins_migration.db")
store13 = M.LocalStore(Path(probe_db))
store13._db.execute("DROP TABLE pins")
store13._db.execute("PRAGMA user_version = 13")
store13._db.commit()
store13.close()
store14 = M.LocalStore(Path(probe_db))
check("a store from 18.x opens with the pins table, as schema 14",
      store14._db.execute("PRAGMA user_version").fetchone()[0] == 14 and
      store14.pins("x") == [])
store14.close()

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
