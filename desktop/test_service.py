#!/usr/bin/env python3
"""Background-service coordination and the Android 14 manifest hook.

    python3 desktop/test_service.py main.py

Runs WITHOUT a display, the way the sync service runs on a phone: Android
starts it from main.py, python-for-android names it in PYTHON_NAME, and
main.py must then import with no window. The service loop is driven for a
scripted few cycles with the network worker replaced by a recorder; both
services are also started from main.py as processes of their own.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ["PYTHON_NAME"] = "Fextsync"        # as python-for-android sets it
os.environ.pop("DISPLAY", None)
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-svc-")
os.environ["USERPROFILE"] = os.environ["HOME"]   # Path.home() on Windows
os.environ["FEXT_LANG"] = "en"          # the English the checks read
ROOT = Path(__file__).resolve().parent.parent
MAIN = Path(sys.argv[1] if len(sys.argv) > 1 else "main.py").resolve()

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""))


spec = importlib.util.spec_from_file_location("main", MAIN)
M = importlib.util.module_from_spec(spec)
sys.modules["main"] = M
spec.loader.exec_module(M)
from kivy.core.window import Window                            # noqa: E402

print("\nHeadless import (service process)")
check("main.py imports with no display and no window", Window is None)

print("\nService loop")
keys = M.IdentityManager()
priv, _p, _a, _n = M.mine_identity()
me = keys.add_identity(priv, "phone")
keys.activate(me)
keys.set_username("svc_user")
pk2, _p, _a, _n = M.mine_identity()
pal, muted = M.address_from_priv(pk2), "Fec" + "M" * 31
store = M.LocalStore(keys.store_path())
store.upsert_contact(pal, "pal", M.pub_to_compressed(M.priv_to_pub(pk2)).hex())
store.upsert_contact(muted, "quiet", "02" + "ab" * 32)
store.set_contact_setting(muted, "notify", 0)
store.close()

events = []


class FakeWorker:
    """Records lifecycle; on start, announces two new messages, as the real
    worker does from its own thread (v11)."""

    def __init__(self, app):
        import threading
        self.app, self.stop_event = app, threading.Event()
        self.beat_owner = "app"

    def start(self):
        events.append(("start", self.app.keys.address, self.beat_owner))
        self.app.notify_incoming(pal, "hello there", "text")
        self.app.notify_incoming(muted, "shh", "text")

    def join(self, timeout=None):
        events.append(("stopped",))


notified = []
M.SyncWorker = FakeWorker
M.android_notify = lambda peer, title, text: notified.append((title, text))
script = iter([True, False, False, True])        # app heartbeat per cycle
M.heartbeat_fresh = lambda owner: next(script)
M.IS_ANDROID = True          # the service only ever runs there
try:
    M.run_background_service(max_cycles=4, tick=0)
finally:
    M.IS_ANDROID = False
starts = [e for e in events if e[0] == "start"]
check("idles while the app's own worker is alive",
      events and events[0][0] == "start" and len(starts) == 1)
check("takes over (as 'service') once the app's heartbeat goes stale",
      starts and starts[0][1] == me and starts[0][2] == "service")
check("raises a notification for a new message", ("pal", "hello there")
      in notified)
check("...but not for a muted chat", all(t != "quiet" for t, _ in notified))
check("stands down when the app comes back", ("stopped",) in events)

events.clear()
M.save_pref("background_delivery", False)
script = iter([False, False])
M.run_background_service(max_cycles=2, tick=0)
check("does nothing when background delivery is switched off",
      not [e for e in events if e[0] == "start"])
M.save_pref("background_delivery", True)

print("\nHeartbeats")
importlib.reload  # (real heartbeat functions, not the scripted one)
spec2 = importlib.util.spec_from_file_location("main2", MAIN)
M2 = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(M2)
check("no heartbeat means not alive", not M2.heartbeat_fresh("app"))
M2.write_heartbeat("app")
check("a fresh heartbeat means alive", M2.heartbeat_fresh("app"))
os.utime(M2._beat_path("app"))
(M2._beat_path("app")).write_text("1")
check("a stale heartbeat means gone", not M2.heartbeat_fresh("app"))

print("\nBoth services start from main.py, as Android starts them")


def service(name, argument="", stderr=subprocess.PIPE):
    """main.py run as python-for-android runs a service: its name in
    PYTHON_NAME, a home of its own, every import it makes listed (on
    `stderr`, with what it logs)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("KIVY_")}
    home = tempfile.mkdtemp(prefix="fext-svc-")
    env.update(PYTHON_NAME=name, PYTHON_SERVICE_ARGUMENT=argument,
               HOME=home, USERPROFILE=home)      # Path.home() on Windows
    return subprocess.Popen([sys.executable, "-X", "importtime", str(MAIN)],
                            env=env, stdout=subprocess.DEVNULL,
                            stderr=stderr)


