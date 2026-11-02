#!/usr/bin/env python3
"""Collect the licences of everything a desktop executable carries.

    python desktop/collect_licences.py OUT_DIR

Run with the build's Python (build_desktop.sh does, then puts OUT_DIR into
the executable as licences/). For each installed distribution, its licence
files, as its own metadata lists them, go to OUT_DIR/<name>/, and
OUT_DIR/README.txt lists them all with their versions and licences. What
the build environment holds is what the executable can carry, so all of it
is listed; only the packaging tools are left out.
"""
import importlib.metadata as md
import re
import shutil
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
SKIP = {"pip", "setuptools", "wheel"}
LICENCE_FILE = re.compile(r"(?:^|[._-])(licen[cs]e|copying|notice|copyright)"
                          r"(?:[._-]|$)", re.I)
#: The parts under the LGPL, which the LGPL lets you replace and asks us to
#: point to the source of.
LGPL = """\
FFmpeg (inside PyAV) and ZBar with libiconv (inside pyzbar) are under the
GNU Lesser General Public License. They are the DLLs in the av.libs and
pyzbar folders beside this one, which you may replace with your own
builds. Their source: https://ffmpeg.org/download.html (the release PyAV
names for its version, below), https://github.com/mchehab/zbar and
https://www.gnu.org/software/libiconv/.
"""


def licence_of(dist):
    meta = dist.metadata
    found = meta.get("License-Expression") or ""
    if not found:
        found = next((c.split("::")[-1].strip() for c in
                      meta.get_all("Classifier") or ()
                      if c.startswith("License ::")), "")
    if not found:
        found = (meta.get("License") or "").strip().splitlines()[0:1]
        found = found[0][:60] if found else "see its files"
    return found


if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
rows, seen = [], set()
for dist in sorted(md.distributions(),
                   key=lambda d: (d.metadata["Name"] or "").lower()):
    name = dist.metadata["Name"] or ""
    key = re.sub(r"[-_.]+", "-", name).lower()
    if not name or key in SKIP or key in seen:
        continue
    seen.add(key)
    copied = 0
    for file in dist.files or ():
        if LICENCE_FILE.search(Path(str(file)).name):
            source = Path(dist.locate_file(file))
            if source.is_file():
                target = OUT / key / Path(str(file)).name
                if target.exists():          # same name, another folder
                    target = OUT / key / "_".join(Path(str(file)).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                copied += 1
    rows.append(f"{name} {dist.version}: {licence_of(dist)}"
                f"{'' if copied else ' (no licence file of its own)'}")
(OUT / "README.txt").write_text(
    "Ferritext is made with the work of many people, freely shared. Each\n"
    "project's licence is in the folder of its name.\n\n" + LGPL + "\n" +
    "\n".join(rows) + "\n", encoding="utf-8")
print(f"{len(rows)} projects' licences in {OUT}")
