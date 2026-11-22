#!/usr/bin/env python3
"""19.0.1 (SECURITY27): the owner's security check of 22 November 2026,
kept as checks. "We need to ensure that other unauthorised users do not
cross over into other machines - this applies to our messages as well -
especially if they act as a server or someone they are not."

    xvfb-run -a python3 desktop/test_security.py main.py

Each check plays one attack against this client's own code and expects it
refused: a relay acting as the server (it carries every message and call,
and answers the directory), someone acting as someone they are not, someone
crossing into another computer or another identity, and a power cut taking
the keys or Remote access's list with it. Where each is answered in the
code is named in its check; ARCHITECTURE_client.md says why.
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
if sys.platform.startswith("linux"):     # xvfb-run's X display
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")
HOME = tempfile.mkdtemp(prefix="fext-security-")
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME                 # Path.home() on Windows
os.environ["FEXT_LANG"] = "en"
os.environ["FEXT_REMOTE_TEST"] = "1"             # no real injector here

_spec = importlib.util.spec_from_file_location(
    "fext_security", sys.argv[1] if len(sys.argv) > 1 else "main.py")
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_security"] = M
_spec.loader.exec_module(M)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""), flush=True)


def refused(fn):
    """True when `fn` raises (the attack refused), with what it said."""
    try:
        fn()
    except Exception as exc:                      # noqa: BLE001
        return True, f"{type(exc).__name__}: {exc}"
    return False, "accepted"


class Party:
    """One identity, its keys and its sealing."""

    def __init__(self, label):
        self.keys = M.IdentityManager()
        priv, _p, _a, _n = M.mine_identity()
        self.address = self.keys.add_identity(priv, label)
        self.keys.activate(self.address)
        self.crypto = M.CryptoEngine(self.keys)
        self.pub = self.keys.public_key_hex


def seal(sender, recipient, body, cid):
    env, _ = sender.crypto.build_envelope(
        body, recipient.pub, x={"k": "text"}, recipient=recipient.address,
        cid=cid)
    return env


alice, bob, carol = Party("alice"), Party("bob"), Party("carol")
relay = Party("relay")                 # a key of the relay's own

print("\nA relay acting as the server")
env = seal(alice, bob, "for bob only", "a" * 32)
ok, why = refused(lambda: bob.crypto.open_envelope(env, carol.address,
                                                   cid="a" * 32))
check("it says another sent a message: refused, the signer's address is "
      "worked out from the signer's key (open_envelope)", ok, why)
ok, why = refused(lambda: carol.crypto.open_envelope(env, alice.address,
                                                     cid="a" * 32))
check("it hands bob's message to carol: she cannot open it", ok, why)
spoiled = dict(env, ciphertext=M.b64e(bytes(
    b ^ (1 if i == 40 else 0)
    for i, b in enumerate(M.b64d(env["ciphertext"])))))
ok, why = refused(lambda: bob.crypto.open_envelope(spoiled, alice.address,
                                                   cid="a" * 32))
check("it changes one bit of it: refused (AES-GCM)", ok, why)
ok, why = refused(lambda: bob.crypto.open_envelope(env, alice.address,
                                                   cid="b" * 32))
check("it delivers it again under another message's number: refused (the "
      "signed block names its message)", ok, why)


class Directory(M.ApiClient):
    """The relay's directory, answering what the test says."""

    def __init__(self, answer):
        self.answer = answer

    def _get(self, path, params=None):
        return dict(self.answer)


ok, why = refused(lambda: Directory({
    "address": bob.address, "username": "bob",
    "public_key": relay.pub}).lookup(username="bob"))
check("asked for bob, it gives its own key under bob's address: refused, "
      "nothing saved (ApiClient.lookup)", ok, why)
ok, why = refused(lambda: Directory({
    "address": carol.address, "username": "carol",
    "public_key": carol.pub}).lookup(address=bob.address))
check("asked for bob's address, it answers with someone else: refused", ok,
      why)
check("…a true answer is taken",
      Directory({"address": bob.address, "username": "bob",
                 "public_key": bob.pub}).lookup(address=bob.address)
      ["address"] == bob.address)
