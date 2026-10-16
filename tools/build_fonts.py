#!/usr/bin/env python3
"""Build Ferritext's bundled fonts into data/fonts/.

    python3 -m pip install fonttools brotli
    python3 tools/build_fonts.py            # downloads the pinned sources
    python3 tools/build_fonts.py --colour   # only the colour stage, on the
                                            # fonts already in data/fonts

Kivy draws a whole label with ONE font file and has no fallback, so a
message mixing letters and emoji needs both in the same file. This merges
EB Garamond (a serif with true italics) with Noto Emoji into "Ferritext
Serif", in four styles, then (v12) lays Twemoji's colour layers over the
emoji: a COLR/CPAL colour font, which FreeType draws in colour. Each Noto
Emoji outline stays as its colour glyph's base, so a renderer without colour
support draws the monochrome emoji as before. The capitals face, Titillium
Web Light (data/fonts/ui.ttf), is shipped unmodified.

EB Garamond, Noto Emoji and Titillium Web are under the SIL Open Font
License 1.1; Twemoji's graphics, by Twitter, Inc and other contributors,
under CC-BY 4.0. Their licence texts ship beside the fonts. The merged
family is renamed, as the OFL asks of modified fonts, and keeps every
original copyright notice.
"""
import hashlib
import io
import logging
import sys
import tarfile
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
#: v12: the colour layers. The npm package carries Twemoji built as a COLR
#: font (mozilla/twemoji-colr), as a WOFF2.
TWEMOJI = ("https://registry.npmjs.org/twemoji-colr-font/-/"
           "twemoji-colr-font-15.0.3.tgz",
           "10cb935907d7dc3d46e254d6ff54b3e042652eb08f6973c81cd2c4225e6ebac5")
TWEMOJI_LICENCE = """Twemoji
The emoji graphics: Twemoji, by Twitter, Inc and other contributors
(https://github.com/jdecked/twemoji), licensed under CC-BY 4.0:
https://creativecommons.org/licenses/by/4.0/
The font they come in: twemoji-colr-font 15.0.3
(https://github.com/mrdrogdrog/twemoji-color-font), under the SIL Open Font
License 1.1.

Changes: Ferritext Serif draws these graphics as the colour layers of its
emoji. They are scaled and placed to fit its own emoji outlines and set to
their width (tools/build_fonts.py). The package's licence follows as it came.

"""
#: Colour layer glyphs are named with this prefix (so a rebuild can find
#: and replace them).
LAYER_PREFIX = "twc."
#: The emoji the colour layers are fitted to: its monochrome outline and its
#: Twemoji face span the same box.
REFERENCE_EMOJI = 0x1F600

#: Where both fonts have a glyph, the text font wins (digits, punctuation,
#: arrows, (c)) EXCEPT in these pictograph ranges: EB Garamond carries a few
#: stray pictographs of its own (a small text-style thumbs-up, for one), and
#: emoji must all come from one consistent set.
EMOJI_FIRST = ((0x2300, 0x23FF), (0x2600, 0x27BF), (0x2B00, 0x2BFF),
               (0x3030, 0x3030), (0x303D, 0x303D), (0x3297, 0x3299),
               (0x1F000, 0x1FAFF))


def fetch(path: str, sha: str = "") -> bytes:
    url = path if path.startswith("https://") else BASE + path
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = resp.read()
    if sha and hashlib.sha256(data).hexdigest() != sha:
        raise SystemExit(f"{path}: upstream file changed (sha256 mismatch); "
                         "review it and update SOURCES")
    return data


def instance(data: bytes, weight: int) -> TTFont:
    font = TTFont(io.BytesIO(data))
    return instancer.instantiateVariableFont(font, {"wght": weight},
                                             updateFontNames=False)


def twemoji_licence(package: bytes) -> str:
    """What we changed, then the package's own licence text, unaltered."""
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as tar:
        text = tar.extractfile("package/LICENSE").read().decode("utf-8")
    return TWEMOJI_LICENCE + "=" * 72 + "\n\n" + text


def twemoji_font(package: bytes) -> TTFont:
    """The Twemoji COLR font from its npm package (a WOFF2)."""
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as tar:
        woff2 = tar.extractfile("package/twemoji.woff2").read()
    font = TTFont(io.BytesIO(woff2))
    font.flavor = None
    return font


def _bounds(glyphset, names) -> tuple:
    from fontTools.pens.boundsPen import BoundsPen
    pen = BoundsPen(glyphset)
    for name in names:
        glyphset[name].draw(pen)
    return pen.bounds


