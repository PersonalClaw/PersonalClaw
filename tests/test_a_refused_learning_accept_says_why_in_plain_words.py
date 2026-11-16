"""A refused learning Accept says why in plain words, wherever it is pressed.

The Inbox's Proposals lens approves a learning proposal through the proposals contract
(``apply.skill_promotion`` → ``learning.proposals.accept``). A refused accept raises one of
learning's ``AcceptError`` sentences, which already say nothing changed and that the proposal
still waits; the contract read it as a dispatcher that broke and prefixed its class name
("NotInstalledError: A workflow named …").

And a proposed template whose step holds something shaped like a credential was refused with the
prompt-card path's words, which name "the card" and a lint code ("the card produced an
unrunnable template (WF_INLINE_SECRET)"), and didn't say the proposal still waits.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw import proposals_contract as pc
from personalclaw.learning import proposals

TAKEN = (
    "A workflow named nightly-digest already exists, so nothing was saved and the proposal "
    "still waits."
)
PLAN_SHAPE = (
    "This names a repeated pattern. It has no steps to save, so it can't be accepted. Nothing "
    "changed, and it is still pending."
)


def _apply(monkeypatch, raised: Exception) -> pc.ApplyOutcome:
    def refusing(pid, *, installer=None, actor="user"):
        raise raised

    monkeypatch.setattr(proposals, "accept", refusing)
    proposal = pc.Proposal(
        title="Template: nightly-digest", apply={"skill_promotion": {"pid": "p1"}}
    )
    return asyncio.run(pc.apply_proposal(proposal))


@pytest.mark.parametrize(
    "raised",
    [
        proposals.NotInstalledError(TAKEN),
        proposals.NoProposalInstallerError(PLAN_SHAPE),
        proposals.AcceptError(TAKEN),
    ],
)
def test_a_refused_accept_answers_its_own_sentence(monkeypatch, raised) -> None:
    outcome = _apply(monkeypatch, raised)
    assert outcome.ok is False
    assert outcome.case == "skill_promotion"
    assert outcome.error == str(raised), "the sentence as learning says it, with no class name"


def test_an_installer_that_broke_is_still_logged_as_the_bug_it_is(monkeypatch, caplog) -> None:
    """``accept`` wraps an installer's own exception as its sentence; the row shows the sentence,
    and the log keeps the bug. A refusal of learning's own is no bug, and logs nothing."""
    import logging

    broke = proposals.NotInstalledError("It could not be installed (boom), so nothing changed.")
    broke.__cause__ = KeyError("boom")
    with caplog.at_level(logging.WARNING, logger="personalclaw.proposals_contract"):
        outcome = _apply(monkeypatch, broke)
    assert outcome.error == str(broke)
    assert any(r.exc_info and r.exc_info[0] is KeyError for r in caplog.records)

    refused = proposals.NotInstalledError(TAKEN)
    refused.__cause__ = proposals.AcceptError(TAKEN)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="personalclaw.proposals_contract"):
        _apply(monkeypatch, refused)
    assert not caplog.records


def test_a_dispatcher_that_breaks_still_says_so(monkeypatch) -> None:
    """Only a refusal is learning's sentence: anything else is still reported as a failure."""
    outcome = _apply(monkeypatch, RuntimeError("disk on fire"))
    assert outcome.ok is False
    assert "disk on fire" in outcome.error


# ── a credential-shaped step ────────────────────────────────────────────────────────────────────

MARKER = "SHAPED-LIKE-A-KEY-0000"


@pytest.fixture
def home(tmp_path, monkeypatch):
    from personalclaw.workflows import defs as defs_mod
    from personalclaw.workflows import native_defs, validator

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(proposals, "_dir", lambda: tmp_path / "proposals")
    monkeypatch.setattr(proposals, "_decisions_path", lambda: tmp_path / "decisions.json")
    (tmp_path / "proposals").mkdir()
    # The detector is `secrets.py`'s; this test is about the words, so a marker stands in for a
    # value of a real credential's shape.
    monkeypatch.setattr(validator, "_looks_like_secret", lambda value: MARKER in value)
    registered = defs_mod.get_provider("native") is None
    if registered:
        defs_mod.register_provider(native_defs.NativeWorkflowDefProvider())
    yield tmp_path
    if registered:
        defs_mod.unregister_provider("native")


def _template_with_a_credential() -> proposals.Proposal:
    _verdict, prop = proposals.enqueue(
        kind=proposals.Kind.TEMPLATE.value,
        title="Template: call-the-weather-api",
        body="\n".join(
            f"- {step}"
            for step in (
                "Call the weather service each morning",
                f"Read today's forecast for Lisbon with {MARKER}",
                "Post a one-line summary",
            )
        ),
        provenance="inferred",
        session_key="dashboard:c1",
        run_id="call-the-weather-api",
        tags=["ad_hoc_to_template", "auto_file", "name:call-the-weather-api"],
    )
    return prop


def test_a_template_with_a_credential_shaped_step_says_so_and_still_waits(home) -> None:
    prop = _template_with_a_credential()
    with pytest.raises(proposals.NotInstalledError) as refused:
        proposals.accept(prop.id, actor="user")
    said = str(refused.value)
    assert said == (
        "The template call-the-weather-api was not saved: one of its steps holds something "
        "shaped like a credential. Reject it, or make the template yourself with the credential "
        "kept as a secret. It still waits."
    )
    assert MARKER not in said, "never the value"
    assert proposals.get(prop.id).status == "pending"
    assert not (home / "workflows" / "defs" / "call-the-weather-api").exists()


def test_the_learning_route_answers_that_sentence_as_its_409(home) -> None:
    import json

    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import learning as L

    prop = _template_with_a_credential()
    request = make_mocked_request(
        "POST",
        f"/api/learning/proposals/{prop.id}/accept",
        match_info={"id": prop.id},
        app=web.Application(),
    )
    request["user"] = "me"
    resp = asyncio.run(L.api_learning_proposal_accept(request))
    body = json.loads(resp.body.decode())
    assert resp.status == 409
    message = body["error"]["message"]
    assert "shaped like a credential" in message and message.endswith("It still waits.")
    for leak in ("WF_INLINE_SECRET", "the card", MARKER):
        assert leak not in message
