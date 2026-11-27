#!/usr/bin/env python3
"""A call's pictures (17.0): the ladder and a room's three sizes, pieces
and how they are put back together, coding and decoding through PyAV,
the camera and screen as FFmpeg reads them, and the pictures drawn in a
call's tiles.

    xvfb-run -a python3 desktop/test_video.py main.py

Needs PyAV (desktop/requirements-exe.txt; the Windows EXE has it). Without
it, only what a computer without video says is checked. FEXT_VIDEO_TEST
gives FFmpeg's moving test picture for the camera and the screen.
"""
import importlib.util
import os
import sys
import tempfile
import threading
import time
import types

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
if sys.platform.startswith("linux"):     # xvfb-run's X display
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-video-")
os.environ["USERPROFILE"] = os.environ["HOME"]   # Path.home() on Windows
os.environ["FEXT_LANG"] = "en"
os.environ["FEXT_VIDEO_TEST"] = "1"

_spec = importlib.util.spec_from_file_location(
    "fext_video", sys.argv[1] if len(sys.argv) > 1 else "main.py")
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_video"] = M
_spec.loader.exec_module(M)
from kivy.base import EventLoop                                # noqa: E402
M_SOURCE = open(_spec.origin, encoding="utf-8").read()

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""))


def finish():
    passed = sum(1 for _n, ok in RESULTS if ok)
    print(f"\n  {passed}/{len(RESULTS)} passed")
    sys.stdout.flush()
    os._exit(0 if passed == len(RESULTS) else 1)


def pump(seconds=0.05):
    end = time.time() + seconds
    while True:
        EventLoop.idle()
        if time.time() >= end:
            break
        time.sleep(0.005)


print("\nThe ladder and a room's sizes")
ladder = [(r.name, r.short, r.fps, r.kbps, r.sound) for r in M.VIDEO_LADDER]
check("the ladder as decided (19.0, V3): 360p24 at 0.5 Mbit/s, 720p30 "
      "at 2.5, 1080p30 at 4.5, 1080p60 at 8, each with its sound",
      ladder == [("360p", 360, 24, 500, "standard"),
                 ("480p", 480, 24, 1000, "standard"),
                 ("720p", 720, 30, 2500, "high"),
                 ("1080p", 1080, 30, 4500, "high"),
                 ("1080p60", 1080, 60, 8000, "ultra")], ladder)
check("a shared screen: 1080 lines at 30 frames a second (was 15), 8 "
      "Mbit/s its ceiling",
      (M.VIDEO_SCREEN.short, M.VIDEO_SCREEN.fps, M.VIDEO_SCREEN.kbps) ==
      (1080, 30, 8000))
check("HEVC and AV1 take two thirds of H.264's bits for the same rung",
      (M.video_kbps(8000, "h264"), M.video_kbps(8000, "hevc"),
       M.video_kbps(4500, "av1")) == (8000, 5333, 3000))
check("720p unless chosen otherwise", M.video_ceiling().name == "720p")
M.save_pref("video_quality", "1080p60")
check("...and as chosen", M.video_ceiling().name == "1080p60")
M.save_pref("video_quality", "720p")
layers = M.video_layers(M.VIDEO_LADDER[2])
check("a room's three sizes of 720p30: 180p15, 360p30, 720p30 at "
      "208, 625, 2500 kbit/s",
      layers == ((180, 15, 208), (360, 30, 625), (720, 30, 2500)), layers)
print("\nA computer's camera asked for what the call wants (19.0, V1)")
top = M.camera_tries("dshow", M.VIDEO_LADDER[4])
check("1080p60: the camera asked for 1920x1080 at 60, then at 30, then "
      "1280x720 at 30, each compressed (MJPEG) first; then as it comes",
      [(t.get("video_size"), t.get("framerate"), t.get("vcodec"))
       for t in top] ==
      [("1920x1080", "60", "mjpeg"), ("1920x1080", "60", None),
       ("1920x1080", "30", "mjpeg"), ("1920x1080", "30", None),
       ("1280x720", "30", "mjpeg"), ("1280x720", "30", None),
       (None, None, None)] and all(t["rtbufsize"] == "128M" for t in top),
      top)
low = M.camera_tries("v4l2", M.VIDEO_LADDER[2])
check("720p (Linux): 1280x720 at 30, compressed first, then as it comes; "
      "no 1080 asked",
      [(t.get("video_size"), t.get("input_format")) for t in low] ==
      [("1280x720", "mjpeg"), ("1280x720", None), (None, None)], low)
mac = M.camera_tries("avfoundation", M.VIDEO_LADDER[3])
check("a Mac: its own pixel format, no MJPEG to ask for",
      [t.get("video_size") for t in mac] == ["1920x1080", "1280x720", None]
      and all(t["pixel_format"] == "uyvy422" for t in mac), mac)
M.save_pref("camera_gave", {"Desk camera": [1280, 720, 30]})
M.save_pref("video_camera", "Desk camera")
check("Settings says what the camera gave, beside a quality it cannot "
      "give; nothing beside one it can",
      M.camera_gave("Desk camera") == (1280, 720, 30) and
      "1280x720" in M.camera_note(M.VIDEO_LADDER[3]) and
      M.camera_note(M.VIDEO_LADDER[2]) == "",
      M.camera_note(M.VIDEO_LADDER[3]))
M.save_pref("camera_gave", {})
M.save_pref("video_camera", None)
java = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "android", "VideoCall.java"), encoding="utf-8").read()
check("a phone's camera opens at the call's size and rate (VideoCall.java, "
      "read here, compiled by CI): the size nearest it, never wider than "
      "1920; a rate range that reaches 60 for 1080p60",
      "public VideoCall(Activity activity, int shortSide, int fps)" in java
      and "this(activity, 720, 30);" in java and
      "pickSize(map == null ? null" in java and
      "int tw = shortSide * 16 / 9, th = shortSide;" in java and
      "if (range.getUpper() != fps)" in java)
check("...and its coder is asked for the high profile, at the level the size "
      "needs, and a constant bitrate where it offers them, else set up as "
      "before",
      "CodecProfileLevel\n                            .AVCProfileHigh" in java
      and "BITRATE_MODE_CBR" in java and
      "layer.encoder.configure(format, null, null," in java and
      "catch (Exception refused)" in java)
check("...which a call asks for with Settings' quality; a video message's "
      "camera opens as before",
      "AndroidVideoCoder(video_ceiling())" in M_SOURCE and
      "_video_call_java()(activity, want.short, want.fps)" in M_SOURCE)
check("a screen's thumbnail moves at 5 frames a second",
      M.video_layers(M.VIDEO_SCREEN)[0][1] == 5)
check("a picture's size: its shape kept, both sides even, never larger",
      M.video_size(1280, 720, 360) == (640, 360) and
      M.video_size(720, 1280, 360) == (360, 640) and
      M.video_size(641, 361, 1080) == (640, 360), M.video_size(641, 361, 1080))
check("drawn to fill a tile or whole inside it; rows a multiple of 4",
      M.video_fit(1280, 720, 300, 300, True) == (532, 300) and
      M.video_fit(1280, 720, 300, 300, False) == (300, 168) and
      M.video_fit(640, 360, 2000, 2000, True) == (640, 360))
check("the best codec every receiver decodes",
      M.pick_video_codec(["av1", "hevc", "h264"],
                         [["hevc", "h264"], ["av1", "hevc", "h264"]])
      == "hevc" and
      M.pick_video_codec(["h264"], [["av1", "hevc", "h264"]]) == "h264" and
      M.pick_video_codec(["hevc"], [["h264"]]) is None)

print("\nPieces, and pictures put back together")
data = bytes(range(256)) * 10
pieces = M.video_pieces(data)
check("a picture cut into pieces of 1000 bytes, put back whole",
      [len(p) for p in pieces] == [1000, 1000, 560] and
      b"".join(pieces) == data)
a = M.VideoAssembler()
now = time.monotonic()
got = [a.put(0, i, 3, 1, M.VIDEO_KEY if i == 0 else 0, p, now)
       for i, p in enumerate(pieces)]
check("a key frame whole when its last piece comes",
      got[:2] == [None, None] and got[2] == (1, True, data) and
      not a.need_key)
check("a piece seen before, or of a picture handed on, is ignored",
      a.put(0, 1, 3, 1, 0, pieces[1], now) is None)
check("the next picture, in one piece", a.put(1, 0, 1, 1, 0, b"x", now) ==
      (1, False, b"x"))
a.put(2, 0, 2, 1, 0, b"a", now)                 # its second piece is lost
check("a later picture whole first: the one before given up, and only a "
      "key frame from then on",
      a.put(3, 0, 1, 1, 0, b"y", now) is None and a.need_key and
      a.lost == 1)
check("...the key frame decoded", a.put(4, 0, 1, 1, M.VIDEO_KEY, b"k", now)
      == (1, True, b"k") and not a.need_key)
a.put(5, 0, 2, 1, 0, b"a", now)
a.expire(now + 1)
check("a picture that waited too long is given up",
      a.need_key and a.lost == 2 and a.put(5, 1, 2, 1, 0, b"b", now) is None)
b = M.VideoAssembler()
check("a stream joined in the middle starts at its first key frame",
      b.put(500, 0, 1, 1, 0, b"d", now) is None and
      b.put(501, 0, 1, 1, M.VIDEO_KEY, b"k", now) == (1, True, b"k") and
      b.lost == 0)

