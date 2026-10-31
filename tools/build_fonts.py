#!/usr/bin/env python3
"""Build Ferritext's bundled fonts into data/fonts/.

    python3 -m pip install fonttools brotli uharfbuzz
    python3 tools/build_fonts.py            # downloads the pinned sources
    python3 tools/build_fonts.py mono sans  # only these families
    python3 tools/build_fonts.py --colour   # only the colour stage, on the
                                            # fonts already in data/fonts

Kivy draws a whole label with ONE font file and has no fallback, so a
message mixing letters and emoji needs both in the same file. This merges
each theme's text face with Noto Emoji (FAMILIES), then (v12) lays
Twemoji's colour layers over the emoji: a COLR/CPAL colour font, which
FreeType draws in colour. Each Noto Emoji outline stays as its colour
glyph's base, so a renderer without colour support draws the monochrome
emoji as before.

  Ferritext Serif  EB Garamond, four styles: Axiom, the app's own look
  Ferritext Mono   IBM Plex Mono, regular and bold: Overclock (15.1)
  Ferritext Sans   Open Sans, regular and bold: Ferrite (15.1)
  Ferritext CJK    Noto Sans SC and KR, regular: Chinese, Japanese and
                   Korean in every theme (15.1; see cjk_characters)
  Ferritext World  Noto Sans and its faces for 21 more scripts, regular:
                   every other script in everyday use (15.1; see WORLD)

Kivy does not slant or embolden a face itself, so a style a family lacks
is drawn upright or regular. The capitals faces, Titillium Web Light
(data/fonts/ui.ttf, as supplied) and Silkscreen (CAPS), are shipped
unmodified.

EB Garamond, IBM Plex Mono, Open Sans, Silkscreen, Noto Sans and its script
faces, Noto Sans SC and KR, Noto Emoji and Titillium Web are under the SIL
Open Font License 1.1; Twemoji's graphics, by
Twitter, Inc and other contributors, under CC-BY 4.0. Their licence texts
ship beside the fonts. The merged families are renamed, as the OFL asks of
modified fonts, and keep every original copyright notice.
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
    "plexmono": ("ibmplexmono/IBMPlexMono-Regular.ttf",
                 "6a3412f058c7d8dfd9170c41e85ade48e5156ecb89356110ca57a0a27734af46"),
    "plexmono_bold": ("ibmplexmono/IBMPlexMono-Bold.ttf",
                      "ac27abd6450a64dd94467580a02fe6235156d5b92f2926ebbc8e7489df64e0be"),
    "opensans": ("opensans/OpenSans%5Bwdth,wght%5D.ttf",
                 "36643644f318a812aab2d2ed3bb98f8cf0872527f835fe9398d95fe6b9adb878"),
    "notosanssc": ("notosanssc/NotoSansSC%5Bwght%5D.ttf",
                   "a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da"),
    "notosanskr": ("notosanskr/NotoSansKR%5Bwght%5D.ttf",
                   "194018e6b2b293a7964f037b25c0249ce1418bc9ab3c971060a03aa57861e252"),
}
#: v15.1: the scripts Unicode recommends for everyday use (UAX #31, table
#: 4) that no other face has, each in its Noto face (Tibetan's is a serif:
#: Noto has no sans), after Noto Sans for their Latin, Greek and Cyrillic.
#: Merged whole, shaping rules and all: SDL_ttf shapes with HarfBuzz once
#: told the script (shaping_script in main.py).
WORLD = {
    "notosans": ("notosans/NotoSans%5Bwdth,wght%5D.ttf",
                 "bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d"),
    "arabic": ("notosansarabic/NotoSansArabic%5Bwdth,wght%5D.ttf",
               "63111b5b2e074dd48cc67692e0a2726d86ee94c1c37fe8598257b7b4e87e869e"),
    "armenian": ("notosansarmenian/NotoSansArmenian%5Bwdth,wght%5D.ttf",
                 "0870908d8318435a5daf1cd280ae15063f990cd9ad60f3e94c734ce9e1ffef71"),
    "bengali": ("notosansbengali/NotoSansBengali%5Bwdth,wght%5D.ttf",
                "dcd42978094e584a849c84a51450eeac40c8826057d566ea6d4b9627a403a05a"),
    "devanagari": ("notosansdevanagari/NotoSansDevanagari%5Bwdth,wght%5D.ttf",
                   "14ec4af41f27482216d1c2229f417ff9b1425e1babb014e57d1d40d03229853e"),
    "ethiopic": ("notosansethiopic/NotoSansEthiopic%5Bwdth,wght%5D.ttf",
                 "0dbccc00b22d180ebd6a4bd8a733918a29e709fa6798023adc3e6cd40da65077"),
    "georgian": ("notosansgeorgian/NotoSansGeorgian%5Bwdth,wght%5D.ttf",
                 "dc591156f36842d38996c4a7a17fee9bb58e45da3e2cac7a31b7d33de700adb9"),
    "gujarati": ("notosansgujarati/NotoSansGujarati%5Bwdth,wght%5D.ttf",
                 "9901d8552f1dd5d2c50dbd4caa6f6e174e74e8264f06594ab259ae6e7b1ac428"),
    "gurmukhi": ("notosansgurmukhi/NotoSansGurmukhi%5Bwdth,wght%5D.ttf",
                 "1e6f728fa620e566f842d81e220265813faa12771214765d289c98e035adc5f2"),
    "hebrew": ("notosanshebrew/NotoSansHebrew%5Bwdth,wght%5D.ttf",
               "7ef36a2c3593758cdb622e1bdef4f84523e92fbc3ccc667438dd80ff54c2de88"),
    "kannada": ("notosanskannada/NotoSansKannada%5Bwdth,wght%5D.ttf",
                "cca4f3b3a8cb12fb261f1b43baf5d2f7f59d90fe123d41f0065ed3a183997ec9"),
    "khmer": ("notosanskhmer/NotoSansKhmer%5Bwdth,wght%5D.ttf",
              "f37a8431a0c5d5ed2f81a767417546aca576a81fb7eff9c924d46aecf828f2ca"),
    "lao": ("notosanslao/NotoSansLao%5Bwdth,wght%5D.ttf",
            "9608b94603a82d09a8038946f9775242f99e3b3459b7f1e4d5b335b578cd7ab3"),
    "malayalam": ("notosansmalayalam/NotoSansMalayalam%5Bwdth,wght%5D.ttf",
                  "312e0e7c3cc15fa09eb42a8f749eeb246b593ed420e3c81aafe8d910c3a6fb56"),
    "myanmar": ("notosansmyanmar/NotoSansMyanmar%5Bwdth,wght%5D.ttf",
                "7abbbfbe2514105d7ce94937aee3feb2ba89b73a256c8b77b5866bd9b83e32ec"),
    "oriya": ("notosansoriya/NotoSansOriya%5Bwdth,wght%5D.ttf",
              "910342275d619081b896b4ed25fd9ad6ff320c25591b810de1fcc4beb153ba82"),
    "sinhala": ("notosanssinhala/NotoSansSinhala%5Bwdth,wght%5D.ttf",
                "9bd93e407a278075be403324063bc94a7e306c44de4df81214e932330c22eecf"),
    "tamil": ("notosanstamil/NotoSansTamil%5Bwdth,wght%5D.ttf",
              "aa3a9b321f4b0bb2c40203ffbde9af89713227866e0e13f76e5b9eeea727cf88"),
    "telugu": ("notosanstelugu/NotoSansTelugu%5Bwdth,wght%5D.ttf",
               "e618af7bf999df192ed4f388eba2e563f2b5015034e9cbb317b5bd793bd7334d"),
    "thaana": ("notosansthaana/NotoSansThaana%5Bwght%5D.ttf",
               "a296795c892a9ec2b0f4d3af315568506ef9fd55920386170992d3935d8866ca"),
    "thai": ("notosansthai/NotoSansThai%5Bwdth,wght%5D.ttf",
             "5a1c559bb539583c8a1fd99d1c5b9491e5e14478c9cd2bd0970d5c3096cc9ef8"),
    "tibetan": ("notoseriftibetan/NotoSerifTibetan%5Bwght%5D.ttf",
                "060ec022b04c306de3f58d051fb0e1cf81a5b610c5910fbfc43bba154c057cda"),
}
SOURCES.update(WORLD)
#: Capitals faces, shipped as they come: Overclock's spaced capitals.
CAPS = {"Silkscreen-Regular.ttf": (
    "silkscreen/Silkscreen-Regular.ttf",
    "c845473330b94c2079ce9af01c51ac8ba2d99c24f4d14c039843bbb8e642ebd8")}
LICENCES = {"OFL-EBGaramond.txt": "ebgaramond/OFL.txt",
            "OFL-NotoEmoji.txt": "notoemoji/OFL.txt",
            "OFL-TitilliumWeb.txt": "titilliumweb/OFL.txt",
            "OFL-IBMPlexMono.txt": "ibmplexmono/OFL.txt",
            "OFL-OpenSans.txt": "opensans/OFL.txt",
            "OFL-Silkscreen.txt": "silkscreen/OFL.txt",
            "OFL-NotoSansCJK.txt": "notosanssc/OFL.txt"}
#: family name: (file prefix, emoji scale, styles). The emoji scale is the
#: fraction of the em emoji are drawn at: Garamond's small x-height makes
#: full-size emoji shout over the text around them. Each style is (style
#: name, source, weight, emoji weight).
FAMILIES = {
    "Ferritext Serif": ("FerritextSerif", 0.86, [
        ("Regular", "garamond", 400, 400),
        ("Italic", "garamond_italic", 400, 400),
        ("Bold", "garamond", 600, 600),
        ("Bold Italic", "garamond_italic", 600, 600)]),
    "Ferritext Mono": ("FerritextMono", 0.92, [
        ("Regular", "plexmono", 400, 400),
        ("Bold", "plexmono_bold", 700, 600)]),
    "Ferritext Sans": ("FerritextSans", 0.92, [
        ("Regular", "opensans", 400, 400),
        ("Bold", "opensans", 700, 600)]),
    "Ferritext CJK": ("FerritextCJK", 0.92, [
        ("Regular", "cjk", 400, 400)]),
    "Ferritext World": ("FerritextWorld", 0.92, [
        ("Regular", "world", 400, 400)]),
}
#: Sources made from others: the CJK face (cjk_face) and the face for
#: every other script (world_face).
DERIVED = {"cjk": ("notosanssc", "notosanskr"), "world": tuple(WORLD)}
#: The licence file of the face for every other script (world_licence).
WORLD_LICENCE = "OFL-NotoSansWorld.txt"
#: Layout and variation tables of the emoji font that the merge cannot
#: reconcile; the text face's own are kept, and add_colour() adds the
#: emoji's sequences.
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
their width, and their colours are made a little quieter (each colour's
chroma reduced, its lightness and hue kept) (tools/build_fonts.py). The
package's licence follows as it came.

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
    """A variable font at `weight`, every other axis at its default; a
    static font as it is."""
    font = TTFont(io.BytesIO(data))
    if "fvar" not in font:
        return font
    axes = {axis.axisTag: axis.defaultValue for axis in font["fvar"].axes}
    axes["wght"] = weight
    return instancer.instantiateVariableFont(font, axes,
                                             updateFontNames=False)


def cjk_characters() -> tuple:
    """(everything but Hangul, Hangul): the characters of everyday Chinese,
    Japanese and Korean. GB2312 (Simplified Chinese), Big5's first level
    (Traditional), JIS X 0208 (Japanese) and KS X 1001 (Korean: Hangul and
    Hanja), each with its punctuation, kana, Greek, Cyrillic and full-width
    forms; with ASCII, Latin-1 and the punctuation and currency signs, so
    that a line of mixed text stays in one face. About 13,500 in all."""
    def charset(codec: str, lead: range, trail: range) -> set:
        out = set()
        for a in lead:
            for b in trail:
                try:
                    out.update(bytes([a, b]).decode(codec))
                except UnicodeDecodeError:
                    pass
        return {ord(c) for c in out}
    chars = (charset("gb2312", range(0xA1, 0xF8), range(0xA1, 0xFF))
             | charset("big5", range(0xA1, 0xC7), range(0x40, 0xFF))
             | charset("euc_jp", range(0xA1, 0xF5), range(0xA1, 0xFF))
             | charset("euc_kr", range(0xA1, 0xFE), range(0xA1, 0xFF))
             | set(range(0x20, 0x7F)) | set(range(0xA0, 0x100))
             | set(range(0x2000, 0x2070)) | set(range(0x20A0, 0x20C1))
             | set(range(0x3000, 0x3100)) | set(range(0xFF00, 0xFFF0)))
    hangul = {c for c in chars if 0xAC00 <= c <= 0xD7A3}
    return chars - hangul, hangul


def cjk_face(sc: bytes, kr: bytes, *, weight: int, work: Path) -> bytes:
    """Noto Sans SC cut to cjk_characters(), with Noto Sans KR's Hangul, as
    one static face at `weight`."""
    from fontTools import subset
    han, hangul = cjk_characters()
    parts = []
    for data, chars, keep_layout in ((sc, han, True), (kr, hangul, False)):
        font = instance(data, weight)
        options = subset.Options()
        options.layout_features = ["*"] if keep_layout else []
        options.name_IDs = ["*"]
        options.notdef_outline = True
        cutter = subset.Subsetter(options)
        cutter.populate(unicodes=chars)
        cutter.subset(font)
        for table in ("vhea", "vmtx", "VORG", "STAT", "DSIG") + (
                () if keep_layout else ("GSUB", "GPOS", "GDEF")):
            if table in font:
                del font[table]
        path = work / f"cjk-part{len(parts)}.ttf"
        font.save(path)
        parts.append(str(path))
    merged = Merger().merge(parts)              # SC first: its Latin wins
    out = io.BytesIO()
    merged.save(out)
    return out.getvalue()


def world_face(*sources: bytes, weight: int, work: Path) -> bytes:
    """Noto Sans and the WORLD script faces as one static face at
    `weight`, Noto Sans first so that its Latin and its line height are
    the ones kept: SDL_ttf makes a line taller for a script whose marks
    stack higher, rather than every line. Their layout tables together
    outgrow 16-bit offsets: fontTools repacks them with HarfBuzz's
    repacker, from the uharfbuzz package."""
    try:
        import uharfbuzz  # noqa: F401
    except ImportError:
        raise SystemExit("the World face needs uharfbuzz: "
                         "python3 -m pip install uharfbuzz")
    parts, notices = [], []
    for data in sources:
        font = instance(data, weight)
        for table in ("STAT", "DSIG", "vhea", "vmtx", "VORG", "BASE",
                      "meta"):
            if table in font:
                del font[table]
        notices.append(font["name"].getDebugName(0))
        path = work / f"world-part{len(parts)}.ttf"
        font.save(path)
        parts.append(str(path))
    merged = Merger().merge(parts)
    first = TTFont(parts[0], lazy=True)
    for table, fields in (("hhea", ("ascent", "descent", "lineGap")),
                          ("OS/2", ("sTypoAscender", "sTypoDescender",
                                    "sTypoLineGap", "usWinAscent",
                                    "usWinDescent"))):
        for field in fields:
            setattr(merged[table], field, getattr(first[table], field))
    merged["name"].setName(" / ".join(dict.fromkeys(notices)), 0, 3, 1,
                           0x409)
    out = io.BytesIO()
    merged.save(out)
    return out.getvalue()


def world_licence(sources: dict) -> str:
    """Each WORLD face's copyright line, then the licence they share."""
    notices = [TTFont(io.BytesIO(sources[key]), lazy=True)["name"]
               .getDebugName(0) for key in WORLD]
    text = fetch(WORLD["notosans"][0].split("/")[0] + "/OFL.txt").decode(
        "utf-8")
    body = text[text.index("This Font Software"):]
    return ("Ferritext World is made of these fonts, each under the SIL "
            "Open Font License 1.1:\n\n" + "\n".join(notices) + "\n\n"
            + body)


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
    soften(font["CPAL"])
    return singles + len(mapping)


