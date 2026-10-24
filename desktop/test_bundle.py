#!/usr/bin/env python3
"""The release files: the single-file desktop client, and the APK's key.

    xvfb-run -a python3 desktop/test_bundle.py main.py

  * tools/bundle_desktop.py builds it, and --verify accepts it — and refuses
    one with a byte of code or of the packed data changed;
  * run from an empty folder (no data/ beside it), it unpacks its fonts and
    icons into ~/.fext/assets, makes the serif — which carries the emoji —
    Kivy's default font, registers the capitals, and finds its window icons;
  * a second start reuses what it unpacked; a damaged file is replaced;
  * main.py run from a checkout uses data/ and unpacks nothing;
  * tools/check_keystore.sh, which the release build runs first, accepts
    the key that FEXT_KEY_SHA256 names, written either way, and refuses
    any other (needs keytool; skipped without it).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CLIENT = Path(sys.argv[1] if len(sys.argv) > 1 else "main.py").resolve()
ROOT = CLIENT.parent
TOOL = ROOT / "tools" / "bundle_desktop.py"
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""))


PROBE = r"""
import importlib.util, json, os, sys
os.environ.update(KIVY_NO_ARGS="1", KIVY_NO_CONSOLELOG="1")
spec = importlib.util.spec_from_file_location("fext_bundled", sys.argv[1])
M = importlib.util.module_from_spec(spec); sys.modules["fext_bundled"] = M
spec.loader.exec_module(M)
from kivy.core.text import LabelBase
print("PROBE " + json.dumps({
    "assets": str(M.ASSET_DIR), "data": str(M.DATA_DIR),
    "serif": str((LabelBase._fonts.get("Roboto") or [""])[0]),
    "caps": M.CAPS_FONT_NAME, "icon": M.DESKTOP_ICON.is_file(),
    "ico": M.DESKTOP_ICO.is_file(), "logo": M.LOGO_FILE.is_file()}))
