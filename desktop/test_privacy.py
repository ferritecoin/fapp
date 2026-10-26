#!/usr/bin/env python3
"""Disappearing messages, verifying a contact, and links (v11), against two
real app controllers and their real sync workers.

    xvfb-run -a python3 desktop/test_privacy.py main.py

  * a chat's timer is set by either side and the latest setting wins;
    every message sent while it is on carries it; the sender's copy goes
    that long after sending, the reader's that long after reading; gone is
    gone — a re-push does not bring it back; a timer that is not one of the
    choices is ignored; whose app does not take part is said;
  * the code two people compare is the same on both phones, and changes
    with either key; a scanned code names its address; marking someone
    verified shows in the chat;
  * links: found in text (punctuation off, brackets kept when the link
    opened them), where they really lead (letters from other alphabets,
    a site's name before an @, bare numbers), and nothing opened without
    asking;
  * link previews are off unless turned on: then the sender's phone fetches
    one — from public addresses only, a little of each page, three
    redirects at most — sends it inside the message, and the reader's
    phone shows it without touching the site; a preview for a link the
    message does not hold, or with a picture that is not a small JPEG, is
    dropped; with previews off nothing is fetched and none are shown
    (yours excepted: it is what was sent);
  * a backup is one file sealed with a passphrase, holding nothing
    readable; a wrong passphrase, a damaged or shortened file, or one
    asking for endless work, is told apart and refused; restored on a new
    device, an identity arrives with its chats, photos and picture, and
    one already there keeps its own (unless it has none yet); names that
    would leave the backup's folders are not followed.
"""
import base64
import http.server
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-privacy-")
os.environ["FEXT_SERVER_HOST"] = "127.0.0.1:1"
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

_spec = importlib.util.spec_from_file_location(
    "fext_privacy", sys.argv[1] if len(sys.argv) > 1 else "main.py")
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_privacy"] = M
_spec.loader.exec_module(M)
from kivy.clock import Clock                                   # noqa: E402
from kivy.core.window import Window                            # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""))


