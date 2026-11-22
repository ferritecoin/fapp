#!/usr/bin/env python3
"""Remote control (19.0, REMOTE): a shared screen controlled in a call,
and remote sessions a computer answers by itself for the people on its
access list (.private/PLAN_REMOTE.md).

    xvfb-run -a python3 desktop/test_remote.py main.py

The wire first (input packets, their numbers, what is held), then where a
finger lands on a letterboxed picture, the access list and the start-up
entry, two call engines with one controlling the other, and two real apps
through stand-ins for the relay: a remote session answered by itself, its
input done, control released and asked again, Stop, a session that drops
and is placed again, someone not listed set aside, and control asked in an
ordinary call, allowed, and ended by the screen no longer shared. Last,
the computer's real injector: on X11 (Xvfb) it moves the pointer, holds a
button and a key and types into a window; on Windows (the Devices
workflow) it moves the cursor and reads it back.
"""
import ctypes
import ctypes.util
import importlib.util
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
if sys.platform.startswith("linux"):     # xvfb-run's X display
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-remote-")
os.environ["USERPROFILE"] = os.environ["HOME"]   # Path.home() on Windows
os.environ["XDG_CONFIG_HOME"] = os.path.join(os.environ["HOME"], ".config")
os.environ["FEXT_LANG"] = "en"          # the English the checks read
os.environ["FEXT_SERVER_HOST"] = "127.0.0.1:1"
os.environ["FEXT_CALL_LOOPBACK"] = "1"         # this machine is "the LAN"

_spec = importlib.util.spec_from_file_location(
    "fext_remote", sys.argv[1] if len(sys.argv) > 1 else "main.py")
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_remote"] = M
_spec.loader.exec_module(M)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""), flush=True)


def wait_until(cond, timeout=5.0, tick=None):
    end = time.time() + timeout
    while time.time() < end:
        if tick is not None:
            tick()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


MOVE, BUTTON, WHEEL, KEY, TEXT = (M.INPUT_MOVE, M.INPUT_BUTTON,
                                  M.INPUT_WHEEL, M.INPUT_KEY, M.INPUT_TEXT)


def key(name):
    return M.REMOTE_KEYS.index(name)


# ---- the wire ------------------------------------------------------------------
print("\nInput packets: each kind both ways, and nothing malformed taken")
events = [(1, MOVE, (0, 65535)), (2, BUTTON, (1, 1, 100, 200)),
          (3, WHEEL, (-120, 240)), (4, KEY, (key("ctrl"), 1)),
          (5, TEXT, ("héllo 中",))]
packet = M.pack_input(events, 1, 2)
check("every kind of event, the buttons and keys held: read back as sent",
      M.unpack_input(packet) == (events, 1, 2), M.unpack_input(packet))
good = M.pack_input([(9, MOVE, (5, 6))], 0, 0)
bad = {
    "empty": b"",
    "another version": bytes([2]) + good[1:],
    "more events than a packet holds": bytes([1, M.REMOTE_KEEP + 2, 0, 0])
    + good[4:],
    "a kind unknown": M._INPUT_HEAD.pack(1, 1, 0, 0) +
    M._INPUT_EVENT.pack(1, 9) + b"\0\0\0\0",
    "a key beyond the list": M.pack_input([(1, KEY, (len(M.REMOTE_KEYS),
                                                     1))], 0, 0),
    "a fourth button": M._INPUT_HEAD.pack(1, 1, 0, 0) +
    M._INPUT_EVENT.pack(1, BUTTON) + struct.pack("!BBHH", 4, 1, 0, 0),
    "text longer than allowed": M._INPUT_HEAD.pack(1, 1, 0, 0) +
    M._INPUT_EVENT.pack(1, TEXT) + bytes([60]) + b"a" * 60,
    "a byte left over": good + b"\0",
    "cut short": good[:-1],
}
refused = [name for name, raw in bad.items() if M.unpack_input(raw) is None]
check("every malformed packet is refused whole", len(refused) == len(bad),
      set(bad) - set(refused))
check("typed text is cut to 48 bytes between characters, never inside one",
      M.remote_text("中" * 20) == "中" * 16 and
      M.remote_text("a" * 60) == "a" * 48)

print("\nNumbers and repeats: a lost packet made good, nothing done twice")
out, done = M.InputOut(), M.RemoteInjector()
into = M.InputIn(done)
for n in range(3):
    out.add(MOVE, (n, n), 0.0)
check("only the newest pointer move is carried",
      [e for e in M.unpack_input(out.packet())[0] if e[1] == MOVE] ==
      [(3, MOVE, (2, 2))])
first = out.packet()
out.add(BUTTON, (1, 1, 10, 20), 0.0)
out.add(BUTTON, (1, 0, 10, 20), 0.0)
out.add(TEXT, ("ok",), 0.0)
second = out.packet()
check("the second packet alone (the first lost) does everything, in order",
      into.take(second) == 4 and done.done == [
          ("move", 2, 2), ("button", 1, True, 10, 20),
          ("button", 1, False, 10, 20), ("text", "ok")], done.done)
check("the same packet again, or an older one late, does nothing",
      into.take(second) == 0 and into.take(first) == 0 and
      len(done.done) == 4)
for n in range(20):
    out.add(KEY, (key("a"), n % 2), 0.0)
check("a packet keeps the last twelve events and the newest move",
      len(M.unpack_input(out.packet())[0]) == M.REMOTE_KEEP + 1)

print("\nWhat is held: a stuck button or key let go")
done, out = M.RemoteInjector(), M.InputOut()
into = M.InputIn(done)
out.add(BUTTON, (1, 1, 50, 60), 0.0)
out.add(KEY, (key("ctrl"), 1), 0.0)
into.take(out.packet())
check("held down there as here", into.held == 1 and into.mods == 2,
      (into.held, into.mods))
late = M.pack_input([(out.number + 1, MOVE, (70, 80))], 0, 0)
into.take(late)      # their letting go was lost; this packet holds nothing
check("a newer packet that holds nothing lets both go there",
      ("button", 1, False, 70, 80) in done.done and
      ("key", "ctrl", False) in done.done and into.held == into.mods == 0,
      done.done[-3:])
out = M.InputOut()
out.add(BUTTON, (2, 1, 1, 1), 0.0)
out.add(KEY, (key("shift"), 1), 0.0)
out.let_go(0.0)
check("the side that controls, letting go, says every button and key up",
      out.held == out.mods == 0 and (3, BUTTON, (2, 0, 0, 0)) in
      out.recent and (4, KEY, (key("shift"), 0)) in out.recent, out.recent)
done = M.RemoteInjector()
into = M.InputIn(done)
into.take(M.pack_input([(1, BUTTON, (3, 1, 9, 9)), (2, KEY, (key("alt"),
                                                            1))], 4, 4))
into.let_go()
check("control ended: the computer lets go of all it was told to hold",
      ("button", 3, False, 9, 9) in done.done and
      ("key", "alt", False) in done.done)

# ---- where a finger lands --------------------------------------------------------
print("\nWhere on their screen: a letterboxed picture")
tex = types.SimpleNamespace(size=(1920, 1080))
picture = types.SimpleNamespace(frames=lambda _u, _t: tex, user="x",
                                track="s", width=1000, height=800,
                                center_x=500, center_y=400)
