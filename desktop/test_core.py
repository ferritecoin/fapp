#!/usr/bin/env python3
"""Core (non-UI) regression suite: envelope extension, storage, sync rules.

    xvfb-run -a python3 desktop/test_core.py main.py [old_main.py]

The optional second argument is a PREVIOUS release's client (for example
`git show v8.0.2:main.py > old.py`). When given, compatibility is checked
against that real code rather than against assumptions about it: an old
client must still read what this one sends, and must still open a database
this one has migrated.
"""
import importlib.util
import json
import os
import sys
import tempfile

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
if sys.platform.startswith("linux"):     # xvfb-run's X display
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")
HOME = tempfile.mkdtemp(prefix="fext-core-")
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME                 # Path.home() on Windows
os.environ["FEXT_LANG"] = "en"          # the English the checks read

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


M = load(sys.argv[1] if len(sys.argv) > 1 else "main.py", "fext_new")
OLD = load(sys.argv[2], "fext_old") if len(sys.argv) > 2 else None


class Party:
    """One identity with its own key store, crypto engine and database."""

    def __init__(self, mod, label):
        self.mod = mod
        self.keys = mod.IdentityManager()
        priv, _pub, _addr, _n = mod.mine_identity()
        self.address = self.keys.add_identity(priv, label)
        self.keys.activate(self.address)
        self.crypto = mod.CryptoEngine(self.keys)

    @property
    def pub(self):
        return self.keys.public_key_hex


def expect_reject(fn, why):
    try:
        fn()
    except Exception as exc:                      # noqa: BLE001
        return True, f"{type(exc).__name__}: {exc}"
    return False, f"accepted although {why}"


def section(title):
    print(f"\n{title}")


# =============================================================================
section("Envelope extension block")
alice, bob, carol = Party(M, "alice"), Party(M, "bob"), Party(M, "carol")
x = {"k": "text", "reply": {"cid": "c" * 32, "snip": "earlier message"}}
env, _ = alice.crypto.build_envelope("hello bob", bob.pub, x=x,
                                     recipient=bob.address, cid="a" * 32)
got = bob.crypto.open_envelope(env, alice.address, cid="a" * 32)
check("v9 -> v9: body and extension arrive intact",
      got["body"] == "hello bob" and got["x"]["reply"]["snip"] ==
      "earlier message")
check("the block is stamped with recipient and message id",
      got["x"]["to"] == bob.address and got["x"]["cid"] == "a" * 32)

plain, _ = alice.crypto.build_envelope("no extras", bob.pub)
got = bob.crypto.open_envelope(plain, alice.address, cid="b" * 32)
check("a v2-shaped message (no block) still opens", "x" not in got)

ok, why = expect_reject(
    lambda: bob.crypto.open_envelope(env, alice.address, cid="f" * 32),
    "the relay delivered it under a different message id")
check("rejects a block bound to a different message id", ok, why)

ok, why = expect_reject(
    lambda: alice.crypto.build_envelope("x", bob.pub, x={"k": "text"}),
    "it had no recipient/cid to bind to")
check("refuses to build a block without recipient and id", ok, why)


def reseal(payload, to_pub):
    """Re-encrypt a decrypted payload to someone else — what a recipient
    attempting a replay would do."""
    m = M
    raw = json.dumps(payload, separators=(",", ":")).encode()
    rp = bytes.fromhex(to_pub)
    eph = int.from_bytes(os.urandom(32), "big") % (m._N - 1) + 1
    ephp = m.pub_to_compressed(m.priv_to_pub(eph))
    key = m.CryptoEngine._derive_key(
        m.ecdh_shared_secret(eph, m.compressed_to_pub(rp)))
    nonce = os.urandom(12)
    ct = m.AESGCM(key).encrypt(nonce, raw, ephp + rp)
    return {"ephemeral_public_key": ephp.hex(), "nonce": m.b64e(nonce),
            "ciphertext": m.b64e(ct)}


payload = bob.crypto.open_envelope(env, alice.address, cid="a" * 32)
replay = reseal(payload, carol.pub)
ok, why = expect_reject(
    lambda: carol.crypto.open_envelope(replay, alice.address, cid="a" * 32),
    "Bob re-encrypted Alice's message to Carol")
check("cross-recipient replay of a v9 message is refused", ok, why)

forged = dict(payload, x=dict(payload["x"], reply={"cid": "z", "snip": "!"}))
ok, why = expect_reject(
    lambda: bob.crypto.open_envelope(reseal(forged, bob.pub), alice.address,
                                     cid="a" * 32),
    "the block was edited after signing")
check("an edited block fails its signature", ok, why)

lifted = dict(payload, body="a different body")
ok, why = expect_reject(
    lambda: bob.crypto.open_envelope(reseal(lifted, bob.pub), alice.address,
                                     cid="a" * 32),
    "the block was moved onto another body")
check("a block cannot be lifted onto a different body", ok, why)

if OLD is not None:
    section("Wire compatibility with the previous release (real old code)")
    old_bob = Party(OLD, "old-bob")
    env9, _ = alice.crypto.build_envelope(
        "from a v9 client", old_bob.pub, x={"k": "text", "fwd": 1},
        recipient=old_bob.address, cid="d" * 32)
    try:
        got = old_bob.crypto.open_envelope(env9, alice.address)
        check("old client opens a v9 message and shows its body",
              got["body"] == "from a v9 client")
    except Exception as exc:                      # noqa: BLE001
        check("old client opens a v9 message and shows its body", False,
              f"{type(exc).__name__}: {exc}")
    old_alice = Party(OLD, "old-alice")
    env8, _ = old_alice.crypto.build_envelope("from an old client", bob.pub)
    got = bob.crypto.open_envelope(env8, old_alice.address, cid="e" * 32)
    check("v9 client opens an old client's message",
          got["body"] == "from an old client" and "x" not in got)


# =============================================================================
section("Local storage: migrations and compatibility")
from pathlib import Path                                       # noqa: E402
import sqlite3                                                 # noqa: E402

DB_DIR = Path(tempfile.mkdtemp(prefix="fext-db-"))
PEER = "Fec" + "P" * 31


