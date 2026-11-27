#!/usr/bin/env python3
"""A phone's screen in a call (18.0, PHONESHARE), checked on a computer.

    xvfb-run -a python3 desktop/test_screen.py main.py

AndroidScreenCoder is Python through pyjnius; here it runs against a
stand-in of the Android classes it uses, which keeps Android's own rules
and refuses what Android would: a projection before the foreground
service (Android 10 on), a display before the stop callback (Android 14),
a second display or a second use of the person's yes (Android 14), and
the app's own classes from a thread Python started (the share starts on
its sender's). What this cannot check is Android itself: the Devices
workflow's emulators do (main.py's self-test shares the screen there).
"""
import contextlib
import importlib.util
import inspect
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
if sys.platform.startswith("linux"):
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-screen-")
os.environ["FEXT_LANG"] = "en"

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "fext_screen", sys.argv[1] if len(sys.argv) > 1 else ROOT / "main.py")
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_screen"] = M
_spec.loader.exec_module(M)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {str(detail)[:500]}" if detail and not cond else ""),
          flush=True)


class Android:
    """The stand-in: Android's classes as AndroidScreenCoder uses them, at
    one SDK level, keeping the order of what was done (self.done). As on a
    phone, the app's own classes (APP) are found from the UI thread (the
    main one here) alone; once found, from any (pyjnius keeps them)."""

    APP = ("org.fext.", "org.jnius.")

    def __init__(self, sdk=34, frames=()):
        self.sdk, self.done, self.service_up = sdk, [], False
        self.frames = list(frames)         # (flags, bytes) the coder makes
        self.used = set()                  # the yeses used (Android 14)
        self.projection = None
        self.found = set()                 # the app's classes found

    # pyjnius, as AndroidJni gives it
    def autoclass(self, name):
        if name.startswith(self.APP) and name not in self.found:
            if threading.current_thread() is not threading.main_thread():
                raise RuntimeError("java.lang.ClassNotFoundException: " +
                                   name)
            self.found.add(name)
        return CLASSES[name](self) if name in FACTORIES else CLASSES[name]

    def context(self):
        return Context(self)

    def runnable(self, fn):
        self.autoclass("org.jnius.NativeInvocationHandler")  # pyjnius' proxy
        return type("Run", (), {"run": staticmethod(fn)})()


class Context:
    MEDIA_PROJECTION_SERVICE = "media_projection"

    def __init__(self, android):
        self.android = android

    @staticmethod
    def getPackageName():
        return "org.fext.fext"

    def getSystemService(self, name):
        assert name == "media_projection"
        return Manager(self.android)

    def getWindowManager(self):
        android = self.android

        class Display:
            @staticmethod
            def getRealMetrics(metrics):
                metrics.widthPixels, metrics.heightPixels = 1080, 2400
                metrics.densityDpi = 420
                android.done.append("metrics")
        return type("WM", (), {"getDefaultDisplay": staticmethod(
            lambda: Display())})()


class Manager:
    def __init__(self, android):
        self.android = android

    def getMediaProjection(self, code, data):
        a = self.android
        a.done.append("getMediaProjection")
        if a.sdk >= 29 and not a.service_up:
            raise RuntimeError("SecurityException: no foreground service of "
                               "type mediaProjection")
        if a.sdk >= 34 and data in a.used:
            raise RuntimeError("SecurityException: that yes was used")
        a.used.add(data)
        a.projection = Projection(a)
        return a.projection


class Projection:
    def __init__(self, android):
        self.android, self.callback, self.displays = android, None, 0

    def registerCallback(self, callback, handler):
        assert handler is not None, "a Handler, not the calling thread's"
        self.android.done.append("registerCallback")
        self.callback = callback

    def unregisterCallback(self, callback):
        self.android.done.append("unregisterCallback")

    def createVirtualDisplay(self, name, w, h, dpi, flags, surface, cb, hnd):
        a = self.android
        a.done.append("createVirtualDisplay")
        if a.sdk >= 34 and self.callback is None:
            raise RuntimeError("IllegalStateException: register a callback "
                               "first")
        if a.sdk >= 34 and self.displays:
            raise RuntimeError("SecurityException: one display a projection")
        self.displays += 1
        a.display_args = (name, w, h, dpi, flags, surface)
        return type("Display", (), {"release": staticmethod(
            lambda: a.done.append("display.release"))})()

    def stop(self):
        self.android.done.append("projection.stop")


class Format:
    KEY_COLOR_FORMAT, KEY_BIT_RATE = "color-format", "bitrate"
    KEY_FRAME_RATE, KEY_I_FRAME_INTERVAL = "frame-rate", "i-frame-interval"
    KEY_REPEAT_PREVIOUS_FRAME_AFTER = "repeat-previous-frame-after"

    def __init__(self, mime, w, h):
        self.values = {"mime": mime, "width": w, "height": h}

    @classmethod
    def createVideoFormat(cls, mime, w, h):
        return cls(mime, w, h)

    def setInteger(self, key, value):
        self.values[key] = value

    setLong = setInteger


