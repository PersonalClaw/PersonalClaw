"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_inbound_mcp.py``

Its checks:
``tests/rails/test_inbound_mcp_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

import pytest

from personalclaw.inbound import caps as caps_mod

_SURFACE_TOKENS = tuple(
    f"PERSONALCLAW_INBOUND_{surface}_TOKEN"
    for surface in ("OPENAI", "MCP", "A2A", "CAPTURE", "BRIDGE")
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch, unset_env):
    """Every test gets its own home and a clean rate bucket.

    The buckets are process-global (they must be, to limit anything), so without
    this reset a test would inherit whatever budget an earlier test spent.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    # Registered before the test. `save_credential` mirrors into `os.environ` itself, so a
    # token minted mid-test is a variable monkeypatch never recorded and therefore never
    # undoes — it would leak into every later test in this xdist worker, where a stale
    # `PERSONALCLAW_INBOUND_*_TOKEN` reads as "this surface has a valid token".
    unset_env(*_SURFACE_TOKENS)
    caps_mod.reset_for_tests()
    yield
    caps_mod.reset_for_tests()
