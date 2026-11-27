#!/usr/bin/env python3
"""21.0 (SERVICE29): a built Windows executable sets up its service, the
service runs, and the executable removes it again: what Settings › Remote
access › The lock screen, from afar asks Windows for, each step run as
administrator (GitHub's Windows runner's account is one; a person's
computer asks them first).

    python desktop/smoke_service.py dist/Ferritext-21.0.0-windows.exe

Set up: the program copied to Program Files; the service FerritextDesk
made to start with Windows, as the system, running that copy; and running
within half a minute. Removed: the service gone, and the copy with it.
desktop/test_winservice.py checks what the service then does (from the
Python file, in the Devices workflow). Only on GitHub's Windows runner:
it changes Windows.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

EXE = Path(sys.argv[1]).resolve()
NAME = "FerritextDesk"
FOLDER = Path(os.environ.get("ProgramFiles") or "C:\\Program Files") / \
    "Ferritext"
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("  [PASS] " if cond else "  [FAIL] ") + name +
          (f" — {detail}" if detail and not cond else ""), flush=True)


def run(flag: str) -> str:
    """The executable run with `flag`, as Settings runs it: '' when done,
    else why not (the file it writes its outcome to)."""
    out = Path(tempfile.mkdtemp(prefix="fext-service-")) / "result.json"
    try:
        code = subprocess.run([str(EXE), flag, str(out)],
                              timeout=180).returncode
    except subprocess.TimeoutExpired:     # each step takes seconds
        return "it did not end within 3 minutes"
    try:
        return json.loads(out.read_text("utf-8")).get("why", "")
    except (OSError, ValueError):
        return f"it wrote no outcome (exit {code})"


def sc(*words: str) -> str:
    return subprocess.run(["sc", *words], capture_output=True,
                          text=True).stdout


def state() -> str:
    """RUNNING, STOPPED…, or '' where there is no such service."""
    found = re.search(r"STATE\s*:\s*\d+\s+(\w+)", sc("query", NAME))
    return found.group(1) if found else ""


def main() -> int:
    if sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true":
        print("  (only on GitHub's Windows runner: it sets up a service)")
        return 0
    print(f"The service of {EXE.name}")
    why = run("--service-install")
    check("set up, as administrator", why == "", why)
    config = sc("qc", NAME)
    binary = re.search(r'BINARY_PATH_NAME\s*:\s*"([^"]+)"', config)
    program = Path(binary.group(1)) if binary else None
    check("...made to start with Windows, as the system",
          "AUTO_START" in config and "LocalSystem" in config, config)
    check("...running its copy in Program Files, where only "
          "administrators can change it",
          program is not None and program.exists() and
          str(program).lower().startswith(str(FOLDER).lower()),
          str(program))
    end = time.monotonic() + 30
    while state() != "RUNNING" and time.monotonic() < end:
        time.sleep(1)
    check("...and running", state() == "RUNNING", state())
    why = run("--service-remove")
    gone = lambda: state() == "" and not FOLDER.exists()     # noqa: E731
    end = time.monotonic() + 15        # Windows lets go of it in a moment
    while not gone() and time.monotonic() < end:
        time.sleep(1)
    check("removed", why == "" and state() == "", why or state())
    check("...and its copy with it", not FOLDER.exists(),
          sorted(p.name for p in FOLDER.iterdir()) if FOLDER.exists() else "")
    passed = sum(RESULTS)
    print(f"\n  {passed}/{len(RESULTS)} passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
