"""The route reference reads each package file once.

``manifest_reference._routes_from_ast`` pairs every literal route with the summary its handler's
docstring gives. It parsed the whole package twice for that, once for the docstrings and once for
the routes, and every test that renders the reference or reads the route census paid both: on a
loaded CI runner that was 15 to 35 s a render. One pass now collects both from each file, and the
summaries are read once every file's docstrings are known, so a route whose handler is defined in
a later file still gets its summary.

These run the census over a small package of their own, so they hold what each file is read for
without the whole tree. The census over the real package is held byte for byte by the checked-in
references (``tests/test_agent_reference.py``, ``tests/rails/test_docs_api_reference.py``).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from personalclaw import manifest_reference

_ROUTES = '''
from handlers import api_later


def register(app):
    app.router.add_get("/api/later", api_later)
    app.router.add_post("/api/here", api_here)
    app.router.add_get("/api/here", api_other)
    app.router.add_route("*", "/api/any", handlers.api_any)


async def api_here(request):
    """POST /api/here — write one here."""
'''

_HANDLERS = '''
async def api_later(request):
    """Read the later one."""


async def api_any(request):
    """Any method at all."""


async def api_here(request):
    """A second definition of a name: the first one read keeps its docstring."""


async def api_other(request):
    """GET /api/here — read one here."""
'''


@pytest.fixture
def package(tmp_path, monkeypatch) -> list[Path]:
    """Two files, read in this order: the route table, then the handlers it imports."""
    files = [tmp_path / "a_routes.py", tmp_path / "b_handlers.py"]
    files[0].write_text(_ROUTES, encoding="utf-8")
    files[1].write_text(_HANDLERS, encoding="utf-8")
    monkeypatch.setattr(manifest_reference, "_route_source_files", lambda: list(files))
    return files


def test_each_file_is_parsed_once(package, monkeypatch):
    parsed: list[str] = []
    parse = ast.parse

    def counting(source, *args, **kwargs):
        parsed.append(source)
        return parse(source, *args, **kwargs)

    monkeypatch.setattr(manifest_reference.ast, "parse", counting)
    manifest_reference._routes_from_ast()
    assert sorted(parsed) == sorted(
        p.read_text(encoding="utf-8") for p in package
    ), f"{len(parsed)} parses of {len(package)} files"


def test_each_route_has_the_summary_its_handler_gives(package):
    routes = {(r["method"], r["path"]): r["summary"] for r in manifest_reference._routes_from_ast()}
    assert routes == {
        # Defined in the file after the one that registers it.
        ("GET", "/api/later"): "Read the later one.",
        # The first definition of the name, in the route table's own file.
        ("POST", "/api/here"): "write one here.",
        # Registered after a route with the same path and another method.
        ("GET", "/api/here"): "read one here.",
        ("*", "/api/any"): "Any method at all.",
    }


def test_a_route_registered_twice_keeps_its_first_handler(package):
    package[1].write_text(
        _HANDLERS + '\n\nasync def api_again(request):\n    """The second registration."""\n',
        encoding="utf-8",
    )
    package.append(package[1].with_name("c_again.py"))
    package[2].write_text(
        'def register(app):\n    app.router.add_get("/api/later", api_again)\n', encoding="utf-8"
    )
    later = [r for r in manifest_reference._routes_from_ast() if r["path"] == "/api/later"]
    assert [r["summary"] for r in later] == ["Read the later one."]