def user_version(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


if OLD is not None:
    path = DB_DIR / "upgrade.db"
    old = OLD.LocalStore(path)
    old.upsert_contact(PEER, "pal", "02" + "ab" * 32)
    old.add_out(PEER, "out from v8", "2026-01-01T00:00:00+00:00", "o" * 32)
    old.add_in(PEER, "in from v8", "2026-01-01T00:01:00+00:00", 41, "i" * 32)
    old.close()
    new = M.LocalStore(path)
    msgs = new.messages_for(PEER)
    check("an old database opens and keeps every message",
          [m["body"] for m in msgs] == ["out from v8", "in from v8"])
    check("old rows take the new defaults",
          all(m["kind"] == "text" and m["starred"] == 0 and
              m["media_state"] == "" for m in msgs))
    check("the old contact gains notify=1 and caps=0",
          (new.get_contact(PEER) or {}).get("notify") == 1 and
          new.get_contact(PEER).get("caps") == 0)
    check(f"the database is stamped with schema {M.LocalStore.SCHEMA_VERSION}",
          user_version(path) == M.LocalStore.SCHEMA_VERSION)
    new.add_in(PEER, "Photo", "2026-01-02T00:00:00+00:00", 42, "m" * 32,
               kind="image", ext={"media": {"kind": "image"}},
               media_state="pending")
    new.set_starred([msgs[0]["id"]], True)
    new.close()

    # ... and back: the previous release must still open what v9 wrote.
    try:
        old = OLD.LocalStore(path)
        rows = old.messages_for(PEER)
        convs = old.conversations()
        old.add_in(PEER, "v8 again", "2026-01-03T00:00:00+00:00", 43,
                   "j" * 32)
        old.close()
        check("the previous release opens a v9 database",
              len(rows) == 3 and convs and convs[0]["preview"] == "Photo")
        check("...and shows a media message by its readable body",
              rows[-1]["body"] == "Photo")
        new = M.LocalStore(path)
        last = new.messages_for(PEER)[-1]
        check("rows the previous release adds afterwards are well-formed",
              last["body"] == "v8 again" and last["kind"] == "text")
        new.close()
    except Exception as exc:                      # noqa: BLE001
        check("the previous release opens a v9 database", False,
              f"{type(exc).__name__}: {exc}")

path = DB_DIR / "future.db"
st = M.LocalStore(path)
st.close()
con = sqlite3.connect(path)
con.execute("PRAGMA user_version = 99")
con.commit()
con.close()
st = M.LocalStore(path)
st.upsert_contact(PEER, "pal", "02" + "ab" * 32)
st.close()
check("a database from a NEWER release opens and is left at its version",
      user_version(path) == 99)

path = DB_DIR / "twice.db"
M.LocalStore(path).close()
M.LocalStore(path).close()
check("migrating twice is a no-op",
      user_version(path) == M.LocalStore.SCHEMA_VERSION)

section("Local storage: stars, deletion, tombstones")
st = M.LocalStore(DB_DIR / "ops.db")
st.upsert_contact(PEER, "pal", "02" + "ab" * 32)
OTHER = "Fec" + "Q" * 31
st.upsert_contact(OTHER, "other", "02" + "cd" * 32)
mine = st.add_out(PEER, "my message", M.now_iso(), "mine" * 8)
st.add_in(PEER, "their message", M.now_iso(), 1, "they" * 8)
st.add_in(OTHER, "someone else's", M.now_iso(), 2, "else" * 8)
theirs = st.message_by_cid(PEER, "they" * 8)["id"]

st.set_starred([mine, theirs], True)
check("starring marks both", len(st.starred(PEER)) == 2)
st.set_starred([mine], False)
check("unstarring is per message", [m["id"] for m in st.starred(PEER)] ==
      [theirs])

removed = st.delete_by_author(PEER, ["mine" * 8, "else" * 8])
check("delete-for-both cannot touch OUR messages or another peer's",
      removed == [] and st.message(mine) is not None and
      st.message_by_cid(OTHER, "else" * 8) is not None)
removed = st.delete_by_author(PEER, ["they" * 8, "late" * 8])
check("delete-for-both removes the author's own message",
      [r["client_msg_id"] for r in removed] == ["they" * 8] and
      st.message(theirs) is None)
outcome = st.add_in(PEER, "arrives after its deletion", M.now_iso(), 3,
                    "late" * 8)
check("a message deleted before it arrived never appears",
      outcome == M.LocalStore.DUPLICATE and
      st.message_by_cid(PEER, "late" * 8) is None)
outcome = st.add_in(PEER, "re-push of a deleted one", M.now_iso(), 1,
                    "they" * 8)
check("a re-pushed copy of a deleted message is refused",
      outcome == M.LocalStore.DUPLICATE)
st.delete_messages([mine])
check("delete-for-me removes and tombstones our own message",
      st.message(mine) is None)
st.add_caps(PEER, M.LocalStore.CAP_V9)
check("capabilities are recorded", st.get_contact(PEER)["caps"] == 1)
st.set_contact_setting(PEER, "notify", 0)
check("notifications can be muted per chat",
      st.get_contact(PEER)["notify"] == 0)
st.close()


# =============================================================================
section("Sync: extension blocks and control messages")
import queue                                                   # noqa: E402
import uuid                                                    # noqa: E402


class StubRelay:
    """v14: a message request turns the stranger's ticks off first."""

    def __init__(self):
        self.ticks = {}

    def set_ticks(self, peer, enabled):
        self.ticks[peer] = enabled
        return {}


class StubApp:
    """What SyncWorker._ingest_message reaches for, and nothing more."""

    def __init__(self, party, db):
        self.keys, self.crypto = party.keys, party.crypto
        self.store = M.LocalStore(db)
        self.ui_queue = queue.Queue()
        self.notified = []
        self.away_asked = []
        self.api = StubRelay()

    # v11: the worker announces from its own thread
    def notify_incoming(self, peer, body, kind, author=None,
                        mentioned=False):
        self.notified.append((peer, body, kind, author))

    def away_reply(self, peer, ext):       # 18.1: asked, not answered
        self.away_asked.append((peer, dict(ext or {})))

    def call_signal_arrived(self, sender, sig):
        self.ui_queue.put(("call_signal", sender, sig))

    def run_commands(self, worker=None):
        return 0

    def events(self):
        out = []
        while not self.ui_queue.empty():
            out.append(self.ui_queue.get_nowait())
        return out


sender, receiver = Party(M, "sender"), Party(M, "receiver")
app = StubApp(receiver, DB_DIR / "sync.db")
worker = M.SyncWorker(app)
_sid = [100]


def deliver(body, x=None, cid=None, frm=sender):
    cid = cid or uuid.uuid4().hex
    env, _ = frm.crypto.build_envelope(body, receiver.pub, x=x,
                                       recipient=receiver.address, cid=cid)
    _sid[0] += 1
    acked = worker._ingest_message({"id": _sid[0], "sender": frm.address,
                                    "client_msg_id": cid, "envelope": env})
    return acked, cid


def media_ref(**over):
    ref = {"kind": "image", "blob": "b" * 32, "sha": "0" * 64,
           "mime": "image/jpeg", "size": 1234, "key": M.b64e(os.urandom(32)),
           "w": 640, "h": 480, "thumb": M.b64e(b"tiny")}
    ref.update(over)
    return ref


# An established chat: the receiver knows the sender (v14: a stranger's
# first message would be a message request; test_flows covers those).
app.store.upsert_contact(sender.address, "sender", sender.pub)
app.events()
acked, first = deliver("first message")
row = app.store.message_by_cid(sender.address, first)
check("a plain message is stored and acked", acked and row and
      row["kind"] == "text")
check("...and is announced for a notification, from the sync thread",
      app.notified and app.notified[-1][:3] == (sender.address,
                                                "first message", "text"))
check("...and the app is asked once whether an away reply is due (18.1)",
      app.away_asked == [(sender.address, {})], app.away_asked)
again, _c = deliver("first message", cid=first)
check("...not again for a copy of it that comes twice",
      again and len(app.away_asked) == 1, app.away_asked)
app.events()

acked, reply = deliver("a reply", x={"k": "text", "reply": {
    "cid": first, "snip": "first message", "from": sender.address}})
row = app.store.message_by_cid(sender.address, reply)
ext = json.loads(row["ext"] or "{}")
check("a reply keeps its quote", ext.get("reply", {}).get("cid") == first)
check("a verified block records the peer as v9-capable",
      app.store.get_contact(sender.address)["caps"] &
      M.LocalStore.CAP_V9)

app.events()
acked, photo = deliver("Photo", x={"k": "media", "media": media_ref()})
row = app.store.message_by_cid(sender.address, photo)
check("a photo is stored as kind=image, pending download",
      row["kind"] == "image" and row["media_state"] == "pending")
check("...and queues its download",
      any(e[0] == "media_pending" for e in app.events()))

acked, bad = deliver("Photo", x={"k": "media", "media": media_ref(key="x")})
row = app.store.message_by_cid(sender.address, bad)
check("a malformed media reference degrades to its readable body",
      row["kind"] == "text" and row["body"] == "Photo" and
      "media" not in json.loads(row["ext"] or "{}"))

acked, nasty = deliver("hi", x={"k": "text", "reply": {
    "cid": "../../etc", "snip": "x" * 5000, "from": "not an address"}})
ext = json.loads(app.store.message_by_cid(sender.address, nasty)["ext"]
                 or "{}")
check("hostile reply fields are dropped or bounded",
      "cid" not in ext.get("reply", {}) and
      "from" not in ext.get("reply", {}) and
      len(ext["reply"].get("snip", "")) <= 300)

# --- delete for both -------------------------------------------------------
ours = app.store.add_out(sender.address, "ours", M.now_iso(), "ours" * 8)
before = app.store.message_count(sender.address)
asked = len(app.away_asked)
acked, ctl = deliver("Deleted a message", x={"k": "ctl", "ctl": {
    "op": "delete", "cids": [first, "ours" * 8]}})
check("delete-for-both control is acked and not stored",
      acked and app.store.message_by_cid(sender.address, ctl) is None)
check("it removes the author's message but not ours",
      app.store.message_by_cid(sender.address, first) is None and
      app.store.message(ours) is not None and
      app.store.message_count(sender.address) == before - 1)
check("a re-delivered copy of the same control is a no-op",
      not app.store.consume_control(sender.address, ctl))
check("...and a control is not something to answer while away",
      len(app.away_asked) == asked, (asked, app.away_asked))

app.events()
acked, _c = deliver("Please resend", x={"k": "ctl", "ctl": {
    "op": "resend", "cid": "ours" * 8}})
check("a resend request reaches the app",
      ("resend_request", sender.address, "ours" * 8, None) in app.events())

new_ref = media_ref(blob="c" * 32)
acked, _c = deliver("Photo", x={"k": "ctl", "ctl": {
    "op": "remedia", "cid": photo, "media": new_ref}})
row = app.store.message_by_cid(sender.address, photo)
check("a re-upload replaces the media reference in place",
      json.loads(row["ext"])["media"]["blob"] == "c" * 32 and
      row["media_state"] == "pending")

stranger = Party(M, "stranger")
acked, _c = deliver("Deleted a message", frm=stranger, x={"k": "ctl", "ctl": {
    "op": "delete", "cids": [reply]}})
check("nobody can delete another person's message",
      app.store.message_by_cid(sender.address, reply) is not None)

if OLD is not None:
    old_sender = Party(OLD, "old-sender")
    env, _ = old_sender.crypto.build_envelope("from v8", receiver.pub)
    _sid[0] += 1
    acked = worker._ingest_message({"id": _sid[0],
                                    "sender": old_sender.address,
                                    "client_msg_id": "v8" * 16,
                                    "envelope": env})
    row = app.store.message_by_cid(old_sender.address, "v8" * 16)
    check("a message from the previous release is stored as text",
          acked and row["body"] == "from v8" and row["kind"] == "text")
    check("...and its sender is not marked v9-capable",
          app.store.get_contact(old_sender.address)["caps"] == 0)

    # The other way: what every 9.1.2 message now carries (its app's
    # features, the version of its author's picture) and the picture
    # controls themselves, through the previous release's own sync code.
    class OldStubApp(StubApp):
        def __init__(self, party, db):
            self.keys, self.crypto = party.keys, party.crypto
            self.store = OLD.LocalStore(db)
            self.ui_queue = queue.Queue()
            self.notified = []
            self.away_asked = []
            self.api = StubRelay()        # a 14.0 client's requests use it

    old_receiver = Party(OLD, "old-receiver")
    old_app = OldStubApp(old_receiver, DB_DIR / "old_sync.db")
    old_worker = OLD.SyncWorker(old_app)

    def deliver_old(body, x):
        cid = uuid.uuid4().hex
        env, _ = sender.crypto.build_envelope(
            body, old_receiver.pub, x=x, recipient=old_receiver.address,
            cid=cid)
        _sid[0] += 1
        acked = old_worker._ingest_message({"id": _sid[0],
                                            "sender": sender.address,
                                            "client_msg_id": cid,
                                            "envelope": env})
        return acked, cid

    acked, c912 = deliver_old("hello from 9.1.2", {
        "k": "text", "ft": M.FEATURES, "pv": 1760000000})
    row = old_app.store.message_by_cid(sender.address, c912)
    check("the previous release takes a 9.1.2 message as it always did",
          acked and row and row["body"] == "hello from 9.1.2")
    before = old_app.store.message_count(sender.address)
    for ctl in ({"op": "pic", "v": 1760000001, "d": M.b64e(b"\xff\xd8" * 9)},
                {"op": "picreq"}):
        acked, _c = deliver_old("Updated their profile photo", {
            "k": "ctl", "ctl": ctl, "ft": M.FEATURES})
        check(f"...and acknowledges a '{ctl['op']}' it cannot use, showing "
              "nothing", acked and
              old_app.store.message_count(sender.address) == before)
    acked, _c = deliver_old("Reacted", {
        "k": "ctl", "ft": M.FEATURES, "ctl": {
            "op": "react", "cid": c912, "e": "\u2764\ufe0f",
            "es": ["\U0001F44D", "\u2764\ufe0f"]}})
    check("...and takes several reactions (v12) as the latest of them",
          acked and old_app.store.reaction_of(sender.address, c912,
                                              sender.address) ==
          "\u2764\ufe0f")
    # 18.1: a video message, and a reply to one, at the release before.
    acked, cvid = deliver_old(M.video_label(42000), {
        "k": "media", "ft": M.FEATURES, "media": {
            "kind": "video", "mime": "video/mp4", "size": 5_000_000,
            "blob": "b" * 32, "sha": "0" * 64, "key": M.b64e(bytes(32)),
            "dur": 42000, "w": 480, "h": 640}})
    row = old_app.store.message_by_cid(sender.address, cvid)
    check("the release before shows a video message as its words: a "
          "text message, with nothing to download",
          acked and row and row["kind"] == "text" and
          row["body"] == "Video message (0:42)" and
          not row.get("media_state"), row and dict(row))
    acked, cre = deliver_old("nice one", {"k": "text", "ft": M.FEATURES,
                                          "reply": {"cid": cvid,
                                                    "kind": "video"}})
    row = old_app.store.message_by_cid(sender.address, cre)
    check("...and takes a reply to one as a reply to that message",
          acked and row and row["body"] == "nice one" and
          OLD.message_ext(row).get("reply", {}).get("cid") == cvid,
          row and row.get("ext"))
    old_app.store.close()
app.store.close()


# =============================================================================
section("v9.1 storage: groups, delivery per member, reactions, listing")
st = M.LocalStore(DB_DIR / "v91.db")
ME = "Fec" + "M" * 31
A, B = "Fec" + "A" * 31, "Fec" + "B" * 31
GID = "ab" * 16
GPEER = M.group_peer(GID)
for addr, name in ((A, "ann"), (B, "ben")):
    st.upsert_contact(addr, name, "02" + "ab" * 32)
st.upsert_contact("Fec" + "C" * 31, "cat", "02" + "cd" * 32, listed=False)
check("someone met only in a group is known but not listed",
      st.get_contact("Fec" + "C" * 31)["listed"] == 0 and
      all(c["username"] != "cat" for c in st.contacts()))
st.upsert_contact("Fec" + "C" * 31, "cat", "02" + "cd" * 32)
check("...and a later direct contact lists them for good",
      st.get_contact("Fec" + "C" * 31)["listed"] == 1)
st.save_group({"id": GID, "name": "Readers", "v": 1, "admin": ME,
               "members": [{"a": ME, "k": "02" + "11" * 32},
                           {"a": A, "k": "02" + "ab" * 32},
                           {"a": B, "k": "02" + "ab" * 32}]})
g = st.group(GID)
check("a group round-trips with its members and version",
      g["name"] == "Readers" and len(g["members"]) == 3 and g["version"] == 1
      and g["state"] == "active")
local = st.add_out(GPEER, "hello group", M.now_iso(), "gmsg" * 8,
                   recipients=[A, B])
check("a group message starts at 0 for every member",
      [r["status"] for r in st.group_sends(local)] == [0, 0])
st.group_send_sent(local, A, 501)
check("one member's copy on the relay is not yet 1/3",
      st.message(local)["status"] == 0)
st.group_send_sent(local, B, 502)
check("every copy on the relay: 1/3", st.message(local)["status"] == 1)
peer = st.apply_receipt(501, "delivered")
check("a receipt for one copy moves only that member",
      peer == GPEER and st.message(local)["status"] == 1)
st.apply_receipt(502, "delivered")
st.apply_receipt(501, "read")
check("delivered to all, read by one: 2/3", st.message(local)["status"] == 2)
st.apply_receipt(502, "read")
check("read by everyone: 3/3", st.message(local)["status"] == 3)
# The relay can report a delivery before this phone has noted the relay's
# answer to its own send (the recipient acknowledged it within
# milliseconds): the receipt waits for the id instead of being dropped.
early = st.add_out(A, "quick reader", M.now_iso(), "fast" * 8)
st.apply_receipt(701, "delivered")
st.mark_sent(early, 701)
check("a receipt that beats the send's own answer still ticks 2/3",
      st.message(early)["status"] == 2)
early_g = st.add_out(GPEER, "quick group", M.now_iso(), "fstg" * 8,
                     recipients=[A, B])
st.apply_receipt(702, "read")
st.group_send_sent(early_g, A, 702)
st.group_send_sent(early_g, B, 703)
check("...and one for a member's copy of a group message",
      {r["member"]: r["status"] for r in st.group_sends(early_g)}
      == {A: 3, B: 1})
hold, M.LocalStore.EARLY_RECEIPT_HOLD = M.LocalStore.EARLY_RECEIPT_HOLD, -1
late = st.add_out(A, "an id seen long ago", M.now_iso(), "late" * 8)
st.apply_receipt(704, "delivered")
st.mark_sent(late, 704)
M.LocalStore.EARLY_RECEIPT_HOLD = hold
check("...but not one held past its minute (a stale id's)",
      st.message(late)["status"] == 1)
st.add_in(GPEER, "from ann", M.now_iso(), 601, "ann1" * 8, author=A)
st.add_in(GPEER, "from ben", M.now_iso(), 602, "ben1" * 8, author=B)
check("the same client id from two authors are two messages",
      st.add_in(GPEER, "dup?", M.now_iso(), 603, "ann1" * 8, author=B)
      == M.LocalStore.STORED)
removed = st.delete_by_author(A, ["ben1" * 8, "ann1" * 8], GPEER)
check("in a group, delete-for-both removes only the author's own message",
      [r["author"] for r in removed] == [A] and
      st.message_by_cid(GPEER, "ben1" * 8) is not None)
st.add_out(ME, "a note to myself", M.now_iso(), "self" * 8)
convs = {c["peer"]: c for c in st.conversations(me=ME)}
check("conversations: a group, notes to self and direct chats are told apart",
      convs[GPEER]["kind"] == "group" and convs[GPEER]["group_name"] ==
      "Readers" and convs[ME]["kind"] == "self" and convs[A]["kind"] == "dm")
check("...with the newest message's author and direction",
      convs[GPEER]["last_author"] == B and
      convs[GPEER]["last_direction"] == "in")
st.save_group({"id": "cd" * 16, "name": "Empty", "v": 1, "admin": ME,
               "members": [{"a": ME, "k": "02" + "11" * 32},
                           {"a": A, "k": "02" + "ab" * 32}]})
check("a group with no messages yet is still listed",
      M.group_peer("cd" * 16) in {c["peer"] for c in st.conversations(ME)})


def counted_by_hand():
    """Each chat's unread count and newest message, from every message."""
    return {row[0]: (row[1], row[2]) for row in st._db.execute(
        "SELECT peer, SUM(direction = 'in' AND read = 0), MAX(id) "
        "FROM messages GROUP BY peer")}


def listed():
    return {c["peer"]: (c["unread"], c["last_id"])
            for c in st.conversations(ME) if c["last_id"]}


for i in range(3):
    st.add_in(A, f"unread {i}", M.now_iso(), 610 + i, f"unr{i}" * 8)
check("v14: the chat list's unread counts and newest messages agree with "
      "counting every message", listed() == counted_by_hand() and
      listed()[A][0] >= 3, str(listed()))
st.mark_read_local([m["id"] for m in st.messages_for(A)])
check("...and still agree once a chat is read",
      listed() == counted_by_hand() and listed()[A][0] == 0)
check("reactions: set, replace and take back",
      st.set_reaction(GPEER, "ann1" * 8, B, "\U0001F44D") and
      st.set_reaction(GPEER, "ann1" * 8, B, "\u2764\ufe0f") and
      not st.set_reaction(GPEER, "ann1" * 8, B, "\u2764\ufe0f") and
      st.reaction_of(GPEER, "ann1" * 8, B) == "\u2764\ufe0f")
st.set_reaction(GPEER, "ann1" * 8, ME, "\U0001F44D")
got = st.reactions_for(GPEER, ["ann1" * 8, "nope" * 8])
check("one reaction per person per message",
      sorted(e for e, _r, _a in got["ann1" * 8]) ==
      sorted(["\u2764\ufe0f", "\U0001F44D"]) and "nope" * 8 not in got)
st.set_reaction(GPEER, "ann1" * 8, ME, "")
check("taking a reaction back removes it",
      st.reaction_of(GPEER, "ann1" * 8, ME) == "")
# v12: several reactions per person.
C1 = "ann2" * 8
TH, HE, LA, WO = "\U0001F44D", "\u2764\ufe0f", "\U0001F602", "\U0001F62E"
st.add_in(GPEER, "react to me", M.now_iso(), None, C1, author=B)
check("several reactions per person (v12), oldest first",
      st.set_reactions(GPEER, C1, B, [TH, HE]) and
      st.reactions_of(GPEER, C1, B) == [TH, HE] and
      not st.set_reactions(GPEER, C1, B, [TH, HE]))
check("...the latest where a release before 12 reads its one",
      st.reaction_of(GPEER, C1, B) == HE and st._db.execute(
          "SELECT COUNT(*) FROM reactions WHERE cid = ?", (C1,)
      ).fetchone()[0] == 1)
st.set_reactions(GPEER, C1, B, [TH, HE, LA, WO])
check("...at most three, the latest kept",
      st.reactions_of(GPEER, C1, B) == [HE, LA, WO])
st.set_reactions(GPEER, C1, B, [HE, WO])
check("...one taken back leaves the others, in their order",
      st.reactions_of(GPEER, C1, B) == [HE, WO] and
      st.reaction_of(GPEER, C1, B) == WO)
st.set_reaction(GPEER, C1, ME, LA)
got = [(e, r) for e, r, _a in st.reactions_for(GPEER, [C1])[C1]]
check("...everyone's together, in the order they were given",
      got == [(HE, B), (WO, B), (LA, ME)], str(got))
check("one reaction from a release before 12 stands for all of theirs",
      st.set_reaction(GPEER, C1, B, TH) and
      st.reactions_of(GPEER, C1, B) == [TH])
st.set_reactions(GPEER, C1, B, [HE, WO])
st.delete_messages([st.message_by_cid(GPEER, C1)["id"]])
check("deleting a message takes all its reactions with it",
      not st.reactions_for(GPEER, [C1]) and not st._db.execute(
          "SELECT COUNT(*) FROM reactions_more WHERE cid = ?", (C1,)
      ).fetchone()[0])
event = st.add_event(GPEER, "Ann added Ben")
check("group events are stored as readable, already-read rows",
      st.message(event)["kind"] == "event" and st.message(event)["read"] == 1)
st.close()

if OLD is not None:
    try:
        old = OLD.LocalStore(DB_DIR / "v91.db")
        convs = old.conversations()
        rows = old.messages_for(GPEER)
        old.close()
        check("the previous release still opens a 9.1 database (groups, "
              "reactions and all)", convs and rows and
              all(isinstance(r["body"], str) and r["body"] for r in rows))
    except Exception as exc:                      # noqa: BLE001
        check("the previous release still opens a 9.1 database", False,
              f"{type(exc).__name__}: {exc}")

check("several reactions are validated whole (v12)",
      M.sanitize_reactions(["\U0001F44D", "\u2764\ufe0f"]) ==
      ["\U0001F44D", "\u2764\ufe0f"] and M.sanitize_reactions([]) == [] and
      M.sanitize_reactions(["\U0001F44D"] * 2) is None and
      M.sanitize_reactions(["\U0001F600", "\U0001F601", "\U0001F602",
                            "\U0001F603"]) is None and
      M.sanitize_reactions(["\U0001F44D", "ok"]) is None and
      M.sanitize_reactions(["\U0001F44D", ""]) is None and
      M.sanitize_reactions("\U0001F44D") is None)
check("emoji reactions are validated",
      M.sanitize_emoji("\U0001F44D\U0001F3FD") and
      M.sanitize_emoji("") == "" and M.sanitize_emoji("ok") is None and
      M.sanitize_emoji("\U0001F44D x") is None and
      M.sanitize_emoji("\U0001F600" * 17) is None)


# =============================================================================
section("v9.1 sync: group membership rules and reactions")
admin, carol, dan = Party(M, "admin"), Party(M, "carol"), Party(M, "dan")
gapp = StubApp(receiver, DB_DIR / "groups.db")
gworker = M.SyncWorker(gapp)


def gdeliver(body, frm, x=None, cid=None):
    cid = cid or uuid.uuid4().hex
    env, _ = frm.crypto.build_envelope(body, receiver.pub, x=x,
                                       recipient=receiver.address, cid=cid)
    _sid[0] += 1
    acked = gworker._ingest_message({"id": _sid[0], "sender": frm.address,
                                     "client_msg_id": cid, "envelope": env})
    return acked, cid


def member(p):
    return {"a": p.address, "k": p.pub, "u": p.keys.username or "x_" +
            p.address[3:8]}


GID2 = "ef" * 16
GP2 = M.group_peer(GID2)


def state(v, members, name="Readers", adm=None):
    return {"id": GID2, "name": name, "v": v,
            "admin": (adm or admin).address,
            "members": [member(p) for p in members]}


acked, early = gdeliver("too early", carol, x={"k": "text",
                                               "grp": {"id": GID2, "v": 1}})
check("a message for an unknown group is kept, in a placeholder",
      acked and gapp.store.group(GID2)["state"] == "pending" and
      gapp.store.message_by_cid(GP2, early)["author"] == carol.address)
gdeliver("join", carol, x={"k": "ctl", "ctl": {
    "op": "group", "g": state(1, [admin, receiver, carol])}})
check("an invitation from someone who is NOT the named admin is refused",
      gapp.store.group(GID2)["state"] == "pending")
forged = state(1, [admin, receiver, carol])
forged["members"][2]["k"] = dan.pub            # dan's key under carol's name
gdeliver("join", admin, x={"k": "ctl", "ctl": {"op": "group", "g": forged}})
check("a state putting someone's key under another's address is refused",
      gapp.store.group(GID2)["state"] == "pending")
gdeliver("join", admin, x={"k": "ctl", "ctl": {
    "op": "group", "g": state(1, [admin, receiver, carol])}})
g = gapp.store.group(GID2)
check("the admin's invitation replaces the placeholder",
      g["state"] == "active" and g["name"] == "Readers" and
      g["admin"] == admin.address and len(g["members"]) == 3)
check("...and says so in the conversation",
      any(m["kind"] == "event" and "added you" in m["body"]
          for m in gapp.store.messages_for(GP2)))
check("members met in the group are known but not listed",
      gapp.store.get_contact(carol.address) is not None and
      all(c["address"] != carol.address for c in gapp.store.contacts()))
acked, gm = gdeliver("hello all", carol, x={"k": "text",
                                            "grp": {"id": GID2, "v": 1}})
row = gapp.store.message_by_cid(GP2, gm)
check("a member's group message lands in the group, with its author",
      acked and row and row["author"] == carol.address)
acked, bad = gdeliver("let me in", dan, x={"k": "text",
                                           "grp": {"id": GID2, "v": 1}})
check("a non-member's group message is acked and dropped",
      acked and gapp.store.message_by_cid(GP2, bad) is None)
gdeliver("x", carol, x={"k": "ctl", "ctl": {
    "op": "group", "g": state(5, [admin, receiver, carol, dan],
                              name="Hijacked")}})
check("a member who is not the admin cannot change the group",
      gapp.store.group(GID2)["name"] == "Readers")
gdeliver("x", admin, x={"k": "ctl", "ctl": {
    "op": "group", "g": state(2, [admin, receiver, carol, dan],
                              name="Readers' Circle")}})
g = gapp.store.group(GID2)
check("the admin renames and adds a member (version 2)",
      g["name"] == "Readers' Circle" and g["version"] == 2 and
      len(g["members"]) == 4)
gdeliver("x", admin, x={"k": "ctl", "ctl": {
    "op": "group", "g": state(2, [admin, receiver], name="Replayed")}})
check("a replayed or stale version is ignored",
      gapp.store.group(GID2)["name"] == "Readers' Circle")
acked, _c = gdeliver("Reacted", carol, x={"k": "ctl", "ctl": {
    "op": "react", "cid": gm, "e": "\U0001F44D", "grp": GID2}})
check("a member's reaction is recorded on the group message",
      gapp.store.reaction_of(GP2, gm, carol.address) == "\U0001F44D")
check("...and announced to the screen (to animate it)",
      any(e[0] == "reaction" for e in gapp.events()))
gdeliver("Reacted", carol, x={"k": "ctl", "ctl": {
    "op": "react", "cid": gm, "e": "<b>hi</b>", "grp": GID2}})
check("text dressed up as a reaction is refused",
      gapp.store.reaction_of(GP2, gm, carol.address) == "\U0001F44D")
gdeliver("Removed", carol, x={"k": "ctl", "ctl": {
    "op": "react", "cid": gm, "e": "", "grp": GID2}})
check("a reaction can be taken back",
      gapp.store.reaction_of(GP2, gm, carol.address) == "")
gdeliver("Reacted", carol, x={"k": "ctl", "ctl": {
    "op": "react", "cid": gm, "e": "\u2764\ufe0f",
    "es": ["\U0001F44D", "\u2764\ufe0f"], "grp": GID2}})
check("a 12 app's several reactions are all recorded (v12)",
      gapp.store.reactions_of(GP2, gm, carol.address) ==
      ["\U0001F44D", "\u2764\ufe0f"])
gdeliver("Reacted", carol, x={"k": "ctl", "ctl": {
    "op": "react", "cid": gm, "e": "\U0001F602",
    "es": ["\U0001F602", "\U0001F602"], "grp": GID2}})
check("...a list that is not a valid one is refused whole, whatever its 'e'",
      gapp.store.reactions_of(GP2, gm, carol.address) ==
      ["\U0001F44D", "\u2764\ufe0f"])
gdeliver("Reacted", carol, x={"k": "ctl", "ctl": {
    "op": "react", "cid": gm, "e": "\U0001F602", "grp": GID2}})
check("...and one from an older app replaces them all",
      gapp.store.reactions_of(GP2, gm, carol.address) == ["\U0001F602"])
gdeliver("Removed", carol, x={"k": "ctl", "ctl": {
    "op": "react", "cid": gm, "e": "", "es": [], "grp": GID2}})
check("...an empty list takes every one back",
      gapp.store.reactions_of(GP2, gm, carol.address) == [])
gdeliver("Deleted", dan, x={"k": "ctl", "ctl": {
    "op": "delete", "cids": [gm], "grp": GID2}})
check("another member cannot delete someone's group message",
      gapp.store.message_by_cid(GP2, gm) is not None)
gdeliver("Deleted", carol, x={"k": "ctl", "ctl": {
    "op": "delete", "cids": [gm], "grp": GID2}})
check("its author can", gapp.store.message_by_cid(GP2, gm) is None)
acked, dm = gdeliver("a direct one", carol)
gdeliver("Reacted", carol, x={"k": "ctl", "ctl": {
    "op": "react", "cid": dm, "e": "\U0001F602"}})
check("reactions work in direct chats too",
      gapp.store.reaction_of(carol.address, dm, carol.address) ==
      "\U0001F602")
gapp.events()
gdeliver("left", admin, x={"k": "ctl", "ctl": {"op": "leave",
                                               "grp": GID2}})
g = gapp.store.group(GID2)
check("when the admin leaves, the first remaining member takes over",
      admin.address not in {m["a"] for m in g["members"]} and
      g["admin"] == receiver.address)
check("...and, being that member, this device publishes the new state",
      ("group_publish", GID2) in gapp.events())
gdeliver("left", dan, x={"k": "ctl", "ctl": {"op": "leave", "grp": GID2}})
check("any member can leave", dan.address not in
      {m["a"] for m in gapp.store.group(GID2)["members"]})
GID3 = "12" * 16
gdeliver("join", admin, x={"k": "ctl", "ctl": {"op": "group", "g": {
    "id": GID3, "name": "Short-lived", "v": 1, "admin": admin.address,
    "members": [member(admin), member(receiver), member(carol)]}}})
gdeliver("out", admin, x={"k": "ctl", "ctl": {"op": "group", "g": {
    "id": GID3, "name": "Short-lived", "v": 2, "admin": admin.address,
    "members": [member(admin), member(carol)]}}})
check("removed by the admin: the group is kept read-only",
      gapp.store.group(GID3)["state"] == "removed")
acked, after = gdeliver("still?", carol, x={"k": "text",
                                            "grp": {"id": GID3, "v": 2}})
check("...and nothing more arrives in it",
      acked and gapp.store.message_by_cid(M.group_peer(GID3), after) is None)
gapp.store.close()


# =============================================================================
section("18.0 sync: a group's admins, with a member on the previous release")
# Phones: the owner's, an 18.0 member's (receiver) and, given the previous
# release, a member on it (elder): an app before 18.0 follows the owner
# alone. The co-admins, deputy and other, only send.
owner, deputy, other = (Party(M, n) for n in ("owner", "deputy", "other"))
elder = Party(OLD or M, "elder")
GID4 = "34" * 16
phones = {"owner": StubApp(owner, DB_DIR / "admins_owner.db"),
          "new": StubApp(receiver, DB_DIR / "admins_new.db")}
if OLD is not None:
    phones["old"] = OldStubApp(elder, DB_DIR / "admins_old.db")
workers = {name: (OLD if name == "old" else M).SyncWorker(phone)
           for name, phone in phones.items()}
AT = 1_780_000_000.0
ORDER = [owner, receiver, deputy, other, elder]   # the owner's heir: deputy


def held(name):
    return phones[name].store.group(GID4)


def send(frm, ctl, to=("new", "old")):
    """`ctl` from `frm`, to each phone named that there is."""
    for name in to:
        if name not in phones:
            continue
        party = {"owner": owner, "new": receiver, "old": elder}[name]
        cid = uuid.uuid4().hex
        env, _ = frm.crypto.build_envelope(
            "Group updated", party.pub, x={"k": "ctl", "ctl": ctl,
                                           "ft": M.FEATURES},
            recipient=party.address, cid=cid)
        _sid[0] += 1
        workers[name]._ingest_message({"id": _sid[0], "sender": frm.address,
                                       "client_msg_id": cid,
                                       "envelope": env})


def gstate(v, name, by=owner, adm=owner, base=None, event=None):
    """The state `by` sends at version `v`: `base`'s admins and log (a
    state), and `event` (e, t) of theirs logged."""
    out = {"id": GID4, "name": name, "v": v, "admin": adm.address,
           "members": [member(p) for p in ORDER]}
    ad, lg = dict((base or {}).get("ad") or {}), list(
        (base or {}).get("lg") or [])
    if event:
        lg.append({"v": v, "a": by.address, "e": event[0], "t": event[1],
                   "at": AT + v})
    out.update({k: val for k, val in (("ad", ad), ("lg", lg)) if val})
    return out


s1 = gstate(1, "Field notes")
phones["owner"].store.save_group(dict(s1, by=owner.address), "active")
send(owner, {"op": "group", "g": s1})
check("every phone takes the owner's invitation",
      all(held(n)["state"] == "active" and held(n)["admin"] == owner.address
          for n in phones))
rights = {"r": ["info", "add", "remove"], "by": owner.address}
s2 = gstate(2, "Field notes", event=("admin", deputy.address))
s2["ad"] = {deputy.address: rights, other.address: rights}
s2["lg"].append(dict(s2["lg"][0], t=other.address))
phones["owner"].store.save_group(dict(s2, by=owner.address), "active")
send(owner, {"op": "group", "g": s2})
check("...and her next, though it names admins and a log the previous "
      "release does not know",
      held("new")["admins"] == s2["ad"] and len(held("new")["log"]) == 2 and
      all(held(n)["version"] == 2 for n in phones))

s3 = gstate(3, "Field notes, 2026", deputy, base=s2,
            event=("name", "Field notes, 2026"))
phones["owner"].events()
send(deputy, {"op": "group", "g": s3}, to=("owner", "new", "old"))
check("a co-admin's change: 18.0 apps take it from him",
      all(held(n)["name"] == "Field notes, 2026" and
          held(n)["by"] == deputy.address for n in ("owner", "new")))
if OLD is not None and hasattr(OLD, "admin_change_allowed"):
    # From 18.0.1 on, the previous release is an 18.0 one (CI's baseline).
    check("...as the previous release, an 18.0 one, does",
          held("old")["name"] == "Field notes, 2026" and
          held("old")["version"] == 3)
elif OLD is not None:
    check("...an app before 18.0 only from the owner",
          held("old")["name"] == "Field notes" and held("old")["version"] == 2)
check("...whose app sends it on, as it is, for those",
      ("group_forward", GID4, []) in phones["owner"].events())
copy = {k: v for k, v in s3.items() if k != "lg"}
send(owner, {"op": "group", "g": copy, "fw": 1})
if OLD is not None:
    check("...which brings the older app up to date",
          held("old")["name"] == "Field notes, 2026" and
          held("old")["version"] == 3)
check("...while an 18.0 app leaves the copy: it has his own",
      held("new")["by"] == deputy.address and len(held("new")["log"]) == 3)

# Two co-admins change it at the same moment: the lower address wins (the
# owner's would, over either). The owner's app takes the loser's first.
first, second = sorted((deputy, other), key=lambda p: p.address)
won = gstate(4, "Picked by the first", first, base=s3,
             event=("name", "Picked by the first"))
lost = gstate(4, "Picked by the second", second, base=s3,
              event=("name", "Picked by the second"))
send(second, {"op": "group", "g": lost}, to=("owner",))
check("two co-admins at once: the owner's app takes the one it gets first, "
      "and sends it on",
      held("owner")["name"] == "Picked by the second" and
      ("group_forward", GID4, []) in phones["owner"].events())
# Its copy goes to the 18.0 phone too, as if her app did not know it yet.
send(owner, {"op": "group", "g": {k: v for k, v in lost.items()
                                   if k != "lg"}, "fw": 1})
send(first, {"op": "group", "g": won}, to=("owner", "new"))
send(second, {"op": "group", "g": lost}, to=("new",))
check("...then the other, which wins on every 18.0 phone",
      all(held(n)["name"] == "Picked by the first" and held(n)["version"] == 4
          for n in ("owner", "new")))
check("...and the log keeps the winner's entry, not the loser's",
      [(e["a"], e["v"]) for e in held("owner")["log"]][-2:] ==
      [(deputy.address, 3), (first.address, 4)])
check("...which, as an older app may hold the other, the owner's app "
      "publishes at the next version",
      ("group_publish", GID4, []) in phones["owner"].events())
s5 = dict(M.group_row_state(held("owner")), v=5)
phones["owner"].store.save_group(dict(s5, by=owner.address), "active")
send(owner, {"op": "group", "g": s5})
check("...so every phone holds the same group",
      all(held(n)["name"] == "Picked by the first" and held(n)["version"] == 5
          for n in phones))

grab = gstate(6, "Mine now", other, adm=other, base=s5)
grab["ad"] = {deputy.address: rights}
send(other, {"op": "group", "g": grab})
check("a co-admin cannot make himself the owner",
      held("new")["admin"] == owner.address and held("new")["version"] == 5)

# The owner leaves: her app sends the state that makes her first other
# admin (deputy, not the first member) the owner, then her leave.
handover = dict(M.group_row_state(held("owner")), v=6,
                admin=deputy.address)
handover["ad"] = {a: e for a, e in handover["ad"].items()
                  if a != deputy.address}
send(owner, {"op": "group", "g": handover})
send(owner, {"op": "leave", "grp": GID4})
check("the owner leaves: every phone, older ones too, has her first other "
      "admin as its owner",
      all(held(n)["admin"] == deputy.address and owner.address not in
          {m["a"] for m in held(n)["members"]}
          for n in ("new", "old") if n in phones))
s7 = gstate(7, "Field notes, kept", deputy, adm=deputy, base=handover)
s7["members"] = [m for m in s7["members"] if m["a"] != owner.address]
send(deputy, {"op": "group", "g": s7})
check("...and follows him from then on",
      all(held(n)["name"] == "Field notes, kept" and held(n)["version"] == 7
          for n in ("new", "old") if n in phones))
for phone in phones.values():
    phone.store.close()


# =============================================================================
section("v15: private keys in Ferrite Core's own format")
KEY_1 = (1).to_bytes(32, "big") + b"\x01"          # key 1, compressed
check("Copy WIF gives Ferrite Core's format (version 163), which its "
      "importprivkey takes", M.b58check_decode(M.priv_to_wif(1)) ==
      (163, KEY_1))
check("both import: Ferrite Core's, and the Bitcoin-style ones (version 128) "
      "earlier releases exported",
      M.parse_private_key(M.priv_to_wif(1)) == 1 and
      M.parse_private_key(M.b58check_encode(128, KEY_1)) == 1)
ok, why = expect_reject(
    lambda: M.parse_private_key(M.b58check_encode(239, KEY_1)),
    "it is a testnet key")
check("a testnet key is refused", ok, why)

# =============================================================================
section("v15: post-quantum encryption (ML-KEM-768, FIPS 203)")
import hashlib                                                 # noqa: E402


def sha3(raw):
    return hashlib.sha3_256(raw).hexdigest()


vectors = json.load(open(os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "mlkem768_vectors.json")))
check("NIST's key generation vectors",
      all((sha3(ek), sha3(dk)) == (t["ek_sha3_256"], t["dk_sha3_256"])
          for t in vectors["keyGen"]
          for ek, dk in [M.mlkem_keygen(bytes.fromhex(t["d"] + t["z"]))]))
