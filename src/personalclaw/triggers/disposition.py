"""The disposition table, as code (AUTOMATION-SUBSTRATE §2 — S63).

§2 is a 17-row table saying what happens to every automation-adjacent surface in the codebase:
ABSORBED, KEPT, or KEPT-and-gains-a-duty. It is the most consequential document in this plan — a
surface absorbed by mistake loses its semantics, and a surface kept by mistake means two schedulers
running at once.

It lives here as data rather than only in prose because a table in a markdown file cannot be checked
against the tree. `missing_surfaces()` verifies every module the table
names still exists, so a rename
during the migration fails a test instead of leaving a row pointing at nothing. That is the same
reasoning as S62's `LEGACY_FIELD_MAP`: the mapping is the artifact, and an unchecked mapping is a
document that is true when written and wrong later.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Verdict(str, Enum):
    """What happens to a surface.

    `KEPT_WITH_DUTY` is separate from `KEPT` because the two produce
    different work: a kept surface is
    untouched, while one that gains a duty needs an edit in this program. Collapsing them lets a
    required emission (fs_watch publishing `FileChanged`, the inbox emitting `InboxItemIngested`)
    read as "nothing to do here".
    """

    ABSORBED = "absorbed"
    KEPT = "kept"
    KEPT_WITH_DUTY = "kept_with_duty"


@dataclass(frozen=True)
class Row:
    """One surface's disposition."""

    surface: str
    module: str
    verdict: Verdict
    #: What must be preserved VERBATIM, for an absorbed surface. Named per row because "absorbed"
    #: without this list is how a rewrite loses the semantics a rename would have kept.
    keeps: tuple[str, ...] = ()
    note: str = ""


#: The table. Ordered as §2 orders it, so a reader can diff the two.
DISPOSITION: tuple[Row, ...] = (
    Row(
        surface="schedule.py machinery",
        module="personalclaw.schedule",
        verdict=Verdict.ABSORBED,
        keeps=(
            "single re-armed asyncio timer (<=30s poll)",
            "croniter minute-match dueness",
            "same-minute refire guard",
            "deterministic per-id BLAKE2b jitter",
            "IANA tz + skip_dates",
            "mtime _sync for external edits",
            "fcntl .crons.lock",
            "per-job timeout + reaper (SIGKILL escalation, PID-recycle checks)",
            "_merge_job_result runtime-field merge-back",
            "canonical action {provider, config} execution model",
        ),
        note="Rename, not rewrite. The crash discipline is layered ON this mechanism.",
    ),
    Row(
        surface="schedule_history.py ScheduleRun",
        module="personalclaw.schedule_history",
        verdict=Verdict.ABSORBED,
        keeps=(
            "honest launched != succeeded",
            "dry-run replay",
            "JSONL caps 100/job + index",
            "last_run_status() reads history, not the volatile job field",
        ),
        note="Becomes the ledger-only fire record, extended with §1.3's typed outcomes.",
    ),
    Row(
        surface="hooks.py ScriptHooks",
        module="personalclaw.hooks",
        verdict=Verdict.ABSORBED,
        keeps=(
            "blocking PreToolUse stays synchronous",
            "agent scoping via fire_for_ids",
            "__hook_depth cap folds into __wf_depth",
        ),
        note="Only 7 of 15 events fire today; wiring the other 8 is §7 step 1.",
    ),
    Row(
        surface="hooks.py HookManager (declarative rules)",
        module="personalclaw.hooks",
        verdict=Verdict.KEPT,
        note="A policy layer, not an automation. Stays in config.json.",
    ),
    Row(
        surface="event_triggers.py",
        module="personalclaw.event_triggers",
        verdict=Verdict.ABSORBED,
        keeps=("max_fires / debounce / rate-cap become trigger gates",),
        note="Fixes the verified sync-CLI silent-skip: a fire with no running loop recorded "
        "fire_count and skipped the action. Fires now spool (§3.3).",
    ),
    Row(
        surface="autonudge.py",
        module="personalclaw.autonudge",
        verdict=Verdict.ABSORBED,
        keeps=(
            "reactive re-arm",
            "delivered-only cycle counting",
            "mid-turn drop == overlap: skip",
            "stop-sentinel",
            "error_count deactivation",
        ),
        note="LAST — blocked on LOOPS-EVOLUTION Phase 4, because the loop engine rides autonudge "
        "as its tick engine. kind:idle ships for USER automations before that.",
    ),
    Row(
        surface="heartbeat.py tasks",
        module="personalclaw.heartbeat",
        verdict=Verdict.ABSORBED,
        keeps=("HEARTBEAT_KEEP retry semantics via the deferred outcome",),
        note="The 4 tick-modulo maintenance sub-tasks become visible, pausable system triggers.",
    ),
    Row(
        surface="Inbox poll loop",
        module="personalclaw.inbox",
        verdict=Verdict.KEPT_WITH_DUTY,
        note="Provider polling is plumbing, not user automation. ONE new duty: emit "
        "InboxItemIngested onto the bus.",
    ),
    Row(
        surface="fs_watch.py",
        module="personalclaw.fs_watch",
        verdict=Verdict.KEPT_WITH_DUTY,
        note="Stays the SSE refresh engine; additionally publishes FileChanged. kind:file "
        "triggers register EXPLICIT watch roots with a path cap — the poller must not become "
        "a battery drain.",
    ),
    Row(
        surface="after_turn_review.py",
        module="personalclaw.after_turn_review",
        verdict=Verdict.KEPT,
        note="Hot-path and cheap; per-turn run records would be journal spam. Surfaced as a "
        "read-only row marked execution: external so the substrate's invariant stays honest.",
    ),
    Row(
        surface="suggestions.py",
        module="personalclaw.suggestions",
        verdict=Verdict.KEPT,
        note="Read-time computation. The counter-example stays the counter-example.",
    ),
    Row(
        surface="engagement_signals.py",
        module="personalclaw.engagement_signals",
        verdict=Verdict.KEPT,
    ),
    Row(
        surface="/api/triggers facade",
        module="personalclaw.dashboard.handlers.triggers",
        verdict=Verdict.KEPT,
        note="Becomes the single API: re-pointed at triggers.json instead of three stores. The id "
        "namespace becomes the migration map.",
    ),
    Row(
        surface="App crons",
        module="personalclaw.apps.app_crons",
        verdict=Verdict.ABSORBED,
        keeps=(
            "manifest jobs reconcile at startup (pruned/converged)",
            "gated on can_use_cron",
            "force-silent because the pseudo-user cannot receive a DM",
        ),
    ),
)


def absorbed() -> tuple[Row, ...]:
    return tuple(r for r in DISPOSITION if r.verdict is Verdict.ABSORBED)


def gains_a_duty() -> tuple[Row, ...]:
    """Surfaces that are kept but need an edit in this program.

    The list a session-64 reader needs: these are the emissions the event bus depends on, and a kept
    surface that never gained its duty is a bus with no publishers.
    """
    return tuple(r for r in DISPOSITION if r.verdict is Verdict.KEPT_WITH_DUTY)


def missing_surfaces() -> list[str]:
    """Modules the table names that do not import.

    Run by a test so a rename during the migration fails loudly rather than leaving a table row
    pointing at nothing. A disposition table nobody checks is a document that was true when written.
    """
    import importlib

    missing: list[str] = []
    for row in DISPOSITION:
        try:
            importlib.import_module(row.module)
        except Exception:
            missing.append(f"{row.surface} -> {row.module}")
    return missing