sent = []
surface = M.RemoteSurface(picture, lambda kind, fields: sent.append(
    (kind, tuple(fields))))
top = 400 + 1000 * 1080 / 1920 / 2
check("the picture's middle is the middle of their screen",
      surface.where(500, 400) == (32768, 32768), surface.where(500, 400))
check("its top left corner is theirs; the bands around it are nowhere",
      surface.where(0, top) == (0, 0) and surface.where(500, 100) is None
      and surface.where(500, top + 5) is None)


class Finger:
    """A touch as Kivy hands it to the widget that took it."""

    def __init__(self, x, y, uid=1):
        self.uid, self.ud, self.grab_current = uid, {}, None
        self.device, self.button = "touch", ""
        self.to(x, y)

    def to(self, x, y):
        self.x, self.y, self.pos = x, y, (x, y)

    def grab(self, widget, exclusive=False):
        self.grab_current = widget

    def ungrab(self, widget):
        self.grab_current = None


surface.active = True               # as start() leaves it, on a phone
tap = Finger(500, 400)
surface.on_touch_down(tap)
surface.on_touch_up(tap)
check("a tap: the pointer there, then a click",
      sent == [(MOVE, (32768, 32768)), (BUTTON, (1, 1, 32768, 32768)),
               (BUTTON, (1, 0, 32768, 32768))], sent)
sent.clear()
drag = Finger(400, 400)
surface.on_touch_down(drag)
drag.to(600, 400)
surface.on_touch_move(drag)
surface.on_touch_up(drag)
check("a finger drawn: the left button down, moved, up where it ends",
      [k for k, _f in sent] == [MOVE, BUTTON, MOVE, BUTTON] and
      sent[1][1][:2] == (1, 1) and sent[3][1][:2] == (1, 0) and
      sent[3][1][2] > sent[1][1][2], sent)
sent.clear()
one, two = Finger(450, 400, 1), Finger(550, 400, 2)
surface.on_touch_down(one)
surface.on_touch_down(two)
two.to(550, 400 + 2 * surface.STEP + 1)
surface.on_touch_move(two)
surface.on_touch_up(two)
surface.on_touch_up(one)
check("two fingers drawn up scroll: two notches, the page moving with them",
      sent == [(WHEEL, (0, -120)), (WHEEL, (0, -120))], sent)
surface.active = False

# ---- the access list ---------------------------------------------------------------
print("\nThe access list: who, until when, per identity")
me, other = "Fme" + "1" * 30, "Fother" + "2" * 28
A1, A2 = "Fpeer" + "3" * 29, "Fpeer" + "4" * 29
now = time.time()
M.remote_access_grant(me, A1, 1)
M.remote_access_grant(me, A2, 0)
listed = M.remote_access_list(me)
check("listed newest first, a day's grant ending in a day, the other never",
      [e["a"] for e in listed] == [A2, A1] and listed[0]["until"] == 0 and
      abs(listed[1]["until"] - (now + 86400)) < 5, listed)
check("one identity's list is not another's",
      M.remote_access_list(other) == [] and
      not M.remote_access_allows(other, A1))
M.remote_access_grant(me, A1, 1, now=now - 2 * 86400)
check("a grant whose end has passed lets no one in",
      not M.remote_access_allows(me, A1) and M.remote_access_allows(me, A2))
check("Settings' line counts them",
      "One person" in M.remote_access_summary(me) and
      "No one" in M.remote_access_summary(other))
check("keep awake: wanted while someone is listed, unless turned off",
      M.keep_awake_wanted(me) and not M.keep_awake_wanted(other))
M.save_pref("remote_awake", False)
check("… and not once Keep this computer awake is off",
      not M.keep_awake_wanted(me))
M.save_pref("remote_awake", True)
M.remote_access_revoke(me, A2)
check("removed: let in no more", not M.remote_access_allows(me, A2))
for n in range(M.REMOTE_SESSIONS_KEPT + 3):
    M.remote_session_kept(A1, "away", n, n + 60)
check("the sessions kept: the newest twenty",
      len(M.remote_sessions()) == M.REMOTE_SESSIONS_KEPT and
      M.remote_sessions()[0]["start"] == M.REMOTE_SESSIONS_KEPT + 2)
M.save_pref("remote_sessions", [])

if sys.platform.startswith("linux"):
    print("\nLinux: started when its person signs in")
    check("an autostart entry made, naming this Ferritext minimised",
          M.set_start_with_session(True) and M.start_with_session() and
          M.STARTUP_FLAG in M.linux_autostart_path().read_text())
    check("… and removed again", M.set_start_with_session(False) and
          not M.linux_autostart_path().exists() and
          not M.start_with_session())

# ---- the offer, and the engine's flags ---------------------------------------------
print("\nThe offer and the media state")
offer = {"id": "a" * 32, "tag": "b" * 16, "k": M.new_call_key()[1],
         "codecs": ["ima16"], "c": []}
check("a remote session's offer says so; anything but true is not one",
      M.sanitize_call_signal("call", dict(offer, rc=True))["rc"] is True and
      M.sanitize_call_signal("call", dict(offer, rc=1))["rc"] is False and
      M.sanitize_call_signal("call", offer)["rc"] is False)
check("the media flags: asks 16, lets 32, can be controlled 64",
      (M.CALL_MEDIA_FLAGS["control"], M.CALL_MEDIA_FLAGS["lets"],
       M.CALL_MEDIA_FLAGS["controllable"]) == (16, 32, 64))
told = []
engine = M.CallEngine("c" * 32, b"t" * 8, 0, None, "",
                      on_event=lambda k, v: told.append((k, v)))


def heard(flags):
    engine._heard_media(M._CALL_MEDIA.pack(flags, 1, 0, 0, 0, 0, 0, 0))


engine.set_control(True, False, True)
check("this side's bits: it asks, and its own screen can be controlled",
      engine._control_bits == 16 | 64)
heard(4 | 64)
check("asking is not controlling until they let it",
      not engine.controlling and ("control", True) not in told)
heard(4 | 32 | 64)
check("they let it: controlling, and the app told",
      engine.controlling and ("control", True) in told)
heard(4 | 64)
check("they stop letting it: no longer, and the app told",
      not engine.controlling and ("control", False) in told)
heard(4 | 32 | 64)
engine.set_control(False, False)
check("this side letting go ends it at once",
      not engine.controlling and told[-1] == ("control", False))
engine.sock.close()

# ---- two engines ---------------------------------------------------------------------
print("\nTwo engines: one controls the other's screen")
os.environ["FEXT_REMOTE_TEST"] = "1"      # the computer keeps what it is told
cid, tg, secret = os.urandom(16).hex(), os.urandom(8), os.urandom(32)
said = {0: [], 1: []}
pair = {}
for role in (0, 1):
    pair[role] = M.CallEngine(cid, tg, role, None, "", on_event=lambda k, v,
                              r=role: said[r].append((k, v)),
                              audio_factory=M.CallAudio)
    pair[role].set_keys(M.CallKeys(secret, cid, tg, caller=role == 0))
pair[0].add_candidates(pair[1].candidates())
pair[1].add_candidates(pair[0].candidates())
for e in pair.values():
    e.start()
    e.start_audio()