check("NIST's encapsulation vector",
      all((k.hex(), sha3(c)) == (t["k"], t["c_sha3_256"])
          for t in vectors["encapsulation"]
          for k, c in [M.mlkem_encaps(bytes.fromhex(t["ek"]),
                                      bytes.fromhex(t["m"]))]))
check("NIST's decapsulation vectors, a tampered ciphertext among them",
      all(M.mlkem_decaps(bytes.fromhex(t["dk"]), bytes.fromhex(t["c"])).hex()
          == t["k"] for t in vectors["decapsulation"]) and
      {t["reason"] for t in vectors["decapsulation"]} ==
      {"valid decapsulation", "modified ciphertext"})
try:
    from cryptography.hazmat.primitives.asymmetric import mlkem
    mlkem.MLKEM768PrivateKey.generate()
except Exception:                     # noqa: BLE001 — older, or built without
    mlkem = None
if mlkem is not None:
    agree = True
    for _ in range(3):
        seed = os.urandom(64)
        ek, dk = M.mlkem_keygen(seed)
        theirs = mlkem.MLKEM768PrivateKey.from_seed_bytes(seed)
        secret, ct = theirs.public_key().encapsulate()
        mine, ct2 = M.mlkem_encaps(ek)
        agree &= (theirs.public_key().public_bytes_raw() == ek and
                  M.mlkem_decaps(dk, ct) == secret and
                  theirs.decapsulate(ct2) == mine)
    check("the same keys as the cryptography library's ML-KEM, and "
          "messages open both ways", agree)
