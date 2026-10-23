"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_browse_live_legs_run_in_ci.py``

Its checks:
``tests/rails/test_browse_live_legs_run_in_ci_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module. Both read the catalogue (``tests/ci_catalogue_steps.py``) for the leg, so the workflow
text parser they once shared went with ``ci.yml`` (#3915).
"""

from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