controller, computer = pair[0], pair[1]
controller.set_control(True, False)
controller.send_input(MOVE, (1, 1))       # not controlling yet: not sent
computer.set_media(False, False, True)
computer.set_control(False, True, True)
check("the controller controls once the computer lets it",
      wait_until(lambda: controller.controlling, 4))
controller.send_input(MOVE, (100, 200))
controller.send_input(BUTTON, (1, 1, 100, 200))
controller.send_input(BUTTON, (1, 0, 100, 200))
controller.send_input(TEXT, ("Grüße",))
controller.send_input(KEY, (key("ctrl"), 1))


def computer_done():
    return computer.input_in.injector.done if computer.input_in else []


check("the computer does each event once, in order (nothing sent before)",
      wait_until(lambda: len(computer_done()) >= 5, 3) and
      computer_done()[:5] == [("move", 100, 200),
                              ("button", 1, True, 100, 200),
                              ("button", 1, False, 100, 200),
                              ("text", "Grüße"), ("key", "ctrl", True)],
      computer_done())
time.sleep(0.6)               # repeats and the half-second state, meanwhile
check("repeated ten times a second, done once", len(computer_done()) == 5,
      computer_done())
injector = computer.input_in.injector
computer.set_control(False, False, True)
check("the computer stops letting it: Ctrl let go there, the controller "
      "told", wait_until(lambda: computer.input_in is None and
                         not controller.controlling, 3) and
      ("key", "ctrl", False) in injector.done and
      ("control", False) in said[0], injector.done[-2:])
controller.send_input(MOVE, (5, 5))
time.sleep(0.3)
check("nothing more is done there", computer.input_in is None)
for e in pair.values():
    e.stop()
for e in pair.values():
    e.join(timeout=2)


# ---- two apps --------------------------------------------------------------------------
class StandInRelay(threading.Thread):
    """The relay's call port: REG answered with the address seen; packets
    passed between the two ends of a tag."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.addr = self.sock.getsockname()
        self.ends, self.passed = {}, 0

    def run(self):
        self.sock.settimeout(0.1)
        while True:
            try:
                data, src = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except ConnectionResetError:    # Windows: an ICMP refusal from
                continue                    # an end that has gone
            except OSError:
                return
            tag = data[1:9]
            if data[0] == M.PKT_REG:
                self.ends.setdefault(tag, {})[data[9]] = src
                self.sock.sendto(struct.pack("!B8sBB", M.PKT_REG_OK, tag,
                                             data[9], 4) +
                                 socket.inet_aton(src[0]) +
                                 struct.pack("!HB", src[1], 1), src)
                continue
            ends = self.ends.get(tag, {})
            role = next((r for r, a in ends.items() if a == src), None)
            if role is not None and (1 - role) in ends:
                self.sock.sendto(data, ends[1 - role])
                self.passed += 1


class MailRelay:
    def __init__(self):
        self.mail, self._next = [], 1

    def post(self, sender, recipient, envelope, cid):
        self._next += 1
        self.mail.append({"id": self._next, "sender": sender,
                          "recipient": recipient, "envelope": envelope,
                          "client_msg_id": cid, "created_at": datetime.now(
                              timezone.utc).isoformat()})
        return self._next


class StubApi:
    clock_offset = 0.0

    def __init__(self, mail, keys, udp):
        self.mail, self.keys, self.udp = mail, keys, udp

    def features(self):
        return {"blobs": True, "calls": True, "call_udp": self.udp[1]}

    def send_message(self, recipient, envelope, client_msg_id):
        return {"server_id": self.mail.post(self.keys.address, recipient,
                                            envelope, client_msg_id)}

    def call_ticket(self):
        return {"ticket": "ticket", "udp": self.udp[1]}

    def read(self, ids):
        return {"updated": len(ids)}

    def relay_now(self):
        return time.time()

    def local_time(self, at):
        return at

    def set_ticks(self, peer, on):
        return {}


def make_party(label, mail, udp):
    app = M.FextApp()
    app._dismiss_splash = lambda *_a: None
    app._probe_transport = lambda: None
    app.build()
    priv, _p, _a, _n = M.mine_identity()
    address = app.keys.add_identity(priv, label)
    app.keys.activate(address)
    app.keys.set_username(label)
    app.keys._data["identities"] = [app.keys.find(address)]
    app.crypto = M.CryptoEngine(app.keys)
    app.api = StubApi(mail, app.keys, udp)
    app.store = M.LocalStore(app.keys.store_path())
    app.worker = M.SyncWorker(app)
    # The computer's screen, shared at once (no capture here).
    app.start_call_video = lambda session, track, then: then("")
    app.stop_call_video = lambda track: None
    app.foreground = True
    return app


def pump(parties):
    from kivy.clock import Clock
    Clock.tick()
    while mail.mail:
        msg = mail.mail.pop(0)
        parties[msg["recipient"]].worker._ingest_message(msg)
    for p in parties.values():
        p._drain_ui_queue(0)


def settle(cond, timeout=8.0):
    return wait_until(cond, timeout, tick=lambda: pump(parties))


notices = []
M.desktop_notify = lambda title, text: notices.append((title, text)) or True
M.remote_control_possible = lambda: True        # Windows' or X11's here
relay = StandInRelay()
relay.start()
mail = MailRelay()
alice = make_party("alice", mail, relay.addr)   # the side that controls
bob = make_party("bob", mail, relay.addr)       # the computer
parties = {alice.keys.address: alice, bob.keys.address: bob}
A, B = alice.keys.address, bob.keys.address
for one, two in ((alice, bob), (bob, alice)):
    one.store.upsert_contact(two.keys.address, two.keys.username,
                             two.keys.public_key_hex)
for one, two in ((alice, B), (bob, A)):
    one.send_text(two, "hi")
settle(lambda: not mail.mail and alice.call_blocker(B) is None)

print("\nSomeone not listed: set aside, without a ring or a word back")
alice.open_conversation(B)
menus = []
real_menu = M.MenuSheet


class Menu:
    def __init__(self, items, title="", **_kw):
        menus.append([text for _i, text, *_r in items])

    def open(self):
        pass


M.MenuSheet = Menu
alice.conversation_screen.open_menu()
M.MenuSheet = real_menu
check("a chat's menu offers Connect to their computer",
      menus and "Connect to their computer" in menus[-1], menus)
alice.connect_remote(B)
settle(lambda: not mail.mail)
time.sleep(0.3)
pump(parties)
check("the offer says it is a remote session, and through the relay only",
      alice.call is not None and alice.call.remote and
      alice.call.engine is not None and
      not alice.call.engine.direct_allowed)
check("their computer, not listing alice, neither rings nor answers",
      bob.call is None and alice.call.state == "calling")
check("… and keeps a line of the try in the chat",
      any("tried to connect to this computer" in (m.get("body") or "")
          for m in bob.store.messages_for(A)))
alice.end_call()
settle(lambda: not mail.mail)

print("\nA remote session: answered by itself, its screen shared, controlled")
real_blocker = M.call_device_blocker
M.call_device_blocker = lambda: "No microphone"    # whatever this machine has
check("a computer without a microphone or speaker: no ordinary call, but "
      "a remote session needs neither",
      alice.call_blocker(B) is not None and
      alice.call_blocker(B, remote=True) is None)
M.call_device_blocker = real_blocker
M.remote_access_grant(B, A, 1)
from kivy.clock import Clock  # noqa: E402
settle(lambda: alice.call is None or alice.call.ended, 3)
Clock.tick()
alice.call = None
alice.connect_remote(B)
check("bob's computer answers by itself, alice controls it at once",
      settle(lambda: alice.controlling() and bob.call is not None and
             bob.call.lets), bob.call and (bob.call.state, bob.call.lets))
call_b, call_a = bob.call, alice.call
check("bob's side: no sound (no device opened), the relay only, muted",
      type(call_b.engine._audio) is M.CallAudio and
      not call_b.engine.direct_allowed and call_b.muted and
      call_b.session.me.screen and call_b.control_kind == "away")
check("alice's side: no sound either, and her ask said",
      type(call_a.engine._audio) is M.CallAudio and call_a.asks)
check("bob's computer says who is using it, in a notice and the red bar",
      notices and "alice" in notices[-1][1] and bob._call_overlay is not None
      and bob._call_overlay.remote_words.text ==
      "alice is controlling this computer" and
      bob._call_overlay.remote_band.opacity == 1, (notices, bob._call_overlay
                                                   and bob._call_overlay.
                                                   remote_words.text))


def overlay_words():
    view = alice._call_overlay
    return view and (view.remote_words.text, view.remote_button.text.lower(),
                     view.status.text[:14])


check("alice's call screen: a remote session, and how to let go",
      settle(lambda: overlay_words() == (
          "You are controlling bob's computer", "release", "Remote session"),
          3), overlay_words())
for kind, fields in ((MOVE, (1000, 2000)), (BUTTON, (1, 1, 1000, 2000)),
                     (BUTTON, (1, 0, 1000, 2000)), (TEXT, ("hello",)),
                     (KEY, (key("shift"), 1))):
    alice.send_remote_input(kind, fields)


def bob_done():
    engine = bob.call.engine if bob.call else None
    return engine.input_in.injector.done if engine and engine.input_in \
        else []


check("what alice does is done on bob's computer, in order",
      settle(lambda: len(bob_done()) >= 5, 4) and bob_done()[:5] == [
          ("move", 1000, 2000), ("button", 1, True, 1000, 2000),
          ("button", 1, False, 1000, 2000), ("text", "hello"),
          ("key", "shift", True)], bob_done())
held_by = bob.call.engine.input_in.injector
alice.release_control()
check("alice releases: bob's computer lets go of Shift, the session kept",
      settle(lambda: not bob.call.lets and not alice.controlling(), 4) and
      ("key", "shift", False) in held_by.done and
      M.remote_sessions() and M.remote_sessions()[0]["kind"] == "away")
alice.toggle_control()
check("asked again: let in again by the list, without a question",
      settle(lambda: alice.controlling() and bob.call.lets, 4))
bob._call_overlay._remote_end()
check("Stop on bob's computer ends the whole session, and it is not placed "
      "again", settle(lambda: alice.call is None or alice.call.ended, 4) and
      bob.call.ended and alice.__dict__.get("_remote_retry") is None)

print("\nA session that drops is placed again, and answered again")
settle(lambda: False, 1.6)                  # the screens close
M.REMOTE_RETRY_EVERY = 0.3
placed = alice.connect_remote(B)


def call_states():
    """Each side's call, for a check that fails: (state, why, remote)."""
    return [(c.state, getattr(c, "why", None), c.remote) if c else None
            for c in (alice.call, bob.call)] + [
                placed, alice.call_blocker(B, remote=True)]


