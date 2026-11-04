"""Draw the app icons for the installed web app (web/icons/): the camera reticle the AR view is aimed with, inside a compass
ring whose north tick is the accent colour. That is the whole idea of the app: directions, remembered.

    python scripts/make_icons.py

Writes icon-192.png and icon-512.png (purpose "any", rounded by the launcher) and maskable-512.png (the same mark kept inside
the central 80% safe zone, for launchers that cut their own shape).
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parents[1] / "web" / "icons"
BG = (5, 6, 8)
INK = (242, 244, 243)
ACCENT = (138, 164, 255)
SS = 4  # drawn this many times larger, then scaled down: smooth edges without an anti-aliasing option


def mark(size: int, scale: float) -> Image.Image:
    """The icon at `size` px; `scale` is how much of the canvas the ring spans (1.0 = edge to edge)."""
    S = size * SS
    img = Image.new("RGB", (S, S), BG)
    glow = Image.new("RGB", (S, S), BG)  # a soft accent light from above, like the app's background
    g = ImageDraw.Draw(glow)
    g.ellipse((S * 0.15, -S * 0.25, S * 0.85, S * 0.55), fill=(26, 32, 58))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(S * 0.12)), 1.0)
    d = ImageDraw.Draw(img)
    c, R = S / 2, S / 2 * scale * 0.78
    w = max(2, round(S * 0.012))
    d.ellipse((c - R, c - R, c + R, c + R), outline=(70, 76, 88), width=w)  # compass ring
    import math
    for i in range(36):  # ticks every 10 degrees, longer every 90
        a = math.radians(i * 10 - 90)
        major = i % 9 == 0
        r0 = R - (S * 0.055 if major else S * 0.03)
        col = ACCENT if i == 0 else ((200, 204, 212) if major else (90, 96, 108))
        d.line((c + r0 * math.cos(a), c + r0 * math.sin(a), c + R * math.cos(a), c + R * math.sin(a)), fill=col,
               width=w * (3 if i == 0 else 2 if major else 1))
    # the reticle: four short strokes around a gap, as the camera view draws it
    gap, ln, lw = S * 0.09 * scale, S * 0.12 * scale, max(3, round(S * 0.032 * scale))
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        x0, y0 = c + dx * gap, c + dy * gap
        x1, y1 = c + dx * (gap + ln), c + dy * (gap + ln)
        d.line((x0, y0, x1, y1), fill=INK, width=lw)
        for x, y in ((x0, y0), (x1, y1)):  # round caps
            d.ellipse((x - lw / 2, y - lw / 2, x + lw / 2, y + lw / 2), fill=INK)
    r = S * 0.035 * scale
    d.ellipse((c - r, c - r, c + r, c + r), fill=ACCENT)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    mark(192, 1.0).save(OUT / "icon-192.png", optimize=True)
    mark(512, 1.0).save(OUT / "icon-512.png", optimize=True)
    mark(512, 0.74).save(OUT / "maskable-512.png", optimize=True)
    print("wrote", ", ".join(p.name for p in sorted(OUT.glob("*.png"))))


if __name__ == "__main__":
    main()