flag = Path(tempfile.mkdtemp(prefix="fext-call-")) / ".call_active"
started = time.time()
_out, err = service("Fextcall", str(flag)).communicate(timeout=120)
took = time.time() - started
check("the call service ends at once with no flag file, having imported "
      "nothing of the app's (no Kivy, no requests)",
      b"Traceback" not in err and b"kivy" not in err and
      b"requests" not in err, err[-300:])
flag.write_text("1")
call = service("Fextcall", str(flag))
time.sleep(took + 3)
waited = call.poll() is None
flag.unlink()
try:
    _out, err = call.communicate(timeout=10)
except subprocess.TimeoutExpired:
    call.kill()
    _out, err = call.communicate()
check("...and waits while its flag file is there, ending when it goes",
      waited and call.returncode == 0, err[-300:])
# Until it says it started, then a few seconds of its loop: not a fixed
# 8 s, which a first start on Windows' runner needs more than (run 7).
log = Path(tempfile.mkdtemp(prefix="fext-sync-")) / "stderr.txt"
with open(log, "wb") as sink:
    sync = service("Fextsync", stderr=sink)
    for _ in range(120):                         # a minute at the most
        if sync.poll() is not None or \
                b"background service: started" in log.read_bytes():
            break
        time.sleep(0.5)
    time.sleep(3)
    running = sync.poll() is None
    sync.terminate()
    sync.wait(timeout=10)
err = log.read_bytes()
check("the sync service runs its loop, windowless",
      running and b"background service: started" in err and
      b"Traceback" not in err, err[-300:])

print("\nManifest hook: service types (Android 14), camera FileProvider, "
      "notification receiver")
hook_spec = importlib.util.spec_from_file_location(
    "p4a_hook", ROOT / "tools" / "p4a_hook.py")
hook = importlib.util.module_from_spec(hook_spec)
hook_spec.loader.exec_module(hook)
work = Path(tempfile.mkdtemp(prefix="dist-"))
(work / "src" / "main").mkdir(parents=True)
manifest = work / "src" / "main" / "AndroidManifest.xml"
# What p4a v2024.01.21's template renders for `services = Fextsync:...,
# Fextcall:..., Fextshare:...`:
manifest.write_text('''<manifest><application>
        <activity android:name="org.kivy.android.PythonActivity"
                  android:label="@string/app_name" />
        <service android:name="org.kivy.android.PythonService"
                 android:process=":pythonservice" />
        <service android:name="org.fext.fext.ServiceFextsync"
                 android:process=":service_fextsync" />
        <service android:name="org.fext.fext.ServiceFextcall"
                 android:process=":service_fextcall" />
        <service android:name="org.fext.fext.ServiceFextshare"
                 android:process=":service_fextshare" />
</application></manifest>''')
cwd = os.getcwd()
os.chdir(work)
try:
    hook.before_apk_assemble(None)
    once = manifest.read_text()
    hook.before_apk_assemble(None)
    twice = manifest.read_text()
finally:
    os.chdir(cwd)