def _drop_sequences(font: TTFont) -> None:
    """Take out the ligature lookup (and its ccmp feature) a previous run
    added: the lookup whose ligatures all end in LAYER_PREFIX glyphs."""
    if "GSUB" not in font:
        return
    gsub = font["GSUB"].table
    lookups = gsub.LookupList.Lookup
    ours = [i for i, lookup in enumerate(lookups) if lookup.LookupType == 4
            and all(lig.LigGlyph.startswith(LAYER_PREFIX)
                    for sub in lookup.SubTable
                    for ligs in sub.ligatures.values() for lig in ligs)]
    for index in reversed(ours):
        records = gsub.FeatureList.FeatureRecord
        gone = [n for n, rec in enumerate(records)
                if rec.Feature.LookupListIndex == [index]]
        for n in reversed(gone):
            del records[n]
            for script in gsub.ScriptList.ScriptRecord:
                for langsys in [script.Script.DefaultLangSys] + [
                        r.LangSys for r in script.Script.LangSysRecord]:
                    if langsys is None:
                        continue
                    langsys.FeatureIndex = [
                        i - 1 if i > n else i
                        for i in langsys.FeatureIndex if i != n]
                    langsys.FeatureCount = len(langsys.FeatureIndex)
        gsub.FeatureList.FeatureCount = len(records)
        del lookups[index]
        for rec in gsub.FeatureList.FeatureRecord:
            rec.Feature.LookupListIndex = [
                i - 1 if i > index else i for i in rec.Feature.LookupListIndex]
    gsub.LookupList.LookupCount = len(lookups)


def _add_sequences(font: TTFont, mapping: dict) -> None:
    """One ligature lookup, under a ccmp feature in every script: a flag,
    a keycap, a skin tone or a family joined with zero-width joiners
    becomes one glyph where the text is shaped (SDL_ttf shapes with
    HarfBuzz)."""
    from fontTools.otlLib.builder import buildLigatureSubstSubtable, \
        buildLookup
    from fontTools.ttLib.tables import otTables
    gsub = font["GSUB"].table
    gsub.LookupList.Lookup.append(
        buildLookup([buildLigatureSubstSubtable(mapping)]))
    gsub.LookupList.LookupCount = len(gsub.LookupList.Lookup)
    record = otTables.FeatureRecord()
    record.FeatureTag = "ccmp"
    record.Feature = otTables.Feature()
    record.Feature.FeatureParams = None
    record.Feature.LookupListIndex = [len(gsub.LookupList.Lookup) - 1]
    record.Feature.LookupCount = 1
    records = gsub.FeatureList.FeatureRecord
    at = sum(1 for rec in records if rec.FeatureTag < "ccmp")
    records.insert(at, record)
    gsub.FeatureList.FeatureCount = len(records)
    for script in gsub.ScriptList.ScriptRecord:
        for langsys in [script.Script.DefaultLangSys] + [
                r.LangSys for r in script.Script.LangSysRecord]:
            if langsys is None:
                continue
            langsys.FeatureIndex = sorted(
                [i + 1 if i >= at else i for i in langsys.FeatureIndex]
                + [at])
            langsys.FeatureCount = len(langsys.FeatureIndex)
            if langsys.ReqFeatureIndex != 0xFFFF and \
                    langsys.ReqFeatureIndex >= at:
                langsys.ReqFeatureIndex += 1


