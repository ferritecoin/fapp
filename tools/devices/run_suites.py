"""Every client suite, one after another, as the device workflow runs them
on Windows and Ubuntu (17.2, .github/workflows/devices.yml): under a
virtual display (xvfb-run) on Linux, on the desktop itself on Windows.
Each suite's log goes to OUT_DIR; a line per suite and the total are
printed, and the exit status is 1 if any suite failed. A suite that fails
runs once more, and its line says so ("ok on a second run"): the call
suites' real-time checks can miss on a busy runner, and a fault of the
system fails both runs. Under a failed suite's line come its failed checks,
or the end of its log if it stopped before them, so that the job's own log
says what failed when its logs cannot be kept; then the lines before
its first few failures.

    python tools/devices/run_suites.py main.py OUT_DIR [suite ...]
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SUITES = ("test_service", "test_core", "test_flows", "test_design",
          "test_composer", "test_bundle", "smoke_desktop", "smoke_chat",
          "test_navigation", "test_pictures", "test_calls", "test_remote",
          "test_conference", "test_notify", "test_gestures", "test_touch",
          "test_privacy", "test_screen", "test_backup", "test_video",
          "test_videomsg", "playthrough")
#: Suites that open no window (as build-apk.yml runs them).
NO_WINDOW = {"test_service", "test_backup"}
PASSED = re.compile(r"(\d+)/(\d+) passed")
#: Under a failed suite's line, the log lines before its first few failed
#: checks too: the job's log is all a run leaves where its files cannot be
#: fetched.
CONTEXT, CONTEXT_FAILS = 8, 3


def run(suite: str, main_py: str, out: Path, attempt: int = 1) -> tuple:
    """(passed, checks, seconds, exit status, what failed) of one suite."""
    command = [sys.executable, "-u", f"desktop/{suite}.py", main_py]
    if sys.platform.startswith("linux") and suite not in NO_WINDOW \
            and shutil.which("xvfb-run"):
        command = ["xvfb-run", "-a"] + command
    start = time.monotonic()
    log_path = out / (f"{suite}.log" if attempt == 1 else f"{suite}.{attempt}.log")
    with open(log_path, "w", encoding="utf-8") as log:
        try:
            # UTF-8 out, on Windows too: its default (cp1252) cannot print
            # every check's name ("→"), and the log is read as UTF-8.
            status = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=1500, env=dict(
                                        os.environ, PYTHONUNBUFFERED="1",
                                        PYTHONIOENCODING="utf-8")
                                    ).returncode
        except subprocess.TimeoutExpired:
            status = "timeout"
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    found = PASSED.findall("\n".join(lines))
    passed, checks = map(int, found[-1]) if found else (0, 0)
    what = [line.strip() for line in lines if "[FAIL]" in line]
    if not found or not what:      # it stopped early: how, from its end
        what += lines[-15:]
    else:                          # what led to the first failures
        fails = [n for n, line in enumerate(lines) if "[FAIL]" in line]
        for n in fails[:CONTEXT_FAILS]:
            what += ["  before: " + line.strip()[:240]
                     for line in lines[max(0, n - CONTEXT):n]]
    return passed, checks, round(time.monotonic() - start), status, what


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows: cp1252
    main_py, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    chosen = sys.argv[3:] or SUITES
    failed_suites, total, all_checks = [], 0, 0
    with open(out / "summary.txt", "w", encoding="utf-8") as summary:
        for suite in chosen:
            passed, checks, seconds, status, what = run(suite, main_py, out)
            ok, note, told = status == 0 and checks and passed == checks, "", []
            if not ok:
                note, told = f"  (first run {passed}/{checks}, exit {status})", what
                passed, checks, seconds, status, what = run(suite, main_py, out, 2)
                ok = status == 0 and checks and passed == checks
                note = ("  ok on a second run" if ok else "  failed twice") + note
                told += [] if ok else [w for w in what if w not in told]
            line = (f"{suite:<16} {'ok  ' if ok else 'FAIL'} {passed}/{checks}"
                    f"  {seconds}s  exit {status}{note}")
            line += "".join(f"\n    {failure}" for failure in told)
            print(line, flush=True)
            summary.write(line + "\n")
            total, all_checks = total + passed, all_checks + checks
            if not ok:
                failed_suites.append(suite)
        line = (f"all {total}/{all_checks} passed; "
                f"failed: {', '.join(failed_suites) or 'none'}")
        print(line)
        summary.write(line + "\n")
    return 1 if failed_suites else 0


if __name__ == "__main__":
    sys.exit(main())
