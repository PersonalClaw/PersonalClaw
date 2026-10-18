"""``tools/ci/rails_census.py``: which test modules are rails, from their source alone.

One table: each row is a module's source and the verdict the census must reach on it. A rail
reads the repository's tree; a test that walks a tree it made, boots a gateway or reaches the
network is not one, and a module with tests of both kinds is mixed and gets split.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.ci import rails_census

_HEAD = "from pathlib import Path\n\nREPO = Path(__file__).resolve().parents[1]\n\n\n"

ROWS: list[tuple[str, str, str]] = [
    (
        "a walk of the repository with rglob is a rail",
        _HEAD + "def test_it():\n    assert list((REPO / 'src').rglob('*.py'))\n",
        "rail",
    ),
    (
        "a walk of tmp_path is not",
        _HEAD + "def test_it(tmp_path):\n    assert not list(tmp_path.rglob('*'))\n",
        "suite",
    ),
    (
        "an ast.parse of a package file is a rail",
        _HEAD
        + "import ast\n\n\ndef test_it():\n"
        + "    ast.parse((REPO / 'src' / 'personalclaw' / 'cli.py').read_text())\n",
        "rail",
    ),
    (
        "an ast.parse of a live module's source is a rail",
        "import ast\nimport inspect\n\nimport personalclaw.cli as cli\n\n\n"
        "def test_it():\n    ast.parse(inspect.getsource(cli))\n",
        "rail",
    ),
    (
        "an ast.parse of a string the test wrote is not",
        "import ast\n\n\ndef test_it():\n    ast.parse('x = 1')\n",
        "suite",
    ),
    (
        "a read of the source index is a rail",
        "import source_index\n\n\ndef test_it():\n    assert source_index.package_files()\n",
        "rail",
    ),
    (
        "a workflow read is a rail",
        _HEAD + "def test_it():\n    (REPO / '.github' / 'workflows' / 'ci.yml').read_text()\n",
        "rail",
    ),
    (
        "a workflow named only in a docstring is not",
        'def test_it():\n    """See .github/workflows/ci.yml."""\n    assert True\n',
        "suite",
    ),
    (
        "a single file read with no parse is not",
        _HEAD + "def test_it():\n    assert (REPO / 'pyproject.toml').read_text()\n",
        "suite",
    ),
    (
        "a gateway start is not",
        "import signed_in_gateway\n\n\ndef test_it():\n    signed_in_gateway.start()\n",
        "suite",
    ),
    (
        "a rail that also calls the network is not",
        "import urllib.request\n\nimport source_index\n\n\ndef test_it():\n"
        "    source_index.package_files()\n    urllib.request.urlopen('https://example.com')\n",
        "suite",
    ),
    (
        "a rail that runs git, a local child, is still a rail",
        "import subprocess\n\nimport source_index\n\n\ndef test_it():\n"
        "    source_index.package_files()\n    subprocess.run(['git', 'status'])\n",
        "rail",
    ),
    (
        "a rail that spawns a container runtime is not",
        "import subprocess\n\nimport source_index\n\n\ndef test_it():\n"
        "    source_index.package_files()\n    subprocess.run(['docker', 'ps'])\n",
        "suite",
    ),
    (
        "a tree read through a module-level helper the test reaches is a rail",
        "import source_index\n\n\ndef _files():\n    return source_index.package_files()\n\n\n"
        "def test_it():\n    assert _files()\n",
        "rail",
    ),
    (
        "a tree read through a fixture the test asks for is a rail",
        "import pytest\nimport source_index\n\n\n@pytest.fixture\ndef files():\n"
        "    return source_index.package_files()\n\n\ndef test_it(files):\n    assert files\n",
        "rail",
    ),
    (
        "a tree read in a decorator's parameters is a rail",
        "import pytest\nimport source_index\n\n\n"
        "@pytest.mark.parametrize('path', source_index.package_files()[:2])\n"
        "def test_it(path):\n    assert path\n",
        "rail",
    ),
    (
        "a module with a tree test and a network test is mixed",
        "import urllib.request\n\nimport source_index\n\n\ndef test_tree():\n"
        "    assert source_index.package_files()\n\n\ndef test_wire():\n"
        "    urllib.request.urlopen('https://example.com')\n",
        "mixed",
    ),
    (
        "a security-marked module stays put whatever it reads",
        "import pytest\nimport source_index\n\npytestmark = pytest.mark.security\n\n\n"
        "def test_it():\n    assert source_index.package_files()\n",
        "security",
    ),
    (
        "a module that reads no tree is not",
        "def test_it():\n    assert 1 + 1 == 2\n",
        "suite",
    ),
]


@pytest.mark.parametrize(("source", "kind"), [(s, k) for _, s, k in ROWS], ids=[r[0] for r in ROWS])
def test_the_census_classifies_each_module(source: str, kind: str) -> None:
    assert rails_census.classify("tests/test_x.py", source).kind == kind


def test_each_verdict_says_why() -> None:
    found = rails_census.classify("tests/test_x.py", ROWS[0][1])
    assert found.tree == {"test_it": "walks the repository (rglob)"}
    assert "walks the repository (rglob)" in found.line()


def _plant(root: Path, rel: str, source: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_misplaced_names_a_rail_outside_tests_rails_and_a_suite_module_inside(
    tmp_path: Path,
) -> None:
    rail, suite = ROWS[5][1], ROWS[-1][1]
    _plant(tmp_path, "tests/test_rail_outside.py", rail)
    _plant(tmp_path, "tests/rails/test_rail_inside.py", rail)
    _plant(tmp_path, "tests/rails/test_suite_inside.py", suite)
    _plant(tmp_path, "tests/test_suite_outside.py", suite)
    _plant(tmp_path, "tests/security/test_marked.py", ROWS[-2][1])
    bad = rails_census.misplaced(rails_census.census(tmp_path))
    assert [line.split(" ", 1)[0] for line in bad] == [
        "tests/rails/test_suite_inside.py",
        "tests/test_rail_outside.py",
    ]
