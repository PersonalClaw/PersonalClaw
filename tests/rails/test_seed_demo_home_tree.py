"""The source-tree checks split from
``tests/test_seed_demo_home.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

import re
from pathlib import Path

import source_index
from seed_demo_home_shared import _FIXTURES_DIR, _REPO_ROOT, FIXTURE_NAME

_DEMO_DIR = _FIXTURES_DIR / FIXTURE_NAME


def test_the_committed_fixture_dbs_are_self_contained() -> None:
    """Read off the repo tree, not a seeded home — this is about what SHIPS.

    Both stores open with ``PRAGMA journal_mode=WAL``, so writes sit in a ``-wal``
    sidecar until checkpointed. Committing the bare ``.db`` without a checkpoint
    ships a fixture that boots EMPTY, and committing the sidecar ships a file that
    is not state. Also pins that no generation-machine path leaked into the bytes.
    """
    demo = source_index.under(source_index.package_any_files(), _DEMO_DIR)
    dbs = [p for p in demo if p.suffix == ".db"]
    assert len(dbs) == 2, f"expected knowledge.db + loops.db in the fixture, found {dbs}"

    strays = sorted(p.name for p in demo if p.name.endswith(("-wal", "-shm", ".db-journal")))
    assert not strays, f"SQLite sidecars must not ship: {strays}"

    for db in dbs:
        blob = db.read_bytes()
        for needle in (b"/Users/", b"/home/", b"/private/tmp", b"/var/folders"):
            assert needle not in blob, (
                f"{db.name} embeds the absolute path {needle.decode()!r} from the "
                "machine that generated it — it will not resolve in a user's home"
            )


def _package_data_globs() -> list[str]:
    text = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"\[tool\.setuptools\.package-data\](.*?)\n\[", text, re.S)
    assert block, "could not find the [tool.setuptools.package-data] block"
    return re.findall(r'"([^"]+)"', block.group(1))


def test_every_fixture_file_is_covered_by_a_package_data_glob() -> None:
    """Only observable from ``pyproject.toml``: an editable install looks perfect
    while the WHEEL ships a half-empty fixture.

    The glob used to be ``tests_fixtures/*/*`` — one level deep, which covered
    ``empty/fixture.yaml`` and nothing else. Every nested file the demo fixture
    needs (``projects/<id>/project.json``,
    ``workspace/memory/history/<date>.md``) was silently dropped from the wheel,
    so ``pip install personalclaw && personalclaw gateway --seed demo-home``
    would have produced a home with no projects and no memory.

    This asserts *coverage of the real tree* rather than the presence of a
    string, so it keeps holding as the fixture grows deeper.
    """
    globs = [g for g in _package_data_globs() if g.startswith("tests_fixtures/")]
    assert globs, "no tests_fixtures glob in the package-data block"

    pkg_root = _FIXTURES_DIR.parent
    matched: set[Path] = set()
    for pattern in globs:
        matched.update(p for p in pkg_root.glob(pattern) if p.is_file())

    present = set(source_index.under(source_index.package_any_files(), _FIXTURES_DIR))
    assert present, "no fixture files on disk — this test would pass vacuously"

    missing = sorted(str(p.relative_to(pkg_root)) for p in present - matched)
    assert not missing, (
        "these fixture files match no package-data glob and would be missing "
        f"from the wheel: {missing}"
    )


def test_the_demo_fixture_is_nested_deeper_than_one_level() -> None:
    """The vacuity floor for the test above.

    A coverage check over a flat tree is satisfied by the old one-level glob, so
    it would pass while the packaging bug it exists to catch is wide open. This
    pins that the fixture really does keep files below ``<fixture>/<file>`` —
    i.e. that there is something for the recursive glob to be load-bearing for.
    """
    depths = [
        len(p.relative_to(_DEMO_DIR).parts)
        for p in source_index.under(source_index.package_any_files(), _DEMO_DIR)
    ]
    assert depths, f"{FIXTURE_NAME} has no files"
    assert max(depths) >= 3, (
        "the demo fixture is flat — the recursive package-data glob is no longer "
        "load-bearing and the coverage test above has nothing to prove"
    )
