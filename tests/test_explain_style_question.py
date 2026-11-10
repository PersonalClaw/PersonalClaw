"""The one optional question: "How should I explain things?", and the claim that it was asked.

The answer is a setting now: the owner's Explanations default, ``agent.explain``, which the window
saves through the config PATCH. ``POST /api/onboarding/explain`` keeps one job, the
claim: ``{"answer": "skip"}`` records the question as asked and writes nothing, so it does not come
back, and a second claim is a 409. The three answers it once took are a 400 naming
``agent.explain``, and nothing writes a preference facet for them any more.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import onboarding_state_shared
import pytest

from personalclaw import onboarding as ob
from personalclaw.dashboard import handlers_system as hs
from personalclaw.preference_facets import load_facets
from personalclaw.vector_memory import VectorMemoryStore

pytestmark = pytest.mark.security

_isolate_home = onboarding_state_shared._isolate_home


def _req(body, state=None):
    r = MagicMock()
    r.json = AsyncMock(return_value=body)
    r.app = {"state": state or MagicMock()}
    return r


async def _json(resp):
    return json.loads(resp.body.decode())


@pytest.fixture
def store(tmp_path, monkeypatch):
    """The global memory's record store, where the route once wrote an answer as a preference:
    every store the dashboard hands out (`handlers.memory._global_provider`) is this one, so a
    row that says nothing is written reads where such a write would land."""
    vs = VectorMemoryStore(db_path=tmp_path / "m.db")
    vs.init()
    vs.embed_fn = lambda t: [1.0, 0.0, 0.0]
    monkeypatch.setattr("personalclaw.dashboard.handlers.memory._global_provider", lambda state: vs)
    return vs


def _wrote_nothing(home, store) -> None:
    """No preference was written, and no setting either."""
    assert load_facets(store) == []
    assert not (home / "config.json").exists(), "the route wrote the config"


class TestTheAskedFlag:
    def test_a_fresh_home_has_not_been_asked(self, _isolate_home):
        assert ob.load_onboarding_state()["explain_asked"] is False

    def test_an_old_file_without_the_field_reads_as_not_asked(self, _isolate_home):
        p = _isolate_home / "entity_settings" / "onboarding.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"step": "done"}), encoding="utf-8")
        assert ob.load_onboarding_state()["explain_asked"] is False

    def test_it_merges_and_reads_back(self, _isolate_home):
        ob.merge_onboarding_state({"explain_asked": True})
        assert ob.load_onboarding_state()["explain_asked"] is True

    def test_a_non_boolean_is_refused(self, _isolate_home):
        with pytest.raises(ValueError, match="'explain_asked' must be a boolean"):
            ob.merge_onboarding_state({"explain_asked": "yes"})


class TestTheAnswer:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("answer", ["briefly", "step_by_step", "with_background"])
    async def test_an_answer_is_a_400_that_names_the_setting_and_writes_nothing(
        self, _isolate_home, store, answer
    ):
        resp = await hs.api_onboarding_explain(_req({"answer": answer}))
        assert resp.status == 400, resp.body
        assert "agent.explain" in (await _json(resp))["error"]["message"]
        _wrote_nothing(_isolate_home, store)
        assert ob.load_onboarding_state()["explain_asked"] is False

    @pytest.mark.asyncio
    async def test_skip_claims_the_question_and_writes_nothing(self, _isolate_home, store):
        resp = await hs.api_onboarding_explain(_req({"answer": "skip"}))
        assert resp.status == 200
        _wrote_nothing(_isolate_home, store)
        assert ob.load_onboarding_state()["explain_asked"] is True

    @pytest.mark.asyncio
    async def test_once_asked_a_second_claim_is_refused_and_changes_nothing(
        self, _isolate_home, store
    ):
        assert (await hs.api_onboarding_explain(_req({"answer": "skip"}))).status == 200
        resp = await hs.api_onboarding_explain(_req({"answer": "skip"}))
        assert resp.status == 409
        _wrote_nothing(_isolate_home, store)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad",
        [
            {},
            {"answer": "loudly"},
            {"answer": 3},
            [],
            {"answer": ["skip"]},
            {"answer": {"a": 1}},
        ],
        ids=repr,
    )
    async def test_an_unknown_answer_is_a_400_and_writes_nothing(self, _isolate_home, store, bad):
        resp = await hs.api_onboarding_explain(_req(bad))
        assert resp.status == 400
        _wrote_nothing(_isolate_home, store)
        assert ob.load_onboarding_state()["explain_asked"] is False

    @pytest.mark.asyncio
    async def test_two_claims_at_once_keep_one(self, _isolate_home, store):
        """Two tabs claiming together: the check and the claim are one step on the loop, with
        nothing awaited between them, so the second is the 409."""
        a, b = await asyncio.gather(
            hs.api_onboarding_explain(_req({"answer": "skip"})),
            hs.api_onboarding_explain(_req({"answer": "skip"})),
        )
        assert sorted([a.status, b.status]) == [200, 409]
        assert ob.load_onboarding_state()["explain_asked"] is True

    def test_the_route_is_the_owners_alone(self):
        from personalclaw.apps.permissions import ROUTE_AUTHZ, OwnerOnly

        assert isinstance(ROUTE_AUTHZ["POST /api/onboarding/explain"], OwnerOnly)
