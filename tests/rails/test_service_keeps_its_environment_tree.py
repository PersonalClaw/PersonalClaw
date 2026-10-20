"""The CLI reference lists exactly the variables a service carries.

The tree half of ``tests/test_service_keeps_its_environment.py``, which says why an installed
service keeps the environment it needs and tests what each platform's unit carries. The table in
``docs/reference/cli.md`` is the documented contract. It reads that table, so it is a rail: it runs
once per CI run, in the rails job.
"""

from __future__ import annotations

import re

import source_index
from rails_repo import REPO

_CLI_REFERENCE = REPO / "docs" / "reference" / "cli.md"


def test_the_cli_reference_lists_exactly_the_carried_names():
    """The table in the CLI reference is the documented contract: the same names, both ways."""
    from personalclaw.service.environment import carried_names

    doc = source_index.text(_CLI_REFERENCE)
    section = doc.split("## `personalclaw service`", 1)[1].split("\n## ", 1)[0]
    table = section.split("| For | Variables |", 1)[1].split("\n\n", 1)[0]
    rows = [line for line in table.splitlines() if line.startswith("|")][1:]  # past |---|
    assert rows, "the table is gone, so this check would pass on nothing"
    documented = {n for row in rows for n in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", row)}
    assert documented == set(carried_names()), (
        f"documented, not carried: {sorted(documented - set(carried_names()))}; "
        f"carried, not documented: {sorted(set(carried_names()) - documented)}"
    )
