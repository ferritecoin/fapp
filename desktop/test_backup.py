#!/usr/bin/env python3
"""Backup and restore, end to end (16.0), each device a process of its own
with its own home folder, as phones are.

    xvfb-run -a python3 desktop/test_backup.py main.py

  * a phone full of everything (chats, a group, photos, a voice note,
    reactions, stars, drafts, pins, mutes, timers, verified and post-
    quantum contacts, a Deleted User, its own picture, a second identity)
    is backed up and restored on a new one through the screens: every
    table of every identity comes back as it was, its files where this
    device keeps them, and the restored identity writes and reads again;
  * restored beside another identity, that one stays in use, untouched;
  * a passphrase typed composed or decomposed (café) opens the same file;
  * a backup without media restores its messages, and their chat opens;
  * a backup whose sealed database is damaged changes nothing at all;
  * backups made by 11.0, 13.0 and 15.1.1 restore here;
  * a full disk while saving leaves no part-file behind;
  * a large backup (20,000 messages, 30 MB of media) is timed both ways;
  * on Android the chosen file is copied off the UI thread (a cloud drive
    can take minutes), a file that cannot be read is said so, and the
    sheet's private copy goes with it;
  * 17.0 (F10): automatic backups made with no one there, the key kept by
    the keystore (never the passphrase), the last three kept, reminders
    stopped meanwhile; the newest restored on a new device with the
    passphrase, every table and file as it was.
"""
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unicodedata
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PASS = "correct horse battery"
PHASE = sys.argv[2] if len(sys.argv) > 2 else "main"
CLIENT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "main.py")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""), flush=True)


def load(client, home):
    """The app's module, on a device whose home is `home`."""
    os.environ.update(KIVY_NO_ARGS="1", KIVY_NO_CONSOLELOG="1", FEXT_LANG="en",
                      FEXT_SERVER_HOST="127.0.0.1:1", HOME=home,
                      USERPROFILE=home)          # Path.home() on Windows
    if sys.platform.startswith("linux"):         # xvfb-run's X display
        os.environ["SDL_VIDEODRIVER"] = "x11"
    spec = importlib.util.spec_from_file_location("fext_backup", client)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fext_backup"] = module
    spec.loader.exec_module(module)
    return module


def dump(db_path):
    """Every row of every table, a file's place reduced to its name."""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    out = {}
    try:
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE "
                                   "type = 'table' ORDER BY name"):
            rows = []
            for row in db.execute(f"SELECT * FROM {table}"):
                row = dict(row)
                for key in ("media_path", "path"):
                    if row.get(key):
                        row[key] = os.path.basename(row[key])
                rows.append(json.dumps(row, sort_keys=True, default=str))
            out[table] = sorted(rows)
    finally:
        db.close()
    return out


def phase(name, home, *args, client=None):
    """Run this file as one device: `name` in a process of its own."""
    command = [sys.executable, "-u", __file__, client or CLIENT, name,
               home] + list(args)
    if sys.platform.startswith("linux"):       # a display of its own
        command = ["xvfb-run", "-a"] + command
    run = subprocess.run(command, capture_output=True, text=True,
                         timeout=900)
    lines = [ln for ln in run.stdout.splitlines()
             if ln.startswith(("  [PASS]", "  [FAIL]", "  ·"))]
    for line in lines:
        print(line, flush=True)
    if run.returncode not in (0, 1):
        print(run.stdout[-3000:], run.stderr[-3000:])
    return [ln.startswith("  [PASS]") for ln in lines
            if ln.startswith(("  [PASS]", "  [FAIL]"))], run


