"""Render frontend/logo.svg onto a #1a1612 background as icon-192.png and icon-512.png.

Usage: python scripts/gen_icons.py
Requires: pip install cairosvg pillow
"""
from io import BytesIO
from pathlib import Path
import cairosvg
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
SVG = FRONTEND / "logo.svg"
BG = (0x1a, 0x16, 0x12, 0xff)
LOGO_FRAC = 0.66


def build(size: int) -> None:
    logo_size = round(size * LOGO_FRAC)
    png_bytes = cairosvg.svg2png(
        url=str(SVG), output_width=logo_size, output_height=logo_size
    )
    logo = Image.open(BytesIO(png_bytes)).convert("RGBA")
    canvas = Image.new("RGBA", (size, size), BG)
    offset = (size - logo_size) // 2
    canvas.alpha_composite(logo, (offset, offset))
    canvas.convert("RGB").save(FRONTEND / f"icon-{size}.png", "PNG")


if __name__ == "__main__":
    build(192)
    build(512)
    print("wrote", FRONTEND / "icon-192.png", FRONTEND / "icon-512.png")
