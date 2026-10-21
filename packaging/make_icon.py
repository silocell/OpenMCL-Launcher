"""Generate the application icon in every format the build needs.

Run this only when the icon design changes; the results are committed.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "icons"
BG_TOP = (82, 168, 113)
BG_BOTTOM = (54, 122, 82)
GLYPH = (246, 251, 247)
SIZES = [16, 24, 32, 48, 64, 128, 256, 512, 1024]


def render(size: int) -> Image.Image:
    # Draw at 4x and downsample; Pillow has no antialiased vector drawing.
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    gradient = Image.new("RGBA", (1, s))
    for y in range(s):
        t = y / max(1, s - 1)
        gradient.putpixel(
            (0, y),
            tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)) + (255,),
        )
    gradient = gradient.resize((s, s))

    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=255
    )
    img.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(img)
    # A play triangle inside a ring: generic launcher iconography.
    ring = int(s * 0.085)
    inset = s * 0.2
    draw.ellipse((inset, inset, s - inset, s - inset), outline=GLYPH, width=ring)

    cx, cy = s / 2, s / 2
    r = s * 0.155
    draw.polygon(
        [(cx - r * 0.62, cy - r), (cx - r * 0.62, cy + r), (cx + r * 0.92, cy)],
        fill=GLYPH,
    )
    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    frames = {size: render(size) for size in SIZES}

    frames[512].save(OUT / "openmcl.png")
    frames[256].save(
        OUT / "openmcl.ico",
        format="ICO",
        sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)],
    )

    # .icns needs an iconset directory; iconutil (macOS) or Pillow can build it.
    iconset = OUT / "openmcl.iconset"
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        frames[size].save(iconset / f"icon_{size}x{size}.png")
        frames[min(size * 2, 1024)].save(iconset / f"icon_{size}x{size}@2x.png")
    try:
        frames[1024].save(OUT / "openmcl.icns", format="ICNS")
    except (OSError, ValueError) as exc:
        print(f"skipped .icns ({exc}); macOS builds fall back to the .png")

    print(f"wrote icons to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
