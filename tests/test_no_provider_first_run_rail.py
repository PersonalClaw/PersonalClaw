"""OU-12 — No-provider first-run legibility rail (ONBOARDING-UX).

A *growing enumeration* of the model-dependent first-run surfaces. Each is driven on
a NO-PROVIDER home and must resolve to the **calm no-model signal** — an envelope /
turn-error text matching the FE ``isNoModelSetupError`` contract, or a *declared*
degraded/empty state — and NEVER to:

  * a raw 500 / traceback / ``UnboundLocalError`` (a crash), or
  * a **silent success**: a 2xx that reports the model-backed work as DONE while it
    actually did nothing (``classified:false`` with no signal; ``processing_status``
    ``done`` with empty insights; ``200`` with empty results).

**The enumeration (its blind spot is every surface NOT listed here — a model-dependent
first-run route that is not in ``COVERED_SURFACES`` is UNGUARDED; add a row + a probe
when one ships):**

  1. ``POST /api/chat``                                       — regression guard (#2856 → #2865)
  2. ``GET  /api/suggestions``                                — regression guard (#2866 → #2868)
  3. ``POST /api/knowledge/items``                            — regression guard (OU-3 runner fix)
  4. ``POST /api/knowledge/items/{id}/generate-intelligence`` — regression guard (OU-3 runner fix)
  5. ``POST /api/loops/classify``                     — MUST-FAIL-ON-MAIN anchor (this atom)

**Ground truth (measured on a live no-provider gateway, ``origin/main`` @ ``df5f59b56``,
2026-09-16).** 1+2 already surface the calm signal (their point-fixes merged). 3+4 ingest
to ``processing_status='partial'`` + ``"insights: model unavailable"`` — OU-3's 2026-08-16
``status:'done'``/empty observation is **STALE**: the runner's ``insights_ok`` →
``partial`` downgrade (``knowledge/pipeline/runner.py``) already fixed those two, so they
are regression guards, not must-fail anchors. **Only 5 still failed open** — it returned
``200 {"classified": false}`` with no no-model signal, indistinguishable from "the model
returned garbage". This atom adds the ``model_unresolved`` preflight to
``api_loop_classify``; the rail below FAILS on ``origin/main`` (surface 5 answers 200) and
PASSES after.

**Non-cheatable floors (see ``TestRailIsNotVacuous``):** the shared HTTP classifier
``is_no_model_signal_http`` is a POSITIVE test — a bare 500 and a plain silent 200 are
both rejected — so a deliberately-added surface that returns a bare 500 on no-provider
fails this suite by construction. The FE-coupling test pins the ``isNoModelSetupError``
port against the REAL backend renders (the ``ERR_MODEL_UNRESOLVED`` first-run render
matches; the stale-pin render does not — the exact split the FE draws).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.dashboard.handlers.loop_routes import api_loop_classify
from personalclaw.errors import AgentError
from personalclaw.http_errors import HTTP_ERROR_CODES
from personalclaw.knowledge.pipeline.runner import ingest_item
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.providers.provider_bridge import (
    ProviderResolutionError,
    resolve_provider_for_use_case,
)
from personalclaw.suggestions import _FALLBACK_SUGGESTIONS, generate_suggestions

# The surfaces this rail covers. A set, not prose, so it is greppable and a new surface
# is a one-line addition here + a probe below. Anything model-dependent and first-run
# NOT in this set is the rail's declared blind spot.
COVERED_SURFACES: frozenset[str] = frozenset(
    {
        "POST /api/chat",
        "GET /api/suggestions",
        "POST /api/knowledge/items",
        "POST /api/knowledge/items/{id}/generate-intelligence",
        "POST /api/loops/classify",
    }
)

# The two substrings the FE `isNoModelSetupError` matcher keys on
# (`web/src/pages/chat/NoModelSetupState.tsx`). Ported here so the backend rail speaks the
# same contract; `test_no_model_text_matches_backend_renders` pins the port against the
# real renders so a backend reword reds this file, not just the FE test.
_NO_MODEL_TEXT_SUBSTRINGS = (
    "no model provider resolves for use case",
    "no provider in config.json declares the capability",
)


def matches_no_model_text(text: str | None) -> bool:
    """Backend port of the FE ``isNoModelSetupError`` (case-insensitive substring)."""
    if not text:
        return False
    low = text.lower()
    return any(sub in low for sub in _NO_MODEL_TEXT_SUBSTRINGS)


def _looks_like_traceback(text: str) -> bool:
    return "Traceback (most recent call last)" in text or '\n  File "' in text


def _body_text(body: object) -> str:
    if isinstance(body, str):
        return body
    try:
        return json.dumps(body)
    except (TypeError, ValueError):
        return str(body)


def is_no_model_signal_http(status: int, body: object) -> bool:
    """POSITIVE test: does this HTTP outcome carry the CALM no-model signal?

    True iff it is not a crash AND it positively signals the unbound model — the
    ``model_unresolved`` wire code, or a body whose text matches the
    ``isNoModelSetupError`` contract. A bare 500, a traceback, and a plain silent 200
    (e.g. ``{"classified": false}`` with no marker) all return False — which is what
    makes the rail reject a silent success and a crash alike.
    """
    text = _body_text(body)
    if status == 500 or _looks_like_traceback(text):
        return False
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("code") == "model_unresolved":
            return True
    return matches_no_model_text(text)


def assert_calm_http_no_model(status: int, body: object) -> None:
    """The shared gate every HTTP surface probe (and the meta-guards) route through."""
    text = _body_text(body)
    assert status != 500, f"raw 500 is never the calm signal (body={text[:200]!r})"
    assert not _looks_like_traceback(text), f"traceback leaked to the client: {text[:200]!r}"
    assert is_no_model_signal_http(status, body), (
        "surface did not carry the calm no-model signal — a silent success? "
        f"(status={status}, body={text[:200]!r})"
    )


async def _classify_response(body: dict):
    """Drive the real ``POST /api/loops/classify`` handler; return (status, parsed body)."""
    req = MagicMock()
    req.json = AsyncMock(return_value=body)
    resp = await api_loop_classify(req)
    parsed = json.loads(resp.body.decode())
    return resp.status, parsed


class _RaisingPool:
    """Mirrors ``ProviderWorker`` on a no-provider home: ``send`` fails exactly as the
    real pool does ("No provider entries registered"), so the ingest runner marks the
    item ``partial`` rather than silently ``done`` with empty insights."""

    async def send(self, prompt: str, timeout: float | None = None) -> str:  # noqa: D401
        raise RuntimeError("No provider entries registered")

    async def send_batch(self, prompts: list[str], timeout: float | None = None) -> list[str]:
        raise RuntimeError("No provider entries registered")


class _AnswerPool:
    """A working model: returns a valid insights bundle. Used to prove the SAME ingest
    reaches a clean ``done`` when a provider IS bound (no behaviour change when bound)."""

    async def send(self, prompt: str, timeout: float | None = None) -> str:
        return json.dumps(
            {
                "summary": "A concise summary.",
                "key_points": ["one", "two"],
                "topics": ["alpha"],
                "action_items": [],
            }
        )

    async def send_batch(self, prompts: list[str], timeout: float | None = None) -> list[str]:
        return [await self.send(p, timeout) for p in prompts]


# --------------------------------------------------------------------------- #
# Surface 5 — POST /api/loops/classify (the must-fail-on-main anchor)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_classify_no_provider_surfaces_calm_signal():
    """On a no-provider home classify must answer the calm ``model_unresolved`` envelope,
    NOT ``200 {"classified": false}``. This is the assertion that reds on ``origin/main``
    (which has no preflight and returns 200) and greens after the fix."""
    with patch("personalclaw.providers.provider_bridge.can_resolve_use_case", return_value=False):
        status, body = await _classify_response(
            {"kind": "goal", "task": "Summarize the weekly team status into three bullets"}
        )
    assert status == 409, f"expected the calm 409, got {status}: {body}"
    assert_calm_http_no_model(status, body)
    # A plain `classified:false` (the origin/main silent-success shape) must be gone.
    assert "classified" not in body, f"leaked the silent-success shape: {body}"


@pytest.mark.asyncio
async def test_classify_with_provider_bound_is_unchanged():
    """When a model resolves, the preflight must NOT fire — no ``model_unresolved`` — so a
    bound instance is byte-for-byte the pre-OU-12 behaviour (a normal 200 classification)."""
    valid = json.dumps({"title": "Weekly status", "goal_type": "open_ended"})
    with (
        patch("personalclaw.providers.provider_bridge.can_resolve_use_case", return_value=True),
        patch("personalclaw.llm_helpers.one_shot_completion", new=AsyncMock(return_value=valid)),
    ):
        status, body = await _classify_response(
            {"kind": "goal", "task": "Summarize the weekly team status into three bullets"}
        )
    assert status == 200, f"a bound instance must classify normally, got {status}: {body}"
    err = body.get("error")
    assert not (
        isinstance(err, dict) and err.get("code") == "model_unresolved"
    ), "the no-model preflight fired even though a provider resolves"


# --------------------------------------------------------------------------- #
# Surface 1 — POST /api/chat (regression guard, #2856 → #2865)
# --------------------------------------------------------------------------- #
def test_chat_no_provider_resolution_carries_calm_signal():
    """The chat surface streams whatever ``resolve_provider_for_use_case('chat')`` raises;
    on a no-provider home that is ``ERR_MODEL_UNRESOLVED``, whose render matches
    ``isNoModelSetupError`` — the calm ``NoModelSetupState``, not a crash."""
    with pytest.raises(ProviderResolutionError) as ei:
        resolve_provider_for_use_case("chat")
    err = ei.value
    # str(exc) is the AgentError.render() when one is attached (provider_bridge).
    assert matches_no_model_text(str(err)), f"chat's no-model error is not the calm signal: {err}"
    assert err.agent_error is not None and err.agent_error.code == "ERR_MODEL_UNRESOLVED"


# --------------------------------------------------------------------------- #
# Surface 2 — GET /api/suggestions (regression guard, #2866 → #2868)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_suggestions_no_provider_returns_declared_empty_state():
    """A no-provider home must yield the declared fallback list quietly — never a per-poll
    traceback (the #2866 crash) and never a raised exception."""
    state = MagicMock()
    state.sessions = MagicMock()
    state.sessions.get_or_create = AsyncMock(
        side_effect=ProviderResolutionError(
            "No provider configured for use case 'background'.",
            AgentError(
                code="ERR_MODEL_UNRESOLVED",
                what="no model provider resolves for use case 'background'",
                why="no provider in config.json declares the capability this use case needs",
                fix="add a model provider in Settings → Providers",
            ),
        )
    )
    with (
        patch("personalclaw.suggestions._build_context", return_value="x" * 120),
        patch(
            "personalclaw.prompt_providers.runtime.render_use_case_prompt",
            return_value="a prompt",
        ),
    ):
        result = await generate_suggestions(state)
    assert result == list(
        _FALLBACK_SUGGESTIONS
    ), "suggestions must degrade to the declared fallback, not crash or return empty"


# --------------------------------------------------------------------------- #
# Surfaces 3+4 — knowledge ingest (regression guard, OU-3 runner fix)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("reingest", [False, True], ids=["create", "generate-intelligence"])
async def test_knowledge_ingest_no_provider_is_not_silent_done(tmp_path, reingest):
    """``POST /api/knowledge/items`` (create) and ``generate-intelligence`` (re-enqueue)
    both funnel to ``ingest_item``. On a no-provider home the model-backed insights stage
    must NOT report the item as a clean success — the OU-3 silent-fail-open was
    ``processing_status='done'`` with empty insights + ``node_phases['insights']='done'``.
    The runner's ``insights_ok`` → ``partial`` downgrade guards it; this pins that."""
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    item_id = store.create_typed_item(
        item_type="note",
        title="OU12 note",
        content=(
            "A note about distributed consensus, the Raft algorithm, and leader election "
            "so the insights stage has real content to (fail to) enrich."
        ),
    )
    if reingest:
        # generate-intelligence's effect: re-queue, then the SAME runner runs again.
        store.update_item(item_id, processing_status="queued", touch=False)

    status = await ingest_item(store, item_id, insights_pool=_RaisingPool(), embedder=None)

    item = store.get_item(item_id)
    node_phases = (item.get("file_metadata") or {}).get("node_phases") or {}
    # The invariant: the item is NOT reported as a clean, fully-enriched success.
    assert status != "done", f"silent-fail-open: no-provider ingest reported done ({item!r})"
    assert item["processing_status"] != "done", f"item persisted as done: {item!r}"
    assert (
        node_phases.get("insights") != "done"
    ), f"the model-backed insights stage falsely claimed done: {node_phases}"
    # And it says WHY, legibly — a degraded state a user can act on, not a blank.
    assert item.get("processing_error"), f"partial item carries no reason: {item!r}"


@pytest.mark.asyncio
async def test_knowledge_ingest_with_provider_bound_reaches_done(tmp_path):
    """No behaviour change when a provider IS bound: the SAME ingest reaches ``done`` with
    real insights. This is the known-good half that keeps the guard above honest."""
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    item_id = store.create_typed_item(
        item_type="note",
        title="OU12 note",
        content="A note about distributed consensus and the Raft algorithm.",
    )
    status = await ingest_item(store, item_id, insights_pool=_AnswerPool(), embedder=None)
    item = store.get_item(item_id)
    assert status == "done", f"a bound provider must reach done, got {status}: {item!r}"
    assert item["processing_status"] == "done"
    assert item.get("insights"), f"insights should be populated when the model works: {item!r}"


# --------------------------------------------------------------------------- #
# The rail's own floors — a rail that inspects nothing must never read as clean.
# --------------------------------------------------------------------------- #
class TestRailIsNotVacuous:
    def test_covered_surfaces_match_the_documented_enumeration(self):
        """The enumeration is the whole rail; its size is pinned so a surface cannot be
        dropped silently, and adding one is a deliberate edit here."""
        assert len(COVERED_SURFACES) == 5, COVERED_SURFACES
        assert "POST /api/loops/classify" in COVERED_SURFACES  # the anchor
        assert "POST /api/chat" in COVERED_SURFACES  # a regression guard

    def test_a_bare_500_surface_fails_the_rail(self):
        """A deliberately-added surface that answers a bare 500 on no-provider MUST fail —
        because every HTTP probe routes through ``assert_calm_http_no_model``, and it
        rejects a crash. This is the clause that keeps the rail from rubber-stamping."""
        with pytest.raises(AssertionError):
            assert_calm_http_no_model(500, {"error": "unhandled: UnboundLocalError"})
        with pytest.raises(AssertionError):
            assert_calm_http_no_model(200, 'Traceback (most recent call last):\n  File "x"')

    def test_a_silent_success_fails_the_rail(self):
        """The origin/main classify shape — 200 with ``classified:false`` and no signal —
        must be rejected. A calm signal is a POSITIVE claim, not merely 'not a 500'."""
        with pytest.raises(AssertionError):
            assert_calm_http_no_model(200, {"classified": False, "kind": "goal"})
        with pytest.raises(AssertionError):
            assert_calm_http_no_model(200, {"ok": True})

    def test_the_real_calm_envelope_passes_the_rail(self):
        """The signal this atom emits is accepted (so the gate is not simply always-red)."""
        assert_calm_http_no_model(
            409,
            {
                "error": {
                    "code": "model_unresolved",
                    "message": "No model provider resolves for use case 'background'.",
                }
            },
        )
        # A different surface could carry the signal as text alone — also accepted.
        assert_calm_http_no_model(409, {"error": "no model provider resolves for use case 'chat'"})

    def test_no_model_text_matches_backend_renders(self):
        """Pin the ``isNoModelSetupError`` port against the REAL backend renders: the
        first-run ``ERR_MODEL_UNRESOLVED`` render matches; the stale-pin render (a model
        WAS chosen, then went missing) does NOT — the exact split the FE draws."""
        first_run = AgentError(
            code="ERR_MODEL_UNRESOLVED",
            what="no model provider resolves for use case 'chat'",
            why="no provider in config.json declares the capability this use case needs",
            fix="add a model provider in Settings → Providers, then bind 'chat' to it",
        )
        stale_pin = AgentError(
            code="ERR_MODEL_UNRESOLVED",
            what="the model pinned for use case 'chat' ('bedrock:x') cannot be built",
            why="the active ref names provider 'bedrock', which is absent from config.json",
            fix="install 'bedrock' in the App Store, or rebind 'chat' in Settings → Models",
        )
        assert matches_no_model_text(first_run.render()), first_run.render()
        assert not matches_no_model_text(stale_pin.render()), stale_pin.render()
        assert not matches_no_model_text("The model returned an error.")
        assert not matches_no_model_text("")
        assert not matches_no_model_text(None)

    def test_model_unresolved_wire_code_is_registered(self):
        """The classify preflight emits ``model_unresolved``; the append-only wire registry
        must carry it (else ``test_http_error_codes_append_only`` reds)."""
        assert "model_unresolved" in HTTP_ERROR_CODES
