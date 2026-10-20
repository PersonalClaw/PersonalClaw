"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_scripted_provider_binding.py``

Its checks:
``tests/rails/test_scripted_provider_binding_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

import pytest

from personalclaw.llm.registry import SCRIPTED_PROVIDER_ENV, reset_default_registry


@pytest.fixture(autouse=True)
def _isolated_registry_and_home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """No shared registry state, no real home, no inherited opt-in.

    ``reset_default_registry()`` runs on BOTH sides: this suite runs under xdist
    beside everything else, and a leaked ``scripted`` type would make an unrelated
    test's provider resolution pick up a fixture.
    """
    monkeypatch.delenv(SCRIPTED_PROVIDER_ENV, raising=False)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    reset_default_registry()
    yield
    reset_default_registry()


#: Env-var spellings the tree deliberately NARRATES as dead (the drift these rails were written
#: after). Kept because the story is why the rails exist; asserted to be read by nothing.
RETIRED_ENV_NAMES = frozenset({"PERSONALCLAW_SCRIPTED_LLM"})