class Codec:
    CONFIGURE_FLAG_ENCODE, INFO_TRY_AGAIN_LATER = 1, -1
    BUFFER_FLAG_KEY_FRAME, BUFFER_FLAG_CODEC_CONFIG = 1, 2
    PARAMETER_KEY_VIDEO_BITRATE = "video-bitrate"
    PARAMETER_KEY_REQUEST_SYNC_FRAME = "request-sync"
    made = []

    def __init__(self, android):
        self.android, self.out, self.params = android, {}, []

    @classmethod
    def createEncoderByType(cls, mime):
        codec = cls(cls.android)
        cls.made.append(codec)
        codec.android.done.append("createEncoderByType")
        return codec

    def configure(self, fmt, surface, crypto, flags):
        self.format = fmt.values
        self.android.done.append("configure")

    def createInputSurface(self):
        self.android.done.append("createInputSurface")
        return "input-surface"

    def start(self):
        self.android.done.append("encoder.start")

    def dequeueOutputBuffer(self, info, timeout):
        if not self.android.frames:
            return -1
        flags, data = self.android.frames.pop(0)
        index = len(self.out)
        self.out[index] = data
        info.offset, info.size, info.flags = 0, len(data), flags
        return index

    def getOutputBuffer(self, index):
        return Held(self.out[index])

    def releaseOutputBuffer(self, index, render):
        del self.out[index]

    def setParameters(self, bundle):
        self.params.append(dict(bundle))

    def stop(self):
        self.android.done.append("encoder.stop")

    def release(self):
        self.android.done.append("encoder.release")


class Held:
    """An output buffer: its data seen through a position and a limit."""

    def __init__(self, data):
        self.data, self.pos, self.end = data, 0, len(data)

    def position(self, n):
        self.pos = n

    def limit(self, n):
        self.end = n


class Heap:
    def __init__(self, n):
        self.buf = b""

    @classmethod
    def allocate(cls, n):
        return cls(n)

    def put(self, held):
        self.buf += held.data[held.pos:held.end]

    def array(self):
        return bytearray(self.buf)


class Info:
    def __init__(self):
        self.offset = self.size = self.flags = 0


class Metrics:
    widthPixels = heightPixels = densityDpi = 0


class Stop:
    """org.fext.call.ProjectionStop: holds the Runnable it was given."""

    def __init__(self, then):
        self.then = then


class JString(str):
    """java.lang.String, as pyjnius makes one."""


class ExtraIntent:
    """android.content.Intent's string extras as pyjnius fills them (run
    61): a Python str goes in as a char[], which getStringExtra reads as
    null."""

    def __init__(self):
        self.extras = {}

    def putExtra(self, key, value):
        self.extras[key] = value if isinstance(value, JString) else \
            list(value)

    def getStringExtra(self, key):
        value = self.extras.get(key)
        return value if isinstance(value, str) else None


CLASSES = {
    "android.content.Context": Context,
    "android.os.Handler": lambda looper: ("handler", looper),
    "android.os.Looper": type("Looper", (), {"getMainLooper": staticmethod(
        lambda: "main-looper")}),
    "org.fext.call.ProjectionStop": Stop,
    "android.util.DisplayMetrics": Metrics,
    "android.media.MediaFormat": Format,
    "android.media.MediaCodec": Codec,
    "android.media.MediaCodecInfo$CodecCapabilities": type(
        "Caps", (), {"COLOR_FormatSurface": 0x7F000789}),
    "android.media.MediaCodec$BufferInfo": Info,
    "java.nio.ByteBuffer": Heap,
    "android.os.Bundle": lambda: Bundle(),
    "android.content.Intent": ExtraIntent,
    "java.lang.String": JString,
}
FACTORIES = set()


class ShareJava:
    """p4a's ServiceFextshare: started, its process runs main.py's share
    branch, which makes its flag's ".up" file: at once, or `delay`
    seconds later, as a slow phone's process starts Python."""
    started = stopped = 0
    delay = 0.0

    @classmethod
    def start(cls, ctx, icon, title, text, flag):
        cls.started += 1

        def up():
            if Path(flag).exists():            # the share still wants it
                Path(flag + ".up").touch()
        if cls.delay:
            threading.Timer(cls.delay, up).start()
        else:
            up()

    @classmethod
    def stop(cls, ctx):
        cls.stopped += 1


CLASSES["org.fext.fext.ServiceFextshare"] = ShareJava


class CameraJava(ShareJava):
    """p4a's ServiceFextcam (20.0), likewise."""
    started = stopped = 0