print("\nWhat a 1-to-1 call's picture may take")
eng = M.CallEngine(os.urandom(16).hex(), os.urandom(8), 0, None, "",
                   on_event=lambda *_a: None, relay_pps=150)
eng.path, eng.packet_ms = "relay", 10
old_relay = eng._video_budget()
eng.relay_pps, eng.relay_kbps, eng.packet_ms = 1000, 8000, 5    # 13.2's
new_relay = eng._video_budget()
eng.path = "direct"
check("through a relay that takes 150 packets a second (13.0), what the "
      "sound leaves (30 pieces, 240 kbit/s), not the ladder's least it "
      "would drop; through 13.2, 6,240; straight, no limit",
      (old_relay, new_relay, eng._video_budget()) == (240, 6240, None),
      (old_relay, new_relay))
eng.sock.close()
M.save_pref("video_quality", "1080p60")


def relayed(decoders):
    """A 1080p60 camera's first plan through relay 13.2, drawn at 1080."""
    plans = []
    eng = M.CallEngine(os.urandom(16).hex(), os.urandom(8), 0, None, "",
                       on_event=lambda kind, value: plans.append(value)
                       if kind == "video_plan" else None,
                       relay_pps=1000, relay_kbps=8000)
    eng.path, eng.packet_ms = "relay", 5
    eng.media = {"flags": 2, "shows": {}}
    eng.their_media = {"shows": {"v": (1920, 1080), "s": (0, 0)},
                       "whole": 0, "lost": 0, "decoders": decoders}
    eng._adapt_video(100.0)
    eng.sock.close()
    return plans[-1] if plans else None


h264 = relayed(["h264"])
had = M._VIDEO_CAN.get("hw")
M._VIDEO_CAN["hw"] = ("hevc",)          # as on a computer with the chip
hevc = relayed(["hevc", "h264"])
if had is None:
    M._VIDEO_CAN.pop("hw", None)
else:
    M._VIDEO_CAN["hw"] = had
check("through relay 13.2 (6,240 kbit/s for a picture): 1080p60's 8 Mbit/s "
      "does not fit in H.264, 1080p30's 4.5 does; in HEVC 1080p60 does, "
      "at two thirds (5,333; where PyAV is, which codes it)",
      h264 == ("v", (1080, 30, 4500), "h264") and (
          hevc == ("v", (1080, 60, 5333), "hevc") or M._pyav() is None),
      (h264, hevc))
M.save_pref("video_quality", "720p")

print("\nA 1-to-1 camera the other side does not draw (17.2, NEXTCALLS)")
plans = []
eng = M.CallEngine(os.urandom(16).hex(), os.urandom(8), 0, None, "",
                   on_event=lambda kind, value: plans.append(value)
                   if kind == "video_plan" else None)
eng.media = {"flags": 2, "shows": {}}                  # this camera on


def drawn_at(size, now):
    eng.their_media = {"shows": {"v": size, "s": (0, 0)}, "whole": 0,
                       "lost": 0, "decoders": ["h264"]}
    eng._adapt_video(now)
    return plans[-1][1][0] if plans else None


big = drawn_at((1280, 720), 100.0)
hidden = drawn_at((0, 0), 103.0)
back = drawn_at((1280, 720), 106.0)
check("drawn at 720 lines it goes at 720p; not drawn, at the least rung "
      "(360p); drawn again, at 720p at once, not a rung every six seconds",
      (big, hidden, back) == (720, 360, 720), (big, hidden, back))
eng.sock.close()

print("\nSomeone leaving while new tiles are shown")
survived = []
for _attempt in range(3):
    recv = M.VideoReceiver(on_key=lambda *a: None)
    recv.start()
    done = threading.Event()

    def new_tiles(recv=recv, done=done):
        n = 0
        while not done.is_set():
            recv.show((f"tile{n}", 1), "v", (10, 10))
            n += 1
    shower = threading.Thread(target=new_tiles)
    shower.start()
    for i in range(3000):
        if not recv.is_alive():
            break
        recv.forget(("gone", i))
    time.sleep(0.2)
    done.set()
    shower.join()
    survived.append(recv.is_alive())
    recv.stop()
check("the pictures' thread goes on (it forgets the one who left while the "
      "screen adds tiles)", all(survived), survived)

print("\nA phone's codecs, when Java cannot be asked (17.1)")
asked = []
saved = M.IS_ANDROID, M.android_video_codecs, M._video_call_java
M.IS_ANDROID = True
M._VIDEO_CAN.pop("dec", None)
M.android_video_codecs = lambda which: asked.append(which) or None
first = M.video_decoders()
M.android_video_codecs = lambda which: asked.append(which) or ["hevc", "h264"]
second = M.video_decoders()
check("a phone whose Java could not be asked has no video for now, and is "
      "asked again: that answer is not kept", first == [] and
      second == ["hevc", "h264"] and len(asked) == 2, (first, second, asked))


class OlderJava:
    @staticmethod
    def codecs(encoders):
        return "hevc"            # a VideoCall that left H.264 out


M.IS_ANDROID, M.android_video_codecs = saved[0], saved[1]
M._video_call_java = lambda: OlderJava
check("...and H.264 is always among a phone's codecs, which every phone "
      "codes", M.android_video_codecs("decoders") == ["hevc", "h264"])
M._video_call_java = saved[2]
M._VIDEO_CAN.pop("dec", None)

print("\nA phone's decoder, and the self-test's camera check (17.2.1)")
# These are of the decoder's pictures as text, RGBA made in Java: what a
# video message's player asks for, and from 18.2 what a call falls back
# to where the phone's chip cannot draw I420 rows.
PICTURE = "4:2:" + M.b64e(bytes(32))        # 4 × 2 RGBA, as Java gives it


class StandInDecoder:
    """VideoCall.Decoder's decode, in Python: `room` pictures in at once,
    each done `cost` seconds after the one before it, handed out on a
    later call; "!" when there is no room."""

    def __init__(self, room=2, cost=0.01):
        self.room, self.cost = room, cost
        self.queue, self.ready, self.done_at, self.handed = [], [], 0.0, []
        self.made = 0

    def pictures(self):
        return self.made

    @staticmethod
    def refused():
        return "no room in the decoder"

    def decode(self, b64, w, h, fill):
        now = time.monotonic()
        while self.queue and self.done_at <= now:
            self.queue.pop(0)
            self.ready.append(PICTURE)
            self.made += 1
            self.done_at += self.cost
        if b64:
            if len(self.queue) >= self.room:
                return "!"
            if not self.queue:
                self.done_at = now + self.cost
            self.queue.append(b64)
            self.handed.append(b64)
        out = self.ready[-1] if self.ready else ""
        self.ready.clear()
        return out


class StandInCoder:
    """The camera's 24 pictures, as AndroidVideoCoder.poll gives them: the
    first a key frame (a phone's next is 10 s later)."""
    error = ""

    def __init__(self):
        self.frames = [(0, "h264", i == 0, bytes([i]) * 50)
                       for i in range(24)]

    def set_plan(self, plan, codec):
        pass

    def poll(self, wait):
        out, self.frames = self.frames, []
        return out

    def close(self):
        pass


def phone_decoder(java, rows=False):
    dec = object.__new__(M.AndroidVideoDecoder)   # no Java to make one
    dec._java, dec.codec, dec.rows = java, "h264", rows
    return dec


java = StandInDecoder()
dec = phone_decoder(java)
first = dec.decode(b"key", (4, 2), False)
java.queue = ["a", "b"]                            # the decoder busy
java.done_at = time.monotonic() + 60
try:
    dec.decode(b"next", (4, 2), False)
    refused = False
except ValueError:
    refused = True
check("a picture the phone's decoder has no room for is refused as a "
      "damaged one is, so the call waits for a key frame",
      first is None and refused and java.handed == [M.b64e(b"key")])
java.queue, java.ready = [], [PICTURE]
check("...and nothing handed in gives what came out late",
      dec.decode(b"", (4, 2), False) == (bytes(32), 4, 2, "rgba") and
      java.handed == [M.b64e(b"key")])

said = []
verdict = M._selftest_camera(lambda *a: said.append(a) or a[1],
                             StandInCoder(),
                             phone_decoder(StandInDecoder(cost=0.5)), pace=0)
check("the self-test's camera, handed over all at once as 17.2.0's was: "
      "a refusal, then nothing but the next key frame, and it fails, "
      "saying why the decoder refused",
      verdict == "FAIL" and
      " 1 refused (no room in the decoder)" in said[-1][2], said)
said.clear()
verdict = M._selftest_camera(lambda *a: said.append(a) or a[1],
                             StandInCoder(), phone_decoder(StandInDecoder()),
                             pace=0.025)
check("...at a call's pace every picture comes back, the last ones waited "
      "for", verdict == "PASS" and said[-1][2] ==
      "24 coded, 24 decoded, 0 refused", said)


class PairsDecoder(StandInDecoder):
    """An emulator's decoder (Devices runs 22 and 23, API 36): slower than
    the pace, so two pictures are done between one ask and the next."""

    def decode(self, b64, w, h, fill):
        if b64:
            self.handed.append(b64)
        done = len(self.handed) - self.made
        if done < 2 and b64:
            return ""
        self.made += done
        return PICTURE if done else ""


said.clear()
verdict = M._selftest_camera(lambda *a: said.append(a) or a[1],
                             StandInCoder(), phone_decoder(PairsDecoder()),
                             pace=0.005)
