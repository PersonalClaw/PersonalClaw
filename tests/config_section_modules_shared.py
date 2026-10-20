"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_config_section_modules.py``

Its checks:
``tests/rails/test_config_section_modules_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "src" / "personalclaw" / "config"


#: The modules the split extracted. Named explicitly rather than globbed so that DELETING one (or
#: quietly folding a section back into loader.py) reds here instead of shrinking the sweep to
#: nothing — a glob would make this whole file vacuous the moment the split was reverted.
SECTION_MODULES = ("safety", "learning", "external_access")