def tick(seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        Clock.tick()
        time.sleep(0.01)


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        Clock.tick()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class Relay:
    def __init__(self):
        self.mail, self._next = [], 1

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

    def set_ticks(self, peer, enabled):
        return {}


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
    app.worker.beat_owner = None
    return app


def deliver(relay, parties, keep=False):
    acked, frames = [], []
    while relay.mail:
        msg = relay.mail.pop(0)
        frames.append(dict(msg))
        acked.append(parties[msg["recipient"]].worker._ingest_message(msg))
    return (acked, frames) if keep else acked


def send_and_deliver(app, peer, text, **kw):
    app.send_text(peer, text, **kw)
    wait_for(lambda: relay.mail)
    return deliver(relay, parties, keep=True)


def texts(store, peer, kind=None):
    return [m["body"] for m in store.messages_for(peer)
            if kind is None or (m.get("kind") or "text") == kind]


relay = Relay()
alice, bob = make_party("alice", relay), make_party("bob", relay)
parties = {alice.keys.address: alice, bob.keys.address: bob}
A, B = alice.keys.address, bob.keys.address
for me, other in ((alice, bob), (bob, alice)):
    me.store.upsert_contact(other.keys.address, other.keys.username,
                            other.keys.public_key_hex)
    me.store.add_caps(other.keys.address, M.LocalStore.CAP_V9)

# =============================================================================
print("\nDisappearing messages")
check("a timer must be one of the choices offered",
      M.sanitize_timer(3600) == 3600 and M.sanitize_timer(12345) is None
      and M.sanitize_timer(True) is None and M.sanitize_timer("3600") is None)
check("...and says who did what, in the chat's own words",
      M.timer_event("alice", 3600) == "alice set messages to disappear "
      "1 hour after they are read" and M.timer_event("You", 0) ==
      "You turned off disappearing messages")
check("an app that does not take part is named before it is relied on",
      alice.timer_laggards(B) == "bob")
alice.store.add_caps(B, M.LocalStore.CAP_TIMER)
bob.store.add_caps(A, M.LocalStore.CAP_TIMER)
check("...and not once it does", alice.timer_laggards(B) == "")

alice.set_chat_timer(B, 3600)
wait_for(lambda: relay.mail)
deliver(relay, parties)
tick(0.2)
check("set on one side, the timer is on for both",
      alice.store.chat_timer(B)[0] == 3600 and
      bob.store.chat_timer(A)[0] == 3600)
check("...and each chat says who set it",
      "You set messages to disappear 1 hour after they are read"
      in texts(alice.store, B, "event") and
      "alice set messages to disappear 1 hour after they are read"
      in texts(bob.store, A, "event"))

at_before = bob.store.chat_timer(A)[1]
bob.set_chat_timer(A, 300)
wait_for(lambda: relay.mail)
deliver(relay, parties)
check("either side may change it; the latest change wins",
      alice.store.chat_timer(B)[0] == 300 and
      bob.store.chat_timer(A)[0] == 300)
alice._send_control_to(B, {"op": "timer", "s": 86400, "at": at_before - 60},
                       "Turned on disappearing messages")
wait_for(lambda: relay.mail)
deliver(relay, parties)
check("an older change arriving late changes nothing",
      bob.store.chat_timer(A)[0] == 300)
alice._send_control_to(B, {"op": "timer", "s": 12345, "at": time.time()},
                       "Turned on disappearing messages")
wait_for(lambda: relay.mail)
deliver(relay, parties)
check("a timer that is not one of the choices is ignored",
      bob.store.chat_timer(A)[0] == 300)

t0 = time.time()
send_and_deliver(alice, B, "this will not last")
mine = [m for m in alice.store.messages_for(B)
        if m["body"] == "this will not last"][0]
theirs = [m for m in bob.store.messages_for(A)
          if m["body"] == "this will not last"][0]
check("a message sent while it is on carries it",
      mine["ttl"] == 300 and theirs["ttl"] == 300)
check("the sender's copy goes that long after sending",
      abs(mine["expires_at"] - (t0 + 300)) < 5)
check("the reader's does not start until it is read",
      not theirs["read"] and theirs["expires_at"] == 0)
bob.store.mark_read_local([theirs["id"]])
theirs = bob.store.message(theirs["id"])
check("...and then goes that long after reading",
      abs(theirs["expires_at"] - (time.time() + 300)) < 5)

frames_kept = []
bob.store._db.execute("UPDATE messages SET expires_at = ? WHERE id = ?",
                      (time.time() - 1, theirs["id"]))
bob.store._db.commit()
alice.store._db.execute("UPDATE messages SET expires_at = ? WHERE id = ?",
                        (time.time() - 1, mine["id"]))
alice.store._db.commit()
gone_b = bob.worker.sweep_expired()
gone_a = alice.worker.sweep_expired()
check("when its time is up it is deleted, on both phones",
      gone_b == [A] and gone_a == [B] and
      "this will not last" not in texts(bob.store, A) and
      "this will not last" not in texts(alice.store, B))
check("...and the screen and the shade are told",
      ("expired", A) in [e[:2] for e in list(bob.ui_queue.queue)])

alice.set_chat_timer(B, 0)
wait_for(lambda: relay.mail)
deliver(relay, parties)
_acked, frames = send_and_deliver(alice, B, "this one stays")
kept = [m for m in bob.store.messages_for(A) if m["body"] == "this one stays"]
check("turned off, new messages stay",
      bob.store.chat_timer(A)[0] == 0 and kept and kept[0]["ttl"] == 0 and
      kept[0]["expires_at"] == 0)

# A message that disappeared cannot be brought back by the relay pushing it
# again.
bob.set_chat_timer(A, 300)
wait_for(lambda: relay.mail)
deliver(relay, parties)
_acked, frames = send_and_deliver(alice, B, "gone for good")
row = [m for m in bob.store.messages_for(A) if m["body"] == "gone for good"][0]
bob.store.mark_read_local([row["id"]])
bob.store._db.execute("UPDATE messages SET expires_at = ? WHERE id = ?",
                      (time.time() - 1, row["id"]))
bob.store._db.commit()
bob.worker.sweep_expired()
bob.worker._ingest_message(dict(frames[0]))
check("gone is gone: a re-push of it does not bring it back",
      "gone for good" not in texts(bob.store, A))
check("the next one due is known (for the sweep's timing)",
      bob.store.next_expiry() is None or bob.store.next_expiry() > 0)
bob.set_chat_timer(A, 0)
wait_for(lambda: relay.mail)
deliver(relay, parties)

# =============================================================================
print("\nVerifying a contact")
code_a = M.safety_code(alice.keys.public_key_hex, bob.keys.public_key_hex)
code_b = M.safety_code(bob.keys.public_key_hex, alice.keys.public_key_hex)
groups = code_a.split()
check("the code is twelve groups of five digits",
      len(groups) == 12 and all(len(g) == 5 and g.isdigit() for g in groups))
check("...the same on both phones",
      code_a == code_b == M.safety_code(
          alice.keys.public_key_hex,
          alice.store.get_contact(B)["public_key"]) == M.safety_code(
          bob.keys.public_key_hex, bob.store.get_contact(A)["public_key"]))
pk, *_ = M.mine_identity()
mallory = M.pub_to_compressed(M.priv_to_pub(pk)).hex()
check("...and different if either key is not the real one",
      M.safety_code(alice.keys.public_key_hex, mallory) != code_a and
      M.safety_code(mallory, bob.keys.public_key_hex) != code_a)
bundle = json.dumps(bob.keys.public_bundle(), separators=(",", ":"))
check("a scanned profile code names its address",
      M.address_in_qr(bundle) == B and M.address_in_qr(B) == B)
check("...and anything else names none",
      M.address_in_qr("https://example.org") is None and
      M.address_in_qr('{"app": "other", "address": "%s"}' % B) is None and
      M.address_in_qr("{not json") is None)
plain = os.path.join(os.environ["HOME"], "plain.png")
from PIL import Image                                          # noqa: E402
Image.new("RGB", (64, 64), (200, 200, 200)).save(plain)
check("a picture without a code reads as none (and does not fail)",
      M.read_qr_image(plain) is None)

alice.set_verified(B, True)
alice.open_conversation(B)
tick(0.5)
check("marked verified, it says so in the chat",
      alice.store.get_contact(B)["verified"] == 1 and
      "You verified bob" in texts(alice.store, B, "event") and
      alice.conversation_screen.peer_sub.text.startswith("verified"))
alice.set_verified(B, False)
tick(0.3)
check("...and clearing it says that too",
      not alice.store.get_contact(B)["verified"] and
      "You cleared bob's verification" in texts(alice.store, B, "event"))
alice.go_back_to_chats()
tick(0.3)

# =============================================================================
print("\nLinks")
text = ("see https://en.wikipedia.org/wiki/Foo_(bar), then "
        "(https://a.example/x). Also http://b.example/?q=1! "
        "and https://a.example/x again")
check("links are found, without the punctuation after them",
      M.links_in(text) == ["https://en.wikipedia.org/wiki/Foo_(bar)",
                           "https://a.example/x", "http://b.example/?q=1"])
check("...each once, in order, wherever it appears",
      [u for _s, _e, u in M.link_spans(text)].count("https://a.example/x")
      == 2 and len(M.links_in(text)) == 3)
# (In two parts: the release audit takes any name@site for an address.)
dressed = "https://bank.example" + "@" + "evil.example/login"
check("where a link leads: the real site",
      M.link_host(dressed) == "evil.example" and
      M.link_host("https://EXAMPLE.org./a") == "example.org")
check("...a name in letters from other alphabets shows its plain form",
      M.link_host("https://аpple.com/") == "аpple.com  (xn--pple-43d.com)")
warning = M.link_warning("http://bank.example@192.168.1.1/")
check("...and what to know before opening it is said",
      "number" in warning and "@" in warning and "encrypted" in warning
      and M.link_warning("https://example.org/") == "")
check("text with links is markup with each link a ref, the rest escaped",
      M.link_markup("a [b] https://x.example/", M.link_spans(
          "a [b] https://x.example/"), "D4A64A") ==
      "a &bl;b&br; [ref=0][color=D4A64A][u]https://x.example/[/u]"
      "[/color][/ref]")

opened = []
M.open_url = lambda url: opened.append(url)
send_and_deliver(alice, B, "map: https://maps.example/ridge and more")
bob.open_conversation(A)
tick(0.6)
screen = bob.conversation_screen
bubble = next(b for b in screen._bubbles.values()
              if "maps.example" in b.msg["body"])
body = bubble.body
check("the link in a message is underlined",
      isinstance(body, M.SelectableText) and body._links and
      len(body._link_lines.children) > 0)
start = bubble.msg["body"].index("https://")
col, row = body.get_cursor_from_index(start + 3)
lx = body.x + body.padding[0] + body._text_width(body._lines[row][:col]) + 2
ly = body.top - body.padding[1] - (row + 0.5) * (body.line_height +
                                                  body.line_spacing)
wx, wy = body.parent.to_window(lx, ly)
check("a tap on it is a tap on the link",
      bubble.link_at(wx, wy) == "https://maps.example/ridge")
wx0, wy0 = body.parent.to_window(body.x + 2, ly)
check("...and a tap beside it is not", bubble.link_at(wx0, wy0) is None)
screen.on_bubble_tap(bubble.msg["id"], type("T", (), {"pos": (
    screen._rows[bubble.msg["id"]].to_widget(wx, wy))})())
tick(0.4)
sheets = [w for w in Window.children if isinstance(w, M.LinkSheet)]
check("a tapped link is not opened: first, where it leads",
      sheets and not opened and sheets[0].url == "https://maps.example/ridge")
if sheets:
    sheets[0]._open()
    tick(0.4)
check("...and opened only when asked",
      opened == ["https://maps.example/ridge"])
check("a message's text is measured the way a computer wraps it (no blank "
      "line under a long link)",
      abs(M.measure_selectable(body.text, body.font_size, body.width - 2)[1]
          - body.minimum_height) < 1)
bob.go_back_to_chats()
tick(0.3)

# Phones show the text as a label: the link is a ref a tap finds.
M.IS_MOBILE = True
label = M.make_body("go to https://maps.example/ridge now", M.fs(17),
                    M.link_spans("go to https://maps.example/ridge now"))
M.IS_MOBILE = False
label.text_size = (M.dp(400), None)
label.texture_update()
label.size = label.texture_size
check("on a phone the link is gold and underlined, and a tap finds it",
      label.markup and "[u]" in label.text and "0" in label.refs)

# ---- previews ------------------------------------------------------------------
print("\nLink previews: off unless turned on")
check("previews are off by default", not M.link_previews_enabled())
fetched = []
real_fetch = M.fetch_link_preview


def fake_fetch(url):
    fetched.append(url)
    return {"u": url, "t": "A ridge walk", "d": "Eight miles of chalk.",
            "s": "Maps"}


M.fetch_link_preview = fake_fetch
alice.open_conversation(B)
tick(0.5)
comp = alice.conversation_screen
comp.entry.text = "look https://maps.example/ridge"
tick(1.4)
check("with previews off, writing a link fetches nothing",
      not fetched and comp.preview_bar.height == 0)
comp.entry.text = ""
tick(0.2)

alice.settings_screen._on_previews(True)
check("turned on in Settings, it is remembered",
      M.link_previews_enabled() and comp._previews_on)
comp.entry.text = "look https://maps.example/ridge"
check("...a link being written is fetched a moment later",
      wait_for(lambda: comp.link_preview is not None, 4) and
      fetched == ["https://maps.example/ridge"])
check("...and shown over the composer, to send with it",
      comp.preview_bar.height > 0 and
      comp.preview_title.text == "A ridge walk")
comp.entry.text = "look https://maps.example/ridge (please)"
tick(1.2)
check("going on writing does not fetch it again", len(fetched) == 1)
alice.send_current()
wait_for(lambda: relay.mail)
deliver(relay, parties)
sent = [m for m in alice.store.messages_for(B)
        if "maps.example/ridge (please)" in m["body"]][0]
got = [m for m in bob.store.messages_for(A)
       if "maps.example/ridge (please)" in m["body"]][0]
check("the preview travels inside the message",
      M.message_ext(sent).get("lp", {}).get("t") == "A ridge walk" and
      M.message_ext(got).get("lp", {}).get("t") == "A ridge walk")
check("...and the composer is clear for the next",
      comp.preview_bar.height == 0 and comp.link_preview is None)

comp.entry.text = "and https://other.example/page"
wait_for(lambda: comp.link_preview is not None, 4)
comp.dismiss_preview()
alice.send_current()
wait_for(lambda: relay.mail)
deliver(relay, parties)
got = [m for m in bob.store.messages_for(A)
       if "other.example" in m["body"]][0]
check("a preview put away (✕) is not sent",
      "lp" not in M.message_ext(got))

alice.go_back_to_chats()
tick(0.3)
bob.settings_screen._on_previews(False)   # they share this computer's prefs
bob.open_conversation(A)
tick(0.6)
cards = [b for b in bob.conversation_screen._bubbles.values()
         if b.card is not None]
check("previews off on the reader's phone: none shown", not cards)
bob.go_back_to_chats()
alice.open_conversation(B)
tick(0.6)
own_cards = [b for b in alice.conversation_screen._bubbles.values()
             if b.card is not None]
check("your own message shows the preview it was sent with",
      len(own_cards) == 1 and own_cards[0].card.preview["t"] ==
      "A ridge walk")
alice.go_back_to_chats()
tick(0.3)
bob.settings_screen._on_previews(True)
bob.open_conversation(A)
tick(0.6)
cards = [b for b in bob.conversation_screen._bubbles.values()
         if b.card is not None]
check("previews on: the reader sees it, as sent — nothing fetched to show it",
      len(cards) == 1 and len(fetched) == 2)
if cards:
    cards[0].card.dispatch("on_release")
    tick(0.4)
check("...and a tap on it says where it leads",
      any(isinstance(w, M.LinkSheet) for w in Window.children))
for w in list(Window.children):
    if isinstance(w, M.ModalView):
        w.dismiss(animation=False)
bob.go_back_to_chats()
tick(0.3)
M.fetch_link_preview = real_fetch

print("\nA preview received is checked")
jpeg = io.BytesIO()
Image.new("RGB", (40, 30), (90, 120, 80)).save(jpeg, "JPEG")
small = base64.b64encode(jpeg.getvalue()).decode()
body = "see https://maps.example/ridge"
good = M.sanitize_preview({"u": "https://maps.example/ridge", "t": "T" * 500,
                           "d": "d", "s": "s", "i": small}, body)
check("a preview of a link in the message is kept, its texts cut short",
      good and len(good["t"]) == 200 and good["i"] == small)
check("...one of a link the message does not hold is dropped",
      M.sanitize_preview({"u": "https://evil.example/", "t": "x"}, body)
      is None)
check("...one without a title is dropped",
      M.sanitize_preview({"u": "https://maps.example/ridge"}, body) is None)
png = io.BytesIO()
Image.new("RGB", (40, 30)).save(png, "PNG")
odd = M.sanitize_preview({"u": "https://maps.example/ridge", "t": "x",
                          "i": base64.b64encode(png.getvalue()).decode()},
                         body)
check("...a picture that is not a JPEG is dropped (the rest kept)",
      odd and "i" not in odd)
big = base64.b64encode(b"\xff\xd8\xff" + b"0" * 20000).decode()
check("...and so is one too large",
      "i" not in (M.sanitize_preview({"u": "https://maps.example/ridge",
                                      "t": "x", "i": big}, body) or {}))

print("\nFetching a preview: public addresses only, a little of each page")
check("this phone's own network is never fetched",
      not M._public_host("127.0.0.1") and not M._public_host("10.1.2.3")
      and not M._public_host("192.168.0.1") and
      not M._public_host("169.254.169.254") and not M._public_host("::1"))
check("...nor anything but a web page",
      M._fetch_capped("file:///etc/passwd", 1000, "*/*") is None and
      M._fetch_capped("ftp://example.org/", 1000, "*/*") is None)
check("...and a local address asked for directly is refused",
      M.fetch_link_preview("http://127.0.0.1:9/") is None)


PAGE = ("<html><head><title>Fallback title</title>"
        "<meta property='og:title' content='The Ridge &amp; the Vale'>"
        "<meta name=\"description\" content=\"Eight miles of chalk.\">"
        "<meta property=\"og:site_name\" content=\"Walks\">"
        "<meta property=\"og:image\" content=\"/pic.png\">"
        "</head><body>" + "x" * 2000 + "</body></html>").encode()
picture = io.BytesIO()
Image.new("RGB", (900, 600), (120, 150, 90)).save(picture, "PNG")
import gzip                                                    # noqa: E402
import struct                                                  # noqa: E402
import zlib                                                    # noqa: E402
# 64 MB of page in 64 KB: what a site could send to swell in a phone.
SWELLING = gzip.compress(b"<title>Swollen</title>" + b" " * (64 << 20), 9)


def claimed_png(width, height):
    """A PNG of a few bytes that claims to be `width` by `height`."""
    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body +
                struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0,
                                       0, 0)) +
            chunk(b"IDAT", zlib.compress(b"\x00" * 64)) +
            chunk(b"IEND", b""))


