#!/usr/bin/env python3
"""Render brand/icon.png and brand/icon@2x.png from brand_src/icon.svg.

Home Assistant serves brand images for custom integrations from a local
``brand/`` directory since HA 2026.3. The PNGs are build artifacts --
edit ``brand_src/icon.svg`` and re-run this script rather than editing
them directly.

Requirements from the Home Assistant brands specification:

- PNG, square (1:1), 256x256 normal and 512x512 hDPI.
- Transparency preferred.
- Trimmed: minimal empty space around the subject.
- Custom integrations must not use Home Assistant branded imagery.

No logo.png is produced. The icon is square, and HA falls back to the
icon when a logo is absent.

Usage:
    pip install cairosvg pillow
    python tools/render_brand.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import cairosvg
    from PIL import Image
except ImportError:
    sys.exit("Install the render dependencies first: pip install cairosvg pillow")

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "brand_src" / "icon.svg"
OUT_DIR = ROOT / "custom_components" / "weather_uploader" / "brand"

# (filename, edge length in pixels) per the brands specification.
TARGETS = [("icon.png", 256), ("icon@2x.png", 512)]

# Render oversampled, then trim and downscale. Trimming to the artwork's
# bounding box is what satisfies the "minimum empty space" requirement,
# and doing it before the final resize keeps edges clean.
SUPERSAMPLE = 1024


def render() -> None:
    """Rasterise the SVG, trim it square, and write both PNG sizes."""
    if not SOURCE.is_file():
        sys.exit(f"Missing source: {SOURCE}")

    tmp = ROOT / "brand_src" / ".render.png"
    cairosvg.svg2png(
        url=str(SOURCE),
        write_to=str(tmp),
        output_width=SUPERSAMPLE,
        output_height=SUPERSAMPLE,
    )

    image = Image.open(tmp).convert("RGBA")
    bbox = image.getbbox()
    if bbox is None:
        sys.exit("Rendered image is empty")
    image = image.crop(bbox)

    # Pad the trimmed artwork back to a square. The spec requires a 1:1
    # aspect ratio, so the shorter axis gets symmetric transparent
    # padding rather than the artwork being stretched.
    side = max(image.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(
        image, ((side - image.width) // 2, (side - image.height) // 2)
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, size in TARGETS:
        out = square.resize((size, size), Image.LANCZOS)
        path = OUT_DIR / name
        out.save(path, "PNG", optimize=True)
        print(f"wrote {path.relative_to(ROOT)} ({size}x{size})")

    tmp.unlink()


if __name__ == "__main__":
    render()
