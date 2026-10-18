"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_routing_policy.py``

Its checks:
``tests/rails/test_routing_policy_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parents[1] / "src/personalclaw/providers/provider_bridge.py"


@pytest.fixture(autouse=True)
def _ollama_is_installed(ollama_app):
    """The entries here are the bundled Ollama app's, a model server that runs its models where
    it is: only its type makes an endpoint on this machine a model here."""