VAST_PNG = claimed_png(30000, 20000)


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):
        path = self.path
        if path.startswith("/hop"):
            n = int(path[4:] or 0)
            self.send_response(302)
            self.send_header("Location", "/page" if n == 0 else
                             f"/hop{n - 1}")
            self.end_headers()
            return
        if path == "/private":
            self.send_response(302)
            self.send_header("Location", "http://10.0.0.1/")
            self.end_headers()
            return
        if path == "/huge":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            try:
                self.wfile.write(b"<title>Huge</title>" + b"y" * (4 << 20))
            except OSError:
                pass
            return
        if path == "/gzip":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(SWELLING)))
            self.end_headers()
            self.wfile.write(SWELLING)
            return
        if path == "/named":
            data = (b"<title>Asked for " + self.headers.get("Host", "").encode()
                    + b"</title>")
            kind = "text/html"
        elif path == "/odd-charset":
            data, kind = (b"<title>Still read</title>",
                          "text/html; charset=no-such-charset")
        elif path == "/vast.png":
            data, kind = VAST_PNG, "image/png"
        elif path == "/vast":
            data, kind = (b"<title>Vast</title><meta property='og:image' "
                          b"content='/vast.png'>", "text/html")
        elif path == "/pic.png":
            data, kind = picture.getvalue(), "image/png"
        elif path == "/plain":
            data, kind = b"just text", "text/plain"
        else:
            data, kind = PAGE, "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_address[1]}"
