"""AG-11 — deferred profile/trust enforcement behaviours (AUTONOMY-GUARDRAILS §4.1/§4.3 + cron).

Three security controls, each tested in the DANGEROUS direction — the thing that must be DENIED is
actually denied, because getting any wrong widens what unattended code may do:

1. §4.1 read-only research subagent class — an auto-fired research spawn's write/execute tools are
   denied at the tool-approval layer (``subagent._run_inner``), not merely declared.
2. Cron-approval rewire — an unattended result-injection turn resolves its approval through
   ``profile_for_session`` (via ``gateway.injection_approval_policy``), not a blanket AUTO_APPROVE.
3. §4.3 project Trust/Preview gate — a project folder's first script touch persists a Preview
   decision and runs read-only (REVIEW_ONLY); only an explicit Trust admits a write grant.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.hooks import TOOL_AUTO_APPROVE, ToolHookResult
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    LLMEvent,
)
from personalclaw.subagent import (
    CAPABILITY_MUTATING,
    CAPABILITY_RESEARCH,
    SubagentManager,
    resolve_capability_class,
)

# ── shared subagent harness ─────────────────────────────────────────────────


@pytest.fixture()
def agent_root(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _mock_memory_ok(monkeypatch):
    monkeypatch.setattr("personalclaw.subagent.check_memory_available", lambda **_kw: (True, 8.0))


def _manager_with_tool(tool_title: str, *, hook_action: str = TOOL_AUTO_APPROVE):
    """A SubagentManager whose subagent stream emits ONE permission request for ``tool_title``.

    ``provider.approve_tool`` / ``provider.reject_tool`` are AsyncMocks so a test can assert which
    fired. The hook returns ``hook_action`` (default: auto-approve) so a tool that clears the §4.1
    research gate is admitted — isolating the gate from the surrounding approval plumbing."""
    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    provider = AsyncMock()
    provider.start = AsyncMock()
    provider.shutdown = AsyncMock()
    provider.context_usage_pct = lambda: 0.0
    provider.approve_tool = AsyncMock()
    provider.reject_tool = AsyncMock()

    async def _stream(*_a, **_kw):
        yield LLMEvent(
            kind=EVENT_PERMISSION_REQUEST, title=tool_title, request_id=1, tool_kind="fs"
        )
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield LLMEvent(kind=EVENT_COMPLETE)

    provider.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    sessions.get_or_create = AsyncMock(return_value=(provider, True, False))
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    sessions.record_success = MagicMock()
    sessions.get_agent = MagicMock(return_value="")
    sessions.has_session = MagicMock(return_value=False)
    sessions.get_approval_policy = MagicMock(return_value="")

    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("msg", None))
    ctx.hooks.on_tool_call = MagicMock(return_value=ToolHookResult(action=hook_action))
    ctx.hooks.auto_approve_subagent_spawn = True
    ctx.hooks.auto_approve_subagent_tools = False

    manager = SubagentManager(sessions=sessions, ctx_builder=ctx)
    return manager, provider


async def _run_spawn(manager, *, capability_class=None, approval_mode="auto"):
    with (
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.subagent.sel"),
        patch("personalclaw.guardrails.policy.ceiling_permits_approval", lambda _v: True),
    ):
        info = manager.spawn(
            "task",
            parent_session_key="",
            approval_mode=approval_mode,
            capability_class=capability_class,
        )
        assert info is not None
        await manager._tasks[info.id]
    return info


# ── §4.1 capability-class resolution ─────────────────────────────────────────


def test_resolve_capability_class_auto_fired_defaults_research():
    # "default research for auto-fired spawns" — the read-only default an unattended run gets.
    assert (
        resolve_capability_class(capability_class="", approval_mode="auto") == CAPABILITY_RESEARCH
    )
    # A human-watched spawn (no auto grant) keeps the full grant — behaviour-preserving.
    assert resolve_capability_class(capability_class="", approval_mode="") == CAPABILITY_MUTATING
    # An explicit class always wins over the by-construction default.
    assert (
        resolve_capability_class(capability_class="mutating", approval_mode="auto")
        == CAPABILITY_MUTATING
    )
    assert (
        resolve_capability_class(capability_class="research", approval_mode="")
        == CAPABILITY_RESEARCH
    )


def test_capability_constants_stay_coherent_across_seams():
    """A research SUBAGENT, a research LEAF and a Preview project run must mean the SAME thing —
    one write-tool policy, not three that drift."""
    from personalclaw.guardrails.project_trust import PREVIEW_CAPABILITY
    from personalclaw.workflows.batch_compile import Capability

    assert CAPABILITY_RESEARCH == Capability.RESEARCH.value == PREVIEW_CAPABILITY
    assert CAPABILITY_MUTATING == Capability.MUTATING.value


# ── §4.1 the DANGEROUS direction: a research spawn must be denied write/execute ──────


@pytest.mark.asyncio
async def test_auto_fired_research_spawn_denies_write_tool(agent_root):
    """LOAD-BEARING. An auto-fired spawn defaults to the research class; its Write tool is DENIED
    at the approval layer. A research subagent gaining write is the exact escalation this prevents.
    """
    manager, provider = _manager_with_tool("Write")
    await _run_spawn(manager, capability_class=None, approval_mode="auto")
    provider.reject_tool.assert_awaited_once_with(1)
    provider.approve_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_fired_research_spawn_denies_bash_execute(agent_root):
    """LOAD-BEARING. Execute tools are denied too — ``Bash`` is an execute verb the research class
    refuses, not just filesystem writes."""
    manager, provider = _manager_with_tool("Bash")
    await _run_spawn(manager, capability_class=None, approval_mode="auto")
    provider.reject_tool.assert_awaited_once_with(1)
    provider.approve_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_research_spawn_allows_read_tool(agent_root):
    """The gate is scoped, not deny-all: a read-only tool clears the research gate and is admitted
    (here by the auto-approve hook). Proves the deny is about WRITES, not every tool."""
    manager, provider = _manager_with_tool("read_file")
    await _run_spawn(manager, capability_class=None, approval_mode="auto")
    provider.approve_tool.assert_awaited_once_with(1)
    provider.reject_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_mutating_spawn_allows_write_tool(agent_root):
    """An explicit mutating grant (the creation-time write grant) admits Write — the research gate
    does not fire, so a legitimately-granted unattended writer still works."""
    manager, provider = _manager_with_tool("Write")
    await _run_spawn(manager, capability_class="mutating", approval_mode="auto")
    provider.approve_tool.assert_awaited_once_with(1)
    provider.reject_tool.assert_not_awaited()


# ── cron rewire: an unattended injection turn resolves through profile_for_session ───


def test_injection_policy_unattended_resolves_through_profile():
    """LOAD-BEARING. A cron/unattended parent's injection turn is NOT blanket AUTO_APPROVE — it is
    the profile-derived policy. Bypassing this back to AUTO_APPROVE reds here."""
    from personalclaw.gateway import injection_approval_policy
    from personalclaw.guardrails.policy import approval_policy_for_session
    from personalclaw.llm_helpers import ToolApprovalPolicy

    for key in ("cron:nightly", "subagent:x", "loop-abc", "_bg"):
        got = injection_approval_policy(key)
        assert got is approval_policy_for_session(key)
        assert got is ToolApprovalPolicy.HOOK_BASED  # HEADLESS.approval == "hook_based"
        assert got is not ToolApprovalPolicy.AUTO_APPROVE


def test_injection_policy_interactive_stays_auto_approve():
    """An interactive (dashboard chat) parent keeps AUTO_APPROVE — a human is present. This is the
    behaviour-preservation half of the rewire."""
    from personalclaw.gateway import injection_approval_policy
    from personalclaw.llm_helpers import ToolApprovalPolicy

    assert injection_approval_policy("dashboard:main") is ToolApprovalPolicy.AUTO_APPROVE
    assert injection_approval_policy("chat:main") is ToolApprovalPolicy.AUTO_APPROVE


def test_injection_policy_is_derived_not_constant(monkeypatch):
    """Proof it READS the profile: forcing an unattended session's profile to approval="auto" makes
    the injection turn AUTO_APPROVE. A hardcoded HOOK_BASED would ignore this and fail."""
    from personalclaw.gateway import injection_approval_policy
    from personalclaw.guardrails import policy as _policy
    from personalclaw.guardrails.policy import SafetyProfile
    from personalclaw.llm_helpers import ToolApprovalPolicy

    monkeypatch.setattr(
        _policy, "profile_for_session", lambda _k: SafetyProfile(name="x", approval="auto")
    )
    assert injection_approval_policy("cron:nightly") is ToolApprovalPolicy.AUTO_APPROVE


# ── §4.3 project Trust/Preview gate ──────────────────────────────────────────


@pytest.fixture()
def trust_home(tmp_path, monkeypatch):
    """Isolate the project_trust store (and any inbox write) to tmp_path — never the real home."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def test_first_touch_persists_preview_and_forces_readonly(trust_home, monkeypatch):
    """LOAD-BEARING. A project folder's FIRST touch: (a) persists a Preview record, and (b) forces
    read-only EVEN when the caller passed a mutating write grant. Preview permitting execution, or
    the first touch not persisting, reds here."""
    import json

    from personalclaw.guardrails import project_trust as pt

    prompts: list[str] = []
    monkeypatch.setattr(pt, "_prompt_trust_vs_preview", lambda d, s: prompts.append(d))

    project = str(trust_home / "proj")
    (trust_home / "proj").mkdir()

    # A write grant is requested, but an untrusted folder is READ-ONLY.
    assert pt.gate_project_capability(project, CAPABILITY_MUTATING) == pt.PREVIEW_CAPABILITY

    # First touch persisted a Preview record keyed by the resolved dir.
    store = json.loads((trust_home / "project_trust.json").read_text())
    key = pt.resolve_dir(project)
    assert key in store and store[key]["trusted"] is False and "decided_at" in store[key]
    # And it prompted the user exactly once.
    assert prompts == [key]


