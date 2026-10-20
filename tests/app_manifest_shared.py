"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_app_manifest.py``

Its checks:
``tests/rails/test_app_manifest_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""


def _valid_manifest(**overrides) -> dict:
    """Return a minimal valid manifest dict with optional overrides."""
    base = {
        "name": "test-app",
        "version": "1.0.0",
        "displayName": "Test App",
        "description": "A test app",
        "author": "tester",
    }
    base.update(overrides)
    return base
