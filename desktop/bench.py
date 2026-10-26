#!/usr/bin/env python3
"""Timings of the app's own work, to measure a change before and after.

    xvfb-run -a python3 desktop/bench.py main.py

Prints one JSON object of milliseconds, each the median of five runs:

  * text: drawing a message-sized label in the app's fonts (English,
    Chinese, emoji), and the first label, which opens the font;
  * store: on a phone holding 50,000 messages, a chat's newest 60 (what
    opening a chat reads), the chat list, a search through every message,
    and the outbox (14.0);
  * crypto: sealing a message for one person and opening it, as before
    and (15.0) post-quantum; each envelope's size in bytes, and a first
    one's, which carries our key too; and making a post-quantum key;
  * memory_mb: the most memory the process used, in MB.

To compare a change, time both versions, as desktop/capture.py describes:

    git worktree add ../before HEAD~1
    xvfb-run -a python3 desktop/bench.py ../before/main.py
    xvfb-run -a python3 desktop/bench.py main.py
    git worktree remove ../before

Timings differ between machines, and between runs by about a tenth: compare
runs on the same machine, and repeat any that surprises you.
"""
import importlib.util
import json
import logging
import os
import resource
import statistics
import sys
import tempfile
import time
import uuid

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-bench-")
os.environ["FEXT_SERVER_HOST"] = "127.0.0.1:1"

# Only the numbers: the app's log and Kivy's start-up notes stay quiet
# (xvfb-run mixes them into the output).
logging.disable(logging.CRITICAL)
_spec = importlib.util.spec_from_file_location("fext_bench", sys.argv[1])
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_bench"] = M
_spec.loader.exec_module(M)
importlib.import_module("kivy.core.window")    # SDL must be up to draw text
from kivy.core.text import Label as CoreLabel          # noqa: E402

RUNS = 5
TEXTS = {"english": "Did you see the trail map I sent? The ridge path "
                    "closes at dusk, so we should start by noon.",
         "chinese": "我们这次走西山那条路线，大概需要四个小时。记得带水和外套。",
         "emoji": "Happy birthday!! 🎂🎈🥳 Photos from the trip: 🇬🇧 🇫🇷 🍝"}


def median_ms(job, repeat=1):
    """The median of RUNS timings of `job`, run `repeat` times each, per
    run of it."""
    times = []
    for _ in range(RUNS):
        start = time.perf_counter()
        for _ in range(repeat):
            job()
        times.append((time.perf_counter() - start) / repeat * 1000)
    return round(statistics.median(times), 3)


def draw(text):
    label = CoreLabel(text=text, font_name="FerritextSerif", font_size=34,
                      text_size=(560, None))
    label.refresh()


M.register_fonts()
start = time.perf_counter()
draw("Hello")
result = {"text": {"first_label": round((time.perf_counter() - start) * 1000,
                                         3)}}
for name, text in TEXTS.items():
    result["text"][name] = median_ms(lambda: draw(text), repeat=60)

# A phone with 50,000 messages: 49 chats of 1,000, one of 1,000 more.
keys = M.IdentityManager()
priv, *_ = M.mine_identity()
me = keys.add_identity(priv, "Personal")
keys.activate(me)
store = M.LocalStore(keys.store_path())
peers = []
for i in range(50):
    pk, *_ = M.mine_identity()
    peers.append(M.address_from_priv(pk))
    store.upsert_contact(peers[-1], f"person{i}",
                         M.pub_to_compressed(M.priv_to_pub(pk)).hex())
rows = [(uuid.uuid4().hex, peers[i % 50], "in" if i % 3 else "out",
         f"Note {i} about the allotment: watering, and the bean poles.",
         3, 1, M.now_iso(), i + 1) for i in range(50_000)]
with store._lock:
    store._db.executemany(
        "INSERT INTO messages (client_msg_id, peer, direction, body, status, "
        "read, created_at, server_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    store._db.commit()
result["store"] = {
    "open_chat": median_ms(lambda: store.messages_for(peers[0], limit=60)),
    "chat_list": median_ms(lambda: store.conversations(me)),
    # A word no message holds: the whole history is searched.
    "search": median_ms(lambda: store.search_messages("courgette")),
}
if hasattr(store, "outbox"):
    result["store"]["outbox"] = median_ms(
        lambda: store.outbox(time.time() - 86400))

# One message, sealed for one person and opened by them.
other = M.IdentityManager()
them = other.add_identity(M.mine_identity()[0], "Them")
other.activate(them)
sender, receiver = M.CryptoEngine(keys), M.CryptoEngine(other)


def seal(x=None, **pq):
    """`pq`: {"pq": (their key, its id)}, from 15.0."""
    return sender.build_envelope(TEXTS["english"], other.public_key_hex,
                                 x=dict(x or {}, k="text"), recipient=them,
                                 cid="c" * 32, **pq)[0]


ways = {"": {}}
if hasattr(M, "mlkem_keygen"):
    ways["_pq"] = {"pq": (other.pq_public, other.pq_kid)}
result["crypto"] = {}
for suffix, pq in ways.items():
    envelope = seal(**pq)
    result["crypto"].update({
        "seal" + suffix: median_ms(lambda: seal(**pq)),
        "open" + suffix: median_ms(lambda: receiver.open_envelope(
            envelope, me, cid="c" * 32)),
        "envelope_bytes" + suffix: len(json.dumps(envelope))})
if hasattr(M, "mlkem_keygen"):
    # Until they have shown they hold it, a message carries our key too.
    first = seal({"pq": keys.pq_kid, "pqe": M.b64e(keys.pq_public)},
                 **ways["_pq"])
    result["crypto"]["envelope_bytes_pq_first"] = len(json.dumps(first))
    result["crypto"]["pq_keygen"] = median_ms(
        lambda: M.mlkem_keygen(os.urandom(64)))
# Linux counts in KB.
result["memory_mb"] = round(
    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
print(json.dumps(result, indent=1))
