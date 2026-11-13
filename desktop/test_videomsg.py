#!/usr/bin/env python3
"""Video messages (18.1, VIDEONOTES), checked on a computer.

    xvfb-run -a python3 desktop/test_videomsg.py main.py

A video message is one MP4 that main.py writes and reads in plain Python,
the same on a phone and a computer. Checked here, in this order:

  1. the file's parts without any video library, as a phone's Python
     (3.11) runs them: NAL units, a picture's size from its parameter set
     (against sets written here bit by bit, and x264's own), ADTS sound,
     the file written and read back, and files that are not what they say
     (cut short, tables changed): refused, never a crash;
  2. the recorder as a phone runs it, against stand-ins for the camera's
     coder and Android's recorder: where a recording begins and ends by
     itself, the sound trimmed to the pictures, what is let go;
  3. the app with a video message in a chat: its bubble, what it says
     while not here, taking a photo once Android gives the camera;
  4. with PyAV (the EXE has it; CI's Python 3.13): a real recording from
     the test picture and a tone, opened by FFmpeg; sound and pictures in
     step; recorded, sent and played in the app.

What this cannot check is a phone itself: the Devices workflow's emulators
do (main.py's self-test records a video message there and has Android's
own reader and player open it).
"""
import importlib.util
import os
import random
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
if sys.platform.startswith("linux"):     # xvfb-run's X display
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-videomsg-")
os.environ["USERPROFILE"] = os.environ["HOME"]   # Path.home() on Windows
os.environ["FEXT_LANG"] = "en"
os.environ["FEXT_SERVER_HOST"] = "127.0.0.1:1"
os.environ["FEXT_VIDEO_TEST"] = "1"     # the camera: FFmpeg's test picture
os.environ.pop("FEXT_THEME", None)

_spec = importlib.util.spec_from_file_location(
    "fext_videomsg", sys.argv[1] if len(sys.argv) > 1 else "main.py")
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_videomsg"] = M
_spec.loader.exec_module(M)

from kivy.clock import Clock                                  # noqa: E402
from kivy.core.window import Window                           # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {str(detail)[:400]}" if detail and not cond else ""),
          flush=True)


def refused(work, *args):
    """work(*args) said no the way it should: a ValueError."""
    try:
        work(*args)
    except ValueError:
        return True
    return False


START = b"\x00\x00\x00\x01"
#: x264's own parameter sets (size, sequence set, picture set).
REAL = [((480, 640), "6742c01eda07814684000003000400000300c23c58ba80",
         "68ce3c80"),
        ((852, 480), "6742c01eda0360f79f84000003000400000300c23c58ba80",
         "68ce3c80"),
        ((854, 480), "6764001eacb201b07bcdc2000003000200000fa01e2c5c90",
         "68ebccb22c"),
        ((1280, 720), "6764001facb402802dd08000000300800003e8078c1950",
         "68ef3cb0")]
SPS, PPS = bytes.fromhex(REAL[0][1]), bytes.fromhex(REAL[0][2])


class Bits:
    """A parameter set written bit by bit: main.py's reader's opposite,
    from the standard's own tables and not from main.py."""

    def __init__(self):
        self.bits = []

    def u(self, count, value):
        self.bits += [(value >> (count - 1 - i)) & 1 for i in range(count)]

    def ue(self, value):
        value += 1
        self.u(value.bit_length() - 1, 0)
        self.u(value.bit_length(), value)

    def se(self, value):
        self.ue(2 * value - 1 if value > 0 else -2 * value)

    def done(self):
        """Its bytes: a stop bit, filled to a byte, and a 3 after any two
        zero bytes that a 0 to 3 would follow (emulation prevention)."""
        bits = self.bits + [1]
        bits += [0] * (-len(bits) % 8)
        out, zeros = bytearray(), 0
        for i in range(0, len(bits), 8):
            byte = int("".join(map(str, bits[i:i + 8])), 2)
            if zeros >= 2 and byte <= 3:
                out.append(3)
                zeros = 0
            out.append(byte)
            zeros = zeros + 1 if byte == 0 else 0
        return bytes(out)