check("connected again", settle(lambda: alice.controlling(), 6),
      call_states())
first_call = alice.call
if first_call is not None:
    alice._on_call_engine(first_call.id, "lost", None)   # the network went
check("dropped: placed again by itself, and controlling again",
      settle(lambda: alice.call is not None and alice.call is not first_call
             and alice.controlling(), 10) and
      alice.__dict__.get("_remote_retry") is None, call_states())
M.REMOTE_RETRY_FOR = 0.0
said = []
alice._set_status = lambda text, *a, **k: said.append(text)
dropped = alice.call
if dropped is not None:
    alice._on_call_engine(dropped.id, "lost", None)
pump(parties)
check("past its ten minutes, it gives up and says so",
      alice.__dict__.get("_remote_retry") is None and
      any("not back" in t for t in said), said)
del alice._set_status
M.REMOTE_RETRY_FOR = 600.0
settle(lambda: bob.call is None or bob.call.ended, 4)
settle(lambda: False, 1.6)

print("\nAn ordinary call: control asked, allowed here, ended by the screen")
voices = []
M.call_audio = lambda: voices.append(M.CallAudio()) or voices[-1]
M.remote_access_revoke(B, A)
asked = []
real_ask = M.ControlAskSheet


class AskSheet:
    def __init__(self, app, call):
        self.app, self.call = app, call
        asked.append(self)

    def open(self):
        pass

    def _answer(self, yes):
        real_ask._answer(self, yes)

    def dismiss(self):
        pass


M.ControlAskSheet = AskSheet
M.calls_possible_here = lambda: True      # a microphone, as if
alice.start_call(B)
lobby = alice.__dict__.get("_lobby")
if lobby is not None:
    lobby.join()
settle(lambda: bob.call is not None and bob.call.state == "incoming")
bob.answer_call()
lobby = bob.__dict__.get("_lobby")
if lobby is not None:
    lobby.join()
check("a voice call between them", settle(
    lambda: alice.call.state == "active" and bob.call.state == "active"),
    (alice.call.state, bob.call.state))
check("no Control offered before bob shares his screen",
      not alice.can_ask_control())
bob.toggle_call_screen()
check("bob shares it: Control offered to alice",
      settle(lambda: alice.can_ask_control(), 4))
alice.toggle_control()
check("alice asks: bob's computer asks him first",
      settle(lambda: bool(asked), 4) and not bob.call.lets)
asked[-1]._answer(True)
check("he allows it: alice controls his computer",
      settle(lambda: alice.controlling() and bob.call.lets, 4))
alice.send_remote_input(KEY, (key("ctrl"), 1))
check("her Ctrl is held there", settle(lambda: ("key", "ctrl", True) in
                                       bob_done(), 4), bob_done())
held_by = bob.call.engine.input_in.injector
bob.toggle_call_screen()
check("bob stops sharing his screen: control ends with it, Ctrl let go, "
      "alice told and asking no more",
      settle(lambda: not alice.controlling() and not alice.call.asks, 4) and
      not bob.call.lets and ("key", "ctrl", False) in held_by.done)
check("the session is kept as allowed here",
      M.remote_sessions()[0]["kind"] == "asked")
bob.toggle_call_screen()
settle(lambda: alice.can_ask_control(), 4)
alice.toggle_control()
settle(lambda: len(asked) == 2, 4)
asked[-1]._answer(False)
settle(lambda: False, 0.5)
check("Not now: nothing is controlled, alice still asking",
      not bob.call.lets and not alice.controlling() and alice.call.asks)
alice.end_call()
check("the call over: nothing left letting or asking",
      settle(lambda: bob.call is None or bob.call.ended, 4) and
      not bob.call.lets)
M.ControlAskSheet = real_ask