check("declares foregroundServiceType on OUR services only",
      once.count('foregroundServiceType="dataSync"') == 1 and
      'ServiceFextsync" android:foregroundServiceType="dataSync"' in once and
      'PythonService" android:foregroundServiceType' not in once)
check("...the call service as 'microphone', so a call keeps its microphone "
      "off screen",
      'ServiceFextcall" android:foregroundServiceType="microphone"' in once)
check("...(18.0) the share service as 'mediaProjection', so a phone's "
      "screen may be captured",
      'ServiceFextshare" android:foregroundServiceType="mediaProjection"'
      in once)
check("running the hook twice changes nothing", once == twice)
check("(17.2) the app's activity may shrink to picture in picture, for a "
      "video call over other apps",
      'PythonActivity" android:supportsPictureInPicture="true" '
      'android:resizeableActivity="true"' in once)
inside = once[once.index("<application>"):once.index("</application>")]
check("declares the camera's FileProvider once, inside <application>",
      once.count("androidx.core.content.FileProvider") == 1 and
      "androidx.core.content.FileProvider" in inside and
      'android:authorities="${applicationId}.fileprovider"' in inside and
      'android:exported="false"' in inside and
      'android:resource="@xml/fext_file_paths"' in inside)
paths = (ROOT / "android" / "fext_file_paths.xml").read_text()
spec_text = (ROOT / "buildozer.spec").read_text()
check("...whose one folder ships, with the class it names",
      '<cache-path name="camera" path="camera/" />' in paths and
      "android/fext_file_paths.xml:xml/fext_file_paths.xml" in spec_text and
      "androidx.core:core:" in spec_text)
perms = next(line for line in spec_text.splitlines()
             if line.startswith("android.permissions"))
services = next(line for line in spec_text.splitlines()
                if line.startswith("services"))
check("calls: the service, its permission, and the audio helper ship",
      "Fextcall:main.py:foreground" in services and
      "Fextsync:main.py:foreground:sticky" in services and
      all(p in perms for p in ("FOREGROUND_SERVICE_MICROPHONE",
                                "MODIFY_AUDIO_SETTINGS", "RECORD_AUDIO",
                                "WAKE_LOCK", "VIBRATE")) and
      "\nandroid.add_src = android\n" in spec_text and
      (ROOT / "android" / "CallAudio.java").is_file())
check("Android's own cloud backup does not copy the keys and chats",
      "\nandroid.allow_backup = False" in spec_text)
check("in dark mode the platform loading screen is black with the mark on "
      "its plate: values-night overrides presplash_color, drawable-night "
      "holds the picture, and both ship",
      '<string name="presplash_color">#000000</string>' in (
          ROOT / "android" / "strings_night.xml").read_text() and
      (ROOT / "android" / "presplash_night.png").is_file() and
      "android/strings_night.xml:values-night/strings.xml" in spec_text and
      "android/presplash_night.png:drawable-night/presplash.png"
      in spec_text and "\nandroid.presplash_color = #FFFFFF" in spec_text)
shipped = next(line for line in spec_text.splitlines()
               if line.startswith("android.add_resources")).split("=", 1)[1]
shipped = {pair.split(":")[0].strip() for pair in shipped.split(",")}
excluded = next(line for line in spec_text.splitlines()
                if line.startswith("source.exclude_dirs"))
check("android/: each file in it ships, as Java or as a resource, and "
      "none among the app's own files",
      all(f.suffix == ".java" or f"android/{f.name}" in shipped
          for f in (ROOT / "android").iterdir()) and
      all((ROOT / s).is_file() for s in shipped) and
      "android" in [d.strip() for d in excluded.split("=")[1].split(",")])
check("notifications: their receiver is declared once, not exported",
      once.count("org.fext.notify.NotifyReceiver") == 1 and
      "org.fext.notify.NotifyReceiver" in inside and
      inside[inside.index("org.fext.notify.NotifyReceiver"):].split(
          "/>")[0].count('android:exported="false"') == 1)