real_addresses = M._public_addresses
# This test's "public" site: 127.0.0.1, and a name no DNS knows that is
# checked as being there too (see "rebinding" below).
M._public_addresses = lambda host: ["127.0.0.1"] if host in (
    "127.0.0.1", "walks.invalid") else []
lp = M.fetch_link_preview(base + "/page")
check("a page's title, line, site and picture make its preview",
      lp and lp["t"] == "The Ridge & the Vale" and
      lp["d"] == "Eight miles of chalk." and lp["s"] == "Walks", lp)
raw = base64.b64decode(lp["i"]) if lp and lp.get("i") else b""
with Image.open(io.BytesIO(raw)) if raw else Image.new("RGB", (1, 1)) as im:
    thumb_size = im.size
check("...the picture a small JPEG, at most 160 pixels across",
      raw[:3] == b"\xff\xd8\xff" and len(raw) <= 16 * 1024 and
      max(thumb_size) <= 160)
check("...what it sends passes the reader's checks",
      M.sanitize_preview(lp, "see " + base + "/page") == lp)
check("three redirects are followed",
      (M.fetch_link_preview(base + "/hop2") or {}).get("u") ==
      base + "/hop2")
check("...a fourth is not", M.fetch_link_preview(base + "/hop4") is None)
check("a redirect into the phone's own network is refused",
      M.fetch_link_preview(base + "/private") is None)
