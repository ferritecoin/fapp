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
import random
import re
import socket
import struct
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
if sys.platform.startswith("linux"):     # xvfb-run's X display
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-calls-")
os.environ["USERPROFILE"] = os.environ["HOME"]   # Path.home() on Windows
os.environ["FEXT_LANG"] = "en"          # the English the checks read
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


def tone(freq, frames, amp=8000, start=0, fmt=M.CALL_PCM):
    """`frames` frames of a steady tone in `fmt` (both channels alike)."""
    per = fmt.frame_bytes // (2 * fmt.channels)
    mono = [int(amp * math.sin(2 * math.pi * freq * n / fmt.rate))
            for n in range(start, start + per * frames)]
    out = array.array("h", (v for v in mono for _c in range(fmt.channels)))
    return out.tobytes()


class FakeAudio(M.CallAudio):
    """A microphone that hears a steady tone, in real time; a speaker that
    keeps what it is given — in the format the call starts it in."""

    name = "fake"

    def __init__(self, freq=440):
        self.freq, self.played, self._sent = freq, [], 0
        self._t0 = None
        self.capturing = True

    def start(self, fmt=M.CALL_PCM, capture=True):
        self.fmt, self._t0, self.capturing = fmt, time.monotonic(), capture

    def set_capturing(self, on):            # 16.1: the microphone, closed
        if on and not self.capturing:
            self._t0, self._sent = time.monotonic(), 0
        self.capturing = on

    def read(self):
        if not self.capturing:
            return []
        due = int((time.monotonic() - self._t0) * 1000 / self.fmt.frame_ms)
        per = self.fmt.frame_bytes // (2 * self.fmt.channels)
        out = []
        while self._sent < due:
            out.append(tone(self.freq, 1, start=self._sent * per,
                            fmt=self.fmt))
            self._sent += 1
        return out[-(200 // self.fmt.frame_ms):]

    def write(self, pcm):
        self.played.append(pcm)

    def queued(self):
        return 0


def dominant(pcm, fmt=M.CALL_PCM):
    """The loudest of a few test frequencies in some PCM (its first
    channel)."""
    s = array.array("h", pcm)[::fmt.channels]
    best, which = 0, None
    for f in (440, 660):
        re = sum(v * math.cos(2 * math.pi * f * n / fmt.rate)
                 for n, v in enumerate(s))
        im = sum(v * math.sin(2 * math.pi * f * n / fmt.rate)
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
    check("both sides offer Opus first (16.0's before 11's), ADPCM after",
          M.call_codecs() == ["opus48", "opus16", "ima16"] and
          M.pick_send_codec(["opus48", "opus16", "ima16"]) == "opus48" and
          M.pick_send_codec(["opus16", "ima16"]) == "opus16" and
          M.pick_send_codec(["ima16"]) == "ima16" and
          M.pick_send_codec(None) == "ima16")
saved = M._OPUS_LIB
M._OPUS_LIB = False
check("without libopus a device offers and sends ADPCM only",
      M.call_codecs() == ["ima16"] and
      M.pick_send_codec(["opus48", "opus16", "ima16"]) == "ima16")
M._OPUS_LIB = saved
ring = M.sanitize_call_signal("ringing", {
    "id": "a" * 32, "k": M.new_call_key()[1], "c": [],
    "codecs": ["opus16", "ima16", "mp3"], "q": 128})
old_answer = M.sanitize_call_signal("answer", {
    "id": "a" * 32, "k": M.new_call_key()[1], "c": [], "codec": "ima16"})
check("ringing and answer say what the callee decodes; an older app's say "
      "nothing, which is ADPCM",
      ring["codecs"] == ["opus16", "ima16"] and
      old_answer["codecs"] == ["ima16"])
check("...and (16.0) the most it wants sent, kept only within reason",
      ring["q"] == 128 and "q" not in old_answer and "q" not in
      M.sanitize_call_signal("answer", {
          "id": "a" * 32, "k": M.new_call_key()[1], "c": [], "q": 10 ** 6}))

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


def held_after(quiet, seconds=1.0):
    """18.1 (CATCHUP's note): a call's buffer 150 ms over its target, as
    when the sound goes straight after starting through a far relay; a
    frame of 5 ms arriving and one played every 5 ms, by a clock of its
    own. How much it holds after `seconds`, in ms."""
    real, now = M.time, [100.0]

    class Clock5:
        def __getattr__(self, name):
            return getattr(real, name)

        def monotonic(self):
            return now[0]
    jb = M.JitterBuffer(8, 2, 40, frame_ms=5)
    for n in range(38):
        jb.put(n, n, 5)
    M.time = Clock5()
    try:
        least = 10 ** 6
        for n in range(38, 38 + int(seconds * 200)):
            now[0] += 0.005
            jb.put(n, n, 5)
            jb.get(quiet)
            least = min(least, jb._held)
    finally:
        M.time = real
    return jb._held, least, jb.target_ms


talking, _l, target = held_after(False)
pausing, least, _t = held_after(True)
check("a call's delay is given back a frame a second while the sound goes "
      "on, so no one hears a skip in a word", talking >= target + 130,
      f"{talking} ms held, target {target}")
check("...and in a pause at every frame: 150 ms over is gone within a "
      "second of quiet (CATCHUP, 18.1)", pausing <= target + 2 * 5,
      f"{pausing} ms held, target {target}")
check("...never below the buffer's own aim", least >= target,
      f"{least} ms at the least")

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


def pair(relay_addr=None, direct=True, ws=False, unanswered=False, hide=None):
    """Two engines of one call, keyed and told where the other is.
    unanswered: the one called (role 1) is made as the app makes it, not
    answered yet (18.1). hide: the role with Hide my location in calls
    on."""
    cid, tg = os.urandom(16).hex(), os.urandom(8)
    events = {0: [], 1: []}
    s = os.urandom(32)
    engines = {}

    def ws_send(role):
        return lambda _tag, _role, packet: engines[1 - role].ws_packet(packet)
    for role in (0, 1):
        if hide is not None:                 # read as each engine is made
            M.save_pref("call_hide", role == hide)
        more = {"unanswered": True} if unanswered and role == 1 else {}
        engines[role] = M.CallEngine(
            cid, tg, role, relay_addr, "ticket" if relay_addr else "",
            on_event=lambda k, v, r=role: events[r].append((k, v)),
            send_ws=ws_send(role) if ws else None,
            audio_factory=lambda r=role: FakeAudio(440 if r == 0 else 660),
            **more)
        engines[role].set_keys(M.CallKeys(s, cid, tg, caller=role == 0))
    if hide is not None:
        M.save_pref("call_hide", False)
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


def listener():
    """A UDP port on this machine that only listens: what is sent
    straight to an address shows up here (heard_at)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    return sock


def heard_at(sock):
    """How many packets have reached a listener since it was last asked."""
    n = 0
    while True:
        try:
            sock.recvfrom(2048)
        except OSError:                      # nothing more is waiting
            return n
        n += 1


print("\nTwo engines on one machine")
# An engine asks video_decoders() on its first tick, in its own thread,
# and the first call of a process loads PyAV there. From a disk read for
# the first time that took over 3 s: asked here first, the waits below
# time the engines only.
M.video_decoders()
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
told, get = [], engines[0]._jitter.get       # 18.1: what its buffer is told
engines[0]._jitter.get = \
    lambda quiet=False: (told.append(quiet), get(quiet))[1]
time.sleep(0.3)
voice = told[:]
engines[1].muted = True
time.sleep(0.5)
n = len(engines[0]._audio.played)
time.sleep(0.4)
tail = b"".join(engines[0]._audio.played[n:])
check("muted, the other side hears silence (and no gap)",
      len(engines[0]._audio.played) > n and not any(tail))
check("its buffer is told when what it just played was a pause, where a "
      "skip is not heard, and not while the voice goes on (CATCHUP, 18.1)",
      len(voice) > 5 and not any(voice) and told[-5:] == [True] * 5,
      f"{sum(voice)} of {len(voice)}, then {told[-5:]}")
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
    real_packet = engines[1]._packet
    count = [0]

    def lossy(path, data, src):
        if data[:1] == bytes([M.PKT_AUDIO]):
            count[0] += 1
            if count[0] % 10 == 3:
                return
        real_packet(path, data, src)
    engines[1]._packet = lossy
    talk(engines, 1.6)
    heard1 = engines[1]._audio.played
    check("two apps with Opus speak Opus to each other (with an app before "
          "16.0: 16 kHz, 20 ms packets, as it expects)",
          sent[0] and set(sent[0][-20:]) == {M.CODEC_IDS["opus16"]} and
          set(sent[1][-20:]) == {M.CODEC_IDS["opus16"]} and
          engines[0].fmt == M.CALL_PCM and engines[0].packet_ms == 20,
          f"{set(sent[0][-20:])}/{set(sent[1][-20:])}")
    check("...each hearing the other's voice",
          dominant(b"".join(heard1[-10:])) == 440 and
          dominant(b"".join(engines[0]._audio.played[-10:])) == 660)
    steady = heard1[25:]
    silent = sum(1 for f in steady if not any(f))
    check("...with one packet in ten lost, no gap is heard",
          len(steady) > 30 and silent == 0 and
          engines[1]._jitter.lost > 3, f"{silent} silent of "
          f"{len(steady)}, {engines[1]._jitter.lost} lost")
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

W = M.CALL_PCM48
if M.opus_library() is not None:
    print("\n16.0: calls at 48 kHz in stereo (CALLQ)")
    rnd = random.Random(7)
    rich = array.array("h")
    for n in range(W.rate * 2):              # two seconds, tones and noise
        v = int(6000 * math.sin(2 * math.pi * 440 * n / W.rate) +
                3000 * math.sin(2 * math.pi * 1250 * n / W.rate) +
                rnd.randint(-2500, 2500))
        rich.extend((v, v))
    rich = rich.tobytes()
    rates = {}
    for _key, _label, kbps in M.CALL_QUALITIES:
        codec = M.OpusCodec(W, kbps * 1000)
        step = W.bytes_for(20)
        sizes = [len(codec.encode(rich[i:i + step]))
                 for i in range(0, len(rich) - step + 1, step)][10:]
        rates[kbps] = round(sum(sizes) * 8 / (len(sizes) * 20))
        codec.close()
    check("Standard, High and Ultra send about 96, 128 and 192 kbit/s",
          all(0.75 * k < rates[k] < 1.15 * k for k in rates), str(rates))
    codec = M.OpusCodec(W, 128000)
    lengths = {ms: M.opus_packet_ms(codec.encode(rich[:W.bytes_for(ms)]))
               for ms in M.CALL_PACKET_MS}
    check("packets of 5, 10, 20 and 40 ms, each saying how long it is",
          lengths == {5: 5, 10: 10, 20: 20, 40: 40}, str(lengths))
    back = codec.decode(codec.encode(rich[:W.bytes_for(10)]))
    lost = codec.decode(None, ms=5)
    check("...each decodes to that much stereo sound, and a lost one is "
          "stood in for at its own length",
          back is not None and len(back) == W.bytes_for(10) and
          lost is not None and len(lost) == W.bytes_for(5))
    older = M.OpusCodec()
    heard = older.decode(codec.encode(rich[:W.bytes_for(20)]))
    check("Opus is Opus: an app before 16.0 could decode one too",
          heard is not None and len(heard) == M.CALL_FRAME_BYTES)
    codec.close()
    older.close()

    jb = M.JitterBuffer(8, 2, 40, frame_ms=5)
    jb.put(0, "a", 20)
    waits = jb.get() is None
    for n, ms in ((1, 10), (2, 10), (4, 10)):
        jb.put(n, "bcde"[n - 1], ms)
    got = [jb.get() for _ in range(5)]
    check("it counts sound, not packets: 40 ms in hand before it plays, "
          "however that is cut, and a lost one is taken to be as long as "
          "the latest", waits and got[:3] == ["a", "b", "c"] and
          got[3] is M.JitterBuffer.CONCEAL and jb.frame_ms == 10 and
          got[4] == "e", str(got))

    def wide_pair(their_kbps=None, **kw):
        engines, events = pair(**kw)
        for e in engines.values():
            e.set_send_codecs(["opus48", "opus16", "ima16"], their_kbps)
        return engines, events

    M.save_pref("call_quality", "ultra")
    engines, events = wide_pair()
    engines[0].set_send_codecs(["opus48", "opus16", "ima16"], 96)
    sent = {0: [], 1: []}
    for role, engine in engines.items():
        real = engine._send

        def spy(ptype, payload, r=role, real=real):
            if ptype == M.PKT_AUDIO and len(payload) > 5:
                sent[r].append((payload[4], M.opus_packet_ms(payload[5:])))
            return real(ptype, payload)
        engine._send = spy
    wait_until(lambda: engines[0].path == "direct", 3)
    talk(engines, 1.5)
    check("two 16.0 apps: 48 kHz stereo both ways, as 'opus48'",
          all(e.wide and e.fmt == W for e in engines.values()) and
          {c for c, _ms in sent[0][-10:]} == {M.CODEC_IDS["opus48"]},
          str([(e.wide, e.fmt) for e in engines.values()]))
    check("...each hearing the other's voice",
          dominant(b"".join(engines[1]._audio.played[-10:]), W) == 440 and
          dominant(b"".join(engines[0]._audio.played[-10:]), W) == 660)
    check("...never sent more than the other side chose: Ultra here, "
          "Standard there, 96 kbit/s",
          engines[0].kbps == 96 and engines[1].kbps == 192,
          f"{engines[0].kbps}/{engines[1].kbps}")
    shortened = wait_until(lambda: engines[0].packet_ms == 5 and
                           engines[1].packet_ms == 5, 9)
    time.sleep(0.3)
    check("on a steady direct way the packets shorten, down to 5 ms "
          "(CALLQ2)", shortened and {ms for _c, ms in sent[0][-5:]} == {5},
          f"{engines[0].packet_ms}/{engines[1].packet_ms} "
          f"{sent[0][-5:]}")
    n = len(engines[0]._audio.played)
    engines[1].muted = True
    time.sleep(0.5)
    tail = b"".join(engines[0]._audio.played[n + 20:])
    check("...muted, the other side hears silence, without a gap",
          len(tail) > W.bytes_for(200) and not any(tail))
    engines[1].muted = False
    summary = engines[0].summary()
    check("the call keeps how it went, for its line in the chat",
          summary["quality"] == "ultra" and summary["kbps"] == 96 and
          summary["path"] == "direct" and summary["rtt"] is not None,
          str(summary))
    finish(engines)

    engines, events = wide_pair(192)
    real_packet = engines[1]._packet
    count = [0]

    def lossy(path, data, src):
        if data[:1] == bytes([M.PKT_AUDIO]):
            count[0] += 1
            if count[0] % 10 == 3:
                return
        real_packet(path, data, src)
    engines[1]._packet = lossy
    wait_until(lambda: engines[0].path == "direct", 3)
    talk(engines, 0.2)
    check("one packet in ten lost: the sender steps its bitrate down",
          wait_until(lambda: engines[0].kbps < 192, 8) and
          engines[1].kbps == 192, f"{engines[0].kbps}/{engines[1].kbps}")
    check("...and the line it leaves says so",
          "–192 kbit/s" in M.call_quality_line(engines[0].summary()),
          M.call_quality_line(engines[0].summary()))
    finish(engines)

    M.save_pref("call_adapt", False)
    for prefer, want in (("quality", 20), ("latency", 5)):
        M.save_pref("call_latency", prefer)
        engines, events = wide_pair(192)
        wait_until(lambda: engines[0].path == "direct", 3)
        talk(engines, 4.5)
        check(f"Adapt to the connection off, preferring {prefer} (17.0): "
              f"{want} ms packets straight between them, at the chosen "
              "quality", engines[0].packet_ms == want and
              engines[0].kbps == 192, f"{engines[0].packet_ms} ms")
        finish(engines)
    M.save_pref("call_adapt", True)
    M.save_pref("call_latency", "quality")
    engines, events = wide_pair(192)
    wait_until(lambda: engines[0].path == "direct", 3)
    talk(engines, 4.5)
    check("...and with it on, preferring quality, never shorter than 20 ms",
          engines[0].packet_ms >= 20, f"{engines[0].packet_ms} ms")
    finish(engines)
    M.save_pref("call_latency", "latency")

    relay13 = StandInRelay()
    relay13.start()
    engines, events = wide_pair(192, relay_addr=relay13.addr, direct=False)
    wait_until(lambda: engines[0].path == "relay", 4)
    talk(engines, 6.5)
    check("through a relay that takes 150 packets a second, 10 ms at the "
          "shortest", engines[0].path == "relay" and
          engines[0].packet_ms == 10 and engines[1].packet_ms == 10,
          f"{engines[0].path} {engines[0].packet_ms}")
    finish(engines)
    relay13.stop = True
    engines, events = wide_pair(192, relay_addr=None, direct=False, ws=True)
    wait_until(lambda: engines[0].path == "ws", 3)
    talk(engines, 2.5)
    check("...over the WebSocket, 20", engines[0].packet_ms == 20 and
          len(engines[1]._audio.played) > 20)
    finish(engines)
    M.save_pref("call_quality", "standard")
    check("the line a call leaves: its quality, the way, the round trip, "
          "what was lost",
          M.call_quality_line({"quality": "high", "kbps": 128,
                               "path": "relay", "rtt": 84.4,
                               "loss": 0.004}) ==
          "High · Through the relay · 84 ms · 0.4% lost" and
          M.call_quality_line({"quality": "", "path": "direct", "rtt": 9,
                               "loss": 0}) == "16 kHz · Direct · 9 ms · 0% lost")

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

print("\nWhere a device is (18.1, CALLPRIV)")
# The other device learns this one's network address, which says roughly
# where it is, from what this one offers (candidates) and from any packet
# it sends straight. A device that is called does neither until it
# answers; with Hide my location in calls on, a device never does.
relay = StandInRelay()
relay.start()
spy = listener()
engines, events = pair(relay_addr=relay.addr, direct=False, unanswered=True)
caller, called = engines[0], engines[1]
# What the caller's offer carries, and one address of the test's own: a
# packet the called device sent straight would be heard there.
called.add_candidates(caller.candidates() + [list(spy.getsockname())])
check("a device that is called and has not answered offers no address",
      called.candidates() == [] and caller.candidates() != [],
      str(called.candidates()))
check("...the call reaches it through the relay all the same",
      wait_until(lambda: caller.path == "relay" and called.path == "relay",
                 3), f"{caller.path}/{called.path}")
time.sleep(0.6)
check("...it has sent nothing straight to the caller's addresses",
      heard_at(spy) == 0 and caller._rx["direct"] == 0.0)
check("...nor does it offer its public address, once the relay has told it",
      called.reflexive is not None and called.candidates() == [],
      str(called.candidates()))
caller.add_candidates([["127.0.0.1", called.port]])   # learnt some other way
time.sleep(0.6)
check("...and it answers nothing that comes straight to it",
      called._direct is None and caller._rx["direct"] == 0.0 and
      caller.path == "relay", str(caller.path))
began = time.monotonic()
called.answered()
check("answered, it offers its addresses at once",
      list(called.reflexive) in called.candidates(), str(called.candidates()))
check("...and looks for the straight way",
      wait_until(lambda: heard_at(spy) > 0, 2))
check("...which both then use",
      wait_until(lambda: caller.path == "direct" and called.path == "direct",
                 3), f"{caller.path}/{called.path}")
print(f"         (straight {time.monotonic() - began:.2f} s after the answer)")
finish(engines)

engines, events = pair(relay_addr=relay.addr, direct=False, unanswered=True,
                       hide=1)
other, hidden = engines[0], engines[1]
hidden.add_candidates(other.candidates() + [list(spy.getsockname())])
other.add_candidates([["127.0.0.1", hidden.port]])
hidden.answered()
check("Hide my location in calls: the device offers no address, answered "
      "or not", wait_until(lambda: hidden.relay_ok and
                           hidden.reflexive is not None, 3) and
      hidden.candidates() == [], str(hidden.candidates()))
wait_until(lambda: hidden.path == "relay" and other.path == "relay", 3)
before = relay.passed
talk(engines, 1.0)
check("...sends nothing straight, and answers nothing that comes straight",
      heard_at(spy) == 0 and hidden._direct is None and
      other._rx["direct"] == 0.0)
check("...and its call goes through the relay, heard on both sides",
      hidden.path == "relay" and other.path == "relay" and
      len(hidden._audio.played) > 15 and len(other._audio.played) > 15 and
      relay.passed - before > 50,
      f"{hidden.path}/{other.path}, {relay.passed - before} passed")
finish(engines)

engines, events = pair(relay_addr=None, direct=True, ws=True, hide=0)
check("...or over the message link, where the relay offers no other way",
      wait_until(lambda: engines[0].path == "ws" and engines[1].path == "ws",
                 3), f"{engines[0].path}/{engines[1].path}")
talk(engines, 1.0)
check("...and is heard there too", len(engines[0]._audio.played) > 15 and
      len(engines[1]._audio.played) > 15)
finish(engines)
relay.stop = True
spy.close()


# ---- two apps -------------------------------------------------------------------
class MailRelay:
    """A relay's mailbox: each message stamped with when it took it, by
    its own clock (`held`: seconds earlier, as if it had waited there)."""

    def __init__(self):
        self.mail, self._next, self.held = [], 1, 0

    def post(self, sender, recipient, envelope, cid):
        self._next += 1
        taken = datetime.fromtimestamp(time.time() - self.held, timezone.utc)
        self.mail.append({"id": self._next, "sender": sender,
                          "recipient": recipient, "envelope": envelope,
                          "client_msg_id": cid,
                          "created_at": taken.isoformat()})
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

    clock_offset = 0.0

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
    # Each party is a device of its own (17.1 will not call an identity on
    # the same device); these apps share one folder, so each forgets the
    # identities the others put there.
    app.keys._data["identities"] = [app.keys.find(address)]
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
    """What a chat says became of its calls, oldest first: each call's
    card (16.1), or a line for a call that never had one."""
    out = []
    for m in app.store.messages_for(peer):
        if m.get("kind") == "event":
            out.append(m["body"])
        elif m.get("kind") == "call":
            card = M.message_ext(m).get("call") or {}
            if card.get("summary"):
                out.append(card["summary"])
    return out


def place_call(app, peer):
    """16.1: calling opens the lobby; its "Start call" places the call."""
    app.start_call(peer)
    lobby = app.__dict__.get("_lobby")
    if lobby is not None:
        lobby.join()


def answer(app, mic=False):
    """16.1: answering opens the lobby; "Confirm & join" answers, with
    the microphone as chosen there (off unless turned on)."""
    app.answer_call()
    lobby = app.__dict__.get("_lobby")
    if lobby is not None:
        if mic:
            lobby.toggle_mic()
        lobby.join()


print("\nThe microphone turned on as the call's sound starts (18.1)")
# The one called can answer, the microphone on, at the moment their
# engine starts its sound: the engine's thread reads `muted` while the
# app's thread changes it. A stand-in microphone changes it at that very
# point.


class TurnedOnMeanwhile(FakeAudio):
    engine = None

    def start(self, fmt=M.CALL_PCM, capture=True):
        super().start(fmt, capture)
        self.engine.set_mic(True)        # the person's choice lands now


engines, events = pair()
caller, called = engines[0], engines[1]
called.muted = True                      # made while it rings: off
mic = TurnedOnMeanwhile(660)
mic.engine = called
called._audio_factory = lambda: mic
talk(engines, 1.0)
check("turned on at that moment, the microphone is open and the other "
      "side hears it (it could be left closed yet marked open: nothing "
      "went out, and the caller's call never began)",
      called.mic_open and mic.capturing and not called.muted and
      caller.last_heard is not None and
      dominant(b"".join(caller._audio.played[-10:])) == 660,
      (called.mic_open, mic.capturing, called.muted, caller.last_heard))
finish(engines)

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

check("a contact not heard from yet is not called: until they accept the "
      "chat request or write (17.1's words)", "accept your chat request" in
      (alice.call_blocker(B) or ""))
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
lobby = alice.__dict__.get("_lobby")
check("calling opens the lobby first (16.1): the microphone off, Start "
      "call, nothing sent yet", lobby is not None and alice.call is None
      and not lobby.session.me.mic and not mail.mail
      and lobby.join_button.label.text.lower() == "start call",
      lobby and lobby.join_button.label.text)
hushed = []
for who in (alice, bob):                 # each one's player, listened to
    who.voice.stop = (lambda really, name: lambda: (
        hushed.append(name), really())[1])(who.voice.stop,
                                           who.keys.username)
lobby.join()
check("Start call: the call screen at once, muted as the lobby left it",
      alice.call is not None and alice._call_overlay is not None and
      alice._call_overlay.status.text == "Calling…" and alice.call.muted)
check("bob, elsewhere in his app, is not taken over: a toast, and the "
      "call's card in alice's chat", settle(
          parties, lambda: bob.call is not None and
          bob.__dict__.get("_call_toast") is not None) and
      bob._call_overlay is None and
      bob.store.call_card(A, bob.call.id) is not None)
check("...and alice sees that it rings",
      settle(parties, lambda: alice.call.state == "ringing") and
      alice._call_overlay.status.text == "Ringing…")
check("a call stops a message that was playing, where it is placed and "
      "where it rings (18.1): once, so one played during the call plays",
      hushed == ["alice", "bob"], hushed)
check("a phone that only rings tells the caller nothing of where it is "
      "(18.1): the call's packets go through the relay, its reply carries "
      "no address, and nothing goes straight",
      settle(parties, lambda: alice.call.path == "relay" and
             bob.call.path == "relay") and
      alice.call.engine._remote == [] and alice.call.engine._direct is None
      and bob.call.engine._direct is None,
      f"{alice.call.path}/{bob.call.path} {alice.call.engine._remote}")
card = M.CallCard(bob.call.session, bob.call_name)
check("the live card says who calls, on both sides (17.1)",
      M.direct_card_title(bob.call.session, "alice") == "alice is calling"
      and M.direct_card_title(alice.call.session, "bob") == "Calling bob…")
toast = bob.__dict__.get("_call_toast")
next(w for w in toast.children if isinstance(w, M.IconButton)) \
    .dispatch("on_release")
check("closing the toast leaves a bar to answer from, wherever bob goes "
      "(17.1)", bob.__dict__.get("_call_toast") is None and
      bob._call_pill is not None and
      bob._call_pill.label.text.startswith("alice is calling"),
      bob._call_pill and bob._call_pill.label.text)
bob.answer_call()
bob_lobby = bob.__dict__.get("_lobby")
check("answering opens bob's lobby: his microphone off, nothing answered "
      "until he confirms", bob_lobby is not None and
      bob.call.state == "incoming" and not bob_lobby.session.me.mic and
      bob.__dict__.get("_call_toast") is None)
bob_lobby.toggle_mic()
bob_lobby.join()
check("answered: both sides go live",
      settle(parties, lambda: alice.call is not None and bob.call is not None
             and alice.call.state == "active" and bob.call.state == "active"),
      f"{alice.call and alice.call.state}/{bob.call and bob.call.state}")
check("...bob's microphone on, as he chose; alice's still off",
      not bob.call.muted and not bob.call.engine.muted and
      alice.call.muted and alice.call.engine.muted)
check("...on the direct way", settle(parties, lambda: alice.call.path ==
                                     "direct" and bob.call.path == "direct"))
time.sleep(1.0)
pump_all(parties)
check("the call screen shows the time running and the way it goes",
      ":" in alice._call_overlay.status.text and
      alice._call_overlay.detail.text.startswith("Direct"))
alice.call.engine.way = "fine"
alice._refresh_call()
lines = M.meter_lines(alice.call_meter())
check("beside the way, its bars: four while it is fine; a tap tells the "
      "numbers, the way first (17.2, F16)",
      alice._call_overlay.meter.bars.lit == 4 and lines[0] == "Direct" and
      any(line.startswith("Delay: ") for line in lines) and
      any(line.startswith("Sound: packets of ") for line in lines), lines)
check("...bars: fine 4; ok 3, or 2 past 300 ms there and back; poor 1; "
      "none before the way is judged",
      [M.meter_bars(w, r) for w, r in (("fine", 50), ("ok", 50),
                                       ("ok", 400), ("poor", 50), ("", None))]
      == [4, 3, 2, 1, 0])
alice.toggle_call_mute()
check("unmute is a toggle on the call screen",
      not alice.call.muted and not alice.call.engine.muted and
      alice._call_overlay.mute.caption.text == "Mute")
alice.toggle_call_mute()
check("...and so is mute", alice.call.muted and alice.call.engine.muted
      and alice._call_overlay.mute.caption.text == "Unmute")
from kivy.core.window import Window  # noqa: E402
alice._on_keyboard(Window, 27)
from kivy.clock import Clock  # noqa: E402
check("back sets the call screen aside, with a bar to return",
      wait_until(lambda: alice._call_overlay.parent is None, 1,
                 tick=Clock.tick) and alice._call_pill is not None and
      alice.call is not None and not alice.call.ended)
floating = alice._call_pill
check("...a small window over the app (17.2): bob's picture, or his disc",
      isinstance(floating, M.CallFloat) and floating.tile is not None and
      floating.tile.user == B)


class Finger:
    """A touch as Kivy hands it to the widget that took it."""

    def __init__(self, x, y):
        self.grab_current = None
        self.to(x, y)

    def to(self, x, y):
        self.x, self.y, self.pos = x, y, (x, y)

    def grab(self, widget, exclusive=False):
        self.grab_current = widget

    def ungrab(self, widget):
        self.grab_current = None


x0, y0 = floating.center
finger = Finger(x0, y0)
floating.on_touch_down(finger)
finger.to(x0 - M.dp(120), y0 + M.dp(80))
floating.on_touch_move(finger)
floating.on_touch_up(finger)
check("...which a finger drags anywhere, the call staying aside",
      floating.center_x < x0 - M.dp(60) and
      alice._call_overlay.parent is None and alice._call_pill is floating,
      (x0, floating.center_x))
finger = Finger(*floating.center)
floating.on_touch_down(finger)
floating.on_touch_up(finger)
check("...and a tap on it brings the call back",
      alice._call_overlay.parent is not None and alice._call_pill is None)
beside = alice.call_chats_sidebar()
chats = [w for w in beside.rows.children if isinstance(w, M.ListRow)]
alice.call_open_chat(B)
check("on a wide screen, your chats beside the call (17.2): one chosen "
      "sets the call aside, floating, and opens that chat",
      len(chats) >= 1 and alice._call_pill is not None and
      alice._call_overlay.parent is None and alice.current_peer == B and
      alice.manager.current == "conversation")
alice._call_pill.dispatch("on_release")
time.sleep(1.1)
pump_all(parties)
wide_was = M.call_is_wide
M.call_is_wide = lambda: True               # a computer's wide window
alice.hide_call()
alice._call_overlay = None                  # built again, beside the chats
alice.show_call()
for _ in range(3):
    pump_all(parties)
column = alice._call_overlay.controls.parent
chats_bar = alice.__dict__.get("_call_chats")
check("...and while the call shows, its own column starts beside them: its "
      "buttons clear of the chats", chats_bar is not None and
      column.x >= chats_bar.right - 1 and column.width > M.dp(100),
      (column.x, chats_bar and chats_bar.right))
M.call_is_wide = wide_was
alice.end_call()
check("hanging up ends it on both sides",
      settle(parties, lambda: alice.call is None and bob.call is None))
check("...each card says it was answered, and which way (17.1)",
      alice.store.call_cards(B)[0].get("outcome") == "answered" and
      bob.store.call_cards(A)[0].get("outcome") == "answered" and
      bob.store.call_cards(A)[0]["host"] == A)
check("...and each chat keeps a line for the call, with its length",
      any(e.startswith("Outgoing call · 0:0") for e in events_of(alice, B))
      and any(e.startswith("Incoming call · 0:0") for e in events_of(bob, A)),
      f"{events_of(alice, B)} / {events_of(bob, A)}")
line = events_of(alice, B)[-1].split("\n")
check("...and under it how the call went (16.0): its quality, the way, the "
      "round trip, what was lost — kept on the phone alone",
      len(line) == 2 and line[1].startswith("Standard · Direct · ") and
      line[1].endswith("lost"), str(line))

print("\nTwo apps: in the caller's chat, the whole screen")
bob.open_conversation(A)
place_call(alice, B)
check("bob has alice's chat open: his phone rings full screen, with "
      "answer and decline", settle(parties, lambda: bob.call is not None
                                   and bob._call_overlay is not None
                                   and bob._call_overlay.accept.parent
                                   is not None) and
      bob.__dict__.get("_call_toast") is None)
answer(bob)
check("both left their microphones off: each stays closed, and the call "
      "goes on, its muted packets heard on both sides", settle(
          parties, lambda: alice.call is not None and bob.call is not None
          and alice.call.state == "active" and bob.call.state == "active")
      and not alice.call.engine.mic_open and not bob.call.engine.mic_open
      and not alice.call.engine._audio.capturing
      and not bob.call.engine._audio.capturing)
check("a voice call offers no camera, on either side (17.1)",
      alice._call_overlay.camera.parent is None and
      bob._call_overlay.camera.parent is None)
alice.toggle_call_camera()
check("...and its camera cannot be turned on",
      not alice.call.session.me.camera)
overlay = alice._call_overlay
alice._their_call_media(alice.call, {
    "mic": True, "camera": True, "screen": True, "recording": False,
    "decoders": ["h264"]})
check("bob shares his screen, camera on: alice sees the screen, his face in "
      "its corner, and asks for both (17.1, multi-view)",
      overlay.picture.track == "s" and overlay.face.opacity == 1 and
      set(overlay.shows()) == {"s", "v"}, overlay.shows())
check("...the screen on the bands' own dark, so the words over it read in "
      "every theme", overlay.letterbox.opacity == 1)
M.save_pref("presenter_face", False)
alice._refresh_call()
check("...Settings › Calls can keep it to the screen alone",
      overlay.face.opacity == 0 and set(overlay.shows()) == {"s"},
      overlay.shows())
M.save_pref("presenter_face", True)
alice._their_call_media(alice.call, {
    "mic": True, "camera": False, "screen": False, "recording": False,
    "decoders": ["h264"]})
alice.end_call()
settle(parties, lambda: alice.call is None and bob.call is None)
bob.go_back_to_chats()
lobby = M.CallLobby(alice, M.CallSession(B, A, group=False, video=False,
                                         host=A))
lobby.toggle_camera()
check("a voice call's lobby has the microphone, and no camera or camera "
      "row (17.1)", lobby.camera_button is None and lobby.mic_button and
      not lobby.session.me.camera and
      all(row[0] != "video" for row in lobby.devices))
video_lobby = M.CallLobby(alice, M.CallSession(B, A, group=False,
                                               video=True, host=B))
check("...a video call's has its camera", video_lobby.camera_button is not
      None)
abouts = [w for w in video_lobby.walk() if isinstance(w, M.AboutButton)]
abouts[0].dispatch("on_release")
check("each device row in the lobby has its ⓘ, which says what it does "
      "(17.2)", len(abouts) == len(video_lobby.devices) >= 2 and
      M.Toast.current is not None and
      M.Toast.current.text == video_lobby.devices[0][4],
      [row[1] for row in video_lobby.devices])

print("\nTwo apps: Hide my location in calls (18.1, CALLPRIV)")
# Alice has it on, bob has not: the setting is read as each engine is
# made, and the two apps share one folder, so it is turned off again
# before bob's app is given the call (pump_all delivers it).
M.save_pref("call_hide", True)
place_call(alice, B)
wait_until(lambda: alice.call is not None and alice.call.engine is not None,
           3)
M.save_pref("call_hide", False)
settle(parties, lambda: bob.call is not None)
answer(bob, mic=True)
check("the caller hides: answered, the call is live through the relay",
      settle(parties, lambda: alice.call is not None and bob.call is not None
             and alice.call.state == "active" and bob.call.state == "active"
             and alice.call.path == "relay" and bob.call.path == "relay"),
      f"{alice.call and alice.call.path}/{bob.call and bob.call.path}")
check("...her call carried no address: bob's phone has none to try",
      bob.call.engine._remote == [] and bob.call.engine._direct is None)
check("...and she does not use the ones bob, who hides nothing, gave on "
      "answering", wait_until(lambda: alice.call.engine._remote != [], 2)
      and alice.call.engine._direct is None,
      str(alice.call.engine._remote))
time.sleep(1.0)
pump_all(parties)
check("...the call screen says which way it goes",
      alice._call_overlay is not None and
      alice._call_overlay.detail.text.startswith("Through the relay") and
      alice.call.path == "relay" and bob.call.path == "relay",
      alice._call_overlay and alice._call_overlay.detail.text)
alice.end_call()
settle(parties, lambda: alice.call is None and bob.call is None)
line = events_of(alice, B)[-1].split("\n")
check("...and so does the line the call leaves",
      len(line) == 2 and " · Through the relay · " in line[1], str(line))

print("\nTwo apps: answered while the call is still being prepared (18.1)")
# An answer can come at once (from the call's notification, or a quick
# thumb), while the app is still preparing the call on another thread.
place_call(alice, B)
settle(parties, lambda: bob.call is not None and bob.call.engine is not None)
bob.call.engine.start_audio()      # its sound started as made: microphone off
wait_until(lambda: bob.call.engine.audio_on, 2)
answer(bob, mic=True)
check("its sound had started with the microphone off, as the engine was "
      "made: the lobby's choice opens it, and alice hears bob",
      wait_until(lambda: bob.call.engine.mic_open and
                 bob.call.engine._audio.capturing, 2) and
      not bob.call.engine.muted and settle(
          parties, lambda: alice.call.state == "active" and
          bob.call.state == "active"),
      (bob.call.engine.mic_open, bob.call.engine._audio.capturing,
       bob.call.engine.muted))
alice.end_call()
settle(parties, lambda: alice.call is None and bob.call is None)
gate = threading.Event()
really_relay, really_new = bob._call_relay, bob._new_engine


def held_relay():
    gate.wait(5)
    return really_relay()


def made_off(call, role, relay, ticket):
    engine = really_new(call, role, relay, ticket)
    engine.muted = True                # read before the lobby's choice
    return engine


bob._call_relay, bob._new_engine = held_relay, made_off
place_call(alice, B)
settle(parties, lambda: bob.call is not None)
answer(bob, mic=True)                  # no engine yet
gate.set()
check("answered before its engine was made: the microphone is as the "
      "lobby left it, on",
      settle(parties, lambda: bob.call.engine is not None and
             bob.call.engine.audio_on and bob.call.engine.mic_open and
             bob.call.engine._audio.capturing and
             not bob.call.engine.muted and alice.call.state == "active"),
      bob.call.engine and (bob.call.engine.mic_open,
                           bob.call.engine.muted))
del bob._call_relay, bob._new_engine
alice.end_call()
settle(parties, lambda: alice.call is None and bob.call is None)

print("\nTwo apps: the other endings")
place_call(alice, B)
settle(parties, lambda: bob.call is not None)
bob.decline_call()
check("declined: both screens close",
      settle(parties, lambda: alice.call is None and bob.call is None))
check("...alice is told, bob's chat says he declined",
      events_of(alice, B)[-1] == "Call declined" and
      events_of(bob, A)[-1] == "Declined call")
check("...alice's card: an outgoing call, unanswered; bob's: declined "
      "(17.1)", alice.store.call_cards(B)[0].get("outcome") == "unanswered"
      and bob.store.call_cards(A)[0].get("outcome") == "declined")

place_call(alice, B)
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
place_call(alice, B)
settle(parties, lambda: bob.call is not None)
place_call(cara, B)
check("calling someone already in a call: they are busy",
      settle(parties, lambda: cara.call is None) and
      events_of(cara, B)[-1].startswith("Busy") and
      events_of(bob, K)[-1] == "Missed call")
alice.end_call()
settle(parties, lambda: alice.call is None and bob.call is None)

print("\nA call while in one (17.2, CALLWAIT)")
tones = []


class FakeTone:
    """The waiting tone, as heard: on from start() until stop()."""

    def __init__(self):
        self.on = False
        tones.append(self)

    def start(self):
        self.on = True

    def stop(self):
        self.on = False


M.DesktopWaitingTone = FakeTone


def waiting(app):
    return app.__dict__.get("_waiting_call")


def bar_buttons(app):
    return {b.text.lower(): b for b in waiting(app)["bar"].walk()
            if isinstance(b, M.Btn)}


def cara_calls_bob():
    place_call(cara, B)
    settle(parties, lambda: waiting(bob) is not None)
    return cara.call.id if cara.call is not None else None


place_call(alice, B)
settle(parties, lambda: bob.call is not None)
answer(bob)
settle(parties, lambda: alice.call is not None and bob.call is not None and
       alice.call.state == bob.call.state == "active")
first = cara_calls_bob()
bar = waiting(bob) and waiting(bob)["bar"]
check("cara calls bob while he talks with alice: her call waits, a bar over "
      "his call and its tone in it; his call goes on",
      bar is not None and bar.parent is M.Window and tones[-1].on and
      bob.call.peer == A and bob.call.state == "active" and
      cara.call is not None and not cara.call.ended)
words = {w.text for w in bar.walk() if isinstance(w, M.Label)}
check("...it says who calls, with Decline and End and answer",
      "cara is calling" in words and
      set(bar_buttons(bob)) == {"decline", "end and answer"},
      f"{words} {list(bar_buttons(bob))}")
second = {"op": "call", "id": "c" * 32, "tag": "e" * 16,
          "k": M.new_call_key()[1], "codecs": ["ima16"], "c": [],
          "t": int(time.time())}
cara._send_control_to(B, second, "Voice call")
settle(parties, lambda: bob.store.call_card(K, "c" * 32) is not None)
check("one call waits at a time: another while it does is busy, and missed",
      (bob.store.call_card(K, "c" * 32) or {}).get("outcome") == "missed"
      and waiting(bob)["sig"]["id"] == first)
bar_buttons(bob)["decline"].dispatch("on_release")
check("Decline: the bar and its tone go, cara is told; bob's call goes on",
      settle(parties, lambda: cara.call is None) and waiting(bob) is None and
      bar.parent is None and not tones[-1].on and
      events_of(cara, B)[-1] == "Call declined" and
      bob.call is not None and bob.call.state == "active")
card = bob.store.call_card(K, first) or {}
check("...bob's chat with cara keeps a declined call's card",
      card.get("outcome") == "declined" and
      card.get("summary") == "Declined call", card)
cara_calls_bob()
cara.end_call()
check("cara gives up while it waits: the bar goes, a missed call for bob",
      settle(parties, lambda: waiting(bob) is None) and
      events_of(bob, K)[-1] == "Missed call" and bob.call.state == "active")
saved = M.CALL_OFFER_MAX_AGE
M.CALL_OFFER_MAX_AGE = 2
cara_calls_bob()
check("left waiting until too old to answer: missed for bob, no answer for "
      "cara, whose phone stops",
      settle(parties, lambda: waiting(bob) is None and cara.call is None, 8)
      and events_of(bob, K)[-1] == "Missed call" and
      events_of(cara, B)[-1] == "No answer")
M.CALL_OFFER_MAX_AGE = saved
cara_calls_bob()
bar_buttons(bob)["end and answer"].dispatch("on_release")
lobby = bob.__dict__.get("_lobby")
check("End and answer: bob's call with alice ends at once, and cara's opens "
      "its lobby, as answering any call does",
      lobby is not None and bob.call is not None and bob.call.peer == K and
      bob.call.state == "incoming" and bob._call_overlay is None and
      settle(parties, lambda: alice.call is None))
lobby.join()
check("...confirmed there, bob and cara talk",
      settle(parties, lambda: cara.call is not None and bob.call is not None
             and cara.call.state == bob.call.state == "active"))
place_call(alice, B)
settle(parties, lambda: waiting(bob) is not None)
cara.end_call()
check("a call that ends by itself while another waits: that one rings, as "
      "any call does",
      settle(parties, lambda: bob.call is not None and bob.call.peer == A and
             bob.call.state == "incoming") and waiting(bob) is None)
bob.decline_call()
settle(parties, lambda: alice.call is None and bob.call is None)
check("in a group's call, its answer is Leave and answer; in none (a call "
      "for another identity), Answer",
      M.waiting_answer(True, True) == "Leave and answer" and
      M.waiting_answer(False, True) == "End and answer" and
      M.waiting_answer(False, False) == "Answer")

print("\nSend later, and planned calls (17.2, SCHEDULE)")
at = time.time() + 3600
later = alice.send_text(B, "see you at the station", send_at=at)
settle(parties, lambda: not mail.mail, 1)
row = alice.store.messages_by_ids([later])[0]
check("a message sent later waits on this device, with its time: not in "
      "the outbox, nothing sent",
      row["status"] == M.MSG_SCHEDULED and M.message_ext(row)["at"] ==
      int(at) and all(m["id"] != later for m in alice.store.outbox()) and
      not any(m["body"] == "see you at the station"
              for m in bob.store.messages_for(A)))
check("...not yet due: it stays", alice.send_due_scheduled(now=at - 60) == 0)
went = alice.send_due_scheduled(now=at + 1)
check("its time come, it goes, as written then; the time it was kept for "
      "stays here",
      went == 1 and settle(parties, lambda: any(
          m["body"] == "see you at the station" and
          "at" not in M.message_ext(m) for m in bob.store.messages_for(A))))
plan_id = alice.plan_call(B, time.time() + 240, True)
settle(parties, lambda: any(p == A for p, _plan, _d in bob.store.plans()))
plans = [plan for p, plan, _d in bob.store.plans() if p == A]
check("a planned call reaches the other side as its card's plan",
      plan_id is not None and len(plans) == 1 and plans[0]["v"] and
      M.plan_state(plans[0]) == "open")
first, again = bob.remind_plans(), bob.remind_plans()
check("...its reminder comes once, five minutes before",
      first == [A] and again == [])
row = next(m for m in bob.store.messages_for(A)
           if M.message_ext(m).get("plan"))
card = M.PlanCardRow(row, bob.conversation_screen)
check("...and its card offers Join near its time",
      card.join is not None and
      M.plan_state(dict(plans[0], at=time.time() + 3 * 86400)) == "soon" and
      M.plan_state(dict(plans[0], at=time.time() - 3 * 86400)) == "past")

print("\nA group call's link, opened (17.2, CALLLINK)")
team = "ab" * 16
alice.store.save_group({"id": team, "name": "Team", "admin": B, "v": 1,
                        "members": [{"a": A, "k": alice.keys.public_key_hex},
                                    {"a": B, "k": bob.keys.public_key_hex}]})
meeting = M.CallSession(M.group_peer(team), B, group=True, video=False,
                        host=B)
meeting.room, meeting.token, meeting.key = "cd" * 8, "ef" * 32, \
    M.b64e(b"k" * 32)
alice.open_link(M.call_link(meeting, "https://relay.example"))
lobby = alice.__dict__.get("_lobby")
check("a member who taps it in any chat: the call's lobby, not a page",
      lobby is not None and lobby.session.room == meeting.room and
      lobby.session.group)
lobby.cancel()
outside = M.CallSession(M.group_peer("ef" * 16), B, group=True, video=False,
                        host=B)
outside.room, outside.token, outside.key = meeting.room, meeting.token, \
    meeting.key
alice.open_link(M.call_link(outside, "https://relay.example"))
check("...anyone not in its group is told it is not theirs",
      alice.__dict__.get("_lobby") is None and M.Toast.current is not None
      and "members of its group" in M.Toast.current.text)

print("\nAnother identity's calls (17.2, IDCALLS)")
priv_w, pub_w, B2, _n = M.mine_identity()
bob.keys.add_identity(priv_w, "bob at work")
bob.keys.activate(B2)
bob.keys.set_username("bobwork")
bob.keys.activate(B)


def test_link(address):
    """bob's connection for another of his identities, as the app makes
    one, on the stand-in relay (its mail delivered by the test)."""
    link = M.IdentityLink(bob, address)
    link.api = StubApi(mail, link.keys, relay.addr)
    link.worker = M.SyncWorker(link)
    link._drain_ui_queue = lambda _dt: None
    parties[address] = link
    return link


def bob_worker():
    bob.worker = M.SyncWorker(bob)
    parties[bob.keys.address] = bob


bob._start_link, bob._start_worker = test_link, bob_worker
bob._api_for = lambda keys: StubApi(mail, keys, relay.addr)
bob._update_links()
work = bob.__dict__["_links"].get(B2)
check("bob's other identity, which has a name: connected on its own, "
      "the one in use unchanged; the background service would keep both",
      work is not None and work.keys.address == B2 and bob.keys.address == B
      and M.service_identities(bob.keys) == [B, B2])
try:
    work.keys._save()
    wrote = True
except RuntimeError:
    wrote = False
check("...its own view of the identities never writes their file",
      not wrote)
work.store.upsert_contact(A, "alice", alice.keys.public_key_hex)
alice.store.upsert_contact(B2, "bobwork", pub_w.hex())
alice.store.add_caps(B2, M.LocalStore.CAP_V9 | M.LocalStore.CAP_CALL)
place_call(alice, B2)
declined_id = alice.call.id if alice.call else None
settle(parties, lambda: waiting(bob) is not None)
bar = waiting(bob) and waiting(bob)["bar"]
words = {w.text for w in bar.walk() if isinstance(w, M.Label)} if bar \
    else set()
check("alice calls bob at work: it rings in the bar, with that identity's "
      "name, Decline and Answer; bob stays who he was",
      bar is not None and "alice is calling" in words and
      "Voice call · for @bobwork" in words and
      set(bar_buttons(bob)) == {"decline", "answer"} and
      bob.keys.address == B and bob.call is None, words)
bar_buttons(bob)["decline"].dispatch("on_release")
check("Decline: alice is told, and the card is in bob at work's chat",
      settle(parties, lambda: alice.call is None) and
      events_of(alice, B2)[-1] == "Call declined" and
      (work.store.call_card(A, declined_id) or {}).get("outcome") ==
      "declined")
place_call(alice, B2)
settle(parties, lambda: waiting(bob) is not None)
bar_buttons(bob)["answer"].dispatch("on_release")
lobby = bob.__dict__.get("_lobby")
check("Answer: bob is now bob at work, in alice's call's lobby",
      bob.keys.address == B2 and lobby is not None and
      bob.call is not None and bob.call.peer == A and
      B in bob.__dict__["_links"] and B2 not in bob.__dict__["_links"])
lobby.join()
check("...confirmed there, they talk",
      settle(parties, lambda: alice.call is not None and bob.call is not None
             and alice.call.state == bob.call.state == "active"))
alice.end_call()
settle(parties, lambda: alice.call is None and bob.call is None)
bob._activate_identity(B)
M.set_identity_rings(B2, False)
bob._update_links()
check("its bell off (Identities): no connection of its own, so its calls "
      "do not ring here", B2 not in bob.__dict__["_links"] and
      M.service_identities(bob.keys) == [B])
M.set_identity_rings(B2, True)
bob._update_links()

saved = M.CALL_RING_SECONDS
M.CALL_RING_SECONDS = 1
place_call(alice, B)
settle(parties, lambda: bob.call is not None)
check("unanswered, it gives up: no answer for alice, missed for bob",
      settle(parties, lambda: alice.call is None and bob.call is None, 8) and
      events_of(alice, B)[-1] == "No answer" and
      events_of(bob, A)[-1] == "Missed call")
M.CALL_RING_SECONDS = saved

told = []
real_send = bob._send_control_to
bob._send_control_to = lambda a, op, fb, pub=None: (
    told.append(op), real_send(a, op, fb, pub))[1]
stale = {"op": "call", "id": "f" * 32, "tag": "e" * 16,
         "k": M.new_call_key()[1], "codecs": ["ima16"], "c": [],
         "t": int(time.time())}
mail.held = 600                      # the relay took it ten minutes ago
alice._send_control_to(B, stale, "Voice call")
mail.held = 0
settle(parties, lambda: not mail.mail)
check("an offer the relay took long ago rings nothing: missed",
      bob.call is None and events_of(bob, A)[-1] == "Missed call")
missed = bob.store.call_card(A, "f" * 32) or {}
check("...a missed call's card all the same, from alice (17.1)",
      missed.get("outcome") == "missed" and missed.get("host") == A,
      missed)
check("...and the caller is told, in case it rings there still (17.1)",
      settle(parties, lambda: any(
          op.get("op") == "end" and op.get("why") == "timeout" and
          op.get("id") == "f" * 32 for op in told)))
skewed = dict(stale, id="d" * 32, t=int(time.time()) - 600)
alice._send_control_to(B, skewed, "Voice call")
check("a caller whose clock is ten minutes behind still rings: an offer is "
      "timed by the relay's clock, not the caller's (17.1)",
      settle(parties, lambda: bob.call is not None and
             bob.call.id == "d" * 32))
bob.decline_call()
settle(parties, lambda: bob.call is None)
bob._send_control_to = real_send

bob.set_blocked([A], True)
place_call(alice, B)
settle(parties, lambda: not mail.mail, 2)
check("someone you blocked cannot make your phone ring", bob.call is None)
alice.end_call()
settle(parties, lambda: alice.call is None)
bob.set_blocked([A], False)

alice.open_conversation(alice.keys.address)
check("saved messages have no call button",
      alice.conversation_screen.call_button.parent is None)

print("\n17.1: who can be called, and the relay's clock")
priv2, pub2, addr2, _n = M.mine_identity()
alice.keys.add_identity(priv2, "alice at work")
alice.keys.activate(A)                  # adding one switched to it
alice.store.upsert_contact(addr2, "alicework", pub2.hex())
alice.store.add_caps(addr2, M.LocalStore.CAP_V9 | M.LocalStore.CAP_CALL)
check("another identity on this same device is not called: one device is "
      "not both ends of a call", "on this device" in
      (alice.call_blocker(addr2) or ""), alice.call_blocker(addr2))
alice.store.upsert_contact(K, "cara", cara.keys.public_key_hex)
alice.store.add_caps(K, M.LocalStore.CAP_V9)
check("an app from before calls is named too old",
      "newer Ferritext" in (alice.call_blocker(K) or ""))
dave = make_party("dave", mail, relay.addr)
D = dave.keys.address
parties[D] = dave
dave.store.upsert_contact(B, "bob", bob.keys.public_key_hex)
dave.send_text(B, "hello, it's dave")
settle(parties, lambda: not mail.mail and bob.store.is_request(D))
check("someone whose request waits cannot be called yet",
      "accept your chat request" in (dave.call_blocker(B) or ""))


def call_cards(app, peer):
    return [M.json.loads(m["ext"])["call"]
            for m in app.store.messages_for(peer) if m["kind"] == "call"]


# 18.1: the try is told to them, as a missed call under Message requests.
words, real_words = [], dave._set_status
dave._set_status = lambda text, *a, **k: words.append(text)
dave.start_call(B, video=True)
settle(parties, lambda: not mail.mail and call_cards(bob, D))
cards = call_cards(bob, D)
check("calling them all the same tells them of it: a missed video call's "
      "card in the chat, which still waits under Message requests, and "
      "nothing rings",
      len(cards) == 1 and cards[0]["outcome"] == "missed" and
      cards[0]["video"] and cards[0]["host"] == D and
      bob.store.is_request(D) and bob.call is None and dave.call is None,
      cards)
check("...the caller is told that they will see it",
      len(words) == 1 and "will see that you called" in words[0], words)
dave.start_call(B)
settle(parties, lambda: not mail.mail)
check("...and pressed again within the minute, it is said again and sent "
      "once", len(words) == 2 and len(call_cards(bob, D)) == 1)
dave._set_status = real_words
offer = {"op": "call", "id": "ab" * 16, "tag": "cd" * 8, "k": "ef" * 32,
         "c": [], "codecs": ["opus"], "t": int(time.time()), "v": False}
for _ in range(2):          # an app that sends a call's offer all the same
    dave._send_control_to(B, dict(offer), "Voice call")
settle(parties, lambda: not mail.mail and len(call_cards(bob, D)) > 1)
check("a call's own offer from someone not yet accepted does not ring "
      "either: a missed call's card, once for the call",
      len(call_cards(bob, D)) == 2 and bob.call is None,
      call_cards(bob, D))
check("...and the one called accepts the request before calling back",
      "Accept their chat request" in (bob.call_blocker(D) or ""),
      bob.call_blocker(D))
bob.accept_request(D)
check("accepting a chat request tells them, so they can call at once",
      settle(parties, lambda: dave.store.get_contact(B)["caps"] &
             M.LocalStore.CAP_CALL) and dave.call_blocker(B) is None)

print("\n17.1: the cards say who called, and call back")
bob.open_conversation(A)
row = M.CallCardRow({"ext": M.json.dumps({"call": missed})},
                    bob.conversation_screen)


def buttons(widget):
    return [(w.text if hasattr(w, "text") else w.label.text).lower()
            for w in widget.walk() if isinstance(w, M.Btn)]


check("a missed call's card: on alice's side of bob's chat, in red, with "
      "Call back", row.anchor_x == "left" and
      M.direct_card_title(row.card.session, "alice") == "Missed voice call"
      and "call back" in buttons(row.card), buttons(row.card))
row.card.on_call_back(row.card.session)
lobby = bob.__dict__.get("_lobby")
check("...which calls her back: the lobby, for a voice call to alice",
      lobby is not None and lobby.session.chat == A and
      not lobby.session.video and lobby.session.host == B)
lobby.cancel()
mine = M.CallCardRow({"ext": M.json.dumps({"call": alice.store.call_cards(
    B)[1]})}, alice.conversation_screen)
check("an unanswered call one made: on one's own side, Call again",
      mine.anchor_x == "right" and "call again" in buttons(mine.card),
      (mine.anchor_x, buttons(mine.card)))
talked = M.session_of_card(dict(missed, outcome="answered", started=100,
                                ended=172), B)
answered = M.CallCard(talked, bob.call_name, on_call_back=print)
check("an answered one: how long it lasted, and no button",
      answered.clock_label.text == "Lasted 1:12" and
      not buttons(answered), (answered.clock_label.text, buttons(answered)))

print("\n17.1: someone the relay no longer knows, found before a send")
looked = []


def lookup(address=None, username=None):
    looked.append(address)
    if address == K:
        raise M.ApiClient.ApiError(404, "not_found", "no such user")
    return {"address": address}


alice.api.lookup = lookup
alice.open_conversation(K)
check("opening a chat asks the relay after its person: gone, Deleted User",
      settle(parties, lambda: (alice.store.get_contact(K) or {}).get(
          "gone")) and looked == [K], looked)
alice.open_conversation(K)
alice.check_recent_chats()
settle(parties, lambda: len(looked) > 1, 1.5)
check("...asked again only hours later; once connected, the newest chats "
      "not asked lately", looked.count(K) == 1 and B in looked, looked)

from email.utils import formatdate
from datetime import timedelta
api = M.ApiClient(alice.keys)


class Answer:
    headers = {"Date": formatdate(time.time() + 300, usegmt=True)}
    elapsed = timedelta(milliseconds=200)


api._read_clock(Answer())
check("the relay's clock is read from its answers' Date",
      298.5 < api.clock_offset < 301.5, f"{api.clock_offset:.2f} s")
check("...requests are signed in its time: a wrong clock here signs in",
      abs(api.auth_fields("x")["timestamp"] - (time.time() + 300)) < 2)
check("...and an offer's age is the relay's, whatever the caller's clock",
      9 < M.call_offer_age({"relay_at": api.relay_now() - 10, "t": 5},
                           api) < 11)
check("the app names its version and platform to the relay, nothing else",
      api._session.headers["User-Agent"] == M.USER_AGENT and
      re.fullmatch(r"Ferritext/[0-9.]+ \(\w+; Python 3\.\d+\)",
                   M.USER_AGENT), M.USER_AGENT)

print("\n17.3: React in a 1-to-1 call, the relay's notices and refusals, "
      "Android 5")
engines, events = pair()
time.sleep(0.3)
engines[0].react("\U0001F44D")
heard = wait_until(lambda: any(k == "react" for k, _v in events[1]), 3)
time.sleep(0.6)                       # the two copies after it come too
reacts = [v for k, v in events[1] if k == "react"]
finish(engines)
check("REACT1: a reaction crosses as its own sealed packet, sent three "
      "times and heard once", heard and reacts == ["\U0001F44D"], reacts)
place_call(alice, B)
settle(parties, lambda: bob.call is not None and
       bob._call_overlay is not None)
answer(bob)
on = settle(parties, lambda: alice.call is not None and
            bob.call is not None and alice.call.state == "active" and
            bob.call.state == "active")
alice.react_in_call("\U0001F602")
shown = settle(parties, lambda: bob._call_overlay is not None and
               bob._call_overlay.their_reaction.text == "\U0001F602")
check("...in a real call: More has React; the emoji shows over the sender's "
      "side on both screens, as a group's does", on and shown and
      alice._call_overlay.my_reaction.text == "\U0001F602",
      (on, shown, alice._call_overlay.my_reaction.text))
label = bob._call_overlay.their_reaction
check("...by their disc, in the screen's upper half (placed by the layout, "
      "not where the screen was before it was laid out)",
      label.center_y > label.parent.center_y and label.x > 0,
      (label.center, label.parent.size))
bob._heard_reaction(bob.call, "free text")
check("...only the quick reactions are shown, as in a group's call",
      label.text == "\U0001F602", label.text)
overlay = alice._call_overlay
overlay.mine.opacity = 1                  # her camera on: her corner shown
alice.react_in_call("\U0001F64F")
check("...with your camera on, yours is drawn once, in your corner",
      overlay.my_reaction.text == "" and
      overlay.mine.reaction.text == "\U0001F64F",
      (overlay.my_reaction.text, overlay.mine.reaction.text))
overlay.mine.opacity = 0
member = bob.call.session.members.get(A)
member.reaction_seen -= M.CALL_REACTION_SECONDS + 1
bob._call_overlay.show_reactions()
check("...and goes after four seconds (the group's timing)",
      bob._call_overlay.their_reaction.text == "")
alice.end_call()
settle(parties, lambda: bob.call is None or bob.call.ended)

refusal = M.ApiClient.ApiError(403, "restricted", "stopped", {
    "what": "calls", "until": time.time() + 3600})
words = M.restricted(refusal)
check("the relay's 403 restricted in words: what, and until when",
      words is not None and "operator" in words and "calls" in words and
      "until" in words and M.restricted(RuntimeError("x")) is None, words)
real_ticket = alice.api.call_ticket


def refused_ticket():
    raise refusal


alice.api.call_ticket = refused_ticket
said = []
real_status = alice._set_status
alice._set_status = lambda text, *a, **k: said.append(text)
place_call(alice, B)
ended = settle(parties, lambda: alice.call is None or alice.call.ended)
alice.api.call_ticket = real_ticket
alice._set_status = real_status
check("calls stopped by the relay's operator: the call ends at once, "
      "saying so", ended and any("operator" in s for s in said), said)
settle(parties, lambda: bob.call is None or bob.call.ended)
while not alice.ui_queue.empty():
    alice.ui_queue.get_nowait()
alice._not_sent(B, 1, "refused", "", M.ApiClient.ApiError(
    403, "restricted", "stopped", {"what": "send", "until": None}))
kind, text, _colour = alice.ui_queue.get_nowait()
check("a message refused so says it waits here, and goes when that ends",
      kind == "status" and "operator" in text and "waits here" in text,
      text)
row = {"id": 99, "peer": B, "kind": "text", "body": "hi", "ttl": 0,
       "client_msg_id": "c99", "ext": "{}", "members": None}
in_group = dict(row, id=98, client_msg_id="c98", members=[B])
real_outbox, real_send_row = alice.store.outbox, alice._send_row
alice.store.outbox = lambda cutoff: [in_group, row]
tried = []
alice._send_row = lambda local_id, *a, **k: tried.append(local_id) or True
left = alice.flush_outbox()                         # every message stopped
tapped = alice.flush_outbox(include_old=True, only_id=99)
alice._stops.clear()
alice._note_stopped(M.ApiClient.ApiError(403, "restricted", "stopped", {
    "what": "groups", "until": time.time() + 60}))
groups_left = alice.flush_outbox()                  # only groups stopped
alice._stops.clear()
alice.store.outbox, alice._send_row = real_outbox, real_send_row
check("...and the outbox leaves what is stopped until the stop ends, rather "
      "than asking every 30 s; a tap still tries it",
      left == 0 and tapped == 1 and groups_left == 1 and tried == [99, 99],
      (left, tapped, groups_left, tried))

sent_frames = []
alice.worker.send_ws = lambda frame: sent_frames.append(frame) or True
for _ in range(2):
    alice.worker._handle_frame({"type": "notice", "id": 7, "at": 1.7e9,
                                "text": "Please stop the spam."})
    alice._drain_ui_queue(0)
opened = [w for w in M.Window.children if isinstance(w, M.NoticeSheet)]
check("a notice from the relay's operator is shown once, in its sheet, and "
      "the relay is told each time it was seen", len(opened) == 1 and
      sent_frames == [{"type": "notice_seen", "ids": [7]}] * 2,
      (len(opened), sent_frames))
for sheet in opened:
    sheet.dismiss(animation=False)

real = (M.IS_ANDROID, list(M._SDK))
try:
    M.IS_ANDROID, M._SDK[:] = True, [21]
    blocked = alice.call_blocker(B)
    group_blocked = M.android_calls_blocker()
    M._SDK[:] = [23]
    allowed = M.android_calls_blocker()
finally:
    M.IS_ANDROID, M._SDK[:] = real
check("ANDROID5: on Android 5 the call buttons say calls need Android 6; "
      "from Android 6 nothing stops them", blocked and "Android 6" in
      blocked and group_blocked == blocked and allowed is None, blocked)

print("\n17.4: several calls in a group's chat, and new calls kept to its "
      "admin")
crew = "be" * 16
cpeer = M.group_peer(crew)
crew_state = {"id": crew, "name": "Crew", "admin": A, "v": 1,
              "members": [{"a": A, "k": alice.keys.public_key_hex},
                          {"a": B, "k": bob.keys.public_key_hex}]}
bob.store.save_group(crew_state)
features = bob.api._features
bob.api._features = dict(features, rooms=True)


def live_call(host, video=False):
    """A group call going on in the crew's chat, as bob's app knows it."""
    s = M.CallSession(cpeer, B, group=True, video=video, host=host)
    s.room, s.token, s.key = os.urandom(8).hex(), os.urandom(32).hex(), \
        M.b64e(os.urandom(32))
    bob._call_sessions()[s.id] = s
    bob.__dict__.setdefault("_watched", {})[s.id] = time.monotonic()
    bob.store.add_call_card(cpeer, M.card_of_session(s))
    return s


menus = []


class Menu:
    """MenuSheet, recorded instead of shown."""

    def __init__(self, items, title=""):
        menus.append((title, [text for _i, text, *_r in items], items))

    def open(self):
        pass


def tap_call():
    """The chat's call button, with what it offers recorded."""
    menus.clear()
    real, M.MenuSheet = M.MenuSheet, Menu
    try:
        bob.start_group_call(cpeer)
    finally:
        M.MenuSheet = real
    lobby = bob.__dict__.get("_lobby")
    if lobby is not None:
        lobby.cancel()
    return lobby


lobby = tap_call()
check("no call going on: the call button opens a new call's lobby",
      lobby is not None and lobby.session.host == B and not menus and
      bob.live_calls_in(cpeer) == [])
first = live_call(A, video=True)
lobby = tap_call()
check("one going on: join it, or start another beside it",
      lobby is None and menus and menus[0][1] == [
          "Join alice's call", "Start another call"], menus)
menus[0][2][1][2]()
lobby = bob.__dict__.get("_lobby")
check("...another is a new room in the same chat",
      lobby is not None and lobby.session.room is None and
      lobby.session.chat == cpeer and lobby.session.id != first.id)
lobby.cancel()
second = live_call(B)
check("two going on: both offered, newest first",
      [s.id for s in bob.live_calls_in(cpeer)] == [second.id, first.id] and
      tap_call() is None and menus[0][1] == [
          "Join bob's call", "Join alice's call", "Start another call"])
bob.open_conversation(cpeer)
bob.show_call_banner(second)
banner = bob.conversation_screen.call_banner
check("...and the chat's banner says how many, its tap the same choice",
      banner is not None and [w.text for w in banner.children][::-1] == [
          "2 calls in progress", "Choose & join"])

bob.store.save_group(dict(crew_state, v=2, r={"deny": {
    "calls": time.time() - 5}}))
check("with new calls kept to the admin, the calls going on are still "
      "offered, and no new one", tap_call() is None and menus[0][1] == [
          "Join bob's call", "Join alice's call"])
second.move("ended")
lobby = tap_call()
check("...with one going on, the button joins it",
      lobby is not None and lobby.session.id == first.id and not menus)
first.move("ended")
M.Toast.current = None
check("...with none, it says only the admin can start one",
      tap_call() is None and not menus and M.Toast.current is not None and
      M.Toast.current.text == "Only the group's admins can start calls here")
menus.clear()
real, M.MenuSheet = M.MenuSheet, Menu
try:
    bob.conversation_screen.plan_call_menu()
finally:
    M.MenuSheet = real
check("...nor plan one", not menus)
bob.api._features = features

print("\nOne identity on two devices (18.1)")
# The relay hands what is sent to an address to every device connected as
# it: each rings. Here: carol calls dan, who is on a phone and a computer.


def another_device(app):
    """`app`'s identity on a second device, as restoring a backup there
    puts it (its post-quantum key with it): its own app and database."""
    app.keys.ensure_pq_seed(app.keys.address)
    twin = M.FextApp()
    twin._dismiss_splash = lambda *_a: None
    twin._probe_transport = lambda: None
    twin.build()
    twin.keys._data["identities"] = [dict(app.keys.find(app.keys.address))]
    twin.keys.activate(app.keys.address)
    twin.crypto = M.CryptoEngine(twin.keys)
    twin.api = StubApi(mail, twin.keys, relay.addr)
    twin.store = M.LocalStore(os.path.join(tempfile.mkdtemp(), "twin.db"))
    twin.worker = M.SyncWorker(twin)
    twin.foreground = True
    return twin


def settle_devices(devices, cond, timeout=6.0):
    """settle(), each message handed to every device of its address."""
    def pump():
        Clock.tick()
        while mail.mail:
            msg = mail.mail.pop(0)
            for app in devices.get(msg["recipient"], ()):
                app.worker._ingest_message(dict(msg))
        for apps in devices.values():
            for app in apps:
                app._drain_ui_queue(0)
    return wait_until(cond, timeout, tick=pump)


mail.mail.clear()
for app in parties.values():             # the earlier parties' calls: over
    if getattr(app, "call", None) is not None and not app.call.ended:
        app._finish_call("hangup", local=False)
carol, dan = (make_party(name, mail, relay.addr) for name in ("carol", "dan"))
C, DN = carol.keys.address, dan.keys.address
dan2 = another_device(dan)
carol.foreground = dan.foreground = True
devices = {C: [carol], DN: [dan, dan2]}
for me, other in ((carol, dan), (dan, carol), (dan2, carol)):
    me.store.upsert_contact(other.keys.address, other.keys.username,
                            other.keys.public_key_hex)
carol.send_text(DN, "hi")
dan.send_text(C, "hi")
settle_devices(devices, lambda: not mail.mail and carol.call_blocker(DN)
               is None)
place_call(carol, DN)
check("a call rings on every device of the identity called",
      settle_devices(devices, lambda: all(
          d.call is not None and d.call.state == "incoming"
          for d in (dan, dan2)) and carol.call.state == "ringing"),
      (dan.call, dan2.call, carol.call and carol.call.state))
first_key = carol.call.their_key
answer(dan2, mic=True)
check("answered on one of them, the call is with that device: its key "
      "for the call is the one the caller uses, whichever rang first",
      settle_devices(devices, lambda: carol.call.state in (
          "connecting", "active")) and
      carol.call.their_key == dan2.call.pub and first_key in (
          dan.call.pub if dan.call else None, dan2.call.pub),
      (carol.call.state, carol.call.their_key))
check("...and the other stops ringing at once, with a card that says "
      "where the call went: no missed call, and no word to the caller",
      settle_devices(devices, lambda: dan.call is None or dan.call.ended)
      and events_of(dan, C)[-1] == "Answered on another device" and
      not carol.call.ended and not dan2.call.ended,
      events_of(dan, C))
call_id = carol.call.id
for why in ("timeout", "declined", "busy"):
    # What a device left ringing (an app before 18.1) says in the end.
    dan._send_control_to(C, {"op": "end", "id": call_id, "why": why},
                         "Voice call")
settle_devices(devices, lambda: not mail.mail)
taken = M.session_of_card(call_cards(dan, C)[-1], DN)
card = M.CallCard(taken, dan.call_name, on_call_back=lambda s: None,
                  on_note=lambda s: None)
check("...its card there offers neither Call back nor a note: the call "
      "was had", taken.outcome == "elsewhere" and buttons(card) == [],
      buttons(card))
check("a device of theirs left ringing, which gives up or declines "
      "later, does not end the call that was answered",
      not carol.call.ended and not dan2.call.ended and
      carol.call.state in ("connecting", "active"), carol.call.state)
dan2.end_call()
check("...and the one that answered ends it as ever",
      settle_devices(devices, lambda: carol.call is None or
                     carol.call.ended))
settle_devices(devices, lambda: all(
    d.call is None for d in (carol, dan, dan2)), 4.0)
place_call(carol, DN)
settle_devices(devices, lambda: all(
    d.call is not None and d.call.state == "incoming" for d in (dan, dan2)))
dan.decline_call()
check("declined on one device, it stops on the other too, as a "
      "declined call, not a missed one",
      settle_devices(devices, lambda: (dan2.call is None or
                                       dan2.call.ended) and
                     (carol.call is None or carol.call.ended)) and
      events_of(dan2, C)[-1] == "Declined call" and
      events_of(carol, DN)[-1] == "Call declined",
      (events_of(dan2, C), events_of(carol, DN)))
try:
    dan2.keys.add_identity(dan2.keys._priv, "again")
    twice = "added twice"
except ValueError as exc:
    twice = str(exc)
check("the same identity cannot be added to one device twice",
      "already exists on this device" in twice, twice)

relay.stop = True

passed = sum(1 for _n, c in RESULTS if c)
print(f"\n  {passed}/{len(RESULTS)} passed")
os._exit(0 if passed == len(RESULTS) else 1)
