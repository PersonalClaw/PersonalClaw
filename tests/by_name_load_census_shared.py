"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_by_name_load_census.py``

Its checks:
``tests/rails/test_by_name_load_census_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


_SPEC = _REPO / "personalclaw-backend.spec"


#: The modules the desktop app's bundle left out although core loads them, so its agent had no
#: memory, automation, prompt or subagent tools, wrote no document and listed no template.
_LOST_IN_THE_DESKTOP_APP = (
    "personalclaw.mcp_memory",
    "personalclaw.mcp_automation",
    "personalclaw.mcp_prompts",
    "personalclaw.mcp_subagents",
    "personalclaw.documents.writers.docx_writer",
    "personalclaw.documents.writers.pdf_writer",
    "personalclaw.workflows.bundled",
    "personalclaw.workflows.bundled.shared",
    "personalclaw.skills.bundled",
    "personalclaw.reference",
)


def _load_manifest():
    spec = importlib.util.spec_from_file_location(
        "backend_bundle_manifest", _REPO / "scripts" / "backend_bundle_manifest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def manifest():
    return _load_manifest()


def _texts(value: object) -> list[str]:
    """Every string in a literal value (a dict's values, not its keys)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for item in value.values() for t in _texts(item)]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [t for item in value for t in _texts(item)]
    return []