ek = alice.keys.pq_public
bad = bytes([0xFF, 0x0F]) + ek[2:]        # a first coefficient of 4095 ≥ q
ok, why = expect_reject(lambda: M.mlkem_encaps(bad), "a coefficient ≥ q")
check("a key that is not a valid ML-KEM-768 key is refused",
      ok and not M.mlkem_ek_ok(bad) and not M.mlkem_ek_ok(ek[:-1]) and
      M.mlkem_ek_ok(ek), why)

pq_env, _ = alice.crypto.build_envelope(
    "after the harvest", bob.pub, x={"k": "text"}, recipient=bob.address,
    cid="q" * 32, pq=(bob.keys.pq_public, bob.keys.pq_kid))
raw = M.b64d(pq_env["ciphertext"])
got = bob.crypto.open_envelope(pq_env, alice.address, cid="q" * 32)
check("a hybrid envelope opens, and says which key it was sealed to",
      got["body"] == "after the harvest" and got.get("_pq") ==
      bob.keys.pq_kid and raw[:4] == M.PQ_MAGIC)
check("...still one envelope of three fields, the size relays accept",
      set(pq_env) == {"ephemeral_public_key", "nonce", "ciphertext"} and
      len(raw) < 96 * 1024)
classic, _ = alice.crypto.build_envelope("as before", bob.pub)
check("a classic one says nothing of the kind",
      "_pq" not in bob.crypto.open_envelope(classic, alice.address))


