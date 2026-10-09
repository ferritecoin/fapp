#!/usr/bin/env python3
"""Background-service coordination and the Android 14 manifest hook.

    python3 desktop/test_service.py main.py

Runs WITHOUT a display, the way service.py runs on a phone: the
environment below is the one service.py sets, and importing main.py must
work under it. The service loop is driven for a scripted few cycles with
the network worker replaced by a recorder.
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

os.environ.update({"KIVY_WINDOW": "none", "KIVY_DPI": "160",
                   "KIVY_METRICS_DENSITY": "1", "KIVY_METRICS_FONTSCALE": "1",
                   "KIVY_NO_ARGS": "1", "KIVY_NO_CONSOLELOG": "1"})
os.environ.pop("DISPLAY", None)
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-svc-")
ROOT = Path(__file__).resolve().parent.parent

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""))


spec = importlib.util.spec_from_file_location(
    "main", sys.argv[1] if len(sys.argv) > 1 else "main.py")
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
    """Records lifecycle; on start, delivers two 'incoming' events."""

    def __init__(self, app):
        import threading
        self.app, self.stop_event = app, threading.Event()
        self.beat_owner = "app"

    def start(self):
        events.append(("start", self.app.keys.address, self.beat_owner))
        self.app.ui_queue.put(("incoming", pal, "hello there", "text"))
        self.app.ui_queue.put(("incoming", muted, "shh", "text"))

    def join(self, timeout=None):
        events.append(("stopped",))


notified = []
M.SyncWorker = FakeWorker
M.android_notify = lambda peer, title, text: notified.append((title, text))
script = iter([True, False, False, True])        # app heartbeat per cycle
M.heartbeat_fresh = lambda owner: next(script)
M.run_background_service(max_cycles=4, tick=0)
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
spec2 = importlib.util.spec_from_file_location("main2", sys.argv[1]
                                               if len(sys.argv) > 1
                                               else "main.py")
M2 = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(M2)
check("no heartbeat means not alive", not M2.heartbeat_fresh("app"))
M2.write_heartbeat("app")
check("a fresh heartbeat means alive", M2.heartbeat_fresh("app"))
os.utime(M2._beat_path("app"))
(M2._beat_path("app")).write_text("1")
check("a stale heartbeat means gone", not M2.heartbeat_fresh("app"))

print("\nManifest hook: service type (Android 14), camera FileProvider")
hook_spec = importlib.util.spec_from_file_location(
    "p4a_hook", ROOT / "tools" / "p4a_hook.py")
hook = importlib.util.module_from_spec(hook_spec)
hook_spec.loader.exec_module(hook)
work = Path(tempfile.mkdtemp(prefix="dist-"))
(work / "src" / "main").mkdir(parents=True)
manifest = work / "src" / "main" / "AndroidManifest.xml"
# What p4a v2024.01.21's template renders for `services = Fextsync:...`:
manifest.write_text('''<manifest><application>
        <service android:name="org.kivy.android.PythonService"
                 android:process=":pythonservice" />
        <service android:name="org.fext.fext.ServiceFextsync"
                 android:process=":service_fextsync" />
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
check("declares foregroundServiceType on OUR service only",
      once.count('foregroundServiceType="dataSync"') == 1 and
      'ServiceFextsync" android:foregroundServiceType' in once)
check("running the hook twice changes nothing", once == twice)
inside = once[once.index("<application>"):once.index("</application>")]
check("declares the camera's FileProvider once, inside <application>",
      once.count("androidx.core.content.FileProvider") == 1 and
      "androidx.core.content.FileProvider" in inside and
      'android:authorities="${applicationId}.fileprovider"' in inside and
      'android:exported="false"' in inside and
      'android:resource="@xml/fext_file_paths"' in inside)
paths = (ROOT / "src" / "android" / "res" / "xml" /
         "fext_file_paths.xml").read_text()
spec_text = (ROOT / "buildozer.spec").read_text()
check("...whose one folder ships, with the class it names",
      '<cache-path name="camera" path="camera/" />' in paths and
      "xml/fext_file_paths.xml" in spec_text and
      "androidx.core:core:" in spec_text)
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
