#!/usr/bin/env python3
"""Voice calls, from the codec up to two apps calling each other.

    xvfb-run -a python3 desktop/test_calls.py main.py

The pieces first — the codec (identical to audioop's, where Python still
has it), the packet seal, the jitter buffer, the signals' validation —
then two call engines on this machine finding each other three ways:
directly, through a stand-in for the relay's call port, and over the
message link. Last, two real app controllers set calls up through the
in-memory relay the other suites use: ring, answer, talk, hang up; decline,
busy, cancel, no answer, an offer that arrives too late, a contact who
cannot take calls; and the call screen itself.

The sound devices are stand-ins (a tone in, a recorder out), so this runs
anywhere; the real ones are exercised on a phone.
"""
import importlib.util
import math
import os
import socket
import struct
import sys
import tempfile
import threading
import time

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-calls-")
os.environ["FEXT_SERVER_HOST"] = "127.0.0.1:1"
os.environ["FEXT_CALL_LOOPBACK"] = "1"         # this machine is "the LAN"

_spec = importlib.util.spec_from_file_location(
    "fext_calls", sys.argv[1] if len(sys.argv) > 1 else "main.py")
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_calls"] = M
_spec.loader.exec_module(M)

import array  # noqa: E402

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


def tone(freq, frames, amp=8000, start=0):
    out = array.array("h", (int(amp * math.sin(2 * math.pi * freq * n / 16000))
                            for n in range(start, start + 320 * frames)))
    return out.tobytes()


class FakeAudio(M.CallAudio):
    """A microphone that hears a steady tone, in real time; a speaker that
    keeps what it is given."""

    name = "fake"

    def __init__(self, freq=440):
        self.freq, self.played, self._sent = freq, [], 0
        self._t0 = None

    def start(self):
        self._t0 = time.monotonic()

    def read(self):
        due = int((time.monotonic() - self._t0) * 50)
        out = []
        while self._sent < due:
            out.append(tone(self.freq, 1, start=self._sent * 320))
            self._sent += 1
        return out[-10:]

    def write(self, pcm):
        self.played.append(pcm)

    def queued(self):
        return 0


def dominant(pcm):
    """The loudest of a few test frequencies in some PCM."""
    s = array.array("h", pcm)
    best, which = 0, None
    for f in (440, 660):
        re = sum(v * math.cos(2 * math.pi * f * n / 16000)
                 for n, v in enumerate(s))
        im = sum(v * math.sin(2 * math.pi * f * n / 16000)
                 for n, v in enumerate(s))
        power = re * re + im * im
        if power > best:
            best, which = power, f
    return which


print("\nThe codec")
speech = tone(220, 50, 9000)
codec = M.ImaCodec()
frames = [codec.encode(speech[i:i + 640]) for i in range(0, len(speech), 640)]
check("20 ms of speech is 163 bytes (ADPCM, 4 bits a sample)",
      all(len(f) == 163 for f in frames))
decoded = b"".join(M.ImaCodec.decode(f) for f in frames)
a, b = array.array("h", speech), array.array("h", decoded)
snr = 10 * math.log10(sum(x * x for x in a) /
                      max(1, sum((x - y) ** 2 for x, y in zip(a, b))))
check("...and comes back clearly (over 20 dB signal to noise)", snr > 20,
      f"{snr:.1f} dB")
alone = M.ImaCodec.decode(frames[17])
check("each frame decodes on its own, so a lost one costs only itself",
      alone == decoded[17 * 640:18 * 640])
codes, state = M._ima_encode_py(speech, (0, 0))
back, _ = M._ima_decode_py(codes, (0, 0))
if M._audioop is not None:
    check("the pure-Python coder matches audioop's, bit for bit",
          (codes, state) == M._audioop.lin2adpcm(speech, 2, (0, 0)) and
          back == M._audioop.adpcm2lin(codes, 2, (0, 0))[0])
else:
    check("the pure-Python coder round-trips (no audioop here)",
          len(back) == len(speech))
