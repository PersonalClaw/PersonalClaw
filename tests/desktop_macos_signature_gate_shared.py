"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_desktop_macos_signature_gate.py``

Its checks:
``tests/rails/test_desktop_macos_signature_gate_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


MAKEFILE = REPO_ROOT / "Makefile"


#: The gate's filename, referenced by both callers. Kept as one constant so a rename has to
#: update this file too rather than quietly unwiring one of the two call sites.
GATE_SCRIPT_NAME = "scripts/verify_macos_app_signature.sh"
