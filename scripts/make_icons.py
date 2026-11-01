#!/usr/bin/env python3
"""
Generate the Artemis app icons into ``static/icons/`` (no external assets).

Design: a moonlight crescent inside a tilted orbital ring on the UI's deep
navy background. Rendered at 4x and downsampled for clean anti-aliasing.

Usage (Pillow is available in venv-openvino):
    venv-openvino/bin/python scripts/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT_DIR = Path(__file__).resolve().parent.parent / "static" / "icons"

BG = (4, 7, 13, 255)  # --bg
BG_GLOW = (18, 40, 66, 255)
MOON_LIGHT = (231, 242, 255)  # --text
MOON_EDGE = (123, 208, 255)  # --primary
RING = (87, 217, 255)  # --primary-strong

MASTER = 2048


def _radial_background(size: int, rounded: bool) -> Image.Image:
    """Navy background with a soft center glow; optionally a rounded square."""
    glow = Image.new("RGBA", (size, size), BG)
    draw = ImageDraw.Draw(glow)
    r = int(size * 0.42)
    c = size // 2
    draw.ellipse((c - r, c - r, c + r, c + r), fill=BG_GLOW)
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.12))

    if not rounded:
        return glow

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=int(size * 0.22), fill=255
    )
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(glow, (0, 0), mask)
    return out


def _crescent(size: int, scale: float) -> Image.Image:
    """Crescent moon as an RGBA layer with a vertical light-to-cyan gradient."""
    c = size / 2
    r = size * 0.27 * scale
    disc = Image.new("L", (size, size), 0)
    ImageDraw.Draw(disc).ellipse((c - r, c - r, c + r, c + r), fill=255)

    # Offset disc carves out the crescent.
    off_x, off_y, cr = r * 0.48, -r * 0.28, r * 0.9
    cut = Image.new("L", (size, size), 0)
    ImageDraw.Draw(cut).ellipse(
        (c + off_x - cr, c + off_y - cr, c + off_x + cr, c + off_y + cr), fill=255
    )
    shape = ImageChops.subtract(disc, cut)

    gradient = Image.new("RGBA", (size, size))
    gdraw = ImageDraw.Draw(gradient)
    for y in range(size):
        t = y / (size - 1)
        color = tuple(
            int(MOON_LIGHT[i] * (1 - t) + MOON_EDGE[i] * t) for i in range(3)
        )
        gdraw.line((0, y, size, y), fill=(*color, 255))

    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    layer.paste(gradient, (0, 0), shape)

    halo = Image.new("RGBA", (size, size), (*MOON_EDGE, 0))
    halo.putalpha(shape.filter(ImageFilter.GaussianBlur(size * 0.03)).point(lambda a: a * 0.6))
    return Image.alpha_composite(halo, layer)


def _ring(size: int, scale: float) -> Image.Image:
    """Thin tilted orbital ring with a small 'satellite' dot."""
    ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ring)
    c = size / 2
    rx, ry = size * 0.40 * scale, size * 0.15 * scale
    width = max(2, int(size * 0.014 * scale))
    draw.ellipse((c - rx, c - ry, c + rx, c + ry), outline=(*RING, 210), width=width)
    dot = size * 0.028 * scale
    dx, dy = c + rx * 0.80, c - ry * 0.60
    draw.ellipse((dx - dot, dy - dot, dx + dot, dy + dot), fill=(*MOON_LIGHT, 255))
    ring = ring.rotate(-24, resample=Image.Resampling.BICUBIC, center=(c, c))

    glow = ring.filter(ImageFilter.GaussianBlur(size * 0.012))
    return Image.alpha_composite(glow, ring)


def render(rounded: bool, scale: float) -> Image.Image:
    base = _radial_background(MASTER, rounded)
    base = Image.alpha_composite(base, _crescent(MASTER, scale))
    return Image.alpha_composite(base, _ring(MASTER, scale))


def save(img: Image.Image, name: str, size: int) -> None:
    out = img.resize((size, size), Image.Resampling.LANCZOS)
    out.save(OUT_DIR / name, optimize=True)
    print(f"wrote static/icons/{name} ({size}x{size})")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    standard = render(rounded=True, scale=1.0)
    # Maskable icons get cropped to a circle by Android: full-bleed background,
    # artwork inside the central 80% safe zone.
    maskable = render(rounded=False, scale=0.78)
    # iOS applies its own rounding, so give it a full-bleed square.
    apple = render(rounded=False, scale=0.9)

    save(standard, "icon-192.png", 192)
    save(standard, "icon-512.png", 512)
    save(maskable, "icon-maskable-512.png", 512)
    save(apple, "apple-touch-icon.png", 180)

    favicon = standard.resize((256, 256), Image.Resampling.LANCZOS)
    favicon.save(
        OUT_DIR.parent / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)]
    )
    print("wrote static/favicon.ico")


if __name__ == "__main__":
    main()
