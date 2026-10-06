#!/usr/bin/env python3
"""Build Ferritext's bundled fonts into data/fonts/.

    python3 -m pip install fonttools
    python3 tools/build_fonts.py            # downloads the pinned sources

Kivy draws a whole label with ONE font file and has no fallback, so a
message mixing letters and emoji needs both in the same file. This merges
EB Garamond (a serif with true italics) with Noto Emoji (monochrome, so emoji
take the text colour in both themes) into "Ferritext Serif", in four styles.
The capitals face, Titillium Web Light (data/fonts/ui.ttf), is shipped
unmodified.

All three are under the SIL Open Font License 1.1; their licence texts ship
beside the fonts. The merged family is renamed, as the OFL asks of modified
fonts, and keeps every original copyright notice.
"""
import hashlib
import io
import logging
import sys
import tempfile
import urllib.request
from pathlib import Path

from fontTools.merge import Merger
from fontTools.ttLib import TTFont
from fontTools.ttLib.scaleUpem import scale_upem
from fontTools.varLib import instancer

logging.getLogger("fontTools").setLevel(logging.ERROR)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "fonts"
BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
SOURCES = {
    "garamond": ("ebgaramond/EBGaramond%5Bwght%5D.ttf",
                 "ef9512f92f6d579e5dc75af59a5a4b1b8b47d2eda89e00b954d44520e5369027"),
    "garamond_italic": ("ebgaramond/EBGaramond-Italic%5Bwght%5D.ttf",
                        "bba2c4499c93c9612b90b9825d32b07da52fce2fe57562a1eb6b833553f93c4e"),
    "emoji": ("notoemoji/NotoEmoji%5Bwght%5D.ttf",
              "de6c18832938afc99caf132b39d6a30a19bac7f2e812e28db2535b4608d27551"),
}
LICENCES = {"OFL-EBGaramond.txt": "ebgaramond/OFL.txt",
            "OFL-NotoEmoji.txt": "notoemoji/OFL.txt",
            "OFL-TitilliumWeb.txt": "titilliumweb/OFL.txt"}
#: (output file, garamond source, weight, emoji weight, style name)
STYLES = [("FerritextSerif-Regular.ttf", "garamond", 400, 400, "Regular"),
          ("FerritextSerif-Italic.ttf", "garamond_italic", 400, 400, "Italic"),
          ("FerritextSerif-Bold.ttf", "garamond", 600, 600, "Bold"),
          ("FerritextSerif-BoldItalic.ttf", "garamond_italic", 600, 600,
           "Bold Italic")]
FAMILY = "Ferritext Serif"
#: Emoji are drawn at this fraction of the em: Garamond's small x-height
#: makes full-size emoji shout over the text around them.
EMOJI_SCALE = 0.86
#: Layout and variation tables of the emoji font that the merge cannot
#: reconcile and SDL_ttf (no HarfBuzz on Android) never reads.
EMOJI_DROP = ("GSUB", "GPOS", "GDEF", "STAT", "fvar", "gvar", "avar", "HVAR",
              "MVAR", "vhea", "vmtx", "meta", "DSIG")
#: Where both fonts have a glyph, the text font wins (digits, punctuation,
#: arrows, (c)) EXCEPT in these pictograph ranges: EB Garamond carries a few
#: stray pictographs of its own (a small text-style thumbs-up, for one), and
#: emoji must all come from one consistent set.
EMOJI_FIRST = ((0x2300, 0x23FF), (0x2600, 0x27BF), (0x2B00, 0x2BFF),
               (0x3030, 0x3030), (0x303D, 0x303D), (0x3297, 0x3299),
               (0x1F000, 0x1FAFF))


def fetch(path: str, sha: str = "") -> bytes:
    with urllib.request.urlopen(BASE + path, timeout=60) as resp:
        data = resp.read()
    if sha and hashlib.sha256(data).hexdigest() != sha:
        raise SystemExit(f"{path}: upstream file changed (sha256 mismatch); "
                         "review it and update SOURCES")
    return data


