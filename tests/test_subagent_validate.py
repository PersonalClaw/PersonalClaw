"""Tests for the _validate_agent fallback chain in subagent.py.

These used to replace eight core modules in ``sys.modules`` with hand-built stubs so
``subagent`` would import "without the full runtime", and to drop ``personalclaw.subagent``
around every test. The stubs broke the day ``subagent`` imported a name they did not define
(``llm.base.EVENT_TOOL_RESULT``), and dropping a module other tests hold splits its identity
for the rest of the worker. The test environment has the full runtime; ``_validate_agent``
reads only ``AppConfig.load``, which each test patches.
"""

from unittest.mock import MagicMock, patch


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


def test_unknown_agent_returns_typed_error_naming_valid_agents():
    """C1.3: an unconfigured agent name is a TYPED error naming the valid agents —
    NOT a silent downgrade to the default. A fan-out that named the wrong agent used
    to run entirely on personalclaw with only a log line; now it fails loudly."""
    from personalclaw.subagent import _validate_agent

    with patch(
        "personalclaw.config.loader.AppConfig.load",
        return_value=_config_with_agents("personalclaw", "code-reviewer", "researcher"),
    ):
        name, err = _validate_agent("nonexistent")
        assert name == ""
        assert err  # non-empty typed error
        assert "nonexistent" in err
        # names the valid agents (personalclaw/-orchestrator excluded from the list)
        assert "code-reviewer" in err
        assert "researcher" in err


def test_unknown_agent_error_when_no_other_agents():
    """The typed error still fires with a placeholder when only reserved agents exist."""
    from personalclaw.subagent import _validate_agent

    with patch(
        "personalclaw.config.loader.AppConfig.load",
        return_value=_config_with_agents("personalclaw"),
    ):
        name, err = _validate_agent("nonexistent")
        assert name == ""
        assert "nonexistent" in err
        assert "none configured" in err


def test_empty_input_returns_empty():
    from personalclaw.subagent import _validate_agent

    name, err = _validate_agent("")
    assert name == ""
    assert err == ""