CLASSES["org.fext.fext.ServiceFextcam"] = CameraJava


class LocationJava(ShareJava):
    """p4a's ServiceFextloc (21.0), likewise."""
    started = stopped = 0


CLASSES["org.fext.fext.ServiceFextloc"] = LocationJava
# The call's other classes, which android_find_call_classes finds as well.
CLASSES.update(dict.fromkeys((
    "org.fext.call.CallAudio", "org.fext.call.VideoCall",
    "org.fext.call.VideoCall$Decoder", "org.jnius.NativeInvocationHandler"),
    object))


class Bundle(dict):
    def putInt(self, key, value):
        self[key] = value


class Service:
    """ShareService's stand-in: up at once, or never."""

    def __init__(self, android, comes_up=True):
        self.android, self.comes_up, self.stopped = android, comes_up, False

    def start(self, timeout=20.0):
        self.android.done.append("service.start")
        self.android.service_up = self.comes_up
        return self.comes_up

    def stop(self):
        self.stopped = True
        self.android.service_up = False
        self.android.done.append("service.stop")


SPS = b"\x00\x00\x00\x01\x67sps\x00\x00\x00\x01\x68pps"
KEY, DELTA = b"\x00\x00\x00\x01\x65key", b"\x00\x00\x00\x01\x41delta"
PLAN = [(0, 180, 15, 300), (2, 720, 15, 1500), (1, 360, 15, 600)]


def as_phone(android, fn):
    """fn() with main.py's pyjnius and Android level the stand-in's."""
    saved = M._jni, M.android_context, M.IS_ANDROID, list(M._SDK)
    M._jni, M.android_context = (lambda: (android.autoclass, None)), \
        android.context
    M.IS_ANDROID, M._SDK[:] = True, [android.sdk]
    try:
        return fn()
    finally:
        M._jni, M.android_context, M.IS_ANDROID = saved[:3]
        M._SDK[:] = saved[3]


def on_thread(fn, *args):
    """fn(*args) on a thread of its own, as VideoSender polls a coder."""
    out = []
    worker = threading.Thread(target=lambda: out.append(fn(*args)))
    worker.start()
    worker.join()
    return out[0] if out else None


def polled(coder, timeout):
    return on_thread(coder.poll, timeout)


def share(sdk, frames=((2, SPS), (1, KEY), (0, DELTA)), comes_up=True,
          found=True):
    """A coder made on the UI thread, as make_video_coder makes it, the
    call's classes found there first (android_find_call_classes) unless
    not `found`."""
    android = Android(sdk, frames)
    Codec.android, Codec.made = android, []
    service = Service(android, comes_up)
    if found:
        as_phone(android, M.android_find_call_classes)
    coder = M.AndroidScreenCoder((-1, f"yes-{sdk}"), jni=android,
                                 service=service)
    coder.set_plan(PLAN, "vp8")
    return android, service, coder


print("\nAndroid 14: the steps in Android's order")
android, service, coder = share(34)
first = polled(coder, 0.2)
done = android.done
check("the service first, then the projection, its stop callback, the "
      "encoder, and last the display",
      done[:3] == ["service.start", "getMediaProjection", "registerCallback"]
      and done.index("encoder.start") < done.index("createVirtualDisplay"),
      done)
fmt = Codec.made[0].format
check("one H.264 encoder fed by a surface, at the plan's largest size and "
      "rate, a still screen's picture repeated",
      fmt["mime"] == "video/avc" and fmt["color-format"] == 0x7F000789 and
      (fmt["width"], fmt["height"]) == (720, 1600) and
      fmt["bitrate"] == 1_500_000 and fmt["frame-rate"] == 15 and
      fmt["repeat-previous-frame-after"] == 100_000, fmt)
check("the display draws the whole screen into the encoder's surface",
      android.display_args == ("Ferritext", 720, 1600, 420, 16,
                               "input-surface"), android.display_args)
check("its first picture is a key one, the coder's settings before it, "
      "sent as the plan's largest layer in H.264; the next as it is; all "
      "that is ready at once", first == [(2, "h264", True, SPS + KEY),
                                         (2, "h264", False, DELTA)], first)
check("...and nothing more until the encoder makes more",
      polled(coder, 0.05) == [])
coder.ask_key()
coder.set_plan([(2, 720, 15, 900)], "h264")
polled(coder, 0.05)
check("a key asked for, or a new bitrate, is told to the encoder",
      {"request-sync": 0, "video-bitrate": 900_000} in Codec.made[0].params or
      ({"video-bitrate": 900_000, "request-sync": 0} in Codec.made[0].params),
      Codec.made[0].params)
check("your own screen draws no tile of its own",
      coder.preview((100, 100)) is None)
