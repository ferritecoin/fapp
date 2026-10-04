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
    check("the database is stamped with schema 2", user_version(path) == 2)
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
check("migrating twice is a no-op", user_version(path) == 2)

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
      ("resend_request", sender.address, "ours" * 8) in app.events())

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
if __name__ == "__main__":
    passed = sum(1 for _n, c in RESULTS if c)
    print(f"\n  {passed}/{len(RESULTS)} passed")
    sys.exit(0 if passed == len(RESULTS) else 1)
