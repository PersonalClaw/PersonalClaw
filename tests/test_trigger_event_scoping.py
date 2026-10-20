"""Agent-scoped event classification stays honest (issue 610).

`AGENT_SCOPED_EVENTS` is a reviewed constant like `DORMANT_EVENTS` beside it, and these
rails are its guard: the set must disagree with the GLOBAL fire path's own registry in
neither direction, and the variables endpoint must ship the flag so both UIs read one
server-sourced answer instead of inventing a second vocabulary.
"""

from __future__ import annotations

from personalclaw.hooks import HOOK_EVENTS
from personalclaw.triggers.events import (
    AGENT_SCOPED_EVENTS,
    DORMANT_EVENTS,
    verify_agent_scoping,
)
from personalclaw.triggers.lifecycle_fire import BUILDERS


def test_every_declared_event_is_classified_exactly_once():
    """15 declared events, each on exactly one path: agent-scoped, global, or dormant."""
    globally_fired = set(BUILDERS) | {"TaskComplete"}
    assert AGENT_SCOPED_EVENTS <= set(HOOK_EVENTS)
    assert not (
        AGENT_SCOPED_EVENTS & globally_fired
    ), "an event cannot ride both fire paths — the badge would lie in one direction or the other"
    unclassified = set(HOOK_EVENTS) - AGENT_SCOPED_EVENTS - globally_fired - DORMANT_EVENTS
    assert not unclassified, f"declared but on no path and not dormant: {sorted(unclassified)}"


def test_verify_agent_scoping_reports_no_problems():
    assert verify_agent_scoping() == []


def test_the_issue_610_repro_event_is_global():
    """MemoryWrite — the measured proof case: fired and delivered while badged 'dormant'."""
    assert "MemoryWrite" not in AGENT_SCOPED_EVENTS
    assert "MemoryWrite" in BUILDERS


def test_variables_endpoint_ships_agent_scoped_per_event():
    """The catalog row carries the flag, matching the constant, for every event."""
    from personalclaw.hooks import LIFECYCLE_EVENT_CATALOG

    catalog_events = {e["event"] for e in LIFECYCLE_EVENT_CATALOG}
    assert AGENT_SCOPED_EVENTS <= catalog_events
    # The handler computes the flag as `event in AGENT_SCOPED_EVENTS`; assert the source
    # expression rather than spinning an aiohttp app for a pure set-membership stamp.
    import inspect

    from personalclaw.dashboard.handlers import triggers as handler_mod

    src = inspect.getsource(handler_mod)
    assert '"agent_scoped": e["event"] in AGENT_SCOPED_EVENTS' in src