store = M.LocalStore(os.path.join(HOME, "alice.db"))
store.upsert_contact(bob.address, "bob", bob.pub)
ok, why = refused(lambda: store.upsert_contact(bob.address, "bob",
                                               relay.pub))
check("whatever passed it on, a contact's key is never replaced by one "
      "that is not its address's (LocalStore.upsert_contact)",
      ok and store.get_contact(bob.address)["public_key"] == bob.pub, why)
state = {"id": "g" * 32, "name": "Ours", "v": 2, "admin": alice.address,
         "members": [{"a": alice.address, "k": alice.pub},
                     {"a": bob.address, "k": relay.pub}]}
check("a group's state with its own key under a member's address: refused "
      "whole (sanitize_group_state)", M.sanitize_group_state(state) is None)
secret = os.urandom(32)
tag = os.urandom(8)
theirs = M.CallKeys(secret, "c" * 32, tag, caller=True)
mine = M.CallKeys(secret, "c" * 32, tag, caller=False)
stranger = M.CallKeys(os.urandom(32), "c" * 32, tag, caller=True)
packet = theirs.seal(M.PKT_INPUT, b"click", 0)
check("it sends a call packet of its own (it holds no call keys): dropped "
      "(CallKeys.open)", mine.open(stranger.seal(M.PKT_INPUT, b"x", 0))
      is None)
check("…a true one is taken once, and the same one sent again dropped",
      mine.open(packet) is not None and mine.open(packet) is None)
moved = bytes([M.PKT_MEDIA]) + theirs.seal(M.PKT_INPUT, b"click", 0)[1:]
check("…one whose kind it changed in the open header: dropped",
      mine.open(moved) is None)
check("it plays an old remote session's offer again, with its own time "
      "saying it is new: set aside by the time signed in it "
      "(remote_offer_for_here)", not M.remote_offer_for_here(
          {"t": time.time() - 3600, "relay_at": time.time()}))
check("…and the same offer twice: answered once (remote_offer_once)",
      M.remote_offer_once("d" * 32) and not M.remote_offer_once("d" * 32))

print("\nSomeone acting as someone they are not")
mallory = Party("mallory")
lying = mallory.crypto.build_envelope("I am alice", bob.pub,
                                      x={"k": "text"}, recipient=bob.address,
                                      cid="e" * 32)[0]
ok, why = refused(lambda: bob.crypto.open_envelope(lying, alice.address,
                                                   cid="e" * 32))
check("mallory signs as herself and says she is alice: refused", ok, why)
opened = bob.crypto.open_envelope(env, alice.address, cid="a" * 32)
raw = json.dumps(opened, separators=(",", ":")).encode()
eph = int.from_bytes(os.urandom(32), "big") % (M._N - 1) + 1
eph_pub = M.pub_to_compressed(M.priv_to_pub(eph))
key = M.CryptoEngine._derive_key(M.ecdh_shared_secret(
    eph, M.compressed_to_pub(bytes.fromhex(carol.pub))))
nonce = os.urandom(12)
resealed = {"ephemeral_public_key": eph_pub.hex(), "nonce": M.b64e(nonce),
            "ciphertext": M.b64e(M.AESGCM(key).encrypt(
                nonce, raw, eph_pub + bytes.fromhex(carol.pub)))}
ok, why = refused(lambda: carol.crypto.open_envelope(resealed, alice.address,
                                                     cid="a" * 32))
check("bob seals alice's signed message again, to carol: refused (the "
      "signed block names its one recipient)", ok, why)
bob_store = M.LocalStore(os.path.join(HOME, "bob.db"))
for who in (alice, mallory):
    bob_store.upsert_contact(who.address, who.address[:8], who.pub)
gid = "f" * 32
bob_store.add_in(gid, "alice wrote this", M.now_iso(), 1, "m" * 32,
                 author=alice.address)