def make_sps(width, height, profile=66, frames=1, chroma=1, order=0,
             scaling=False, set_id=0, level=31):
    """A sequence parameter set for width × height (`frames` 0: fields)."""
    w = Bits()
    w.u(8, profile)
    w.u(8, 0)
    w.u(8, level)
    w.ue(set_id)
    if profile in (100, 110, 122, 244):
        w.ue(chroma)
        if chroma == 3:
            w.u(1, 0)
        w.ue(0)
        w.ue(0)
        w.u(1, 0)
        w.u(1, 1 if scaling else 0)
        for i in range((8 if chroma != 3 else 12) if scaling else 0):
            w.u(1, 1 if i in (0, 6) else 0)       # one small list, one large
            if i == 0:
                for _ in range(16):
                    w.se(1)
            if i == 6:
                w.se(-8)             # the next scale 0: the list ends here
    w.ue(4)
    w.ue(order)
    if order == 0:
        w.ue(4)
    elif order == 1:
        w.u(1, 0)
        w.se(-3)
        w.se(5)
        w.ue(2)
        w.se(1)
        w.se(-1)
    w.ue(1)
    w.u(1, 0)
    unit = 16 * (2 - frames)
    across, down = -(-width // 16), -(-height // unit)
    w.ue(across - 1)
    w.ue(down - 1)
    w.u(1, frames)
    if not frames:
        w.u(1, 0)
    w.u(1, 1)
    crop_x, crop_y = across * 16 - width, down * unit - height
    step_x = 1 if chroma in (0, 3) else 2
    step_y = (2 if chroma == 1 else 1) * (2 - frames)
    w.u(1, 1 if crop_x or crop_y else 0)
    if crop_x or crop_y:
        w.ue(0)
        w.ue(crop_x // step_x)
        w.ue(0)
        w.ue(crop_y // step_y)
    w.u(1, 0)
    return bytes([0x67]) + w.done()


def adts(frames, rate_index=8, channels=1, checksum=False, kind=1):
    """AAC frames as Android's recorder writes them (ADTS)."""
    out = b""
    for frame in frames:
        size = len(frame) + (9 if checksum else 7)
        out += bytes([0xFF, 0xF0 | (0 if checksum else 1),
                      (kind << 6) | (rate_index << 2) | (channels >> 2),
                      ((channels & 3) << 6) | (size >> 11),
                      (size >> 3) & 0xFF, ((size & 7) << 5) | 0x1F, 0xFC])
        out += (b"\x00\x00" if checksum else b"") + frame
    return out


def made_up(count=48, every=24, size=200, step=42):
    """[(ms, key, data)]: pictures of made-up bytes, each one NAL unit
    after its length; a key picture every `every`."""
    return [(i * step, i % every == 0, struct.pack(">I", size) + bytes(
        [0x65 if i % every == 0 else 0x41]) + bytes([i % 251]) * (size - 1))
        for i in range(count)]


TMP = Path(os.environ["HOME"])

print("\nNAL units, and a picture's size from its parameter set")
check("a coder's picture is cut at its start codes, of three bytes or "
      "four, and the zeros before one are not a unit's",
      M.annexb_nals(START + b"\x67AB" + b"\x00\x00\x01\x68C" + b"\x00" +
                    START + b"\x65DEF\x00\x00") ==
      [b"\x67AB", b"\x68C", b"\x65DEF"] and M.annexb_nals(b"") == [] and
      M.annexb_nals(b"no start code") == [])
sizes = [(640, 480), (854, 480), (480, 852), (1920, 1080), (322, 242),
         (16, 16), (4096, 2160)]
got = [M.sps_size(make_sps(w, h)) for w, h in sizes]
check("the size of a Baseline picture, cropped where it is no multiple "
      "of 16", got == sizes, got)
got = [M.sps_size(make_sps(w, h, profile=100)) for w, h in sizes]
check("...of a High one (a chroma format, bit depths)", got == sizes, got)
got = [M.sps_size(make_sps(854, 480, profile=100, chroma=c))
       for c in (0, 1, 2, 3)]
check("...in every chroma format: each crops by its own step",
      got == [(854, 480)] * 4, got)
got = [M.sps_size(make_sps(854, 480, profile=100, scaling=True, chroma=c))
       for c in (1, 3)]
check("...past scaling lists, one of them ended early",
      got == [(854, 480)] * 2, got)
got = [M.sps_size(make_sps(720, 576, order=o, frames=f))
       for o in (0, 1, 2) for f in (1, 0)]
check("...however its pictures are counted, in frames or fields",
      got == [(720, 576)] * 6, got)
guarded = make_sps(640, 480, set_id=63, level=0)
check("...and with the byte that keeps a start code out of it taken "
      "out first", b"\x00\x00\x03" in guarded and
      M.sps_size(guarded) == (640, 480), guarded.hex())
got = [M.sps_size(bytes.fromhex(sps)) for _size, sps, _pps in REAL]
check("x264's own sets, Baseline and High, say their sizes",
      got == [size for size, _sps, _pps in REAL], got)
check("a set cut short before its size, or of no size, is refused",
      all(refused(M.sps_size, SPS[:n]) for n in range(1, 8)) and
      M.sps_size(SPS[:8]) == (480, 640) and
      refused(M.sps_size, make_sps(8192 + 16, 480)))

print("\nA phone's sound (ADTS)")
frames = [bytes([i]) * (20 + i) for i in range(12)]
check("its frames, rate and channels, after headers of 7 bytes",
      M.adts_frames(adts(frames)) == (16000, 1, frames) and
      M.adts_frames(adts(frames, rate_index=3, channels=2)) ==
      (48000, 2, frames))
check("...and of 9, with a checksum",
      M.adts_frames(adts(frames, checksum=True)) == (16000, 1, frames))
check("a last frame cut short (the recorder stopped in it) is left out",
      M.adts_frames(adts(frames)[:-5]) == (16000, 1, frames[:-1]))
check("what is not AAC-LC in ADTS, or is empty, is refused",
      refused(M.adts_frames, b"RIFF" + bytes(40)) and
      refused(M.adts_frames, adts(frames, kind=0)) and
      refused(M.adts_frames, adts(frames, rate_index=14)) and
      refused(M.adts_frames, b"") and refused(M.adts_frames, adts([])))

print("\nThe file, written and read back")
pictures = made_up()
sound = [bytes([200 + i % 50]) * (40 + i % 7) for i in range(31)]
path = str(TMP / "made.mp4")
length, width, height = M.mp4_write(path, SPS, PPS, pictures, 16000, 1,
                                    sound)
video = M.mp4_read(path)
data = Path(path).read_bytes()
check("its length is the longer of the pictures and the sound; its size "
      "the parameter set's",
      (length, width, height) == (2016, 480, 640) and
      video.length_ms == 2016 and (video.width, video.height) == (480, 640),
      (length, width, height, video.length_ms))
check("moov comes before mdat (it plays as it downloads), and the file "
      "is little larger than what it holds",
      data[4:8] == b"ftyp" and 0 < data.find(b"moov") < data.find(b"mdat")
      and len(data) - sum(len(p[2]) for p in pictures) - sum(map(len, sound))
      < 2500, len(data))
check("every picture comes back at its time, the key pictures marked",
      [(ms, key) for ms, key, _at, _size in video.samples] ==
      [(ms, key) for ms, key, _data in pictures])
check("...as a decoder takes it: after a start code, a key picture "
      "after the parameter sets",
      video.picture(0) == START + SPS + START + PPS + START +
      pictures[0][2][4:] and
      video.picture(1) == START + pictures[1][2][4:] and
      video.picture(24).startswith(START + SPS + START + PPS))
check("the picture on show at a time, and the key picture decoding "
      "starts from",
      [video.index_at(ms) for ms in (-5, 0, 41, 42, 1000, 99999)] ==
      [0, 0, 0, 1, 23, 47] and
      [video.key_before(i) for i in (0, 23, 24, 47)] == [0, 0, 24, 24])
several = [(ms, key, d + struct.pack(">I", 3) + b"\x41zz")
           for ms, key, d in pictures]
M.mp4_write(path, SPS, PPS, several, 16000, 1, sound)
check("a picture of several NAL units keeps them all",
      M.mp4_read(path).picture(1) == START + pictures[1][2][4:] + START +
      b"\x41zz")
M.mp4_write(path, SPS, PPS, pictures, 16000, 1, [])
silent = M.mp4_read(path)
check("without sound it is the pictures alone", b"soun" not in
      Path(path).read_bytes() and len(silent.samples) == 48 and
      silent.length_ms == 2016)
uneven = [(ms + (7 if i % 3 == 1 else 0), key, d)
          for i, (ms, key, d) in enumerate(pictures)]
M.mp4_write(path, SPS, PPS, uneven, 16000, 1, sound)
check("pictures that came unevenly keep their own times",
      [s[0] for s in M.mp4_read(path).samples] == [p[0] for p in uneven])
check("one picture is a file too; pictures out of order are refused",
      M.mp4_write(path, SPS, PPS, pictures[:1], 16000, 1, sound[:1])[0]
      == 64 and len(M.mp4_read(path).samples) == 1 and
      refused(M.mp4_write, path, SPS, PPS, pictures[:2][::-1], 16000, 1,
              []))

print("\nFiles that are not what they say")
M.mp4_write(path, SPS, PPS, pictures, 16000, 1, sound)
good = Path(path).read_bytes()
moov = good.find(b"moov") - 4
mdat = good.find(b"mdat") - 4


def reads(data):
    """True: read, every picture inside the file. False: refused. Any
    other error is a fault in the reader, and raises here."""
    try:
        got = M.mp4_pictures(data)
    except ValueError:
        return False
    assert all(0 <= at and at + size <= len(data)
               for _ms, _key, at, size in got.samples), "outside the file"
    for index in range(len(got.samples)):
        got.picture(index)
    return True


check("the whole file is read", reads(good))
cut = [reads(good[:n]) for n in range(0, len(good), 37)]
check("cut short anywhere, it is refused: no table reaches past its end",
      not any(cut), f"{sum(cut)} of {len(cut)} cuts were read")


def patched(at, new):
    return good[:at] + new + good[at + len(new):]


stsz = good.find(b"stsz") + 4
stco = good.find(b"stco") + 4
stts = good.find(b"stts") + 4
mdhd = good.find(b"mdhd") + 4
check("a picture said to be larger than the file, or to lie past its "
      "end, is refused",
      not reads(patched(stsz + 12, struct.pack(">I", 2 ** 31))) and
      not reads(patched(stco + 8, struct.pack(">I", len(good) - 10))) and
      not reads(patched(stco + 8, struct.pack(">I", 2 ** 32 - 1))))
check("more pictures than a file may hold, or none, are refused",
      not reads(patched(stsz + 8, struct.pack(">I", 2 ** 31))) and
      not reads(patched(stsz + 8, struct.pack(">I", 0))) and
      not reads(patched(stsz + 4, struct.pack(">II", 4000, 30000))))
check("times that stand still or run back, and a clock of nothing, are "
      "refused",
      not reads(patched(stts + 8, struct.pack(">II", 48, 0))) and
      not reads(patched(mdhd + 12, struct.pack(">I", 0))))
check("a box that says it is longer than what holds it is refused",
      not reads(patched(moov, struct.pack(">I", len(good) * 2))) and
      not reads(patched(good.find(b"trak") - 4, struct.pack(">I", 9))) and
      not reads(patched(good.find(b"stbl") - 4,
                        struct.pack(">I", 2 ** 32 - 1))))
check("pictures that are not H.264, or have no parameter sets, and a "
      "file with no pictures at all, are refused",
      not reads(good.replace(b"avc1", b"hev1")) and
      not reads(good.replace(b"avcC", b"abcD")) and
      not reads(good.replace(b"vide", b"soun")) and
      not reads(good.replace(b"moov", b"free")) and
      not reads(b"") and not reads(os.urandom(4000)))
rng = random.Random(181)
outcomes = {True: 0, False: 0}
for _ in range(4000):
    at = rng.randrange(moov, mdat + 8)
    changed = bytearray(good)
    for k in range(rng.choice((1, 1, 2, 4))):
        changed[min(at + k, len(good) - 1)] = rng.randrange(256)
    outcomes[reads(bytes(changed))] += 1
check("4,000 files with bytes of their tables changed at random: each "
      "is read with every picture inside it, or refused",
      outcomes[True] and outcomes[False], outcomes)

print("\nWhat a video message is called, and what apps make of it")
check("its words, for an app before 18.1 and every preview",
      M.video_label(42000) == "Video message (0:42)" and
      M.video_label(60040) == "Video message (1:00)" and
      M.voice_label(4200) == "Voice message (0:04)")
check("...shown in this phone's language only where the message is one",
      M.shown_body("video", "Video message (0:42)") ==
      M.tr("Video message ({time})", time="0:42") and
      M.shown_body("text", "Video message (0:42)") ==
      "Video message (0:42)" and
      M.shown_body("voice", "Video message (0:42)") ==
      "Video message (0:42)" and
      M.shown_body("video", "Voice message (0:42)") ==
      "Voice message (0:42)")
reference = {"kind": "video", "mime": "video/mp4", "size": 5_000_000,
             "blob": "b" * 32, "sha": "0" * 64, "key": M.b64e(bytes(32)),
             "dur": 42000, "w": 480, "h": 640,
             "thumb": M.b64e(b"\xff\xd8 a small picture")}
check("a video's reference is taken field by field: MP4 alone, its "
      "length, size and still picture kept",
      M.sanitize_media(reference) == reference and
      M.sanitize_media(dict(reference, mime="video/webm")) is None and
      M.sanitize_media(dict(reference, mime="image/jpeg")) is None and
      M.sanitize_media(dict(reference, size=M.MAX_MEDIA_BYTES + 1)) is None
      and "thumb" not in M.sanitize_media(
          dict(reference, thumb="A" * 24004)))
check("a reply to one is kept as a reply to a video; it saves as .mp4; "
      "a quote of one names it",
      M.sanitize_ext({"reply": {"cid": "c" * 32, "kind": "video"}})
      ["reply"].get("kind") == "video" and
      "kind" not in M.sanitize_ext(
          {"reply": {"cid": "c" * 32, "kind": "hologram"}})["reply"] and
      M.media_suffix("video", "video/mp4") == "mp4" and
      M.quote_text({"kind": "video", "snip": ""}) == "Video message" and
      M.message_text({"kind": "video", "body": "Video message (0:42)",
                      "ext": "{}"}) == "")
check("this app says it plays them (bit 32 of its features), and one "
      "that says so is remembered",
      M.FEATURES & M.FEATURE_VIDEO == 32 and
      M.SyncWorker.caps_of({"ft": M.FEATURES}) & M.LocalStore.CAP_VIDEO
      and not M.SyncWorker.caps_of({"ft": M.FEATURES - 32}) &
      M.LocalStore.CAP_VIDEO)


class Chats:
    """A store's two answers downloads_by_itself asks for."""
    requests = ()

    def is_request(self, peer):
        return peer in self.requests

    def group(self, _gid):
        return None


really_metered = M.on_metered_network
row = {"peer": "FecSomeone", "kind": "video"}
M.on_metered_network = lambda: False
on_wifi = M.downloads_by_itself(Chats(), row)
M.on_metered_network = lambda: True
on_data = M.downloads_by_itself(Chats(), row)
M.on_metered_network = really_metered
asker = Chats()
asker.requests = ("FecSomeone",)
check("it downloads by itself on Wi-Fi; on mobile data, and from "
      "someone not yet accepted, it waits for a tap",
      on_wifi and not on_data and not M.downloads_by_itself(asker, row))

print("\nIts still picture")
noise = os.urandom(640 * 480 * 3)
poster = M.poster_b64((noise, 640, 480, "rgb"))
head = M.image_header(M.b64d(poster)) if poster else None
check("a small JPEG, 320 across at the most and under 16 kB even of a "
      "picture that is all noise: small enough to go in the message",
      head is not None and head[0] == "JPEG" and max(head[1:]) == 320 and
      len(M.b64d(poster)) <= M.VIDEO_POSTER_BYTES and
      len(poster) <= 24000, (head, len(poster)))
head = M.image_header(M.b64d(M.poster_b64(
    (bytes([10, 200, 90, 255]) * (90 * 160), 90, 160, "rgba"))))
check("...from a phone's picture too (RGBA), never made larger; nothing "
      "from no picture or a broken one",
      head == ("JPEG", 90, 160) and M.poster_b64(None) == "" and
      M.poster_b64((b"short", 64, 48, "rgb")) == "", head)

print("\nThe recorder, as a phone runs it")
OTHER_SPS = bytes.fromhex(REAL[1][1])


class Camera:
    """A phone's camera and coder as the recorder uses them: the five
    calls, and flip. Made-up pictures, a hundred a second; `early` of
    them come before the first key picture, each after a delimiter unit,
    as some coders send them."""

    def __init__(self, size=300, early=30):
        self.plan, self.codec, self.error = [], "", ""
        self.sps, self.size, self.early = SPS, size, early
        self.asked = self.flips = self.count = self.serial = self.closes = 0
        self.key_due = self.closed = self.quiet = False
        self.follows = []

    def follow(self, on):
        self.follows.append((on, list(self.plan)))

    def set_plan(self, plan, codec):
        self.plan, self.codec = list(plan), codec

    def ask_key(self, layer=None):
        self.asked, self.key_due = self.asked + 1, True

    def preview(self, box):
        self.serial += 1
        return (self.serial, bytes([self.serial % 256, 80, 160]) * 64, 8, 8,
                "rgb")

    def poll(self, timeout):
        time.sleep(0.01)
        if not self.plan or self.closed or self.quiet:
            return []
        self.count += 1
        body = bytes([self.count % 250 + 1]) * self.size
        if self.key_due and self.count > self.early:
            self.key_due = False
            return [(0, "h264", True, START + self.sps + START + PPS +
                     b"\x00\x00\x01\x65" + body)]
        return [(0, "h264", False, START + b"\x09\xf0" +
                 b"\x00\x00\x01\x41" + body)]

    def flip(self):
        self.flips += 1

    def close(self):
        self.closed, self.closes = True, self.closes + 1


class Microphone:
    """Android's recorder as the recorder uses it (VoiceRecorder with
    adts): stopped, a file of numbered frames, one for every 64 ms it
    ran."""
    made: list = []

    def __init__(self, adts=False):
        self.adts, self.cancelled = adts, False
        self.began = time.monotonic()
        M.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = str(M.MEDIA_DIR / f"rec_heard{len(self.made)}.aac")
        Microphone.made.append(self)

    def stop(self):
        count = int((time.monotonic() - self.began) * 16000 / 1024)
        Path(self.path).write_bytes(adts(
            [b"heard" + struct.pack(">H", i) + b"." * 25
             for i in range(count)]))
        return self.path, count * 64, ""

    def cancel(self):
        self.cancelled = True


def sound_frames(data):
    """The numbers of the Microphone's frames a file holds."""
    return [i for i in range(400)
            if b"heard" + struct.pack(">H", i) in data]


def leftovers():
    return sorted(p.name for p in M.MEDIA_DIR.glob("rec_*"))


def phone_recorder(camera, most=M.MAX_MEDIA_BYTES):
    M.make_video_coder = lambda track: camera
    return M.VideoNoteRecorder(most)


def run_until(cond, timeout=6.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.02)
    return cond()


REAL_PARTS = (M.IS_ANDROID, M.make_video_coder, M.VoiceRecorder,
              M.VIDEO_NOTE_KEY_EVERY, M.VIDEO_NOTE_QUIET)
M.IS_ANDROID, M.VoiceRecorder, M.VIDEO_NOTE_KEY_EVERY = True, Microphone, 0.2
try:
    camera = Camera()
    rec = phone_recorder(camera)
    shown = rec.preview((320, 320))
    rec.flip()
    check("made, it shows the camera and records nothing yet; the other "
          "camera can be turned to",
          shown is not None and not rec.recording and not rec.elapsed_ms
          and camera.flips == 1 and not camera.plan and not Microphone.made)
    rec.start()
    rec.flip()
    check("Record starts the microphone (as ADTS) and asks the coder for "
          "480 lines at 24 a second and a key picture; no turning to the "
          "other camera now",
          Microphone.made[-1].adts and camera.plan ==
          [(0, 480, 24, 700)] and camera.codec == "h264" and
          camera.asked == 1 and camera.flips == 1 and rec.recording)
    check("...and (18.1) tells the camera, before its coder is made, to "
          "keep the way up it has: a phone turned while it records does "
          "not change the shape of the file's pictures",
          camera.follows == [(False, [])], camera.follows)
    run_until(lambda: rec.elapsed_ms >= 700)
    rec.preview((320, 320))
    rec.stop()
    path, length, width, height, poster = rec.write()
    data, video = Path(path).read_bytes(), M.mp4_read(path)
    heard = sound_frames(data)
    check("the file begins at the first key picture: what the coder made "
          "before it is left out, and so are its delimiter units",
          video.samples[0][:2] == (0, True) and
          video.picture(0).endswith(b"\x65" + bytes([32]) * 300) and
          video.picture(1) == START + b"\x41" + bytes([33]) * 300 and
          (width, height) == (480, 640),
          (video.samples[0], video.picture(1)[:8]))
    check("a key picture is asked for every so often, so playing can "
          "start from more places",
          sum(1 for s in video.samples if s[1]) >= 3 and camera.asked >= 3,
          camera.asked)
    lead = (camera.early + 1) * 0.01 * 16000 / 1024
    check("the sound heard before that picture is cut off, and the sound "
          "ends with the pictures",
          heard and abs(heard[0] - lead) <= 2.5 and
          heard == list(range(heard[0], heard[-1] + 1)) and
          abs(len(heard) * 64 - length) <= 64, (heard[:3], lead, length))
    check("its length is its pictures', and its still picture is the "
          "first one shown after recording began",
          abs(length - video.samples[-1][0]) <= 100 and
          700 <= length <= 1500 and
          M.image_header(M.b64d(poster)) == ("JPEG", 8, 8), length)
    check("stopped and written, the camera is let go and only the video "
          "is left", camera.closed and leftovers() == [Path(path).name],
          leftovers())
    M.discard_media_file(path)

    camera = Camera(early=0)
    rec = phone_recorder(camera)
    rec.max_seconds = 1
    rec.start()
    over = run_until(lambda: rec.ended)
    count = camera.count
    time.sleep(0.2)
    check("when its time is up it ends by itself, and takes no more",
          over and not rec.error and 1000 <= rec.elapsed_ms <= 1100 and
          rec.elapsed_ms == rec.elapsed_ms, (over, rec.elapsed_ms))
    rec.stop()
    path = rec.write()[0]
    check("...leaving a file of that length, never longer",
          950 <= M.mp4_read(path).length_ms <= 1100 and
          M.mp4_read(path).samples[-1][0] < 1000 and
          camera.count >= count, M.mp4_read(path).length_ms)
    M.discard_media_file(path)

    camera = Camera(size=3000, early=0)
    rec = phone_recorder(camera, 150_000)
    rec.start()
    over = run_until(lambda: rec.ended)
    rec.stop()
    rec.stop()
    path = rec.write()[0]
    check("before the file would be larger than it may be, it ends by "
          "itself",
          over and not rec.error and 30_000 < os.path.getsize(path) <
          150_000, os.path.getsize(path))
    check("stopped twice, it lets go of the camera once and the file is "
          "the same", camera.closes == 1 and leftovers() ==
          [Path(path).name], (camera.closes, leftovers()))
    M.discard_media_file(path)
    lengths = [phone_recorder(Camera(), most).max_seconds for most in (
        M.MAX_MEDIA_BYTES, 4 * 2 ** 20, 2 ** 20, 5000, -1)]
    check("a relay that takes smaller files gets a shorter message: what "
          "fits at the rate it is recorded at; the app's own limit holds "
          "the whole minute",
          lengths == [60, 44, 11, 0, 0], lengths)

    M.VIDEO_NOTE_QUIET = 0.4
    camera = Camera(early=0)
    rec = phone_recorder(camera)
    rec.start()
    run_until(lambda: rec.elapsed_ms >= 150)
    camera.quiet = True
    stood = rec.elapsed_ms
    over = run_until(lambda: rec.ended, 2.0)
    check("a camera that hands over nothing more has stopped, though it "
          "says nothing (Android, when another app takes it): the "
          "recording ends there, its time standing still meanwhile",
          over and rec.error == "the camera gives no pictures" and
          rec.elapsed_ms - stood <= 30, (over, rec.error, stood,
                                         rec.elapsed_ms))
    rec.stop()
    path = rec.write()[0]
    check("...and what came before is still a file",
          len(M.mp4_read(path).samples) > 5)
    M.discard_media_file(path)
    camera = Camera(early=10 ** 6)
    rec = phone_recorder(camera)
    rec.start()
    over = run_until(lambda: rec.ended, 2.0)
    rec.stop()
    check("...as has one that never gives a first picture: nothing to "
          "send, nothing left behind",
          over and rec.error and not rec.elapsed_ms and
          refused(rec.write) and leftovers() == [], (over, leftovers()))
    verdicts = []
    for error, early in (("this phone has no camera", 0), ("", 10 ** 6)):
        camera = Camera(early=early)
        camera.error = error
        verdicts.append(M._selftest_video_message(
            lambda *words: words[1], recorder=phone_recorder(camera),
            seconds=0.3))
    check("the emulators' self-test skips a device with no camera, and "
          "fails one whose camera gives nothing",
          verdicts == ["SKIP", "FAIL"] and leftovers() == [],
          (verdicts, leftovers()))
    M.VIDEO_NOTE_QUIET = REAL_PARTS[4]

    camera = Camera(early=0)
    rec = phone_recorder(camera)
    rec.start()
    run_until(lambda: rec.elapsed_ms >= 150)
    camera.sps = OTHER_SPS
    over = run_until(lambda: rec.ended)
    rec.stop()
    path, _length, width, height, _poster = rec.write()
    check("a key picture of another size ends it there: one file holds "
          "one size",
          over and (width, height) == (480, 640) and
          OTHER_SPS not in Path(path).read_bytes())
    M.discard_media_file(path)

    camera = Camera(early=0)
    rec = phone_recorder(camera)
    rec.start()
    run_until(lambda: rec.elapsed_ms >= 150)
    camera.error = "the camera was taken away"
    over = run_until(lambda: rec.ended)
    rec.stop()
    path = rec.write()[0]
    check("a camera that fails ends it, saying why; what came before is "
          "still a file",
          over and rec.error == "the camera was taken away" and
          len(M.mp4_read(path).samples) > 5)
    M.discard_media_file(path)

    camera = Camera(early=10 ** 6)
    rec = phone_recorder(camera)
    rec.start()
    time.sleep(0.2)
    rec.stop()
    check("with no key picture there is nothing to write, and nothing is "
          "left behind", refused(rec.write) and leftovers() == [],
          leftovers())

    camera = Camera(early=0)
    rec = phone_recorder(camera)
    rec.start()
    run_until(lambda: rec.elapsed_ms >= 100)
    rec.cancel()
    check("cancelled, the camera and the microphone are let go and "
          "nothing is kept", camera.closed and leftovers() == [] and
          not rec.sound, leftovers())

    def no_microphone(adts=False):
        raise OSError("no microphone")
    M.VoiceRecorder = no_microphone
    camera = Camera()
    rec = phone_recorder(camera)
    try:
        rec.start()
        started = True
    except OSError:
        started = False
    rec.cancel()
    check("where the microphone will not start, Record says so (it "
          "raises) and the camera is let go",
          not started and camera.closed and not rec.recording)

    # A thread that used Java leaves the JVM before it ends: pyjnius's
    # detach, counted here by a stand-in for it.
    import types
    left = []
    sys.modules["jnius"] = types.ModuleType("jnius")
    sys.modules["jnius"].detach = lambda: left.append(
        threading.current_thread().name)
    M.VoiceRecorder = Microphone
    rec = phone_recorder(Camera(early=0))
    rec.start()
    run_until(lambda: rec.elapsed_ms >= 100)
    rec.cancel()
    sender = M.VideoSender("v", Camera(early=0), lambda *_a: None)
    receiver = M.VideoReceiver(lambda *_a: None, decoder=lambda codec: None)
    for thread in (sender, receiver):
        thread.start()
    time.sleep(0.2)
    for thread in (sender, receiver):
        thread.stop()
        thread.join(2)
    check("the recorder's thread leaves the JVM when it ends; so do a "
          "call's video sender and receiver, which did not until 18.1",
          sorted(left) == ["fext-video-in", "fext-video-message",
                           "fext-video-v"] and leftovers() == [],
          (left, leftovers()))
    del left[:]


    class Decoder:
        """The calls' decoder as the pictures' thread uses it. `late`: as
        a phone's, which hands a picture out a call after it was given;
        `bad`: the pictures it refuses. A picture is told by its last
        byte (made_up)."""

        def __init__(self, late=False, bad=()):
            self.late, self.bad = late, set(bad)
            self.fed, self.waiting, self.closed = [], None, False

        def decode(self, data, box, fill):
            out, self.waiting = self.waiting, None
            if data:
                if data[-1] in self.bad:
                    raise ValueError("a damaged picture")
                self.fed.append(data[-1])
                shown = (bytes(12), 2, 2, "rgb")
                if self.late:
                    self.waiting = shown
                else:
                    out = shown
            return out

        def close(self):
            self.closed = True


    print("\nIts pictures while it plays")
    path = str(TMP / "played.mp4")
    M.mp4_write(path, SPS, PPS, pictures, 16000, 1, sound)
    decoder = Decoder(late=True)
    M.android_video_decoder = lambda codec: decoder
    shown = M.VideoNotePictures(path, lambda: 0)
    shown._step(0)
    check("nothing is decoded until the bubble's size is known",
          decoder.fed == [])
    shown.box = (260, 340)
    shown._step(0)
    first = shown.latest
    shown._step(0)
    check("a phone's decoder hands a picture out a call later: it is "
          "asked again, with nothing, until it does",
          decoder.fed == [0] and first is None and
          shown.latest == (1, bytes(12), 2, 2, "rgb"))
    clock = [0]
    shown = M.VideoNotePictures(path, lambda: clock[0])
    shown.box = (260, 340)
    shown.start()
    run_until(lambda: shown.latest is not None)
    shown.stop()
    shown.join(2)
    check("its thread ends when told to, lets the decoder go and leaves "
          "the JVM", not shown.is_alive() and decoder.closed and
          left == ["fext-video-message"], left)
    del sys.modules["jnius"]
finally:
    (M.IS_ANDROID, M.make_video_coder, M.VoiceRecorder,
     M.VIDEO_NOTE_KEY_EVERY, M.VIDEO_NOTE_QUIET) = REAL_PARTS

REAL_DECODER = M.FfmpegDecoder
decoder = Decoder()
M.FfmpegDecoder = lambda codec: decoder
shown = M.VideoNotePictures(path, lambda: 0)
shown.box = (260, 340)
for ms in range(0, 500, 10):
    shown._step(ms)
check("each picture is decoded when the sound reaches it, in order, one "
      "at a time", decoder.fed == list(range(12)) and shown.latest[0] == 12,
      decoder.fed)
for _ in range(14):
    shown._step(1500)
check("the sound far ahead (a slow decoder): the pictures between are "
      "left out, and decoding goes on from the key picture before where "
      "the sound is",
      decoder.fed[12:] == list(range(24, 36)), decoder.fed[12:])
del decoder.fed[:]
for _ in range(5):
    shown._step(100)
check("the sound moved back: decoding starts again at the key picture "
      "before it", decoder.fed == [0, 1, 2], decoder.fed)
decoder = Decoder(bad=[5])
shown = M.VideoNotePictures(path, lambda: 0)
shown.box = (260, 340)
for ms in list(range(0, 300, 10)) + [1100] * 5:
    shown._step(ms)
check("a picture the decoder refuses: the ones that build on it are "
      "left out, up to the next key picture",
      decoder.fed == [0, 1, 2, 3, 4, 24, 25, 26], decoder.fed)
M.FfmpegDecoder = REAL_DECODER

print("\nTaking a photo, once Android has given the camera")
REAL_PHOTO = (M.android_has_permission, M.android_request_permissions,
              M._android_camera_intent, M.run_on_ui)
held, asked, opened, said = [], [], [], []
M.android_has_permission = lambda name: name in held
M.android_request_permissions = lambda names, answer=None: asked.append(
    (list(names), answer))
M._android_camera_intent = lambda callback: opened.append(callback)
M.run_on_ui = lambda work, *args: work(*args)
try:
    M.android_take_photo("a photo's callback", said.append)
    check("without the camera permission it is asked for first, and no "
          "camera app is opened yet (17.0 to 18.0.2 opened it unasked, "
          "which Android refuses an app that declares the camera)",
          asked and asked[0][0] == ["android.permission.CAMERA"] and
          not opened, asked)
    asked[0][1](asked[0][0], [True])
    check("given, the camera app opens for the photo",
          opened == ["a photo's callback"] and not said)
    M.android_take_photo("again", said.append)
    asked[-1][1](asked[-1][0], [False])
    check("refused, the chat says what taking a photo needs",
          len(opened) == 1 and said ==
          ["Taking a photo needs the camera permission"], said)

    def no_camera_app(callback):
        raise RuntimeError("no camera app")
    M._android_camera_intent = no_camera_app
    M.android_take_photo("third", said.append)
    asked[-1][1](asked[-1][0], [True])
    check("given, but no camera opens: said too",
          said[-1] == "Couldn't open the camera")
    held.append("android.permission.CAMERA")
    count = len(asked)
    try:
        M.android_take_photo("fourth", said.append)
        raised = False
    except RuntimeError:
        raised = True
    check("with the permission held nothing is asked; a camera that does "
          "not open raises, as before", raised and len(asked) == count)
finally:
    (M.android_has_permission, M.android_request_permissions,
     M._android_camera_intent, M.run_on_ui) = REAL_PHOTO

# ---- a person with one contact, and video messages in the chat -------------
keys = M.IdentityManager()
priv, _p, _a, _n = M.mine_identity()
keys.activate(keys.add_identity(priv, "Personal"))
keys.set_username("writer")
store = M.LocalStore(keys.store_path())
pk, _pp, _ad, _nn = M.mine_identity()
PEER = M.address_from_priv(pk)
store.upsert_contact(PEER, "reader",
                     M.pub_to_compressed(M.priv_to_pub(pk)).hex(), listed=True)
M.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
MADE = str(M.MEDIA_DIR / "made.mp4")
M.mp4_write(MADE, SPS, PPS, made_up(), 16000, 1, sound)
POSTER = M.poster_b64((bytes([200, 120, 40]) * (48 * 64), 48, 64, "rgb"))
SHAPE = {"kind": "video", "mime": "video/mp4", "size": 5_000_000,
         "dur": 2016, "w": 480, "h": 640, "thumb": POSTER}
THERE = dict(SHAPE, blob="b" * 32, sha="0" * 64, key=M.b64e(bytes(32)))
IDS = {}
IDS["mine"] = store.add_out(PEER, "Video message (0:02)", M.now_iso(),
                            "a" * 32, kind="video", ext={"media": SHAPE},
                            media_path=MADE, media_state="ready", status=1)
for name, state, cid in (("coming", "pending", "b" * 32),
                         ("broken", "unreadable", "c" * 32)):
    store.add_in(PEER, "Video message (0:02)", M.now_iso(), len(IDS) + 1,
                 cid, unread=False, kind="video", ext={"media": THERE},
                 media_state=state)
    IDS[name] = store.message_by_cid(PEER, cid)["id"]
store.close()

M.FextApp._start_worker = lambda self: setattr(self, "worker", None)
M.SplashOverlay.HOLD = 0.2
app = M.FextApp()
STATUS, OPENED, SENT, PHOTOS = [], [], [], []
HAVE_AV = M._pyav() is not None
TONE = ("import math,struct,sys,time\n"
        "out=sys.stdout.buffer\n"
        "for n in range(700):\n"
        "    out.write(b''.join(struct.pack('<h',int(9000*math.sin("
        "2*math.pi*440*(n*320+i)/16000))) for i in range(320)))\n"
        "    out.flush(); time.sleep(0.02)\n")


def tone_microphone(rate=None):
    return [lambda feed, chunk=None: M._ProcessCapture(
        feed, [sys.executable, "-c", TONE])]


def screen():
    return app.conversation_screen


def content(name):
    bubble = screen()._bubbles.get(IDS[name])
    return bubble.media if bubble is not None else None


def sheets(kind):
    return [v for v in Window.children if isinstance(v, kind)]


def close_sheets():
    for view in list(Window.children):
        if isinstance(view, M.Sheet):
            view.dismiss(animation=False)


STEPS = []


def step(fn, delay=0.4):
    """A step, then `delay` seconds. One that returns False is not ready:
    it runs again every 0.2 s, for 12 s at the most, and then counts as a
    failed check."""
    STEPS.append((fn, delay))


def run(i=0, tries=0):
    if i >= len(STEPS):
        finish()
        return
    fn, delay = STEPS[i]
    try:
        ready = fn() is not False
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check(f"step {i} ({getattr(fn, '__name__', '?')}) ran", False,
              f"{type(exc).__name__}: {exc}")
        ready = True
    if not ready and tries < 60:
        Clock.schedule_once(lambda _dt: run(i, tries + 1), 0.2)
        return
    if not ready:                   # its checks never ran: say so
        check(f"step {i} ({getattr(fn, '__name__', '?')}) was ready in "
              "12 s", False)
    Clock.schedule_once(lambda _dt: run(i + 1), delay)


def open_chat():
    print("\nA video message in the chat")
    app.api._features = {"blobs": True}
    app._set_status = lambda text, color=None, toast=True: \
        STATUS.append(text)
    M.open_with_system = lambda path: OPENED.append(path) or True
    M._capture_routes = tone_microphone
    app.fetch_media = lambda local_id: None     # there is no relay here
    app.open_conversation(PEER)


step(open_chat, 1.0)


def bubbles():
    mine, coming, broken = (content(n) for n in ("mine", "coming", "broken"))
    check("each is a picture with a play mark and its length",
          all(isinstance(c, M.VideoContent) for c in (mine, coming, broken))
          and mine.mark.opacity == 1 and not mine.mark.disabled and
          mine.length.text == "0:02" and mine.chip.text == "")
    check("...its still picture until it plays, in a bubble as upright as "
          "the video (a photo's would be wider, with bars beside it)",
          mine.picture.texture is not None and
          tuple(mine.picture.texture.size) == (48, 64) and
          mine.width < M.dp(260) and
          abs(mine.width / mine.height - 480 / 640) < 0.02,
          (mine.size, mine.picture.texture and mine.picture.texture.size))
    check("one on its way says so, with no play mark over it",
          coming.chip.text == "Downloading…" and coming.mark.opacity == 0
          and coming.mark.disabled, coming.chip.text)
    check("one that is no video says it cannot be shown",
          broken.chip.text == "This video cannot be shown" and
          broken.mark.opacity == 0, broken.chip.text)
    M.on_metered_network = lambda: True
    coming.refresh(coming.msg)
    check("on mobile data it waits for a tap, and says how large it is",
          coming.chip.text == "Tap to download · 4.8 MB", coming.chip.text)
    M.on_metered_network = lambda: False


step(bubbles, 0.2)


def tap_unplayable():
    screen().on_media_tap(content("mine").msg)


step(tap_unplayable, 0.5)


def unplayable_opened():
    check("where it cannot be played here (no video library, or a sound "
          "this computer cannot decode), the computer's own player opens "
          "the file", OPENED == [MADE] and not app.voice.playing and
          not screen().video_playing(content("mine").msg), OPENED)


step(unplayable_opened, 0.1)


def camera_key():
    print("\nThe camera key, and asking to record")
    s = screen()
    s.take_photo = lambda: PHOTOS.append("photo")
    really = M.video_note_why_not
    M.video_note_why_not = lambda in_call: "this device records no video"
    s.camera_menu()
    check("where this device records no video, the camera key takes a "
          "photo at once, as before",
          PHOTOS == ["photo"] and not sheets(M.MenuSheet))
    M.video_note_why_not = lambda in_call: ""
    s.camera_menu()
    M.video_note_why_not = really


step(camera_key, 0.6)


def camera_menu_shown():
    menu = sheets(M.MenuSheet)
    rows = [w.title.text for w in walk(menu[0])
            if isinstance(w, M.ListRow)][::-1] if menu else []
    check("where it does, the key offers a photo or a video message",
          rows == ["Take a photo", "Record a video message"], rows)
    close_sheets()
    app.in_a_call = lambda: True
    screen().record_video()
    check("in a call, recording waits: the call has the camera and the "
          "microphone", STATUS and STATUS[-1].startswith("After the call")
          and not sheets(M.VideoNoteSheet), STATUS[-1:])
    del app.in_a_call


def walk(widget):
    for child in widget.children:
        yield child
        yield from walk(child)


step(camera_menu_shown, 0.4)


def permissions():
    """A phone that has not given the camera or the microphone."""
    real = (M.IS_ANDROID, M.android_has_permission,
            M.android_request_permissions, M.android_calls_too_old,
            M.call_video_why_not, M.run_on_ui, M.VideoNoteRecorder,
            M.android_find_call_classes)
    M.android_find_call_classes = lambda: None
    held, asked, made = [], [], []
    M.IS_ANDROID = True
    M.android_has_permission = lambda name: name in held
    M.android_request_permissions = lambda names, answer=None: \
        asked.append((list(names), answer))
    M.android_calls_too_old = lambda: False
    M.call_video_why_not = lambda track="v": ""
    M.run_on_ui = lambda work, *args: work(*args)
    M.VideoNoteRecorder = lambda most: made.append(most) or 1 / 0
    try:
        screen().record_video()
        check("a phone is asked for the camera and the microphone before "
              "anything opens",
              asked and asked[0][0] == ["android.permission.CAMERA",
                                        "android.permission.RECORD_AUDIO"]
              and not made, asked)
        held.append("android.permission.CAMERA")
        asked[0][1](asked[0][0], [True, False])
        check("one of them refused: said, and not asked for again in a "
              "loop", len(asked) == 1 and not made and STATUS[-1] ==
              "Video messages need the camera and microphone permissions",
              STATUS[-1:])
        held.append("android.permission.RECORD_AUDIO")
        screen().record_video()
        check("both given: the camera opens, for a file as large as this "
              "relay takes (here it fails, and that is said)",
              made == [M.MAX_MEDIA_BYTES - 64] and len(asked) == 1 and
              STATUS[-1] == "Couldn't open the camera", STATUS[-1:])
        M.android_calls_too_old = lambda: True
        screen().record_video()
        check("on Android 5, where a call's camera cannot run, recording "
              "says what it needs", len(made) == 1 and
              "Android 6" in STATUS[-1], STATUS[-1:])
    finally:
        (M.IS_ANDROID, M.android_has_permission,
         M.android_request_permissions, M.android_calls_too_old,
         M.call_video_why_not, M.run_on_ui, M.VideoNoteRecorder,
         M.android_find_call_classes) = real


step(permissions, 0.2)

SHEET = {}


def sheet_opens():
    print("\nThe recording sheet")
    SHEET["camera"] = Camera(early=0)
    M.make_video_coder = lambda track: SHEET["camera"]
    app.send_video = SENT.append
    sheet = SHEET["sheet"] = M.VideoNoteSheet(
        app, M.VideoNoteRecorder(), "a note about older apps")
    sheet.open()


step(sheet_opens, 0.8)


def sheet_before():
    sheet, camera = SHEET["sheet"], SHEET["camera"]
    words = [w.text for w in walk(sheet) if isinstance(w, M.Label)]
    buttons = [w.text.lower() for w in walk(sheet.before)
               if isinstance(w, M.Btn)]
    check("it shows what the camera sees, as a mirror, and says how long "
          "a message may be",
          sheet.view.texture is not None and
          sheet.view.texture.tex_coords[0] > sheet.view.texture.tex_coords[2]
          and "Up to a minute, with sound" in words and
          "a note about older apps" in words, words)
    check("Cancel, the other camera (a phone has two) and Record",
          sorted(buttons) == ["cancel", "other camera", "record"],
          buttons)
    sheet.recorder.flip()
    check("...which turns the camera round", camera.flips == 1)
    check("before Record, a tap beside the sheet or Back closes it, as "
          "any sheet", sheet.auto_dismiss and sheet.dismissable)
    sheet._record()


step(sheet_before, 1.3)


def sheet_recording():
    sheet = SHEET["sheet"]
    buttons = [w.text.lower() for w in walk(sheet.during)
               if isinstance(w, M.Btn)]
    check("Record starts it: the time counts against the minute, in red, "
          "with Delete and Send",
          sheet.recorder.recording and sheet.time.text in (
              "Recording · 0:01 of 1:00", "Recording · 0:02 of 1:00") and
          sheet.during.parent is not None and sheet.before.parent is None
          and sorted(buttons) == ["delete", "send"] and
          tuple(sheet.time.color) == tuple(M.C(M.T.alert)),
          (sheet.time.text, buttons))
    app._on_keyboard(Window, 27)
    check("while it records, Back and a tap beside the sheet do nothing: "
          "only Delete and Send close it, so a slip loses no recording",
          not sheet.auto_dismiss and sheets(M.VideoNoteSheet) == [sheet]
          and sheet.recorder.recording and not sheet.recorder.ended)
    sheet._send()


step(sheet_recording, 0.6)


def sheet_sent():
    sheet, camera = SHEET["sheet"], SHEET["camera"]
    check("Send hands the recording to the app and closes the sheet; the "
          "recorder is the app's to stop",
          SENT == [sheet.recorder] and not sheets(M.VideoNoteSheet) and
          not camera.closed)
    sheet.recorder.cancel()
    del SENT[:]
    camera = SHEET["camera"] = Camera(early=0)
    sheet = SHEET["sheet"] = M.VideoNoteSheet(app, M.VideoNoteRecorder())
    sheet.open()
    sheet._record()
    sheet._send()


step(sheet_sent, 0.6)


def sheet_short():
    camera = SHEET["camera"]
    check("sent at once, it is too short: said, nothing is sent, and the "
          "camera and microphone are let go",
          not SENT and STATUS[-1] == "That was too short to send" and
          camera.closed and leftovers() == [], (STATUS[-1:], leftovers()))
    camera = SHEET["camera"] = Camera(early=0)
    sheet = SHEET["sheet"] = M.VideoNoteSheet(app, M.VideoNoteRecorder())
    sheet.recorder.max_seconds = 1
    sheet.open()
    sheet._record()


step(sheet_short, 1.0)


def sheet_full():
    sheet = SHEET["sheet"]
    if not SENT or sheets(M.VideoNoteSheet):
        return False                # not sent yet, or still closing
    check("when its time is up it is sent by itself, as a voice message "
          "is at its limit",
          SENT == [sheet.recorder] and not sheets(M.VideoNoteSheet))
    sheet.recorder.cancel()
    del SENT[:]
    SHEET["camera"] = Camera(early=0)
    sheet = SHEET["sheet"] = M.VideoNoteSheet(app, M.VideoNoteRecorder())
    sheet.open()
    sheet._record()


step(sheet_full, 0.5)


def sheet_deleted():
    sheet, camera = SHEET["sheet"], SHEET["camera"]
    sheet.dismiss(animation=False)
    check("Delete, or closing the sheet, sends nothing and keeps nothing",
          not SENT and camera.closed and leftovers() == [], leftovers())
    SHEET["camera"] = Camera(early=0)
    sheet = SHEET["sheet"] = M.VideoNoteSheet(app, M.VideoNoteRecorder())
    sheet.open()
    sheet._record()


step(sheet_deleted, 1.0)


def sheet_camera_fails():
    SHEET["camera"].error = "another app took the camera"


step(sheet_camera_fails, 0.6)


def sheet_camera_failed():
    sheet, camera = SHEET["sheet"], SHEET["camera"]
    check("a camera that fails while it records is said, and nothing is "
          "sent by itself: what was recorded waits for Send or Delete",
          sheet.time.text.startswith("Recording stopped at 0:0") and
          not SENT and sheets(M.VideoNoteSheet) == [sheet] and
          sheet.recorder.ended, (sheet.time.text, SENT))
    check("...the camera and the microphone let go meanwhile, and the "
          "time no longer in red",
          camera.closes == 1 and sheet.recorder.sound is None and
          tuple(sheet.time.color) == tuple(M.C(M.T.text_2)))
    sheet._send()
    check("...and Send sends it", SENT == [sheet.recorder])
    sheet.recorder.cancel()
    del SENT[:]
    SHEET["camera"] = Camera(early=0)
    sheet = SHEET["sheet"] = M.VideoNoteSheet(app, M.VideoNoteRecorder())
    sheet.open()
    sheet._record()


step(sheet_camera_failed, 0.8)


def sheet_out_of_sight():
    sheet, camera = SHEET["sheet"], SHEET["camera"]
    app.on_pause()
    app.on_resume()
    count = camera.count
    check("when the app leaves the screen (a phone's home key, its "
          "screen going off) the recording stops where it is: nothing is "
          "recorded unseen, the camera and the microphone are let go",
          sheet.recorder.ended and not sheet.recorder.error and
          camera.closes == 1 and sheet.recorder.sound is None and
          sheets(M.VideoNoteSheet) == [sheet] and not SENT,
          (camera.closes, SENT))
    SHEET["count"] = count


step(sheet_out_of_sight, 0.5)


def sheet_back_in_sight():
    sheet, camera = SHEET["sheet"], SHEET["camera"]
    check("back in the app, the sheet says where it stopped and waits "
          "for Send or Delete; nothing was sent by itself",
          sheet.time.text.startswith("Recording stopped at 0:0") and
          camera.count == SHEET["count"] and not SENT and
          sheets(M.VideoNoteSheet) == [sheet], (sheet.time.text, SENT))
    sheet._send()
    check("...and Send sends what was recorded", SENT == [sheet.recorder])
    sheet.recorder.cancel()
    del SENT[:]
    SHEET["camera"] = Camera(early=0)
    sheet = SHEET["sheet"] = M.VideoNoteSheet(app, M.VideoNoteRecorder())
    sheet.open()


step(sheet_back_in_sight, 0.8)


def sheet_left_before_record():
    app.on_pause()
    app.on_resume()


step(sheet_left_before_record, 0.6)


def sheet_closed_unseen():
    check("a sheet that was not recording yet closes when the app "
          "leaves the screen, and the camera with it",
          not sheets(M.VideoNoteSheet) and SHEET["camera"].closed and
          not SENT and leftovers() == [])
    SHEET["camera"] = Camera(early=0)
    sheet = SHEET["sheet"] = M.VideoNoteSheet(app, M.VideoNoteRecorder())
    sheet.open()
    sheet._record()


step(sheet_closed_unseen, 0.8)


def sheet_call_begins():
    sheet, camera = SHEET["sheet"], SHEET["camera"]
    hushed = []
    really, app.voice.stop = app.voice.stop, lambda: hushed.append(1)
    app._call_begins()
    app.voice.stop = really
    check("a call on this device (it rings, is placed or joined) stops "
          "the recording where it is, and a message that was playing",
          sheet.recorder.ended and camera.closes == 1 and hushed == [1]
          and sheets(M.VideoNoteSheet) == [sheet] and not SENT,
          (camera.closes, hushed, SENT))


step(sheet_call_begins, 0.4)


def sheet_after_call():
    sheet = SHEET["sheet"]
    check("...which waits, under the call, for Send or Delete",
          sheet.time.text.startswith("Recording stopped at 0:0") and
          not SENT, sheet.time.text)
    sheet.dismiss(animation=False)
    check("...and Delete keeps nothing", not SENT and leftovers() == [],
          leftovers())
    recorder = M.VideoNoteRecorder(1_000_000)
    sheet = M.VideoNoteSheet(app, recorder)
    words = [w.text for w in walk(sheet) if isinstance(w, M.Label)]
    check("where the relay takes smaller files, the sheet says how long "
          "a message may be there",
          "Up to 0:10, with sound" in words and
          "Up to a minute, with sound" not in words, words)
    sheet._closed()                     # never opened: its tick, its camera
    limits = []
    for blob_max, peer in ((None, PEER), (2 ** 20, PEER),
                           (64 * 2 ** 20, PEER), (2 ** 20, keys.address)):
        app.api._features = {"blobs": True, "blob_max": blob_max}
        limits.append(app.media_limit(peer))
    app.api._features = {"blobs": True}
    check("the recorder is given what this chat's relay takes: the "
          "app's own limit, the relay's where it is less, never more; a "
          "note to self goes to no relay",
          limits == [M.MAX_MEDIA_BYTES - 64, 2 ** 20 - 64,
                     M.MAX_MEDIA_BYTES - 64, M.MAX_MEDIA_BYTES - 64] and
          app.media_limit(PEER) == M.MAX_MEDIA_BYTES - 64, limits)
    camera = SHEET["camera"] = Camera(early=0)
    camera.error = "no camera"
    sheet = SHEET["sheet"] = M.VideoNoteSheet(app, M.VideoNoteRecorder())
    sheet.open()


step(sheet_after_call, 0.5)


def sheet_no_camera():
    sheet = SHEET["sheet"]
    check("a camera that will not open is said in the sheet, and Record "
          "is off", sheet.time.text == "Couldn't open the camera" and
          sheet.record.disabled, sheet.time.text)

    def silent():
        raise OSError("no way to record here")
    SHEET["camera"].error = ""
    really, M.PcmRecorder = M.PcmRecorder, silent
    sheet._record()
    M.PcmRecorder = really
    check("with no microphone, Record says so and the sheet closes",
          STATUS[-1].startswith("Couldn't start recording") and
          SHEET["camera"].closed and not SENT, STATUS[-1:])
    close_sheets()
    del app.send_video
    M.make_video_coder = REAL_PARTS[1]


step(sheet_no_camera, 0.5)


# ---- with PyAV: real pictures and sound -------------------------------------
class FlashCamera:
    """A camera that is black until `at` (time.monotonic()), then white:
    real H.264, through main.py's own coder."""

    def __init__(self, at):
        self.at, self.error, self.plan = at, "", []
        self.coder = M.FfmpegEncoder("h264", 320, 240, 24, 300)
        self.key, self.due = True, time.monotonic()
        self.frames = {}
        for name, luma in (("black", 16), ("white", 235)):
            frame = M._pyav().VideoFrame(320, 240, "yuv420p")
            for plane, value in zip(frame.planes, (luma, 128, 128)):
                plane.update(bytes([value]) * plane.buffer_size)
            self.frames[name] = frame

    def set_plan(self, plan, codec):
        self.plan = list(plan)

    def ask_key(self, layer=None):
        self.key = True

    def preview(self, box):
        return None

    def poll(self, timeout):
        time.sleep(max(0.0, self.due - time.monotonic()))
        self.due = max(self.due + 1 / 24, time.monotonic() - 0.02)
        if not self.plan:
            return []
        key, self.key = self.key, False
        frame = self.frames["white" if time.monotonic() >= self.at
                            else "black"]
        return [(0, "h264", is_key, data)
                for data, is_key in self.coder.encode(frame, key)]

    def close(self):
        pass


class FlashMicrophone:
    """A capture route's source: silence until `at`, then a tone, every
    sample at its own time."""
    name, failed = "flash", False

    def __init__(self, feed, at):
        self.feed, self.at = feed, at
        self.halt = threading.Event()
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        began, done = time.monotonic(), 0
        first = int((self.at - began) * 16000)
        while not self.halt.wait(0.01):
            until = int((time.monotonic() - began) * 16000)
            self.feed(b"".join(struct.pack(
                "<h", 0 if i < first else 12000 if i // 8 % 2 else -12000)
                for i in range(done, until)))
            done = until

    def stop(self):
        self.halt.set()


class Speaker:
    """A speaker that takes the sound at its own pace, as a real one does
    (the machines these suites run on have none)."""

    def __init__(self, pull):
        self.pull, self.on = pull, True
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        every, due = M.CALL_FRAME_SAMPLES / M.PCM_RATE, time.monotonic()
        while self.on:
            self.pull()
            due += every
            time.sleep(max(0.0, due - time.monotonic()))

    def stop(self):
        self.on = False


def real_recording():
    print("\nA real recording: the test picture and a tone, through PyAV")
    av = M._pyav()
    import av.logging
    rec = M.VideoNoteRecorder()
    run_until(lambda: rec.preview((320, 320)) is not None)
    rec.start()
    run_until(lambda: rec.elapsed_ms >= 2600, 10)
    rec.preview((320, 320))
    rec.stop()
    path, length, width, height, poster = rec.write()
    with av.logging.Capture(True) as logs:
        with av.open(path) as played:
            kinds = sorted(st.type for st in played.streams)
            names = sorted(st.codec_context.name for st in played.streams)
            shown = sum(1 for _ in played.decode(video=0))
        with av.open(path) as played:
            heard = sum(f.samples for f in played.decode(audio=0)) / 16000
    said = [str(e[2]).strip() for e in logs if e[0] <= av.logging.WARNING]
    video = M.mp4_read(path)
    check("FFmpeg opens the file: H.264 pictures and AAC sound, every "
          "picture decoded, and nothing it warns of",
          kinds == ["audio", "video"] and names == ["aac", "h264"] and
          shown == len(video.samples) and not said,
          (kinds, names, shown, len(video.samples), said))
    check("480 lines, about 24 pictures a second, a key picture every "
          "two seconds",
          (width, height) == (852, 480) and
          20 <= shown * 1000 / length <= 26 and
          [round(s[0] / 1000) for s in video.samples if s[1]] == [0, 2],
          (width, height, shown, length))
    check("the sound is as long as the pictures",
          abs(heard * 1000 - length) <= 70 and 2500 <= length <= 3400,
          (heard, length))
    check("at this rate a minute fits in a message with room to spare, "
          "and its still picture in the message itself",
          os.path.getsize(path) * 60000 / length < 0.9 * M.MAX_MEDIA_BYTES
          and M.image_header(M.b64d(poster))[0] == "JPEG" and
          len(poster) <= 24000, (os.path.getsize(path), length))
    M.discard_media_file(path)
    check("nothing is left of the recording but that file",
          leftovers() == [], leftovers())


def flash_and_tone():
    """Where a flash and a tone made at the same moment are in the file
    recorded of them: (seconds to the flash, seconds to the tone)."""
    at = time.monotonic() + 1.2
    M.make_video_coder = lambda track: FlashCamera(at)
    M._capture_routes = lambda rate=None: [
        lambda feed, chunk=None: FlashMicrophone(feed, at)]
    try:
        rec = M.VideoNoteRecorder()
        rec.start()
        run_until(lambda: rec.elapsed_ms >= 2200, 10)
        rec.stop()
        path = rec.write()[0]
    finally:
        M.make_video_coder = REAL_PARTS[1]
        M._capture_routes = tone_microphone
    flash = None
    with M._pyav().open(path) as played:
        stream = played.streams.video[0]
        for frame in played.decode(stream):
            if bytes(frame.reformat(16, 12, "gray").planes[0])[0] > 128:
                flash = float(frame.pts * stream.time_base)
                break
    import array
    pcm = array.array("h", M.decode_voice_pcm(path) or b"")
    tone = next((i for i, v in enumerate(pcm) if abs(v) > 4000), None)
    M.discard_media_file(path)
    return flash, tone / 16000 if tone is not None else None


def in_step():
    print("\nSound and pictures in step")
    tries = []
    for _ in range(2):      # in real time: a busy machine gets a second go
        flash, tone = flash_and_tone()
        tries.append((flash, tone))
        if flash is not None and tone is not None and \
                abs(flash - tone) <= 0.1:
            break
    check("a flash and a tone made at the same moment are in the file "
          "within a tenth of a second of each other (a computer's "
          "recording; a phone's needs a phone)",
          flash is not None and tone is not None and
          abs(flash - tone) <= 0.1 and 0.5 <= flash <= 1.5, tries)


PLAY = {}


def record_in_app():
    print("\nRecorded, sent and played in the app (a note to self)")
    M._playback_routes = lambda fmt=None: [Speaker]
    app.open_conversation(keys.address)
    PLAY["before"] = len(app.store.messages_for(keys.address))
    app.voice.set_speed(2.0)        # a voice message's speed, remembered
    screen().camera_menu()


def record_menu():
    menu = sheets(M.MenuSheet)
    check("with a camera and PyAV, the camera key offers the video "
          "message", bool(menu))
    close_sheets()
    screen().record_video()


def record_sheet():
    sheet = (sheets(M.VideoNoteSheet) or [None])[0]
    if sheet is None or sheet.view.texture is None:
        return False
    buttons = [w.text.lower() for w in walk(sheet.before)
               if isinstance(w, M.Btn)]
    check("its sheet shows the camera; a computer has one camera, so no "
          "other is offered; nothing is said in a note to self",
          sorted(buttons) == ["cancel", "record"] and
          not any(isinstance(w, M.Label) and "18.1" in w.text
                  for w in walk(sheet)), buttons)
    PLAY["sheet"] = sheet
    sheet._record()


def record_send():
    if PLAY["sheet"].recorder.elapsed_ms < 2000:
        return False
    PLAY["sheet"]._send()


def record_arrived():
    rows = app.store.messages_for(keys.address)
    if len(rows) == PLAY["before"]:
        return False
    row = PLAY["row"] = rows[-1]
    media = M.message_ext(row).get("media") or {}
    check("Send puts it in the chat: a video with its words, length, "
          "size and still picture, its file kept here",
          row["kind"] == "video" and row["body"] in (
              "Video message (0:02)", "Video message (0:03)") and
          media.get("mime") == "video/mp4" and
          (media.get("w"), media.get("h")) == (852, 480) and
          media.get("thumb") and row["media_state"] == "ready" and
          os.path.isfile(row["media_path"]) and leftovers() == [],
          (row["kind"], row["body"], row["media_state"], leftovers()))


def play_tap():
    bubble = screen()._bubbles.get(PLAY["row"]["id"])
    if bubble is None or not isinstance(bubble.media, M.VideoContent):
        return False
    PLAY["content"] = bubble.media
    PLAY["poster"] = bubble.media.picture.texture
    screen().on_media_tap(PLAY["row"])


def play_running():
    box, s = PLAY["content"], screen()
    if box._frames is None or box.progress <= 0:
        return False
    check("a tap plays it in its bubble: its pictures in place of the "
          "still one, the play mark and length away, a line of progress",
          s.video_playing(PLAY["row"]) and
          box.picture.texture is box._frames and
          box.picture.texture is not PLAY["poster"] and
          box.mark.opacity == 0 and box.length.opacity == 0 and
          0 < box.progress < 1, (box.progress, box.mark.opacity))
    check("...the pictures no larger than the bubble, in the video's shape",
          box._frames.width <= box.width + 4 and
          abs(box._frames.width / box._frames.height - 852 / 480) < 0.05,
          (tuple(box._frames.size), tuple(box.size)))
    check("...at its own speed, whatever speed voice messages play at, "
          "and that speed is kept for them",
          app.voice._rate == 1.0 and app.voice.speed == 2.0)
    PLAY["at"] = app.voice.position_ms()
    PLAY["seen"] = s._video.latest[0]


def play_follows():
    s = screen()
    now, seen = app.voice.position_ms(), s._video.latest[0]
    check("the pictures follow the sound: about 24 new ones for every "
          "second of it",
          now > PLAY["at"] and
          14 <= (seen - PLAY["seen"]) * 1000 / (now - PLAY["at"]) <= 30,
          (now - PLAY["at"], seen - PLAY["seen"]))
    PLAY["thread"] = s._video
    s.on_media_tap(PLAY["row"])


def play_stopped():
    box, s = PLAY["content"], screen()
    check("a second tap stops it: the still picture, the play mark and "
          "the length are back, and its pictures' thread ends",
          not app.voice.playing and s._video is None and
          box.picture.texture is PLAY["poster"] and box.mark.opacity == 1
          and box.length.opacity == 1 and box.progress == 0 and
          not PLAY["thread"].is_alive(),
          (box.mark.opacity, box.progress, PLAY["thread"].is_alive()))
    s.on_media_tap(PLAY["row"])


def play_to_the_end():
    if app.voice.playing:
        return False
    box = PLAY["content"]
    check("played to its end, it stops by itself and is as it was",
          screen()._video is None and box.mark.opacity == 1 and
          box.picture.texture is PLAY["poster"] and box.progress == 0)
    said = []
    verdict = M._selftest_video_message(
        lambda *words: (said.append(words), words[1])[1],
        decoder=M.FfmpegDecoder("h264"), summary=lambda path: (
            2, int(M.mp4_read(path).length_ms)), seconds=1.0)
    check("the emulators' self-test, with this computer's decoder in "
          "Android's place: a video message recorded, read back and "
          "decoded passes",
          verdict == "PASS" and said[0][0] == "video-message" and
          leftovers() == [], said)


if HAVE_AV:
    step(real_recording, 0.2)
    step(in_step, 0.2)
    step(record_in_app, 0.8)
    step(record_menu, 0.8)
    step(record_sheet, 0.3)
    step(record_send, 0.1)
    step(record_arrived, 0.8)
    step(play_tap, 0.3)
    step(play_running, 0.5)
    step(play_follows, 0.5)
    step(play_stopped, 0.3)
    step(play_to_the_end, 0.2)
else:
    print("\n  (no PyAV on this Python: a real recording, its timing and "
          "playing in the app are checked where it is installed)")


def finish():
    passed = sum(1 for _n, ok in RESULTS if ok)
    print(f"\n  {passed}/{len(RESULTS)} passed", flush=True)
    os._exit(0 if passed == len(RESULTS) else 1)


def start(_dt):
    if app.splash is not None or app.store is None:
        Clock.schedule_once(start, 0.2)
        return
    Clock.schedule_once(lambda _dt: run(0), 0.3)


Clock.schedule_once(start, 0.5)
threading.Timer(300, lambda: os._exit(3)).start()
app.run()