android.projection.callback.then.run()          # Android's own Stop
check("Android stops it: said, and nothing more is read",
      coder.error == "Android ended it" and
      polled(coder, 0.05) == [])
coder.close()
check("let go in the reverse of its making, the service stopped",
      done[-6:] == ["display.release", "encoder.stop", "encoder.release",
                    "unregisterCallback", "projection.stop", "service.stop"],
      done[-6:])
coder.close()
check("closed twice: harmless", service.stopped)

print("\nWhat Android refuses, refused here first")
android, service, coder = share(34, comes_up=False)
polled(coder, 0.05)
check("no service in time: no projection asked for, and said why",
      "getMediaProjection" not in android.done and
      "service" in coder.error and service.stopped, (android.done,
                                                     coder.error))
android, service, coder = share(34)
polled(coder, 0.05)
again = M.AndroidScreenCoder(None, jni=android, service=service)
again.set_plan(PLAN, "h264")
polled(again, 0.05)
check("a yes is good once: a coder without one asks Android for nothing",
      "yes" in again.error and android.done.count("getMediaProjection") == 1,
      again.error)
coder.close()
saved = M.IS_ANDROID
M.IS_ANDROID, M.AndroidScreenCoder.granted = True, None
try:
    M.make_video_coder("s")
    refused = ""
except RuntimeError as exc:
    refused = str(exc)
M.AndroidScreenCoder.granted = (-1, "yes")
taken = M.make_video_coder("s")
M.IS_ANDROID = saved
check("the call's screen on a phone: no yes, no coder; a yes, taken once",
      "yes" in refused and isinstance(taken, M.AndroidScreenCoder) and
      M.AndroidScreenCoder.granted is None, refused)

print("\nAndroid 10 and Android 9")
android, service, coder = share(29)
check("Android 10: the service before the projection is enough",
      polled(coder, 0.2)[0] == (2, "h264", True, SPS + KEY) and
      "registerCallback" in android.done)
coder.close()
android, service, coder = share(28)
check("Android 9 needs no service, and is given one all the same",
      polled(coder, 0.2) and android.done[0] == "service.start")
coder.close()

print("\nThe thread a share starts on")
android, service, coder = share(34, found=False)
polled(coder, 0.05)
check("it starts on its sender's thread, where Android finds none of the "
      "app's own classes: not found on the UI thread first, it fails, "
      "saying so", "ClassNotFoundException" in coder.error and
      service.stopped, coder.error)
android, service, coder = share(34)
check("...as a call begins they are (android_find_call_classes): the "
      "screen's stop callback, pyjnius' proxy and the share service",
      {"org.fext.call.ProjectionStop", "org.jnius.NativeInvocationHandler",
       "org.fext.fext.ServiceFextshare"} <= android.found and
      polled(coder, 0.2), android.found)
coder.close()
M.DATA_DIR.mkdir(parents=True, exist_ok=True)   # the app's, made as it starts
real = M.ShareService()


def start_and_stop():
    M.android_find_call_classes()                   # the UI thread, first
    up = on_thread(real.start, 1.0)                 # then the sender's
    on_thread(real.stop)
    return up


check("the share service, started from the sender's thread, is up once "
      "its process says so (main.py's share branch), and stops with the "
      "share", as_phone(Android(34), start_and_stop) and
      (ShareJava.started, ShareJava.stopped) == (1, 1) and
      not real.flag.exists() and not real.up.exists())


def slow_start():
    """18.0.2: as Devices run 16's Android 16 emulator, whose share service
    said so after more than the 6 s the app then waited."""
    M.android_find_call_classes()
    ShareJava.delay = 6.5
    try:
        up = on_thread(real.start)                  # its own wait
    finally:
        ShareJava.delay = 0.0
        on_thread(real.stop)
    return up


check("...and on a slow phone, whose service's Python takes 6.5 s to say "
      "so, the share waits for it", as_phone(Android(34), slow_start) and
      not real.up.exists())


class Decoder:
    """Android's decoder: a picture for each one handed in."""

    @staticmethod
    def decode(data, box, fill):
        return (b"", 0, 0, "rgba") if data else None


said = []
android, service, coder = share(34, frames=[(2, SPS), (1, KEY)] +
                                [(0, DELTA)] * 23)
verdict = on_thread(M._selftest_screen, lambda *a: said.append(a) or a[1],
                    coder, Decoder(), 0)
check("the device self-test shares the screen as a call does, and "
      "decodes what it sends", verdict == "PASS" and
      said == [("screen", "PASS", "24 coded, 24 decoded, 0 refused")] and
      service.stopped, said)
said.clear()
android, service, coder = share(34, comes_up=False)
check("...and fails, saying why, where the share would",
      on_thread(M._selftest_screen, lambda *a: said.append(a) or a[1],
                coder, Decoder(), 0) == "FAIL" and "service" in said[-1][2],
      said)

