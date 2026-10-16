"""Build the single-file desktop release: main.py with its fonts and icons.

    python3 tools/bundle_desktop.py OUT.py            # write the release file
    python3 tools/bundle_desktop.py --verify OUT.py   # check one against data/

Kivy draws every label in one font, with no fallback, so the client needs
its own type to show emoji and its serif; and it needs the mark for its
window. A bare main.py has neither. This appends them to the code as
comment lines — a manifest, then base64 of one xz stream — which Python
ignores and the client unpacks into ~/.fext/assets on first start, checking
each file's SHA-256 on every start (see _unpack_bundled_assets in main.py).

Nothing is hidden by this: --verify unpacks a release file and compares it,
byte for byte, with main.py and data/ at the same commit. Packed together
the four styles of the serif share their emoji, so xz keeps the file small.
"""
import base64
import hashlib
import json
import lzma
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MARK = "# FERRITEXT-ASSETS "
#: What a computer needs from data/: the type with its licences, the mark
#: for the loading screen, and the window icons.
FILES = sorted(["fonts/" + p.name for p in (DATA / "fonts").iterdir()
                if p.suffix in (".ttf", ".txt")]) + [
    "ferritext_logo.png", "icon_desktop.png", "icon_desktop.ico"]

HEADER = """
# =============================================================================
#  The fonts and icons of this desktop release (unpacked on first start into
#  ~/.fext/assets, see _unpack_bundled_assets): Ferritext Serif — EB Garamond
#  with Noto Emoji, coloured with Twemoji's graphics (CC-BY 4.0) — and
#  Titillium Web, under the SIL Open Font License (the licence texts are
#  included), and the Ferritext mark. Built from data/ by
#  tools/bundle_desktop.py; check this file against the source with
#      python3 tools/bundle_desktop.py --verify <this file>
# =============================================================================
"""


def payload() -> tuple:
    blobs = [(DATA / rel).read_bytes() for rel in FILES]
    files = [[rel, len(b), hashlib.sha256(b).hexdigest()]
             for rel, b in zip(FILES, blobs)]
    digest = hashlib.sha256(json.dumps(files).encode()).hexdigest()
    return {"digest": digest, "files": files}, b"".join(blobs)


def build(out: Path) -> None:
    code = (ROOT / "main.py").read_text("utf-8")
    manifest, data = payload()
    packed = base64.b64encode(lzma.compress(
        data, preset=9 | lzma.PRESET_EXTREME)).decode("ascii")
    lines = [MARK + json.dumps(manifest, separators=(",", ":"))]
    lines += ["# " + packed[i:i + 76] for i in range(0, len(packed), 76)]
    out.write_text(code.rstrip("\n") + "\n" + HEADER + "\n".join(lines)
                   + "\n", "utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB: {len(FILES)} "
          f"files, {len(data) // 1024} KB unpacked)")


def verify(path: Path) -> bool:
    text = path.read_text("utf-8")
    at = text.rfind("\n" + MARK)
    if at < 0:
        print("no bundled assets in", path)
        return False
    ok = True
    code = text[:text.rfind(HEADER, 0, at + 1)] if HEADER in text else ""
    if code != (ROOT / "main.py").read_text("utf-8").rstrip("\n") + "\n":
        print("the code differs from main.py")
        ok = False
    lines = text[at + 1:].splitlines()
    try:
        manifest = json.loads(lines[0][len(MARK):])
        data = lzma.decompress(base64.b64decode("".join(
            line[2:] for line in lines[1:] if line.startswith("# "))))
    except (ValueError, lzma.LZMAError) as exc:
        print(f"the bundled data is damaged: {exc}")
        return False
    pos = 0
    for rel, size, sha in manifest["files"]:
        chunk, pos = data[pos:pos + size], pos + size
        same = hashlib.sha256(chunk).hexdigest() == sha and \
            (DATA / rel).is_file() and (DATA / rel).read_bytes() == chunk
        print(f"  {'ok  ' if same else 'DIFF'} {rel}")
        ok = ok and same
    if pos != len(data):
        print("trailing bytes after the last file")
        ok = False
    return ok


def main(argv: list) -> int:
    if len(argv) == 2 and argv[0] == "--verify":
        good = verify(Path(argv[1]))
        print("matches main.py and data/" if good else "DOES NOT MATCH")
        return 0 if good else 1
    if len(argv) == 1:
        build(Path(argv[0]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
