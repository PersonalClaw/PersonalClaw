"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_native_ollama_bundle.py``

Its checks:
``tests/rails/test_native_ollama_bundle_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