print("\nThe self-test on Android 5, where calls are off (17.3)")
printed, real_off_ui, real_back = io.StringIO(), M.off_ui, M._selftest_back
M.off_ui = lambda work: work()
M._selftest_back = lambda say: print("the Back sheet opens")
old_phone = Android(21)
try:
    with contextlib.redirect_stdout(printed):
        as_phone(old_phone, M.android_selftest)
        before_ui = printed.getvalue()
        M.Clock.tick()                     # the UI thread's turn
finally:
    M.off_ui, M._selftest_back = real_off_ui, real_back
said = [line.split(" ")[1:3] for line in printed.getvalue().splitlines()
        if line.startswith("FEXT-SELFTEST ")]
check("on Android 5 every check is skipped, the coders' and the camera's "
      "too: they are a call's (the Devices run's API 21 failed them); a "
      "string on an Intent is read back there too (21.1)",
      said == [[c, "SKIP"] for c in (
          "call-classes", "call-audio", "codecs-encoders", "codecs-decoders",
          "camera", "screen", "video-message", "video-draw",
          "camera-service", "service-notice", "call-notice", "location",
          "location-service")] + [["string-extra", "PASS"]] +
      [["live-notice", "SKIP"], ["call-lobby", "SKIP"], ["done", "1/1"]],
      said)

check("\"done\" is the UI thread's to say, once the sheet that Back closes "
      "is open: the emulators' check presses Back on reading it (18.1)",
      "done" not in before_ui and printed.getvalue().splitlines()[-2:] ==
      ["the Back sheet opens", "FEXT-SELFTEST done 1/1"],
      printed.getvalue().splitlines()[-3:])
check("the sync service's class and the notifier are found on the UI "
      "thread first, as the app finds them: the check's own thread could "
      "not (Devices run 23, API 36)",
      {"org.fext.fext.ServiceFextsync", "org.fext.notify.Notifier"}
      <= old_phone.found, sorted(old_phone.found))

print("\nSizes, and the pieces Android builds")
check("a screen's size: its shorter side at most the plan's, in 16s",
      M.screen_size(1080, 2400, 720) == (720, 1600) and
      M.screen_size(2400, 1080, 720) == (1600, 720) and
      M.screen_size(720, 1280, 1080) == (720, 1280) and
      M.screen_size(1080, 2340, 360) == (352, 768))
java = (ROOT / "android" / "ProjectionStop.java").read_text("utf-8")
check("the one Java class only passes Android's stop on to Python",
      "extends MediaProjection.Callback" in java and "then.run();" in java
      and java.count(";") <= 6)
hook = importlib.util.spec_from_file_location(
    "p4a_hook", ROOT / "tools" / "p4a_hook.py")
H = importlib.util.module_from_spec(hook)
hook.loader.exec_module(H)
manifest = ('<service android:name="org.x.ServiceFextsync" />'
            '<service android:name="org.x.ServiceFextcall" />'
            '<service android:name="org.x.ServiceFextshare" />'
            '<service android:name="org.x.ServiceFextcam" />'
            '<service android:name="org.x.ServiceFextloc" />')
typed = H.declare_service_types(manifest)
spec = (ROOT / "buildozer.spec").read_text("utf-8")
check("the share service is declared, of type mediaProjection, with its "
      "permission", 'ServiceFextshare" android:foregroundServiceType='
      '"mediaProjection"' in typed and
      "Fextshare:main.py:foreground" in spec and
      "FOREGROUND_SERVICE_MEDIA_PROJECTION" in spec)

java = """    public boolean deleteSurroundingText(int beforeLength, int afterLength) {
        if (Build.VERSION.SDK_INT <= 29 /* Android 10.0 (Q) */) {
            if (beforeLength > 0 && afterLength == 0) {
                while (beforeLength-- > 0) {
                    nativeGenerateScancodeForUnichar('\\b');
                }
                return true;
           }
        }

        if (!super.deleteSurroundingText(beforeLength, afterLength)) {
            return false;
        }
        updateText();
        return true;
    }
"""
kept = H.keep_backspace(java)
check("20.0: SDL's Java is given the keyboard's backspace past its own "
      "text (Android 11 and later deleted nothing of what the app had put "
      "in the field), once, before SDL's own deleting",
      kept.count("nativeGenerateScancodeForUnichar('\\b');") == 2 and
      kept.index("fextBefore") < kept.index("if (!super.deleteSurr") and
      H.keep_backspace(kept) == kept)
try:
    H.keep_backspace(java.replace("super.delete", "other.delete"))
    refused = False
except RuntimeError as exc:
    refused = "backspace" in str(exc)
check("...and a build with another SDL stops, saying so", refused)

