#!/usr/bin/env python3
"""Build the Ferritext desktop client into an executable with Nuitka.

    python desktop/build_desktop.py             a folder  (dist/ferritext.dist/)
    python desktop/build_desktop.py --onefile   one file  (dist/<name>)

On Windows, desktop\\build_exe.bat does all of it from a Command Prompt
(an environment, the packages, then this). CI builds the release the same
way (.github/workflows/build-apk.yml), so what you build is what it ships.

The desktop client IS ../main.py, the same single file the APK ships. It
detects the platform at import time (IS_MOBILE), so no desktop-specific
fork exists or is needed.

Prerequisites (in a venv, Python 3.13 as CI builds it):
    pip install -r desktop/requirements-exe.txt
the client's packages, the extras it carries on a computer (README,
Downloads) and Nuitka, at the versions CI builds with. Linux also needs a
C compiler and patchelf (and libmtdev1 to silence a harmless Kivy
touch-provider probe at startup); Windows, Visual Studio's C++ build
tools.

Check what you built: `<the executable> --check report.json` lists what
it can do on this computer; desktop/smoke_exe.py holds it against plain
Python (the build must not have less).
"""
import argparse
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

#: What each adds on a computer is in the README; the app does without any
#: of them. They are imported inside functions, in try/except, where
#: Nuitka does not look: each is named here, and only when it is installed
#: (the build takes what it is given). (what must be installed, the flag)
#: A module, not its whole package, where that is enough: a package brings
#: its tests along. PyAV whole: its compiled modules import each other in
#: ways Nuitka cannot see. (Until 20.0 only `av` itself was named: `import
#: av` then failed in the EXE, which had no video, no screen sharing and
#: no remote control, and said nothing.)
EXTRAS = (("sounddevice", "--include-module=sounddevice"),
          ("numpy", "--include-module=numpy"),
          ("av", "--include-package=av"),
          ("pyzbar", "--include-module=pyzbar.pyzbar"),
          ("pyogg", "--include-module=pyogg"),
          ("coincurve", "--include-module=coincurve._cffi_backend"))


def version() -> str:
    spec = (ROOT / "buildozer.spec").read_text(encoding="utf-8")
    return re.search(r"^version\s*=\s*(\S+)", spec, re.M).group(1)


def nuitka_arguments(args, source: Path, ver: str) -> list:
    mode = ["--mode=standalone"]
    if args.onefile:
        # Unpacked once per version into the user's cache folder, then
        # reused: only the first start after an update waits for it.
        mode = ["--mode=onefile",
                "--onefile-tempdir-spec={CACHE_DIR}/Ferritext/{VERSION}"]
    # The Ferritext mark on the executable itself (Explorer, a pinned
    # taskbar button, the Dock); the running window sets its own from
    # data/ as well. On Windows, no console window opens beside the app.
    platform = []
    if sys.platform == "win32":
        platform = [f"--windows-icon-from-ico={ROOT / 'data' / 'icon_desktop.ico'}",
                    "--windows-console-mode=disable"]
    elif sys.platform == "darwin":
        platform = [f"--macos-app-icon={ROOT / 'data' / 'icon_desktop.png'}"]
    extras = [flag for module, flag in EXTRAS
              if importlib.util.find_spec(module)]
    return [
        *mode,
        # certifi ships a data file (cacert.pem). The client calls
        # certifi.where() for BOTH https and the websocket's TLS, so without
        # this the app builds fine and then cannot open a secure connection.
        "--include-package-data=certifi",
        # The fonts (the serif carries the emoji), the mark and the window
        # icons, which main.py finds in data/ next to __file__. Nuitka
        # cannot see a path built at runtime: the folder is named here.
        f"--include-data-dir={ROOT / 'data'}=data",
        f"--include-data-dir={HERE / 'build' / 'licences'}=licences",
        *platform,
        # qrcode picks its image backend by dynamic import at call time.
        # Settings draws QR codes; miss this and that screen raises.
        "--include-package=qrcode",
        "--include-package=PIL",
        # ...but not Pillow's Tk viewer: the app has no Tk.
        "--nofollow-import-to=tkinter",
        # Nor multiprocessing, which the app never uses (its watcher and
        # service are the program itself, started with a flag): Python's
        # own concurrent.futures.process alone imports it, for a
        # ProcessPoolExecutor no one asks for. With it in the build, Nuitka
        # compiles main.py a second time (as __parents_main__), and two
        # compiles of 21.0's main.py at once ran the Windows compiler out
        # of memory (build run 167: "compiler is out of heap space").
        "--nofollow-import-to=multiprocessing",
        "--nofollow-import-to=concurrent.futures.process",
        # On Windows, Kivy imports each package in its kivy_deps namespace
        # by its bare name, which a compiled package does not answer to:
        # import kivy fails. They only put their DLL folders on the search
        # path; the build carries the DLLs beside the executable instead
        # (Nuitka's Kivy plugin SDL's and GLEW's, nuitka-package.config.yml
        # ANGLE's).
        "--nofollow-import-to=kivy_deps",
        # websocket-client is imported in a try/except (the client falls
        # back to HTTP polling without it). Nuitka honours that and would
        # silently drop it, turning instant delivery off in the build.
        "--include-package=websocket",
        "--include-package=requests",
        "--include-package=urllib3",
        "--include-package=cryptography",
        *extras,
        # PyOgg's Opus DLL (calls in Opus) and ANGLE's, which Nuitka does
        # not find by itself.
        f"--user-package-configuration-file={HERE / 'nuitka-package.config.yml'}",
        f"--output-dir={args.out}",
        f"--output-filename={args.name}",
        "--company-name=Ferritext",
        "--product-name=Ferritext",
        f"--file-version={ver}",
        f"--product-version={ver}",
        "--file-description=Ferritext end-to-end encrypted messenger",
        "--assume-yes-for-downloads",
        "--remove-output",
        str(source),
    ]


def main() -> int:
    ver = version()
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--onefile", action="store_true",
                        help="one executable, not a folder")
    parser.add_argument("--name", default=f"Ferritext-{ver}-windows.exe"
                        if sys.platform == "win32" else "fext",
                        help="the executable's file name")
    parser.add_argument("--out", type=Path, default=HERE / "dist",
                        help="where the build is left (default: desktop/dist)")
    parser.add_argument("--source", type=Path, default=ROOT / "main.py",
                        help="the client to build (default: ../main.py)")
    args = parser.parse_args()
    args.out = args.out.resolve()

    # Built from a copy under a fixed name without dots. Fixed: Nuitka's
    # build folders keep their paths from one version to the next, and its
    # cache of compiled C (kept by CI) stays valid. Without dots: a
    # one-file build on Windows can make a DLL named after the source, and
    # Nuitka's build tool refuses a name with a dot in it ("A shared
    # library should have exactly one target with the suffix: .dll"), as
    # a release's Ferritext-<version>-desktop.py has.
    source = HERE / "build" / "ferritext.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.source, source)

    # The licences of everything it carries, inside it as licences/.
    subprocess.run([sys.executable, str(HERE / "collect_licences.py"),
                    str(HERE / "build" / "licences")], check=True)
    return subprocess.run([sys.executable, "-m", "nuitka",
                           *nuitka_arguments(args, source, ver)]).returncode


if __name__ == "__main__":
    sys.exit(main())