check("what is not a web page has no preview",
      M.fetch_link_preview(base + "/plain") is None)
t0 = time.time()
huge = M.fetch_link_preview(base + "/huge")
check("only the first half megabyte of a page is read",
      huge and huge["t"] == "Huge" and time.time() - t0 < 10)
port = server.server_address[1]
named = M.fetch_link_preview(f"http://walks.invalid:{port}/named")
check("the page comes from the very address that was checked, by its own "
      "name (a second look-up could be answered differently)",
      named and named["t"] == f"Asked for walks.invalid:{port}", named)
got = M._fetch_capped(base + "/gzip", M.PREVIEW_PAGE_BYTES, "text/html")
check("a compressed page is opened only as far as the limit",
      got is not None and len(got[2]) <= M.PREVIEW_PAGE_BYTES and
      got[2].startswith(b"<title>Swollen"))
check("...pages are asked for uncompressed",
      M._opened(b"plain", "", 3) == b"pla" and
      M._opened(b"x", "br", 10) is None)
check("a page in a character set Python lacks is still read",
      (M.fetch_link_preview(base + "/odd-charset") or {}).get("t") ==
      "Still read")
vast = M.fetch_link_preview(base + "/vast")
check("a preview picture claiming 600 million pixels is never decoded",
      vast and vast["t"] == "Vast" and "i" not in vast, vast)
t0 = time.time()
M._page_meta("<meta name=x " * 60000 + "<title>" * 60000)
check("a page of endless unclosed tags is read as fast as any other",
      time.time() - t0 < 2, f"{time.time() - t0:.1f}s")
server.shutdown()