def altered(at, envelope=pq_env):
    flipped = bytearray(M.b64d(envelope["ciphertext"]))
    flipped[at] ^= 1
    return dict(envelope, ciphertext=M.b64e(bytes(flipped)))


for name, at in (("its ML-KEM ciphertext", 12 + 500),
                 ("its sealed message", len(raw) - 1)):
    ok, why = expect_reject(
        lambda: bob.crypto.open_envelope(altered(at), alice.address,
                                         cid="q" * 32),
        f"{name} was altered")
    check(f"a hybrid envelope with {name} altered is refused", ok, why)
ok, why = expect_reject(
    lambda: bob.crypto.open_envelope(altered(5), alice.address, cid="q" * 32),
    "its key id was altered")
check("...and one naming a key this device does not hold says so",
      ok and "post-quantum key" in why, why)
ok, why = expect_reject(
    lambda: carol.crypto.open_envelope(pq_env, alice.address, cid="q" * 32),
    "it was sealed to Bob")
check("nobody else opens it", ok, why)
forged = dict(bob.crypto.open_envelope(classic, alice.address),
              _pq=bob.keys.pq_kid)
check("a sender cannot claim the post-quantum mark",
      "_pq" not in bob.crypto.open_envelope(reseal(forged, bob.pub),
                                            alice.address))

