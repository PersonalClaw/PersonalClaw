"""How long a response header line every ``aiohttp`` session in PersonalClaw reads.

aiohttp refuses a response whose status line or any header line is over 8,190 bytes (its
``max_line_size`` and ``max_field_size``), with a 400 the caller sees as the request failing.
GitHub's redirect for a release download carried a ``content-security-policy`` header of 9,243
bytes on 2026-10-23, so every code-map grammar fetched on a cold cache was refused: the code map
could not parse a language it had not fetched before, and main's suite went red on any shard whose
grammar cache missed (#3905). Each session takes :data:`RESPONSE_HEADER_LINE_MAX`, so a header that
size reads, and one far past it is still refused.
"""

from __future__ import annotations

from typing import Final

#: A response header line up to 32 KiB: three and a half times the CSP header GitHub sent, and
#: still a bound, so a server cannot make a session buffer a header without end. Every
#: ``aiohttp.ClientSession`` takes it as both ``max_line_size`` and ``max_field_size``: aiohttp
#: holds a response to both, and a single long header can trip either.
RESPONSE_HEADER_LINE_MAX: Final = 32 * 1024

__all__ = ["RESPONSE_HEADER_LINE_MAX"]
