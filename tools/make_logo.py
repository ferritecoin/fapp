"""Render the Ferrite Text Messenger mark and every Android asset from it.

    python3 tools/make_logo.py            # rewrites the PNGs under data/

The mark is data/ferritext_logo_packet.svg: the bead on its wire, exactly
as in the supplied artwork (data/ferritext_logo.svg, kept byte-for-byte as
delivered, where it sits in a disc), in a rounded parallelogram, a packet
on its way. This script rasterizes the file's single path itself (a few
dozen lines of path parsing and an even-odd fill), so the build needs
nothing beyond Pillow, which the app already depends on. Any SVG library
would add a native dependency to every machine that builds Ferritext, for
the sake of one path.

The path is the filled shape with the ferrite-bead glyph (a rectangle on a
wire) knocked out of it, drawn with fill-rule:evenodd: the bead's outline
punches a hole, and the rectangle's interior, nested one level deeper, is
filled again. Rendering it with any other fill rule gives a black blob.

Every size below is the fraction of the output that the mark's reach
fills: the circle about the artwork's centre that holds all of it. So a
launcher's round mask, a legacy icon's circular crop or the plate on a dark
ground never cuts into it, whatever its shape.
"""
import math
import re
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "data" / "ferritext_logo_packet.svg"
#: The notification bar's icon (white line art; Android uses its alpha), and
#: the launch image at night: buildozer.spec ships both from android/.
STATUS = ROOT / "android" / "ic_stat_ferritext.png"
NIGHT = ROOT / "android" / "presplash_night.png"

WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)
_NUMBER = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtZz]|" + _NUMBER.pattern)
_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2,
          "Z": 0}


def _bezier(points, steps):
    """Flatten one Bezier segment (any degree) by de Casteljau."""
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        pts = list(points)
        while len(pts) > 1:
            pts = [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                   for a, b in zip(pts, pts[1:])]
        out.append(pts[0])
    return out


def parse_path(d: str, steps: int = 24) -> list:
    """SVG path data -> list of closed polygons (lists of (x, y)).

    Supports every command the supplied artwork uses (and the rest of the
    straight/Bezier set): absolute and relative, with implicit repetition of
    a command's parameters, which is how most of this path is written.
    """
    tokens = _TOKEN.findall(d)
    contours, current = [], []
    x = y = sx = sy = 0.0
    cmd, prev_ctrl, i = None, None, 0
    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                if current:
                    contours.append(current)
                current, x, y, prev_ctrl = [], sx, sy, None
                continue
        if cmd is None:
            raise ValueError("path data must start with a command")
        n = _ARITY[cmd.upper()]
        args = [float(t) for t in tokens[i:i + n]]
        if len(args) < n:
            raise ValueError(f"truncated '{cmd}' segment in path data")
        i += n
        rel = cmd.islower()
        up = cmd.upper()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if up == "M":
            if current:
                contours.append(current)
            x, y = ox + args[0], oy + args[1]
            sx, sy, current, prev_ctrl = x, y, [(x, y)], None
            cmd = "l" if rel else "L"        # repeats after M are line-tos
        elif up == "L":
            x, y = ox + args[0], oy + args[1]
            current.append((x, y))
            prev_ctrl = None
        elif up == "H":
            x = (x if rel else 0.0) + args[0]
            current.append((x, y))
            prev_ctrl = None
        elif up == "V":
            y = (y if rel else 0.0) + args[0]
            current.append((x, y))
            prev_ctrl = None
        elif up in "CS":
            if up == "C":
                c1 = (ox + args[0], oy + args[1])
                c2, end = (ox + args[2], oy + args[3]), (ox + args[4],
                                                         oy + args[5])
            else:
                c1 = ((2 * x - prev_ctrl[0], 2 * y - prev_ctrl[1])
                      if prev_ctrl else (x, y))
                c2, end = (ox + args[0], oy + args[1]), (ox + args[2],
                                                         oy + args[3])
            current.extend(_bezier([(x, y), c1, c2, end], steps))
            prev_ctrl, (x, y) = c2, end
        elif up in "QT":
            if up == "Q":
                c1, end = (ox + args[0], oy + args[1]), (ox + args[2],
                                                         oy + args[3])
            else:
                c1 = ((2 * x - prev_ctrl[0], 2 * y - prev_ctrl[1])
                      if prev_ctrl else (x, y))
                end = (ox + args[0], oy + args[1])
            current.extend(_bezier([(x, y), c1, end], steps))
            prev_ctrl, (x, y) = c1, end
    if current:
        contours.append(current)
    return contours


