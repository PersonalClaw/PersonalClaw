"""The CLI reference names every fixture ``--seed`` can copy, and counts them right.

``docs/reference/cli.md`` said "Two fixtures ship" and listed ``empty`` and ``demo-home``,
while the wheel ships every directory under ``personalclaw/tests_fixtures/`` (the package-data
glob is ``tests_fixtures/**/*``) and the seeder accepts any of them: ``six-month-home`` was
seedable and undocumented. The table is read here and held to the directories that ship.
"""

from __future__ import annotations

import re
from pathlib import Path

from personalclaw import seed
from personalclaw.evals import scenarios

_REPO = Path(__file__).resolve().parents[1]
_CLI_REFERENCE = _REPO / "docs" / "reference" / "cli.md"
_COUNT_WORDS = {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six"}


def _shipped() -> list[str]:
    root = seed._fixtures_root()
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def _documented() -> tuple[str, list[str]]:
    """The count word before the fixture table, and the fixture names its rows give."""
    text = _CLI_REFERENCE.read_text(encoding="utf-8")
    head = re.search(r"^(\w+) fixtures ship:\n", text, re.MULTILINE)
    assert head, "the CLI reference no longer introduces its fixture table"
    rows = []
    for line in text[head.end() :].split("\n"):
        if line.startswith("### "):
            break
        row = re.match(r"\| `([a-z0-9-]+)` \|", line)
        if row:
            rows.append(row.group(1))
    return head.group(1), rows


def test_the_fixture_table_lists_what_ships() -> None:
    shipped = _shipped()
    # Positive control: the package really carries the bare home a scenario defaults to.
    assert "empty" in shipped
    word, rows = _documented()
    assert sorted(rows) == shipped
    assert word == _COUNT_WORDS[len(shipped)]


def test_a_scenario_defaults_to_a_fixture_that_ships() -> None:
    assert scenarios.DEFAULT_FIXTURE_HOME in _shipped()
