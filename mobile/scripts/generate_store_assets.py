"""Generate `mobile/assets/` — the five source images `@capacitor/assets generate` reads.

The store icon/splash matrix itself (every iOS AppIcon slot, every Android mipmap density) is
NOT committed: it lives in `ios/` and `android/`, which are generated and gitignored
(`mobile/.gitignore`). What IS committed is this script plus its five outputs in
`mobile/assets/`, the layer `npx @capacitor/assets generate` consumes after `cap add`
(documented in `docs/maintainers/mobile-release.md`):

    icon-only.png        1024x1024 opaque   iOS app icon (Apple rejects alpha in store icons)
    icon-foreground.png  1024x1024 alpha    Android adaptive-icon foreground layer
    icon-background.png  1024x1024 opaque   Android adaptive-icon background layer
    splash.png           2732x2732 opaque   splash, light
    splash-dark.png      2732x2732 opaque   splash, dark

Brand inputs, per `docs/brand/README.md`'s own regeneration recipe: `desktop/icon.png` is the
claw alpha mask (1024px, geometry identical to `personalclaw-mark.svg`), and the coral→amber
gradient stops below are transcribed from that SVG — asserted against it at runtime, so a
brand-gradient change reds this script instead of silently shipping stale colors. Pillow is a
core dependency (`pyproject.toml`), so the repo venv runs this as-is:

    .venv/bin/python mobile/scripts/generate_store_assets.py

Two more self-checks run before anything is written: the adaptive-icon foreground must keep
every opaque pixel inside Android's 66/108 safe circle (outside it, launcher shapes clip the
mark), and the splash artwork must stay inside the central 1200px square that
`@capacitor/assets` preserves across device aspect ratios.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
BRAND_SVG = REPO_ROOT / "docs" / "brand" / "personalclaw-mark.svg"
CLAW_MASK = REPO_ROOT / "desktop" / "icon.png"
OUT_DIR = REPO_ROOT / "mobile" / "assets"

# The brand gradient, transcribed from docs/brand/personalclaw-mark.svg (diagonal, top-left to
# bottom-right) and re-asserted against that file in main(). Cream is the claw fill
# docs/brand/avatar.png uses on brand-colored ground; the dark splash ground is the coral
# family taken down to near-black — a splash needs *some* ground color, and web/'s design
# tokens are deliberately not copied here (mobile/README.md, "Safe areas").
GRADIENT_STOPS: tuple[tuple[float, str], ...] = (
    (0.00, "#c85a48"),
    (0.45, "#ff6b5b"),
    (0.75, "#ff9a7a"),
    (1.00, "#ffb454"),
)
CREAM = "#fff3e9"
SPLASH_DARK_GROUND = "#1a120f"

ICON_SIZE = 1024
SPLASH_SIZE = 2732

# How large the claw's bounding box may be, per surface. The adaptive-icon safe circle is
# 66/108 of the canvas (Android adaptive-icon spec); @capacitor/assets keeps the central
# 1200px of a 2732px splash visible on every aspect ratio.
ICON_CLAW_BOX = 660
FOREGROUND_CLAW_BOX = 560
SPLASH_CLAW_BOX = 1000
ADAPTIVE_SAFE_RADIUS = ICON_SIZE * 66 / 108 / 2  # 313.0px
SPLASH_SAFE_BOX = 1200


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _assert_stops_match_brand_svg() -> None:
    """Red instead of silently shipping stale colors when the brand gradient changes."""
    svg = BRAND_SVG.read_text(encoding="utf-8")
    svg_stops = re.findall(r'stop-color="(#[0-9a-fA-F]{6})"', svg)
    ours = [color for _, color in GRADIENT_STOPS]
    assert svg_stops == ours, (
        f"gradient stops drifted: {BRAND_SVG} has {svg_stops}, this script has {ours} — "
        "update GRADIENT_STOPS to match the brand SVG, then regenerate."
    )


def brand_gradient(size: int) -> Image.Image:
    """The brand's diagonal linear gradient at ``size``x``size``, exact per pixel."""
    stops = [(offset, _rgb(color)) for offset, color in GRADIENT_STOPS]

    def color_at(t: float) -> tuple[int, int, int]:
        for (o0, c0), (o1, c1) in zip(stops, stops[1:]):
            if t <= o1:
                f = 0.0 if o1 == o0 else (t - o0) / (o1 - o0)
                return tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))  # type: ignore
        return stops[-1][1]

    # A pixel's position along the (0,0)->(size,size) gradient axis depends only on x+y.
    diagonal = [color_at(s / (2 * (size - 1))) for s in range(2 * size - 1)]
    image = Image.new("RGB", (size, size))
    image.putdata([diagonal[x + y] for y in range(size) for x in range(size)])
    return image


