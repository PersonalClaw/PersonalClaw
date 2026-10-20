"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_python_children_in_the_desktop_app.py``

Its checks:
``tests/rails/test_python_children_in_the_desktop_app_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _declared() -> tuple[str, ...]:
    """The modules the bundle's entry runs (``_frozen_child.CHILD_MODULES``)."""
    from personalclaw import _frozen_child

    return _frozen_child.CHILD_MODULES