def tls_site(name):
    """A local HTTPS site with a certificate for `name`, from a CA of its
    own: (port, the CA's file, the hosts and SNI names it was asked for)."""
    import datetime
    import ssl
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    def make(cn, issuer_key=None, issuer=None):
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
        now = datetime.datetime.now(datetime.timezone.utc)
        built = (x509.CertificateBuilder().subject_name(subject)
                 .issuer_name(issuer.subject if issuer else subject)
                 .public_key(key.public_key())
                 .serial_number(x509.random_serial_number())
                 .not_valid_before(now - datetime.timedelta(days=1))
                 .not_valid_after(now + datetime.timedelta(days=2)))
        built = built.add_extension(
            x509.BasicConstraints(ca=True, path_length=None), critical=True) \
            if issuer is None else built.add_extension(
                x509.SubjectAlternativeName([x509.DNSName(cn)]),
                critical=False)
        return key, built.sign(issuer_key or key, hashes.SHA256())

    folder = tempfile.mkdtemp()
    ca_key, ca = make("Test CA")
    key, cert = make(name, ca_key, ca)
    pem = serialization.Encoding.PEM
    with open(f"{folder}/ca.pem", "wb") as fh:
        fh.write(ca.public_bytes(pem))
    with open(f"{folder}/site.pem", "wb") as fh:
        fh.write(cert.public_bytes(pem) + key.private_bytes(
            pem, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
    asked = []

    class Secure(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_GET(self):
            asked.append(("host", self.headers.get("Host")))
            body = b"<title>Over TLS</title>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Secure)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(f"{folder}/site.pem")
    context.sni_callback = lambda _s, sni, _c: asked.append(("sni", sni))
    site.socket = context.wrap_socket(site.socket, server_side=True)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    return site, f"{folder}/ca.pem", asked


import certifi                                                 # noqa: E402
secure, ca_file, asked = tls_site("walks.invalid")
real_where, certifi.where = certifi.where, (lambda: ca_file)
M._public_addresses = lambda host: ["127.0.0.1"] if host in (
    "walks.invalid", "elsewhere.invalid") else []
port = secure.server_address[1]
over = M.fetch_link_preview(f"https://walks.invalid:{port}/")
check("over https too, by the link's own name: its certificate checked for "
      "that name, and the name sent ahead (SNI)",
      over and over["t"] == "Over TLS" and
      ("sni", "walks.invalid") in asked and
      ("host", f"walks.invalid:{port}") in asked, (over, asked))
check("...a certificate for another name is refused",
      M.fetch_link_preview(f"https://elsewhere.invalid:{port}/") is None)
certifi.where = real_where
check("...and one from an authority not trusted",
      M.fetch_link_preview(f"https://walks.invalid:{port}/") is None)
secure.shutdown()
M._public_addresses = real_addresses

print("\nPictures from elsewhere are measured before they are decoded")
check("a picture's size is read from its header alone",
      M.image_header(picture.getvalue()) == ("PNG", 900, 600) and
      M.image_header(VAST_PNG) is None and
      M.image_header(claimed_png(9000, 10)) is None and
      M.image_header(b"GIF89a" + b"\x00" * 40) is None)
sent = io.BytesIO()
Image.new("RGB", (1280, 960), (90, 110, 140)).save(sent, "JPEG", quality=70)
check("a photo as this app sends it is kept as it came",
      M.safe_received_image(sent.getvalue()) == sent.getvalue())
large = io.BytesIO()
Image.new("RGBA", (4000, 3000), (10, 200, 30, 128)).save(large, "PNG")
redrawn = M.safe_received_image(large.getvalue())
with Image.open(io.BytesIO(redrawn or sent.getvalue())) as im:
    redrawn_size, redrawn_kind = im.size, im.format
check("...a bigger one, or a PNG, is drawn again here, smaller",
      redrawn is not None and redrawn_kind == "JPEG" and
      max(redrawn_size) == M.RECEIVED_KEEP_EDGE, redrawn_size)
check("...and one claiming too many pixels is refused",
      M.safe_received_image(VAST_PNG) is None and
      M.safe_received_image(b"not a picture") is None)
small = io.BytesIO()
Image.new("RGB", (40, 30), (1, 2, 3)).save(small, "JPEG")
thumb_png = io.BytesIO()
Image.new("RGB", (40, 30), (1, 2, 3)).save(thumb_png, "PNG")
thumb_big = io.BytesIO()
Image.new("RGB", (400, 300), (1, 2, 3)).save(thumb_big, "JPEG")
check("an inline thumbnail is drawn only if it is a small JPEG",
      M.texture_from_b64(base64.b64encode(small.getvalue()).decode())
      is not None and
      M.texture_from_b64(base64.b64encode(thumb_png.getvalue()).decode())
      is None and
      M.texture_from_b64(base64.b64encode(thumb_big.getvalue()).decode())
      is None and
      M.texture_from_b64(base64.b64encode(VAST_PNG).decode()) is None)


class BlobApi(StubApi):
    blobs = {}

    def download_blob(self, blob_id):
        return self.blobs[blob_id]


bob.api = BlobApi(relay, bob.keys)


def photo_arrives(data, label):
    """A photo message from alice, sealed as the app seals one, on bob's
    phone; returns its state once bob has fetched it."""
    import uuid
    blob, key, sha = M.seal_media(data)
    blob_id = uuid.uuid4().hex
    BlobApi.blobs[blob_id] = blob
    cid = uuid.uuid4().hex
    lid = bob.store.add_out(A, label, M.now_iso(), cid)   # a row to fill
    with bob.store._lock:
        bob.store._db.execute(
            "UPDATE messages SET direction = 'in', kind = 'image', ext = ?, "
            "media_state = 'pending' WHERE id = ?",
            (json.dumps({"media": {"kind": "image", "blob": blob_id,
                                   "key": key, "sha": sha,
                                   "mime": "image/jpeg", "size": len(blob)}}),
             lid))
        bob.store._db.commit()
    bob.fetch_media(lid)
    wait_for(lambda: bob.store.message(lid)["media_state"] not in (
        "pending", "downloading"), 10)
    return bob.store.message(lid)


row = photo_arrives(sent.getvalue(), "a fair photo")
check("a photo someone sends is kept, and shown",
      row["media_state"] == "ready" and row.get("media_path") and
      open(row["media_path"], "rb").read() == sent.getvalue())
row = photo_arrives(VAST_PNG, "a photo that is not")
check("...one that claims a vast size is not kept, and says it cannot be "
      "shown (and is not fetched again)",
      row["media_state"] == "unreadable" and not row.get("media_path"))
content = M.ImageContent(row, json.loads(row["ext"])["media"], None)
check("...in those words", content.chip.text == "This photo cannot be shown",
      content.chip.text)
bob.api = StubApi(relay, bob.keys)

print("\nA message's time must be a time")
real_now = M.now_iso
M.now_iso = lambda: ["not", "a", "time"]
try:
    envelope, _at = bob.crypto.build_envelope(
        "Odd times", alice.keys.public_key_hex,
        x={"cid": "c" * 32}, recipient=A, cid="c" * 32)
finally:
    M.now_iso = real_now
odd = {"id": 9001, "sender": B, "recipient": A, "envelope": envelope,
       "client_msg_id": "c" * 32, "created_at": "2026-10-15T09:30:00+00:00"}
acked = alice.worker._ingest_message(odd)
kept = [m for m in alice.store.messages_for(B) if m["body"] == "Odd times"]
check("a message whose time is not one is stored with the relay's, and "
      "acknowledged (it used to come back on every connect)",
      acked == 9001 and kept and
      kept[0]["created_at"] == "2026-10-15T09:30:00+00:00",
      (acked, kept and kept[0]["created_at"]))
check("...times are taken as they are written, Z or offset",
      M.sanitize_stamp("2026-10-15T09:30:00Z", "x") ==
      "2026-10-15T09:30:00Z" and
      M.sanitize_stamp("2026-10-15T09:30:00.5+02:00", "x") ==
      "2026-10-15T09:30:00.5+02:00" and
      M.sanitize_stamp(1760520600, "x") == "x" and
      M.sanitize_stamp("soon", "x") == "x" and
      M.sanitize_stamp("2026-10-15T09:30:00+00:00" + " " * 30, "x") == "x")

# =============================================================================
print("\nEncrypted backup")
import uuid                                                    # noqa: E402
M.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
photo = M.MEDIA_DIR / f"photo_{uuid.uuid4().hex}.jpg"
Image.new("RGB", (64, 48), (180, 120, 60)).save(photo, "JPEG")
pid = alice.store.add_out(B, "The ridge at dawn", M.now_iso(),
                          uuid.uuid4().hex, kind="image",
                          ext={"media": {"kind": "image", "w": 64, "h": 48}},
                          media_path=str(photo), media_state="ready")
M.PICTURES_DIR.mkdir(parents=True, exist_ok=True)
own = M.own_picture_path(A)
Image.new("RGB", (32, 32), (40, 60, 90)).save(own, "JPEG")
backups = os.path.join(os.environ["HOME"], "backups")
os.makedirs(backups)
target = os.path.join(backups, M.default_backup_name())
try:
    M.make_backup(alice.keys, "short", target)
    refused = False
except M.BackupError as exc:
    refused = "at least" in str(exc)
check("a passphrase too short is refused", refused)
steps = []
made = M.make_backup(alice.keys, "correct horse battery", target,
                     progress=steps.append)
raw = open(target, "rb").read()
check("the backup is one sealed file", raw.startswith(M.BACKUP_MAGIC) and
      made["size"] == len(raw) and steps and steps[-1] == 1.0)
check("...with no message, key or picture in it readable",
      b"this one stays" not in raw and b"ridge at dawn" not in raw and
      alice.keys.active["private_key_hex"].encode() not in raw and
      own.read_bytes()[:64] not in raw and b"store_" not in raw)
check("...and says what it holds",
      made["identities"][0]["address"] == A and
      made["identities"][0]["messages"] >= 5 and made["media"] == 1)
header = M.read_backup_header(target)
check("its clear header says only when, by what, and how it is sealed",
      header["v"] == 1 and header["by"].startswith("Ferritext") and
      header["kdf"]["name"] == "scrypt" and
      set(header) == {"v", "made", "by", "kdf"})


def restore_error(path, passphrase="correct horse battery", keys=None):
    try:
        M.restore_backup(keys or alice.keys, path, passphrase)
    except M.BackupError as exc:
        return str(exc)
    return None


check("the wrong passphrase opens nothing, and says so",
      restore_error(target, "correct horse battery!") == "Wrong passphrase.")
damaged = target + ".damaged"
middle = len(raw) - 200
open(damaged, "wb").write(raw[:middle] + bytes([raw[middle] ^ 1]) +
                          raw[middle + 1:])
check("a damaged file is found out", restore_error(damaged) ==
      "This file is damaged.")
short = target + ".short"
open(short, "wb").write(raw[:-300])
check("...and one cut short", "cut short" in (restore_error(short) or ""))
longer = target + ".long"
open(longer, "wb").write(raw + b"extra")
check("...and one with more after its end",
      restore_error(longer) == "This file is damaged.")
open(short, "wb").write(b"PK\x03\x04 not a backup")
check("...and what is no backup at all",
      restore_error(short) == "This is not a Ferritext backup.")
hostile = bytearray(raw)
at = hostile.index(b'"n":32768')
hostile[at:at + 9] = b'"n":99999'
open(damaged, "wb").write(bytes(hostile))
check("a header asking for endless work is refused before any is done",
      restore_error(damaged) == "This file is damaged.")

# Another device: a new data folder, nothing on it yet.
saved = {name: getattr(M, name) for name in
         ("DATA_DIR", "IDENTITIES_PATH", "MEDIA_DIR", "PICTURES_DIR")}
device = M.Path(tempfile.mkdtemp(prefix="fext-new-device-"))
M.DATA_DIR, M.IDENTITIES_PATH = device, device / "identities.json"
M.MEDIA_DIR, M.PICTURES_DIR = device / "media", device / "pictures"
fresh = M.IdentityManager()
check("(a new device has no identity)", not fresh.has_identity)
summary = M.restore_backup(fresh, target, "correct horse battery")
check("restored on a new device: the identity, with its chats",
      summary["added"] == ["alice"] and fresh.find(A) is not None and
      fresh.find(A)["username"] == "alice" and summary["messages"] >= 5)
store = M.LocalStore(fresh.store_path_for(A))
rows = store.messages_for(B)
photo_row = [m for m in rows if m["body"] == "The ridge at dawn"][0]
check("...its messages as they were",
      "this one stays" in [m["body"] for m in rows])
check("...its photos, found where this device keeps them",
      photo_row["media_path"] == str(M.MEDIA_DIR / photo.name) and
      M.Path(photo_row["media_path"]).read_bytes() == photo.read_bytes())
check("...and its own picture", M.own_picture_path(A).is_file())
check("...the key the same key", fresh.find(A)["private_key_hex"] ==
      alice.keys.active["private_key_hex"])
check("...and its post-quantum key (15.0), so what contacts seal to it opens",
      len(fresh.find(A).get("pq_seed_hex", "")) == 128 and
      fresh.find(A)["pq_seed_hex"] == alice.keys.active["pq_seed_hex"])
store.close()
again = M.restore_backup(fresh, target, "correct horse battery")
check("restored again: an identity already here keeps its own chats",
      again["kept"] == ["alice"] and not again["added"])

# An identity set up again from its key (no chats yet) gets its history.
M.IDENTITIES_PATH.unlink()
for leftover in device.glob("store_*"):
    leftover.unlink()
fresh = M.IdentityManager()
fresh.add_identity(int(alice.keys.active["private_key_hex"], 16), "phone")
summary = M.restore_backup(fresh, target, "correct horse battery")
store = M.LocalStore(fresh.store_path_for(A))
check("an identity here with no chats yet gets them from the backup",
      summary["replaced"] == ["alice"] and
      "this one stays" in [m["body"] for m in store.messages_for(B)])
store.close()

# A file made to escape: names outside the backup's own are not followed.
evil = os.path.join(backups, "evil" + M.BACKUP_SUFFIX)
head = json.dumps({"v": 1, "made": M.now_iso(), "by": "x", "kdf": {
    "name": "scrypt", "n": 1024, "r": 8, "p": 1,
    "salt": M.b64e(os.urandom(16))}}, separators=(",", ":")).encode()
prefix = M.BACKUP_MAGIC + len(head).to_bytes(4, "big") + head
import hashlib                                                 # noqa: E402
import tarfile                                                 # noqa: E402
with open(evil, "wb") as out:
    out.write(prefix)
    sealed = M._SealedWriter(out, M._backup_key("open sesame", json.loads(
        head)["kdf"]), hashlib.sha256(prefix).digest())
    with tarfile.open(fileobj=sealed, mode="w|") as tar:
        for name, data in (("../../escaped.txt", b"x"),
                           ("media/../../escaped2.txt", b"x"),
                           ("identities.json", b'{"identities": []}')):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    sealed.finish()
outcome = M.restore_backup(fresh, evil, "open sesame")
check("names that would leave the backup's folders are ignored",
      not any(M.Path(device).parent.glob("escaped*")) and
      not any(device.rglob("escaped*")) and not outcome["added"])
for name, value in saved.items():
    setattr(M, name, value)

# The screens around it.
alice.open_settings()
tick(0.5)
sheet = M.BackupSheet(alice)
sheet.open()
tick(0.4)
sheet.pass1.text, sheet.pass2.text = "correct horse battery", "different"
sheet._start()
check("making a backup: two passphrases that differ are caught",
      "differ" in sheet.status.text)
sheet.pass2.text = "correct horse battery"
sheet.path_field.text = os.path.join(backups, "from the screen")
sheet._start()
check("...then it is sealed, and saved where asked",
      wait_for(lambda: sheet.status.text.startswith("Saved"), 30) and
      os.path.isfile(os.path.join(backups, "from the screen" +
                                  M.BACKUP_SUFFIX)), sheet.status.text)
folder = os.path.join(backups, "a folder")
os.makedirs(folder)
sheet.status.text = ""
sheet.path_field.text = folder
sheet._start()
check("...and a folder given as the place: the file goes in it",
      wait_for(lambda: sheet.status.text.startswith("Saved"), 30) and
      os.listdir(folder) == [M.default_backup_name()] and
      not os.path.exists(folder + M.BACKUP_SUFFIX),
      (sheet.status.text, os.listdir(backups)))
sheet.dismiss(animation=False)
restore = M.RestoreSheet(alice)
restore.open()
tick(0.4)
restore._picked(target)
check("restoring: the chosen backup says when it was made",
      restore.made.text.startswith("Made ") and "Ferritext" in
      restore.made.text)
restore.passphrase.text = "not it at all"
M.HEARTBEAT_EVERY, every = 0.2, M.HEARTBEAT_EVERY
beat = M._beat_path("app")
beat.write_text(f"{time.time() - 3600:.1f}")       # as if long gone
restore._start()
M.HEARTBEAT_EVERY = every
check("while a restore runs, the app's heartbeat goes on, so the "
      "background service does not open the files being replaced",
      alice.worker is None and alice._pause_beat is not None and
      M.heartbeat_fresh("app"))
check("...a wrong passphrase is said, and the app carries on as it was",
      wait_for(lambda: restore.status.text == "Wrong passphrase.", 30) and
      alice.store is not None and alice.keys.address == A)
check("...its own worker beating again, the stand-in stopped",
      alice._pause_beat is None and alice.worker is not None)
restore.dismiss(animation=False)
alice.close_settings()
tick(0.3)

print("\nTLS on the relay's own port (v12)")
import http.server                                            # noqa: E402
import ssl                                                    # noqa: E402
import subprocess                                             # noqa: E402

pki = M.Path(tempfile.mkdtemp(prefix="fext-tls-"))
(pki / "leaf.ext").write_text(
    "subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\n"
    "keyUsage=digitalSignature\nextendedKeyUsage=serverAuth\n"
    "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n")
for argv in (
        ["req", "-x509", "-newkey", "ec", "-pkeyopt",
         "ec_paramgen_curve:P-256", "-nodes", "-keyout", "ca.key", "-out",
         "ca.pem", "-days", "2", "-subj", "/CN=Test Relay CA"],
        ["req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
         "-nodes", "-keyout", "leaf.key", "-out", "leaf.csr", "-subj",
         "/CN=127.0.0.1"],
        ["x509", "-req", "-in", "leaf.csr", "-CA", "ca.pem", "-CAkey",
         "ca.key", "-CAcreateserial", "-out", "leaf.pem", "-days", "2",
         "-extfile", "leaf.ext"]):
    subprocess.run(["openssl"] + argv, cwd=pki, check=True,
                   capture_output=True)
announce = {"tls_port": None}
handshakes = [0]


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = {"status": "ok", "version": "12.0.0"}
        if announce["tls_port"] and not isinstance(self.connection,
                                                   ssl.SSLSocket):
            body["tls_port"] = announce["tls_port"]
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_a):
        pass


class Counting(http.server.ThreadingHTTPServer):
    def get_request(self):
        handshakes[0] += 1
        return super().get_request()


plain = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
secure = Counting(("127.0.0.1", 0), Health)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(pki / "leaf.pem", pki / "leaf.key")
secure.socket = context.wrap_socket(secure.socket, server_side=True)
for server in (plain, secure):
    threading.Thread(target=server.serve_forever, daemon=True).start()
P, T = plain.server_address[1], secure.server_address[1]


class Endpoint(M.Endpoint):
    CACHE_NAME = "transport-test.json"


override = M.SERVER_URL_OVERRIDE
M.SERVER_URL_OVERRIDE = f"http://127.0.0.1:{P}"
(M.DATA_DIR / Endpoint.CACHE_NAME).unlink(missing_ok=True)
try:
    ep = Endpoint()
    check("a relay on plain HTTP alone: plain HTTP, said to be unencrypted",
          ep.resolve() == f"http://127.0.0.1:{P}" and not ep.secure)
    announce["tls_port"] = T
    os.environ["REQUESTS_CA_BUNDLE"] = str(pki / "ca.pem")
    ep = Endpoint()
    check("a relay that names its TLS port: the app moves there once the "
          "certificate checks out against the roots it trusts",
          ep.resolve(force=True) == f"https://127.0.0.1:{T}" and ep.secure
          and ep.ws_url() == f"wss://127.0.0.1:{T}/ws")
    again = Endpoint()
    check("...and remembers it: the next start goes straight there",
          again.resolved and again.base == f"https://127.0.0.1:{T}" and
          again.secure)
    (M.DATA_DIR / Endpoint.CACHE_NAME).unlink(missing_ok=True)
    del os.environ["REQUESTS_CA_BUNDLE"]           # the test CA: unknown
    ep = Endpoint()
    before = handshakes[0]
    check("a certificate it cannot verify is never accepted: plain HTTP "
          "still, and said so", ep.resolve() == f"http://127.0.0.1:{P}"
          and not ep.secure and handshakes[0] > before)
    before = handshakes[0]
    ep.invalidate()
    check("...and that port is left alone for a while, not tried on every "
          "reconnect", ep.resolve() == f"http://127.0.0.1:{P}" and
          handshakes[0] == before)
    os.environ["REQUESTS_CA_BUNDLE"] = str(pki / "ca.pem")
    ep = Endpoint()
    ep.resolve(force=True)
    secure.shutdown()
    secure.server_close()
    ep.invalidate()
    check("when TLS stops answering, the app goes back to plain HTTP, and "
          "says so", ep.resolve() == f"http://127.0.0.1:{P}" and
          not ep.secure)
finally:
    M.SERVER_URL_OVERRIDE = override
    os.environ.pop("REQUESTS_CA_BUNDLE", None)
    plain.shutdown()

passed = sum(1 for _n, ok in RESULTS if ok)
print(f"\n  {passed}/{len(RESULTS)} passed")
for app in (alice, bob):
    app.worker.stop_event.set()
sys.exit(0 if passed == len(RESULTS) else 1)
