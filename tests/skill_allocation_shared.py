"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_skill_allocation.py``

Its checks:
``tests/rails/test_skill_allocation_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _usage_store_stays_in_tmp(tmp_path, monkeypatch):
    """The turn-time use counter writes beside the skills dir — keep it in tmp.

    `SkillUsageStore()` resolves its own path from `skills_dir()`, so an unpatched run
    would record uses into whatever home the process resolves. Conftest's
    ``_isolate_real_home_writers`` already redirects that, but a test that exercises the use
    counter should not depend on another fixture for its isolation.
    """
    counter_home = tmp_path / "usage-home"
    counter_home.mkdir()
    monkeypatch.setattr("personalclaw.skills.usage.skills_dir", lambda: counter_home)
