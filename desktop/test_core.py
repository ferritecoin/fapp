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
os.environ.setdefault("SDL_VIDEODRIVER", "x11")
HOME = tempfile.mkdtemp(prefix="fext-core-")
os.environ["HOME"] = HOME

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


class StubApp:
    """What SyncWorker._ingest_message reaches for, and nothing more."""

    def __init__(self, party, db):
        self.keys, self.crypto = party.keys, party.crypto
        self.store = M.LocalStore(db)
        self.ui_queue = queue.Queue()

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


app.events()
acked, first = deliver("first message")
row = app.store.message_by_cid(sender.address, first)
check("a plain message is stored and acked", acked and row and
      row["kind"] == "text")
check("...and raises an 'incoming' event for notifications",
      any(e[0] == "incoming" for e in app.events()))

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
if __name__ == "__main__":
    passed = sum(1 for _n, c in RESULTS if c)
    print(f"\n  {passed}/{len(RESULTS)} passed")
    sys.exit(0 if passed == len(RESULTS) else 1)
