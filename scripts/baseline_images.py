#!/usr/bin/env python3
"""Compress the redesign baseline's snapshots and measure their ink and coral, one JSON line each.

``docs/screenshots/baseline.mjs`` captures every screen as a full-colour PNG into a scratch
folder, then runs this on it. Two things happen per snapshot:

* **Figures, read off the pixels.** ``ink`` is the share of the viewport that is not the
  page's own background (the most common colour), so it counts text, borders, fills and
  icons alike: the visual dimension of the density analysis. ``coral`` is the share in the
  coral-and-red hue family, held to the brief's "coral under ~10% of a screen"
  (``web/REDESIGN.md`` §3, rule 7). The danger red sits in the same hue band, so a screen
  showing an error counts it too; the report reads the number with its snapshot.
* **A small file to commit.** The snapshot is reduced to a 128-colour palette without
  dithering (UI flats compress to almost nothing, and text keeps its anti-aliasing steps)
  and saved optimized. The publication-hygiene rail caps every tracked binary at 512 KB; a
  snapshot over ``--max-bytes`` fails here, before it is ever staged.

Run by the capture script; by hand only to re-compress a folder::

    .venv/bin/python scripts/baseline_images.py RAW_DIR OUT_DIR

It prints ``{"file", "ink", "coral", "bytes"}`` per PNG, in name order.
"""

from __future__ import annotations

import argparse
import colorsys
import json
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

#: How far a pixel's channels may stray from the background and still read as background.
#: Anti-aliased hairlines land above it, the canvas's own noise below.
_INK_THRESHOLD = 14

#: The coral-and-red hue band, in degrees, and the floors that keep a warm grey out of it.
_CORAL_HUES = ((345.0, 360.0), (0.0, 18.0))
_CORAL_MIN_SATURATION = 0.45
_CORAL_MIN_VALUE = 0.55

#: Palette size of the committed snapshot. 64 bands soft text; 256 buys nothing visible.
_COLOURS = 128


def _is_coral(rgb: tuple[int, int, int]) -> bool:
    hue, sat, val = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    degrees = hue * 360
    in_band = any(low <= degrees <= high for low, high in _CORAL_HUES)
    return in_band and sat >= _CORAL_MIN_SATURATION and val >= _CORAL_MIN_VALUE


def measure(image: Image.Image) -> dict[str, float]:
    """The ink and coral shares of one RGB snapshot, each in ``[0, 1]``."""
    counts = Counter(image.getdata())
    total = sum(counts.values())
    background = counts.most_common(1)[0][0]
    ink = 0
    coral = 0
    for rgb, n in counts.items():
        if max(abs(a - b) for a, b in zip(rgb, background)) > _INK_THRESHOLD:
            ink += n
        if _is_coral(rgb):
            coral += n
    return {"ink": round(ink / total, 4), "coral": round(coral / total, 4)}


def compress(image: Image.Image, out: Path) -> int:
    """Write the palette PNG and return its size in bytes."""
    quantized = image.quantize(
        colors=_COLOURS, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    )
    quantized.save(out, format="PNG", optimize=True)
    return out.stat().st_size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("raw", type=Path, help="folder of full-colour PNG snapshots")
    parser.add_argument("out", type=Path, help="folder the compressed snapshots go to")
    parser.add_argument("--max-bytes", type=int, default=262_144, help="refuse a larger snapshot")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    too_big = []
    for path in sorted(args.raw.glob("*.png")):
        with Image.open(path) as opened:
            image = opened.convert("RGB")
        figures = measure(image)
        size = compress(image, args.out / path.name)
        if size > args.max_bytes:
            too_big.append(f"{path.name}: {size} bytes")
        print(json.dumps({"file": path.name, **figures, "bytes": size}), flush=True)
    if too_big:
        print("over --max-bytes: " + ", ".join(too_big), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
