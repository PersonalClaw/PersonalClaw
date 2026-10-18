"""A hook's exit-2 block is recorded as `blocked` only where it was HONORED.

🔴 **The measured defect.** A `PreToolUse` hook fired through the informational
`fire_tool_hooks` seam recorded `last_status: "blocked"` next to `enforcement: "enforcing"` —
and the out-of-workspace write it claimed to block landed on disk. Driven twice, on codex
and on claude-code (the hook fired 3× and `hooked1.txt` still said
`HOOKED`). Worse than inert: it reported success.

`ActionResult.blocked` is a *request* ("PreToolUse exit_code 2 is a block signal"), and only
`fire_for_ids` — whose one pre-tool caller, `pre_tool_hooks`, turns it into the call's refusal —
converts that request into a refusal. So the status is written per FIRE, not per exit code.

The three states an operator must be able to tell apart from the trigger surface, each pinned
below:

1. fired and BLOCKED         → `last_status == "blocked"`, outcome `REFUSED`
2. fired and only REPORTED   → `last_status == "advisory"`, outcome `RAN` + a reason saying so
3. never fired               → `hook_to_record` returns None (no row at all)

`test_informational_fire_never_records_blocked` is the rail: revert the `enforced` branch in
`hooks.run_script_hook` and it goes red, which is the whole point of it existing.
"""

from __future__ import annotations

import asyncio

import hook_advisory_status_shared

from personalclaw.hooks import (
    ENFORCEMENT_ADVISORY,
    HOOK_EVENT_PRE_TOOL_USE,
    HOOK_EVENT_STOP,
    ScriptHook,
    ScriptHookStore,
    fire_tool_hooks,
)
from personalclaw.triggers.history import HOOK_STATUS_TO_OUTCOME, hook_to_record
from personalclaw.triggers.models import Outcome

_isolated = hook_advisory_status_shared._isolated


def _store(tmp_path, event: str = HOOK_EVENT_PRE_TOOL_USE) -> tuple[ScriptHookStore, ScriptHook]:
    store = ScriptHookStore(config_dir=tmp_path)
    hook = store.create(
        ScriptHook(
            id="h1",
            name="policy",
            event=event,
            provider="bash",
            provider_config={"command": "exit 2"},
            enabled=True,
            # The owner's yes (`triggers.grants`): these tests are about what a fire that RUNS
            # records. An ungranted hook's refusal on the gating seam is
            # `test_a_grant_is_for_the_action_the_owner_allowed`'s.
            capabilities={"providers": ["bash"]},
        ).to_dict()
    )
    return store, hook


# ── (2) the informational path REPORTS ──


def test_informational_fire_never_records_blocked(tmp_path):
    """THE RAIL. `fire()` is the global/informational seam — nothing consumes its verdict."""
    store, hook = _store(tmp_path)
    asyncio.run(store.fire(HOOK_EVENT_PRE_TOOL_USE, tool_name="write_file"))
    assert hook.last_status != "blocked", (
        "the informational fire reported an enforcement it never achieved — this seam "
        "cannot block, the tool is already running"
    )
    assert hook.last_status == "advisory"
    assert hook.run_count == 1


def test_fire_tool_hooks_seam_records_advisory(tmp_path):
    """The measured site itself: `chat_runner`'s ACP `EVENT_TOOL_CALL` adapter."""
    store, hook = _store(tmp_path)
    asyncio.run(fire_tool_hooks(store, "Running: write_file", '{"path": "/tmp/hooked1.txt"}'))
    assert hook.last_status == "advisory"


def test_stop_hook_exit_two_is_advisory_on_either_path(tmp_path):
    """`PreToolUse` is the only event with a block seam, so a `Stop` hook never blocks —
    not even through `fire_for_ids`, which is otherwise the enforcing path."""
    store, hook = _store(tmp_path, event=HOOK_EVENT_STOP)
    asyncio.run(store.fire_for_ids(HOOK_EVENT_STOP, [hook.id]))
    assert hook.last_status == "advisory"


# ── (1) the gating path BLOCKS ──


def test_gating_fire_records_blocked(tmp_path):
    """`fire_for_ids` + a blocking event: the one step every path asks (`pre_tool_hooks`) turns
    this exit 2 into the call's refusal, so the block really happened."""
    store, hook = _store(tmp_path)
    asyncio.run(store.fire_for_ids(HOOK_EVENT_PRE_TOOL_USE, [hook.id], tool_name="write_file"))
    assert hook.last_status == "blocked"


# ── the trigger surface: what an operator reads ──


def test_advisory_is_mapped_and_projects_as_ran_with_a_reason(tmp_path, caplog):
    """`hook_to_record` reports an unmapped status as FAILED plus a warning, so a new status must
    be mapped in the same change that writes it."""
    assert "advisory" in HOOK_STATUS_TO_OUTCOME
    # ONE vocabulary, two levels: `enforcement` says whether this hook CAN block, `last_status`
    # says whether the last fire DID. The shared word is deliberate, not a coincidence to rename.
    assert ENFORCEMENT_ADVISORY == "advisory"
    hook = ScriptHook(
        id="h1", name="policy", event=HOOK_EVENT_PRE_TOOL_USE, provider="bash", enabled=True
    )
    hook.last_status = "advisory"
    hook.last_run = 1_700_000_000.0
    hook.run_count = 3
    with caplog.at_level("WARNING"):
        record = hook_to_record(hook)
    assert record is not None
    assert record.outcome == Outcome.RAN.value  # it ran; nothing was refused
    assert "reported only" in record.reason and "already ran" in record.reason
    assert "HOOK_STATUS_TO_OUTCOME" not in caplog.text


def test_the_three_states_are_distinguishable(tmp_path):
    """(1) vs (2) vs (3) from the projection alone — the operator's actual read."""

    def _row(status: str, run_count: int):
        hook = ScriptHook(id="h1", name="policy", event=HOOK_EVENT_PRE_TOOL_USE, provider="bash")
        hook.last_status = status
        hook.last_run = 1_700_000_000.0 if run_count else 0.0
        hook.run_count = run_count
        return hook_to_record(hook)

    blocked = _row("blocked", 1)
    advisory = _row("advisory", 1)
    never = _row("", 0)

    assert blocked is not None and blocked.outcome == Outcome.REFUSED.value
    assert advisory is not None and advisory.outcome == Outcome.RAN.value
    assert never is None
    assert len({blocked.outcome, advisory.outcome}) == 2
