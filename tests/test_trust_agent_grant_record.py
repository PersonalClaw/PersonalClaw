"""#541 + #683 — "Always for this agent" keeps its promise, and the transcript says which.

One defect with two faces, both in `api_chat_session_approve`:

* **#683** — `trust_agent` was remapped to ``"approved"`` before the record was written
  (``action = "approved"``), and the preserving tuple two hundred lines later listed only
  ``("trust", "trust_reads")``. So a STANDING per-agent grant and a one-off Allow left
  byte-identical transcript rows, and the grant that explains every subsequent silent
  approval was invisible at the point it was made.
* **#541** — the card promises *"Saved on this agent: every tool runs without asking, in
  this chat and future ones."* unconditionally, while the handler degrades a
  reserved/unnamed/unknown agent to session scope and tells only ``logger.info``. The
  approve response carried nothing, so the client could not know which happened.

Both need the same missing fact — **did the grant persist?** — so both are fixed by
deciding it once and reporting it in all three places that need it: the return value, the
transcript row, and the prompt-time signal the card reads (that last one lives in
``chat_runner``/``perm_meta``; see ``approvalGrantPromise.test.tsx`` for its consumer).

Written against the ROUTE and the FILE, never against the in-memory object alone: the
whole point of #541 is that a promise about *future chats* is a claim about what survives a
reload, so every persistence assertion below re-reads ``config.json`` from disk.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

from personalclaw.agents.defaults import LITE_AGENT_NAME
from personalclaw.config.loader import AgentProfile, AppConfig

GRANTEE = "researcher"
BYSTANDER = "archivist"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """An isolated config home whose config.json is written and re-read for real."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: tmp_path / "config.json")
    cfg = AppConfig()
    cfg.default_agent = GRANTEE
    # Two editable agents: the grant target and a bystander whose profile must not move.
    cfg.agents = {
        GRANTEE: AgentProfile(description="the chat's agent"),
        BYSTANDER: AgentProfile(description="a different agent entirely"),
    }
    cfg.save()
    return tmp_path


def _session_awaiting_approval(state, key: str, agent: str):
    """A session bound to ``agent`` holding one pending permission row for 'req-1'."""
    session = state.get_or_create_session(key)
    session.agent = agent
    session.append("permission", "bash", json.dumps({"request_id": "req-1"}))
    fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    session._approval_futures["req-1"] = fut
    return session, fut


def _recorded(session) -> str | None:
    """The resolution the transcript row now carries — the auditor's view."""
    for msg in reversed(session.messages):
        if msg.get("role") == "permission":
            return json.loads(msg.get("cls", "{}")).get("resolved")
    return None


async def _approve(state, key: str, action: str) -> dict:
    async with TestClient(TestServer(_make_app(state))) as client:
        resp = await client.post(
            f"/api/chat/sessions/{key}/approve", json={"action": action, "request_id": "req-1"}
        )
        assert resp.status == 200
        return await resp.json()


# ── #541 clause 1: the promise, and whether the write path kept it ─────────────────────


@pytest.mark.asyncio
async def test_a_persisted_grant_is_readable_back_after_a_reload(home):
    """The card's promise is about FUTURE chats, so the test re-reads the file.

    Asserting the in-memory ``cfg`` here would pass on a build that never called
    ``save()`` — and "in this chat and future ones" is precisely a claim about the bytes
    on disk, not about the request that wrote them.
    """
    state = _make_state(home)
    _session_awaiting_approval(state, "s1", GRANTEE)

    await _approve(state, "s1", "trust_agent")

    reloaded = AppConfig.load()
    assert reloaded.agents[GRANTEE].approval_mode == "auto"


@pytest.mark.asyncio
async def test_a_grant_does_not_leak_to_another_agent(home):
    """The blast radius of "this agent" is exactly one agent."""
    state = _make_state(home)
    _session_awaiting_approval(state, "s1", GRANTEE)

    await _approve(state, "s1", "trust_agent")

    reloaded = AppConfig.load()
    assert (
        reloaded.agents[BYSTANDER].approval_mode == ""
    ), "a grant scoped to one agent moved another agent's approval_mode"


