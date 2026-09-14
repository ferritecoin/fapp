"""Generate the Ferrite Text Messenger mark and the Android assets from it.

    python3 tools/make_logo.py            # rewrites everything under data/

The mark is a laurel wreath around a wire-frame globe, with a ferrite bead —
a rectangle threaded by a wire, exactly as it is drawn on a schematic — as
the central glyph. It is generated rather than stored as a binary so the
geometry stays editable and every size can be regenerated from one source.

Needs Pillow. Everything is drawn at 5x and downsampled, which is cheaper
than anti-aliasing each shape and gives cleaner leaf edges.
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

S = 800                     # final size of the mark
K = 5                       # supersample factor
W = S * K
U = K                       # one reference unit (of 800) in supersampled px
CX, CY = 400 * U, 400 * U

GREY = (150, 150, 150, 255)
BLACK = (0, 0, 0, 255)
CREAM = (253, 248, 238, 255)          # COL_BG_LIST, the app background

img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# ---------------------------------------------------------------- globe ----
R = 252 * U
gw = 13 * U
d.ellipse([CX - R, CY - R, CX + R, CY + R], outline=GREY, width=gw)
for frac in (0.34, 0.68):                     # meridians, as nested ellipses
    rw = int(R * frac)
    d.ellipse([CX - rw, CY - R, CX + rw, CY + R], outline=GREY, width=gw)
d.line([CX, CY - R, CX, CY + R], fill=GREY, width=gw)
for frac in (-0.72, -0.38, 0.0, 0.38, 0.72):  # parallels, as chords
    y = CY + int(R * frac)
    half = int(math.sqrt(max(R * R - (y - CY) ** 2, 0)))
    d.line([CX - half, y, CX + half, y], fill=GREY, width=gw)


# ---------------------------------------------------------------- laurel ---
def leaf(cx, cy, length, width, point_dx, point_dy):
    """One pointed laurel leaf, based at (cx, cy), aimed along the vector.

    Drawn into its own tile pointing +x and then rotated about its BASE, so
    the base stays on the branch arc whatever the angle.
    """
    L, H = int(length), int(width)
    top, bottom = [], []
    for i in range(25):
        t = i / 24.0
        y = (H / 2.0) * math.sin(math.pi * t) ** 0.72     # a pointed lens
        top.append((t * L, -y))
        bottom.append((t * L, y))
    pad = int(L * 0.75)
    tile = Image.new("RGBA", (L + pad * 2, H + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(tile).polygon(
        [(x + pad, y + pad + H / 2.0) for x, y in top + list(reversed(bottom))],
        fill=BLACK)
    angle = math.degrees(math.atan2(-point_dy, point_dx))   # y grows downward
    tile = tile.rotate(-angle, resample=Image.BICUBIC, expand=True,
                       center=(pad, pad + H / 2.0))
    img.alpha_composite(tile, (int(cx - pad), int(cy - pad - H / 2.0)))


def branch(side):
    """Half the wreath: leaves along an arc, fanning up and outward.

    No stem is drawn. The leaf bases sit on the arc and overlap their
    neighbours, so the branch reads as continuous — drawing the arc as well
    leaves a bare wire showing through on the inside of the curve.
    """
    arc_r = 292 * U
    ax, ay = 400 * U, 398 * U
    n = 15
    a0, a1 = 10.0, 128.0               # degrees from straight down
    flare = math.radians(58)           # lean off the stem, towards the tips
    for i in range(n):
        t = i / (n - 1.0)
        a = math.radians(a0 + (a1 - a0) * t)
        px = ax + side * arc_r * math.sin(a)
        py = ay + arc_r * math.cos(a)
        rx, ry = side * math.sin(a), math.cos(a)          # outward radial
        tx, ty = side * math.cos(a), -math.sin(a)         # along the branch
        dx = tx * math.cos(flare) + rx * math.sin(flare)
        dy = ty * math.cos(flare) + ry * math.sin(flare)
        size = 1.0 - 0.24 * t                             # taper to the tip
        leaf(px, py, 96 * U * size, 38 * U * size, dx, dy)


for _side in (1, -1):
    branch(_side)

# ------------------------------------------------------- ferrite bead ------
bw = 14 * U
bx, by, lean = 45 * U, 115 * U, 48 * U
corners = [(CX - bx + lean, CY - by), (CX + bx + lean, CY - by),
           (CX + bx - lean, CY + by), (CX - bx - lean, CY + by)]
d.line(corners + [corners[0]], fill=BLACK, width=bw, joint="curve")
d.line([CX - 152 * U, CY, CX + 152 * U, CY], fill=BLACK, width=bw)

# ---------------------------------------------------------------- output ---
mark = img.resize((S, S), Image.LANCZOS)

OUT = (Path(sys.argv[1]) if len(sys.argv) > 1
       else Path(__file__).resolve().parent.parent / "data")
OUT.mkdir(parents=True, exist_ok=True)


def on_cream(size: int, inset: float) -> Image.Image:
    """The mark centred on the app's cream, filling `inset` of the canvas."""
    plate = Image.new("RGBA", (size, size), CREAM)
    m = int(size * inset)
    plate.alpha_composite(mark.resize((m, m), Image.LANCZOS),
                          ((size - m) // 2, (size - m) // 2))
    return plate


# Transparent — the in-app loading screen paints its own background.
mark.save(OUT / "ferritext_logo.png")
# Launcher icon on cream: a transparent icon picks up whatever the launcher
# happens to be showing behind it.
on_cream(512, 0.82).save(OUT / "icon.png")
# Android presplash, shown by the platform before any Python is running.
on_cream(800, 0.62).save(OUT / "presplash.png")

for _f in ("ferritext_logo.png", "icon.png", "presplash.png"):
    print("wrote", OUT / _f)