check("...with the classes that draw and answer them, and a call's "
      "full-screen permission",
      (ROOT / "android" / "Notifier.java").is_file() and
      (ROOT / "android" / "NotifyReceiver.java").is_file() and
      "USE_FULL_SCREEN_INTENT" in perms)
check("(15.1) the keep-running guide may ask Android to leave the battery "
      "alone", "REQUEST_IGNORE_BATTERY_OPTIMIZATIONS" in perms)

# 18.1: the sync service's notification is the app's own, and secret.
java = (ROOT / "android" / "Notifier.java").read_text("utf-8")
notice = java[java.index("public static void serviceNotice("):]
notice = notice[:notice.index("\n    }\n")]
check("the sync service's own notice: a channel and a notification that "
      "a locked phone does not show, silent, without a badge, put in the "
      "place of python-for-android's",
      notice.count("VISIBILITY_SECRET") == 2 and
      "IMPORTANCE_LOW" in notice and "setShowBadge(false)" in notice and
      "service.startForeground(SERVICE_NOTICE" in notice, notice)


class FakeNotifier:
    SERVICE_NOTICE = 7
    given = []

    @classmethod
    def serviceNotice(cls, service):
        cls.given.append(service)


class FakePythonService:
    mService = "the service"


real = M._notifier, M._jni, M.IS_ANDROID
M._notifier = lambda: FakeNotifier
M._jni = lambda: ((lambda name: FakePythonService), None)
try:
    took = M.android_service_notice()
    check("the service hands itself to the notifier for it",
          took and FakeNotifier.given == ["the service"], FakeNotifier.given)
    M.IS_ANDROID = True
    M.run_background_service(max_cycles=0)
    check("...as it starts, before anything else",
          FakeNotifier.given == ["the service"] * 2, FakeNotifier.given)
    M._notifier = lambda: None
    check("an APK built without the notifier keeps the notification it "
          "has", M.android_service_notice() is False)
finally:
    M._notifier, M._jni, M.IS_ANDROID = real

# ...and the emulators' self-test asks Android what is shown.
said, started, shown = [], [], []
real_sdk, M._SDK[:] = list(M._SDK), [34]


def say(*words):
    said.append(words)
    return words[1]


def notices():
    """python-for-android's notice (its id 1) first; the app's own after."""
    shown.append(1)
    return ([], [1]) if len(shown) < 3 else ([M.NOTICE_SECRET], [])


try:
    verdict = M._selftest_service_notice(say, 5.0, started.append, notices)
    check("the self-test starts the service, waits until its own secret "
          "notice is the only one a service shows, and stops it",
          verdict == "PASS" and started == [True, False] and
          len(shown) == 3, (verdict, started, shown, said))
    shown.clear()
    verdict = M._selftest_service_notice(
        say, 1.0, started.append, lambda: ([0], []))
    check("...a notice a locked phone would show fails it",
          verdict == "FAIL" and "visibility [0]" in said[-1][2], said[-1])
    verdict = M._selftest_service_notice(
        say, 1.0, started.append, lambda: ([M.NOTICE_SECRET], [1]))
    check("...and so does python-for-android's notice still there",
          verdict == "FAIL", said[-1])
    M._SDK[:] = [21]
    check("...before Android 6, which cannot be asked, it is skipped",
          M._selftest_service_notice(say, 1.0, started.append,
                                     notices) == "SKIP")
finally:
    M._SDK[:] = real_sdk
manifest.write_text("<manifest><application></application></manifest>")
os.chdir(work)
try:
    hook.before_apk_assemble(None)
    failed = False
except RuntimeError:
    failed = True
finally:
    os.chdir(cwd)
check("fails the BUILD (not the phone) if the service is missing", failed)

passed = sum(1 for _n, c in RESULTS if c)
print(f"\n  {passed}/{len(RESULTS)} passed")
sys.exit(0 if passed == len(RESULTS) else 1)