def claw_alpha(box: int) -> tuple[Image.Image, int]:
    """The claw's alpha channel scaled so its content bounding box fits ``box`` pixels.

    Returns the resized full-canvas alpha and its canvas edge length. The mask's content is
    centered on its own canvas (asserted), so center-pasting the canvas centers the claw.
    """
    mask = Image.open(CLAW_MASK).convert("RGBA").split()[3]
    left, top, right, bottom = mask.getbbox()  # type: ignore[misc]
    assert abs((left + right) - mask.width) <= 2 and abs((top + bottom) - mask.height) <= 2, (
        f"{CLAW_MASK} content is no longer centered on its canvas — re-center it or teach "
        "this script to offset by the bbox center."
    )
    scale = box / max(right - left, bottom - top)
    edge = round(mask.width * scale)
    return mask.resize((edge, edge), Image.LANCZOS), edge


def paste_claw(canvas: Image.Image, fill: Image.Image, box: int) -> None:
    """Center the claw, filled with ``fill`` (a same-size image), onto ``canvas``."""
    alpha, edge = claw_alpha(box)
    layer = fill.resize((edge, edge), Image.LANCZOS).convert("RGBA")
    layer.putalpha(alpha)
    offset = ((canvas.width - edge) // 2, (canvas.height - edge) // 2)
    canvas.paste(layer, offset, layer)


def _assert_inside_adaptive_safe_circle(foreground: Image.Image) -> None:
    alpha = foreground.split()[3]
    center = foreground.width / 2 - 0.5
    limit = ADAPTIVE_SAFE_RADIUS**2
    worst = max(
        (x - center) ** 2 + (y - center) ** 2
        for y in range(foreground.height)
        for x in range(foreground.width)
        if alpha.getpixel((x, y)) > 0  # type: ignore[operator]
    )
    assert worst <= limit, (
        f"adaptive-icon foreground leaks the 66/108 safe circle (r={worst ** 0.5:.0f}px > "
        f"{ADAPTIVE_SAFE_RADIUS:.0f}px) — shrink FOREGROUND_CLAW_BOX."
    )


def build() -> dict[str, Image.Image]:
    gradient_icon = brand_gradient(ICON_SIZE)
    cream = Image.new("RGB", (ICON_SIZE, ICON_SIZE), _rgb(CREAM))

    icon_only = gradient_icon.copy()
    paste_claw(icon_only, cream, ICON_CLAW_BOX)

    icon_foreground = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    paste_claw(icon_foreground, cream, FOREGROUND_CLAW_BOX)
    _assert_inside_adaptive_safe_circle(icon_foreground)

    gradient_claw_fill = brand_gradient(SPLASH_SIZE // 2)
    splash = Image.new("RGB", (SPLASH_SIZE, SPLASH_SIZE), _rgb(CREAM))
    paste_claw(splash, gradient_claw_fill, SPLASH_CLAW_BOX)

    splash_dark = Image.new("RGB", (SPLASH_SIZE, SPLASH_SIZE), _rgb(SPLASH_DARK_GROUND))
    paste_claw(splash_dark, gradient_claw_fill, SPLASH_CLAW_BOX)

    assert SPLASH_CLAW_BOX <= SPLASH_SAFE_BOX, "splash artwork exceeds the preserved center"
    return {
        "icon-only.png": icon_only,
        "icon-foreground.png": icon_foreground,
        "icon-background.png": gradient_icon,
        "splash.png": splash,
        "splash-dark.png": splash_dark,
    }


def main() -> int:
    _assert_stops_match_brand_svg()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, image in build().items():
        path = OUT_DIR / name
        image.save(path, format="PNG", optimize=True)
        print(f"wrote {path.relative_to(REPO_ROOT)}  {image.width}x{image.height} {image.mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
