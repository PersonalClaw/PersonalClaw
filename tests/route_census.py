"""The routes the package registers, parsed once per process and again when their source changes.

``manifest_reference._routes_from_ast`` is the census the published route reference is rendered
from: it parses every module in the package twice, once for the handlers' docstrings and once for
the route registrations, about 9 s a call on a loaded machine. Every "apps cannot" module and the
posture rail called it for itself, so a worker that ran them all parsed the package once per
module.

:func:`routes` keeps the census for the rest of the process (one copy per xdist worker, for the
session), keyed on what it was read from:

- every file ``manifest_reference._route_source_files`` names, each with its size and its
  modification time, so a file added, removed or rewritten (a planted route) is read afresh;
- the census function itself, so a census put in its place (``monkeypatch``) is asked, not this
  cache.

A key it has answered before is answered from memory again: a test that plants a route and takes
it back costs the real census nothing more. What it returns is shared by every test that asks, so
a test reads it and never changes it. ``tests/test_route_census.py`` holds each claim.
"""

from __future__ import annotations

import os
from typing import Any

from personalclaw import manifest_reference

#: Every census this process has read, by what it was read from.
_READ: dict[tuple[Any, ...], tuple[dict[str, Any], ...]] = {}


def _source() -> tuple[Any, ...]:
    """What the census reads, as it is now: the census function, and each source file's path,
    size and modification time."""
    files = []
    for path in manifest_reference._route_source_files():
        stat = os.stat(path)
        files.append((str(path), stat.st_size, stat.st_mtime_ns))
    return (manifest_reference._routes_from_ast, tuple(files))


def routes() -> tuple[dict[str, Any], ...]:
    """Every literal route the package registers (``manifest_reference._routes_from_ast``), read
    once for each state of its source."""
    key = _source()
    if key not in _READ:
        _READ[key] = tuple(manifest_reference._routes_from_ast())
    return _READ[key]
