#!/usr/bin/env python3
"""Start a built desktop executable as a person would, and check it.

    python desktop/smoke_exe.py dist/Ferritext-15.1.1-windows.exe [OUT_DIR]

The executable runs in a fresh, empty home folder, so nothing of yours is
touched. It must start, draw its window within a minute, and still be
running some seconds later; then it is closed. It starts twice: the first
start unpacks the executable, the second is the start people see every
day. The size and both start times are printed, and each start's
screenshot and Kivy log are saved in OUT_DIR (default: smoke_exe/), with
the errors the log holds, for a look (on Linux, Kivy's probes for xclip
and mtdev log some that do no harm), and what the executable itself wrote:
a start that fails before Kivy's log begins says why there.

"Drawn" is judged from screenshots, the screen before the start against
the screen after: something must have appeared (half a percent of the
screen), and what appeared must have detail, not be one flat colour, as a
window that failed to draw would be. Run it on a machine with a screen
(GitHub's Windows runners have one).
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import ImageChops, ImageGrab, ImageStat

EXE = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "smoke_exe").resolve()
OUT.mkdir(parents=True, exist_ok=True)
STARTED = "Start application main loop"     # Kivy logs it when it is up
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("  [PASS] " if cond else "  [FAIL] ") + name +
          (f" — {detail}" if detail and not cond else ""), flush=True)


def drawn(before, after):
    """(the share of the screen that changed, the spread of brightness in
    the part that did): a window drawn has both."""
    diff = ImageChops.difference(before.convert("RGB"), after.convert("RGB"))
    mask = diff.convert("L").point(lambda v: 255 if v > 24 else 0)
    box = mask.getbbox()
    if box is None:
        return 0.0, 0.0
    share = mask.histogram()[255] / (before.width * before.height)
    return share, ImageStat.Stat(after.convert("L").crop(box)).stddev[0]


def stop(proc):
    """End the executable and what it started (a one-file build runs the
    app as its child)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True)
    else:
        os.killpg(proc.pid, 9)
    proc.wait(30)


def fresh_home():
    """An empty home folder, shaped as a real one. On Windows a profile has
    AppData: a one-file build unpacks itself into AppData\\Local, and where
    the profile has none it stops at once, before any Python runs (exit
    code 0xC0000409)."""
    home = Path(tempfile.mkdtemp(prefix="fext-smoke-"))
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
               KIVY_HOME=str(home / ".kivy"))
    if os.name == "nt":
        for var, sub in (("LOCALAPPDATA", "Local"), ("APPDATA", "Roaming")):
            folder = home / "AppData" / sub
            folder.mkdir(parents=True)
            env[var] = str(folder)
    return home, env


def exit_code(code):
    """Windows' codes read best in hex (0xC0000409, not 3221226505)."""
    return hex(code) if code is not None and code > 255 else str(code)


def start(n, home, env):
    logs = home / ".kivy" / "logs"
    old = set(logs.glob("*.txt")) if logs.is_dir() else set()
    before = ImageGrab.grab()
    began = time.monotonic()
    said = OUT / f"start{n}.out.txt"         # what the executable writes
    with open(said, "wb") as out:
        proc = subprocess.Popen([str(EXE)], env=env, cwd=str(home),
                                stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=os.name != "nt")
    text, up = "", None

    def log_text():                        # this start's log, not the last's
        new = sorted(set(logs.glob("*.txt")) - old) if logs.is_dir() else []
        return new[-1].read_text("utf-8", "replace") if new else ""
    while time.monotonic() - began < 60 and proc.poll() is None:
        text = log_text()
        if STARTED in text:
            up = time.monotonic() - began
            break
        time.sleep(0.2)
    time.sleep(4)                         # the loading screen, then the app
    after = ImageGrab.grab()
    running = proc.poll() is None
    text = log_text() or text
    (OUT / f"start{n}.log").write_text(text, "utf-8")
    after.save(OUT / f"start{n}.png")
    if running:
        stop(proc)
        # Its window can outlast it a moment: wait for the screen to be as it
        # was, so that the next start is judged on what it draws itself.
        until = time.monotonic() + 10
        while time.monotonic() < until and \
                drawn(before, ImageGrab.grab())[0] >= 0.005:
            time.sleep(0.2)
    wrote = said.read_text("utf-8", "replace").strip()
    share, spread = drawn(before, after)
    label = "first start (unpacks)" if n == 1 else "second start"
    check(f"{label}: up in {up:.1f} s" if up else f"{label}: up",
          up is not None, f"exit code {exit_code(proc.returncode)}; it wrote: "
          f"{wrote[-600:] or 'nothing'}; log: {text[-600:] or 'none'}")
    check(f"{label}: its window drawn ({share:.1%} of the screen changed, "
          f"brightness spread {spread:.0f})", share >= 0.005 and spread >= 10,
          f"see {OUT / f'start{n}.png'}")
    check(f"{label}: still running after its first screen", running,
          f"exit code {exit_code(proc.returncode)}; log: {text[-800:]}")
    errors = [line for line in text.splitlines()
              if "[ERROR" in line or "[CRITICAL" in line]
    if errors:
        print("    errors it logged, for a look:\n      " +
              "\n      ".join(errors[:10]))
    return up


print(f"{EXE.name}: {EXE.stat().st_size / 1e6:.1f} MB")
home, env = fresh_home()
for n in (1, 2):
    start(n, home, env)
passed = sum(RESULTS)
print(f"\n  {passed}/{len(RESULTS)} passed")
sys.exit(0 if passed == len(RESULTS) else 1)
