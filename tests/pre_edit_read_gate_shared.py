"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_pre_edit_read_gate.py``

Its checks:
``tests/rails/test_pre_edit_read_gate_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.agents.native import read_gate

_BUILTIN_TOOLS_SRC = Path(read_gate.__file__).with_name("builtin_tools.py")


@pytest.fixture(autouse=True)
def _clean_ledger():
    # The ledger is process-global; reset around every test so a leaked observation from
    # one test cannot admit a write another test asserts is refused (or vice versa).
    read_gate.reset_all()
    yield
    read_gate.reset_all()


#: Write paths that CANNOT be gated on an observed target, declared so the exemption is
#: visible and tested rather than silently missing from the registry. ``bash`` takes a
#: command, not a path: ``sed -i`` / ``> file`` mutate files the gate never sees a name
#: for. It is fenced by approval + risk level + the bash denylist instead, and this row
#: is the honest statement of the residual hole.
_UNGATEABLE_WRITE_PATHS = {"bash": "takes an opaque command, not a target path"}
