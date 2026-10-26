"""The guard on #3927's last Done-when line (``tests/collection_parse_guard.py``) can fail.

Each case collects a planted tree in a fresh process with the guard loaded, as breadth's
``collection-parses`` leg collects the whole suite. The tree has files under two of the index's
roots (``src/personalclaw/planted.py``, ``web/src/generate.py``) and a test module. A parse of
such a file during collection must fail the guard, whoever makes it (our code, or the standard
library for it) and wherever in collection it falls (a conftest, a test module, a conftest's
last collection-finish hook or its wrapper), unless one of its calling frames is one of the two
excused functions: pytest's assertion rewrite, or Hypothesis's own constants scan. The guard
names each such parse and the frame that made it. A function of the same name in another module
is not excused, and the guard names those two and no other. The controls that pass are
Hypothesis's real scan reading the planted file and the assertion rewrite loading a root module
named as a test, so each excusal is shown to fire. A clean collection that errors or collects
nothing is still red, the guard refuses more than one process, and on the real tree its walks
keep their floors.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import nested_pytest
import pytest

TESTS = Path(__file__).resolve().parent

_PLANTED = 'LIMIT = 4127\nNAME = "planted constant"\n'

#: A helper outside every root that parses the planted file, the way a conftest-level import
#: would: from a function of its own, from one named as Hypothesis's scan is, with the file name
#: left off (its text or its bytes), with the file's name on a text that is not the file's, or
#: with that name relative to the repository, as the source index names its own parses. A file
#: under another root, ``web/src``, is parsed with no name and under its name on another text.
_READER = """
import ast
from pathlib import Path

PLANTED = Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "planted.py"
SCRIPT = Path(__file__).resolve().parents[1] / "web" / "src" / "generate.py"


def _get_local_constants():
    return ast.parse(PLANTED.read_text(encoding="utf-8"), filename=str(PLANTED))


def parse():
    return ast.parse(PLANTED.read_text(encoding="utf-8"), filename=str(PLANTED))


def nameless():
    return ast.parse(PLANTED.read_text(encoding="utf-8"))


def nameless_bytes():
    return ast.parse(PLANTED.read_bytes())


def edited():
    return ast.parse(PLANTED.read_text(encoding="utf-8") + "EDITED = 1\\n", filename=str(PLANTED))


def relative():
    return ast.parse("EDITED = 1\\n", filename="src/personalclaw/planted.py")


def script_edited():
    return ast.parse("EDITED = 1\\n", filename=str(SCRIPT))


def nameless_script():
    return ast.parse(SCRIPT.read_text(encoding="utf-8"))
"""

_TEST = "def test_one():\n    assert True\n"

#: A test module that parses the planted file as pytest imports it.
_TEST_THAT_PARSES = "import reader\n\nreader.parse()\n\n\n" + _TEST

_GIVEN = textwrap.dedent("""
    from hypothesis import given, strategies as st


    @given(st.integers())
    def test_any_integer(n):
        assert isinstance(n, int)
    """)


def _tree(root: Path, conftest: str, test: str = _TEST) -> None:
    (root / "src" / "personalclaw").mkdir(parents=True)
    (root / "src" / "personalclaw" / "planted.py").write_text(_PLANTED, encoding="utf-8")
    (root / "web" / "src").mkdir(parents=True)
    (root / "web" / "src" / "generate.py").write_text("GENERATED = 4127\n", encoding="utf-8")
    (root / "helpers").mkdir()
    (root / "helpers" / "reader.py").write_text(_READER, encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "conftest.py").write_text(textwrap.dedent(conftest), encoding="utf-8")
    (root / "tests" / "test_planted.py").write_text(test, encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")


_IMPORT_HELPERS = """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helpers"))
"""


def _collect(root: Path, *, workers: str = "0") -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "COV_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(TESTS), env.get("PYTHONPATH", "")])
    # A cold store, as on a CI runner: Hypothesis's scan then parses the planted file itself.
    env["HYPOTHESIS_STORAGE_DIRECTORY"] = str(root / ".hypothesis-store")
    return subprocess.run(
        [
            *nested_pytest.argv(root),
            "--collect-only",
            "-qq",
            f"-n{workers}",
        ]
        + ["-p", "no:cacheprovider"]
        + ["-p", "collection_parse_guard", "tests"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=110,
    )


#: A conftest's collection-finish hook that parses the planted file: a plain one, a ``trylast``
#: one, or a wrapper that parses after the hooks inside it are done.
_FINISH_HOOK = """
    import pytest
    import reader


    @pytest.hookimpl({marker})
    def pytest_collection_finish(session):
        reader.parse()
"""

_FINISH_WRAPPER = """
    import pytest
    import reader


    @pytest.hookimpl(wrapper=True)
    def pytest_collection_finish(session):
        result = yield
        reader.parse()
        return result