check("a decoder that makes two pictures between asks lost none: the "
      "self-test counts what was decoded, not what was handed out to draw "
      "(18.1; it failed an emulator on \"17 decoded\" of 24)",
      verdict == "PASS" and said[-1][2] ==
      "24 coded, 24 decoded, 0 refused, 12 drawn", said)



print("\nA phone's pictures as the decoder's own rows (18.2)")
import re                                                     # noqa: E402
from pathlib import Path                                      # noqa: E402
W, H = 64, 48
RED, BLUE = (81, 90, 240), (41, 240, 110)           # Y, U, V (BT.601)


def i420(top, foot):
    """A W × H picture of two halves as VideoCall.i420 packs it: its
    size, the rows of Y, then for each two rows U beside V."""
    y = bytes([top[0]]) * (W * H // 2) + bytes([foot[0]]) * (W * H // 2)
    under = b"".join(
        bytes([c[1]]) * (W // 2) + bytes([c[2]]) * (W // 2)
        for c in [top] * (H // 4) + [foot] * (H // 4))
    return M.struct.pack("!HH", W, H) + y + under


class RowsDecoder:
    """VideoCall.Decoder's planes, in Python: bytes in, the last
    picture's rows out (none where it is not drawn; one byte: refused)."""

    def __init__(self):
        self.calls, self.busy = [], False

    def planes(self, data, draw, most):
        self.calls.append((data, draw, most))
        if self.busy:
            return bytes(1)
        return i420(RED, BLUE) if draw else b""

    @staticmethod
    def refused():
        return "no room in the decoder"

    def decode(self, *_a):
        raise AssertionError("the text path, though rows can be drawn")


java = RowsDecoder()
dec = phone_decoder(java, rows=True)
got = dec.decode(b"coded", (360, 640), True)
check("a call's decoder on a phone is handed bytes and gives its rows back as "
      "bytes: no text, and the shorter side wanted said",
      java.calls == [(b"coded", True, 360)] and got is not None and
      got[1:] == (W, H, "i420") and len(got[0]) == W * H * 3 // 2,
      (java.calls, got and got[1:]))
check("...a picture decoded and not drawn copies nothing",
      dec.decode(b"late", None, True) is None and
      java.calls[-1] == (b"late", False, 0))
java.busy = True
try:
    dec.decode(b"next", (360, 640), True)
    refused = ""
except ValueError as exc:
    refused = str(exc)
check("...and one refused is refused as before, saying why",
      refused == "no room in the decoder", refused)
check("rows that are not the size their header says are no picture",
      M.unpack_planes(i420(RED, BLUE)[:-1]) is None and
      M.unpack_planes(b"") is None and
      M.unpack_planes(bytes(4)) is None)
rows = M.unpack_planes(i420(RED, BLUE))[0]
picture = M.YuvPicture(W, H)
picture.draw(rows)
pixels = picture.texture.pixels


def pixel(col, row):                   # row 0: the texture's lowest row
    at = (row * W + col) * 4
    return tuple(pixels[at:at + 3])


def near(colour, wanted):
    return all(abs(a - b) <= 6 for a, b in zip(colour, wanted))


check("the graphics chip makes the colours of them (BT.601): red is red, "
      "blue is blue, at the size of the picture",
      tuple(picture.texture.size) == (W, H) and
      near(pixel(5, H - 2), (255, 0, 0)) and
      near(pixel(W - 5, 2), (0, 0, 255)),
      (pixel(5, H - 2), pixel(W - 5, 2)))
check("...the right way up for a tile to show as it is: the picture's "
      "top half above its lower half, and no flip asked for",
      near(pixel(W // 2, H - 4), (255, 0, 0)) and
      near(pixel(W // 2, 3), (0, 0, 255)) and
      tuple(picture.texture.tex_coords) ==
      (0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0))
check("...and no plane bleeds into its neighbour at the picture's edges",
      all(near(pixel(c, H - 1), (255, 0, 0)) and
          near(pixel(c, 0), (0, 0, 255)) for c in (0, W // 2 - 1,
                                                  W // 2, W - 1)),
      [pixel(c, r) for r in (H - 1, 0) for c in (0, W // 2, W - 1)])


class RowsReceiver:
    """A VideoReceiver's latest, for a CallVideo's tick."""
    serial = 1

    def latest(self, _who, _track):
        return (self.serial, rows, W, H, "i420")

    def show(self, *_a):
        pass

    def stop(self):
        pass


class Tile:
    size = (W, H)
    refreshed = asked = 0

    def refresh(self):
        self.refreshed += 1

    class canvas:                                   # noqa: N801
        @staticmethod
        def ask_update():
            Tile.asked += 1


textures = {}
layer = M.CallVideo("me", textures, on_key=lambda *a: None,
                    on_failed=lambda *a: None)
layer.receiver.stop()
layer.receiver = RowsReceiver()
tile = Tile()
layer.view = {("ann", "v"): tile}
layer.tick()
first = textures.get(("ann", "v"))
layer.receiver.serial = 2
layer.tick()
check("a call shows them in the tile's own texture: made at the first "
      "picture, drawn into at the next",
      first is not None and tuple(first.size) == (W, H) and
      textures[("ann", "v")] is first and tile.refreshed == 1 and
      Tile.asked == 1, (first, tile.refreshed, Tile.asked))
real_picture = M.YuvPicture.__init__


def no_shader(self, *_a):
    raise RuntimeError("the picture's shader did not compile")


M.YuvPicture.__init__ = no_shader
layer.view = {("bea", "v"): tile}
layer.tick()                                        # must not raise
M.YuvPicture.__init__ = real_picture
check("a chip that refuses the shader is said once, and the phone's "
      "decoders are asked for RGBA as before from then on",
      ("bea", "v") not in textures and M.YuvPicture.usable is False)
text_java = StandInDecoder()
text_java.ready = [PICTURE]
check("...as text, the way 18.1 did it",
      phone_decoder(text_java, rows=True).decode(b"", (4, 2), False) ==
      (bytes(32), 4, 2, "rgba"))
M.YuvPicture.usable = True
layer.close()
said.clear()
drawer = threading.Thread(target=lambda: M._selftest_draw(
    lambda *a: said.append(a) or a[1]))
drawer.start()                # the check waits; the UI thread draws
while drawer.is_alive():
    M.Clock.tick()
    time.sleep(0.01)
check("the self-test has the device's own chip draw red over blue and "
      "reads it back (the emulators run it)",
      len(said) == 1 and said[0][:2] == ("video-draw", "PASS") and
      "red drawn (25" in said[0][2], said)
said.clear()
M.YuvPicture.__init__ = no_shader
drawer = threading.Thread(target=lambda: M._selftest_draw(
    lambda *a: said.append(a) or a[1]))
drawer.start()
while drawer.is_alive():
    M.Clock.tick()
    time.sleep(0.01)
M.YuvPicture.__init__ = real_picture
check("...and fails, saying why, on one that refuses",
      said[0][:2] == ("video-draw", "FAIL") and "shader" in said[0][2],
      said)


print("\n21.0 (NEXT29): each picture drawn as it comes")


class RgbaReceiver(RowsReceiver):
    def latest(self, _who, _track):
        return (self.serial, bytes(W * H * 4), W, H, "rgba")


layer = M.CallVideo("me", {}, on_key=lambda *a: None,
                    on_failed=lambda *a: None)
layer._clock.cancel()             # its 30 ticks a second: the push alone
layer.DRAW_MOST = 10              # a slow cap, to see it hold: each of the
#                                   clock's ticks here waits a 60th itself
layer.receiver.stop()
layer.receiver = RgbaReceiver()
layer.view = {("ann", "v"): Tile()}
layer.receiver.serial = 5
layer._soon()                     # (the receiver's thread) one is ready
M.Clock.tick()
first = layer._drawn.get(("ann", "v"))
M.Clock.tick()                    # the clock's time is its last tick's
layer._ticked = time.monotonic()  # as if one were drawn just now
layer.receiver.serial = 6
layer._soon()                     # and the next at once
M.Clock.tick()
soon = layer._drawn.get(("ann", "v"))
time.sleep(1.2 / layer.DRAW_MOST)
M.Clock.tick()
check("a picture ready is drawn at the UI thread's next frame (it waited "
      "for the next of 30 ticks a second), the one after it no sooner "
      "than DRAW_MOST a second allows",
      first == 5 and soon == 5 and layer._drawn.get(("ann", "v")) == 6,
      (first, soon, layer._drawn))
check("...a computer draws 60 a second at most, a phone 30",
      M.CallVideo.DRAW_MOST == (30 if M.IS_MOBILE else 60))
layer.close()


class OnePicture:
    codec = "h264"

    def decode(self, _data, box, _fill):
        return (bytes(W * H * 4), W, H, "rgba")


pings = []
receiver = M.VideoReceiver(lambda *a: None, decoder=lambda _c: OnePicture(),
                           on_picture=lambda: pings.append(1))
receiver.show("ann", "v", (W, H))
receiver._decode("ann", "v", "h264", b"a picture")
check("...the receiver says so as each one is decoded",
      pings == [1] and receiver.latest("ann", "v")[0] == 1, pings)


class BothWays(RowsDecoder):
    """A phone's decoder asked either way, as the self-test compares
    them: rows, or text."""
    asked = []

    def planes(self, data, draw, most):
        BothWays.asked.append("rows")
        return super().planes(data, draw, most)

    def decode(self, b64, w, h, fill):
        BothWays.asked.append("text")
        return PICTURE

    def close(self):
        pass


real_maker = M.android_video_decoder
M.android_video_decoder = lambda codec, rows=False: phone_decoder(
    BothWays(), rows)
told = M._selftest_paths([(0, "h264", True, b"k"), (0, "h264", False, b"p")],
                         pace=0)
M.android_video_decoder = real_maker
check("...and it times a picture both ways on the device, for the log: "
      "the rows' way and 18.1's text, each with a decoder of its own",
      re.fullmatch(r"a picture for a phone's tile: \d+\.\d ms as rows, "
                   r"\d+\.\d ms as text", told) and
      BothWays.asked == ["rows", "rows", "text", "text"] and
      M.YuvPicture.usable is True, (told, BothWays.asked))
java_source = (Path(sys.argv[1]).resolve().parent / "android" /
               "VideoCall.java").read_text(encoding="utf-8")
check("VideoCall.java (compiled by CI alone) has the rows' side of it: "
      "bytes in and out, the picture's crop, and no sum for any pixel",
      "public byte[] planes(byte[] data, boolean draw, int most)"
      in java_source and "static byte[] i420(Image image, int most)"
      in java_source and "image.getCropRect()" in java_source and
      "out = i420(image, most);" in java_source)

print("\nA phone's camera, which way up (18.1)")
# VideoCall.java is compiled by CI alone; its turns are read here.
import re                                                     # noqa: E402
from pathlib import Path                                      # noqa: E402

java = (Path(__file__).resolve().parent.parent / "android"
        / "VideoCall.java").read_text("utf-8")
check("the self-test's count of pictures decoded is Android's decoder's "
      "own: one for each picture it releases, drawn or not",
      "pictures++;" in java.split("class Decoder")[1] and
      "public int pictures()" in java)


def java_rule(name):
    """What the one-line method `name` returns, as Python."""
    body = re.search(r"\b%s\([^)]*\) \{\s*return (.+?);\s*\}" % name,
                     java, re.S).group(1)
    body = re.sub(r"(\w+) \? (.+?) : (.+)", r"(\2) if \1 else (\3)", body)
    return lambda **names: eval(body, {}, names)      # the file's own text


sent, lying = java_rule("sentTurn"), java_rule("lying")
# Android's rule for an upright picture: the camera's own turn (which the
# matrix it hands over has made already), and how the phone is held, the
# other way round for a front camera.
wrong = [(camera, front, held)
         for camera in (0, 90, 180, 270) for front in (False, True)
         for held in (0, 90, 180, 270)
         if (camera + sent(front=front, held=held)) % 360 !=
         (camera + (-held if front else held)) % 360]
check("the picture sent is turned by how the phone is held alone: with "
      "the turn Android has made already, that is Android's own rule for "
      "an upright picture, for every camera", not wrong, wrong)
check("...so a phone held upright sends an upright picture, and one "
      "turned on its side a lying one, upright still",
      lying(sensor=90, turn=sent(front=False, held=0)) and
      lying(sensor=270, turn=sent(front=True, held=0)) and
      not lying(sensor=90, turn=sent(front=False, held=90)) and
      not lying(sensor=270, turn=sent(front=True, held=270)))
check("...the camera's own turn is not made a second time (18.0.2: a "
      "picture on its side, squeezed), and the front camera's mirroring "
      "is taken out of what is sent and kept in this phone's own tile",
      "draw(sentTurn(), front)" in java and
      "draw(shownTurn(), false)" in java and
      "Matrix.setRotateM(pos, 0, -turn" in java and
      not re.search(r"sensor[^;]*\+ turn \+ 360", java))


class TurnedCoder(StandInCoder):
    def facts(self):
        return "front camera, sensor 270, held 0, page 0, from 1280x720: " \
               "352x640"


said.clear()
M._selftest_camera(lambda *a: said.append(a) or a[1], TurnedCoder(),
                   phone_decoder(StandInDecoder()), pace=0.025)
check("the self-test says which camera, its turns and the size coded",
      said[-1][2].endswith("0 refused; front camera, sensor 270, held 0, "
                           "page 0, from 1280x720: 352x640"), said)

print("\nA device that falls behind shows fewer pictures, not older "
      "ones (18.1)")


class CountingDecoder:
    """A phone's decoder as the receiver uses it: what it was given, and
    for what tile. `after`: how many asks later a picture comes out."""
    codec, hands_out_late = "h264", True

    def __init__(self, after=0):
        self.given, self.after, self.wait = [], after, None

    def decode(self, data, box, fill):
        self.given.append((bytes(data), box))
        if box is None:
            return None
        if data:
            self.wait = self.after
        if self.wait is None or self.wait > 0:
            self.wait = None if self.wait is None else self.wait - 1
            return None
        self.wait = None
        return (b"rgba", box[0], box[1], "rgba")


def whole_picture(number, key=False):
    flags = M.VIDEO_KEY if key else 0
    return M._VIDEO_PIECE.pack(1, flags | M.VIDEO_START, number, 0, 1) + b"p"


made = CountingDecoder()
behind = M.VideoReceiver(on_key=lambda *a: None, decoder=lambda codec: made)
behind.show("ann", "v", (360, 640))
now = time.monotonic()
behind._piece("ann", "v", whole_picture(0, key=True), now, 0.0)
first = behind.latest("ann", "v")
behind._piece("ann", "v", whole_picture(1), now, M.VIDEO_LATE + 0.2)
check("a picture in time is decoded for its tile and shown",
      first is not None and made.given[0] == (b"p", (360, 640)), made.given)
check("...one that waited longer than a picture may is decoded, as the "
      "next one needs it, and not made ready for the screen: nothing "
      "older than the last is shown",
      made.given[1] == (b"p", None) and behind.latest("ann", "v") == first,
      made.given)
behind._piece("ann", "v", whole_picture(2), now, 0.01)
check("...and the next in time is shown again",
      behind.latest("ann", "v")[0] == first[0] + 1)
check("...the one too late is counted, for the call's meter (18.2)",
      behind.late == 1, behind.late)
# 20.0: a device on which even decoding alone is too slow (an older
# phone): every picture was late, none was drawn, the picture stood still.
asked, strained = [], []
old_phone = M.VideoReceiver(on_key=lambda *a: asked.append(a),
                            decoder=lambda codec: CountingDecoder(),
                            on_strain=lambda: strained.append(1))
old_phone.show("ann", "v", (360, 640))
old_phone._piece("ann", "v", whole_picture(0, key=True), now, 0.0)
shown = old_phone.latest("ann", "v")
for n in range(5):                      # five more wait behind this one
    old_phone.put("ann", "v", whole_picture(2 + n))
old_phone.put("bob", "v", whole_picture(0, key=True))    # another's
old_phone._piece("ann", "v", whole_picture(1), now, old_phone.SKIP + 0.1)
check("a second behind, what waits of that picture is let go (another "
      "person's is kept) and a key frame asked for at once",
      old_phone._inbox.qsize() == 1 and asked == [("ann", "v", 0)] and
      old_phone.skips == 1 and old_phone.latest("ann", "v") == shown,
      (old_phone._inbox.qsize(), asked, old_phone.skips))
old_phone._piece("ann", "v", whole_picture(8), now + 0.1, 0.0)
check("...the pictures that follow wait for the key frame",
      old_phone.latest("ann", "v") == shown)
old_phone._piece("ann", "v", whole_picture(9, key=True), now + 0.2, 0.0)
check("...which is shown as it comes: the picture moves again, from now",
      old_phone.latest("ann", "v")[0] == shown[0] + 1 and
      old_phone.ease == 1 and not strained)
old_phone._piece("ann", "v", whole_picture(10), now + 5,
                 old_phone.SKIP + 0.1)
check("let go twice within half a minute: a picture half the size is "
      "asked for, and whoever says what this device shows is told",
      old_phone.ease == 2 and strained == [1] and old_phone.skips == 2,
      (old_phone.ease, strained))
old_phone._piece("ann", "v", whole_picture(11, key=True), now + 6, 0.0)
for at in (40, 45, 80, 85, 120, 125):
    old_phone._piece("ann", "v", whole_picture(at), now + at,
                     old_phone.SKIP + 0.1)
    old_phone._piece("ann", "v", whole_picture(at + 1, key=True),
                     now + at + 1, 0.0)
check("...a quarter at the least, however often after that",
      old_phone.ease == 4 and len(strained) == 2, (old_phone.ease, strained))
slow = CountingDecoder(after=2)
asking = M.VideoReceiver(on_key=lambda *a: None, decoder=lambda codec: slow)
asking.show("ann", "v", (360, 640))
asking._piece("ann", "v", whole_picture(0, key=True), now, 0.0)
check("a phone's decoder, which hands a picture out a moment after it is "
      "given one, is asked again at once while nothing else waits: the "
      "picture is shown now, not with the next one",
      asking.latest("ann", "v") is not None and
      [g[0] for g in slow.given] == [b"p", b"", b""], slow.given)
slow.given.clear()
asking.put("ann", "v", whole_picture(5))         # another already waits
asking._piece("ann", "v", whole_picture(1), now, 0.0)
check("...with another picture waiting, that one is not kept waiting",
      [g[0] for g in slow.given] == [b"p"], slow.given)
real_android = M.IS_ANDROID
M.IS_ANDROID = True
try:
    box = M.video_box((1080, 2200), "v")
    check("a phone asks for, and draws, another's camera at 360 lines at "
          "the most, a shared screen at 720, in the tile's shape",
          box[0] == 360 and abs(box[1] - 2200 / 3) < 0.01 and
          M.video_box((1080, 2200), "s")[0] == 720 and
          M.video_box((300, 500), "v") == (300, 500) and
          M.video_layer_for(1000) == 1 and M.video_layer_for(150) == 0,
          box)
    # 18.2: the phone's owner may raise it (Settings › Calls).
    M.save_pref("phone_picture", 480)
    check("...or what its owner chose: 480 lines of a camera, a shared "
          "screen as before, a room's picture still the middle size",
          M.phone_picture() == 480 and
          M.video_box((1080, 2200), "v")[0] == 480 and
          M.video_box((1080, 2200), "s")[0] == 720 and
          M.video_layer_for(1000) == 1)
    M.save_pref("phone_picture", 720)
    check("...at 720 a room's largest picture too",
          M.video_box((1080, 2200), "v")[0] == 720 and
          M.video_layer_for(1000) == 2 and M.video_layer_for(150) == 0)
    M.save_pref("phone_picture", 1080)
    check("...and a size it does not offer is not taken: 360",
          M.phone_picture() == 360 and
          M.video_box((1080, 2200), "v")[0] == 360)
finally:
    M.IS_ANDROID = real_android
    M.save_pref("phone_picture", None)
check("...a computer, which turns pictures over in time, as large as "
      "its tile", M.video_box((1080, 2200), "v") == (1080, 2200) and
      M.video_layer_for(1000) == 2)
check("...and a phone's decoder and coder give their pictures up at "
      "once: the decoder asked not to hold any back, the coders' taken "
      "a moment after each picture",
      "KEY_LOW_LATENCY" in java and
      "handler.postDelayed(takeCoded, 8)" in java)
decoder = java.split("class Decoder")[1]
check("...and its first picture, the key frame, waits for a decoder just "
      "started (Devices run 25: refused after 2 ms, and no picture after "
      "it could be decoded)",
      "codec.dequeueInputBuffer(at == 0 ? 100000 : 2000)" in decoder and
      "public String refused()" in decoder and
      decoder.count("!feed(data)") == 2)     # the text's way and the rows'
check("...asked only where the decoder says it can, and made again "
      "without if it still refuses: one asked for what it cannot do "
      "decoded nothing at all (Devices run 24, Android 11)",
      "FEATURE_LowLatency" in decoder and
      decoder.index("started(mime, true)") <
      decoder.index("catch (Exception refused)") <
      decoder.index("started(mime, false)") and
      "codec.release();\n                throw error;" in decoder)

sound = (Path(__file__).resolve().parent.parent / "android"
         / "CallAudio.java").read_text("utf-8")
check("a phone's speaker (18.1) is asked for Android's low-latency path, "
      "holds 40 ms of sound to begin with, not all it can, and is given "
      "more only when it runs dry twice in a second",
      "PERFORMANCE_MODE_LOW_LATENCY" in sound and
      "setBufferSizeInFrames(\n                    Math.min(depthMost, "
      "rateHz * 40 / 1000))" in sound and
      "gaps - gapsThen >= 2" in sound and "tune();" in sound)

if M._pyav() is None:
    print("\nNo PyAV here: what a computer without it says")
    check("the camera says what is missing",
          "PyAV" in M.call_video_why_not("v"))
    finish()

print("\nCoding and decoding (PyAV)")
check("this computer decodes H.264, HEVC and AV1 and codes H.264",
      M.video_decoders() == ["av1", "hevc", "h264"] and
      M.video_encoders() == ["h264"],
      (M.video_decoders(), M.video_encoders()))
source = M.video_sources("camera")[0]
coder = M.FfmpegVideoCoder(source)
coder.set_plan([(layer,) + spec for layer, spec in enumerate(layers)],
               "h264")
pictures, t0 = [], time.monotonic()
while time.monotonic() - t0 < 2.0:
    pictures += coder.poll(0.5)
by_layer = {n: [p for p in pictures if p[0] == n] for n in range(3)}
rates = {n: len(v) / 2.0 for n, v in by_layer.items()}
check("the camera coded at three sizes at once, each at its own rate",
      13 <= rates[0] <= 17 and 26 <= rates[1] <= 32 and 26 <= rates[2] <= 32,
      rates)
check("each size starts with a key frame, and has no other unasked",
      all(v and v[0][2] and not any(p[2] for p in v[1:])
          for v in by_layer.values()))
coder.ask_key(2)
later = []
while not later:
    later = [p for p in coder.poll(0.5) if p[0] == 2]
check("a key frame when asked", later[0][2])
dec = M.FfmpegDecoder("h264")
out = None
for _layer, _c, _k, data in by_layer[2]:
    out = dec.decode(data, (640, 360), True) or out
check("decoded and drawn at its tile's size", out is not None and
      out[1:] == (640, 360, "rgb") and len(out[0]) == 640 * 360 * 3,
      out and out[1:])
odd = M.FfmpegDecoder("h264")
for _layer, _c, _k, data in by_layer[1]:
    got = odd.decode(data, (100, 75), True)
check("a size whose rows FFmpeg pads comes out unpadded",
      got is not None and len(got[0]) == got[1] * got[2] * 3, got and got[1:])
kbits = {n: sum(len(p[3]) for p in v) * 8 / 2000 for n, v in by_layer.items()}
check("each size near its bitrate (kbit/s, the first second's key frame "
      "included)", kbits[2] < 2500 * 1.6 and kbits[1] < 625 * 1.6 and
      kbits[0] < 208 * 2.2, {n: round(k) for n, k in kbits.items()})
preview = coder.preview((160, 120))
check("this device's own picture, small, for its tile",
      preview is not None and preview[2:4] == (212, 120), preview and
      preview[2:4])

print("\nThe coder (19.0, V2)")
check("what is really sent, for the call's meter: the largest size, at the "
      "rate the camera gives",
      coder.sent() == (1280, 720, 30) and coder.capture.gave ==
      (1280, 720, 30), (coder.sent(), coder.capture.gave))
used = coder._coders[2][0]
check("a graphics chip's H.264 coders tried first; here (no chip) each "
      "failed and is not tried again; x264 codes",
      used.name == "libx264" and {"h264_nvenc", "h264_qsv", "h264_amf",
                                  "h264_videotoolbox"} <=
      M._VIDEO_CAN.get("bad", set()), (used.name, M._VIDEO_CAN.get("bad")))
sps = next((nal for _l, _c, key, data in by_layer[2] if key
            for nal in M.annexb_nals(data) if nal[0] & 0x1F == 7), b"")
check("x264's high profile (18.2: baseline) and its best setting first",
      sps[1:2] == bytes([100]) and used.preset == 0 and
      M.X264_PRESETS[0] == "veryfast", sps[1:2])
held, now = coder._coders[2], time.monotonic()
held[0].cost = 0.9 / held[0].fps
coder._pace(2, held, now)
waited = coder._coders[2] is held
coder._pace(2, held, now + 1.1)
faster = coder._coders[2][0]
check("a computer too slow for it (a picture in more than half its frame's "
      "time, for a second) steps to the next faster setting, remembered "
      "for that size",
      waited and faster is not used and faster.preset == 1 and
      M._VIDEO_CAN["x264"][((1280, 720), 30)] == 1)
quick = coder._coders[2]
quick[0].cost = 0.1 / quick[0].fps
coder._pace(2, quick, now + 2.0)
coder._pace(2, quick, now + 2.0 + M.X264_PATIENCE + 0.1)
check("...and back to the better one after a while with time to spare",
      coder._coders[2][0].preset == 0)
coder.close()
frame = M.VideoCapture(M.video_sources("screen")[0])
frame.start()
still = frame.next_frame(0, 3.0)[1]
frame.stop()
spent = {}
for screen in (True, False):
    enc = M.FfmpegEncoder("h264", 1920, 1080, 30, 8000, screen=screen)
    sizes = [len(d) for n in range(30)
             for d, _k in enc.encode(still, n == 0)]
    spent[screen] = sum(sizes[1:])
check("a shared screen is coded for its quality under its ceiling: a still "
      "screen costs a few kilobytes a second, where a fixed bitrate spends "
      "its whole budget on it",
      spent[True] * 5 < spent[False] and spent[True] < 60000, spent)

print("\nA shared screen over mobile data (19.0.1, MOBILE191)")
check("a shared screen's coder holds a quarter of a second of its ceiling "
      "(19.0: a whole second), a camera's a second",
      M.FfmpegEncoder("h264", 1920, 1080, 30, 8000,
                      screen=True).ctx.rc_buffer_size == 2_000_000 and
      M.FfmpegEncoder("h264", 1280, 720, 30, 2500).ctx.rc_buffer_size ==
      2_500_000)
grab = M.VideoCapture(M.video_sources("screen")[0])
grab.start()
first = grab.next_frame(0, 3.0)
time.sleep(0.2)
second = grab.next_frame(first[0], 3.0)[1]
grab.stop()
first = first[1]
check("two captures of a moving screen differ by their signature, the same "
      "one does not", M.frame_signature(first) != M.frame_signature(second)
      and M.frame_signature(first) == M.frame_signature(first))


class StillCapture:
    """A screen that changes only when the test says (its `frame`)."""

    def __init__(self, frame):
        self.frame, self.serial, self.gave, self.error = frame, 0, None, ""

    def next_frame(self, _serial, _timeout):
        time.sleep(1 / 30)
        self.serial += 1
        return self.serial, self.frame

    def stop(self):
        pass


def still_coder(kind, frame):
    coder = M.FfmpegVideoCoder(M.video_sources(kind)[0])
    coder.capture.stop()
    coder.capture = StillCapture(frame)
    coder.set_plan([(0, 1080, 30, 8000)], "h264")
    return coder


coder = still_coder("screen", first)


def polled(seconds):
    """What the coder sends while the screen is polled for `seconds`."""
    out, until = [], time.monotonic() + seconds
    while time.monotonic() < until:
        out += coder.poll(0.5)
    return out


opened = coder.poll(0.5)
settling = polled(M.SCREEN_SETTLE + 0.3)
unchanged = polled(0.7)
coder.capture.frame = second
changed = coder.poll(0.5)
check("a screen that stops changing is coded on for a second (sharpened), "
      "then sends nothing; a change is sent at once, without a key frame",
      len(opened) == 1 and opened[0][2] and 15 <= len(settling) <= 45 and
      not any(p[2] for p in settling) and not unchanged and
      len(changed) == 1 and not changed[0][2],
      (len(opened), len(settling), len(unchanged), [p[2] for p in changed]))
polled(M.SCREEN_SETTLE + 0.3)
coder.ask_key(0)
owed = coder.poll(0.5)
check("a key frame asked for (a picture lost there) is sent though nothing "
      "changed", len(owed) == 1 and owed[0][2])
polled(M.SCREEN_SETTLE + 0.3)
coder._coders[0][2] = time.monotonic() - M.VIDEO_SCREEN_KEY_EVERY - 1
still_due = polled(0.3)
coder.capture.frame = first
due = coder.poll(0.5)
check("its own key frame, once due, waits for a change to send",
      not still_due and len(due) == 1 and due[0][2])
coder.close()
camera = still_coder("camera", first)
coded = [p for _ in range(10) for p in camera.poll(0.5)]
camera.close()
check("a camera is coded every frame, still or not, as before",
      len(coded) >= 9, len(coded))


class Quiet:
    """A decoder that takes every picture and shows none."""
    codec, hands_out_late = "h264", False

    def decode(self, data, box, fill):
        return None


asked_keys = []
recv = M.VideoReceiver(on_key=lambda *a: asked_keys.append(a),
                       decoder=lambda codec: Quiet())
now = time.monotonic()
recv._piece("ann", "s", whole_picture(0, key=True), now, 0.0)
recv._piece("ann", "s", whole_picture(1), now + 0.1, 0.0)
stream = recv._streams[("ann", "s", 0)]
recv._check(now + 120)
recv._piece("ann", "s", whole_picture(2), now + 120, 0.0)
check("still for two minutes, the stream goes on with the next picture: no "
      "key frame asked for (19.0 started again after two seconds, and asked)",
      recv._streams[("ann", "s", 0)] is stream and not stream.need_key and
      not asked_keys and stream.whole == 3, (stream.whole, asked_keys))
recv._piece("ann", "s", whole_picture(9), now + 240, 0.0)
check("…but after a gap where pictures are missing, a key frame first",
      recv._streams[("ann", "s", 0)] is not stream)
recv._check(now + 240 + M.VideoReceiver.GONE + 1)
check("a stream silent for ten minutes is let go",
      ("ann", "s", 0) not in recv._streams)

print("\n21.1: a key frame a viewer can start from (the owner's phone saw no "
      "picture of a PC)")


def ffmpeg_option(coder, option):
    """Whether FFmpeg's `coder` has `option` (None: its libraries not found
    beside PyAV's): an option FFmpeg does not know is ignored unsaid."""
    import ctypes
    import glob
    import av
    base = os.path.dirname(av.__file__)
    found = glob.glob(os.path.join(base, "..", "av.libs", "*")) + \
        glob.glob(os.path.join(base, "*.so*")) + \
        glob.glob(os.path.join(base, "*.dll"))
    util = [p for p in found if "avutil" in os.path.basename(p)]
    codec = [p for p in found if "avcodec" in os.path.basename(p)]
    if not util or not codec:
        return None
    util = ctypes.CDLL(util[0], mode=getattr(ctypes, "RTLD_GLOBAL", 0))
    lib = ctypes.CDLL(codec[0])
    lib.avcodec_find_encoder_by_name.restype = ctypes.c_void_p
    lib.avcodec_find_encoder_by_name.argtypes = [ctypes.c_char_p]
    lib.avcodec_alloc_context3.restype = ctypes.c_void_p
    lib.avcodec_alloc_context3.argtypes = [ctypes.c_void_p]
    util.av_opt_find.restype = ctypes.c_void_p
    util.av_opt_find.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                 ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
    found_coder = lib.avcodec_find_encoder_by_name(coder.encode())
    if not found_coder:
        return None
    ctx = lib.avcodec_alloc_context3(found_coder)
    return bool(util.av_opt_find(ctx, option.encode(), None, 0, 1))


known = {}
for family, coder in (("nvenc", "h264_nvenc"), ("qsv", "h264_qsv"),
                      ("amf", "h264_amf")):
    for option in M._FFMPEG_OPTIONS[family]:
        known[(coder, option)] = ffmpeg_option(coder, option)
asked_idr = {c: M._FFMPEG_OPTIONS[f].get(o) for f, c, o in (
    ("nvenc", "h264_nvenc", "forced-idr"), ("qsv", "h264_qsv", "forced_idr"),
    ("amf", "h264_amf", "forced_idr"))}
if None in known.values():
    print("  (FFmpeg's libraries not found beside PyAV here: its options not "
          "looked up)")
check("a graphics chip's coder (NVIDIA, Intel, AMD) is told to make an IDR "
      "when a key frame is asked for, each option one FFmpeg knows by that "
      "name (FFmpeg ignores, unsaid, one it does not)",
      set(asked_idr.values()) == {"1"} and
      all(v is not False for v in known.values()), (asked_idr, known))


class Packet:
    def __init__(self, key):
        self.is_keyframe = key


class ChipContext:
    """A chip's coder as FFmpeg's context: `keys` says, for each picture
    handed in, the packets that come out (and at the end, the rest)."""

    def __init__(self, *keys):
        self.keys = list(keys)

    def encode(self, frame):
        return [Packet(k) for k in self.keys.pop(0)]


import av                                                  # noqa: E402

M.FfmpegEncoder._prove(av, ChipContext([True], [True], []), 64, 48)
check("a chip's coder is used once it makes a key frame when asked (its "
      "second picture, asked, a key frame like its first)", True)
try:
    M.FfmpegEncoder._prove(av, ChipContext([True], [False], []), 64, 48)
    refused = False
except RuntimeError:
    refused = True
late_ok = True
try:
    M.FfmpegEncoder._prove(av, ChipContext([], [True], [True]), 64, 48)
except RuntimeError:
    late_ok = False
check("...one whose picture asked to be a key frame is not one is not used "
      "(x264 is), and one a picture behind is used", refused and late_ok)


class NoKeys:
    """A chip's coder that makes no key frame when asked (21.0's nvenc)."""
    name, codec, fps, preset = "h264_nvenc", "h264", 30, 0

    def __init__(self, size):
        self.size, self.kbps, self.cost, self.closed = size, 8000, 0.0, False

    def set_kbps(self, kbps):
        self.kbps = kbps

    def encode(self, frame, key):
        return [(b"\x00\x00\x00\x01\x41" + bytes(16), False)]

    def close(self):
        self.closed = True


coder = still_coder("screen", first)
began = coder.poll(0.5)
fake = NoKeys(coder._coders[0][0].size)
coder._coders[0][0] = fake
given = []
for _ in range(M.FfmpegVideoCoder.KEY_OWED_MOST):  # the viewer asks again
    coder.ask_key(0)
    given += coder.poll(0.5)
bad = M._VIDEO_CAN.get("bad", set())
coder.ask_key(0)
after = coder.poll(0.5)
check(f"in a session, a coder asked for a key frame that hands out "
      f"{M.FfmpegVideoCoder.KEY_OWED_MOST} pictures, none a key frame, is "
      f"put aside (and a chip's not used again this run); the next picture "
      f"is a key frame from another",
      began and began[0][2] and not any(p[2] for p in given) and
      fake.closed and "h264_nvenc" in bad and after and after[0][2] and
      coder._coders[0][0].name == "libx264",
      ([p[2] for p in given], fake.closed, sorted(bad), after[:1]))
bad.discard("h264_nvenc")
x264 = [p for _ in range(4) for p in (coder.ask_key(0) or coder.poll(0.5))]
check("...x264, asked, gives a key frame each time and is kept",
      len(x264) == 4 and all(p[2] for p in x264) and
      coder._coders[0][0].name == "libx264")
coder.close()

class Shows(Quiet):
    """A decoder that shows each picture it takes."""

    def decode(self, data, box, fill):
        return (b"\0" * 12, 2, 2, "rgb")


asks = []
waiting = M.VideoReceiver(on_key=lambda *a: asks.append(a),
                          decoder=lambda codec: Shows())
waiting.show("bob", "s", (800, 600), False)
since = waiting._shown[("bob", "s")]
for later in (1.0, 1.6, 2.0, 3.2, 4.7, 7.0, 10.7):
    waiting._check(since + later)
asked_then = len(asks)
waiting._piece("bob", "s", whole_picture(0, key=True), since + 10.8, 0.0)
for later in (23.0, 35.0):
    waiting._check(since + later)
check("a picture shown and none come yet is asked for after 1.5 s, then "
      "twice as long between asks (3, 6, … 12 s: a still screen whose "
      "first picture was lost sent nothing more); no more once one is "
      "decoded", asked_then == 3 and len(asks) == 3 and
      asks[0] == ("bob", "s", 0), asks)
blind = M.VideoReceiver(on_key=lambda *a: asks.append(a),
                        decoder=lambda codec: Quiet())
blind.show("ann", "s", (800, 600), False)
since = blind._shown[("ann", "s")]
blind._piece("ann", "s", whole_picture(0, key=True), since + 0.1, 0.0)
before = len(asks)
blind._check(since + 1.6)
check("...a picture taken in that showed nothing does not count: still "
      "asked for (a decoder waiting for a key frame of its own)",
      len(asks) == before + 1, asks[before:])
blind.show("ann", "s", None)
check("...and hidden, then shown again, it is watched again",
      ("ann", "s") not in blind._decoded and ("ann", "s") not in
      blind._shown)

big = M.VideoAssembler()
now = time.monotonic()
taken = big.put(0, 0, M.VIDEO_MAX_PIECES + 1, 1, M.VIDEO_KEY, b"x", now)
check("a picture of more pieces than a stream carries is lost, and a key "
      "frame wanted (it was dropped unsaid: nothing was asked for)",
      taken is None and big.lost == 1 and big.wants_key and
      big.put(1, 0, 1, 1, M.VIDEO_KEY | M.VIDEO_START, b"k", now) ==
      (1, True, b"k"))
check("a shared screen is at most 3840 across (a phone's decoder takes "
      "4K): three screens side by side are scaled down whole",
      M.video_size(5760, 1080, 1080, M.SCREEN_LONGEST) == (3840, 720) and
      M.video_size(1920, 1080, 1080, M.SCREEN_LONGEST) == (1920, 1080))
java = (Path(sys.argv[1] if len(sys.argv) > 1 else "main.py").resolve()
        .parent / "android" / "VideoCall.java").read_text("utf-8")
check("a phone's decoder is given room for the largest picture a stream "
      "carries, and says so when a picture is larger still",
      "KEY_MAX_INPUT_SIZE, MOST_IN" in java and
      "MOST_IN = 1 << 20" in java and
      1 << 20 >= M.VIDEO_MAX_PIECES * M.VIDEO_PIECE_BYTES and
      "the decoder's room" in java)
plans = []
eng = M.CallEngine(os.urandom(16).hex(), os.urandom(8), 0, None, "",
                   on_event=lambda kind, value: plans.append(value)
                   if kind == "video_plan" else None)
eng.path, eng.media = "direct", {"flags": 4, "shows": {}}
eng.their_media = {"shows": {"v": (0, 0), "s": (1280, 720)}, "whole": 0,
                   "lost": 0, "decoders": ["h264"]}


def adapt(at, whole=0, lost=0, rtt=None):
    eng.their_media.update(whole=whole, lost=lost)
    eng.rtt_ms = rtt
    eng._adapt_video(at)
    return plans[-1] if plans else None


first_plan = adapt(100.0)
check("a screen drawn at 720 lines (a phone) is still sent at its own "
      "1080 (x264 codes a screen best at its own size), its ceiling 8 "
      "Mbit/s to start", first_plan == ("s", (1080, 30, 8000), "h264"),
      first_plan)
lossy = adapt(102.1, whole=90, lost=10)
check("pictures lost there: a step lower (5 Mbit/s)",
      lossy == ("s", (1080, 30, 5000), "h264"), lossy)
eng.screen_cap = 1500
capped = adapt(104.2, whole=190, lost=10)
check("Data saver there: 1.5 Mbit/s at the most",
      capped == ("s", (1080, 30, 1500), "h264"), capped)
eng.screen_cap = 0
adapt(106.3, whole=290, lost=10, rtt=40)
queued = adapt(108.4, whole=390, lost=10, rtt=300)
check("the way's delay grown from 40 to 300 ms (a queue filling): a step "
      "lower before anything is lost", queued == ("s", (1080, 30, 3000),
                                                     "h264"), queued)
for n in range(3):
    adapt(110.5 + 2.1 * n, whole=490 + 100 * n, lost=10, rtt=45)
check("six clean seconds: a step back up",
      plans[-1] == ("s", (1080, 30, 5000), "h264"), plans[-1])
eng.sock.close()

print("\nSent, received, decoded (threads)")
received = M.VideoReceiver(on_key=lambda *a: asked.append(a))
asked = []
received.start()
received.show("ann", "v", (320, 180))
sent = []
coder = M.FfmpegVideoCoder(source)
coder.set_plan([(1, 360, 30, 500)], "h264")
sender = M.VideoSender("v", coder, lambda layer, flags, payload: (
    sent.append((layer, flags, len(payload))),
    received.put("ann", "v", payload)))
sender.start()
time.sleep(1.5)
sender.stop()
coder.close()
time.sleep(0.2)
latest = received.latest("ann", "v")
check("pieces at most 1,010 bytes, the first of each picture marked",
      sent and max(n for _l, _f, n in sent) <= 1010 and
      sent[0][1] & M.VIDEO_START and sent[0][1] & M.VIDEO_KEY)
check("the other side draws the newest picture", latest is not None and
      latest[2:] == (320, 180, "rgb") and received.pictures >= 35,
      (latest and latest[2:], received.pictures))
check("...and asked for no key frame", asked == [], asked)
received.put("ann", "v", sent and M._VIDEO_PIECE.pack(1, 1, 99999, 0, 2) +
             b"\0" * 10)
received.put("ann", "v", M._VIDEO_PIECE.pack(1, 1, 100001, 0, 1) + b"\0")
time.sleep(0.3)
check("a lost picture asks for a key frame, once", asked[:1] ==
      [("ann", "v", 1)] and len(asked) == 1, asked)
received.stop()

print("\nA group call's pictures, engine to engine")
key_a, key_b = os.urandom(32), os.urandom(32)
room = "a1b2c3d4e5f60718"
events = []
engine_a = M.RoomEngine(room, 0, None, "", key_a, lambda *e: events.append(e),
                        audio_factory=M.CallAudio)
engine_b = M.RoomEngine(room, 1, None, "", key_b, lambda *e: events.append(e),
                        audio_factory=M.CallAudio)
# Each one's "relay" is the other: what a room's relay passes on, sent
# straight (relay 13.2's choice of sizes is the private relay suite's).
engine_a.relay = ("127.0.0.1", engine_b.sock.getsockname()[1])
engine_b.relay = ("127.0.0.1", engine_a.sock.getsockname()[1])
engine_a.relay_ok = engine_b.relay_ok = True
engine_b.set_voice(0, key_a)
seen_b = M.VideoReceiver(on_key=lambda *a: None)
seen_b.start()
seen_b.show(0, "v", (480, 270))
engine_b.video = seen_b
engine_a.start()
engine_b.start()
coder = M.FfmpegVideoCoder(source)
coder.set_plan([(0,) + layers[0], (2,) + layers[2]], "h264")
sender = M.VideoSender("v", coder, engine_a.video_emit("v"))
sender.start()
time.sleep(2.0)
got_b = seen_b.latest(0, "v")
check("member 1 draws member 0's camera, sealed with 0's own key",
      got_b is not None and got_b[2:] == (480, 270, "rgb") and
      seen_b.pictures >= 25, (got_b and got_b[2:], seen_b.pictures))
sizes = {k[2] for k in seen_b._streams}
check("both sizes sent came, each its own stream", sizes == {0, 2}, sizes)
wrong = M.RoomEngine(room, 1, None, "", key_b, lambda *e: None,
                     audio_factory=M.CallAudio)
wrong.set_voice(0, os.urandom(32))
wrong.video = M.VideoReceiver(on_key=lambda *a: None)
head = M._ROOM_VHEAD.pack(M.ROOM_MEDIA, bytes.fromhex(room), 0, 0x76, 0, 1, 0)
wrong._set_voice(0, os.urandom(32))
wrong._picture(head + os.urandom(64))
check("a picture sealed with another key is refused",
      wrong.received == 0 and wrong.video._inbox.empty())
sender.stop()
coder.close()
engine_a.stop()
engine_b.stop()
seen_b.stop()

print("\nA 1-to-1 call's pictures, engine to engine")
call_id, call_tag = os.urandom(16).hex(), os.urandom(8)
secret = os.urandom(32)
ev = {0: [], 1: []}
pair = {}
for role in (0, 1):
    pair[role] = M.CallEngine(call_id, call_tag, role, None, "",
                              on_event=lambda k, v, r=role: ev[r].append((k, v)),
                              audio_factory=M.CallAudio)
    pair[role].set_keys(M.CallKeys(secret, call_id, call_tag,
                                   caller=role == 0))
pair[0].add_candidates(pair[1].candidates())
pair[1].add_candidates(pair[0].candidates())
seen = {r: M.VideoReceiver(on_key=lambda *a, r=r: pair[r].ask_video_key(a[1]))
        for r in (0, 1)}
for role in (0, 1):
    seen[role].start()
    pair[role].video, pair[role].video_who = seen[role], "other"
    pair[role].start()
for _ in range(100):
    if pair[0].path == "direct" and pair[1].path == "direct":
        break
    time.sleep(0.03)
pair[0].set_media(True, True, False)               # 0's camera on
pair[1].set_media(True, False, False, shows={"v": (480, 270)})
seen[1].show("other", "v", (480, 270))
time.sleep(1.5)
media = pair[0].their_media
check("each side says what it sends and where it draws the other's "
      "camera; the other hears it", media is not None and
      media["shows"]["v"] == (480, 270) and pair[1].their_media["camera"]
      and media["decoders"] == M.video_decoders(), media)
plans = [v for k, v in ev[0] if k == "video_plan"]
check("the sender picks a rung the other side draws it at, no more: "
      "360p (its tile is 270 lines)",
      plans and plans[-1][0] == "v" and plans[-1][1] == (360, 24, 500) and
      plans[-1][2] == "h264", plans)
check("...and its sound steps down with it, to Standard's 96 kbit/s",
      pair[0]._ceiling == 96)
coder = M.FfmpegVideoCoder(source)
coder.set_plan([(0,) + plans[-1][1]], plans[-1][2])
sender = M.VideoSender("v", coder, pair[0].video_emit("v"))
sender.start()
time.sleep(1.5)
got = seen[1].latest("other", "v")
check("the other side draws the picture, sealed with the call's keys",
      got is not None and got[2:] == (480, 270, "rgb") and
      seen[1].pictures >= 25, (got and got[2:], seen[1].pictures))
keys_before = len([1 for k, _v in ev[0] if k == "video_key"])
pair[1].ask_video_key("v")
time.sleep(0.3)
check("a key frame asked for reaches the sender",
      len([1 for k, _v in ev[0] if k == "video_key"]) == keys_before + 1)
sender.stop()
coder.close()
for role in (0, 1):
    pair[role].stop()
    seen[role].stop()

print("\n21.1: a still screen whose first picture is lost, engine to engine")
call_id, call_tag = os.urandom(16).hex(), os.urandom(8)
ev = {0: [], 1: []}
pair = {}
for role in (0, 1):
    pair[role] = M.CallEngine(call_id, call_tag, role, None, "",
                              on_event=lambda k, v, r=role: ev[r].append((k, v)),
                              audio_factory=M.CallAudio)
    pair[role].set_keys(M.CallKeys(secret, call_id, call_tag,
                                   caller=role == 0))
pair[0].add_candidates(pair[1].candidates())
pair[1].add_candidates(pair[0].candidates())
viewer = M.VideoReceiver(on_key=lambda *a: pair[1].ask_video_key(a[1]))
lost_pieces = []
taken = viewer.put


first_seen = []


def lossy(who, track, payload):
    """The way loses every piece of the screen's first picture, and of the
    pictures that sharpen it for a second after (SCREEN_SETTLE): after
    them a still screen (a locked one) sends nothing at all."""
    if track == "s":
        first_seen[:] = first_seen or [time.monotonic()]
        if time.monotonic() - first_seen[0] < M.SCREEN_SETTLE + 0.3:
            lost_pieces.append(1)
            return
    taken(who, track, payload)


viewer.put = lossy
viewer.start()
pair[1].video, pair[1].video_who = viewer, "other"
for role in (0, 1):
    pair[role].start()
for _ in range(100):
    if pair[0].path == "direct" and pair[1].path == "direct":
        break
    time.sleep(0.03)
pair[0].set_media(True, False, True)                # 0 shares its screen
viewer.show("other", "s", (960, 540), False)
screen_coder = still_coder("screen", first)
screen_sender = M.VideoSender("s", screen_coder, pair[0].video_emit("s"))
screen_sender.start()
answered, until = 0, time.monotonic() + 8.0
while time.monotonic() < until and viewer.latest("other", "s") is None:
    asked = [1 for k, v in ev[0] if k == "video_key" and v == "s"]
    for _ in asked[answered:]:
        screen_coder.ask_key()                  # as CallVideo.ask_key does
    answered = len(asked)
    time.sleep(0.05)
check("its first pictures lost on the way (the first and its second of "
      "sharpening), a still screen sends nothing more, and the viewer asks "
      "for one after 1.5 s: the picture comes (it waited for good: the "
      "owner's phone, a locked PC)",
      lost_pieces and answered >= 1 and
      viewer.latest("other", "s") is not None,
      (len(lost_pieces), answered, viewer.pictures))
screen_sender.stop()
screen_coder.close()
for role in (0, 1):
    pair[role].stop()
viewer.stop()

print("\nRecording a call (F13)")
keys = M.IdentityManager()
priv, *_ = M.mine_identity()
keys.activate(keys.add_identity(priv, "rec"))
key = M.recording_key(keys)
check("off until turned on in Settings; a recording key of this identity's "
      "own", not M.call_recording_allowed() and len(key) == 32 and
      key == M.recording_key(keys))
cam = M.FfmpegVideoCoder(source)
seen_frames = []


def stage_picture():
    got = cam.preview((640, 360))
    return got[1:4] if got else None


rec = M.CallRecorder(key, M.ROOM_PCM, "Allotment", stage_picture)
rec.start()
import array  # noqa: E402
hum = array.array("h", [6000 if i % 96 < 48 else -6000
                        for i in range(M.ROOM_PCM.frame_bytes // 2)])
for _ in range(400):                         # 2 s of sound, in 5 ms frames
    rec.played(hum.tobytes())
    rec.mic(bytes(M.ROOM_PCM.frame_bytes))
    time.sleep(0.005)
rec.stop()
rec.join(timeout=30)
cam.close()
head = M.recording_header(rec.path) if rec.path else {}
check("sealed into the recordings, its title and length in the clear",
      rec.path is not None and rec.path.suffix == M.RECORDING_SUFFIX and
      head.get("title") == "Allotment" and 1 <= head.get("seconds", 0) <= 4,
      (rec.error, head))
raw = rec.path.read_bytes() if rec.path else b""
check("...and nothing of it readable without the key",
      b"ftyp" not in raw and b"mdat" not in raw)
plain = M.open_recording(rec.path, key, M.DATA_DIR / "cache" / "out.mp4")
with M._pyav().open(str(plain)) as played:
    kinds = sorted(st.type for st in played.streams)
    length = float(played.duration or 0) / 1e6
check("unsealed with it: an MP4 of the picture and the sound, as long as "
      "the call", kinds == ["audio", "video"] and 1.5 <= length <= 4.0,
      (kinds, length))
other = M.IdentityManager()
priv2, *_ = M.mine_identity()
other.activate(other.add_identity(priv2, "other"))
try:
    M.open_recording(rec.path, M.recording_key(other),
                     M.DATA_DIR / "cache" / "x.mp4")
    refused = False
except M.BackupError:
    refused = True
check("another identity's key opens nothing", refused and
      not (M.DATA_DIR / "cache" / "x.mp4").exists())
session = M.new_group_call("g:" + "ab" * 16, keys.address, video=False)
them = M.CallMember(other.address)
them.recording = True
session.update_member(them.state())
check("one who records says so in their state; the others are told once",
      them.state().get("rec") is True and session.recording() and
      session.recording_news == [other.address])

print("\n21.1 (SCREEN60, CURSOR30): the screen's rate, and its pointer left out")
M.save_pref("remote_smooth", None)
was = M.screen_fps()
M.save_pref("remote_smooth", True)
check("a shared screen is read 30 times a second; 60 with Settings › "
      "Remote access › Smoother screen", was == 30 and M.screen_fps() == 60)
os.environ.pop("FEXT_VIDEO_TEST")
found = M.video_sources("screen")          # this X display's (xvfb)
os.environ["FEXT_VIDEO_TEST"] = "1"
check("...FFmpeg's grab asked for 60 first, then 30 and 15 where it will "
      "not open", found and found[0].options == (
          {"framerate": "60"}, {"framerate": "30"}, {"framerate": "15"}),
      found and found[0].options)
check("...the service's helper reads it as often (not started here)",
      M.DeskCapture(found[0]).fps == 60)
plans = []
planner = M.CallEngine(os.urandom(16).hex(), os.urandom(8), 0, None, "",
                       on_event=lambda k, v: plans.append((k, v)),
                       audio_factory=M.CallAudio)
planner._plan_screen(0.0, "h264", time.monotonic())
check("...and a 1-to-1 call's plan for it says 60 a second",
      plans and plans[-1][0] == "video_plan" and plans[-1][1][1][1] == 60,
      plans)
M.save_pref("remote_smooth", False)
grab = M.FfmpegVideoCoder(found[0])
grab.set_pointer(False)                  # any thread asks; poll applies it
opened = grab.capture
grab._apply_pointer()
check("CURSOR30: the screen read without its pointer (the side controlling "
      "draws its own): FFmpeg's grab opened again without it",
      grab.pointer is False and grab.capture is not opened and
      all(o.get("draw_mouse") == "0" for o in grab.source.options),
      grab.source.options)
grab.set_pointer(True)
grab._apply_pointer()
check("...and with it again once that ends",
      all(o.get("draw_mouse") == "1" for o in grab.source.options))
grab.close()
desk = M.DeskCapture(found[0])
grab = types.SimpleNamespace(capture=desk, source=found[0], pointer=True,
                             _pointer_wanted=False)
M.FfmpegVideoCoder._apply_pointer(grab)
check("...the service's helper is told, without opening anything again",
      desk.pointer is False and grab.capture is desk)

finish()