def load_svg(path: Path = SVG):
    text = path.read_text("utf-8")
    box = re.search(r'viewBox="([^"]+)"', text)
    vx, vy, vw, vh = (float(v) for v in box.group(1).replace(",", " ").split())
    d = re.search(r'\sd="([^"]+)"', text).group(1)
    return (vx, vy, vw, vh), parse_path(d)


def render(size: int, supersample: int = 4, path: Path = SVG) -> Image.Image:
    """The mark as an RGBA image: black where the artwork is filled,
    transparent elsewhere, anti-aliased by supersampling.

    Even-odd is done by XOR: each contour is filled into its own mask and
    toggled into the accumulator, so a point covered by an odd number of
    contours ends up set — precisely fill-rule:evenodd.
    """
    (vx, vy, vw, vh), contours = load_svg(path)
    big = size * supersample
    scale = big / max(vw, vh)
    acc = Image.new("L", (big, big), 0)
    for contour in contours:
        layer = Image.new("L", (big, big), 0)
        ImageDraw.Draw(layer).polygon(
            [((px - vx) * scale, (py - vy) * scale) for px, py in contour],
            fill=255)
        acc = ImageChops.logical_xor(acc.convert("1"),
                                     layer.convert("1")).convert("L")
    alpha = acc.resize((size, size), Image.LANCZOS)
    mark = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    mark.putalpha(alpha)
    return mark


def _reach(path: Path = SVG) -> float:
    """The fraction of its canvas the artwork's reach spans: twice the
    farthest any point of it lies from the canvas's centre."""
    (vx, vy, vw, vh), contours = load_svg(path)
    cx, cy = vx + vw / 2, vy + vh / 2
    far = max(math.hypot(px - cx, py - cy) for c in contours for px, py in c)
    return 2 * far / max(vw, vh)


#: The artwork's reach, as a fraction of its canvas (the supplied disc's
#: is its diameter, 4096 / 6628).
REACH = _reach()


def _canvas(size: int, reach: float) -> int:
    """The artwork's canvas, in pixels, at which its reach fills `reach`
    of a `size` square."""
    return int(round(size * reach / REACH))