check("a frame claiming an impossible decoder state is refused",
      M.ImaCodec.decode(struct.pack("<hB", 0, 99) + bytes(160)) is None and
      M.ImaCodec.decode(b"short") is None)

print("\nOpus (v11)")
lib = M.opus_library()
if lib is None:
    check("libopus is here to test with (FEXT_OPUS, or the system's)",
          False, "install libopus0, or set FEXT_OPUS")
else:
    opus = M.OpusCodec()
    voice = tone(440, 60, 9000)
    packets = [opus.encode(voice[i:i + 640])
               for i in range(0, len(voice), 640)]
    check("20 ms of speech is a packet of about 60 bytes (24 kbit/s)",
          all(20 < len(p) < 110 for p in packets[5:]) and
          sum(len(p) for p in packets[5:]) / len(packets[5:]) < 80,
          str([len(p) for p in packets[5:10]]))
    out = [opus.decode(p) for p in packets]
    check("...and is heard again, the same voice",
          all(len(o) == 640 for o in out) and
          dominant(b"".join(out[20:30])) == 440)
    conceal = opus.decode(None)
    check("a lost packet is stood in for by Opus itself",
          conceal is not None and len(conceal) == 640 and any(conceal))
    opus.close()
    fresh = M.OpusCodec()
    for p in packets[:30]:
        fresh.decode(p)
    rebuilt = fresh.decode(packets[31], fec=True)       # 30 was lost
    check("...or rebuilt from the copy the next packet carries",
          rebuilt is not None and len(rebuilt) == 640 and
          dominant(rebuilt) == 440)
    fresh.close()
    check("both sides offer Opus first, ADPCM after",
          M.call_codecs() == ["opus16", "ima16"] and
          M.pick_send_codec(["opus16", "ima16"]) == "opus16" and
          M.pick_send_codec(["ima16"]) == "ima16" and
          M.pick_send_codec(None) == "ima16")
saved = M._OPUS_LIB
M._OPUS_LIB = False
check("without libopus a device offers and sends ADPCM only",
      M.call_codecs() == ["ima16"] and
      M.pick_send_codec(["opus16", "ima16"]) == "ima16")
M._OPUS_LIB = saved
ring = M.sanitize_call_signal("ringing", {
    "id": "a" * 32, "k": M.new_call_key()[1], "c": [],
    "codecs": ["opus16", "ima16", "mp3"]})
old_answer = M.sanitize_call_signal("answer", {
    "id": "a" * 32, "k": M.new_call_key()[1], "c": [], "codec": "ima16"})
check("ringing and answer say what the callee decodes; an older app's say "
      "nothing, which is ADPCM",
      ring["codecs"] == ["opus16", "ima16"] and
      old_answer["codecs"] == ["ima16"])

print("\nThe seal on every packet")
secret, tag = os.urandom(32), os.urandom(8)
caller = M.CallKeys(secret, "c" * 32, tag, caller=True)
callee = M.CallKeys(secret, "c" * 32, tag, caller=False)
packet = caller.seal(M.PKT_AUDIO, b"twenty ms", 1234)
got = callee.open(packet)
check("the other side opens it", got is not None and got[3] == b"twenty ms"
      and got[0] == M.PKT_AUDIO and got[2] == 1234)
check("...once: the same packet again is refused", callee.open(packet) is None)
check("a packet does not open for its own sender (a key per direction)",
      caller.open(caller.seal(M.PKT_PING, b"", 1)) is None)
bent = bytearray(caller.seal(M.PKT_AUDIO, b"x", 1))
bent[-3] ^= 1
check("an altered packet is refused", callee.open(bytes(bent)) is None)
head = bytearray(caller.seal(M.PKT_AUDIO, b"x", 1))
head[0] = M.PKT_BYE
check("...and so is one whose header was changed",
      callee.open(bytes(head)) is None)
