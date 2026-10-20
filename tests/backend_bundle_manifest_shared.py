"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_backend_bundle_manifest.py``

Its checks:
``tests/rails/test_backend_bundle_manifest_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


_MANIFEST_FILE = _REPO_ROOT / "scripts" / "backend_bundle_manifest.py"


def _load_manifest():
    spec = importlib.util.spec_from_file_location("backend_bundle_manifest", _MANIFEST_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def manifest():
    return _load_manifest()