#: The emoji's colours, a little quieter than Twemoji's own: chroma (in
#: OKLCH) scaled by this, lightness kept, hue untouched — so skin tones and
#: flags stay what they are, only less loud beside the text.
SOFT_CHROMA = 0.78


def _oklab(r, g, b):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = lin(r), lin(g), lin(b)
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def _srgb(L, a, b):
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    rgb = (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
           -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
           -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)

    def enc(c):
        c = min(1.0, max(0.0, c))
        c = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
        return round(c * 255)
    return tuple(enc(c) for c in rgb)


def soften(cpal) -> None:
    """Scale every palette colour's chroma by SOFT_CHROMA."""
    for palette in cpal.palettes:
        for i, colour in enumerate(palette):
            L, a, b = _oklab(colour.red, colour.green, colour.blue)
            r, g, b2 = _srgb(L, a * SOFT_CHROMA, b * SOFT_CHROMA)
            palette[i] = type(colour)(red=r, green=g, blue=b2,
                                      alpha=colour.alpha)


def build(source: bytes, emoji: bytes, weight: int, emoji_weight: int,
          family: str, style: str, out: Path, work: Path,
          twemoji: TTFont = None) -> None:
    prefix, emoji_scale, _styles = FAMILIES[family]
    text = instance(source, weight)
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
    scale_upem(marks, round(upm * emoji_scale))
    marks["head"].unitsPerEm = upm          # glyphs stay at emoji_scale
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
    ps = f"{prefix}-{style.replace(' ', '')}"
    for rec_id, value in ((0, " / ".join(c for c in copyrights if c)),
                          (1, family), (2, style),
                          (3, f"{ps};{family} built by tools/build_fonts.py"),
                          (4, f"{family} {style}"), (6, ps),
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


def styles(names) -> list:
    """(family, output file, source, weight, emoji weight, style) for each
    style of the families named by their last word (serif, mono, sans), or
    of every family."""
    out = []
    for family, (prefix, _scale, family_styles) in FAMILIES.items():
        if names and family.split()[-1].lower() not in names:
            continue
        for style, source, weight, emoji_weight in family_styles:
            out.append((family, f"{prefix}-{style.replace(' ', '')}.ttf",
                        source, weight, emoji_weight, style))
    return out


def colour_only(names) -> None:
    """The colour stage alone, on the fonts already built in data/fonts."""
    package = fetch(*TWEMOJI)
    twemoji = twemoji_font(package)
    for _family, out, *_rest in styles(names):
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
    names = {a.lower() for a in sys.argv[1:] if not a.startswith("--")}
    unknown = names - {f.split()[-1].lower() for f in FAMILIES}
    if unknown:
        sys.exit(f"no such family: {', '.join(sorted(unknown))}")
    if "--colour" in sys.argv[1:]:
        colour_only(names)
        return
    todo = styles(names)
    needed = {source for _f, _o, source, *_r in todo} | {"emoji"}
    needed = set().union(*(DERIVED.get(k, (k,)) for k in needed))
    src = {key: fetch(*SOURCES[key]) for key in sorted(needed)}
    package = fetch(*TWEMOJI)
    twemoji = twemoji_font(package)
    with tempfile.TemporaryDirectory() as tmp:
        made = {"cjk": cjk_face, "world": world_face}
        for family, out, source, weight, emoji_weight, style in todo:
            data = (made[source](*(src[k] for k in DERIVED[source]),
                                 weight=weight, work=Path(tmp))
                    if source in DERIVED else src[source])
            build(data, src["emoji"], weight, emoji_weight, family,
                  style, OUT / out, Path(tmp), twemoji)
            print(f"{out:32} {(OUT / out).stat().st_size:>9,} B")
    for out, (path, sha) in CAPS.items():
        (OUT / out).write_bytes(fetch(path, sha))
    for out, path in LICENCES.items():
        (OUT / out).write_bytes(fetch(path))
    if "world" in {source for _f, _o, source, *_r in todo}:
        (OUT / WORLD_LICENCE).write_text(world_licence(src), "utf-8")
    (OUT / "CC-BY-Twemoji.txt").write_text(twemoji_licence(package), "utf-8")
    if not (OUT / "ui.ttf").exists():
        sys.exit("data/fonts/ui.ttf (Titillium Web Light) is missing")


if __name__ == "__main__":
    main()
