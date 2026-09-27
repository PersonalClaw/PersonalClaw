"""Tests for `personalclaw gateway` composable CLI flags.

Covers argparse parsing, --test-mode bundle expansion with override
semantics, and the --approval yolo safety rail.
"""

import argparse
from pathlib import Path

import pytest

from personalclaw.cli import _resolve_gateway_args

# ─── Helpers ─────────────────────────────────────────────────────────────


def _ns(**kwargs) -> argparse.Namespace:
    """Build an argparse.Namespace with gateway-flag defaults filled in.

    Mirrors what the CLI parser produces for `personalclaw gateway`. Tests
    override only the fields they exercise.
    """
    defaults = {
        "command": "gateway",
        "headless": False,
        "no_crons": False,
        "seed": None,
        "seed_replace": False,
        "no_open": False,
        "port": None,
        "json_ready": False,
        "approval": None,
        "test_mode": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ─── _resolve_gateway_args: bundle expansion + override semantics ────────


class TestNoFlags:
    """Without any new flags, current behavior is preserved byte-for-byte."""

    def test_defaults_pass_through(self):
        result = _resolve_gateway_args(_ns())
        assert result == {
            "no_dashboard": False,
            "no_crons": False,
            "no_open": False,
            "port_override": None,
            "json_ready": False,
            "approval_mode": None,
            # AS-6 §6 recovery lever. Enumerated here (an exact dict, not a subset) on purpose:
            # every key this resolver returns is splatted into `_gateway`, so a new one has to
            # be argued for in this test before it can reach the entry point.
            "safe_surfaces": False,
        }

    def test_headless_flag_passes_through(self):
        result = _resolve_gateway_args(_ns(headless=True, no_crons=True, no_open=True))
        assert result["no_dashboard"] is True
        assert result["no_crons"] is True
        assert result["no_open"] is True
        # New flags untouched.
        assert result["port_override"] is None
        assert result["json_ready"] is False
        assert result["approval_mode"] is None


class TestTestModeBundle:
    """`--test-mode` expands to the documented bundle."""

    def test_bundle_defaults(self):
        result = _resolve_gateway_args(_ns(test_mode=True))
        assert result["port_override"] == "auto"
        assert result["json_ready"] is True
        assert result["no_open"] is True
        assert result["approval_mode"] == "reads"

    def test_explicit_approval_overrides_bundle(self, tmp_path, monkeypatch):
        # yolo bundle override needs the safety rail to pass; isolate home.
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        result = _resolve_gateway_args(_ns(test_mode=True, approval="yolo"))
        assert result["approval_mode"] == "yolo"
        # Other bundle defaults still apply.
        assert result["port_override"] == "auto"
        assert result["json_ready"] is True
        assert result["no_open"] is True

    def test_explicit_port_overrides_bundle(self):
        result = _resolve_gateway_args(_ns(test_mode=True, port="9999"))
        assert result["port_override"] == "9999"
        assert result["approval_mode"] == "reads"
        assert result["json_ready"] is True
        assert result["no_open"] is True

    def test_explicit_interactive_overrides_bundle(self):
        result = _resolve_gateway_args(_ns(test_mode=True, approval="interactive"))
        assert result["approval_mode"] == "interactive"

    def test_explicit_reads_redundant_but_accepted(self):
        # Same as the bundle default — should be a no-op, not an error.
        result = _resolve_gateway_args(_ns(test_mode=True, approval="reads"))
        assert result["approval_mode"] == "reads"


class TestStandaloneFlags:
    """Each new flag works without --test-mode."""

    def test_port_int(self):
        result = _resolve_gateway_args(_ns(port="9999"))
        assert result["port_override"] == "9999"
        assert result["json_ready"] is False  # not set by --port

    def test_port_auto_alone(self):
        result = _resolve_gateway_args(_ns(port="auto"))
        assert result["port_override"] == "auto"
        assert result["json_ready"] is False

    def test_port_auto_uppercase_canonicalized(self):
        # Case-insensitive auto — common typo, should accept.
        result = _resolve_gateway_args(_ns(port="AUTO"))
        assert result["port_override"] == "auto"

    def test_port_auto_mixedcase_canonicalized(self):
        result = _resolve_gateway_args(_ns(port="Auto"))
        assert result["port_override"] == "auto"

    def test_port_int_canonicalized_to_string(self):
        # Integer string passes through unchanged so downstream
        # comparison with "auto" works without type-juggling.
        result = _resolve_gateway_args(_ns(port="1234"))
        assert result["port_override"] == "1234"

    def test_json_ready_alone(self):
        result = _resolve_gateway_args(_ns(json_ready=True))
        assert result["json_ready"] is True
        assert result["port_override"] is None  # not set by --json-ready

    def test_approval_reads_alone(self):
        result = _resolve_gateway_args(_ns(approval="reads"))
        assert result["approval_mode"] == "reads"

    def test_approval_interactive_alone(self):
        result = _resolve_gateway_args(_ns(approval="interactive"))
        assert result["approval_mode"] == "interactive"


class TestPortValidation:
    """`--port` rejects garbage at parse time, not deep in startup."""

    def test_non_numeric_string_rejected(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _resolve_gateway_args(_ns(port="abc"))
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert "must be an integer or 'auto'" in captured.err

    def test_float_string_rejected(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _resolve_gateway_args(_ns(port="99.5"))
        assert exc.value.code == 2

    def test_negative_port_rejected(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _resolve_gateway_args(_ns(port="-1"))
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert "out of range" in captured.err

    def test_zero_port_rejected(self, capsys):
        # Port 0 means "ephemeral" only via the `auto` keyword. Bare 0 is a typo.
        with pytest.raises(SystemExit) as exc:
            _resolve_gateway_args(_ns(port="0"))
        assert exc.value.code == 2

    def test_port_above_65535_rejected(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _resolve_gateway_args(_ns(port="70000"))
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert "out of range" in captured.err

    def test_port_max_accepted(self):
        result = _resolve_gateway_args(_ns(port="65535"))
        assert result["port_override"] == "65535"

    def test_port_min_accepted(self):
        result = _resolve_gateway_args(_ns(port="1"))
        assert result["port_override"] == "1"


# ─── Safety rail: --approval yolo ────────────────────────────────────────


class TestApprovalYoloSafetyRail:
    """`--approval yolo` refuses to run against the default home."""

    def test_yolo_refused_without_personalclaw_home(self, monkeypatch, capsys):
        monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
        with pytest.raises(SystemExit) as exc:
            _resolve_gateway_args(_ns(approval="yolo"))
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert "PERSONALCLAW_HOME must be explicitly set" in captured.err

    def test_yolo_refused_when_personalclaw_home_empty_string(self, monkeypatch, capsys):
        monkeypatch.setenv("PERSONALCLAW_HOME", "")
        with pytest.raises(SystemExit) as exc:
            _resolve_gateway_args(_ns(approval="yolo"))
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert "PERSONALCLAW_HOME must be explicitly set" in captured.err

    def test_yolo_refused_when_resolves_to_default_home(self, monkeypatch, capsys):
        # Point PERSONALCLAW_HOME at the literal default; rail must catch it.
        monkeypatch.setenv("PERSONALCLAW_HOME", str(Path.home() / ".personalclaw"))
        with pytest.raises(SystemExit) as exc:
            _resolve_gateway_args(_ns(approval="yolo"))
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert "main gateway home" in captured.err

    def test_yolo_refused_via_tilde_expansion(self, monkeypatch, capsys):
        # `~/.personalclaw` expands then resolves to the same path as Path.home() / .personalclaw.
        monkeypatch.setenv("PERSONALCLAW_HOME", "~/.personalclaw")
        with pytest.raises(SystemExit) as exc:
            _resolve_gateway_args(_ns(approval="yolo"))
        assert exc.value.code == 2
        # Confirm we hit the same-as-default branch (not the resolve-failure branch).
        captured = capsys.readouterr()
        assert "main gateway home" in captured.err

    def test_yolo_accepted_with_isolated_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        result = _resolve_gateway_args(_ns(approval="yolo"))
        assert result["approval_mode"] == "yolo"

    def test_yolo_accepted_via_test_mode_bundle_with_isolated_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        result = _resolve_gateway_args(_ns(test_mode=True, approval="yolo"))
        assert result["approval_mode"] == "yolo"
        assert result["port_override"] == "auto"

    def test_reads_mode_skips_safety_rail(self):
        # Rail only applies to yolo; reads should pass with default home.
        result = _resolve_gateway_args(_ns(approval="reads"))
        assert result["approval_mode"] == "reads"

    def test_interactive_mode_skips_safety_rail(self):
        result = _resolve_gateway_args(_ns(approval="interactive"))
        assert result["approval_mode"] == "interactive"


# ─── --approval reads: a call that only reads, by its tool's declaration ──


class TestApprovalReads:
    """`--approval reads` auto-approves a call whose EFFECTIVE risk is safe — a tool that
    declares it only reads, or a read-only shell command — and asks about everything else,
    whatever the tool is called. It used to read the tool's NAME: a leading read verb and no
    write-shaped word passed, so `list_and_archive` and `get_and_move` ran unasked."""

    @staticmethod
    def _orch():
        from unittest.mock import AsyncMock, MagicMock, patch

        from personalclaw.config.loader import AppConfig
        from personalclaw.gateway import GatewayOrchestrator

        cfg = AppConfig()
        with patch.object(cfg, "load_credentials", return_value={}):
            orch = GatewayOrchestrator(cfg, approval_mode="reads")
        state = MagicMock()
        state._sessions = {}
        state.is_yolo_active.return_value = False
        # The human's answer when the gate asks: no. So an ASK reads False and an auto-approve
        # reads True, and `request_approval` being awaited at all is the evidence it asked.
        state.request_approval = AsyncMock(return_value=False)
        orch.dashboard_state = state
        return orch, state

    @staticmethod
    def _event(title, *, risk_level="", tool_input=None, tool_kind=""):
        from personalclaw.llm.events import EVENT_PERMISSION_REQUEST, AgentEvent

        return AgentEvent(
            kind=EVENT_PERMISSION_REQUEST,
            request_id="r1",
            title=title,
            tool_kind=tool_kind,
            risk_level=risk_level,
            tool_input=tool_input if tool_input is not None else {},
        )

    async def _decide(self, event):
        from unittest.mock import patch

        orch, state = self._orch()
        with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
            decision = await orch._interactive_approval("cron")(event, "")
        # A `ToolDecision`, truthy exactly when the call may run.
        return bool(decision), state.request_approval.await_count

    @pytest.mark.asyncio
    @pytest.mark.parametrize("title", ["read_file", "memory_recall", "knowledge_search"])
    async def test_a_declared_read_runs_unasked(self, title):
        approved, asked = await self._decide(self._event(title, risk_level="safe"))
        assert (approved, asked) == (True, 0)

    @pytest.mark.asyncio
    async def test_a_read_only_command_runs_unasked(self):
        event = self._event("bash", risk_level="destructive", tool_input={"command": "ls -la"})
        assert await self._decide(event) == (True, 0)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "title", ["list_and_archive", "get_and_move", "find_and_move_files", "memory_remember"]
    )
    async def test_a_declared_change_asks_whatever_its_name(self, title):
        approved, asked = await self._decide(self._event(title, risk_level="caution"))
        assert (approved, asked) == (False, 1)

    @pytest.mark.asyncio
    async def test_a_call_that_declares_nothing_asks(self):
        # An ACP CLI's own read-labelled tool: its kind is not a declaration.
        event = self._event("Read foo.txt", tool_kind="read")
        assert await self._decide(event) == (False, 1)

    @pytest.mark.asyncio
    async def test_a_mutating_command_asks(self):
        event = self._event("bash", risk_level="destructive", tool_input={"command": "rm -rf x"})
        assert await self._decide(event) == (False, 1)