seed = alice.keys.find(alice.address).get("pq_seed_hex", "")
saved = M.DATA_DIR, M.IDENTITIES_PATH
M.DATA_DIR = Path(tempfile.mkdtemp(prefix="fext-device-"))
M.IDENTITIES_PATH = M.DATA_DIR / "identities.json"
fresh = M.IdentityManager()
fresh.activate(fresh.add_identity(
    int(alice.keys.active["private_key_hex"], 16), "again"))
check("the post-quantum key is random, not made from the identity key: "
      "the same identity set up again has another",
      len(seed) == 128 and fresh.pq_kid != alice.keys.pq_kid)
M.DATA_DIR = Path(tempfile.mkdtemp(prefix="fext-device-"))
M.IDENTITIES_PATH = M.DATA_DIR / "identities.json"
restored = M.IdentityManager()
restored.activate(restored.restore_identity(dict(alice.keys.active)))
check("...while a backup brings the key back with it",
      restored.pq_kid == alice.keys.pq_kid and
      M.IdentityManager().pq_public == alice.keys.pq_public)
M.DATA_DIR, M.IDENTITIES_PATH = saved

if OLD is not None:
    old_dan = Party(OLD, "old-dan")
    sent, _ = alice.crypto.build_envelope(
        "keys in the block", old_dan.pub, x={
            "k": "text", "pq": alice.keys.pq_kid,
            "pqe": M.b64e(alice.keys.pq_public)},
        recipient=old_dan.address, cid="r" * 32)
    try:
        got = old_dan.crypto.open_envelope(sent, alice.address,
                                           cid="r" * 32)
        check("the previous release reads a message carrying our key",
              got["body"] == "keys in the block")
    except Exception as exc:                      # noqa: BLE001
        check("the previous release reads a message carrying our key",
              False, f"{type(exc).__name__}: {exc}")


