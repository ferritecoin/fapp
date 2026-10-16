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
    buttons stack when their labels will not fit; Back closes a sheet first.
"""
import importlib.util
import json
import os
import sys
import tempfile

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-design-")
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
needed = list(M.SERIF_FILES.values()) + ["ui.ttf", "OFL-EBGaramond.txt",
                                         "OFL-NotoEmoji.txt",
                                         "OFL-TitilliumWeb.txt",
                                         "CC-BY-Twemoji.txt"]
missing = [f for f in needed if not os.path.isfile(os.path.join(fonts, f))]
check("the serif (four styles), the capitals face and their licences ship",
      not missing, str(missing))
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