print("\nRemote access in Settings")
M.remote_access_grant(B, A, 7)
M.remote_session_kept(A, "away", time.time() - 600, time.time())
sheet = M.RemoteAccessSheet(bob)
texts = [w.text for w in sheet.walk() if hasattr(w, "text") and
         isinstance(getattr(w, "text"), str)]
check("the sheet lists alice, the checklist and the last session",
      any("alice" in t for t in texts) and
      any("Signs in by itself" in t for t in texts) and
      any("BEFORE YOU LEAVE" in t.upper() for t in texts) and
      any("10 min" in t for t in texts), texts[:12])
sheet._remove(A)
check("Remove takes her off the list", not M.remote_access_allows(B, A))
menus.clear()
M.MenuSheet = Menu
sheet._pick()
sheet._chosen(A)
bob.store.set_verified(A, True)
sheet._chosen(A)
M.MenuSheet = real_menu
check("adding: alice offered, verified first, then how long",
      len(menus) == 3 and "alice" in menus[0] and
      any("Verify" in t for t in menus[1]) and
      "Until I remove them" in menus[2], menus)
sheet._grant(A, 30)
check("granted for thirty days", abs(M.remote_access_list(B)[0]["until"] -
                                     (time.time() + 30 * 86400)) < 5)

print("\nA remote session for another identity on this computer")
# bob's computer holds a second identity, connected through its own link
# (IDCALLS): that identity's access list decides, not the one in use.
B2 = "Fbobtwo" + "5" * 27


class StandInLink:
    """Another identity's connection (IdentityLink), as far as an offer
    needs one: its keys, store and relay, and what it sends."""

    def __init__(self, address):
        self.keys = types.SimpleNamespace(address=address, username="bob2")
        self.store = M.LocalStore(os.path.join(os.environ["HOME"], "b2.db"))
        self.api, self.sent = None, []

    def _send_control_to(self, peer, ctl, text):
        self.sent.append((peer, ctl))


link = StandInLink(B2)
link.store.upsert_contact(A, "alice", alice.keys.public_key_hex)
bob.__dict__["_links"] = {B2: link}
switched, stopped, rang = [], [], []
bob._activate_identity = switched.append
bob._stop_link = stopped.append
bob._wait_call = lambda sender, sig, link=None: rang.append(sig) or True


def other_offer(n, rc=True, age=0.0):
    return M.sanitize_call_signal("call", dict(
        offer, id=f"{n:032x}", rc=rc, t=int(time.time() - age)))


bob._identity_call(B2, A, other_offer(1))
lines = [m["body"] for m in link.store.messages_for(A)]
check("not on that identity's list: set aside, no ring, a line in its "
      "chat", not switched and not rang and not M.take_call_offer(B2) and
      any("tried to connect" in t for t in lines), lines)
M.remote_access_grant(B2, A, 1)
bob._identity_call(B2, A, other_offer(2))
kept = M.take_call_offer(B2)
check("on its list: the app switches to it, which answers the offer kept "
      "for it, still a remote session",
      switched == [B2] and stopped == [B2] and not rang and kept and
      kept[0] == A and kept[1]["rc"] is True, (switched, kept))
bob.call = types.SimpleNamespace(ended=False)
bob._identity_call(B2, A, other_offer(3))
check("a call on here: busy, told through its connection",
      wait_until(lambda: link.sent, 3) and
      link.sent[-1][1]["why"] == "busy" and switched == [B2], link.sent)
bob.call = None
bob._identity_call(B2, A, other_offer(4, age=M.CALL_OFFER_MAX_AGE + 60))
check("an offer from while it was off: nothing", switched == [B2] and
      not M.take_call_offer(B2) and not rang)
bob._identity_call(B2, A, other_offer(5, rc=False))
check("an ordinary call for it still waits in the bar, as before",
      len(rang) == 1 and switched == [B2])
for name in ("_activate_identity", "_stop_link", "_wait_call"):
    del bob.__dict__[name]
bob.__dict__.pop("_links", None)

# ---- 19.0.1: for the long term (the owner's note of 22 November) ------------
print("\n19.0.1: one computer of several, and an old offer played again")
now = time.time()
here = M.device_id()
check("a computer has a number of its own, kept",
      len(here) == 16 and M.device_id() == here)
real_mark = M._machine_mark
M._machine_mark = lambda: "another machine"
copied = M.device_id()
M._machine_mark = real_mark
check("its data folder copied to another computer: a new number there",
      copied != here and len(copied) == 16)
M.save_pref("device", None)
here = M.device_id()
check("an offer naming this computer, or none, is for it",
      M.remote_offer_for_here({"t": now, "to": here}) and
      M.remote_offer_for_here({"t": now}))
check("one naming another computer of the identity is not",
      not M.remote_offer_for_here({"t": now, "to": "0" * 16}))
check("one signed eleven minutes ago, or ahead, or not at all, is not: "
      "whatever time the relay says it took it",
      not M.remote_offer_for_here({"t": now - 660}) and
      not M.remote_offer_for_here({"t": now + 660}) and
      not M.remote_offer_for_here({}))
check("a computer whose own clock drifted 20 minutes from the relay's (months "
      "alone) is judged by the relay's: a fresh offer is answered, an old one "
      "not", M.remote_offer_for_here({"t": now}, now=now + 1200,
                                     relay_now=now) and
      not M.remote_offer_for_here({"t": now - 660}, now=now + 1200,
                                  relay_now=now))
check("…and one whose clock agrees with the relay's is judged by its own",
      not M.remote_offer_for_here({"t": now - 660}, now=now,
                                  relay_now=now - 600))
check("the same offer is answered once", M.remote_offer_once("e" * 32) and
      not M.remote_offer_once("e" * 32))
check("the wire keeps a computer's number, a wrong one dropped",
      M.sanitize_call_signal("call", dict(offer, rc=True, to=here))["to"] ==
      here and "to" not in M.sanitize_call_signal(
          "call", dict(offer, rc=True, to=here + "0")) and
      "to" not in M.sanitize_call_signal("call", dict(offer, rc=False,
                                                      to=here)))
answer = M.sanitize_call_signal("answer", {
    "id": "a" * 32, "k": offer["k"], "c": [], "dev": here,
    "dn": "Home\nPC\x00" + "x" * 90})
check("…and its name, one printable line of 40 characters at most",
      answer["dev"] == here and "\n" not in answer["dn"] and
      len(answer["dn"]) == 40 and answer["dn"].startswith("Home PC"),
      answer)

print("\n19.0.1: the session names its computer")
M.REMOTE_RETRY_EVERY = 0.3
M.save_pref("remote_computers", None)
M.remote_access_grant(B, A, 7)
M.save_pref("device_name", "Home PC")
settle(lambda: alice.call is None or alice.call.ended, 3)
alice.call = None
alice.connect_remote(B)
check("connected, bob's computer said which it is, and alice keeps it",
      settle(lambda: alice.controlling() and bob.call.lets, 6) and
      [c["dev"] for c in M.remote_computers(A, B)] == [here] and
      M.remote_computers(A, B)[0]["n"] == "Home PC",
      M.remote_computers(A, B))
check("bob's computer tells alice its state: its screen usable, its name, "
      "the grant's end", settle(lambda: alice.call.rc_state is not None, 4)
      and alice.call.rc_state["s"] == "ok" and
      alice.call.rc_state["n"] == "Home PC" and
      abs(alice.call.rc_state["u"] - (now + 7 * 86400)) < 60,
      alice.call.rc_state)
