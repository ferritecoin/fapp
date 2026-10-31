#!/usr/bin/env python3
"""The design system, measured: contrast, type, themes, and the parts.

    xvfb-run -a python3 desktop/test_design.py main.py

  * every text/ground pair either theme uses meets WCAG AA (4.5:1 for text,
    3:1 for large text and for marks such as ticks);
  * the bundled fonts are present, registered as Kivy's default family, and
    packaged into the APK with their licences; the emoji are drawn in
    colour, a flag or a family as one picture, in labels and text fields;
  * the theme follows the system unless chosen, and a status colour from
    the other theme is translated;
  * capitals are tracked and end in an ellipsis rather than overflowing;
    buttons stack when their labels will not fit; Back closes a sheet first;
  * (15.1) every language's pack is complete, its language is chosen from
    Settings or the system's, and Chinese lines break by its rules.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unicodedata

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-design-")
os.environ["FEXT_LANG"] = "en"          # the English the checks read
os.environ.pop("FEXT_THEME", None)

CLIENT = sys.argv[1] if len(sys.argv) > 1 else "main.py"
ROOT = os.path.dirname(os.path.abspath(CLIENT))
_spec = importlib.util.spec_from_file_location("fext_design", CLIENT)
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_design"] = M
_spec.loader.exec_module(M)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""))


def luminance(hex_color):
    h = hex_color.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = out
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


print("\nContrast (WCAG AA)")
TEXT_ON = {
    "text": ("bg", "surface", "surface_hi", "own", "peer"),
    "text_2": ("bg", "surface", "surface_hi", "own", "peer", "quote_own",
               "quote_peer"),
    "text_3": ("bg", "surface", "own", "peer"),
    "accent": ("bg", "surface", "surface_hi", "own", "peer"),
    "alert": ("bg", "surface", "peer"),
    "ok": ("bg",),
    "on_accent": ("accent",),
    "on_alert": ("alert",),
}
MARKS_ON = {"tick": ("own", "peer"), "tick_grey": ("own", "peer"),
            "rule_strong": ("bg",)}
for name, theme in M.THEMES.items():
    worst = []
    for fg, grounds in TEXT_ON.items():
        for bg in grounds:
            ratio = contrast(theme[fg], theme[bg])
            if ratio < 4.5:
                worst.append(f"{fg} on {bg} {ratio:.2f}")
    check(f"{name}: every text colour reads at 4.5:1 or better on its grounds",
          not worst, ", ".join(worst))
    weak = [f"{fg} on {bg} {contrast(theme[fg], theme[bg]):.2f}"
            for fg, grounds in MARKS_ON.items() for bg in grounds
            if contrast(theme[fg], theme[bg]) < (3.0 if fg != "rule_strong"
                                                 else 1.3)]
    check(f"{name}: ticks stand out at 3:1 from both bubbles", not weak,
          ", ".join(weak))
low = [p for p in M.AVATAR_PALETTE if contrast("#FFFFF8", p) < 3.0]
check("every avatar colour carries its (large) initial at 3:1", not low,
      str(low))
for name in M.THEMES:
    M.apply_theme(name)
    poor = [p for p in M.AVATAR_PALETTE
            if contrast(M.mix(p, "#FFFFFF", .45) if M.T.dark
                        else M.mix(p, "#000000", .18), M.T.peer) < 4.5]
    check(f"{name}: group authors' names (in their colour) read at 4.5:1",
          not poor, str(poor))
M.apply_theme("dark")

print("\nType")
fonts = os.path.join(ROOT, "data", "fonts")
needed = [f for files in M.FACES.values() for f in files.values()] + \
    list(M.CAPS_FACES.values()) + [f for _n, f in M.SCRIPT_FACES] + [
        "OFL-EBGaramond.txt", "OFL-NotoEmoji.txt", "OFL-TitilliumWeb.txt",
        "OFL-IBMPlexMono.txt", "OFL-OpenSans.txt", "OFL-Silkscreen.txt",
        "OFL-NotoSansCJK.txt", "OFL-NotoSansWorld.txt", "CC-BY-Twemoji.txt"]
missing = [f for f in needed if not os.path.isfile(os.path.join(fonts, f))]
check("every theme set's faces, the capitals faces, the CJK and World "
      "faces and their licences ship", not missing, str(missing))
from kivy.core.text import LabelBase                          # noqa: E402
registered = LabelBase._fonts.get("Roboto") or ()
check("the serif is Kivy's default family, so every label uses it",
      registered and str(registered[0]).endswith("FerritextSerif-Regular.ttf"),
      str(registered))
check("the capitals face is registered from the supplied ui.ttf",
      M.CAPS_FONT_NAME == "FerritextCaps" and
      str(LabelBase._fonts["FerritextCaps"][0]).endswith("ui.ttf"))
spec = open(os.path.join(ROOT, "buildozer.spec"), encoding="utf-8").read()
exts = next(line for line in spec.splitlines()
            if line.startswith("source.include_exts")).split("=", 1)[1]
exts = {e.strip() for e in exts.split(",")}
check("the APK packages the fonts and the licence texts",
      {"ttf", "txt"} <= exts, str(exts))

print("\nThemes")


def prefs(**kw):
    os.makedirs(str(M.DATA_DIR), exist_ok=True)
    (M.DATA_DIR / "prefs.json").write_text(json.dumps(kw), "utf-8")


prefs(theme="light")
check("a chosen theme is used whatever the system says",
      M.APPEARANCE.resolve() == "light")
prefs(theme="system")
os.environ["FEXT_THEME"] = "light"
check("'System' follows the system (light)", M.APPEARANCE.resolve() == "light")
os.environ["FEXT_THEME"] = "dark"
check("'System' follows the system (dark, on black by default)",
      M.APPEARANCE.resolve() == "black")
os.environ.pop("FEXT_THEME")
M.system_prefers_dark = lambda: None
check("with no answer from the system, the dark theme",
      M.APPEARANCE.resolve() == "black")
prefs(theme="system")
M.system_prefers_dark = lambda: True
check("Black background (v12): the dark theme on a black page, by default",
      M.APPEARANCE.resolve() == "black" and M.THEMES["black"]["bg"] ==
      "#000000")
prefs(theme="system", black=False)
check("...a dark grey page when it is turned off",
      M.APPEARANCE.resolve() == "dark")
prefs(theme="system", black=True)
M.system_prefers_dark = lambda: False
check("...and in light, the light theme still",
      M.APPEARANCE.resolve() == "light")
prefs(theme="dark", black=True)
M.apply_theme(M.APPEARANCE.resolve())
check("...chosen dark too; it counts as dark everywhere a widget asks",
      M.T.name == "black" and M.T.dark and M.T.text == M.THEMES["dark"]["text"])
M.apply_theme("dark")
M.system_prefers_dark = lambda: None
prefs(theme="system", text_size="large", reduce_motion=True)
M.APPEARANCE.resolve()
check("text size and reduced motion are read from the preferences",
      abs(M.APPEARANCE.scale - 1.15) < 1e-9 and M.APPEARANCE.reduce_motion
      and M.motion(0.3) == 0)
prefs(theme="system")
M.APPEARANCE.resolve()
M.apply_theme("light")
check("a status colour posted in the dark theme is shown in the light one",
      M.retint(M.THEMES["dark"]["alert"]) == M.THEMES["light"]["alert"])
check("the old colour names follow the theme",
      M.COL_DANGER == M.THEMES["light"]["alert"] and
      M.COL_BG == M.THEMES["light"]["bg"])
M.apply_theme("dark")

print("\nTheme sets (15.1)")
from kivy.core.text import Label as CoreLabel                 # noqa: E402
from kivy.metrics import dp, sp                               # noqa: E402
check("each theme set names a palette for a light and a dark phone, and "
      "each palette belongs to one set",
      all(look["light"] in M.THEMES and look["dark"] in M.THEMES
          for look in M.THEME_SETS.values()) and
      set(M.PALETTE_SET) == set(M.THEMES))
prefs(theme="dark", theme_set="overclock", black=True)
night = M.APPEARANCE.resolve()
prefs(theme="light", theme_set="overclock")
day = M.APPEARANCE.resolve()
M.apply_theme(day)
check("Overclock: obsidian by night, Black background or not; violet by "
      "day, a dark page too", night == "overclock_night" and
      day == "overclock_day" and M.T.dark)
prefs(theme="dark", theme_set="ferrite", black=True)
dark = M.APPEARANCE.resolve()
prefs(theme="light", theme_set="ferrite")
light = M.APPEARANCE.resolve()
M.apply_theme(light)
check("Ferrite: graphite by night, slate by day",
      dark == "ferrite_dark" and light == "ferrite_light" and not M.T.dark)
prefs(theme="dark", theme_set="nonesuch")
check("an unknown theme set is Axiom", M.APPEARANCE.resolve() == "black")
faces = {}
for key, look in M.THEME_SETS.items():
    M.use_theme(look["dark"])
    files = LabelBase._fonts["Roboto"]
    faces[key] = (str(files[0]), str(files[1]), str(files[2]),
                  M.CAPS_FONT_NAME)
check("each set's face becomes the default family, with its capitals",
      faces["axiom"][0].endswith("FerritextSerif-Regular.ttf") and
      faces["overclock"][0].endswith("FerritextMono-Regular.ttf") and
      faces["ferrite"][0].endswith("FerritextSans-Regular.ttf") and
      faces["axiom"][3] == faces["ferrite"][3] == "FerritextCaps" and
      faces["overclock"][3] == "FerritextPixel", str(faces))
check("...and a style a face lacks is drawn in its nearest (italic upright)",
      faces["ferrite"][1] == faces["ferrite"][0] and
      faces["ferrite"][2].endswith("FerritextSans-Bold.ttf"))
M.use_theme("overclock_night")
M.APPEARANCE.scale = 1.0
square = (M.corner(dp(10)), M.fs(20))
M.use_theme("dark")
check("Overclock cuts its corners square and sets its wider face smaller; "
      "Axiom is as it was", square == (dp(2), sp(20) * 0.82) and
      (M.corner(dp(10)), M.fs(20)) == (dp(10), sp(20)))
check("Chinese, Japanese and Korean need the CJK face; other text does not",
      M.script_face("明天见") == M.script_face("こんにちは") ==
      M.script_face("안녕하세요") == "FerritextCJK" and
      M.script_face("See you, café 👍") is None)
drawn_in = {}
for key, look in M.THEME_SETS.items():
    M.use_theme(look["dark"])
    for text in ("明天见 👍", "See you"):
        core = CoreLabel(text=text, font_size=dp(20))
        core.refresh()
        drawn_in[key, text] = os.path.basename(core.options["font_name_r"])
M.use_theme("dark")
check("a label with Chinese is drawn in the CJK face in every set, the rest "
      "in the set's own", all(drawn_in[k, "明天见 👍"] ==
                              "FerritextCJK-Regular.ttf" and
                              drawn_in[k, "See you"] != "FerritextCJK-Regular.ttf"
                              for k in M.THEME_SETS), str(drawn_in))
field = M.InkTextInput(text="")
field.text = "我在路上了"
cjk = field.font_name
field.text = "on my way"
check("a text field holding Chinese is set wholly in the CJK face, and goes "
      "back", cjk == "FerritextCJK" and field.font_name == "Roboto")
prefs(theme="system")
M.APPEARANCE.resolve()

print("\nLanguages (15.1)")
import subprocess                                             # noqa: E402
from datetime import datetime                                 # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "tools"))
import phrases                                                # noqa: E402
gap = subprocess.run([sys.executable, os.path.join(ROOT, "tools",
                                                   "phrases.py"), "--check"],
                     capture_output=True, text=True)
check("every pack translates every phrase main.py shows, and no other, "
      "with the same {fields}", gap.returncode == 0, gap.stdout[-400:])
unfit = [(code, english) for code, pack in phrases.packs().items()
         for english, text in pack.items()
         if not isinstance(text, str) or not text.strip()]
for code, pack in phrases.packs().items():
    for english, text in pack.items():
        try:
            text.format(**{f: "x" for f in phrases.fields(english)})
        except (AttributeError, IndexError, KeyError, ValueError):
            unfit.append((code, english))
check("...and each translation is filled in as its English is", not unfit,
      str(unfit[:3]))
check("the system's language: Simplified Chinese from a Chinese tag but a "
      "Traditional one, English from the rest",
      [M.pick_language([tag]) for tag in (
          "zh_CN.UTF-8", "zh-Hans-SG", "zh", "zh-TW", "zh-Hant-HK", "en-GB",
          "fr-FR", "")] == ["zh-CN"] * 3 + ["en"] * 5)
prefs(language="zh-CN")
chosen = M.LANG.resolve()
prefs(language="system")
os.environ["FEXT_LANG"] = "zh_CN.UTF-8"
system = M.LANG.resolve()
os.environ["FEXT_LANG"] = "en"
check("a language chosen in Settings is used, whatever the system's",
      chosen == "zh-CN" and system == "zh-CN" and M.LANG.resolve() == "en")
monday = datetime(2026, 10, 26, 9, 5)
M.LANG.load("zh-CN")
zh = (M.tr("Settings"), M.trn("{n} new message", "{n} new messages", 1),
      M.date_text(monday, "wkd_day_mon_time"), M.tr("No pack has this"),
      M.shown_body("voice", M.voice_label(12000)),
      M.quote_text({"kind": "image", "snip": "Photo"}),
      M.quote_text({"kind": "image", "snip": "the summit"}))
M.LANG.load("en")
en = (M.tr("Settings"), M.trn("{n} new message", "{n} new messages", 1),
      M.trn("{n} new message", "{n} new messages", 2),
      M.date_text(monday, "wkd_day_mon_time"), M.voice_label(12000))
check("in Chinese: its words, one form for any number, its dates; a phrase "
      "it lacks in English", zh[:4] == ("设置", "1 条新消息",
                                       "10月26日 周一 09:05",
                                       "No pack has this"), str(zh))
check("...a photo or voice message from any app is named in it, a "
      "caption is not", zh[4:] == ("语音消息（0:12）", "照片", "the summit"),
      str(zh))
check("in English: 1 message, 2 messages, Mon 26 Oct; a voice message's "
      "body is English for the other phone",
      en == ("Settings", "1 new message", "2 new messages",
             "Mon 26 Oct 09:05", "Voice message (0:12)"), str(en))
check("Settings › Language lists the system's (and which) and each "
      "language by its own name",
      M.SettingsScreen._language_choice("system") == "System (English)" and
      M.SettingsScreen._language_choice("zh-CN") == "简体中文")


def width_of(text):
    return len(text), 0


check("a Chinese line never starts with a full stop, nor ends with an "
      "opening bracket", M.break_lines("一二三四五。六七", 5, width_of) ==
      "一二三四\n五。六七" and M.break_lines("一二三（四五）", 4, width_of) ==
      "一二三\n（四五）")
check("...words break at a space, which goes; markup is measured without "
      "its tags, and a break goes in after them",
      M.break_lines("alpha beta", 6, width_of) == "alpha\nbeta" and
      M.break_lines("[i]一二三[/i]四五", 3, width_of, markup=True) ==
      "[i]一二三[/i]\n四五")
M.LANG.load("zh-CN")
note = M.InfoLabel(M.tr("System follows your phone's setting (now dark)."),
                   font_size=M.fs(14.5))
M.LANG.load("en")
whole = CoreLabel(text=note.text, font_size=M.fs(14.5))
whole.refresh()
note.width = whole.width - M.fs(14.5) / 2   # all but its full stop fits
note.texture_update()
starts = [line.words[0].text[:1] for line in note._label._cached_lines[1:]
          if line.words]
check("...and a label is laid out by those rules", starts and
      not set(starts) & M.NO_LINE_START, str(starts))

print("\nEvery script (15.1)")
SAMPLES = {
    "Arab": "مرحبا بالعالم", "Hebr": "שלום עולם", "Thaa": "ހެލޯ ދުނިޔެ",
    "Deva": "नमस्ते दुनिया", "Beng": "হ্যালো বিশ্ব", "Guru": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ",
    "Gujr": "નમસ્તે દુનિયા", "Orya": "ନମସ୍କାର ବିଶ୍ୱ", "Taml": "வணக்கம் உலகம்",
    "Telu": "నమస్కారం ప్రపంచం", "Knda": "ನಮಸ್ಕಾರ ಜಗತ್ತು",
    "Mlym": "നമസ്കാരം ലോകം", "Sinh": "ආයුබෝවන් ලෝකය", "Thai": "สวัสดีชาวโลก",
    "Laoo": "ສະບາຍດີໂລກ", "Tibt": "བཀྲ་ཤིས་བདེ་ལེགས།",
    "Mymr": "မင်္ဂလာပါ ကမ္ဘာ", "Khmr": "សួស្តី ពិភពលោក",
    "Geor": "გამარჯობა მსოფლიო", "Armn": "Բարեւ աշխարհ", "Ethi": "ሰላም ዓለም"}
world = M.SCRIPT_FILES.get("FerritextWorld", "")
has = M.font_characters(world) if world else frozenset()
lacking = {code: text for code, text in SAMPLES.items()
           if {ord(ch) for ch in text if M._drawn(ch)} - has}
check("the World face has every letter of 21 scripts", world and
      not lacking, str(lacking))
caps = M.font_characters(str(M.FONT_DIR / "ui.ttf"))
serif = str(M.FONT_DIR / M.SERIF_FILES["fn_regular"])
check("a font's characters are read from its cmap (format 12, and 4 in the "
      "capitals face)", 0x1F600 in M.font_characters(serif) and
      ord("A") in caps and 0x4E00 not in caps and len(has) > 8000)
M.use_theme("overclock_night")
greek_in_mono = M.script_face("Καλημέρα")
M.use_theme("dark")
check("a label is drawn in its theme's face, or the first face that has "
      "all its text: Arabic, Hindi or Thai in the World face, Chinese in "
      "the CJK face, Greek in the World face only where the theme's lacks it",
      M.script_face("مرحبا") == M.script_face("नमस्ते") ==
      M.script_face("สวัสดี") == "FerritextWorld" and
      M.script_face("明天见") == "FerritextCJK" and
      M.script_face("See you, café 👍") is None and
      M.script_face("Καλημέρα") is None and
      greek_in_mono == "FerritextWorld")
check("each is shaped as its script; Latin, Chinese and Georgian as Latin",
      all(M.shaping_script(text) == code for code, text in SAMPLES.items()
          if code not in ("Geor", "Armn", "Ethi")) and
      M.shaping_script("hello 明天 გამარჯობა") == "Latn")
#: From Unicode's BidiCharacterTest.txt: the text, the paragraph's
#: direction (0, 1, or 2 for its own), its level, each character's level,
#: and the order they are shown in.
BIDI_CASES = [
    "0661 0028 0627 0029;1;1;2 1 1 1;3 2 1 0",
    "0028 0661 0029 0627;0;0;0 2 0 1;0 1 2 3",
    "0627 0028 0661 003F 0020 0029 005D;0;0;1 1 2 1 1 1 0;5 4 3 2 1 0 6",
    "0028 06F1 0029 0627;1;1;1 2 1 1;3 2 1 0",
    "0028 0627 0029 06F1;0;0;0 1 0 2;0 1 2 3",
    "0627 0028 06F1 0029;0;0;1 1 2 1;3 2 1 0",
    "05D0 0020 0031 002D 0032;0;0;1 1 2 2 2;2 3 4 1 0",
    "0061 0020 2329 0062 002E 0031 3009;1;1;2 2 2 2 2 2 2;0 1 2 3 4 5 6",
    "0061 0020 0031 0020 0032 002D 0033;1;1;2 2 2 2 2 2 2;0 1 2 3 4 5 6",
    "05D0 0020 3008 05D1 002E 0031 3009;0;0;1 1 1 1 1 2 1;6 5 4 3 2 1 0",
    "0028 05D0 2680 0061 0028 0029 05D1;0;0;0 1 0 0 0 0 1;0 1 2 3 4 5 6",
    "05D0 0028 0061 0028 0062 0029 0028 0029;1;1;1 1 2 2 2 2 1 1;"
    "7 6 2 3 4 5 1 0",
    "0028 0061 0029 05D0 0062 005D 05D1;1;1;1 2 1 1 2 1 1;6 5 4 3 2 1 0",
    "0061 0028 0028 05D0 0029 2680 0028 2681 0029;0;0;0 0 0 1 0 0 0 0 0;"
    "0 1 2 3 4 5 6 7 8",
    "2680 0028 2681 0061 0029 005D 2682;1;1;1 1 1 2 1 1 1;6 5 4 3 2 1 0",
    "05D0 0028 0061 0029 2680 0029 05D1;1;1;1 1 2 1 1 1 1;6 5 4 3 2 1 0",
]
wrong = []
for case in BIDI_CASES:
    codes, direction, level, levels, order = case.split(";")
    text = "".join(chr(int(h, 16)) for h in codes.split())
    base, got = M.bidi_levels(text, {"0": 0, "1": 1, "2": None}[direction])
    shown = M.display_order(text, got, base)
    if (base, got, shown) != (int(level), [int(v) for v in levels.split()],
                              [int(v) for v in order.split()]):
        wrong.append(codes)
check("right to left is put in order as Unicode's own tests have it "
      "(numbers, brackets, Arabic digits)", not wrong, str(wrong))
check("...a number or a Latin word in Arabic reads left to right, and a "
      "bracket faces the right way",
      M.shown_line("abc אבג 123") == "abc 123 גבא" and
      M.shown_line("مع John الساعة 10:30") ==
      "10:30 ةعاسلا John عم" and M.shown_line("אב (גד)") == "(דג) בא")
three = lambda text: (len(text), 0)                      # noqa: E731
check("...a paragraph breaks into lines in reading order, each then shown "
      "right to left; on one line it is shortened at the end it is read to",
      M.display_text("שלום עולם רב לכולם", 9, three) ==
      ("םלוע םולש\nםלוכל בר", True) and
      M.display_text("שלום עולם רב לכולם", 9, three, one_line=True) ==
      ("…לוע םולש", True))
check("...and in markup, each piece keeps its tags, and an escaped bracket "
      "its escape (here mirrored)",
      M.display_text("[b]שלום[/b] [ref=0]עולם[/ref] &bl;", None, three,
                     markup=True) ==
      ("&br; [ref=0]םלוע[/ref] [b]םולש[/b]", True))
arabic = CoreLabel(text=SAMPLES["Arab"], font_size=dp(20))
arabic.refresh()
M._shape_as(arabic, "Arab")
joined = arabic.get_extents(SAMPLES["Arab"])[0]
M._shape_as(arabic, "Latn")
apart = arabic.get_extents(SAMPLES["Arab"])[0]
check("a label in Arabic is drawn in the World face, its letters joined",
      arabic.options["font_name_r"] == world and
      arabic.options["font_script_name"] == "Arab" and joined < apart,
      f"{joined} {apart}")
note = M.InfoLabel(SAMPLES["Arab"] + " " + SAMPLES["Arab"] + " 2026",
                   font_size=M.fs(16))
note.width = dp(150)
note.texture_update()
lines = [line.words[0].text for line in note._label._cached_lines
         if line.words]
check("...a wrapped one shows each line in display order, the first "
      "words read on the first line, lined up on the right",
      len(lines) > 1 and M.shown_line("مرحبا") in lines[0] and
      "2026" in lines[-1] and note._label.options["halign"] == "right",
      str(lines))
thai = "สวัสดีครับพรุ่งนี้เจอกันที่สถานี"
parted = [p for p in M.line_pieces(thai) if p[0] in "ะาำๅ" or
          unicodedata.category(p[0])[0] == "M"]
khmer = M.line_pieces("ស្ត្រី")
check("Thai, Lao, Khmer and Burmese break between letters, never parting "
      "a vowel or mark from its letter, nor a stacked letter",
      len(M.line_pieces(thai)) > 10 and not parted and khmer == ["ស្ត្រី"],
      str(parted or khmer))
field = M.InkTextInput(text="مرحبا بالعالم", size=(dp(300), dp(48)))
field.cursor = (len(field.text), 0)
end = field.cursor_offset()
field.cursor = (0, 0)
start = field.cursor_offset()
check("a field holding Arabic lines up on the right, its caret at the "
      "left end of what is typed", field.base_direction == "rtl" and
      end < start, f"{end} {start}")

print("\nParts")
from kivy.app import App                                      # noqa: E402
from kivy.clock import Clock                                  # noqa: E402
from kivy.core.window import Window                           # noqa: E402
from kivy.metrics import dp                                   # noqa: E402
from kivy.uix.boxlayout import BoxLayout                      # noqa: E402
from kivy.core.text import Label as CoreLabel                 # noqa: E402


def hues(pixels) -> int:
    """How many solid pixels are in colour (not a grey of any shade)."""
    return sum(1 for i in range(0, len(pixels), 4) if pixels[i + 3] > 200
               and max(pixels[i:i + 3]) - min(pixels[i:i + 3]) > 60)


def drawn(text, color=(1, 1, 1, 1), **style):
    core = CoreLabel(text=text, font_size=dp(40), color=color, **style)
    core.refresh()
    return core.texture.width, hues(core.texture.pixels)


class Harness(App):
    def build(self):
        Window.size = (dp(360), dp(780))
        self.root_box = BoxLayout(orientation="vertical")
        Clock.schedule_once(self.parts, 0.3)
        return self.root_box

    def parts(self, _dt):
        self.colour()
        self.reactions()
        caps = M.CapsLabel("Section", size=11, size_hint_x=None,
                           width=dp(300))
        check("capitals are set in upper case", caps.text == "SECTION")
        loose = M.CapsLabel("Section", size=11, tracking=0.3)
        tight = M.CapsLabel("Section", size=11, tracking=0.0)
        check("...with tracking added between the letters",
              loose.natural_width > tight.natural_width + dp(10))
        caps.width = dp(30)
        check("...and too long for their room, they end in an ellipsis",
              caps._fit().endswith("…") and len(caps._fit()) < 7)
        row = M.ButtonRow([M.Btn("Cancel", kind="quiet"),
                           M.Btn("Remove this identity for good",
                                 kind="solid_danger")])
        self.root_box.add_widget(row)
        self.row = row
        sheet = M.Sheet("A sheet")
        sheet.add(M.InfoLabel("Body text"))
        sheet.open()
        self.sheet = sheet
        Clock.schedule_once(self.settle, 1.2)

    def colour(self):
        face, face_hues = drawn("\U0001F600")
        check("emoji are drawn in colour", face_hues > 200, str(face_hues))
        check("...whatever the colour of the text around them",
              drawn("\U0001F600", color=(0, 0, 0, 1))[1] > 200 and
              drawn("\U0001F600", color=(.9, .1, .1, 1))[1] > 200)
        styles = [drawn("\U0001F600", bold=b, italic=i)[1]
                  for b in (False, True) for i in (False, True)]
        check("...in all four styles of the serif", min(styles) > 200,
              str(styles))
        joined = {name: drawn(text) for name, text in (
            ("flag", "\U0001F1EC\U0001F1E7"),
            ("skin tone", "\U0001F44D\U0001F3FD"),
            ("family", "\U0001F468\u200D\U0001F469\u200D\U0001F467"),
            ("keycap", "1\uFE0F\u20E3"),
            ("heart on fire", "\u2764\uFE0F\u200D\U0001F525"))}
        apart = {k: v for k, v in joined.items()
                 if abs(v[0] - face) > 2 or v[1] < 100}
        check("a flag, a skin tone, a family or a keycap is one picture",
              not apart, f"{apart} (one emoji is {face} wide)")
        check("letters keep the colour of the text", drawn("Aa \u00A9")[1] == 0)
        self.ink = M.InkTextInput(text="Ink \U0001F600",
                                  foreground_color=(.3, .3, .3, 1),
                                  font_size=dp(40), size_hint=(None, None),
                                  size=(dp(300), dp(70)))
        self.root_box.add_widget(self.ink)

    def reactions(self):
        thumbs_up, heart = "\U0001F44D", "\u2764\ufe0f"
        names = {"FecMe": "ann", "FecA": "ben", "FecB": "cy", "FecC": "dee",
                 "FecD": "eli"}
        given = [(thumbs_up, a, f"2026-10-15T10:0{i}:00+00:00")
                 for i, a in enumerate(("FecA", "FecMe", "FecB"))]
        given += [(heart, a, f"2026-10-15T11:0{i}:00+00:00")
                  for i, a in enumerate(("FecA", "FecB", "FecC", "FecD"))]
        bar = M.ReactionBar()
        bar.set(given, "FecMe", lambda _e: None, name_of=names.get,
                under=M.T.peer)
        self.root_box.add_widget(bar)
        bar.layout_for(dp(320))
        mine, many = bar.chip_for(thumbs_up), bar.chip_for(heart)
        mine._sync()
        drawn_lines = [type(i).__name__ for c in bar.chips
                       for i in c.canvas.before.children
                       if type(i).__name__ in ("Line", "SmoothLine")]
        check("reactions have no outline (v13): a fill a shade off the "
              "bubble, gold for yours", not drawn_lines and
              mine.fill == M.mix(M.T.peer, M.T.accent, .30) and
              many.fill == M.mix(M.T.peer, M.T.text, .09), drawn_lines)
        check("...in a group, up to three who gave one show as faces, the "
              "latest first, inside the reaction",
              [f.disc.seed for f in mine.faces] == ["FecB", "FecMe", "FecA"]
              and mine.label.text == thumbs_up and
              mine.faces[-1].right <= mine.right - dp(8),
              [f.disc.seed for f in mine.faces])
        check("...four or more as a count", not many.faces and
              many.label.text == f"{heart} 4")
        direct = M.ReactionBar()
        direct.set(given[:2], "FecMe", lambda _e: None)
        check("in a direct chat, the count alone",
              not direct.chips[0].faces and
              direct.chips[0].label.text == f"{thumbs_up} 2")
        self.root_box.remove_widget(bar)

    def settle(self, _dt):
        lines = [t for t in (self.ink._lines_labels or ()) if t]
        pixels = lines[0].pixels if lines else b""
        inked = sum(1 for i in range(0, len(pixels), 4)
                    if pixels[i + 3] > 200 and
                    all(abs(c - 77) < 6 for c in pixels[i:i + 3]))
        check("a text field draws emoji in colour too, not as a silhouette",
              hues(pixels) > 200 and
              tuple(self.ink.foreground_color) == (1, 1, 1, 1),
              f"{hues(pixels)} in colour")
        check("...and its letters in the colour it is given", inked > 20,
              str(inked))
        check("buttons whose labels will not fit side by side stack",
              self.row.orientation == "vertical" and
              self.row.height >= 2 * M.TOUCH)
        check("a sheet rises from the bottom edge",
              abs(self.sheet.y) < 1 and self.sheet.width <= Window.width,
              f"y={self.sheet.y} rise={self.sheet.rise}")

        class Shell:
            manager = type("Mgr", (), {"current": "chats"})()
        self.handled = M.FextApp._on_keyboard(Shell(), Window, 27)
        Clock.schedule_once(self.closed, 0.5)

    def closed(self, _dt):
        check("Back closes the open sheet first",
              self.handled and self.sheet.parent is None)
        looks = [M.tick_look(s) for s in (0, 1, 2, 3)]
        marks = [M.TickMarks(count=c, tint=t, clock=k) for c, t, k in looks]
        check("two ticks at most: a clock while on this phone, one grey tick "
              "on the relay, two grey on their phone, two gold once read",
              [(c, t == M.T.tick, k) for c, t, k in looks] ==
              [(0, False, True), (1, False, False), (2, False, False),
               (2, True, False)] and marks[0].width > 0 and
              marks[1].width < marks[2].width == marks[3].width,
              str([(c, t, k) for c, t, k in looks]))
        avatars = [M.Avatar("FecX", "Ann", kind=k) for k in
                   ("dm", "group", "self")]
        check("people, groups and notes to self have distinct discs",
              avatars[0]._initial.text == "A" and avatars[1].kind == "group"
              and avatars[2]._initial is None)
        Clock.schedule_once(lambda _dt: self.stop(), 0.2)


Harness().run()
passed = sum(1 for _n, c in RESULTS if c)
print(f"\n  {passed}/{len(RESULTS)} passed")
os._exit(0 if passed == len(RESULTS) else 1)