def instance(data: bytes, weight: int) -> TTFont:
    font = TTFont(io.BytesIO(data))
    return instancer.instantiateVariableFont(font, {"wght": weight},
                                             updateFontNames=False)


def build(garamond: bytes, emoji: bytes, weight: int, emoji_weight: int,
          style: str, out: Path, work: Path) -> None:
    text = instance(garamond, weight)
    for table in ("STAT",):
        if table in text:
            del text[table]
    metrics = (text["hhea"].ascent, text["hhea"].descent,
               text["OS/2"].sTypoAscender, text["OS/2"].sTypoDescender,
               text["OS/2"].sTypoLineGap, text["OS/2"].usWinAscent,
               text["OS/2"].usWinDescent)
    upm = text["head"].unitsPerEm
    marks = TTFont(io.BytesIO(emoji))
    marks = instancer.instantiateVariableFont(marks, {"wght": emoji_weight},
                                              updateFontNames=False)
    for table in EMOJI_DROP:
        if table in marks:
            del marks[table]
    scale_upem(marks, round(upm * EMOJI_SCALE))
    marks["head"].unitsPerEm = upm          # glyphs stay at EMOJI_SCALE
    emoji_cps = set(marks.getBestCmap())
    for sub in text["cmap"].tables:
        if not hasattr(sub, "cmap"):
            continue
        for cp in [cp for cp in sub.cmap if cp in emoji_cps and any(
                lo <= cp <= hi for lo, hi in EMOJI_FIRST)]:
            del sub.cmap[cp]
    copyrights = [text["name"].getDebugName(0), marks["name"].getDebugName(0)]
    a, b = work / f"text-{style}.ttf", work / f"emoji-{style}.ttf"
    text.save(a)
    marks.save(b)
    merged = Merger().merge([str(a), str(b)])   # first font wins on overlap
    hhea, os2 = merged["hhea"], merged["OS/2"]
    (hhea.ascent, hhea.descent, os2.sTypoAscender, os2.sTypoDescender,
     os2.sTypoLineGap, os2.usWinAscent, os2.usWinDescent) = metrics
    hhea.lineGap = 0
    os2.usWeightClass = weight
    bold, italic = "Bold" in style, "Italic" in style
    os2.fsSelection = (os2.fsSelection & ~0b1100001) | (1 if italic else 0) \
        | (0b100000 if bold else 0) | (0b1000000 if not (bold or italic) else 0)
    merged["head"].macStyle = (1 if bold else 0) | (2 if italic else 0)
    name = merged["name"]
    ps = f"FerritextSerif-{style.replace(' ', '')}"
    for rec_id, value in ((0, " / ".join(c for c in copyrights if c)),
                          (1, FAMILY), (2, style),
                          (3, f"{ps};{FAMILY} built by tools/build_fonts.py"),
                          (4, f"{FAMILY} {style}"), (6, ps),
                          (13, "This Font Software is licensed under the SIL "
                               "Open Font License, Version 1.1."),
                          (14, "https://openfontlicense.org")):
        name.setName(value, rec_id, 3, 1, 0x409)
    for rec_id in (16, 17, 21, 22, 25):
        name.removeNames(nameID=rec_id)
    merged.save(out)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    src = {key: fetch(path, sha) for key, (path, sha) in SOURCES.items()}
    with tempfile.TemporaryDirectory() as tmp:
        for out, source, weight, emoji_weight, style in STYLES:
            build(src[source], src["emoji"], weight, emoji_weight, style,
                  OUT / out, Path(tmp))
            print(f"{out:32} {(OUT / out).stat().st_size:>9,} B")
    for out, path in LICENCES.items():
        (OUT / out).write_bytes(fetch(path))
    if not (OUT / "ui.ttf").exists():
        sys.exit("data/fonts/ui.ttf (Titillium Web Light) is missing")


if __name__ == "__main__":
    main()
