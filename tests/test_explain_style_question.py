"""The one optional question: "How should I explain things?"

Offered once, after onboarding, as a dismissible card that never blocks the composer. An answer
writes a pinned ``style`` preference facet, so it shows wherever preferences are listed and can be
changed or forgotten there. An answer or a Skip records that the question was asked, in the
onboarding state, so the card does not come back.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import onboarding_state_shared
import pytest

from personalclaw import onboarding as ob
from personalclaw.dashboard import handlers_system as hs
from personalclaw.memory_service import MemoryService
from personalclaw.preference_facets import facet_state, load_facets
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
    vs = VectorMemoryStore(db_path=tmp_path / "m.db")
    vs.init()
    vs.embed_fn = lambda t: [1.0, 0.0, 0.0]
    monkeypatch.setattr(hs, "_explain_store", lambda request: vs)
    return vs


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
    @pytest.mark.parametrize(
        ("answer", "text"),
        [
            ("briefly", "Explain things briefly."),
            ("step_by_step", "Explain things step by step."),
            ("with_background", "Explain things with the background."),
        ],
    )
    async def test_an_answer_writes_a_pinned_style_facet_and_marks_it_asked(
        self, _isolate_home, store, answer, text
    ):
        resp = await hs.api_onboarding_explain(_req({"answer": answer}))
        assert resp.status == 200, resp.body
        facets = [f for _k, f in load_facets(store) if f.cls == "style"]
        assert [f.text for f in facets] == [text]
        assert facets[0].pinned is True
        assert facet_state(facets[0]) == "Active"
        assert ob.load_onboarding_state()["explain_asked"] is True
        # Read back through the facet store the Memory page lists.
        listed = MemoryService.over_vector_store(store).facets()
        assert any(f["text"] == text and f["pinned"] for f in listed)

    @pytest.mark.asyncio
    async def test_skip_writes_no_facet_and_is_remembered(self, _isolate_home, store):
        resp = await hs.api_onboarding_explain(_req({"answer": "skip"}))
        assert resp.status == 200
        assert load_facets(store) == []
        assert ob.load_onboarding_state()["explain_asked"] is True

    @pytest.mark.asyncio
    async def test_once_asked_a_second_answer_is_refused_and_changes_nothing(
        self, _isolate_home, store
    ):
        assert (await hs.api_onboarding_explain(_req({"answer": "skip"}))).status == 200
        resp = await hs.api_onboarding_explain(_req({"answer": "briefly"}))
        assert resp.status == 409
        assert load_facets(store) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad",
        [
            {},
            {"answer": "loudly"},
            {"answer": 3},
            [],
            {"answer": ["briefly"]},
            {"answer": {"a": 1}},
        ],
        ids=repr,
    )
    async def test_an_unknown_answer_is_a_400_and_writes_nothing(self, _isolate_home, store, bad):
        resp = await hs.api_onboarding_explain(_req(bad))
        assert resp.status == 400
        assert load_facets(store) == []
        assert ob.load_onboarding_state()["explain_asked"] is False

    @pytest.mark.asyncio
    async def test_two_answers_at_once_store_one_preference(
        self, _isolate_home, store, monkeypatch
    ):
        """Two tabs answering together: the first claims the question before its write, so the
        second is the 409 a second answer gets, and one preference is kept."""
        import time

        import personalclaw.preference_facets as pf

        real = pf.upsert_facet

        def slow(*a, **k):
            time.sleep(0.2)
            return real(*a, **k)

        monkeypatch.setattr(pf, "upsert_facet", slow)
        a, b = await asyncio.gather(
            hs.api_onboarding_explain(_req({"answer": "briefly"})),
            hs.api_onboarding_explain(_req({"answer": "with_background"})),
        )
        assert sorted([a.status, b.status]) == [200, 409]
        assert [f.text for _k, f in load_facets(store) if f.cls == "style"] == [
            "Explain things briefly."
        ]

    @pytest.mark.asyncio
    async def test_a_failed_write_leaves_the_question_to_ask_again(
        self, _isolate_home, store, monkeypatch
    ):
        import personalclaw.preference_facets as pf

        real = pf.upsert_facet

        def broken(*_a, **_k):
            raise OSError("the memory store is not writable")

        monkeypatch.setattr(pf, "upsert_facet", broken)
        with pytest.raises(OSError):
            await hs.api_onboarding_explain(_req({"answer": "briefly"}))
        assert ob.load_onboarding_state()["explain_asked"] is False
        monkeypatch.setattr(pf, "upsert_facet", real)
        assert (await hs.api_onboarding_explain(_req({"answer": "briefly"}))).status == 200

    def test_the_route_is_the_owners_alone(self):
        from personalclaw.apps.permissions import ROUTE_AUTHZ, OwnerOnly

        assert isinstance(ROUTE_AUTHZ["POST /api/onboarding/explain"], OwnerOnly)