# =============================================================================
section("secp256k1: every tier gives the same bytes (16.0)")
# libsecp256k1 (coincurve, where installed) now also multiplies and signs;
# the windowed pure-Python tier and the affine reference must agree with it
# byte for byte, or messages would stop opening between the two.
import hashlib                                                # noqa: E402

native = M.HAVE_NATIVE_SECP256K1
print(f"  (libsecp256k1 here: {native})")
rng = __import__("random").Random(1604)


def pure(fn, *args):
    """`fn` with the native tier switched off."""
    saved = M.HAVE_NATIVE_SECP256K1
    M.HAVE_NATIVE_SECP256K1 = False
    try:
        return fn(*args)
    finally:
        M.HAVE_NATIVE_SECP256K1 = saved


same_sign = same_mul = same_ecdh = True
for _ in range(40):
    priv = rng.randrange(1, M._N)
    other = rng.randrange(1, M._N)
    msg = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 300)))
    point = M._ec_mul_affine(other, M._G)
    sig = M.ecdsa_sign(msg, priv)
    same_sign &= sig == pure(M.ecdsa_sign, msg, priv) == \
        M.ecdsa_sign_affine(msg, priv) and M.ecdsa_verify_affine(
            msg, sig, M._ec_mul_affine(priv, M._G))
    same_mul &= M._ec_mul(priv, point) == pure(M._ec_mul, priv, point) == \
        M._ec_mul_affine(priv, point) and \
        M.priv_to_pub(priv) == M._ec_mul_affine(priv, M._G)
    same_ecdh &= M.ecdh_shared_secret(priv, point) == pure(
        M.ecdh_shared_secret, priv, point) == hashlib.sha256(
        M.pub_to_compressed(M._ec_mul_affine(priv, point))).digest()
check("signatures: the same bytes from every tier, and they verify",
      same_sign)
check("scalar multiples (each message's own key, a public key) agree",
      same_mul)
check("shared secrets (ECDH) agree", same_ecdh)
edges = [(1, M._G), (M._N - 1, M._G), (M._N, M._G), (0, M._G),
         (M._N + 5, M.priv_to_pub(7))]
check("...at the edges: 1, N-1, N and 0 (no point), and k past N",
      all(M._ec_mul(k, p) == pure(M._ec_mul, k, p) ==
          (M._ec_mul_affine(k % M._N, p) if k % M._N else None)
          for k, p in edges))


print("\nA file put in place while something has it open (17.2.1)")
# Windows refuses a rename onto a file anything has open: the Devices runs
# lost a setting that way. The refusal is played here on any system.
real_replace, waits, tries = M.os.replace, M.RENAME_WAITS, []


def refusing(src, dst):
    tries.append(dst)
    if len(tries) <= 2:
        raise PermissionError(13, "in use", str(dst))
    real_replace(src, dst)


M.os.replace, M.RENAME_WAITS = refusing, (0.001, 0.001, 0.001)
try:
    M.save_pref("put_in_place", 1)
finally:
    M.os.replace, M.RENAME_WAITS = real_replace, waits
check("a setting is saved though the first two renames are refused",
      M.load_prefs().get("put_in_place") == 1 and len(tries) == 3, tries)
# 18.0: the Devices run's Windows job lost a setting again. The app's
# own threads read prefs.json all the time, and Windows refuses the
# rename while any of them has it open: played here, with readers on
# four threads while fifty settings are saved, no try again.
import threading                                               # noqa: E402
import time                                                    # noqa: E402
open_now, refused, stop = [0], [], threading.Event()
real_read_text = Path.read_text


def reading(path, *args, **kwargs):
    if path.name != "prefs.json":
        return real_read_text(path, *args, **kwargs)
    open_now[0] += 1
    try:
        time.sleep(0.001)                   # a reader holds it a moment
        return real_read_text(path, *args, **kwargs)
    finally:
        open_now[0] -= 1


def as_windows(src, dst):
    if Path(dst).name == "prefs.json" and open_now[0]:
        refused.append(dst)
        raise PermissionError(13, "in use", str(dst))
    real_replace(src, dst)


def keep_reading():
    while not stop.is_set():
        M.load_prefs()


Path.read_text, M.os.replace, M.RENAME_WAITS = reading, as_windows, ()
readers = [threading.Thread(target=keep_reading) for _ in range(4)]
for each in readers:
    each.start()
saved = []
try:
    for n in range(50):
        M.save_pref("read_meanwhile", n)
        saved.append(M.load_prefs().get("read_meanwhile"))
finally:
    stop.set()
    for each in readers:
        each.join()
    Path.read_text, M.os.replace, M.RENAME_WAITS = \
        real_read_text, real_replace, waits
check("a setting saved while the app's own threads read the settings is "
      "never refused by them (Windows' rule played)",
      saved == list(range(50)) and not refused,
      f"{len(refused)} refused; {saved[:8]}")