check("alice's call screen: Keys while she controls",
      alice._call_overlay is not None and
      alice._call_overlay.keys_button.parent is not None)
os.environ["FEXT_REMOTE_SCREEN"] = "admin"
check("a window run as administrator in front: alice is told, under the "
      "band", settle(lambda: alice.call.rc_state["s"] == "admin", 8) and
      settle(lambda: "administrator" in
             alice._call_overlay.remote_note_words.text, 2) and
      alice._call_overlay.remote_note.opacity == 1,
      alice._call_overlay.remote_note_words.text)
os.environ["FEXT_REMOTE_SCREEN"] = "secure"
check("a Windows security screen: alice is told it cannot be used from "
      "afar", settle(lambda: "security screen" in
                     alice._call_overlay.remote_note_words.text, 8))
os.environ["FEXT_REMOTE_SCREEN"] = "ok"
check("usable again: the note now says the access ends within two weeks "
      "(its grant was for seven days)",
      settle(lambda: "ends" in alice._call_overlay.remote_note_words.text and
             "security" not in alice._call_overlay.remote_note_words.text, 8),
      alice._call_overlay.remote_note_words.text)
M.remote_access_grant(B, A, 0)
bob.call.__dict__.pop("rc_mine", None)    # read again now, not in 30 s
check("…and once it is until removed, the note goes",
      settle(lambda: not alice._call_overlay.remote_note_words.text, 10) and
      alice._call_overlay.remote_note.opacity == 0,
      alice._call_overlay.remote_note_words.text)

print("\n19.0.1: Data saver, the keys, the programs, a restart")
alice.set_remote_saver(True)
check("Data saver on: bob's computer holds its screen to 1.5 Mbit/s",
      settle(lambda: bob.call.engine.screen_cap == M.REMOTE_SAVER_KBPS, 4))
alice.set_remote_saver(False)
check("…and off: no limit", settle(lambda: bob.call.engine.screen_cap == 0,
                                   4))
start = len(bob_done())
alice.remote_keys(("ctrl", "shift", "escape"))
check("Task Manager's keys: each down in order, then up in reverse",
      settle(lambda: len(bob_done()) >= start + 6, 4) and
      bob_done()[start:start + 6] == [
          ("key", "ctrl", True), ("key", "shift", True),
          ("key", "escape", True), ("key", "escape", False),
          ("key", "shift", False), ("key", "ctrl", False)],
      bob_done()[start:])
M._REMOTE_TEST_PROGRAMS[:] = [
    {"w": 100 + n, "p": 10 + n, "t": f"Window {n}", "x": f"app{n}.exe",
     "a": n == 2, "h": n == 3} for n in range(8)]
shown = []
real_show = M.RemoteProgramsSheet.show
M.RemoteProgramsSheet.show = classmethod(
    lambda cls, app, programs: shown.append(programs))
alice.remote_programs()
check("Programs: bob's computer's windows, in two parts put together",
      settle(lambda: shown, 4) and [p["t"] for p in shown[-1]] ==
      [f"Window {n}" for n in range(8)] and shown[-1][2]["a"] and
      shown[-1][3]["h"], shown[-1:])
said = []
alice._set_status = lambda text, *a, **k: said.append(text)
alice.remote_ask({"op": "end", "w": 103, "p": 13})
check("one not answering ended, and the list asked for again",
      settle(lambda: len(shown) >= 2, 5) and
      103 not in [p["w"] for p in shown[-1]] and
      any("close" in t.lower() for t in said), (said, shown[-1:]))
alice.remote_ask({"op": "end", "w": 102, "p": 12})
check("one run as administrator: not closed, and why",
      settle(lambda: any("administrator" in t for t in said), 4) and
      102 in [p["w"] for p in M._REMOTE_TEST_PROGRAMS], said)
before = len(M._REMOTE_TEST_RESTARTS)
waiting = alice.call.rc_waiting
alice.remote_ask({"op": "restart"})
check("Restart: bob's computer restarts, says who asked, and alice waits "
      "for it", settle(lambda: alice.call.restarting, 4) and
      len(M._REMOTE_TEST_RESTARTS) == before + 1 and
      "alice" in M._REMOTE_TEST_RESTARTS[-1] and
      M.load_prefs()["remote_restart"]["by"] == A and not waiting)
restarted = alice.call
bob.end_call()                      # it goes down
check("its call ends: alice keeps placing it, for longer, and says why",
      settle(lambda: alice.__dict__.get("_remote_retry") is not None, 4) and
      alice.__dict__["_remote_retry"]["restart"] and
      alice.__dict__["_remote_retry"]["to"] == here and
      any("restart and sign in" in t for t in said), said[-3:])
check("…and is controlling again once it answers",
      settle(lambda: alice.call is not restarted and alice.controlling(), 10)
      and alice.call.session.remote_to == here)
took = M.remote_restart_back(M.load_prefs()["remote_restart"]["at"] + 95)
check("its next start says how long it took, for Remote access",
      took is not None and abs(took - 95) < 1 and
      M.load_prefs()["remote_back"]["took"] == took and
      M.load_prefs().get("remote_restart") is None)
del alice._set_status
M.RemoteProgramsSheet.show = real_show
alice.end_call()
settle(lambda: bob.call is None or bob.call.ended, 4)
settle(lambda: False, 1.6)

print("\n19.0.1: another computer named, the app lock, the checklist")
fake = "1" * 16
M.remote_computer_seen(A, B, fake, "Office PC", now=time.time() + 5)
opened = []
real_pick = M.RemoteComputersSheet
M.RemoteComputersSheet = lambda app, peer, known: types.SimpleNamespace(
    open=lambda: opened.append([c["n"] for c in known]))
check("two computers known: alice is asked which",
      alice.connect_remote(B) is False and
      opened == [["Office PC", "Home PC"]], opened)
M.RemoteComputersSheet = real_pick
alice.connect_remote(B, to=fake, chosen=True)
settle(lambda: False, 1.5)
check("one named that is not bob's: bob's computer sets the offer aside",
      bob.call is None or bob.call.ended)
alice.end_call()
settle(lambda: not mail.mail)
settle(lambda: False, 1.0)
M.AppLock.turn_on("4321", 0)
alice.call = None
alice.connect_remote(B, to=here, chosen=True)
check("the app lock on: a session from afar finds Ferritext locked",
      settle(lambda: bob.call is not None and bob.call.lets, 6) and
      bob.locked, (bob.call and bob.call.lets, bob.locked))
M.AppLock.turn_off()
bob.unlocked()
alice.end_call()
settle(lambda: bob.call is None or bob.call.ended, 4)
rows = {key: (ok, words) for key, ok, _t, words in M.remote_checklist()}
check("Before you leave: what it takes, read from the computer",
      {"start", "keepup", "signin", "power", "tried", "back"} <= set(rows)
      and rows["tried"][0] is True and rows["back"][0] is True and
      "1 min 35 s" in rows["back"][1] and rows["power"][0] is None, rows)


