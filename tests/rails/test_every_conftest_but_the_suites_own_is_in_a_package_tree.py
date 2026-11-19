"""Every ``conftest.py`` the suite loads but its own sits in a package.

pytest imports a conftest whose folder has no ``__init__.py`` under the bare name ``conftest``,
and drops whatever ``sys.modules`` held under that name first. The suite's own,
``tests/conftest.py``, is that name, and root tests read it by that name (``import conftest``).
So a second conftest outside a package replaces it for every root test imported after pytest
reaches the second one's folder. ``tests/documents/`` collects before ``tests/test_*.py``, which
is why the documents package's tests are a package (#6267): as a plain conftest, theirs was what
every root ``import conftest`` read in a run that collected the folder.

The suite collects ``tests/`` and loads a conftest at the repository's root too, so this rail
walks ``tests/`` for conftests and looks for one at the root. An ``__init__.py`` that is only
local passes here and fails on CI's fresh checkout. The premise test runs that import in a nested
pytest, so the rail fails, and its rule can go, if pytest ever stops doing it.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from collections.abc import Iterable
from pathlib import Path

import nested_pytest
from rails_repo import REPO

#: The suite's own conftest, the one ``import conftest`` means.
SUITES = "tests/conftest.py"
TESTS = REPO / "tests"


def plain_conftests(root: Path, found: Iterable[Path]) -> list[str]:
    """Each of *found* but the suite's own whose folder holds no ``__init__.py``, relative to
    *root*: one pytest would import as ``conftest``."""
    return sorted(
        path.relative_to(root).as_posix()
        for path in found
        if path.relative_to(root).as_posix() != SUITES
        and not (path.parent / "__init__.py").is_file()
    )


def test_every_conftest_but_the_suites_own_is_in_a_package() -> None:
    # Each conftest the suite loads: under tests/, which it collects, and one at the root.
    found = sorted(TESTS.rglob("conftest.py")) + sorted(REPO.glob("conftest.py"))
    # Vacuity: the walk finds the suite's own and the documents package's, so a broken walk
    # cannot pass on nothing.
    named = {path.relative_to(REPO).as_posix() for path in found}
    assert {SUITES, "tests/documents/conftest.py"} <= named, named
    plain = plain_conftests(REPO, found)
    assert not plain, (
        "each of these conftests is in a folder with no __init__.py, so pytest imports it as "
        f"`conftest` and it replaces {SUITES} for the root tests that `import conftest`: "
        f"{plain}. Add an __init__.py beside it (as tests/documents/ has), or move what it holds "
        "into a helper module the tests import by its own name."
    )


def test_the_rule_names_a_plain_conftest_and_passes_one_in_a_package(tmp_path: Path) -> None:
    for planted in [
        SUITES,
        "tests/documents/__init__.py",
        "tests/documents/conftest.py",
        "tests/plain/conftest.py",
        "conftest.py",
        "tests/deep/pkg/__init__.py",
        "tests/deep/pkg/conftest.py",
        "tests/deep/conftest.py",
        "packages/elsewhere/conftest.py",
    ]:
        (tmp_path / planted).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / planted).write_text("")
    # The root's own and the plain ones under tests/; a folder the suite does not collect is not
    # read.
    found = sorted((tmp_path / "tests").rglob("conftest.py")) + sorted(tmp_path.glob("conftest.py"))
    assert plain_conftests(tmp_path, found) == [
        "conftest.py",
        "tests/deep/conftest.py",
        "tests/plain/conftest.py",
    ]


_ROOT_TEST = """
import conftest


def test_reads_the_suites_conftest():
    assert conftest.WHOSE == "the suite's"
"""


def _session(root: Path, package: bool) -> subprocess.CompletedProcess[str]:
    """A nested pytest over a suite laid out as this one is: its own conftest in ``tests/``, a
    ``documents`` folder with a conftest of its own (in a package if *package*), and a root test
    that reads ``import conftest``."""
    tests = root / "tests"
    (tests / "documents").mkdir(parents=True)
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
    (tests / "conftest.py").write_text('WHOSE = "the suite\'s"\n')
    (tests / "documents" / "conftest.py").write_text('WHOSE = "the documents folder\'s"\n')
    (tests / "documents" / "test_documents.py").write_text("def test_documents():\n    pass\n")
    if package:
        (tests / "documents" / "__init__.py").write_text("")
    (tests / "test_reads.py").write_text(textwrap.dedent(_ROOT_TEST))
    return subprocess.run(
        nested_pytest.argv(
            root, "-q", "-p", "no:cacheprovider", "-p", "no:randomly", "-o", "addopts="
        ),
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        # No PYTEST_ or PERSONALCLAW_ variable of the outer run reaches the nested one.
        env={k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))},
    )


def test_the_premise_a_plain_conftest_takes_the_suites_name(tmp_path: Path) -> None:
    """In a run that collects ``documents/``, a plain conftest there is what ``import conftest``
    reads in a root test; in a package, the root test reads the suite's own."""
    plain = _session(tmp_path / "plain", package=False)
    said = plain.stdout + plain.stderr
    assert plain.returncode == 1 and "1 failed, 1 passed" in said, said[-3000:]
    assert "the documents folder's" in said, said[-3000:]
    packaged = _session(tmp_path / "package", package=True)
    said = packaged.stdout + packaged.stderr
    assert packaged.returncode == 0 and "2 passed" in said, said[-3000:]