print("\n20.0: a call's camera kept on when the app leaves the screen")
check("the camera service is declared, of type camera, with its "
      "permission; the call service stays the microphone's alone (Android "
      "14 would refuse it to someone who never gave the camera)",
      'ServiceFextcam" android:foregroundServiceType="camera"' in typed and
      'ServiceFextcall" android:foregroundServiceType="microphone"' in typed
      and "Fextcam:main.py:foreground" in spec and
      "FOREGROUND_SERVICE_CAMERA" in spec)
check("21.0: the location service is declared, of type location, with "
      "its permission and the place's",
      'ServiceFextloc" android:foregroundServiceType="location"' in typed
      and "Fextloc:main.py:foreground" in spec and
      "FOREGROUND_SERVICE_LOCATION" in spec and
      "ACCESS_FINE_LOCATION" in spec and "ACCESS_COARSE_LOCATION" in spec)


class Video:
    """A call's video with the camera's coder in it, or not."""
    def __init__(self):
        self.coders, self.stopped = {}, []

    def start(self, track):
        self.coders[track] = object()
        return ""

    def stop(self, track):
        self.coders.pop(track, None)
        self.stopped.append(track)

    def close(self):
        self.coders.clear()


class Shell:
    """FextApp's camera parts, on a stand-in for the rest of the app."""
    _camera_service = M.FextApp._camera_service
    _pause_call_camera = M.FextApp._pause_call_camera
    stop_call_video = M.FextApp.stop_call_video
    close_call_video = M.FextApp.close_call_video

    def __init__(self):
        self.video, self.toggled = Video(), []
        self.call = types.SimpleNamespace(ended=False, state="active")

    def call_video(self, create=True):
        return self.video

    def _toggle_call_video(self, track):
        self.toggled.append(track)


phone, shell = Android(34), Shell()
service = M.CameraService()
shell.video.start("v")
as_phone(phone, lambda: shell._camera_service(True))
check("a call's camera turned on starts the camera service, in sight "
      "(Android gives a service started out of sight no camera)",
      CameraJava.started == 1 and service.on)
as_phone(phone, lambda: shell._camera_service(True))
check("...once", CameraJava.started == 1)
as_phone(phone, shell._pause_call_camera)
check("the app left with it on, in a call: the camera stays on (it went "
      "off, and the other side lost the picture)",
      shell.toggled == [] and "_camera_paused" not in shell.__dict__)
shell.call.state = "incoming"
as_phone(phone, shell._pause_call_camera)
check("...the lobby's, before the call is answered, still goes off",
      shell.toggled == ["v"] and shell.__dict__.pop("_camera_paused"))
shell.call.state = "active"
as_phone(phone, lambda: shell.stop_call_video("v"))
check("the camera turned off stops the service",
      CameraJava.stopped == 1 and not service.on and
      shell.video.stopped == ["v"])
shell.video.start("v")
del shell.toggled[:]
as_phone(phone, shell._pause_call_camera)
check("...and without the service (it could not start), the camera goes "
      "off out of sight, as before", shell.toggled == ["v"])
as_phone(phone, lambda: shell._camera_service(True))
as_phone(phone, shell.close_call_video)
check("the call over: the service stopped with the camera",
      CameraJava.stopped == 2 and not service.on)
before = CameraJava.started
shell._camera_service(True)
check("a computer has no such service", CameraJava.started == before)
said = []
verdict = as_phone(phone, lambda: M._selftest_camera_service(
    lambda *a: said.append(a) or a[1], M.CameraService(), 2.0))
check("the device self-test starts it, sees it up, and stops it",
      verdict == "PASS" and said == [("camera-service", "PASS", "up")] and
      not service.on, said)
CameraJava.delay = 5.0
check("...and fails where it does not come up",
      as_phone(phone, lambda: M._selftest_camera_service(
          lambda *a: a[1], M.CameraService(), 0.3)) == "FAIL")
CameraJava.delay = 0.0

print("\nThe location self-test (21.0)")


class StandInPlace:
    """AndroidLocation's part the self-test uses: a provider on or off,
    and the points it hands a follower."""

    PROVIDERS, enabled, points = ("gps", "network"), {"gps"}, []

    class _Manager:
        @staticmethod
        def isProviderEnabled(name):
            return name in StandInPlace.enabled

    @classmethod
    def _manager(cls):
        return cls._Manager()

    def follow(self, on_point):
        for point in StandInPlace.points:
            on_point(point)

    def unfollow(self, on_point):
        pass


def location_selftest(wait=1.0, up_wait=1.0):
    """Its verdicts, run on a thread of its own as the self-test's are,
    the UI thread's clock turning meanwhile, on a stand-in phone that has
    found none of the app's classes yet (as a device, it finds them from
    the UI thread alone)."""
    said = []
    worker = threading.Thread(target=lambda: M._selftest_location(
        lambda *a: said.append(a) or a[1], wait, up_wait))

    def run():
        worker.start()
        while worker.is_alive():
            M.Clock.tick()
            worker.join(0.01)
    as_phone(Android(34), run)
    return said