other = M.CallKeys(secret, "d" * 32, tag, caller=False)
check("keys belong to one call", other.open(caller.seal(1, b"x", 1)) is None)
late = [caller.seal(M.PKT_AUDIO, bytes([i]), i) for i in range(20)]
check("packets out of order still open", all(callee.open(p) for p in
                                             reversed(late)))
check("the shared secret comes out the same on both sides",
      M.call_secret(*M.new_call_key()[:1], M.new_call_key()[1]) is not None)
k1, k2 = M.new_call_key(), M.new_call_key()
check("...from each side's own key and the other's",
      M.call_secret(k1[0], k2[1]) == M.call_secret(k2[0], k1[1]))

print("\nSmoothing out arrival")
jb = M.JitterBuffer(target=3)
jb.put(0, "f0")
check("it waits for a few frames before it starts", jb.get() is None)
for n in (2, 1):
    jb.put(n, f"f{n}")
check("...then plays them in order, whatever order they came in",
      [jb.get(), jb.get(), jb.get()] == ["f0", "f1", "f2"])
for n in (3, 5):
    jb.put(n, f"f{n}")
check("a frame that never came is concealed",
      jb.get() == "f3" and jb.get() is M.JitterBuffer.CONCEAL and
      jb.get() == "f5")
jb.put(4, "f4")
check("...and one that comes after its turn is dropped", jb.late == 1)
for n in range(6, 40):
    jb.put(n, f"f{n}")
check("frames piling up never hold more than a short delay", len(jb) <= jb.high)
dry = M.JitterBuffer(target=2)
for n in range(3):
    dry.put(n, n)
while dry.get() is not None and dry.get() is not M.JitterBuffer.CONCEAL:
    pass
before = dry.target
for burst in range(3):
    for n in range(10 + burst * 10, 12 + burst * 10):
        dry.put(n, n)
    while dry.get() not in (None,):
        pass
check("running dry twice in a short while buys a frame more of buffer",
      dry.target > before, f"{before} -> {dry.target}")

print("\nSignals")
key = M.new_call_key()[1]
offer = {"id": "a" * 32, "tag": "b" * 16, "k": key, "codecs": ["ima16"],
         "c": [["192.168.1.20", 50000], ["not an ip", 1], ["10.0.0.1", 0],
               ["224.0.0.1", 9]], "t": 1}
sig = M.sanitize_call_signal("call", offer)
check("a good offer passes, keeping only real addresses",
      sig is not None and sig["c"] == [["192.168.1.20", 50000]])
check("an offer without a usable codec, key or id is refused",
      M.sanitize_call_signal("call", dict(offer, codecs=["opus"])) is None and
      M.sanitize_call_signal("call", dict(offer, k="02" + "00" * 32)) is None
      and M.sanitize_call_signal("call", dict(offer, id="x")) is None)
check("an ending carries a reason from the list, or 'hangup'",
      M.sanitize_call_signal("end", {"id": "a" * 32, "why": "busy"})["why"]
      == "busy" and M.sanitize_call_signal(
          "end", {"id": "a" * 32, "why": "<script>"})["why"] == "hangup")