check("mallory edits alice's message: nothing changes "
      "(LocalStore.edit_message: the author's own only)",
      bob_store.edit_message(gid, "m" * 32, "in", "mallory's words",
                             M.now_iso(), author=mallory.address) is None and
      bob_store.message_by_cid(gid, "m" * 32, "in")["body"] ==
      "alice wrote this")
check("mallory deletes it for everyone: nothing goes (delete_by_author)",
      bob_store.delete_by_author(mallory.address, ["m" * 32], gid) == [] and
      bob_store.message_by_cid(gid, "m" * 32, "in") is not None)
group = {"state": "active", "admin": alice.address, "admins": {},
         "rules": {}, "members": [{"a": alice.address},
                                  {"a": mallory.address}]}
check("mallory pins in a group whose rule does not let members: not let "
      "(group_may_pin); its owner is",
      not M.group_may_pin(group, mallory.address) and
      M.group_may_pin(group, alice.address))

print("\nCrossing into another computer, or another identity")
engine = M.CallEngine(os.urandom(16).hex(), os.urandom(8), 1, None, "",
                      on_event=lambda *_a: None)
engine.lets = False
engine._take_input(M.pack_input([(1, M.INPUT_TEXT, ("rm -rf",))], 0, 0))
check("input from a call this computer does not let in: nothing done "
      "(CallEngine._take_input)", engine.input_in is None)
engine.sock.close()
check("an offer for another computer of the same identity: not for this "
      "one (its number, \"to\")", not M.remote_offer_for_here(
          {"t": time.time(), "to": "0" * 16}) and M.remote_offer_for_here(
          {"t": time.time(), "to": M.device_id()}))
M.remote_access_grant(alice.address, carol.address, 1)
check("listed by one identity is not by another on the same computer "
      "(remote_access_allows, per identity)",
      M.remote_access_allows(alice.address, carol.address) and
      not M.remote_access_allows(bob.address, carol.address))
check("a grant past its end lets no one in",
      not M.remote_access_allows(alice.address, carol.address,
                                 now=time.time() + 2 * 86400))
words = []
app = types.SimpleNamespace(call_name=lambda peer: "carol",
                            controlling=lambda call: False)
M.RemoteControlMixin._remote_cmd(app, types.SimpleNamespace(
    lets=False, asks=False, engine=None), {"op": "restart", "n": 1})
check("a restart, or a program closed, asked of a computer that does not "
      "let the asker in: nothing done (RemoteControlMixin._remote_cmd)",
      not M._REMOTE_TEST_RESTARTS)
first = M.hold_lock(Path(HOME) / "instance.lock")
check("one Ferritext per data folder (main: instance.lock)",
      first is not None and M.hold_lock(Path(HOME) / "instance.lock") is None)

print("\nA power cut: the keys and Remote access's list survive")
M.IDENTITIES_PATH.write_bytes(b"\0" * 300)       # what a cut can leave
kept = M.IdentityManager()
check("identities.json left as zeros: its last good copy is read, the keys "
      "are there (IdentityManager._load, write_durably)",
      kept.find(mallory.address) is not None, [
          i["address"][:8] for i in kept.identities])
kept.remove_identity(mallory.address)
gone = mallory.keys.find(mallory.address)["priv"] if \
    "priv" in mallory.keys.find(mallory.address) else None
texts = [M.IDENTITIES_PATH.read_text("utf-8"),
         M.IDENTITIES_PATH.with_name("identities.json.bak").read_text(
             "utf-8")]
check("an identity removed: its key is in neither the file nor its copy",
      all(mallory.address not in t for t in texts) and
      (gone is None or all(str(gone) not in t for t in texts)))
M.remote_access_grant(bob.address, alice.address, 0)
M.save_pref("theme", "dark")                     # the list in the copy too
(M.DATA_DIR / "prefs.json").write_bytes(b"")
check("prefs.json left empty: the copy is read, Remote access's list still "
      "lets alice in (load_prefs)",
      M.remote_access_allows(bob.address, alice.address))

passed = sum(ok for _n, ok in RESULTS)
print(f"\n  {passed}/{len(RESULTS)} passed")
sys.exit(0 if passed == len(RESULTS) else 1)
