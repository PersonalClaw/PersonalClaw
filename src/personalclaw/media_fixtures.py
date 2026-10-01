"""Tiny media made with the standard library: a solid-colour PNG and a sine-tone WAV.

A model's Test (``providers.model_test``) hands a model one small real input, and the offline
image stub (``image_gen.stub_provider``) answers with one. Both are generated rather than
committed as binaries, so the wheel carries no media blobs and every input is the same each time.
"""

from __future__ import annotations

import math
import struct
import wave
import zlib


def solid_png(rgb: tuple[int, int, int], size: int = 64) -> bytes:
    """A valid ``size``×``size`` PNG of one colour (8-bit RGB, no Pillow needed)."""
    row = b"\x00" + bytes(rgb) * size  # filter byte 0, then the row's pixels
    raw = row * size

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def write_tone_wav(path: str) -> None:
    """Write half a second of a 220 Hz tone to ``path``: 16 kHz, mono, 16-bit — a clip every
    speech engine decodes, with no speech in it."""
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        frames = bytearray()
        for i in range(8000):
            frames += struct.pack("<h", int(12000 * math.sin(2 * math.pi * 220 * i / 16000)))
        w.writeframes(bytes(frames))