real_location = M.AndroidLocation, M.LOCATION_SOURCE, M.android_has_permission
M.AndroidLocation = StandInPlace
M.android_has_permission = lambda _p: True
StandInPlace.points = [{"lat": 51.5007, "lon": -0.1246, "acc": 5, "at": 1}]
said = location_selftest()
check("the device self-test finds where the phone is as a live share "
      "does, and launches the location service as a live share does, "
      "from the UI thread (from a thread of its own its class was not "
      "found: Devices run 52, Android 5); up, then stopped",
      said == [("location", "PASS", "51.500700, -0.124600 (±5 m)"),
               ("location-service", "PASS", "up")] and
      (LocationJava.started, LocationJava.stopped) == (1, 1), said)
StandInPlace.points, LocationJava.delay = [], 5.0
said = location_selftest()
check("...and fails, saying why, where no point comes or the service does "
      "not come up",
      said == [("location", "FAIL", "no point in 1 s from gps"),
               ("location-service", "FAIL", "not up in 1 s")], said)
LocationJava.delay = 0.0
StandInPlace.enabled = set()
check("...with location off, the first is skipped",
      location_selftest()[0] == ("location", "SKIP", "location is off"))
M.android_has_permission = lambda _p: False
check("...without the permission, both are",
      location_selftest() == [
          ("location", "SKIP", "no location permission"),
          ("location-service", "SKIP", "no location permission")])

print("\nThe live location's notice self-test (21.1, LIVENOTICE30)")


def live_notice_selftest(shows=True, stops=True, sdk=34):
    """Its verdict on a stand-in phone: the notice shows the words the flag
    file holds while the service runs (as Notifier.liveNotice does), and
    Stop does what NotifyReceiver does (the mark left, the flag taken)."""
    said = []

    def shown():
        if not (shows and service.flag.exists()):
            return None
        return json.loads(service.flag.read_text())["text"]

    def press_stop(svc):
        if stops:
            Path(str(svc.flag) + ".stopped").touch()
            svc.flag.unlink()

    service = M.LocationService()

    def run():
        worker = threading.Thread(target=lambda: M._selftest_live_notice(
            lambda *a: said.append(a) or a[1], 1.0, service, shown,
            press_stop))
        worker.start()
        while worker.is_alive():
            M.Clock.tick()
            worker.join(0.01)
    as_phone(Android(sdk), run)
    return said, service


M.android_has_permission = lambda _p: True
said, service = live_notice_selftest()
check("the location service's notice says with whom and until when, and "
      "its Stop ends the service and tells the app",
      said == [("live-notice", "PASS", "'With the self-test until 12:00'; "
                "Stop: the service ended, the app told")] and
      not service.flag.exists() and not service.stopped_mark.exists(), said)
check("...and fails, saying why, where the notice is not the app's own",
      live_notice_selftest(shows=False)[0] == [
          ("live-notice", "FAIL", "shown: None")])
check("...or Stop leaves the service running",
      live_notice_selftest(stops=False)[0][0][1] == "FAIL")
check("...skipped before Android 6, which cannot say what is shown",
      live_notice_selftest(sdk=21)[0] == [
          ("live-notice", "SKIP", "needs Android 6")])


class Shown:
    """A notice Android shows (StatusBarNotification), as pyjnius gives
    it: its words a Python str when they are a String."""

    def __init__(self, nid, text):
        self.nid, self.text = nid, text

    def getId(self):
        return self.nid

    def getNotification(self):
        return types.SimpleNamespace(extras=types.SimpleNamespace(
            getCharSequence=lambda key: self.text
            if key == "android.text" else None))


class Styled:
    """Words that are not a String: a Java object to pyjnius."""

    @staticmethod
    def toString():
        return "With Ann until 12:00"


def notice_words(shown, nid=8):
    saved = M.android_context
    manager = types.SimpleNamespace(getActiveNotifications=lambda: shown)
    M.android_context = lambda: types.SimpleNamespace(
        getSystemService=lambda _name: manager)
    try:
        return M.android_notice_words(nid)
    finally:
        M.android_context = saved


def press_stop_sent():
    """What android_press_live_stop hands to Java, on a stand-in phone."""
    pressed = []
    ctx = types.SimpleNamespace(getPackageName=lambda: "org.fext.fext")
    saved = M.android_context, M._notifier
    M.android_context = lambda: ctx
    M._notifier = lambda: types.SimpleNamespace(
        pressStopLive=lambda *given: pressed.append(given))
    try:
        M.android_press_live_stop(types.SimpleNamespace(flag=Path("/f/.x")))
    finally:
        M.android_context, M._notifier = saved
    return pressed, ctx