# ---- engines on this machine -------------------------------------------------
class StandInRelay(threading.Thread):
    """The relay's call port, as the client sees it: REG answered with the
    address seen; packets passed between the two ends of a tag."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.addr = self.sock.getsockname()
        self.ends, self.passed, self.stop = {}, 0, False

    def run(self):
        self.sock.settimeout(0.1)
        while not self.stop:
            try:
                data, src = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            tag = data[1:9]
            if data[0] == M.PKT_REG:
                role = data[9]
                self.ends.setdefault(tag, {})[role] = src
                self.sock.sendto(struct.pack("!B8sBB", M.PKT_REG_OK, tag, role,
                                             4) + socket.inet_aton(src[0]) +
                                 struct.pack("!HB", src[1], 1), src)
                continue
            ends = self.ends.get(tag, {})
            role = next((r for r, a in ends.items() if a == src), None)
            if role is not None and (1 - role) in ends:
                self.sock.sendto(data, ends[1 - role])
                self.passed += 1


def pair(relay_addr=None, direct=True, ws=False):
    """Two engines of one call, keyed and told where the other is."""
    cid, tg = os.urandom(16).hex(), os.urandom(8)
    events = {0: [], 1: []}
    s = os.urandom(32)
    engines = {}

    def ws_send(role):
        return lambda _tag, _role, packet: engines[1 - role].ws_packet(packet)
    for role in (0, 1):
        engines[role] = M.CallEngine(
            cid, tg, role, relay_addr, "ticket" if relay_addr else "",
            on_event=lambda k, v, r=role: events[r].append((k, v)),
            send_ws=ws_send(role) if ws else None,
            audio_factory=lambda r=role: FakeAudio(440 if r == 0 else 660))
        engines[role].set_keys(M.CallKeys(s, cid, tg, caller=role == 0))
    if direct:
        engines[0].add_candidates(engines[1].candidates())
        engines[1].add_candidates(engines[0].candidates())
    for e in engines.values():
        e.start()
    return engines, events


def talk(engines, seconds=1.0):
    for e in engines.values():
        e.start_audio()
    time.sleep(seconds)


def finish(engines):
    for e in engines.values():
        e.stop()
    for e in engines.values():
        e.join(timeout=2)


print("\nTwo engines on one machine")
engines, events = pair()
check("they find each other directly",
      wait_until(lambda: engines[0].path == "direct" and
                 engines[1].path == "direct", 3), str(engines[0].path))
talk(engines, 1.2)
heard0 = engines[0]._audio.played
heard1 = engines[1]._audio.played
check("each hears the other: speech flows both ways",
      len(heard0) > 20 and len(heard1) > 20, f"{len(heard0)}/{len(heard1)}")
check("...and it is the other side's voice, not its own",
      dominant(b"".join(heard1[-10:])) == 440 and
      dominant(b"".join(heard0[-10:])) == 660)
check("the round trip is measured, and short here",
      engines[0].rtt_ms is not None and engines[0].rtt_ms < 50,
      str(engines[0].rtt_ms))
check("first sound is reported (the call becomes active)",
      ("audio", None) in events[0] and ("audio", None) in events[1])
engines[1].muted = True
time.sleep(0.5)
n = len(engines[0]._audio.played)
time.sleep(0.4)
tail = b"".join(engines[0]._audio.played[n:])
check("muted, the other side hears silence (and no gap)",
      len(engines[0]._audio.played) > n and not any(tail))
engines[1].hangup()
check("hanging up tells the other side at once, on the call's own path",
      wait_until(lambda: ("bye", None) in events[0], 2))
finish(engines)

if M.opus_library() is not None:
    engines, events = pair()
    for e in engines.values():
        e.set_send_codecs(["opus16", "ima16"])
    wait_until(lambda: engines[0].path == "direct", 3)
    sent = {0: [], 1: []}
    for role, engine in engines.items():
        real = engine._send

        def spy(ptype, payload, r=role, real=real):
            if ptype == M.PKT_AUDIO and len(payload) > 5:
                sent[r].append(payload[4])
            return real(ptype, payload)
        engine._send = spy
    # One packet in ten lost on the way into side 1.
    real_put = engines[1]._jitter.put
    count = [0]

    def lossy(number, frame):
        count[0] += 1
        if count[0] % 10 != 3:
            real_put(number, frame)
    engines[1]._jitter.put = lossy
    talk(engines, 1.6)
    heard1 = engines[1]._audio.played
    check("two apps with Opus speak Opus to each other",
          sent[0] and set(sent[0][-20:]) == {M.CODEC_IDS["opus16"]} and
          set(sent[1][-20:]) == {M.CODEC_IDS["opus16"]},
          f"{set(sent[0][-20:])}/{set(sent[1][-20:])}")
    check("...each hearing the other's voice",
          dominant(b"".join(heard1[-10:])) == 440 and
          dominant(b"".join(engines[0]._audio.played[-10:])) == 660)
    steady = heard1[25:]
    silent = sum(1 for f in steady if not any(f))
    check("...with one packet in ten lost, no gap is heard",
          len(steady) > 30 and silent == 0, f"{silent} silent of "
          f"{len(steady)}")
    finish(engines)
    engines, events = pair()
    engines[0].set_send_codecs(["ima16"])          # their app: ADPCM only
    engines[1].set_send_codecs(["opus16", "ima16"])
    wait_until(lambda: engines[0].path == "direct", 3)
    talk(engines, 1.2)
    check("with an app that decodes only ADPCM, it is sent ADPCM — and "
          "heard both ways",
          engines[0].send_codec == "ima16" and
          engines[1].send_codec == "opus16" and
          dominant(b"".join(engines[1]._audio.played[-10:])) == 440 and
          dominant(b"".join(engines[0]._audio.played[-10:])) == 660)
    finish(engines)

relay = StandInRelay()
relay.start()
engines, events = pair(relay_addr=relay.addr, direct=False)
check("with no direct way, both register with the relay",
      wait_until(lambda: engines[0].relay_ok and engines[1].relay_ok, 3))
check("...and learn the address they were seen from",
      engines[0].reflexive is not None and
      ("reflexive", engines[0].reflexive) in events[0])
check("...which goes into what they offer the other side",
      list(engines[0].reflexive) in engines[0].candidates())
check("speech then goes through the relay",
      wait_until(lambda: engines[0].path == "relay", 3), str(engines[0].path))
talk(engines, 1.0)
check("...and is heard on the other side",
      len(engines[1]._audio.played) > 15 and relay.passed > 50,
      f"{len(engines[1]._audio.played)} frames, {relay.passed} passed")
finish(engines)

engines, events = pair(relay_addr=relay.addr, direct=True)
check("given both, the direct way wins",
      wait_until(lambda: engines[0].path == "direct" and
                 engines[1].path == "direct", 3), str(engines[0].path))
finish(engines)
relay.stop = True

engines, events = pair(relay_addr=None, direct=False, ws=True)
check("with UDP no use at all, speech goes over the message link",
      wait_until(lambda: engines[0].path == "ws", 3), str(engines[0].path))
talk(engines, 1.0)
check("...and is heard", len(engines[1]._audio.played) > 15)
finish(engines)

stranger = M.CallEngine("e" * 32, os.urandom(8), 0, None, "",
                        on_event=lambda *_a: None)
stranger.set_keys(M.CallKeys(os.urandom(32), "e" * 32, stranger.tag, True))
engines, events = pair()
stranger.add_candidates(engines[1].candidates())
stranger.start()
time.sleep(0.6)
check("packets from outside the call are ignored",
      engines[1].received > 0 and not any(k == "bye" for k, _v in events[1]))
stranger.stop()
finish(engines)


# ---- two apps -------------------------------------------------------------------
class MailRelay:
    def __init__(self):
        self.mail, self._next = [], 1

    def post(self, sender, recipient, envelope, cid):
        self._next += 1
        self.mail.append({"id": self._next, "sender": sender,
                          "recipient": recipient, "envelope": envelope,
                          "client_msg_id": cid})
        return self._next


class StubApi:
    def __init__(self, mail, keys, udp):
        self.mail, self.keys, self.udp = mail, keys, udp
        self._features = {"blobs": True, "calls": True, "call_udp": udp[1]}

    def features(self):
        return self._features

    def send_message(self, recipient, envelope, client_msg_id):
        return {"server_id": self.mail.post(self.keys.address, recipient,
                                            envelope, client_msg_id)}

    def call_ticket(self):
        return {"ticket": "ticket", "udp": self.udp[1]}

    def read(self, ids):
        return {"updated": len(ids)}


def make_party(label, mail, udp):
    app = M.FextApp()
    app._dismiss_splash = lambda *_a: None
    app._probe_transport = lambda: None
    app.build()
    priv, _p, _a, _n = M.mine_identity()
    address = app.keys.add_identity(priv, label)
    app.keys.activate(address)
    app.keys.set_username(label)
    app.crypto = M.CryptoEngine(app.keys)
    app.api = StubApi(mail, app.keys, udp)
    app.store = M.LocalStore(app.keys.store_path())
    app.worker = M.SyncWorker(app)
    return app


def deliver(mail, parties):
    n = 0
    while mail.mail:
        msg = mail.mail.pop(0)
        parties[msg["recipient"]].worker._ingest_message(msg)
        n += 1
    return n


def pump_all(parties):
    from kivy.clock import Clock
    Clock.tick()
    deliver(mail, parties)
    for p in parties.values():
        p._drain_ui_queue(0)


def settle(parties, cond, timeout=6.0):
    return wait_until(cond, timeout, tick=lambda: pump_all(parties))


def events_of(app, peer):
    return [m["body"] for m in app.store.messages_for(peer)
            if m.get("kind") == "event"]


print("\nTwo apps: a call from start to end")
relay = StandInRelay()
relay.start()
M.ENDPOINT._base = f"http://127.0.0.1:{relay.addr[1]}"
M.calls_possible_here = lambda: True
voices = {}
M.call_audio = lambda: voices.setdefault(len(voices), FakeAudio(
    440 if len(voices) % 2 == 0 else 660))
mail = MailRelay()
alice = make_party("alice", mail, relay.addr)
bob = make_party("bob", mail, relay.addr)
parties = {alice.keys.address: alice, bob.keys.address: bob}
A, B = alice.keys.address, bob.keys.address
for me, other in ((alice, bob), (bob, alice)):
    me.store.upsert_contact(other.keys.address, other.keys.username,
                            other.keys.public_key_hex)
alice.foreground = bob.foreground = True

check("a contact whose app cannot take calls is not called",
      alice.call_blocker(B) is not None and "9.1.2" in alice.call_blocker(B))
for me, other in ((alice, B), (bob, A)):
    me.send_text(other, "hi")
settle(parties, lambda: not mail.mail and alice.store.get_contact(B)["caps"]
       & M.LocalStore.CAP_CALL and bob.store.get_contact(A)["caps"]
       & M.LocalStore.CAP_CALL)
check("every message says the app takes calls, so now they can be called",
      alice.call_blocker(B) is None)
check("a group, or saved messages, cannot be called",
      alice.call_blocker(A) is not None)

alice.open_conversation(B)
bar = alice.conversation_screen.normal_bar
check("a direct chat's header has the call button",
      alice.conversation_screen.call_button.parent is bar)
alice.conversation_screen.call_button.dispatch("on_release")
check("calling opens the call screen at once",
      alice.call is not None and alice._call_overlay is not None and
      alice._call_overlay.status.text == "Calling…")
check("bob's phone rings, with answer and decline",
      settle(parties, lambda: bob.call is not None and
             bob._call_overlay is not None and
             bob._call_overlay.accept.parent is not None))
check("...and alice sees that it rings",
      settle(parties, lambda: alice.call.state == "ringing") and
      alice._call_overlay.status.text == "Ringing…")
bob.answer_call()
check("answered: both sides go live",
      settle(parties, lambda: alice.call is not None and bob.call is not None
             and alice.call.state == "active" and bob.call.state == "active"),
      f"{alice.call and alice.call.state}/{bob.call and bob.call.state}")
check("...on the direct way", settle(parties, lambda: alice.call.path ==
                                     "direct" and bob.call.path == "direct"))
time.sleep(1.0)
pump_all(parties)
check("the call screen shows the time running and the way it goes",
      ":" in alice._call_overlay.status.text and
      alice._call_overlay.detail.text.startswith("Direct"))
alice.toggle_call_mute()
check("mute is a toggle on the call screen",
      alice.call.muted and alice.call.engine.muted and
      alice._call_overlay.mute.caption.text == "Unmute")
alice.toggle_call_mute()
from kivy.core.window import Window  # noqa: E402
alice._on_keyboard(Window, 27)
from kivy.clock import Clock  # noqa: E402
check("back sets the call screen aside, with a bar to return",
      wait_until(lambda: alice._call_overlay.parent is None, 1,
                 tick=Clock.tick) and alice._call_pill is not None and
      alice.call is not None and not alice.call.ended)
alice._call_pill.dispatch("on_release")
check("...which brings it back", alice._call_overlay.parent is not None and
      alice._call_pill is None)
time.sleep(1.1)
pump_all(parties)
alice.end_call()
check("hanging up ends it on both sides",
      settle(parties, lambda: alice.call is None and bob.call is None))
check("...and each chat keeps a line for the call, with its length",
      any(e.startswith("Outgoing call · 0:0") for e in events_of(alice, B))
      and any(e.startswith("Incoming call · 0:0") for e in events_of(bob, A)),
      f"{events_of(alice, B)} / {events_of(bob, A)}")

print("\nTwo apps: the other endings")
alice.start_call(B)
settle(parties, lambda: bob.call is not None)
bob.decline_call()
check("declined: both screens close",
      settle(parties, lambda: alice.call is None and bob.call is None))
check("...alice is told, bob's chat says he declined",
      events_of(alice, B)[-1] == "Call declined" and
      events_of(bob, A)[-1] == "Declined call")

alice.start_call(B)
settle(parties, lambda: bob.call is not None)
alice.end_call()
check("cancelled before an answer: a missed call for bob",
      settle(parties, lambda: alice.call is None and bob.call is None) and
      events_of(alice, B)[-1] == "Cancelled call" and
      events_of(bob, A)[-1] == "Missed call")

cara = make_party("cara", mail, relay.addr)
parties[cara.keys.address] = cara
K = cara.keys.address
for me, other in ((cara, bob), (bob, cara)):
    me.store.upsert_contact(other.keys.address, other.keys.username,
                            other.keys.public_key_hex)
cara.store.add_caps(B, M.LocalStore.CAP_V9 | M.LocalStore.CAP_CALL)
alice.start_call(B)
settle(parties, lambda: bob.call is not None)
cara.start_call(B)
check("calling someone already in a call: they are busy",
      settle(parties, lambda: cara.call is None) and
      events_of(cara, B)[-1].startswith("Busy") and
      events_of(bob, K)[-1] == "Missed call")
alice.end_call()
settle(parties, lambda: alice.call is None and bob.call is None)

saved = M.CALL_RING_SECONDS
M.CALL_RING_SECONDS = 1
alice.start_call(B)
settle(parties, lambda: bob.call is not None)
check("unanswered, it gives up: no answer for alice, missed for bob",
      settle(parties, lambda: alice.call is None and bob.call is None, 8) and
      events_of(alice, B)[-1] == "No answer" and
      events_of(bob, A)[-1] == "Missed call")
M.CALL_RING_SECONDS = saved

stale = {"op": "call", "id": "f" * 32, "tag": "e" * 16,
         "k": M.new_call_key()[1], "codecs": ["ima16"], "c": [],
         "t": int(time.time()) - 600}
alice._send_control_to(B, stale, "Voice call")
settle(parties, lambda: not mail.mail)
check("an offer that arrives long after it was made rings nothing: missed",
      bob.call is None and events_of(bob, A)[-1] == "Missed call")

bob.set_blocked([A], True)
alice.start_call(B)
settle(parties, lambda: not mail.mail, 2)
check("someone you blocked cannot make your phone ring", bob.call is None)
alice.end_call()
settle(parties, lambda: alice.call is None)
bob.set_blocked([A], False)

alice.open_conversation(alice.keys.address)
check("saved messages have no call button",
      alice.conversation_screen.call_button.parent is None)
relay.stop = True

passed = sum(1 for _n, c in RESULTS if c)
print(f"\n  {passed}/{len(RESULTS)} passed")
os._exit(0 if passed == len(RESULTS) else 1)