# =============================================================================
#  The phone that is backed up
# =============================================================================
def make_phone(home, out):
    M = load(CLIENT, home)
    from PIL import Image
    sys.path.insert(0, HERE)
    keys = M.IdentityManager()
    priv, *_ = M.mine_identity()
    alice = keys.add_identity(priv, "Personal")
    keys.activate(alice)
    keys.set_username("alice")
    store = M.LocalStore(keys.store_path())
    people = {}
    for name in ("bob", "cara", "dev"):
        p, *_ = M.mine_identity()
        address = M.address_from_priv(p)
        pub = M.pub_to_compressed(M.priv_to_pub(p)).hex()
        store.upsert_contact(address, name, pub)
        people[name] = {"address": address, "priv": p, "pub": pub}
    bob, cara, dev = (people[n]["address"] for n in ("bob", "cara", "dev"))
    M.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    photo = M.MEDIA_DIR / f"photo_{uuid.uuid4().hex}.jpg"
    Image.new("RGB", (640, 480), (90, 140, 200)).save(photo, "JPEG")
    voice = M.MEDIA_DIR / f"voice_{uuid.uuid4().hex}.m4a"
    voice.write_bytes(os.urandom(48000))
    now = M.now_iso()
    cid = uuid.uuid4().hex
    store.add_in(bob, "Are we still on for Saturday?", now, 101, cid,
                 unread=False)
    first = store.message_by_cid(bob, cid, "in")["id"]
    store.add_out(bob, "Yes — 10:30 at the station", now, uuid.uuid4().hex)
    store.add_out(bob, "Photo", now, uuid.uuid4().hex, kind="image",
                  ext={"media": {"kind": "image", "w": 640, "h": 480}},
                  media_path=str(photo), media_state="ready")
    heard = uuid.uuid4().hex
    store.add_in(bob, "Voice message (0:03)", now, 102, heard, kind="voice",
                 ext={"media": {"kind": "voice", "dur": 3000}},
                 media_state="ready", unread=False)
    store.set_media(store.message_by_cid(bob, heard, "in")["id"],
                    path=str(voice), state="ready")
    store.set_reaction(bob, cid, alice, "\U0001F44D")
    store.set_starred([first], True)
    store.add_caps(bob, M.LocalStore.CAP_V9 | M.LocalStore.CAP_CALL |
                   M.LocalStore.CAP_PIC | M.LocalStore.CAP_TIMER)
    if hasattr(store, "set_gone"):
        store.set_gone(dev, True)                   # 16.0: a Deleted User
    store.add_out(dev, "Still there?", now, uuid.uuid4().hex)
    store.set_chat_flag([bob], "pinned", 1000.0)
    store.set_chat_flag([cara], "archived", 1)
    store.add_in(cara, "In the archive", now, 103, uuid.uuid4().hex)
    store.set_draft(bob, "half a thought")
    store.set_chat_timer(cara, 86400, time.time())
    store.set_mute(dev, M.LocalStore.MUTE_FOREVER)
    gid = "ab" * 16
    store.save_group({"id": gid, "name": "Hikers", "admin": alice, "v": 2,
                      "members": [{"a": alice, "k": keys.public_key_hex,
                                   "u": "alice"},
                                  {"a": bob, "k": people["bob"]["pub"],
                                   "u": "bob"},
                                  {"a": cara, "k": people["cara"]["pub"],
                                   "u": "cara"}]}, "active")
    store.add_in(M.group_peer(gid), "Summit by noon", now, 104,
                 uuid.uuid4().hex, author=bob)
    M.PICTURES_DIR.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (96, 96), (200, 80, 60)).save(
        M.own_picture_path(alice), "JPEG")
    # A second identity on the phone, with a chat of its own.
    work_priv, *_ = M.mine_identity()
    work = keys.add_identity(work_priv, "Work")
    work_store = M.LocalStore(M.IdentityManager.store_path_for(work))
    work_store.upsert_contact(bob, "bob", people["bob"]["pub"])
    work_store.add_in(bob, "The quarterly numbers", now, 201,
                      uuid.uuid4().hex)
    work_store.close()
    store.close()
    expect = {a: dump(str(M.IdentityManager.store_path_for(a)))
              for a in (alice, work)}
    backups = os.path.join(home, "backups")
    os.makedirs(backups)
    made = M.make_backup(keys, PASS, os.path.join(backups, "full"
                                                  + M.BACKUP_SUFFIX))
    M.make_backup(keys, PASS, os.path.join(backups, "text"
                                           + M.BACKUP_SUFFIX),
                  include_media=False)
    composed = unicodedata.normalize("NFC", "café au lait 1982")
    M.make_backup(keys, composed, os.path.join(backups, "cafe"
                                               + M.BACKUP_SUFFIX))
    json.dump({"alice": alice, "work": work, "expect": expect,
               "made": made, "bob": {"priv": hex(people["bob"]["priv"]),
                                     "address": bob,
                                     "pub": people["bob"]["pub"]},
               "photo": photo.name, "voice": voice.name,
               "photo_bytes": hashlib.sha256(photo.read_bytes()).hexdigest(),
               "alice_record": keys.find(alice)},
              open(out, "w"))
    print("  · a phone with two identities, chats, a group and media made "
          f"({made['size'] / 1e6:.2f} MB backup)")


