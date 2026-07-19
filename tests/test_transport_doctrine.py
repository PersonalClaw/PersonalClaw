"""Guardrail tests for the realtime-transport doctrine (SSE M5).

VISION.md "Realtime transport" — single-transport-per-concern:
- Always-on dashboard state (status/sessions/titles/notifications/refresh) rides
  the ONE multiplexed WebSocket; it is NOT also fanned to a global SSE stream.
- Page-scoped feeds (campaigns/logs/file-watch) use per-resource SSE.
- Nothing is delivered over two transports.

These tests pin the structural invariants so a future change can't silently
re-introduce the dual-emit (the debt M3 removed) or split always-on concerns into
their own streams (the connection-budget anti-pattern).
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from personalclaw.dashboard import state as state_mod

# Anchor source reads to the package root (this file is <root>/tests/…) so these
# structural guards pass regardless of the pytest invocation cwd — they read repo
# files by relative path and were silently cwd-fragile (only passed when run from
# the PersonalClaw/ dir, failing from the repo root).
_ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text()


def test_state_has_no_global_sse_hub():
    """The dead global SSE hub + accessor must stay gone (M3)."""
    assert not hasattr(state_mod.DashboardState, "sse_hub"), (
        "global SSE hub removed in M3 — dashboard state rides the WebSocket"
    )
    # The per-resource campaign registry IS expected to remain.
    assert hasattr(state_mod.DashboardState, "loop_sse")


def test_broadcast_does_not_publish_to_a_global_sse_hub():
    """_broadcast must fan to WS only — never to a global per-process SSE hub.

    Pins the single-transport rule structurally: the funnel's source must not
    contain a global ``self._sse.publish(...)`` (the removed dual-emit). A
    per-resource ``loop_sse()`` publish is a different concern and is done
    by the watchdog, not here.
    """
    src = inspect.getsource(state_mod.DashboardState._broadcast)
    assert "_sse.publish" not in src, (
        "_broadcast must not publish to a global SSE hub — that is the dual-emit "
        "M3 removed; always-on state rides the WebSocket"
    )


def test_no_global_api_stream_route():
    """The dead global /api/stream SSE endpoint must stay removed."""
    server_src = _read("src/personalclaw/dashboard/server.py")
    # The per-campaign /api/campaigns/{id}/stream is registered in its own
    # handler module, not here; the global /api/stream must be gone.
    assert not re.search(r'add_get\(\s*["\']/api/stream["\']', server_src), (
        "/api/stream (global SSE) was removed in M3"
    )


def test_useSSE_hook_deleted():
    """The dead, never-mounted useSSE.ts frontend hook must stay deleted (M3)."""
    assert not (_ROOT / "web/src/hooks/useSSE.ts").exists(), (
        "useSSE.ts was dead (never mounted) and removed in M3"
    )


def test_per_resource_sse_substrate_present():
    """The reusable SSE substrate must remain — it powers per-resource streams."""
    from personalclaw.dashboard import sse

    assert hasattr(sse, "SseHub")
    assert hasattr(sse, "SseRegistry")
    assert hasattr(sse, "stream_response")


def test_unified_loop_sse_events_are_all_registered_in_the_frontend():
    """Every event the unified Loop backend publishes on loop_sse() MUST be listed in
    the FE useRunStream RUN_LIFECYCLE union — EventSource silently DROPS event types with
    no registered listener, so an unlisted publish is a missed refetch (the C326/C367/
    C369 plan_step/deleted/ratchet_regression drift). The one loop_routes handler +
    loop/watchdog serve EVERY kind (goal/code/general/design); the cockpit subscribes
    via useRunStream (P16 collapsed the per-cockpit LIFECYCLE arrays into the ONE shared
    RUN_LIFECYCLE union in useRunStream.ts). Pin the contract so a new publish without the
    matching FE listener fails CI instead of silently never reaching an open cockpit.
    """
    handler = _read("src/personalclaw/dashboard/handlers/loop_routes.py")
    watchdog = _read("src/personalclaw/loop/watchdog.py")
    design = _read("src/personalclaw/loop/kinds/design.py")
    fe = _read("web/src/pages/loops/useRunStream.ts")

    # Backend event names: direct loop_sse().publish(registry_key(..), "EVENT", ..) in
    # the handler (the action handler publishes a variable action ∈ start/pause/resume/
    # stop, added explicitly) + the watchdog's self._publish(loop_id, "EVENT", ..).
    published = set(re.findall(r'loop_sse\(\)\.publish\(\s*registry_key\([^)]*\),\s*"([a-z_]+)"', handler))
    published |= set(re.findall(r'self\._publish\(\s*[a-zA-Z_.]+,\s*"([a-z_]+)"', watchdog))
    # The design kind (which uses THIS cockpit's useRunStream) publishes its phase-trail
    # advance through the cycle context — same drift class as the code kind's ctx.publish.
    published |= set(re.findall(r'ctx\.publish\(\s*[a-zA-Z_.]+,\s*"([a-z_]+)"', design))

    m = re.search(r"const RUN_LIFECYCLE = \[([^\]]*)\]", fe)
    assert m, "couldn't find the RUN_LIFECYCLE union in useRunStream.ts"
    registered = set(re.findall(r"'([a-z_]+)'", m.group(1)))

    missing = published - registered
    assert not missing, (
        f"Unified Loop backend publishes SSE events the FE useRunStream never listens "
        f"for: {sorted(missing)} — add them to RUN_LIFECYCLE or they'll silently never reach "
        f"the cockpit."
    )


def test_code_cockpit_sse_events_are_all_registered_in_the_frontend():
    """The Code cockpit subscribes to the SAME unified per-loop feed (P16 pointed it at
    the shared useRunStream), but a code loop ALSO publishes code-specific events from the
    sdlc kind's on_new_cycle orchestration (stage_advance / gate_check / task_started /
    task_done / stage_stalled / blocked) via ctx.publish(...). Those plus the shared
    watchdog + handler events MUST all be in the shared RUN_LIFECYCLE union, or EventSource
    drops them and the cockpit's stage rail / gate banner / task buckets only update on the
    slow fallback poll (the regression this guards — the FE list was narrowed to the goal
    watchdog's events at the cutover, dropping every code-specific one)."""
    handler = _read("src/personalclaw/dashboard/handlers/loop_routes.py")
    watchdog = _read("src/personalclaw/loop/watchdog.py")
    sdlc = _read("src/personalclaw/loop/kinds/sdlc.py")
    fe = _read("web/src/pages/loops/useRunStream.ts")

    published = set(re.findall(r'loop_sse\(\)\.publish\(\s*registry_key\([^)]*\),\s*"([a-z_]+)"', handler))
    published |= set(re.findall(r'self\._publish\(\s*[a-zA-Z_.]+,\s*"([a-z_]+)"', watchdog))
    # The sdlc kind publishes its orchestration events through the cycle context.
    published |= set(re.findall(r'ctx\.publish\(\s*[a-zA-Z_.]+,\s*"([a-z_]+)"', sdlc))

    m = re.search(r"const RUN_LIFECYCLE = \[([^\]]*)\]", fe)
    assert m, "couldn't find the RUN_LIFECYCLE union in useRunStream.ts"
    registered = set(re.findall(r"'([a-z_]+)'", m.group(1)))

    missing = published - registered
    assert not missing, (
        f"A code loop publishes SSE events the shared RUN_LIFECYCLE never listens for: "
        f"{sorted(missing)} — add them to RUN_LIFECYCLE or they'll silently never reach the "
        f"cockpit (stage advances / gate failures / task transitions would only land on "
        f"the slow poll)."
    )