def _centred(art: Image.Image, size: int) -> Image.Image:
    """A canvas-sized image centred in a `size` square, its margin cropped
    away where the canvas is the larger."""
    out = Image.new(art.mode, (size, size))
    out.paste(art, ((size - art.width) // 2,) * 2)
    return out


def _on(ground, image: Image.Image) -> Image.Image:
    """`image` laid on a solid ground (None: left transparent)."""
    if ground is None:
        return image
    out = Image.new("RGBA", image.size, ground)
    out.alpha_composite(image)
    return out


def mark(size: int, reach: float, ground=None) -> Image.Image:
    """A `size` square with the mark's reach filling `reach` of it,
    centred, on an optional solid ground (None = transparent)."""
    return _on(ground, _centred(render(_canvas(size, reach)), size))


def outline(canvas: int, grow: float = 0, supersample: int = 4):
    """The artwork's outer shape (its path's first contour) filled, grown
    by `grow` pixels all round (shrunk where negative): an L mask of the
    canvas. The edge is traced by a band 2 * |grow| wide with a disc at
    each of its points, which adds or takes away the same depth
    everywhere, corners included."""
    (vx, vy, vw, vh), contours = load_svg()
    big = canvas * supersample
    scale = big / max(vw, vh)
    edge = [((px - vx) * scale, (py - vy) * scale) for px, py in contours[0]]
    mask = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon(edge, fill=255)
    if grow:
        fill, r = (255 if grow > 0 else 0), abs(grow) * supersample
        draw.line(edge + edge[:1], fill=fill, width=round(2 * r))
        for x, y in edge:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)
    return mask.resize((canvas, canvas), Image.LANCZOS)


def plated(size: int, reach: float, ground=None) -> Image.Image:
    """The mark on a white plate of its own shape a little larger than
    itself, the plate's reach filling `reach` of a `size` square, on an
    optional ground. Where the ground is dark (a phone at night, a dark
    title bar) the mark's own black would vanish, and its knocked-out bead
    with it."""
    inner = reach * 0.90
    plate = Image.new("RGBA", (size, size), WHITE)
    plate.putalpha(_centred(outline(_canvas(size, inner),
                                    grow=size * reach * 0.05), size))
    plate.alpha_composite(mark(size, inner))
    return _on(ground, plate)


#: Sizes in the Windows .ico: the title bar, taskbar and Alt-Tab pick from
#: these (and their 125/150/200% cousins) instead of shrinking one image.
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def desktop_icon(size: int) -> Image.Image:
    """The window and taskbar icon on a computer: the mark on its plate,
    transparent outside it, so it reads on light and dark title bars."""
    return plated(size, 1.0)


def status_icon(size: int = 96) -> Image.Image:
    """The notification bar's icon (24 dp at 4x, the mark in its 22 dp live
    area): white line art on transparent, as Android draws only its alpha.
    The shape's edge, and the bead on its wire where the mark knocks them
    out of it."""
    canvas = _canvas(size, 0.92)
    shape = _centred(outline(canvas), size)
    edge = ImageChops.subtract(               # half a dp, the wire's weight
        shape, _centred(outline(canvas, grow=-size / 48), size))
    glyph = ImageChops.subtract(shape, mark(size, 0.92).getchannel("A"))
    icon = Image.new("RGBA", (size, size), WHITE)
    icon.putalpha(ImageChops.lighter(edge, glyph))
    return icon


def write_desktop_icons(out: Path) -> None:
    desktop_icon(512).save(out / "icon_desktop.png")
    frames = {s: desktop_icon(s) for s in ICO_SIZES}
    frames[256].save(out / "icon_desktop.ico", format="ICO",
                     sizes=[(s, s) for s in ICO_SIZES],
                     append_images=[frames[s] for s in ICO_SIZES
                                    if s != 256])


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    # In-app loading screen: transparent, so it sits on whatever the screen
    # paints (white, as specified); the mark as large as its square holds.
    mark(800, 1.0).save(out / "ferritext_logo.png")
    # Legacy launcher icon (Android < 8, and launchers that ignore adaptive
    # icons): white ground, as specified, the mark large enough to survive
    # the circular crop some launchers apply to legacy icons.
    mark(512, 0.84, WHITE).save(out / "icon.png")
    # Adaptive icon (Android 8+). The launcher supplies the mask shape, so
    # the background layer is plain white and the foreground keeps the mark
    # inside the 66/108 safe zone that every mask shape is guaranteed to
    # show in full; 0.58 leaves a little air inside it.
    Image.new("RGBA", (432, 432), WHITE).save(out / "icon_bg.png")
    mark(432, 0.58).save(out / "icon_fg.png")
    # Platform presplash, shown before any Python runs, as wide as the
    # screen: white ground, the mark a little smaller than the loading
    # screen's that follows it.
    mark(800, 0.47, WHITE).save(out / "presplash.png")
    # The same two on a phone in dark mode, and in the app's dark themes:
    # the mark on its plate, on black (the platform picks drawable-night
    # and values-night's ground by itself), so a phone at night is never
    # lit up white while the app starts.
    plated(800, 1.0).save(out / "ferritext_logo_dark.png")
    plated(800, 0.47, BLACK).save(NIGHT)
    # Window, taskbar and dock icon on Windows, macOS and Linux.
    write_desktop_icons(out)
    status_icon().save(STATUS)
    for name in ("ferritext_logo.png", "ferritext_logo_dark.png", "icon.png",
                 "icon_bg.png", "icon_fg.png", "presplash.png",
                 "icon_desktop.png", "icon_desktop.ico"):
        print("wrote", out / name)
    print("wrote", NIGHT)
    print("wrote", STATUS)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data")