"""


_PLANTED_FILE = "src/personalclaw/planted.py"


@pytest.mark.parametrize(
    ("conftest", "test", "named", "by"),
    [
        pytest.param(
            _IMPORT_HELPERS + "    import ast\n    import reader\n"
            "    ast.parse(reader.PLANTED.read_text(), filename=str(reader.PLANTED))\n",
            _TEST,
            _PLANTED_FILE,
            "conftest.<module> (",
            id="a conftest-level parse of a src/personalclaw file",
        ),
        pytest.param(
            _IMPORT_HELPERS + "    import reader\n    reader._get_local_constants()\n",
            _TEST,
            _PLANTED_FILE,
            "reader._get_local_constants (",
            id="the same parse from a function named _get_local_constants in another module",
        ),
        pytest.param(
            _IMPORT_HELPERS + "    import reader\n    reader.nameless()\n",
            _TEST,
            _PLANTED_FILE,
            "reader.nameless (",
            id="the same source parsed with no file name",
        ),
        pytest.param(
            _IMPORT_HELPERS + "    import reader\n    reader.nameless_bytes()\n",
            _TEST,
            _PLANTED_FILE,
            "reader.nameless_bytes (",
            id="the file's bytes parsed with no file name",
        ),
        pytest.param(
            _IMPORT_HELPERS + "    import reader\n    reader.edited()\n",
            _TEST,
            _PLANTED_FILE,
            "reader.edited (",
            id="the file's name on a text that is not the file's",
        ),
        pytest.param(
            _IMPORT_HELPERS + "    import reader\n    reader.relative()\n",
            _TEST,
            _PLANTED_FILE,
            "reader.relative (",
            id="the file's name relative to the repository, on a text that is not the file's",
        ),
        pytest.param(
            _IMPORT_HELPERS + "    import reader\n    reader.script_edited()\n",
            _TEST,
            "web/src/generate.py",
            "reader.script_edited (",
            id="a web/src file's name on a text that is not the file's",
        ),
        pytest.param(
            _IMPORT_HELPERS + "    import reader\n    reader.nameless_script()\n",
            _TEST,
            "web/src/generate.py",
            "reader.nameless_script (",
            id="a script under web/src parsed with no file name",
        ),
        pytest.param(
            _IMPORT_HELPERS + "    import pyclbr\n    import reader\n"
            "    pyclbr.readmodule_ex('planted', path=[str(reader.PLANTED.parent)])\n",
            _TEST,
            _PLANTED_FILE,
            "pyclbr.",
            id="the standard library reading the file for a conftest",
        ),
        pytest.param(
            _IMPORT_HELPERS,
            _TEST_THAT_PARSES,
            _PLANTED_FILE,
            "reader.parse (",
            id="a test module's parse as pytest imports it",
        ),
        pytest.param(
            _IMPORT_HELPERS + _FINISH_HOOK.format(marker=""),
            _TEST,
            _PLANTED_FILE,
            "reader.parse (",
            id="a parse in a conftest's collection-finish hook",
        ),
        pytest.param(
            _IMPORT_HELPERS + _FINISH_HOOK.format(marker="trylast=True"),
            _TEST,
            _PLANTED_FILE,
            "reader.parse (",
            id="a parse in a conftest's last collection-finish hook",
        ),
        pytest.param(
            _IMPORT_HELPERS + _FINISH_WRAPPER,
            _TEST,
            _PLANTED_FILE,
            "reader.parse (",
            id="a parse in a conftest's collection-finish wrapper, after its hooks",
        ),
    ],
)
def test_a_parse_of_the_tree_during_collection_fails_the_guard(
    tmp_path: Path, conftest: str, test: str, named: str, by: str
):
    """Each fails the guard, which names the file and the frame that made the parse."""
    _tree(tmp_path, conftest, test)
    done = _collect(tmp_path)
    out = done.stdout + done.stderr
    assert done.returncode == 1, out[-3000:]
    assert "collection-parse-guard: 1 parse(s) of the source tree" in out, out[-3000:]
    assert f"{named} was parsed during collection by {by}" in out, out[-3000:]


def test_each_parse_is_named_with_the_frame_that_made_it(tmp_path: Path):
    _tree(
        tmp_path, _IMPORT_HELPERS + "    import reader\n    reader.parse()\n    reader.nameless()\n"
    )
    done = _collect(tmp_path)
    out = done.stdout + done.stderr
    assert done.returncode == 1, out[-3000:]
    assert "collection-parse-guard: 2 parse(s) of the source tree" in out, out[-3000:]
    named = [x for x in out.splitlines() if " was parsed during collection by " in x]
    assert len(named) == 2, out[-3000:]
    assert any(
        f"{_PLANTED_FILE} was parsed during collection by reader.parse (" in x for x in named
    )
    assert any(
        f"{_PLANTED_FILE} was parsed during collection by reader.nameless (" in x for x in named
    )


def test_hypothesis_own_scan_of_the_tree_is_excused(tmp_path: Path):
    """The control that passes: the conftest only imports the planted module, a Hypothesis test
    makes the plugin scan it at the end of collection, and that scan parses it."""
    conftest = """
    import importlib.util
    import sys
    from pathlib import Path

    _path = Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "planted.py"
    _spec = importlib.util.spec_from_file_location("planted_module", _path)
    planted_module = importlib.util.module_from_spec(_spec)
    sys.modules["planted_module"] = planted_module  # the scan reads sys.modules
    _spec.loader.exec_module(planted_module)
    """
    _tree(tmp_path, conftest, _GIVEN)
    done = _collect(tmp_path)
    out = done.stdout + done.stderr
    assert done.returncode == 0, out[-3000:]
    line = next(x for x in out.splitlines() if x.startswith("collection-parse-guard: clean"))
    excused = line.split("_get_local_constants ", 1)[1].split(",", 1)[0]
    assert int(excused) >= 1, f"Hypothesis's scan parsed nothing, so nothing was excused: {line}"


def test_the_assertion_rewrite_of_a_root_module_is_excused(tmp_path: Path):
    """The other control that passes: a module under a root whose name matches pytest's default
    ``*_test.py`` (as ``src/personalclaw/providers/model_test.py`` does), imported by the conftest,
    is parsed by the assertion rewrite as it loads, under its own name."""
    conftest = _IMPORT_HELPERS + (
        "    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'personalclaw'))"
        "\n    import planted_test\n"
    )
    _tree(tmp_path, conftest)
    (tmp_path / "src" / "personalclaw" / "planted_test.py").write_text(
        _PLANTED + "REWRITTEN = 1\n", encoding="utf-8"
    )
    done = _collect(tmp_path)
    out = done.stdout + done.stderr
    assert done.returncode == 0, out[-3000:]
    assert "collection-parse-guard: clean" in out, out[-3000:]


def test_a_clean_tree_passes_and_names_the_two_excused_callers(tmp_path: Path):
    _tree(tmp_path, _IMPORT_HELPERS + "    import reader\n")
    done = _collect(tmp_path)
    out = done.stdout + done.stderr
    assert done.returncode == 0, out[-3000:]
    line = next(x for x in out.splitlines() if x.startswith("collection-parse-guard: clean"))
    named = {pair.rsplit(" ", 1)[0] for pair in line.split(" excused ", 1)[1].split(", ")}
    assert named == {
        "_pytest.assertion.rewrite._rewrite_test",
        "hypothesis.internal.conjecture.providers._get_local_constants",
    }, line


@pytest.mark.parametrize(
    ("test", "code"),
    [
        pytest.param(
            'raise RuntimeError("a test module that cannot be imported")\n',
            pytest.ExitCode.INTERRUPTED,
            id="a collection error",
        ),
        pytest.param('"""No tests."""\n', pytest.ExitCode.NO_TESTS_COLLECTED, id="no tests"),
    ],
)
def test_a_clean_collection_that_errors_or_collects_nothing_is_red(
    tmp_path: Path, test: str, code: pytest.ExitCode
):
    _tree(tmp_path, _IMPORT_HELPERS + "    import reader\n", test)
    done = _collect(tmp_path)
    out = done.stdout + done.stderr
    assert "collection-parse-guard: clean" in out, out[-3000:]
    assert done.returncode == code, out[-3000:]


def test_the_guard_refuses_more_than_one_process(tmp_path: Path):
    _tree(tmp_path, _IMPORT_HELPERS + "    import reader\n")
    done = _collect(tmp_path, workers="2")
    out = done.stdout + done.stderr
    assert done.returncode == pytest.ExitCode.USAGE_ERROR, out[-3000:]
    assert "collection_parse_guard counts one process's parses: run it with -n0" in out, out[-3000:]


def test_the_real_trees_walks_keep_their_floors():
    """On the real tree the guard indexes the roots' files with the source index's floors: a walk
    that breaks fails the leg, rather than leaving a nameless parse nothing to match."""
    code = (
        "import source_index, collection_parse_guard\n"
        "source_index.FLOORS['package'] = 10**9\n"
        "collection_parse_guard._root_sources(source_index.REPO)\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "COV_"))}
    env["PYTHONPATH"] = str(TESTS)
    done = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=110
    )
    assert done.returncode == 1, done.stdout + done.stderr
    assert "VacuousWalk: the package walk found" in done.stderr, done.stderr[-3000:]