def test_preview_does_not_reprompt_on_second_touch(trust_home, monkeypatch):
    """The prompt fires ONCE: a folder that fires every 20 minutes must not stack prompts. Second
    touch stays read-only but does not re-prompt (the decision already persisted)."""
    from personalclaw.guardrails import project_trust as pt

    prompts: list[str] = []
    monkeypatch.setattr(pt, "_prompt_trust_vs_preview", lambda d, s: prompts.append(d))
    project = str(trust_home / "proj")
    (trust_home / "proj").mkdir()

    pt.gate_project_capability(project, CAPABILITY_MUTATING)
    pt.gate_project_capability(project, CAPABILITY_MUTATING)
    assert len(prompts) == 1  # only the first touch prompted


def test_trusted_folder_honors_write_grant(trust_home):
    """An explicit Trust admits the write grant — Trust is what lets project scripts run/write."""
    from personalclaw.guardrails import project_trust as pt

    project = str(trust_home / "proj")
    (trust_home / "proj").mkdir()
    pt.record_project_trust(project, trusted=True)
    assert pt.gate_project_capability(project, CAPABILITY_MUTATING) == CAPABILITY_MUTATING
    # A trusted folder with no grant still resolves by the caller's default (None passes through).
    assert pt.gate_project_capability(project, None) is None


