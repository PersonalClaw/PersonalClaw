"""Tests for the _validate_agent fallback chain in subagent.py.

Heavy dependencies are stubbed at the sys.modules level so subagent.py imports
without the full runtime.
"""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Stub out heavy transitive imports before importing subagent
_STUBS = [
    "personalclaw.context",
    "personalclaw.hooks",
    "personalclaw.providers",
    "personalclaw.llm.base",
    "personalclaw.sel",
    "personalclaw.session",
    "personalclaw.textfmt",
    "personalclaw.stats",
]


@pytest.fixture(autouse=True)
def _stub_modules():
    """Inject stub modules so subagent.py can be imported."""
    originals = {}
    for mod_name in _STUBS:
        originals[mod_name] = sys.modules.get(mod_name)
        stub = types.ModuleType(mod_name)
        # providers.base needs specific names
        if mod_name == "personalclaw.llm.base":
            stub.EVENT_COMPLETE = "complete"
            stub.EVENT_PERMISSION_REQUEST = "permission"
            stub.EVENT_TEXT_CHUNK = "text"
            stub.EVENT_TOOL_CALL = "tool_call"
            stub.LLMEvent = type("LLMEvent", (), {})
            stub.ModelProvider = type("ModelProvider", (), {})
        if mod_name == "personalclaw.hooks":
            stub.TOOL_AUTO_APPROVE = "auto"
            stub.TOOL_DENY = "deny"
            stub.fire_tool_hooks = MagicMock()
            stub.safe_read_file = lambda path: ""
            stub.get_global_hook_store = MagicMock()
        if mod_name == "personalclaw.textfmt":
            stub.extract_options = lambda x: (x, [])
        if mod_name == "personalclaw.stats":
            stub.Stats = MagicMock
        if mod_name == "personalclaw.sel":
            stub.sel = MagicMock()
        if mod_name == "personalclaw.context":
            stub.ContextBuilder = MagicMock
        if mod_name == "personalclaw.session":
            stub.SessionManager = MagicMock
        sys.modules[mod_name] = stub

    # Clear cached subagent module so it reimports with stubs
    sys.modules.pop("personalclaw.subagent", None)

    yield

    # Restore
    for mod_name in _STUBS:
        if originals[mod_name] is None:
            sys.modules.pop(mod_name, None)
        else:
            sys.modules[mod_name] = originals[mod_name]
    sys.modules.pop("personalclaw.subagent", None)


def _config_with_agents(*names: str) -> MagicMock:
    """Return a stub AppConfig whose ``.agents`` is keyed by *names*."""
    cfg = MagicMock()
    cfg.agents = {n: MagicMock() for n in names}
    return cfg


def test_found_returns_requested():
    from personalclaw.subagent import _validate_agent

    with patch(
        "personalclaw.config.loader.AppConfig.load",
        return_value=_config_with_agents("code-reviewer", "personalclaw"),
    ):
        name, err = _validate_agent("code-reviewer")
        assert name == "code-reviewer"
        assert err == ""


def test_unknown_agent_falls_back_to_default_without_error():
    """An unconfigured agent name resolves to an empty name (caller uses the
    default agent) and returns no error — the mismatch is only logged."""
    from personalclaw.subagent import _validate_agent

    with patch(
        "personalclaw.config.loader.AppConfig.load",
        return_value=_config_with_agents("personalclaw"),
    ):
        name, err = _validate_agent("nonexistent")
        assert name == ""
        assert err == ""


def test_empty_input_returns_empty():
    from personalclaw.subagent import _validate_agent

    name, err = _validate_agent("")
    assert name == ""
    assert err == ""
