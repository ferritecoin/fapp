#!/usr/bin/env python3
"""Profile and group pictures, end to end.

    xvfb-run -a python3 desktop/test_pictures.py main.py

The pixels (cropping, size limits, refusing what should not be kept), the
store (versions only move forward), and the protocol between real app
controllers over an in-memory relay: who is sent a picture and when, asking
for one that went missing, a group's picture from its admin only, removal,
blocking. Then the screens: discs that follow their picture, the framing
sheet, and the pages a disc opens.
"""
import importlib.util
import io
import os
import sys
import tempfile
import time

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-pictures-")
os.environ["FEXT_SERVER_HOST"] = "127.0.0.1:1"

_spec = importlib.util.spec_from_file_location(
    "fext_pictures", sys.argv[1] if len(sys.argv) > 1 else "main.py")
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_pictures"] = M
_spec.loader.exec_module(M)

from PIL import Image, ImageDraw  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""))


class Relay:
    def __init__(self):
        self.mail = []
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

    def read(self, ids):
        return {"updated": len(ids)}


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


def wait_for(cond, timeout=5.0):
    from kivy.clock import Clock
    end = time.time() + timeout
    while time.time() < end:
        Clock.tick()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def opened(party, msg):
    return party.crypto.open_envelope(msg["envelope"], msg["sender"],
                                      cid=msg["client_msg_id"])


def deliver(relay, parties):
    n = 0
    while relay.mail:
        msg = relay.mail.pop(0)
        parties[msg["recipient"]].worker._ingest_message(msg)
        n += 1
    return n


def pump(app):
    app._drain_ui_queue(0)


def settle(relay, parties, rounds=6):
    """Deliver, run each app's queue (which may start sends), and repeat
    until nothing more moves."""
    for _ in range(rounds):
        time.sleep(0.05)
        moved = deliver(relay, parties)
        for p in parties.values():
            pump(p)
        wait_for(lambda: relay.mail, 0.4)
        if not moved and not relay.mail:
            break