def test_preview_folder_stays_readonly(trust_home):
    """A folder explicitly kept in Preview (trusted=False) forces read-only regardless of grant."""
    from personalclaw.guardrails import project_trust as pt

    project = str(trust_home / "proj")
    (trust_home / "proj").mkdir()
    pt.record_project_trust(project, trusted=False)
    assert pt.project_decision(project) == pt.DECISION_PREVIEW
    assert pt.gate_project_capability(project, CAPABILITY_MUTATING) == pt.PREVIEW_CAPABILITY


def test_blank_cwd_passes_through(trust_home):
    """No project folder → no gate: the grant passes through unchanged (gate is project-only)."""
    from personalclaw.guardrails import project_trust as pt

    assert pt.gate_project_capability("", CAPABILITY_MUTATING) == CAPABILITY_MUTATING
    assert pt.gate_project_capability("   ", None) is None


def test_corrupt_store_is_preview_not_trusted(trust_home):
    """Fail-CLOSED for the decision: an unreadable store treats every folder as Preview (read-only),
    never trusted. A corrupt file must never widen access."""
    from personalclaw.guardrails import project_trust as pt

    (trust_home / "project_trust.json").write_text("}{ not json")
    project = str(trust_home / "proj")
    (trust_home / "proj").mkdir()
    assert pt.project_decision(project) == pt.DECISION_UNKNOWN
    assert pt.gate_project_capability(project, CAPABILITY_MUTATING) == pt.PREVIEW_CAPABILITY


def test_record_and_decision_round_trip(trust_home):
    """Trust/Preview decisions persist and read back keyed by resolved dir (symlink/./ collapse)."""
    from personalclaw.guardrails import project_trust as pt

    project = str(trust_home / "proj")
    (trust_home / "proj").mkdir()
    assert pt.project_decision(project) == pt.DECISION_UNKNOWN
    pt.record_project_trust(project + "/.", trusted=True)  # a different spelling of the same dir
    assert pt.project_decision(project) == pt.DECISION_TRUSTED
