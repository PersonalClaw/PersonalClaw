"""``tests/route_census.py``: the route census is parsed once per process, and again for a change.

The family modules and the posture rail read every route the package registers from
``route_census.routes()``, which keeps ``manifest_reference._routes_from_ast``'s answer for the
process. A cache that kept answering after the source changed would hide a route added tomorrow
from every census built on it, so each way the source can change is planted here: a source file
rewritten (in size, or only in time), one added, one taken away, and the census function itself
put in another's place. A planted source is read through ``_route_source_files``, the list the
census reads, so nothing is written into the package.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import route_census

from personalclaw import manifest_reference

pytestmark = pytest.mark.security


def _registers(*paths: str) -> str:
    """A route module that registers a GET at each of *paths*."""
    lines = ["def register(app):"]
    lines += [f"    app.router.add_get({path!r}, api_planted)" for path in paths]
    lines += ["", "", "async def api_planted(request):", '    """A planted read."""']
    return "\n".join(lines) + "\n"


def _paths(routes: tuple[dict, ...]) -> list[str]:
    return [r["path"] for r in routes]


@pytest.mark.timeout(40)
def test_the_census_is_read_once_for_the_process() -> None:
    first = route_census.routes()
    assert len(first) >= 500, f"only {len(first)} routes — vacuous"
    assert ("GET", "/api/chat/sessions/{session}") in {(r["method"], r["path"]) for r in first}
    # Asked again, it is answered without parsing anything.
    with patch.object(manifest_reference.ast, "parse", side_effect=AssertionError("parsed")):
        assert route_census.routes() is first


def test_a_planted_route_is_read_afresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    planted = tmp_path / "planted_routes.py"
    planted.write_text(_registers("/api/planted/one"), encoding="utf-8")
    files = [planted]
    monkeypatch.setattr(manifest_reference, "_route_source_files", lambda: list(files))

    first = route_census.routes()
    assert _paths(first) == ["/api/planted/one"]
    assert first[0]["summary"] == "A planted read.", first
    assert route_census.routes() is first, "asked again, the same answer"

    # Rewritten with a second route: a file of another size.
    planted.write_text(_registers("/api/planted/one", "/api/planted/two"), encoding="utf-8")
    assert _paths(route_census.routes()) == ["/api/planted/one", "/api/planted/two"]

    # Rewritten to the same size, a second later: only its time tells the two apart.
    before = planted.stat()
    planted.write_text(_registers("/api/planted/one", "/api/planted/owt"), encoding="utf-8")
    assert planted.stat().st_size == before.st_size
    os.utime(planted, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    assert _paths(route_census.routes()) == ["/api/planted/one", "/api/planted/owt"]

    # A second module that registers a route.
    another = tmp_path / "another_routes.py"
    another.write_text(_registers("/api/planted/three"), encoding="utf-8")
    files.append(another)
    assert "/api/planted/three" in _paths(route_census.routes())

    # And taken away again.
    files.remove(another)
    assert "/api/planted/three" not in _paths(route_census.routes())


def test_a_source_it_read_before_is_answered_from_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A test that plants a route and takes it back costs the real census nothing more."""
    planted = tmp_path / "planted_routes.py"
    planted.write_text(_registers("/api/planted/one"), encoding="utf-8")
    files = [planted]
    monkeypatch.setattr(manifest_reference, "_route_source_files", lambda: list(files))
    first = route_census.routes()
    another = tmp_path / "another_routes.py"
    another.write_text(_registers("/api/planted/two"), encoding="utf-8")
    files.append(another)
    assert route_census.routes() is not first
    files.remove(another)
    with patch.object(manifest_reference.ast, "parse", side_effect=AssertionError("parsed")):
        assert route_census.routes() is first


def test_a_census_put_in_its_place_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planted = tmp_path / "planted_routes.py"
    planted.write_text(_registers("/api/planted/one"), encoding="utf-8")
    monkeypatch.setattr(manifest_reference, "_route_source_files", lambda: [planted])
    assert _paths(route_census.routes()) == ["/api/planted/one"]
    stand_in = [{"method": "POST", "path": "/api/planted/stand-in", "summary": ""}]
    monkeypatch.setattr(manifest_reference, "_routes_from_ast", lambda: list(stand_in))
    assert _paths(route_census.routes()) == ["/api/planted/stand-in"]
