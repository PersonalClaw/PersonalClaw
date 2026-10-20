"""The source-pin census counts each way a test reads the product's source, and nothing else.

Each row plants one test module in a tree of its own under ``tmp_path`` and lists the sites the
census must find in it, by kind and literal (``tests/source_pin_census.py`` holds the rule). The
census reads files and never imports them, so the planted modules name product modules they never
load. A row whose list is empty is a form the census must not count: a pin written in a docstring,
a document read through the package, a docstring's own text, a file a test wrote.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import source_pin_census
from table_rows import row

#: Where each row's module is planted.
PLANTED = "tests/test_planted.py"

#: A module the planted tests may import from, as a shared module under tests/ would be.
SHARED = (
    "tests/pins_shared.py",
    """
    from pathlib import Path

    SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"


    def source_of(name):
        return (SRC / name).read_text(encoding="utf-8")


    def note():
        return "not source"
    """,
)

#: (what the row plants, the planted module, the sites the census must find in it).
COUNTED: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "a getsource call, and a pin on it",
        """
        import inspect

        from personalclaw import gateway


        def test_it():
            assert "start(" in inspect.getsource(gateway)
        """,
        [("getsource", ""), ("in", "start(")],
    ),
    (
        "a name bound from getsource, under in, not in and count",
        """
        import inspect

        from personalclaw import gateway


        def test_it():
            src = inspect.getsource(gateway)
            assert "start(" in src
            assert "stop(" not in src
            assert src.count("serve(") == 1
        """,
        [("getsource", ""), ("in", "start("), ("not_in", "stop("), ("count", "serve(")],
    ),
    (
        "the source index",
        """
        import source_index


        def test_it():
            text = source_index.text(source_index.REPO / "src/personalclaw/gateway.py")
            assert "start(" in text
        """,
        [("in", "start(")],
    ),
    (
        "a module's file, read",
        """
        from pathlib import Path

        from personalclaw import gateway


        def test_it():
            assert "start(" in Path(gateway.__file__).read_text(encoding="utf-8")
        """,
        [("in", "start(")],
    ),
    (
        "a module's file through str() and an aliased Path",
        """
        from pathlib import Path as _Path

        import personalclaw.gateway


        def test_it():
            text = _Path(str(personalclaw.gateway.__file__)).read_text()
            assert "start(" in text
        """,
        [("in", "start(")],
    ),
    (
        "a module's file, opened",
        """
        from personalclaw import gateway


        def test_it():
            assert "start(" in open(gateway.__file__).read()
        """,
        [("in", "start(")],
    ),
    (
        "a module's file, opened in a with",
        """
        from personalclaw import gateway


        def test_it():
            with open(gateway.__file__, encoding="utf-8") as handle:
                text = handle.read()
            assert "start(" in text
        """,
        [("in", "start(")],
    ),
    (
        "a module's sibling, and the file inspect names",
        """
        import inspect
        from pathlib import Path

        from personalclaw import gateway


        def test_it():
            assert "add_get(" in Path(gateway.__file__).with_name("routes.py").read_text()
            assert "start(" in Path(inspect.getsourcefile(gateway)).read_text()
            assert "stop(" in Path(inspect.getsourcefile(gateway) or "").read_text()
        """,
        [("in", "add_get("), ("in", "start("), ("in", "stop(")],
    ),
    (
        "a module's file held in a name",
        """
        from pathlib import Path

        from personalclaw import gateway


        def test_it():
            path = Path(gateway.__file__)
            text = path.read_text(encoding="utf-8")
            assert "start(" in text
        """,
        [("in", "start(")],
    ),
    (
        "a path under src/ from the repository's root",
        """
        from pathlib import Path

        ROOT = Path(__file__).resolve().parents[1]


        def test_it():
            text = (ROOT / "src" / "personalclaw" / "gateway.py").read_text()
            assert "start(" in text
        """,
        [("in", "start(")],
    ),
    (
        "a web source file",
        """
        from pathlib import Path

        REPO = Path(__file__).resolve().parents[1]


        def test_it():
            assert "fetch(" in (REPO / "web" / "src" / "lib" / "api.ts").read_text()
        """,
        [("in", "fetch(")],
    ),
    (
        "each file of the package, in a loop",
        """
        from pathlib import Path

        import personalclaw


        def test_it():
            for path in sorted(Path(personalclaw.__file__).parent.rglob("*.py")):
                assert "eval(" not in path.read_text(encoding="utf-8"), path
        """,
        [("not_in", "eval(")],
    ),
    (
        "source text held at module level",
        """
        from pathlib import Path

        from personalclaw import gateway

        SOURCE = Path(gateway.__file__).read_text(encoding="utf-8")


        def test_it():
            assert "start(" in SOURCE
        """,
        [("in", "start(")],
    ),
    (
        "a helper that reads the module",
        """
        from pathlib import Path

        from personalclaw import gateway


        def _source():
            return Path(gateway.__file__).read_text(encoding="utf-8")


        def test_it():
            assert "start(" in _source()
        """,
        [("in", "start(")],
    ),
    (
        "a helper that cuts the source it is handed",
        """
        import inspect

        from personalclaw import gateway


        def _body(text):
            return text.split("def run", 1)[1]


        def test_it():
            assert "start(" in _body(inspect.getsource(gateway))
            assert "start(" in _body("def run(): start()")
        """,
        [("getsource", ""), ("in", "start(")],
    ),
    (
        "a helper and a path from a shared module under tests",
        """
        import pins_shared
        from pins_shared import SRC, note, source_of


        def test_it():
            assert "start(" in source_of("gateway.py")
            assert "add_get(" in (SRC / "routes.py").read_text()
            assert "stop(" in pins_shared.source_of("gateway.py")
            assert "source" in note()
            assert "source" in pins_shared.note()
        """,
        [("in", "start("), ("in", "add_get("), ("in", "stop(")],
    ),
    (
        "text derived from source text",
        """
        import inspect
        from textwrap import dedent

        from personalclaw import gateway, routes


        def test_it(name="start"):
            src = inspect.getsource(gateway)
            assert "lowered" in src.lower()
            assert "joined" in "".join([src, inspect.getsource(routes)])
            assert "a whole line" in src.splitlines()
            assert "dedented" in dedent(src)
            assert "sliced" in src[10:]
            assert f"def {name}(" in src
            assert "added" in src + "\\n"
        """,
        [
            ("getsource", ""),
            ("getsource", ""),
            ("in", "lowered"),
            ("in", "joined"),
            ("in", "a whole line"),
            ("in", "dedented"),
            ("in", "sliced"),
            ("in", "def ("),
            ("in", "added"),
        ],
    ),
    (
        "a loop over source texts",
        """
        import inspect

        from personalclaw import gateway, routes


        def test_it():
            for text in (inspect.getsource(gateway), inspect.getsource(routes)):
                assert "start(" in text
            assert all("stop(" not in line for line in inspect.getsource(gateway).splitlines())
            sources = {"gateway": inspect.getsource(gateway)}
            for name, body in sources.items():
                assert "serve(" in body, name
        """,
        [
            ("getsource", ""),
            ("getsource", ""),
            ("getsource", ""),
            ("getsource", ""),
            ("in", "start("),
            ("not_in", "stop("),
            ("in", "serve("),
        ],
    ),
    (
        "a path a test class holds",
        """
        from pathlib import Path

        REPO = Path(__file__).resolve().parents[1]


        class TestIt:
            MODULE = REPO / "web" / "src" / "app" / "identity.tsx"

            def test_it(self):
                assert "USERNAME_MAX_LEN" in self.MODULE.read_text(encoding="utf-8")
        """,
        [("in", "USERNAME_MAX_LEN")],
    ),
]

#: (what the row plants, the planted module): forms the census must not count.
NOT_COUNTED: list[tuple[str, str]] = [
    (
        "a pin written in a docstring or a comment",
        '''
        def test_it():
            """assert "start(" in inspect.getsource(gateway)"""
            # assert "start(" in Path(gateway.__file__).read_text()
            assert True
        ''',
    ),
    (
        "a document read through the package's file",
        """
        from pathlib import Path

        import personalclaw
        from personalclaw import gateway


        def test_it():
            reference = Path(personalclaw.__file__).parent / "reference"
            assert "/api/runs" in (reference / "routes.md").read_text()
            assert "hello" in Path(gateway.__file__).with_name("phrases.json").read_text()
            prompts = Path(personalclaw.__file__).parent / "config" / "prompts"
            for name in ("chat.md", "background.md"):
                assert "schedule" not in (prompts / name).read_text(), name
        """,
    ),
    (
        "a document read from the repository",
        """
        from pathlib import Path

        import source_index

        REPO = Path(__file__).resolve().parents[1]


        def test_it():
            assert "pins" in (REPO / "docs" / "maintainers" / "testing.md").read_text()
            assert "pins" in source_index.text(REPO / "docs/maintainers/testing.md")
            assert "version" in (REPO / "src" / "personalclaw" / "defaults.json").read_text()
        """,
    ),
    (
        "a docstring's text",
        """
        import inspect
        from pathlib import Path

        from personalclaw import gateway


        def test_it():
            assert Path(gateway.__file__).read_text(), "a read with no pin on it"
            assert "Start" in inspect.getdoc(gateway.start)
            assert "gateway" in gateway.__doc__
        """,
    ),
    (
        "the test's own file, another test file, and a file the test wrote",
        """
        from pathlib import Path

        REPO = Path(__file__).resolve().parents[1]


        def test_it(tmp_path):
            assert "test_it" in Path(__file__).read_text()
            assert "def test_" in (REPO / "tests" / "test_other.py").read_text()
            planted = tmp_path / "src" / "personalclaw" / "planted.py"
            planted.parent.mkdir(parents=True)
            planted.write_text("start()")
            assert "start(" in planted.read_text()
            assert "start(" in (tmp_path / "src" / "personalclaw" / "planted.py").read_text()
        """,
    ),
    (
        "a response body",
        """
        from pathlib import Path

        from personalclaw import gateway


        def test_it(client):
            assert Path(gateway.__file__).read_text(), "a read with no pin on it"
            resp = client.get("/api/runs")
            assert "runs" in resp.text
            assert resp.text.count("run") == 1
        """,
    ),
    (
        "a module's file name, read nowhere",
        """
        import inspect

        from personalclaw import gateway


        def test_it():
            assert "gateway" in gateway.__file__
            assert inspect.getsourcefile(gateway).endswith(".py")
        """,
    ),
]


def _plant(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def _found(root: Path, rel: str = PLANTED) -> list[tuple[str, str]]:
    return sorted((s.kind, s.literal) for s in source_pin_census.sites(root) if s.path == rel)


def test_each_way_a_test_reads_the_products_source_is_counted(tmp_path: Path, subtests) -> None:
    assert len(COUNTED) == 19, "a planted form was dropped from the table"
    for n, (case, module, expected) in enumerate(COUNTED):
        with row(subtests, case=case):
            root = tmp_path / f"counted-{n}"
            _plant(root, *SHARED)
            _plant(root, PLANTED, module)
            assert _found(root) == sorted(expected)
            assert _found(root, SHARED[0]) == [], "the shared module holds no pin of its own"


def test_what_reads_no_product_source_is_not_counted(tmp_path: Path, subtests) -> None:
    assert len(NOT_COUNTED) == 7, "a planted form was dropped from the table"
    for n, (case, module) in enumerate(NOT_COUNTED):
        with row(subtests, case=case):
            root = tmp_path / f"not-counted-{n}"
            _plant(root, PLANTED, module)
            assert _found(root) == []


def test_a_needle_that_is_not_a_literal_is_not_a_literal_pin(tmp_path: Path) -> None:
    """The rule counts a compare by its literal: a needle taken from a table is not one, though
    the getsource call it reads still is."""
    _plant(
        tmp_path,
        PLANTED,
        """
        import inspect

        from personalclaw import gateway

        NEEDLES = ("start(", "stop(")


        def test_it():
            src = inspect.getsource(gateway)
            for needle in NEEDLES:
                assert needle in src
        """,
    )
    assert _found(tmp_path) == [("getsource", "")]


def test_a_pin_inside_tests_rails_is_not_counted(tmp_path: Path) -> None:
    """An AST rail in tests/rails may read the source, with its reason in its docstring."""
    pin = """
    import inspect

    from personalclaw import gateway


    def test_it():
        assert "start(" in inspect.getsource(gateway)
    """
    _plant(tmp_path, "tests/rails/test_gateway_tree.py", pin)
    _plant(tmp_path, PLANTED, pin)
    assert {s.path for s in source_pin_census.sites(tmp_path)} == {PLANTED}


def test_the_measure_counts_each_half_by_kind(tmp_path: Path) -> None:
    """Both halves in one total: the getsource sites (the CI contract's own rule) and the
    literal pins, each kind counted apart."""
    _plant(
        tmp_path,
        PLANTED,
        """
        import inspect
        from pathlib import Path

        from personalclaw import gateway

        READ = inspect.getsource


        def test_it():
            src = Path(gateway.__file__).read_text()
            assert "start(" in src and "stop(" not in src
            assert src.count("serve(") == 1
            assert "run(" in inspect.getsource(gateway)
        """,
    )
    assert source_pin_census.measure(tmp_path) == {
        "sites": 6,
        "getsource": 2,
        "in": 2,
        "not_in": 1,
        "count": 1,
    }


@pytest.mark.parametrize("kind", source_pin_census.KINDS)
def test_every_kind_is_a_key_of_the_measure(tmp_path: Path, kind: str) -> None:
    _plant(tmp_path, PLANTED, "def test_it():\n    assert True\n")
    assert source_pin_census.measure(tmp_path)[kind] == 0