@pytest.mark.asyncio
async def test_the_response_reports_that_the_grant_persisted(home):
    """#541's residue: the backend knew at decision time and told only the log.

    The client cannot render an honest outcome from ``{"ok": true}``, which is all this
    route used to return — so the FE could not distinguish a saved grant from one that
    silently did nothing, which is the whole defect.
    """
    state = _make_state(home)
    _session_awaiting_approval(state, "s1", GRANTEE)

    body = await _approve(state, "s1", "trust_agent")

    assert body["grant"] == {"scope": "agent", "persisted": True, "agent": GRANTEE}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent", "why"),
    [
        (LITE_AGENT_NAME, "a reserved system agent's config is fixed"),
        ("never-configured", "an agent with no profile has nothing to write to"),
    ],
)
async def test_a_grant_that_cannot_persist_says_so(home, agent, why):
    """The degraded branch, reported rather than logged — so the card can stop promising.

    Both of these took the ``else`` that only ``logger.info``s. The user was told "in this
    chat and future ones" and got neither a saved profile nor any signal.
    """
    state = _make_state(home)
    _session_awaiting_approval(state, "s1", agent)

    body = await _approve(state, "s1", "trust_agent")

    assert body["grant"]["persisted"] is False, why
    # Nothing was written anywhere — not to the named agent, not to the default.
    reloaded = AppConfig.load()
    assert all(p.approval_mode == "" for p in reloaded.agents.values())


@pytest.mark.asyncio
async def test_an_unnamed_session_grants_to_the_default_agent(home):
    """The deliberate case that is NOT degraded, pinned so the fix cannot over-refuse.

    An empty ``session.agent`` means the implicit default agent — that IS the agent
    running the chat, so the grant persists to it. Collapsing this into the degraded
    branch would make the commonest session shape unable to remember anything.
    """
    state = _make_state(home)
    _session_awaiting_approval(state, "s1", "")

    body = await _approve(state, "s1", "trust_agent")

    assert body["grant"] == {"scope": "agent", "persisted": True, "agent": GRANTEE}
    assert AppConfig.load().agents[GRANTEE].approval_mode == "auto"


# ── #683: the transcript distinguishes a standing grant from an Allow-once ──────────────


@pytest.mark.asyncio
async def test_the_transcript_records_a_standing_grant_as_a_standing_grant(home):
    """#683's headline: the row said ``approved``, exactly as a one-off Allow does."""
    state = _make_state(home)
    session, _ = _session_awaiting_approval(state, "s1", GRANTEE)

    await _approve(state, "s1", "trust_agent")

    assert _recorded(session) == "trust_agent"


@pytest.mark.asyncio
async def test_the_transcript_distinguishes_a_degraded_grant_from_a_persisted_one(home):
    """Three outcomes, not two — the third is the one #541 left unsignalled.

    An auditor answering "why did this tool run without asking me?" has to be able to tell
    a grant that will keep auto-approving future chats from one that expires with this
    session. Collapsing them (either into ``approved`` or into one ``trust_agent``) loses
    the fact that explains every later silent approval.
    """
    state = _make_state(home)
    session, _ = _session_awaiting_approval(state, "s1", LITE_AGENT_NAME)

    await _approve(state, "s1", "trust_agent")

    assert _recorded(session) == "trust_agent_session"


@pytest.mark.asyncio
async def test_allow_once_still_records_approved(home):
    """Vacuity floor. A build that wrote ``trust_agent`` unconditionally would pass every
    test above and destroy the distinction in the other direction."""
    state = _make_state(home)
    session, fut = _session_awaiting_approval(state, "s1", GRANTEE)

    body = await _approve(state, "s1", "approved")

    assert _recorded(session) == "approved"
    assert fut.result() == "approved"
    # No grant object on a scope that grants nothing — absence is the honest report.
    assert "grant" not in body
    assert AppConfig.load().agents[GRANTEE].approval_mode == ""


@pytest.mark.asyncio
async def test_the_sibling_trust_verbs_are_untouched(home):
    """The two rungs that already recorded themselves keep doing so, unchanged."""
    state = _make_state(home)
    for verb in ("trust", "trust_reads"):
        session, _ = _session_awaiting_approval(state, f"s-{verb}", GRANTEE)
        await _approve(state, f"s-{verb}", verb)
        assert _recorded(session) == verb


@pytest.mark.asyncio
async def test_a_standing_grant_still_takes_effect_this_turn(home):
    """The grant is a grant, not just a record: the pending call is approved and the
    session is trusted. A fix that only relabelled the row would pass #683's tests while
    breaking the feature."""
    state = _make_state(home)
    session, fut = _session_awaiting_approval(state, "s1", GRANTEE)

    await _approve(state, "s1", "trust_agent")

    assert fut.result() == "approved"
    assert session._trust is True