"""


def probe(path: Path, home: Path) -> dict:
    env = dict(os.environ, HOME=str(home), SDL_VIDEODRIVER="x11")
    out = subprocess.run([sys.executable, "-c", PROBE, str(path)], env=env,
                         capture_output=True, text=True, timeout=120)
    for line in out.stdout.splitlines():
        if line.startswith("PROBE "):
            return json.loads(line[6:])
    print(out.stdout[-2000:], out.stderr[-2000:])
    return {}


def tool(*args) -> int:
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)],
                          capture_output=True, text=True).returncode


work = Path(tempfile.mkdtemp(prefix="fext-bundle-"))
built = work / "Ferritext-desktop.py"

print("\nBuilding and verifying")
check("the release file builds", tool(built) == 0 and built.is_file())
check("--verify accepts it", tool("--verify", built) == 0)
text = built.read_text("utf-8")
bad_code = work / "bad_code.py"
bad_code.write_text(text.replace('APP_NAME = "Ferritext"',
                                 'APP_NAME = "Ferritekst"', 1), "utf-8")
check("...and refuses one whose code differs from main.py",
      tool("--verify", bad_code) == 1)
lines = text.splitlines(keepends=True)
middle = len(lines) - 200
lines[middle] = lines[middle][:10] + ("A" if lines[middle][10] != "A"
                                      else "B") + lines[middle][11:]
bad_data = work / "bad_data.py"
bad_data.write_text("".join(lines), "utf-8")
check("...or whose packed fonts were altered",
      tool("--verify", bad_data) == 1)

print("\nRun from an empty folder")
run_dir = work / "Downloads"
run_dir.mkdir()
alone = run_dir / "Ferritext-desktop.py"
shutil.copy(built, alone)
home = work / "home"
home.mkdir()
first = probe(alone, home)
assets = Path(first.get("assets", "/nonexistent"))
check("it unpacks its fonts and icons into ~/.fext/assets",
      assets.is_dir() and assets.parent == home / ".fext" / "assets",
      str(first))
check("the bundled serif (which carries the emoji) is Kivy's default font",
      first.get("serif", "").startswith(str(assets)) and
      first["serif"].endswith("FerritextSerif-Regular.ttf"))
check("...the capitals face is registered, and the icons are found",
      first.get("caps") == "FerritextCaps" and first.get("icon") and
      first.get("ico") and first.get("logo"))
data = ROOT / "data"
same = all((assets / rel).read_bytes() == (data / rel).read_bytes()
           for rel in ("fonts/FerritextSerif-Regular.ttf", "fonts/ui.ttf",
                       "fonts/OFL-EBGaramond.txt", "icon_desktop.ico"))
check("...each one byte for byte the file in data/", same)
stamps = {p: p.stat().st_mtime_ns for p in assets.rglob("*") if p.is_file()}
second = probe(alone, home)
check("a second start reuses them",
      second.get("assets") == str(assets) and stamps == {
          p: p.stat().st_mtime_ns for p in assets.rglob("*") if p.is_file()})
damaged = assets / "fonts" / "FerritextSerif-Italic.ttf"
damaged.write_bytes(b"\0" * 1024)
third = probe(alone, home)
good = hashlib.sha256((data / "fonts" / "FerritextSerif-Italic.ttf")
                      .read_bytes()).hexdigest()
check("a damaged file is replaced at the next start",
      third.get("assets") == str(assets) and
      hashlib.sha256(damaged.read_bytes()).hexdigest() == good)

print("\nFrom a checkout")
(work / "home2").mkdir()
plain = probe(CLIENT, work / "home2")
check("main.py beside data/ uses data/ and unpacks nothing",
      plain.get("assets") == str(data) and
      not (work / "home2" / ".fext" / "assets").exists(), str(plain))

print("\nThe signing key's check")
KEYTOOL = shutil.which("keytool")


def keystore(name: str) -> str:
    """A fresh PKCS12 key; returns its SHA-256 as keytool prints it."""
    ks = work / f"{name}.p12"
    subprocess.run([KEYTOOL, "-genkeypair", "-keystore", str(ks),
                    "-storetype", "PKCS12", "-alias", "fext", "-keyalg",
                    "RSA", "-keysize", "2048", "-validity", "10000",
                    "-storepass", "pw-test-1", "-dname", f"CN={name}"],
                   capture_output=True, check=True)
    info = subprocess.run([KEYTOOL, "-list", "-v", "-keystore", str(ks),
                           "-storepass", "pw-test-1", "-alias", "fext"],
                          capture_output=True, text=True).stdout
    return next(line.split("SHA256:", 1)[1].strip()
                for line in info.splitlines() if "SHA256:" in line)


def check_key(expected: str):
    """Run check_keystore on the 'project' key with FEXT_KEY_SHA256 set."""
    script = (f'. "{ROOT / "tools" / "check_keystore.sh"}"; '
              f'STOREPASS=pw-test-1; KEYPASS=pw-test-1; '
              f'check_keystore "{work / "project.p12"}" fext')
    out = subprocess.run(["bash", "-c", script], capture_output=True,
                         text=True, env=dict(os.environ,
                                             FEXT_KEY_SHA256=expected))
    return out.returncode, out.stdout + out.stderr


if KEYTOOL is None:
    print("  (skipped: no keytool here)")
else:
    ours, theirs = keystore("project"), keystore("another")
    rc, out = check_key("")
    check("without FEXT_KEY_SHA256 any working key is accepted", rc == 0, out)
    rc, out = check_key(ours)
    check("...with it, the key it names is accepted",
          rc == 0 and "the key FEXT_KEY_SHA256 names" in out, out)
    rc, out = check_key(ours.replace(":", "").lower())
    check("...written in lower case without colons too", rc == 0, out)
    rc, out = check_key(theirs)
    check("...and any other key stops the build, naming the variable",
          rc != 0 and "::error::" in out and "FEXT_KEY_SHA256" in out, out)

shutil.rmtree(work, ignore_errors=True)
passed = sum(1 for _n, c in RESULTS if c)
print(f"\n  {passed}/{len(RESULTS)} passed")
sys.exit(0 if passed == len(RESULTS) else 1)