source = Path(M.__file__).read_text(encoding="utf-8")
check("...and every file the app puts in place whole goes through "
      "replace_file", source.count("os.replace(") == 2 and
      ".replace(DATA_DIR" not in source)


# =============================================================================
section("18.1: what arrives malformed is refused or let go, never raised")
# Before 18.1 a message that opened and then raised ended the session; the
# relay sent it again on every reconnect, and the mailbox waited behind it.
import ast                                                     # noqa: E402
import base64                                                  # noqa: E402
import random                                                  # noqa: E402

mapp = StubApp(receiver, DB_DIR / "malformed.db")       # a mailbox of its own
mapp.store.upsert_contact(sender.address, "sender", sender.pub)
mworker = M.SyncWorker(mapp)


def sealed(x=None, body="hello", change=None):
    """A frame from `sender` whose signed payload `change` alters just
    before it is sealed: what an app made to do harm could send."""
    real, cid = json.dumps, uuid.uuid4().hex

    def dumps(obj, **kw):
        if change and isinstance(obj, dict) and "signature" in obj:
            change(obj)
        return real(obj, **kw)
    try:
        json.dumps = dumps
        env, _ = sender.crypto.build_envelope(
            body, receiver.pub, x=x, recipient=receiver.address, cid=cid)
    finally:
        json.dumps = real
    _sid[0] += 1
    return {"id": _sid[0], "sender": sender.address, "client_msg_id": cid,
            "envelope": env}


def taken(frame):
    """What the worker answers for `frame`, or the exception it raised."""
    try:
        return mworker._ingest_message(frame)
    except Exception as exc:                               # noqa: BLE001
        return exc


def named(name):
    return lambda payload: payload["sender"].update(username=name)


got = taken(sealed(change=lambda p: p.update(body=["not", "text"])))
check("a body that is not text: refused as an envelope that will not open, "
      "left on the relay for now", got is None, repr(got))
before = mapp.store.get_contact(sender.address)["username"]
frames = [sealed(body=f"named {n}", change=named(name)) for n, name in
          enumerate((2 ** 200, {"a": 1}, "x" * 500, "[size=abc]"))]
got = [taken(frame) for frame in frames]
check("a sender's name that is no name (a number too large to store, an "
      "object, 500 letters, markup): the message is kept, the name is not",
      got == [f["id"] for f in frames] and
      mapp.store.get_contact(sender.address)["username"] == before,
      f"{got} {mapp.store.get_contact(sender.address)['username']!r}")
got = taken(sealed(change=named("\ud800")))
check("half of a surrogate pair anywhere in it (no database stores it, no "
      "screen draws it): refused whole", got is None, repr(got))
frame = sealed(x={"k": "media", "media": dict(media_ref(), kind={"a": 1})},
               body="a photo, its kind an object")
got = taken(frame)
check("a media block whose kind is not a word: the words arrive, as a "
      "plain message", got == frame["id"] and mapp.store.message_by_cid(
          sender.address, frame["client_msg_id"])["kind"] == "text",
      repr(got))

real_ext = M.sanitize_ext_with_body


def faulty(*_a):
    raise RuntimeError("a fault of this app's own")


M.sanitize_ext_with_body = faulty
try:
    frame = sealed(x={"k": "msg"}, body="opened, not taken in")
    got = [taken(frame) for _n in range(M.SyncWorker.MAX_DECRYPT_ATTEMPTS)]
finally:
    M.sanitize_ext_with_body = real_ext
last = mapp.store.messages_for(sender.address)[-1]
check("a message that opens and cannot be taken in (a fault here) is left "
      "on the relay twice, then recorded as unreadable and acknowledged: "
      "it never holds up what comes after it",
      got == [None, None, frame["id"]] and "could not read" in last["body"]
      and last["read"], f"{got} {last['body'][:40]!r}")
real_control = mworker._handle_control
mworker._handle_control = faulty
try:
    frame = sealed(x={"k": "ctl", "ctl": {"op": "timer"}})
    got = taken(frame)
finally:
    mworker._handle_control = real_control
check("...and an instruction that cannot be followed is acknowledged at "
      "once: it is no better for coming again", got == frame["id"],
      repr(got))


def link_of(mark, payload):
    raw = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"https://example.org{M.CALL_LINK_PATH}#{mark}{raw}"


# Past the depth Python's JSON reader follows (1,000 on 3.11, some
# 10,000 on 3.13): a RecursionError, which is not a ValueError.
deep = b"[" * 50_000
try:
    got = (M.parse_call_link(link_of(M.CALL_LINK_MARK, deep)),
           M.parse_join_link(link_of(M.JOIN_LINK_MARK, deep)),
           M.address_in_qr("[" * 50_000),
           M.address_in_qr("{" + '"a":[' * 50_000))
except Exception as exc:                                   # noqa: BLE001
    got = exc
check("a link or a scanned code holding JSON nested tens of thousands deep "
      "is not one of ours, and says so without raising",
      got == (None, None, None, None), repr(got)[:80])

rng = random.Random(181)
ODD = [None, True, 0, -1, 2 ** 200, 1.5, float("nan"), float("inf"), "", "a",
       "9" * 5000, "\x00", "\u202eabc", "[size=abc]", "../..", "x" * 9000,
       "ab" * 16, "0f" * 32, sender.address, M.now_iso(), "image", "video"]
WORDS = sorted(set(__import__("re").findall(
    r'[\[(]"([a-z_]{1,10})"[\]),]', source)))


def odd(depth=0):
    roll = rng.random()
    if depth > 3 or roll < 0.5:
        return rng.choice(ODD)
    if roll < 0.65:
        return [odd(depth + 1) for _n in range(rng.choice((0, 1, 3, 40)))]
    return {rng.choice(WORDS): odd(depth + 1)
            for _n in range(rng.choice((1, 2, 4, 8)))}


TAKERS = [getattr(M, name) for name in sorted(dir(M))
          if name.startswith("sanitize_")]
people = [sender.address, receiver.address]
raised = []
for _n in range(1500):
    value = odd()
    for take in TAKERS:
        args = {"sanitize_stamp": (value, M.now_iso()),
                "sanitize_preview": (value, "see https://example.org/"),
                "sanitize_ext_with_body": (value, "see https://example.org/"),
                "sanitize_group_rules": (value, people),
                "sanitize_group_admins": (value, set(people), people[0]),
                "sanitize_call_signal": ("call", value)}.get(
            take.__name__, (value,))
        try:
            take(*args)
        except Exception as exc:                           # noqa: BLE001
            raised.append(f"{take.__name__}: {type(exc).__name__}")
check(f"1,500 values nobody meant to send, through each of the "
      f"{len(TAKERS)} sanitizers: none raises",
      not raised and len(TAKERS) >= 20, str(sorted(set(raised)))[:200])

tree = ast.parse(source)
check("no assert guards anything in the app (the APK is compiled without "
      "them): a column a setting may not name is refused by a raise",
      not [n for n in ast.walk(tree) if isinstance(n, ast.Assert)] and
      expect_reject(lambda: mapp.store.set_contact_setting(
          sender.address, "address", 1), "a column outside the list")[0])
opened = []
real_open = __import__("webbrowser").open
__import__("webbrowser").open = opened.append
try:
    M.open_url("https://example.org/page")
    refused = [expect_reject(lambda u=u: M.open_url(u), "not a web link")[0]
               for u in ("file:///etc/passwd", "intent://x#Intent;end",
                         "javascript:alert(1)", "", "ftp://example.org")]
finally:
    __import__("webbrowser").open = real_open
check("only a web link is ever handed to the browser, whoever asks",
      opened == ["https://example.org/page"] and all(refused),
      f"{opened} {refused}")

key = M.b64e(os.urandom(32))
real_run, real_platform = M.subprocess.run, sys.platform
said = []
real_warning = M.log.warning
M.log.warning = lambda *a, **_kw: said.append(" ".join(map(str, a)))


def failing(argv, **_kw):
    raise M.subprocess.CalledProcessError(1, argv)


try:
    sys.platform, M.subprocess.run = "darwin", failing
    ok, why = expect_reject(lambda: M.DeviceKeystore.seal(M.b64d(key)),
                            "the keychain refused")
finally:
    sys.platform, M.subprocess.run = real_platform, real_run
    M.log.warning = real_warning
check("a keystore that refuses is reported without the key (a failed "
      "command's own text holds its arguments, the key among them on a "
      "Mac)", ok and key not in why and key not in " ".join(said) and
      "BackupError" in why, why[:90])


# =============================================================================
if __name__ == "__main__":
    passed = sum(1 for _n, c in RESULTS if c)
    print(f"\n  {passed}/{len(RESULTS)} passed")
    sys.exit(0 if passed == len(RESULTS) else 1)