class Reg:
    """winreg's calls the readers make, over a dict of keys."""
    HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER = "HKLM", "HKCU"

    def __init__(self, keys):
        self.keys = keys

    def OpenKey(self, root, path):
        if (root, path) not in self.keys:
            raise OSError(path)
        return Key(self.keys[(root, path)])

    @staticmethod
    def QueryValueEx(key, name):
        if name not in key:
            raise OSError(name)
        return key[name], 1


class Key(dict):
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


WINLOGON = (r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon")
os.environ["USERNAME"] = "Bob"
check("Windows signs in by itself: netplwiz's setting for this person, or "
      "not", M.windows_signs_in_by_itself(Reg({("HKLM", WINLOGON): {
          "AutoAdminLogon": "1", "DefaultUserName": "bob"}})) is True and
      M.windows_signs_in_by_itself(Reg({("HKLM", WINLOGON): {
          "AutoAdminLogon": "1", "DefaultUserName": "someone"}})) is False
      and M.windows_signs_in_by_itself(Reg({("HKLM", WINLOGON): {
          "AutoAdminLogon": "0"}})) is False and
      M.windows_signs_in_by_itself(Reg({})) is False)
DESK = r"Control Panel\Desktop"
POLICY = r"Software\Policies\Microsoft\Windows\Control Panel\Desktop"
check("the screen saver that asks for sign-in, its minutes; a policy's word "
      "first; Dynamic lock; nothing",
      M.windows_screen_locks_itself(Reg({("HKCU", DESK): {
          "ScreenSaveActive": "1", "ScreenSaverIsSecure": "1",
          "ScreenSaveTimeOut": "600"}})) == 10 and
      M.windows_screen_locks_itself(Reg({("HKCU", DESK): {
          "ScreenSaveActive": "1", "ScreenSaverIsSecure": "1",
          "ScreenSaveTimeOut": "600"}, ("HKCU", POLICY): {
          "ScreenSaveActive": "0"}})) == 0 and
      M.windows_screen_locks_itself(Reg({("HKCU", (
          r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon")): {
          "EnableGoodbye": 1}})) == -1 and
      M.windows_screen_locks_itself(Reg({})) == 0)

print("\n19.0.1: reminders before an access ends; what is read off the wire")
M.save_pref("remote_reminded", None)
M.remote_computer_seen(A, B, here, "Home PC", until=time.time() + 3 * 86400)
check("an access ending in three days: said once in the chat",
      alice.remote_reminders() == 1 and alice.remote_reminders() == 0 and
      any("ends" in (m.get("body") or "") and "Home PC" in m["body"]
          for m in alice.store.messages_for(B)))
check("…and again on its last day",
      alice.remote_reminders(time.time() + 2.5 * 86400) == 1 and
      alice.remote_reminders(time.time() + 2.6 * 86400) == 0)
state = M.sanitize_remote_state({"op": "state", "s": "evil", "n": "x" * 99,
                                 "dev": "zz", "u": True, "si": "yes"})
check("a computer's state, every field checked",
      state == {"s": "ok", "n": "x" * 40, "dev": None, "u": None,
                "si": None}, state)
check("a list of programs, every field checked, 30 at the most",
      len(M.sanitize_programs([{"w": 1, "p": 2, "t": "t", "x": "x"}] * 40))
      == 30 and M.sanitize_programs([{"w": "1", "p": 2}, None, 3]) == [])
check("a word too long, not JSON, or without a short op: refused",
      M.parse_rcmd(b"{}" + b" " * M.RCMD_BYTES) is None and
      M.parse_rcmd(b"\xff") is None and
      M.parse_rcmd(b'{"op": "' + b"x" * 20 + b'"}') is None and
      M.parse_rcmd(b'{"op": "list", "n": 1}') == {"op": "list", "n": 1})
check("a word only in its own role: a computer not let in does nothing it "
      "is asked", bob._remote_cmd(types.SimpleNamespace(
          lets=False, asks=False, engine=None), {"op": "restart", "n": 9})
      is None and len(M._REMOTE_TEST_RESTARTS) == before + 1)

print("\n19.0.1: one Ferritext at a time, and the watcher (keep running)")
base = tempfile.mkdtemp(prefix="fext-guard-")
first = M.hold_lock(Path(base) / "instance.lock")
check("one Ferritext holds its data folder; a second is refused",
      first is not None and M.hold_lock(Path(base) / "instance.lock") is None)
first.close()
check("…and it is free again once the first has gone",
      M.hold_lock(Path(base) / "instance.lock") is not None)
fake_app = Path(base) / "fake_app.py"
fake_app.write_text(
    "import json, os, sys, time\n"
    "base = sys.argv[1]\n"
    "mode = open(os.path.join(base, 'mode')).read().strip()\n"
    "open(os.path.join(base, 'starts'), 'a').write(f'{os.getpid()}\\n')\n"
    "json.dump({'pid': os.getpid(), 'cmd': [sys.executable, __file__, base],"
    " 'quit': False}, open(os.path.join(base, 'keepup.json'), 'w'))\n"
    "end = time.time() + 40\n"
    "while time.time() < end:\n"
    "    if mode == 'crash':\n"
    "        sys.exit(3)\n"
    "    if mode == 'beat':\n"
    "        open(os.path.join(base, 'keepup.beat'), 'w').write('1')\n"
    "    time.sleep(0.2)\n")
GUARD_ENV = dict(os.environ, FEXT_GUARD_QUICK="0.2,1.5,0.6,0.4,0.4,0.4")


def guard_dir(mode):
    folder = Path(tempfile.mkdtemp(prefix="fext-guard-"))
    (folder / "mode").write_text(mode)
    return folder


def starts(folder):
    try:
        return [int(n) for n in (folder / "starts").read_text().split()]
    except OSError:
        return []


def watcher(folder):
    return subprocess.Popen([sys.executable, sys.argv[1] if len(sys.argv)
                               > 1 else "main.py", M.GUARD_FLAG, str(folder)],
                              env=GUARD_ENV)


folder = guard_dir("crash")
(folder / "keepup.json").write_text(json.dumps({
    "pid": 999999, "cmd": [sys.executable, str(fake_app), str(folder)],
    "quit": False}))
guard = watcher(folder)
check("Ferritext gone without being closed (a crash): the watcher starts "
      "it again, and again", wait_until(lambda: len(starts(folder)) >= 2, 15),
      starts(folder))
second = watcher(folder)
check("a second watcher for the same folder ends at once",
      wait_until(lambda: second.poll() is not None, 10))
(folder / "keepup.json").write_text(json.dumps({
    "pid": 999999, "cmd": [sys.executable, str(fake_app), str(folder)],
    "quit": True}))
check("closed by its person: the watcher ends, and starts nothing more",
      wait_until(lambda: guard.poll() is not None, 10))
folder = guard_dir("hang")
hung = subprocess.Popen([sys.executable, str(fake_app), str(folder)])
wait_until(lambda: starts(folder), 5)
(folder / "keepup.beat").write_text("1")          # its last beat, long ago
os.utime(folder / "keepup.beat", (time.time() - 100, time.time() - 100))
guard = watcher(folder)
check("Ferritext that stops answering (no beat): ended, then started again",
      wait_until(lambda: len(starts(folder)) >= 2, 20) and
      wait_until(lambda: hung.poll() is not None, 5), starts(folder))
(folder / "mode").write_text("beat")
count = len(starts(folder))
wait_until(lambda: False, 3)
check("one that answers is left alone",
      len(starts(folder)) <= count + 1 and guard.poll() is None,
      starts(folder))
data = json.loads((folder / "keepup.json").read_text())
(folder / "keepup.json").write_text(json.dumps(dict(data, quit=True)))
wait_until(lambda: guard.poll() is not None, 10)
for pid in starts(folder):
    try:
        os.kill(pid, 9)
    except OSError:
        pass
if os.name == "nt":
    # A one-file EXE runs an unpacked copy of itself, under the name it was
    # built with: a download renamed "… (1).exe" is known by that copy.
    M.guard_tell([r"C:\Downloads\Ferritext-19.0.1-windows (1).exe"], True)
    told = json.loads((M.DATA_DIR / "keepup.json").read_text("utf-8"))
    mine = M._process_image(os.getpid()) or ""
    check("Windows: keepup.json names the program file Ferritext runs, so "
          "that a renamed download is still known to the watcher",
          mine and told.get("image", "").lower() == mine.lower() and
          M._guard_alive(os.getpid(), told["cmd"], told["image"]), told)

# ---- the computer's own injector ------------------------------------------------------
os.environ.pop("FEXT_REMOTE_TEST", None)
if sys.platform.startswith("linux") and os.environ.get("DISPLAY") and \
        ctypes.util.find_library("Xtst"):
    print("\nX11: the pointer, a button, a key and typed text (Xvfb)")
    from kivy.core.window import Window
    from kivy.uix.textinput import TextInput
    x11 = ctypes.CDLL(ctypes.util.find_library("X11"))
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XQueryPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + \
        [ctypes.POINTER(ctypes.c_ulong)] * 2 + \
        [ctypes.POINTER(ctypes.c_int)] * 4 + [ctypes.POINTER(ctypes.c_uint)]
    x11.XGetKeyboardMapping.restype = ctypes.POINTER(ctypes.c_ulong)
    x11.XGetKeyboardMapping.argtypes = [ctypes.c_void_p, ctypes.c_ubyte,
                                        ctypes.c_int,
                                        ctypes.POINTER(ctypes.c_int)]
    x11.XFree.argtypes = [ctypes.c_void_p]
    x11.XDisplayWidth.argtypes = x11.XDisplayHeight.argtypes = \
        [ctypes.c_void_p, ctypes.c_int]
    dpy = x11.XOpenDisplay(None)
    root = x11.XDefaultRootWindow(dpy)
    width, height = x11.XDisplayWidth(dpy, 0), x11.XDisplayHeight(dpy, 0)

    def pointer():
        r, c = ctypes.c_ulong(), ctypes.c_ulong()
        at = [ctypes.c_int() for _ in range(4)]
        mask = ctypes.c_uint()
        x11.XQueryPointer(dpy, root, ctypes.byref(r), ctypes.byref(c),
                          *(ctypes.byref(v) for v in at), ctypes.byref(mask))
        return at[0].value, at[1].value, mask.value

    real = M.X11Injector()
    check("the computer's own way in is X11's here",
          isinstance(M.remote_injector(), M.X11Injector))
    # The server takes the injector's requests on their own connection:
    # what this one reads back may come a moment before them.
    real.move(0, 0)
    wait_until(lambda: pointer()[:2] == (0, 0), 1)
    corner = pointer()
    real.move(65535, 65535)
    wait_until(lambda: pointer()[:2] == (width - 1, height - 1), 1)
    far = pointer()
    check("the pointer to either corner of the whole screen",
          corner[:2] == (0, 0) and far[:2] == (width - 1, height - 1),
          (corner, far, width, height))
    real.button(1, True, 32768, 32768)
    held = wait_until(lambda: pointer()[2] & 256, 1)
    pressed = pointer()
    real.button(1, False, 32768, 32768)
    check("the left button held, then let go",
          held and wait_until(lambda: not pointer()[2] & 256, 1) and
          abs(pressed[0] - (width - 1) / 2) <= 1, (pressed, pointer()))
    real.key("shift", True)
    shifted = wait_until(lambda: pointer()[2] & 1, 1)
    real.key("shift", False)
    check("Shift held, then let go", shifted and
          wait_until(lambda: not pointer()[2] & 1, 1))
    clicks = []
    Window.bind(on_mouse_down=lambda _w, x, y, b, _m: clicks.append(
        ("down", round(x), round(y), b)), on_mouse_up=lambda _w, x, y, b,
        _m: clicks.append(("up", round(x), round(y), b)))

    def tick():
        Clock.tick()
        Window.mainloop()               # the window's own events, read

    def share(x, y):
        """A point of the window as a share of the whole screen."""
        return (round((Window.left + x) * 65535 / (width - 1)),
                round((Window.top + y) * 65535 / (height - 1)))
    for _ in range(10):
        tick()
    real.button(1, True, *share(100, 50))
    real.button(1, False, *share(100, 50))
    wait_until(lambda: len(clicks) >= 2, 3, tick=tick)
    check("a click arrives in a window, where it was aimed",
          clicks[:2] == [("down", 100, 50, "left"), ("up", 100, 50, "left")],
          clicks)
    field = TextInput(size_hint=(None, None), size=Window.size)
    Window.add_widget(field)
    field.focus = True
    typed = "Hello, World! ö中ö文中"
    real.move(*share(30, 30))
    for _ in range(5):
        tick()
    real.text(typed)
    wait_until(lambda: field.text == typed, 3, tick=tick)
    check("typed text arrives in a window: capitals, signs, and letters the "
          "layout lacks, each on a spare key of its own",
          field.text == typed and len(set(real._mapped.values())) == 3,
          (field.text, real._mapped))
    spares = list(real._mapped.values())
    real.close()
    per = ctypes.c_int()

    def first_sym(code):
        syms = x11.XGetKeyboardMapping(dpy, code, 1, ctypes.byref(per))
        try:
            return syms[0]
        finally:
            x11.XFree(syms)
    check("closed: the spare keys put back as no key",
          spares and all(first_sym(code) == 0 for code in spares))
    Window.remove_widget(field)
elif sys.platform == "win32":
    print("\nWindows: the cursor moved and read back (SendInput)")
    user32 = ctypes.windll.user32

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    real = M.WindowsInjector()
    check("the computer's own way in is Windows' here",
          isinstance(M.remote_injector(), M.WindowsInjector))
    left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    wide, high = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    real.move(0, 0)
    time.sleep(0.2)
    got = POINT()
    user32.GetCursorPos(ctypes.byref(got))
    real.move(32768, 32768)
    time.sleep(0.2)
    mid = POINT()
    user32.GetCursorPos(ctypes.byref(mid))
    check("the cursor to the desktop's corner and middle (every screen)",
          (got.x, got.y) == (left, top) and
          abs(mid.x - (left + wide / 2)) <= 2 and
          abs(mid.y - (top + high / 2)) <= 2,
          ((got.x, got.y), (mid.x, mid.y), (left, top, wide, high)))
else:
    print("\n(no X11 with XTEST, and not Windows: the real injector is not "
          "tried here)")

passed = sum(1 for _n, ok in RESULTS if ok)
print(f"\n{passed}/{len(RESULTS)} passed")
os._exit(0 if passed == len(RESULTS) else 1)