def java_body(java, head):
    """One method of a Java file, from its head to its closing brace."""
    body = java[java.index(head):]
    return body[:body.index("\n    }\n")]


pressed, phone = press_stop_sent()
notifier = (ROOT / "android" / "Notifier.java").read_text("utf-8")
live, built, press = (java_body(notifier, head) for head in (
    "public static void liveNotice(", "static Intent stopLiveIntent(",
    "public static void pressStopLive("))
check("...its Stop, as the self-test presses it, is what the notice's own "
      "Stop sends, built in Java (pyjnius gave the activity to setClassName "
      "as a name, run 60, and the flag to putExtra as a char[], run 61): to "
      "the app's own receiver, with the flag as a string",
      pressed == [(phone, str(Path("/f/.x")))] and
      type(pressed[0][1]) is str and
      "stopLiveIntent(service, flag)" in live and
      "sendBroadcast(stopLiveIntent(ctx, flag))" in press and
      "setClass(ctx, NotifyReceiver.class)" in built and
      'putExtra("flag", flag)' in built, (pressed, built))
# Every other extra: the self-test's bare str (below) is on purpose.
source = Path(_spec.origin).read_text("utf-8").replace(
    inspect.getsource(M._selftest_string_extra), "")
values = re.findall(r"\.putExtra\([^,]+,\s*([^\s(]+)", source)
check("each extra the app puts on an Intent is a Java object (_jstring, "
      "_charseq, cast): a Python str would go in as a char[], which "
      "Android reads back as none (run 61)",
      len(values) == source.count(".putExtra(") and
      set(values) <= {"_jstring", "_charseq", "cast"}, values)


def string_extra(jstring=None):
    """The self-test's string-extra check on a stand-in phone."""
    said = []
    saved = M._jni, M._jstring
    M._jni = lambda: (CLASSES.get, None)
    M._jstring = jstring or M._jstring
    try:
        verdict = M._selftest_string_extra(
            lambda *words: said.append(words) or words[1])
    finally:
        M._jni, M._jstring = saved
    return verdict, said[-1][2]


passed, detail = string_extra()
failed, _ = string_extra(jstring=lambda text: text)
check("the emulators' self-test reads a Java String back from an Intent "
      "(string-extra), says what a Python str gives, and fails where "
      "the app's String would not read back",
      passed == "PASS" and failed == "FAIL" and detail ==
      "a Java String read back as 'fext', a Python str as None", detail)
check("a notice's words read back, a String as Python's str (run 59, "
      "Android 16) or styled words as a Java object; none shown is None",
      notice_words([Shown(8, "With Ann until 12:00")]) ==
      notice_words([Shown(3, "x"), Shown(8, Styled())]) ==
      "With Ann until 12:00" and notice_words([Shown(8, None)]) == "" and
      notice_words([Shown(3, "x")]) is None)


class LiveShell:
    """FextApp's live-share parts, on a stand-in for the rest."""
    _follow_live = M.FextApp._follow_live
    _live_service = M.FextApp._live_service
    _live_check_stop = M.FextApp._live_check_stop

    def __init__(self):
        self._live, self._live_clock, self.stops = {}, None, []

    def live_notice_words(self):
        return "With Ann until 12:00"

    def stop_live(self, peer, cid):
        self.stops.append(cid)
        self._live.pop(cid, None)

    def _on_live_point(self, point):
        pass

    def _live_tick(self, _dt):
        pass


M.LOCATION_SOURCE, live, before = StandInPlace(), LiveShell(), \
    LocationJava.started
as_phone(Android(34), lambda: live._follow_live(
    "peer", "cid", int(time.time()) + 900, None))
check("a live share launches the location service in sight, from the UI "
      "thread, as the camera's (on a thread of its own, its class was not "
      "found and the share was not kept up out of sight)",
      LocationJava.started == before + 1 and "cid" in live._live)
check("21.1: ...its notice's words given to the service with its flag",
      json.loads(M.LocationService().flag.read_text()) == {
          "title": "Sharing your location", "text": "With Ann until 12:00"})
M.LocationService().stopped_mark.touch()         # Stop tapped on the notice
as_phone(Android(34), lambda: live._live_check_stop())
check("...and once its Stop was tapped, every share this phone keeps up "
      "ends, the app told once", live.stops == ["cid"] and not live._live
      and not M.LocationService().stopped_mark.exists(), live.stops)
live._live_clock.cancel()
M.AndroidLocation, M.LOCATION_SOURCE, M.android_has_permission = \
    real_location

passed = sum(RESULTS)
print(f"\n  {passed}/{len(RESULTS)} passed")
os._exit(0 if passed == len(RESULTS) else 1)