# =============================================================================
#  A new phone, restored through the screens
# =============================================================================
def restore_on_new_phone(home, facts_path, backups):
    facts = json.load(open(facts_path))
    M = load(CLIENT, home)
    from kivy.base import EventLoop
    from kivy.clock import Clock
    from kivy.core.window import Window

    def pump(seconds=0.2):
        end = time.time() + seconds
        while time.time() < end:
            EventLoop.idle()
            time.sleep(0.01)

    def wait_for(cond, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            Clock.tick()
            if cond():
                return True
            time.sleep(0.05)
        return cond()

    app = M.FextApp()
    app._dismiss_splash = lambda *_a: None
    app._probe_transport = lambda: None
    app._start_worker = lambda: None          # no relay here
    root = app.build()
    Window.add_widget(root)
    pump(0.5)
    check("(the new phone has no identity)", not app.keys.has_identity)
    sheet = M.RestoreSheet(app)
    sheet.open()
    pump(0.3)
    sheet._picked(os.path.join(backups, "full" + M.BACKUP_SUFFIX))
    sheet.passphrase.text = "not the passphrase"
    sheet._start()
    check("a wrong passphrase says so, and restores nothing",
          wait_for(lambda: sheet.status.text == "Wrong passphrase.") and
          not app.keys.has_identity, sheet.status.text)
    sheet.passphrase.text = PASS
    sheet._start()
    check("the right one restores both identities, and says so",
          wait_for(lambda: sheet.status.text.startswith("Restored")) and
          "Personal" in sheet.status.text and "Work" in sheet.status.text,
          sheet.status.text)
    alice, work = facts["alice"], facts["work"]
    check("...the first one in use", app.keys.address == alice and
          app.store is not None)
    same = {a: dump(str(M.IdentityManager.store_path_for(a)))
            for a in (alice, work)}
    for who, address in (("Personal", alice), ("Work", work)):
        want, got = facts["expect"][address], same[address]
        differ = [t for t in set(want) | set(got)
                  if want.get(t) != got.get(t)]
        check(f"every table of '{who}' as it was", not differ, differ)
    store = app.store
    photo_row = [m for m in store.messages_for(facts["bob"]["address"])
                 if m["kind"] == "image"][0]
    check("its photo and voice note, where this phone keeps them",
          photo_row["media_path"] == str(M.MEDIA_DIR / facts["photo"]) and
          hashlib.sha256(open(photo_row["media_path"], "rb").read())
          .hexdigest() == facts["photo_bytes"] and
          (M.MEDIA_DIR / facts["voice"]).is_file())
    check("...and its own picture", M.own_picture_path(alice).is_file())
    app.refresh_sidebar()
    pump(0.4)
    shown = {c["peer"] for c in store.conversations(app.keys.address)}
    check("its chats are in the list again, the group among them",
          facts["bob"]["address"] in shown and any(
              p.startswith(M.GROUP_PREFIX) for p in shown))
    app.open_conversation(facts["bob"]["address"])
    pump(0.6)
    screen = app.conversation_screen
    check("...a chat opens with its messages, and its draft",
          len(screen._bubbles) >= 4 and
          screen.entry.text == "half a thought", len(screen._bubbles))
    sheet.dismiss(animation=False)

    # The restored identity writes and reads again: Bob, on his own phone.
    relay = []

    class Api:
        def __init__(self, keys):
            self.keys = keys
            self._features = {"blobs": True}

        def features(self):
            return self._features

        def send_message(self, recipient, envelope, cid):
            relay.append({"id": len(relay) + 1, "sender": self.keys.address,
                          "recipient": recipient, "envelope": envelope,
                          "client_msg_id": cid})
            return {"server_id": len(relay)}

        def read(self, ids):
            return {}

    bob_app = M.FextApp()
    bob_app._dismiss_splash = lambda *_a: None
    bob_app._probe_transport = lambda: None
    bob_app.build()
    bob_addr = bob_app.keys.add_identity(int(facts["bob"]["priv"], 16),
                                         "bob")
    bob_app.keys.activate(bob_addr)
    bob_app.crypto = M.CryptoEngine(bob_app.keys)
    bob_app.api = Api(bob_app.keys)
    bob_app.store = M.LocalStore(bob_app.keys.store_path())
    bob_app.worker = M.SyncWorker(bob_app)
    bob_app.store.upsert_contact(alice, "alice",
                                 facts["alice_record"]["public_key_hex"]
                                 if "public_key_hex" in facts["alice_record"]
                                 else app.keys.public_key_hex)
    app.crypto = M.CryptoEngine(app.keys)
    app.api = Api(app.keys)
    app.worker = M.SyncWorker(app)
    sent = app.send_text(facts["bob"]["address"], "Back on a new phone")
    wait_for(lambda: relay)
    for msg in list(relay):
        bob_app.worker._ingest_message(msg)
    relay.clear()
    got = bob_app.store.messages_for(alice)
    check("the restored identity writes: Bob reads it",
          sent and got and got[-1]["body"] == "Back on a new phone")
    bob_app.send_text(alice, "Welcome back")
    wait_for(lambda: relay)
    for msg in list(relay):
        app.worker._ingest_message(msg)
    check("...and reads what Bob writes back",
          app.store.messages_for(facts["bob"]["address"])[-1]["body"] ==
          "Welcome back")


# =============================================================================
#  Restored beside another identity; other passphrases and files
# =============================================================================
def restore_beside_another(home, facts_path, backups):
    facts = json.load(open(facts_path))
    M = load(CLIENT, home)
    keys = M.IdentityManager()
    priv, *_ = M.mine_identity()
    carol = keys.add_identity(priv, "Carol's")
    keys.activate(carol)
    store = M.LocalStore(keys.store_path())
    store.upsert_contact(facts["bob"]["address"], "bob", facts["bob"]["pub"])
    store.add_in(facts["bob"]["address"], "Carol's own chat", M.now_iso(),
                 9, uuid.uuid4().hex)
    store.close()
    before = dump(str(M.IdentityManager.store_path_for(carol)))
    summary = M.restore_backup(keys, os.path.join(
        backups, "full" + M.BACKUP_SUFFIX), PASS)
    check("restored beside another identity: both of the backup's added",
          sorted(summary["added"]) == ["Personal", "Work"])
    check("...the one in use stays in use, its chats untouched",
          keys.address == carol and before == dump(str(
              M.IdentityManager.store_path_for(carol))))
    decomposed = unicodedata.normalize("NFD", "café au lait 1982")
    other = M.IdentityManager()
    try:
        M.restore_backup(other, os.path.join(backups, "cafe"
                                             + M.BACKUP_SUFFIX), decomposed)
        opened = True
    except M.BackupError:
        opened = False
    check("a passphrase typed decomposed opens a file sealed composed",
          opened)


def restore_text_only(home, facts_path, backups):
    facts = json.load(open(facts_path))
    M = load(CLIENT, home)
    from kivy.base import EventLoop
    from kivy.core.window import Window
    app = M.FextApp()
    app._dismiss_splash = lambda *_a: None
    app._probe_transport = lambda: None
    app._start_worker = lambda: None
    Window.add_widget(app.build())
    summary = M.restore_backup(app.keys, os.path.join(
        backups, "text" + M.BACKUP_SUFFIX), PASS)
    app.resume_identity(None)
    rows = app.store.messages_for(facts["bob"]["address"])
    photo = [m for m in rows if m["kind"] == "image"][0]
    media = os.listdir(M.MEDIA_DIR) if M.MEDIA_DIR.exists() else []
    check("a backup without media restores every message, and no media",
          summary["messages"] == sum(i["messages"] for i in
                                     facts["made"]["identities"]) and
          len(rows) == 4 and not media, (summary["messages"], media))
    check("...a photo left behind is no longer pointed at",
          not photo["media_path"])
    try:
        app.open_conversation(facts["bob"]["address"])
        for _ in range(40):
            EventLoop.idle()
            time.sleep(0.01)
        opened = len(app.conversation_screen._bubbles) == 4
    except Exception as exc:                     # pragma: no cover
        opened = exc
    check("...and its chat opens without it", opened is True, opened)


def damaged_store(home, facts_path, backups):
    """A file whose seal is sound but whose database is not."""
    facts = json.load(open(facts_path))
    M = load(CLIENT, home)
    keys = M.IdentityManager()
    record = facts["alice_record"]
    head = json.dumps({"v": 1, "made": M.now_iso(), "by": "test", "kdf": {
        "name": "scrypt", "n": 1024, "r": 8, "p": 1,
        "salt": M.b64e(os.urandom(16))}}, separators=(",", ":")).encode()
    prefix = M.BACKUP_MAGIC + len(head).to_bytes(4, "big") + head
    path = os.path.join(home, "damaged" + M.BACKUP_SUFFIX)
    junk = b"SQLite format 3\x00" + os.urandom(8192)
    with open(path, "wb") as out:
        out.write(prefix)
        sealed = M._SealedWriter(out, M._backup_key(PASS, json.loads(
            head)["kdf"]), hashlib.sha256(prefix).digest())
        with tarfile.open(fileobj=sealed, mode="w|") as tar:
            for name, data in (
                    ("identities.json", json.dumps(
                        {"identities": [record]}).encode()),
                    (f"db/store_{record['address']}.db", junk)):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        sealed.finish()
    try:
        M.restore_backup(keys, path, PASS)
        why = None
    except M.BackupError as exc:
        why = str(exc)
    check("a sealed but damaged database is found out",
          why == "This file is damaged.", why)
    again = M.IdentityManager()
    check("...and nothing is changed: no identity, no database",
          not again.has_identity and not keys.has_identity and
          not M.IdentityManager.store_path_for(record["address"]).exists())


def old_release_backup(home, out_path):
    """Run by an older main.py: a phone with a chat and a photo, saved."""
    M = load(CLIENT, home)
    from PIL import Image
    keys = M.IdentityManager()
    priv, *_ = M.mine_identity()
    me = keys.add_identity(priv, "Old phone")
    keys.activate(me)
    store = M.LocalStore(keys.store_path())
    p, *_ = M.mine_identity()
    friend = M.address_from_priv(p)
    store.upsert_contact(friend, "friend",
                         M.pub_to_compressed(M.priv_to_pub(p)).hex())
    store.add_in(friend, "From an older Ferritext", M.now_iso(), 1,
                 uuid.uuid4().hex)
    M.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    photo = M.MEDIA_DIR / f"photo_{uuid.uuid4().hex}.jpg"
    Image.new("RGB", (32, 32), (1, 2, 3)).save(photo, "JPEG")
    store.add_out(friend, "Photo", M.now_iso(), uuid.uuid4().hex,
                  kind="image", ext={"media": {"kind": "image"}},
                  media_path=str(photo), media_state="ready")
    store.close()
    M.make_backup(keys, PASS, out_path)
    json.dump({"me": me, "friend": friend}, open(out_path + ".json", "w"))
    print("  · made by", M.APP_VERSION)


def restore_old(home, made_path, version):
    facts = json.load(open(made_path + ".json"))
    M = load(CLIENT, home)
    keys = M.IdentityManager()
    summary = M.restore_backup(keys, made_path, PASS)
    store = M.LocalStore(M.IdentityManager.store_path_for(facts["me"]))
    rows = store.messages_for(facts["friend"])
    schema = store._db.execute("PRAGMA user_version").fetchone()[0]
    check(f"a backup made by {version} restores here, its chat brought up "
          f"to date (schema {M.LocalStore.SCHEMA_VERSION})",
          summary["added"] == ["Old phone"] and len(rows) == 2 and
          rows[-1]["media_path"] and os.path.isfile(rows[-1]["media_path"])
          and schema == M.LocalStore.SCHEMA_VERSION,
          (summary, len(rows), schema))


def full_disk(home, facts_path, backups):
    M = load(CLIENT, home)
    shutil.copytree(os.path.join(os.path.dirname(facts_path), ".fext"),
                    os.path.join(home, ".fext"))
    keys = M.IdentityManager()
    real = os.fsync

    def no_room(_fd):
        raise OSError(28, "No space left on device")
    os.fsync = no_room
    target = os.path.join(home, "out" + M.BACKUP_SUFFIX)
    try:
        M.make_backup(keys, PASS, target)
        why = None
    except M.BackupError as exc:
        why = str(exc)
    finally:
        os.fsync = real
    check("a full disk while saving is said so, and leaves no part-file",
          why and "could not be written" in why and
          not os.listdir(home) == [] and
          not [n for n in os.listdir(home) if n.endswith(".part")] and
          not os.path.exists(target), why)


def large(home, out_dir):
    M = load(CLIENT, home)
    keys = M.IdentityManager()
    priv, *_ = M.mine_identity()
    me = keys.add_identity(priv, "Big")
    keys.activate(me)
    store = M.LocalStore(keys.store_path())
    p, *_ = M.mine_identity()
    friend = M.address_from_priv(p)
    store.upsert_contact(friend, "friend",
                         M.pub_to_compressed(M.priv_to_pub(p)).hex())
    now = M.now_iso()
    with store._lock:
        store._db.executemany(
            "INSERT INTO messages (peer, direction, body, created_at, "
            "client_msg_id, status, read) VALUES (?, ?, ?, ?, ?, 3, 1)",
            [(friend, "in" if i % 2 else "out",
              f"message {i} " + "words " * 12, now, uuid.uuid4().hex)
             for i in range(20000)])
        store._db.commit()
    M.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(30):
        media = M.MEDIA_DIR / f"photo_{uuid.uuid4().hex}.jpg"
        media.write_bytes(os.urandom(1 << 20))
        store.add_out(friend, "Photo", now, uuid.uuid4().hex, kind="image",
                      ext={"media": {"kind": "image"}},
                      media_path=str(media), media_state="ready")
    store.close()
    target = os.path.join(out_dir, "large" + M.BACKUP_SUFFIX)
    steps = []
    t0 = time.monotonic()
    made = M.make_backup(keys, PASS, target, progress=steps.append)
    took_make = time.monotonic() - t0
    fresh_home = tempfile.mkdtemp(prefix="fext-large-new-")
    M.DATA_DIR = M.Path(fresh_home) / ".fext"
    M.IDENTITIES_PATH = M.DATA_DIR / "identities.json"
    M.MEDIA_DIR, M.PICTURES_DIR = M.DATA_DIR / "media", \
        M.DATA_DIR / "pictures"
    other = M.IdentityManager()
    back = []
    t0 = time.monotonic()
    summary = M.restore_backup(other, target, PASS, progress=back.append)
    took_restore = time.monotonic() - t0
    print(f"  · large: {made['size'] / 1e6:.1f} MB, made in "
          f"{took_make:.1f} s, restored in {took_restore:.1f} s")
    check("a large backup (20,000 messages, 30 MB of media) both ways, its "
          "progress only ever rising to the end",
          summary["messages"] == 20030 and len(os.listdir(M.MEDIA_DIR)) == 30
          and steps == sorted(steps) and steps[-1] == 1.0 and
          back == sorted(back) and back[-1] == 1.0,
          (summary["messages"], len(os.listdir(M.MEDIA_DIR))))


def android_paths(home):
    """android_open_document / android_save_document with the phone's own
    pieces stood in for: the copy must not run on the UI thread."""
    M = load(CLIENT, home)
    from kivy.clock import Clock
    source = os.path.join(home, "picked" + M.BACKUP_SUFFIX)
    with open(source, "wb") as fh:
        fh.write(os.urandom(3 << 20))
    seen = {}

    class Activity:
        handler = None

        @classmethod
        def bind(cls, on_activity_result):
            cls.handler = on_activity_result

        @classmethod
        def unbind(cls, on_activity_result):
            cls.handler = None

    class Uri:
        def __init__(self, path):
            self.path = path

    class Resolver:
        fail = False

        def openFileDescriptor(self, uri, mode):
            seen["thread"] = threading.current_thread()
            time.sleep(0.3)                     # a cloud drive, fetching
            if Resolver.fail:
                raise OSError("the provider refused")
            fd = os.open(uri.path, os.O_RDONLY if mode == "r" else
                         os.O_WRONLY | os.O_CREAT | os.O_TRUNC)

            class Parcel:
                def detachFd(self):
                    return fd
            return Parcel()

    class Context:
        def getContentResolver(self):
            return Resolver()

        def startActivityForResult(self, intent, code):
            seen["asked"] = code

    class Intent:
        ACTION_OPEN_DOCUMENT = ACTION_CREATE_DOCUMENT = "x"
        CATEGORY_OPENABLE = EXTRA_TITLE = "y"

        def __init__(self, *_a):
            pass

        def addCategory(self, *_a):
            pass

        def setType(self, *_a):
            pass

        def putExtra(self, *_a):
            pass

    class Result:
        def __init__(self, path):
            self._uri = Uri(path)

        def getData(self):
            return self._uri

    fake_android = type(sys)("android")
    fake_android.activity = Activity
    sys.modules["android"] = fake_android
    M._jni = lambda: (lambda name: Intent if "Intent" in name else str,
                      None)
    M.android_context = lambda: Context()
    got = []
    M.android_open_document(got.append)
    t0 = time.monotonic()
    Activity.handler(M.REQ_OPEN_FILE, -1, Result(source))
    back_in = time.monotonic() - t0
    end = time.time() + 10
    while not got and time.time() < end:
        Clock.tick()
        time.sleep(0.02)
    copy = got[0] if got else None
    check("a backup chosen on a phone is copied off the UI thread",
          back_in < 0.2 and seen.get("thread") is not
          threading.main_thread(), f"{back_in:.2f} s on the UI thread")
    check("...into a private copy of the very same bytes",
          copy and os.path.dirname(copy) == str(M.DATA_DIR / "cache") and
          open(copy, "rb").read() == open(source, "rb").read())
    sheet = M.RestoreSheet.__new__(M.RestoreSheet)
    states = []
    sheet.path, sheet.made = copy, type("L", (), {"text": ""})()
    sheet.status = None
    sheet.set_status = lambda _label, text, *_a: states.append(text)
    Resolver.fail = True
    got.clear()
    M.android_open_document(lambda path: (got.append(path),
                                          sheet._picked(path)))
    Activity.handler(M.REQ_OPEN_FILE, -1, Result(source))
    end = time.time() + 10
    while not got and time.time() < end:
        Clock.tick()
        time.sleep(0.02)
    check("a file the phone cannot read is said so; the earlier copy goes",
          got == [None] and states[-1:] == ["That file cannot be read."] and
          not os.path.exists(copy) and
          not os.listdir(M.DATA_DIR / "cache"), states)
    Resolver.fail = False
    saved = []
    target = os.path.join(home, "saved" + M.BACKUP_SUFFIX)
    M.android_save_document(source, "x", saved.append)
    t0 = time.monotonic()
    Activity.handler(M.REQ_SAVE_FILE, -1, Result(target))
    back_in = time.monotonic() - t0
    end = time.time() + 10
    while not saved and time.time() < end:
        Clock.tick()
        time.sleep(0.02)
    check("a backup saved on a phone is copied off the UI thread too",
          back_in < 0.2 and saved == [True] and
          open(target, "rb").read() == open(source, "rb").read())
    saved.clear()
    M.android_save_document(source, "x", saved.append)
    Activity.handler(M.REQ_SAVE_FILE, 0, None)          # cancelled
    end = time.time() + 5
    while not saved and time.time() < end:
        Clock.tick()
        time.sleep(0.02)
    check("...and a save cancelled is said to be not saved", saved == [False])


# =============================================================================
#  17.0: automatic backups (F10), made with no one there
# =============================================================================
def fake_keystore(M, gone=False):
    """This device's keystore, as the system keeps it (the tests have
    none): what it seals opens only through it."""
    secret = b"system's own key"

    def seal(cls, data):
        return "kept:" + M.b64e(bytes(b ^ secret[i % len(secret)]
                                      for i, b in enumerate(data)))

    def open_(cls, sealed):
        if gone or not sealed.startswith("kept:"):
            raise M.BackupError("the keystore has no such key")
        raw = M.b64d(sealed[5:])
        return bytes(b ^ secret[i % len(secret)] for i, b in enumerate(raw))
    M.DeviceKeystore.seal = classmethod(seal)
    M.DeviceKeystore.open = classmethod(open_)
    M.DeviceKeystore.available = classmethod(lambda cls: True)


def auto_backups(home, folder):
    M = load(CLIENT, home)
    fake_keystore(M)
    keys = M.IdentityManager()
    os.makedirs(folder, exist_ok=True)
    check("off until turned on", not M.AutoBackup.on() and
          not M.AutoBackup.due())
    M.AutoBackup.turn_on(PASS, folder, media=True)
    kept = json.dumps(M.load_prefs()["auto_backup"])
    check("turned on: the key is kept by the keystore, never the "
          "passphrase", M.AutoBackup.on() and PASS not in kept and
          M.load_prefs().get("backup_every") == 7 and M.AutoBackup.due())
    made = []
    for _ in range(4):
        made.append(M.AutoBackup.run(keys))
        time.sleep(1.05)                   # each its own name
    names = sorted(n for n in os.listdir(folder)
                   if n.startswith(M.AUTO_BACKUP_PREFIX))
    check(f"four made with no one there; the last "
          f"{M.AUTO_BACKUPS_KEPT} kept, the oldest gone",
          len(names) == M.AUTO_BACKUPS_KEPT and
          os.path.basename(made[0]["path"]) not in names and
          os.path.basename(made[-1]["path"]) in names, names)
    check("...no copy left behind in the cache",
          not [n for n in os.listdir(M.DATA_DIR / "cache")
               if n.endswith(M.BACKUP_SUFFIX)])
    check("...and none due until a week has passed", not M.AutoBackup.due()
          and M.AutoBackup.due(time.time() + 8 * 86400))
    check("reminders stop while backups are made by themselves",
          M.backup_due(time.time() + 40 * 86400) is None)
    fake_keystore(M, gone=True)
    try:
        M.AutoBackup.run(keys)
        refused = None
    except M.BackupError as exc:
        refused = str(exc)
    check("a keystore that lost the key: nothing made, and it says so",
          refused is not None and len(os.listdir(folder)) ==
          M.AUTO_BACKUPS_KEPT, refused)
    print(f"  · newest: {names[-1]}")


def auto_restore(home, facts_path, folder):
    facts = json.load(open(facts_path))
    M = load(CLIENT, home)
    newest = sorted(n for n in os.listdir(folder)
                    if n.startswith(M.AUTO_BACKUP_PREFIX))[-1]
    keys = M.IdentityManager()
    try:
        M.restore_backup(keys, os.path.join(folder, newest),
                         "not the passphrase")
        wrong = None
    except M.BackupError as exc:
        wrong = str(exc)
    check("on a new device, an automatic backup does not open without the "
          "passphrase", wrong is not None)
    summary = M.restore_backup(keys, os.path.join(folder, newest), PASS)
    same = {a: dump(str(M.IdentityManager.store_path_for(a)))
            for a in (facts["alice"], facts["work"])}
    check("...with it, every table of every identity comes back as it was",
          sorted(summary["added"]) == sorted(
              [facts["alice_record"]["label"], "Work"]) and
          same == facts["expect"], summary)
    check("...the photo and voice note with them",
          (M.MEDIA_DIR / facts["photo"]).is_file() and
          (M.MEDIA_DIR / facts["voice"]).is_file())


def recordings(home, folder):
    """17.2 (F18): call recordings in a backup only when Settings says so."""
    M = load(CLIENT, home)
    fake_keystore(M)
    keys = M.IdentityManager()
    os.makedirs(folder, exist_ok=True)
    M.recordings_dir().mkdir(parents=True, exist_ok=True)
    (M.recordings_dir() / RECORDING).write_bytes(M.RECORDING_MAGIC +
                                                 os.urandom(4096))
    plain = M.make_backup(keys, PASS, os.path.join(folder, "plain" +
                                                   M.BACKUP_SUFFIX))
    check("a backup leaves the call recordings out, unless Settings says "
          "otherwise", not M.backup_recordings_wanted() and
          plain["recordings"] == 0, plain)
    M.save_pref("backup_recordings", True)
    M.AutoBackup.turn_on(PASS, folder, media=False)
    made = M.AutoBackup.run(keys)
    check("...turned on (Settings › Calls › Recordings in backups), an "
          "automatic backup holds them", made["recordings"] == 1, made)


def recordings_restore(home, folder):
    M = load(CLIENT, home)
    newest = sorted(n for n in os.listdir(folder)
                    if n.startswith(M.AUTO_BACKUP_PREFIX))[-1]
    M.restore_backup(M.IdentityManager(), os.path.join(folder, newest), PASS)
    check("...restored on a new device, they are its recordings again",
          [p.name for p in M.kept_recordings()] == [RECORDING],
          M.kept_recordings())


RECORDING = "call-2026-11-06-120000.fextrec"


# =============================================================================
#  The run
# =============================================================================
def main():
    root = tempfile.mkdtemp(prefix="fext-backup-test-")

    def device(name):
        path = os.path.join(root, name)
        os.makedirs(path)
        return path

    phone = device("phone")
    facts = os.path.join(phone, "facts.json")
    ok = []
    print("\nThe phone that is backed up")
    got, run = phase("make", phone, facts)
    ok += got
    if not os.path.exists(facts):
        print(run.stdout[-4000:], run.stderr[-4000:])
        sys.exit(1)
    backups = os.path.join(phone, "backups")
    print("\nA new phone, restored through the screens")
    ok += phase("new", device("new"), facts, backups)[0]
    print("\nBeside another identity; passphrases")
    ok += phase("beside", device("beside"), facts, backups)[0]
    print("\nWithout media")
    ok += phase("text", device("text"), facts, backups)[0]
    print("\nA damaged database inside a sound seal")
    ok += phase("damaged", device("damaged"), facts, backups)[0]
    print("\nBackups made by older releases")
    for tag in ("v11.0.0", "v13.0.0", "v15.1.1"):
        old = os.path.join(root, f"old_{tag}.py")
        try:
            source = subprocess.run(["git", "show", f"{tag}:main.py"],
                                    capture_output=True, check=True,
                                    cwd=os.path.dirname(CLIENT)).stdout
        except (subprocess.CalledProcessError, OSError):
            print(f"  · {tag}: not in this checkout")
            continue
        open(old, "wb").write(source)
        made = os.path.join(root, f"made_{tag}" + ".fextbackup")
        phase("old-make", device(f"old_{tag}"), made, client=old)
        ok += phase("old-restore", device(f"old_{tag}_new"), made, tag)[0]
    print("\nAutomatic backups (17.0, F10)")
    auto = os.path.join(root, "auto")
    ok += phase("auto", phone, auto)[0]
    ok += phase("auto-restore", device("auto_new"), facts, auto)[0]
    print("\nCall recordings (17.2, F18)")
    rec = os.path.join(root, "rec")
    ok += phase("recordings", phone, rec)[0]
    ok += phase("recordings-restore", device("rec_new"), rec)[0]
    print("\nA full disk")
    ok += phase("disk", device("disk"), facts, backups)[0]
    print("\nA large backup")
    ok += phase("large", device("large"), root)[0]
    print("\nOn Android")
    ok += phase("android", device("android"))[0]
    passed = sum(ok)
    print(f"\n  {passed}/{len(ok)} passed")
    shutil.rmtree(root, ignore_errors=True)
    sys.exit(0 if ok and passed == len(ok) else 1)


if PHASE == "main":
    main()
else:
    HOME = sys.argv[3]
    try:
        if PHASE == "make":
            make_phone(HOME, sys.argv[4])
        elif PHASE == "new":
            restore_on_new_phone(HOME, sys.argv[4], sys.argv[5])
        elif PHASE == "beside":
            restore_beside_another(HOME, sys.argv[4], sys.argv[5])
        elif PHASE == "text":
            restore_text_only(HOME, sys.argv[4], sys.argv[5])
        elif PHASE == "damaged":
            damaged_store(HOME, sys.argv[4], sys.argv[5])
        elif PHASE == "old-make":
            old_release_backup(HOME, sys.argv[4])
        elif PHASE == "old-restore":
            restore_old(HOME, sys.argv[4], sys.argv[5])
        elif PHASE == "disk":
            full_disk(HOME, sys.argv[4], sys.argv[5])
        elif PHASE == "large":
            large(HOME, sys.argv[4])
        elif PHASE == "android":
            android_paths(HOME)
        elif PHASE == "auto":
            auto_backups(HOME, sys.argv[4])
        elif PHASE == "auto-restore":
            auto_restore(HOME, sys.argv[4], sys.argv[5])
        elif PHASE == "recordings":
            recordings(HOME, sys.argv[4])
        elif PHASE == "recordings-restore":
            recordings_restore(HOME, sys.argv[4])
    except Exception as exc:                     # the phase's own failure
        import traceback
        traceback.print_exc()
        check(f"phase '{PHASE}' ran to its end", False, repr(exc))
    os._exit(0 if RESULTS and all(ok for _n, ok in RESULTS) else 1)
