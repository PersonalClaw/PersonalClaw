"""Deterministic offline stub image-gen provider — validation/dev ONLY.

Gated by ``PCLAW_IMAGE_GEN_STUB=1`` (see registry._register_stub_provider); never
active in a normal run. Returns a tiny solid-color PNG (color derived from the
prompt so different prompts yield visibly different images) with NO network call,
so the full generate/edit/artifact/render path can be exercised repeatedly for
free. Edit returns a different shade so an edit is visibly distinct from its source.
"""

from __future__ import annotations

import base64
import hashlib
import struct
import zlib
from typing import Any

from personalclaw.image_gen.provider import ImageGenModel, ImageGenProvider, ImageResult


def _solid_png(rgb: tuple[int, int, int], size: int = 64) -> bytes:
    """A minimal valid solid-color PNG (no Pillow dependency)."""
    r, g, b = rgb
    # one row: filter byte 0 + size*RGB
    row = b"\x00" + bytes(rgb) * size
    raw = row * size
    comp = zlib.compress(raw, 9)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    return (
        b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", comp) + _chunk(b"IEND", b"")
    )


def _color_for(text: str) -> tuple[int, int, int]:
    h = hashlib.sha256(text.encode()).digest()
    return (h[0], h[1], h[2])


class StubImageProvider(ImageGenProvider):
    """Offline deterministic image generator for validation."""

    @property
    def name(self) -> str:
        return "stub"

    @property
    def display_name(self) -> str:
        return "Stub (offline test image)"

    async def is_available(self) -> bool:
        return True

    async def list_models(self) -> list[ImageGenModel]:
        from personalclaw.image_gen.registry import active_image_gen

        resolved = active_image_gen()
        active = resolved[1] if resolved and resolved[0].name == "stub" else ""
        return [
            ImageGenModel(
                name="stub-1",
                description="Deterministic offline test image",
                sizes=["64x64"],
                supports_edit=True,
                downloaded=True,
                active=active == "stub-1",
            )
        ]

    async def generate(
        self,
        prompt: str,
        *,
        model: str = "",
        size: str = "",
        n: int = 1,
        **opts: Any,
    ) -> list[ImageResult]:
        png = _solid_png(_color_for(prompt))
        return [
            ImageResult(
                b64=base64.b64encode(png).decode(),
                mime="image/png",
                revised_prompt=f"stub render of: {prompt}",
            )
        ]

    async def edit(
        self,
        prompt: str,
        *,
        source_image: str,
        mask: str = "",
        model: str = "",
        size: str = "",
        n: int = 1,
        **opts: Any,
    ) -> list[ImageResult]:
        # A distinct shade (prompt+"edit") so an edit is visibly different from source.
        png = _solid_png(_color_for(prompt + "::edit"))
        return [ImageResult(b64=base64.b64encode(png).decode(), mime="image/png")]
