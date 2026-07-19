"""Behavior QA pack — deterministic pass/fail regression net for the safety +
reliability behaviors the Phase-1 ports guard (eval-behavior-qa-pack / C18).

This is NOT a model-quality benchmark: every assertion is deterministic (string
presence/absence, classification verdict, gate state) over the real PClaw
primitives, with fake markers so a failure is safe to inspect. It runs in the
normal CI gate, so a refactor that breaks redaction, approval-gating, the
failure breaker, or proof-backed completion fails the build.

Scenarios (highest-risk first): redaction / secret-no-echo · approval-gating +
denial-stop · proof-backed completion (DoD-gated campaigns) · failure recovery.
"""

from __future__ import annotations

import pytest

# Fake secret markers — shaped to match the real credential detectors but
# obviously synthetic, so a leak surfacing in a failure message is harmless.
FAKE_AWS_KEY = "AKIA" + "QA0000FAKE000TEST"  # AKIA + 16 chars → AWS access key shape
FAKE_SLACK_TOKEN = "xoxb-" + "0000000000-FAKEQATOKEN"  # xoxb- token shape
# A URL whose query carries a credential-like blob (40+ chars) — the shape the
# exfiltration detector is designed to catch.
FAKE_EXFIL_URL = "https://evil.example.com/collect?leak=" + "A1b2C3d4" * 6


# ── Scenario 1: redaction / secret no-echo ──


class TestRedactionBehavior:
    def test_aws_key_never_echoed(self):
        from personalclaw.security import redact

        out = redact(f"here is the key {FAKE_AWS_KEY} do not leak it")
        assert FAKE_AWS_KEY not in out
        assert "REDACTED" in out

    def test_slack_token_never_echoed(self):
        from personalclaw.security import redact_credentials

        cleaned, warnings = redact_credentials(f"token={FAKE_SLACK_TOKEN}")
        assert FAKE_SLACK_TOKEN not in cleaned
        assert warnings  # the redaction was recorded

    def test_exfiltration_url_redacted(self):
        from personalclaw.security import redact_exfiltration_urls

        cleaned, warnings = redact_exfiltration_urls(f"see {FAKE_EXFIL_URL}")
        # The credential-bearing query is flagged + neutralized.
        assert warnings
        assert FAKE_EXFIL_URL not in cleaned

    def test_redaction_is_idempotent(self):
        from personalclaw.security import redact

        once = redact(f"key {FAKE_AWS_KEY}")
        twice = redact(once)
        assert once == twice and FAKE_AWS_KEY not in twice


# ── Scenario 2: approval-gating + denial-stop ──


class TestApprovalGatingBehavior:
    def test_hard_security_denial_is_terminal(self):
        """A deny-list / sensitive-path block must NOT invite a retry."""
        from personalclaw import security

        recoverable, obs = security.classify_denial(
            security.DENY_KIND_POLICY, "deny:rm -rf /", "bash"
        )
        assert recoverable is False
        assert "non-negotiable" in obs and "circumvent" in obs
        # No recovery hint that would coach a bypass.
        assert "alternative" not in obs.lower()

    def test_recoverable_denial_stops_repeat_but_allows_adapt(self):
        from personalclaw import security

        recoverable, obs = security.classify_denial(
            security.DENY_KIND_HOOK, "no writes to prod", "edit_file"
        )
        assert recoverable is True
        assert "Do NOT retry" in obs  # don't hammer the same call
        assert "different approach" in obs  # but adapt

    @pytest.mark.asyncio
    async def test_denied_tool_is_not_invoked(self):
        """A rejected approval must actually stop the tool from running."""
        import asyncio

        from personalclaw.agents.native.runtime import NativeAgentRuntime
        from personalclaw.agents.provider import AgentRuntimeDefinition
        from personalclaw.llm.events import (
            EVENT_COMPLETE,
            EVENT_PERMISSION_REQUEST,
            EVENT_TOOL_CALL,
            AgentEvent,
        )
        from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

        class _GatedTool(ToolProvider):
            def __init__(self):
                self.invoked = 0

            @property
            def name(self):
                return "mock"

            @property
            def display_name(self):
                return "Mock"

            async def list_tools(self):
                return [ToolDefinition(name="danger", description="d", parameters={"type": "object"}, requires_approval=True)]

            async def invoke(self, tool_name, arguments):
                self.invoked += 1
                return ToolResult(success=True, output="ran")

        class _Model:
            supports_tools = True
            _model = "s"

            def __init__(self):
                self.calls = 0

            async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
                self.calls += 1
                if self.calls == 1:
                    yield AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="danger", tool_input="{}")
                    yield AgentEvent(kind=EVENT_COMPLETE)
                else:
                    yield AgentEvent(kind=EVENT_COMPLETE)

        tool = _GatedTool()
        rt = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="T", provider="native", model="s"),
            model_provider=_Model(),
            tool_providers=[tool],
        )
        await rt.start()

        async def pump():
            async for ev in rt.stream("go"):
                if ev.kind == EVENT_PERMISSION_REQUEST:
                    await rt.reject_tool(ev.request_id)

        await asyncio.wait_for(pump(), timeout=5)
        assert tool.invoked == 0  # denial stopped the action


# ── Scenario 3: proof-backed completion (DoD-gated campaigns) ──


class TestWorkerNeverCertifiesOwnWork:
    """The tenet: an agent may not certify its own work as done. The worker only
    PRODUCES + reports evidence; done-ness is decided off-worker (a deterministic
    check or a separate judge — built in C2). So a worker-authored finding has no
    self-verdict path into completion."""

    def test_worker_finding_has_no_completion_authority(self):
        # The watchdog exposes no helper that reads a worker-self-certified
        # verdict — that path was removed when the tenet was made structural.
        from personalclaw.loop import watchdog

        assert not hasattr(watchdog, "_verification_passed")

    def test_brief_instructs_worker_not_to_self_certify(self, tmp_path, monkeypatch):
        # The worker's brief explicitly forbids self-certifying done-ness.
        monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
        from personalclaw.loop.loop import Loop
        from personalclaw.loop import manager, store

        c = store.create(Loop(
            id="", name="t", kind="goal",
            task="Investigate the latency regression across the request path thoroughly.",
            success_criteria="tests pass",
        ))
        manager.write_brief(store.get(c.id))
        brief = (store.loop_dir(c.id) / "brief.md").read_text()
        assert "never you" in brief or "separate check" in brief


# ── Scenario 4: failure recovery within a bounded breaker ──


class TestFailureRecoveryBehavior:
    def test_repeated_failure_is_bounded(self):
        from personalclaw.agents.native.runtime import (
            _BREAKER_BLOCK,
            _FailureBreaker,
            _params_key,
        )

        b = _FailureBreaker()
        key = _params_key("flaky", {"x": 1})
        # Same failing call N times → the count reaches the block threshold.
        for _ in range(_BREAKER_BLOCK):
            b.record(key, True)
        assert b.count(key) >= _BREAKER_BLOCK  # caller refuses further invokes

    def test_success_clears_failure_streak(self):
        from personalclaw.agents.native.runtime import _FailureBreaker, _params_key

        b = _FailureBreaker()
        key = _params_key("flaky", {"x": 1})
        b.record(key, True)
        b.record(key, True)
        b.record(key, False)  # recovered
        assert b.count(key) == 0