def photo(width, height, seed=1):
    """A photo-like picture: smooth gradients and a few shapes."""
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(0, height, 4):
        draw.rectangle((0, y, width, y + 4),
                       fill=(40 + y * 150 // height, 90, 160 - y * 90 // height))
    draw.ellipse((width * .3, height * .2, width * .7, height * .8),
                 fill=(200, 160 + seed % 60, 90))
    return image


def jpeg(image, quality=85, **kw):
    out = io.BytesIO()
    image.save(out, "JPEG", quality=quality, **kw)
    return out.getvalue()


tmp = tempfile.mkdtemp(prefix="fext-pic-src-")

print("\nPixels")
big = os.path.join(tmp, "big.jpg")
with open(big, "wb") as fh:
    fh.write(jpeg(photo(4000, 3000)))
source = M.open_picture_source(big)
check("a large photo is opened no bigger than cropping needs",
      max(source.size) <= M.PICTURE_SOURCE_EDGE and source.mode == "RGB")
data = M.picture_jpeg(source)
with Image.open(io.BytesIO(data)) as im:
    check("a picture is a square JPEG of the set size, within the limit",
          im.format == "JPEG" and im.size == (M.PICTURE_EDGE,
                                              M.PICTURE_EDGE) and
          len(data) <= M.PICTURE_MAX_BYTES, f"{im.size} {len(data)}")
noise = Image.frombytes("RGB", (900, 900), os.urandom(900 * 900 * 3))
loud = M.picture_jpeg(noise)
check("even noise, which JPEG cannot squeeze, is brought within the limit",
      len(loud) <= M.PICTURE_MAX_BYTES, str(len(loud)))

# A camera's rotation flag is applied to the pixels.
exif = Image.Exif()
exif[0x0112] = 6                                 # rotate 90 degrees
rot = os.path.join(tmp, "rot.jpg")
with open(rot, "wb") as fh:
    fh.write(jpeg(photo(800, 400), exif=exif.tobytes()))
check("a camera's rotation is applied (a sideways shot comes out upright)",
      M.open_picture_source(rot).size == (400, 800))

src_exif = Image.Exif()
src_exif[0x8827] = 400                           # ISO speed
src_exif[0x0110] = "Secret Camera"
tagged = os.path.join(tmp, "tagged.jpg")
with open(tagged, "wb") as fh:
    fh.write(jpeg(photo(600, 600), exif=src_exif.tobytes()))
made = M.picture_jpeg(M.open_picture_source(tagged))
check("nothing of the original file's details goes with the picture",
      b"Secret Camera" not in made and b"Exif" not in made)

check("someone else's picture is drawn again here",
      M.clean_picture(data) is not None and
      Image.open(io.BytesIO(M.clean_picture(data))).size ==
      (M.PICTURE_EDGE, M.PICTURE_EDGE))
png = io.BytesIO()
photo(200, 120).save(png, "PNG")
cleaned = M.clean_picture(png.getvalue())
check("a PNG is taken, centre-cropped to a square JPEG",
      cleaned is not None and cleaned[:2] == b"\xff\xd8" and
      Image.open(io.BytesIO(cleaned)).size == (M.PICTURE_EDGE,
                                               M.PICTURE_EDGE))
gif = io.BytesIO()
photo(64, 64).save(gif, "GIF")
huge = io.BytesIO()
Image.new("RGB", (3000, 3000), (9, 9, 9)).save(huge, "PNG")
check("refused: not a picture, a GIF, too many pixels, too many bytes",
      M.clean_picture(b"\x00junk" * 50) is None and
      M.clean_picture(gif.getvalue()) is None and
      M.clean_picture(huge.getvalue()) is None and
      M.clean_picture(b"\xff\xd8" + b"\x00" * (M.PICTURE_ACCEPT_BYTES + 1))
      is None)
now = int(time.time())
check("versions are times, and always rise",
      M.next_picture_version(0) >= now and
      M.next_picture_version(now + 100) == now + 101)

print("\nThe store")
st = M.LocalStore(M.DATA_DIR / "pic_test.db")
check("a picture is recorded", st.set_picture("x", 5, "/a") and
      st.picture_version("x") == 5 and st.picture_paths() == {"x": "/a"})
check("...and never replaced by an older or equal version",
      not st.set_picture("x", 5, "/b") and not st.set_picture("x", 3, "/c")
      and st.picture("x")["path"] == "/a")
check("a newer version replaces it, a removal included",
      st.set_picture("x", 9, None) and st.picture_paths() == {} and
      st.picture_version("x") == 9)
st.set_picture_sent("k", 7)
st.set_picture_sent("k", 4)
check("what was sent only moves forward too", st.picture_sent("k") == 7 and
      st.picture_sent("nobody") == 0)
st.close()

print("\nBetween two people")
relay = Relay()
alice, bob = make_party("alice", relay), make_party("bob", relay)
parties = {alice.keys.address: alice, bob.keys.address: bob}
A, B = alice.keys.address, bob.keys.address
for me, other in ((alice, bob), (bob, alice)):
    me.store.upsert_contact(other.keys.address, other.keys.username,
                            other.keys.public_key_hex)

alice.send_text(B, "hello")
wait_for(lambda: relay.mail)
x = opened(bob, relay.mail[0])["x"]
check("every message says what its app can do (x.ft)",
      x.get("ft") == M.FEATURES and "pv" not in x)
settle(relay, parties)
check("...which the other side records as capabilities",
      bob.store.get_contact(A)["caps"] & M.LocalStore.CAP_PIC)
check("no picture yet: nothing but the message was sent",
      bob.store.picture_version(A) == 0)

alice.set_picture(M.picture_jpeg(photo(500, 500, 3)))
wait_for(lambda: True, 0.5)
check("setting a picture before they have written sends it to nobody",
      not relay.mail and alice.store.picture_version(A) > 0)
pa = M.PICTURES.path(A)
check("...and it is kept, and shown on this identity's discs",
      pa and os.path.isfile(pa) and
      M.Avatar(A, "alice").has_picture)

bob.send_text(A, "hi")
wait_for(lambda: relay.mail)
settle(relay, parties)
check("once they have written, alice knows bob's app keeps pictures",
      alice.store.get_contact(B)["caps"] & M.LocalStore.CAP_PIC)
alice.send_text(B, "a picture comes first")
wait_for(lambda: len(relay.mail) >= 2)
ops = [opened(bob, m)["x"].get("ctl", {}).get("op") for m in relay.mail]
check("the next message is preceded by the picture (a 'pic' control)",
      ops == ["pic", None], str(ops))
sealed = len(relay.mail[0]["envelope"]["ciphertext"])
check("...which fits easily inside one sealed message",
      sealed < 96 * 1024, str(sealed))
settle(relay, parties)
row = bob.store.picture(A)
check("bob keeps it, at alice's version",
      row and row["version"] == alice.store.picture_version(A) and
      os.path.isfile(row["path"]))
check("...in a file this device wrote itself",
      Image.open(row["path"]).format == "JPEG")
alice.send_text(B, "again")
wait_for(lambda: relay.mail)
check("it is not sent again with later messages",
      len(relay.mail) == 1 and
      opened(bob, relay.mail[0])["x"].get("pv") ==
      alice.store.picture_version(A))
settle(relay, parties)

# Bob loses it (a reinstall, say): alice's next message says which version
# is current, and bob's app asks for it.
M.discard_picture_file(row["path"])
bob.store._db.execute("DELETE FROM pictures")
bob.store._db.commit()
alice.send_text(B, "do you see me?")
wait_for(lambda: relay.mail)
deliver(relay, parties)
pump(bob)
wait_for(lambda: relay.mail, 2)
check("a device that lost the picture asks for it ('picreq')",
      relay.mail and opened(alice, relay.mail[0])["x"]["ctl"]["op"]
      == "picreq")
settle(relay, parties)
check("...and is sent it again", bob.store.picture_version(A) ==
      alice.store.picture_version(A) and bob.store.picture(A)["path"])
alice.answer_picture_request(B, None)
check("asking again at once is not answered (once in ten minutes)",
      not alice.answer_picture_request(B, None) and not relay.mail)

old_path = bob.store.picture(A)["path"]
v_before = bob.store.picture_version(A)
alice.set_picture(M.picture_jpeg(photo(500, 500, 40)))
wait_for(lambda: relay.mail, 2)
settle(relay, parties)
check("a changed picture is sent at once, and replaces the old one",
      bob.store.picture_version(A) > v_before and
      bob.store.picture(A)["path"] == old_path and os.path.isfile(old_path))
alice.set_picture(None)
wait_for(lambda: relay.mail, 2)
settle(relay, parties)
check("removing it removes it on the other side, file and all",
      bob.store.picture(A)["path"] is None and not os.path.isfile(old_path))

# Something bob's app will not keep counts as that version (so it is not
# asked for over and over), and leaves no picture.
bad = io.BytesIO()
Image.new("RGB", (2400, 2400), (1, 2, 3)).save(bad, "PNG", optimize=True)
v_bad = M.next_picture_version(alice.store.picture_version(A))
alice._send_control_to(B, {"op": "pic", "v": v_bad,
                           "d": M.b64e(bad.getvalue())}, "x")
settle(relay, parties)
check("a picture it will not keep is refused, its version remembered",
      bob.store.picture_version(A) == v_bad and
      bob.store.picture(A)["path"] is None)
alice._send_control_to(B, {"op": "pic", "v": v_bad - 5,
                           "d": M.b64e(M.picture_jpeg(photo(300, 300)))},
                       "x")
settle(relay, parties)
check("an older version arriving late changes nothing",
      bob.store.picture_version(A) == v_bad and
      bob.store.picture(A)["path"] is None)

# Someone whose app does not keep pictures is not sent one.
cara = make_party("cara", relay)
parties[cara.keys.address] = cara
K = cara.keys.address
for p, q in ((alice, cara), (cara, alice), (bob, cara), (cara, bob)):
    p.store.upsert_contact(q.keys.address, q.keys.username,
                           q.keys.public_key_hex)
alice.store.add_caps(K, M.LocalStore.CAP_V9)          # 9.1.1: no pictures
alice.set_picture(M.picture_jpeg(photo(400, 400, 9)))
wait_for(lambda: relay.mail, 2)
time.sleep(0.4)
recipients = {m["recipient"] for m in relay.mail}
check("an app that keeps no pictures is never sent one",
      K not in recipients and B in recipients, str(recipients))
settle(relay, parties)

bob.set_blocked([A], True)
v_held = bob.store.picture_version(A)
alice.set_picture(M.picture_jpeg(photo(400, 400, 11)))
wait_for(lambda: relay.mail, 2)
settle(relay, parties)
check("someone you blocked cannot change the picture you see",
      bob.store.picture_version(A) == v_held)
check("...and you send yours to nobody you blocked",
      not bob._picture_wanted_by(bob.store.get_contact(A)))
bob.set_blocked([A], False)

print("\nGroups")
for p in (alice, bob, cara):
    p.store.add_caps(alice.keys.address, M.LocalStore.CAP_PIC
                     | M.LocalStore.CAP_V9)
alice.store.add_caps(K, M.LocalStore.CAP_PIC)        # cara upgraded
gpeer = alice.create_group("Walkers", [B])
gid = M.gid_of(gpeer)
wait_for(lambda: relay.mail)
settle(relay, parties)
check("bob is in alice's group", (bob.store.group(gid) or {}).get("state")
      == "active")
check("only the admin may set a group's picture",
      not bob.set_picture(M.picture_jpeg(photo(300, 300)), gid))
alice.set_picture(M.picture_jpeg(photo(400, 400, 21)), gid)
wait_for(lambda: relay.mail, 2)
ops = [opened(parties[m["recipient"]], m)["x"]["ctl"]["op"]
       for m in relay.mail]
check("the admin's group picture goes to each member ('gpic')",
      ops == ["gpic"], str(ops))
settle(relay, parties)
check("bob keeps the group's picture under the group",
      bob.store.picture(gpeer) and bob.store.picture(gpeer)["path"] and
      bob.store.picture_version(gpeer) == alice.store.picture_version(gpeer))
forged = M.picture_jpeg(photo(300, 300, 50))
alice.add_group_members(gid, [K])
wait_for(lambda: len(relay.mail) >= 2, 3)
settle(relay, parties)
check("someone added later is sent the group's picture too",
      cara.store.picture_version(gpeer) ==
      alice.store.picture_version(gpeer) and cara.store.picture(gpeer)["path"])
v_group = cara.store.picture_version(gpeer)
bob._send_control_to(K, {"op": "gpic", "grp": gid, "v": v_group + 50,
                         "d": M.b64e(forged)}, "x")
settle(relay, parties)
check("a member who is not the admin cannot change it for others",
      cara.store.picture_version(gpeer) == v_group)
bob.current_peer = gpeer
alice.send_text(gpeer, "the admin writes")
wait_for(lambda: len(relay.mail) >= 2)
grp = opened(bob, next(m for m in relay.mail if m["recipient"] == B)
             )["x"]["grp"]
check("the admin's group messages say which group picture is current",
      grp.get("pv") == alice.store.picture_version(gpeer))
settle(relay, parties)

# v12: someone met only in the group. Bob and dee are not each other's
# contacts; dee's picture must still reach bob (and stay end to end).
dee = make_party("dee", relay)
parties[dee.keys.address] = dee
D = dee.keys.address
for p, q in ((alice, dee), (dee, alice)):
    p.store.upsert_contact(q.keys.address, q.keys.username,
                           q.keys.public_key_hex)
    p.store.add_caps(q.keys.address, M.LocalStore.CAP_PIC
                     | M.LocalStore.CAP_V9)
alice.add_group_members(gid, [D])
wait_for(lambda: relay.mail, 3)
settle(relay, parties)
check("someone met only in a group is known to bob, but not listed",
      (dee.store.group(gid) or {}).get("state") == "active" and
      bob.store.get_contact(D) and not bob.store.get_contact(D)["listed"])
dee._on_picture = lambda *_a: None         # keep dee's own copy out of the book
dee.set_picture(M.picture_jpeg(photo(400, 400, 80)))
settle(relay, parties)
bob.send_text(gpeer, "bob writes to the group")
wait_for(lambda: relay.mail)
settle(relay, parties)
dee.send_text(gpeer, "dee writes to the group")
wait_for(lambda: len(relay.mail) >= 4, 3)
to_bob = [opened(bob, m)["x"].get("ctl", {}).get("op")
          for m in relay.mail if m["recipient"] == B]
check("a member's picture goes before their group message to the members "
      "whose apps keep pictures, contacts or not ('pic')",
      to_bob == ["pic", None], str(to_bob))
settle(relay, parties)
check("...bob keeps it at dee's version, in a file of his own",
      bob.store.picture_version(D) == dee.store.picture_version(D) and
      bob.store.picture(D)["path"] and
      os.path.isfile(bob.store.picture(D)["path"]))
check("...and cara, whose app dee had not heard from, asked for it and has "
      "it too", cara.store.picture_version(D) == dee.store.picture_version(D)
      and cara.store.picture(D)["path"])
lost = bob.store.picture(D)["path"]
M.discard_picture_file(lost)
bob.store._db.execute("DELETE FROM pictures WHERE peer = ?", (D,))
bob.store._db.commit()
dee.send_text(gpeer, "still there?")
wait_for(lambda: relay.mail)
settle(relay, parties)
check("...a member who lost it asks again and is answered, in the group too",
      bob.store.picture_version(D) == dee.store.picture_version(D) and
      os.path.isfile(bob.store.picture(D)["path"]))
check("...and dee is still not in bob's chat list for all that",
      not bob.store.get_contact(D)["listed"])

print("\nScreens")
bob._load_pictures()
disc = M.Avatar(gpeer, "Walkers", kind="group")
check("a group's disc shows its picture", disc.has_picture)
plain = M.Avatar(K, "cara")
check("someone without a picture keeps the coloured disc and initial",
      not plain.has_picture and plain._initial.opacity == 1)
# Cara's first picture reaches bob while her disc is on bob's screen. (All
# three apps share one module here, so cara's own copy is kept out of the
# book: only bob's handling of what arrived may put it there.)
cara_disc = M.Avatar(K, "cara")
before = cara_disc.has_picture
bob.send_text(K, "hello cara")
wait_for(lambda: relay.mail)
settle(relay, parties)
cara._on_picture = lambda *_a: None
cara.set_picture(M.picture_jpeg(photo(400, 400, 70)))
wait_for(lambda: relay.mail, 2)
settle(relay, parties)
check("discs already on screen follow a new picture as it arrives",
      not before and cara_disc.has_picture and
      M.PICTURES.path(K) == bob.store.picture(K)["path"])

bob.open_conversation(gpeer)
wait_for(lambda: False, 0.6)
faces = [r.face for r in bob.conversation_screen._rows.values()
         if r.author == D and r.face is not None and r.face.parent is r]
check("in the group, the disc beside dee's messages shows dee's picture",
      faces and faces[-1].has_picture)
bob.open_conversation(A)
header = bob.conversation_screen.peer_avatar_holder.children[0]
check("the chat's disc is a button", isinstance(header, M.AvatarButton))
header.dispatch("on_release")
wait_for(lambda: bob.manager.current == "info", 1)
check("...that opens the chat's page",
      bob.manager.current == "info" and bob.chat_info_screen.peer == A)
check("...whose disc shows the picture full size",
      bob.chat_info_screen.hero_disc.has_picture)

alice._load_pictures()
alice.open_profile()
texts = [w.text for w in alice.profile_screen.walk()
         if isinstance(w, M.Label)]
check("the profile page offers to change the photo",
      "Change the profile photo" in texts)
alice.set_picture(None)
texts = [w.text for w in alice.profile_screen.walk()
         if isinstance(w, M.Label)]
check("...or, without one, to set one", "Set a profile photo" in texts)

frame_src = os.path.join(tmp, "frame.jpg")
with open(frame_src, "wb") as fh:
    fh.write(jpeg(photo(800, 600)))
got = []
sheet = M.PictureCropSheet(frame_src, got.append)
sheet.open()
wait_for(lambda: True, 0.3)
box = sheet.crop_box()
check("framing starts on the centre square of the photo",
      all(abs(a - b) < 2 for a, b in zip(box, (100, 0, 700, 600))),
      str(box))
sheet.zoom(2.0, (sheet.side / 2, sheet.side / 2))
box = sheet.crop_box()
check("zooming in frames a smaller square around the same centre",
      all(abs(a - b) < 3 for a, b in zip(box, (250, 150, 550, 450))),
      str(box))
from kivy.graphics.transformation import Matrix  # noqa: E402
sheet.scatter.apply_transform(Matrix().translate(5000, -5000, 0))
sheet._clamp()
box = sheet.crop_box()
check("dragging past an edge stops at the edge",
      abs(box[0]) < 2 and abs(box[1]) < 2 and abs(box[2] - 300) < 3,
      str(box))
sheet.zoom(0.01, (0, 0))
box = sheet.crop_box()
check("...and zooming out stops where the photo just covers the circle",
      abs((box[2] - box[0]) - 600) < 3, str(box))
sheet._use()
check("using it hands over the framed picture",
      got and Image.open(io.BytesIO(got[0])).size ==
      (M.PICTURE_EDGE, M.PICTURE_EDGE))

passed = sum(1 for _n, c in RESULTS if c)
print(f"\n  {passed}/{len(RESULTS)} passed")
os._exit(0 if passed == len(RESULTS) else 1)