def add_colour(font: TTFont, twemoji: TTFont) -> int:
    """Lay Twemoji's colour layers over `font`'s emoji (v12): a COLR v0
    table naming, for each emoji glyph, its layers and their palette
    colours. The emoji glyph itself is untouched and stays its base, so a
    renderer without colour support draws it as before. Layers are scaled
    and placed so the face of REFERENCE_EMOJI fills the box its outline
    fills; every emoji is then given that emoji's advance.

    Twemoji's sequences (flags, keycaps, skin tones, families) come too:
    each a new glyph reached by a ligature, whose own outline is its first
    part's (so without colour, 👍🏽 is still a 👍). Run again on its own
    output, it replaces what it added. Returns how many emoji are in
    colour."""
    from fontTools.colorLib.builder import buildCOLR
    from fontTools.pens.transformPen import TransformPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    import copy
    for table in ("COLR", "CPAL"):
        if table in font:
            del font[table]
    _drop_sequences(font)
    order = [g for g in font.getGlyphOrder() if not g.startswith(LAYER_PREFIX)]
    for name in set(font.getGlyphOrder()) - set(order):
        del font["glyf"].glyphs[name]
        del font["hmtx"].metrics[name]
    font.setGlyphOrder(order)
    cmap, tcmap = font.getBestCmap(), twemoji.getBestCmap()
    layers_of = twemoji["COLR"].ColorLayers
    glyphs, tglyphs = font.getGlyphSet(), twemoji.getGlyphSet()
    ref, tref = cmap[REFERENCE_EMOJI], tcmap[REFERENCE_EMOJI]
    x0, y0, x1, y1 = _bounds(glyphs, [ref])
    tx0, ty0, tx1, ty1 = _bounds(tglyphs, [r.name for r in layers_of[tref]])
    scale = (y1 - y0) / (ty1 - ty0)
    advance = font["hmtx"][ref][0]
    dx = (advance - twemoji["hmtx"][tref][0] * scale) / 2
    dy = y0 - ty0 * scale
    colr, made = {}, {}

    def layers(tbase: str) -> list:
        records = []
        for layer in layers_of[tbase]:
            name = made.get(layer.name)
            if name is None:
                name = LAYER_PREFIX + layer.name
                pen = TTGlyphPen(None)
                tglyphs[layer.name].draw(
                    TransformPen(pen, (scale, 0, 0, scale, dx, dy)))
                glyph = pen.glyph()
                font["glyf"].glyphs[name] = glyph
                glyph.recalcBounds(font["glyf"])
                font["hmtx"].metrics[name] = (advance,
                                              getattr(glyph, "xMin", 0))
                order.append(name)
                made[layer.name] = name
            records.append((name, layer.colorID))
        return records

    for cp, base in sorted(cmap.items()):
        tbase = tcmap.get(cp)
        if tbase not in layers_of or not any(
                lo <= cp <= hi for lo, hi in EMOJI_FIRST):
            continue
        colr[base] = layers(tbase)
        lsb = font["hmtx"][base][1]
        font["hmtx"].metrics[base] = (advance, lsb)
    singles = len(colr)
    # The sequences: Twemoji's one ligature lookup, its glyphs renamed to
    # this font's (through the characters they stand for).
    by_glyph = {name: cp for cp, name in tcmap.items()}
    mapping = {}
    for lookup in twemoji["GSUB"].table.LookupList.Lookup:
        if lookup.LookupType != 4:
            continue
        for sub in lookup.SubTable:
            for first, ligatures in sub.ligatures.items():
                for lig in ligatures:
                    parts = [first] + list(lig.Component)
                    try:
                        mine = tuple(cmap[by_glyph[p]] for p in parts)
                    except KeyError:
                        continue
                    if lig.LigGlyph not in layers_of or mine in mapping:
                        continue
                    name = LAYER_PREFIX + "seq." + lig.LigGlyph
                    pen = TTGlyphPen(glyphs)
                    pen.addComponent(mine[0], (1, 0, 0, 1, 0, 0))
                    glyph = pen.glyph()
                    font["glyf"].glyphs[name] = glyph
                    font["hmtx"].metrics[name] = (
                        advance, font["hmtx"][mine[0]][1])
                    order.append(name)
                    colr[name] = layers(lig.LigGlyph)
                    mapping[mine] = name
    font.setGlyphOrder(order)
    font["glyf"].glyphOrder = order
    for name in mapping.values():
        font["glyf"].glyphs[name].recalcBounds(font["glyf"])
    if mapping:
        _add_sequences(font, mapping)
    font["COLR"] = buildCOLR(colr, version=0)
    font["CPAL"] = copy.deepcopy(twemoji["CPAL"])
    return singles + len(mapping)


def build(garamond: bytes, emoji: bytes, weight: int, emoji_weight: int,
          style: str, out: Path, work: Path,
          twemoji: TTFont = None) -> None:
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
    if twemoji is not None:
        add_colour(merged, twemoji)
        name_colour(merged)
    merged.save(out)


def name_colour(font: TTFont) -> None:
    """Say in the font's own copyright line whose colour emoji it holds."""
    notice = (font["name"].getDebugName(0) or "").split(" / Twemoji")[0]
    font["name"].setName(
        notice + " / Twemoji colour emoji: Twitter, Inc and other "
        "contributors (CC-BY 4.0)", 0, 3, 1, 0x409)


def colour_only() -> None:
    """The colour stage alone, on the fonts already built in data/fonts."""
    package = fetch(*TWEMOJI)
    twemoji = twemoji_font(package)
    for out, *_rest in STYLES:
        # Keeps the fonts' own date, so running the stage again changes nothing.
        font = TTFont(OUT / out, recalcTimestamp=False)
        count = add_colour(font, twemoji)
        name_colour(font)
        font.save(OUT / out)
        print(f"{out:32} {(OUT / out).stat().st_size:>9,} B  "
              f"{count} emoji in colour")
    (OUT / "CC-BY-Twemoji.txt").write_text(twemoji_licence(package), "utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if "--colour" in sys.argv[1:]:
        colour_only()
        return
    src = {key: fetch(path, sha) for key, (path, sha) in SOURCES.items()}
    package = fetch(*TWEMOJI)
    twemoji = twemoji_font(package)
    with tempfile.TemporaryDirectory() as tmp:
        for out, source, weight, emoji_weight, style in STYLES:
            build(src[source], src["emoji"], weight, emoji_weight, style,
                  OUT / out, Path(tmp), twemoji)
            print(f"{out:32} {(OUT / out).stat().st_size:>9,} B")
    for out, path in LICENCES.items():
        (OUT / out).write_bytes(fetch(path))
    (OUT / "CC-BY-Twemoji.txt").write_text(twemoji_licence(package), "utf-8")
    if not (OUT / "ui.ttf").exists():
        sys.exit("data/fonts/ui.ttf (Titillium Web Light) is missing")


if __name__ == "__main__":
    main()
