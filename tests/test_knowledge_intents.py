"""Intent-driven ingestion — the Tier-3 layer (NL intents + by-value outcomes)."""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.knowledge.intents import (
    Intent,
    IntentMatch,
    IntentStore,
    build_match_prompt,
    match_intent,
    run_intents,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path):
    return IntentStore(tmp_path / "intents.json")


# ── model + applies_to ──


def test_applies_to_all_when_empty():
    i = Intent(id="x", enabled_for=[])
    assert i.applies_to("note") and i.applies_to("pdf")


def test_applies_to_filtered_types():
    i = Intent(id="x", enabled_for=["pdf", "document"])
    assert i.applies_to("pdf")
    assert not i.applies_to("note")


def test_disabled_never_applies():
    i = Intent(id="x", enabled=False)
    assert not i.applies_to("note")


def test_roundtrip_dict():
    i = Intent(
        id="inv", goal="track investment ideas", enabled_for=["bookmark"], propose_skill=True
    )
    assert Intent.from_dict(i.to_dict()) == i


def test_legacy_description_loads_as_goal():
    # A pre-existing intents.json used `description`; it should populate `goal`.
    i = Intent.from_dict({"id": "old", "description": "homelab improvements"})
    assert i.goal == "homelab improvements"


def test_from_dict_derives_id_from_goal_when_absent():
    """The user never types an id — the backend derives a stable slug from the goal
    (single source of truth). An explicit id is still honored."""
    from personalclaw.knowledge.intents import _ID_RE, slugify_goal

    derived = Intent.from_dict({"goal": "Track performance & latency wins!"})
    assert derived.id == "track-performance-latency-wins"
    assert _ID_RE.match(derived.id)
    # Explicit id wins; symbol-only goals still yield a valid slug.
    assert Intent.from_dict({"id": "custom", "goal": "x"}).id == "custom"
    assert slugify_goal("  ???  ").startswith("intent-")
    assert _ID_RE.match(slugify_goal("  ???  "))
    assert _ID_RE.match(slugify_goal("中文 only"))


def test_slug_fallback_separates_goals_it_cannot_transliterate():
    """The fallback is derived, so a non-Latin script does not collapse every goal onto
    one id. It used to be the constant ``intent``, which gave every Japanese, Chinese,
    Korean, Greek, Hebrew and Cyrillic goal the SAME id — so a user writing in any of
    those could hold exactly one intent and the second silently replaced the first."""
    from personalclaw.knowledge.intents import _ID_RE, slugify_goal

    unslugifiable = ["健康診断", "日本語", "건강 검진", "Привет", "עברית", "???", "!!!"]
    slugs = [slugify_goal(g) for g in unslugifiable]
    assert len(set(slugs)) == len(slugs), f"goals collapsed onto one id: {slugs}"
    assert all(_ID_RE.match(s) for s in slugs)
    # Stable: the create path derives the id from the goal on every POST, so the same
    # goal must keep resolving to the same row rather than accumulating duplicates.
    assert slugs == [slugify_goal(g) for g in unslugifiable]


# ── store CRUD ──


def test_upsert_and_load(store):
    store.upsert(Intent(id="a", goal="A"))
    intents = store.load()
    assert len(intents) == 1 and intents[0].id == "a"


def test_get(store):
    store.upsert(Intent(id="a", goal="find x"))
    assert store.get("a").goal == "find x"
    assert store.get("missing") is None


def test_upsert_replaces_same_id(store):
    """`replace=True` is the edit path — the caller named the id, so it means that row."""
    store.upsert(Intent(id="a", goal="first"))
    store.upsert(Intent(id="a", goal="second"), replace=True)
    intents = store.load()
    assert len(intents) == 1 and intents[0].goal == "second"


def test_create_refuses_to_overwrite_a_different_goal(store):
    """The invariant: no write ever reduces the intent count. The id is DERIVED from the
    goal, so two differently-worded goals can slugify onto one id — and the create path
    overwrote the first, destroying it, while answering as if it had created something."""
    first = Intent.from_dict({"goal": "track homelab drive health"})
    second = Intent.from_dict({"goal": "Track homelab drive health!"})
    assert first.id == second.id, "precondition: these goals must collide"

    store.upsert(first)
    with pytest.raises(ValueError, match=f"intent_id_taken:{first.id}"):
        store.upsert(second)

    survived = store.load()
    assert len(survived) == 1
    assert survived[0].goal == "track homelab drive health"


def test_create_of_an_identical_goal_stays_idempotent(store):
    """Refusing a collision must not break re-posting the SAME goal: the create path
    derives the id on every POST, so an unchanged goal has to keep resolving to its own
    row (and carry a changed flag with it) rather than raising."""
    store.upsert(Intent.from_dict({"goal": "track drive health"}))
    store.upsert(Intent.from_dict({"goal": "track drive health", "propose_skill": True}))
    rows = store.load()
    assert len(rows) == 1 and rows[0].propose_skill is True


def test_upsert_rejects_bad_id(store):
    with pytest.raises(ValueError, match="invalid intent id"):
        store.upsert(Intent(id="Bad ID!"))


def test_delete(store):
    store.upsert(Intent(id="a"))
    assert store.delete("a") is True
    assert store.delete("a") is False
    assert store.load() == []


def test_load_missing_file_is_empty(tmp_path):
    assert IntentStore(tmp_path / "nope.json").load() == []


# ── prompt + matching ──


def test_build_prompt_includes_goal_and_content():
    i = Intent(id="x", goal="anything that helps my homelab")
    p = build_match_prompt(i, "Proxmox cluster tips and tricks")
    assert "anything that helps my homelab" in p
    assert "Proxmox cluster" in p


class _StubPool:
    def __init__(self, response):
        self._r = response

    async def send(self, prompt, timeout=None):
        return self._r


_MATCH = (
    '{"relevant": true, "takeaway": "Cheap NAS build", '
    '"fields": [{"name": "budget", "type": "number", "value": 400}, '
    '{"name": "stack", "type": "tags", "value": ["truenas", "zfs"]}]}'
)


def test_match_relevant_returns_typed_fields():
    i = Intent(id="lab", goal="homelab improvements")
    m = _run(match_intent(i, "TrueNAS on a $400 box", pool=_StubPool(_MATCH)))
    assert isinstance(m, IntentMatch) and m.relevant
    assert m.takeaway == "Cheap NAS build"
    by_name = {f["name"]: f for f in m.fields}
    assert by_name["budget"]["type"] == "number" and by_name["budget"]["value"] == 400
    assert by_name["stack"]["value"] == ["truenas", "zfs"]


def test_match_not_relevant_returns_none():
    i = Intent(id="lab", goal="homelab")
    assert _run(match_intent(i, "text", pool=_StubPool('{"relevant": false}'))) is None


def test_match_raise_on_error_distinguishes_failure_from_not_relevant():
    """A model failure is swallowed to None by default (graceful ingest path), but
    raise_on_error=True propagates it so a retroactive run can report 'couldn't
    evaluate' instead of a misleading 0-match."""

    class _BoomPool:
        async def send(self, prompt, timeout=None):
            raise RuntimeError("pool cold")

    i = Intent(id="lab", goal="homelab")
    # Default: error → None (indistinguishable from not-relevant, but graceful).
    assert _run(match_intent(i, "text", pool=_BoomPool())) is None
    # raise_on_error: the error surfaces.
    with pytest.raises(RuntimeError):
        _run(match_intent(i, "text", pool=_BoomPool(), raise_on_error=True))


def test_match_coerces_unknown_field_type_to_string():
    i = Intent(id="lab", goal="g")
    resp = '{"relevant": true, "fields": [{"name": "x", "type": "wat", "value": "v"}]}'
    m = _run(match_intent(i, "c", pool=_StubPool(resp)))
    assert m.fields[0]["type"] == "string"


def test_run_intents_skips_non_matching_type():
    intents = [Intent(id="inv", goal="g", enabled_for=["bookmark"])]
    out = _run(run_intents(intents, "note", "text", pool=_StubPool(_MATCH)))
    assert out == []


def test_run_intents_no_pool_is_empty():
    intents = [Intent(id="inv", goal="g")]
    assert _run(run_intents(intents, "note", "text", pool=None)) == []


def test_run_intents_matches_multiple_concurrently():
    """All applicable intents are matched (concurrently); each relevant one yields an
    outcome."""
    intents = [Intent(id="a", goal="g1"), Intent(id="b", goal="g2"), Intent(id="c", goal="g3")]
    out = _run(run_intents(intents, "note", "some content", pool=_StubPool(_MATCH)))
    assert {m.intent_id for m in out} == {"a", "b", "c"}


def test_run_intents_isolates_one_failing_intent():
    """One intent's matcher blowing up doesn't sink the others (gather isolates it)."""

    class _FlakyPool:
        def __init__(self):
            self.n = 0

        async def send(self, prompt, timeout=None):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("boom")
            return _MATCH

    out = _run(
        run_intents(
            [Intent(id="x", goal="g"), Intent(id="y", goal="g")], "note", "c", pool=_FlakyPool()
        )
    )
    # The failing one is dropped; the other still matches.
    assert len(out) == 1


# ── outcomes store (by value, soft back-ref) ──


def test_outcome_record_and_query(tmp_path):
    from personalclaw.knowledge.store import KnowledgeStore

    s = KnowledgeStore(str(tmp_path / "k.db"))
    iid = s.create_typed_item(item_type="note", title="N", content="x")
    s.record_intent_outcome(
        "lab",
        intent_name="homelab",
        item_id=iid,
        item_title="N",
        takeaway="t",
        fields=[{"name": "a", "type": "string", "value": "b"}],
    )
    by_intent = s.outcomes_for_intent("lab")
    assert len(by_intent) == 1
    assert by_intent[0]["takeaway"] == "t"
    assert by_intent[0]["fields"][0]["value"] == "b"
    assert s.outcomes_for_item(iid)[0]["intent_id"] == "lab"
    assert s.intent_outcome_counts() == {"lab": 1}


def test_outcome_survives_item_deletion_with_null_backref(tmp_path):
    from personalclaw.knowledge.store import KnowledgeStore

    s = KnowledgeStore(str(tmp_path / "k.db"))
    iid = s.create_typed_item(item_type="note", title="N", content="x")
    s.record_intent_outcome("lab", item_id=iid, item_title="N", takeaway="kept")
    s.delete_item(iid)
    outcomes = s.outcomes_for_intent("lab")
    assert len(outcomes) == 1  # insight survives
    assert outcomes[0]["item_id"] is None  # back-ref severed
    assert outcomes[0]["takeaway"] == "kept"


def test_outcome_rerun_replaces_same_pair(tmp_path):
    from personalclaw.knowledge.store import KnowledgeStore

    s = KnowledgeStore(str(tmp_path / "k.db"))
    iid = s.create_typed_item(item_type="note", title="N", content="x")
    s.record_intent_outcome("lab", item_id=iid, takeaway="v1")
    s.record_intent_outcome("lab", item_id=iid, takeaway="v2")
    outcomes = s.outcomes_for_intent("lab")
    assert len(outcomes) == 1 and outcomes[0]["takeaway"] == "v2"


def test_delete_intent_outcomes(tmp_path):
    from personalclaw.knowledge.store import KnowledgeStore

    s = KnowledgeStore(str(tmp_path / "k.db"))
    iid = s.create_typed_item(item_type="note", title="N", content="x")
    s.record_intent_outcome("lab", item_id=iid, takeaway="t")
    assert s.delete_intent_outcomes("lab") == 1
    assert s.outcomes_for_intent("lab") == []


def test_clear_item_intent_outcomes_spares_orphans(tmp_path):
    """clear_item_intent_outcomes removes only outcomes sourced from THIS item; a
    by-value outcome orphaned by a deleted item (item_id NULL) is preserved."""
    from personalclaw.knowledge.store import KnowledgeStore

    s = KnowledgeStore(str(tmp_path / "k.db"))
    iid = s.create_typed_item(item_type="note", title="N", content="x")
    s.record_intent_outcome("lab", item_id=iid, takeaway="from-this-item")
    s.record_intent_outcome("lab", item_id=None, takeaway="orphaned-but-kept")
    removed = s.clear_item_intent_outcomes(iid)
    assert removed == 1
    remaining = s.outcomes_for_intent("lab")
    assert len(remaining) == 1 and remaining[0]["takeaway"] == "orphaned-but-kept"


# ── runner integration (intent matches land as outcomes) ──


def test_runner_records_intent_outcomes(tmp_path):
    from personalclaw.knowledge.pipeline import ensure_nodes_registered
    from personalclaw.knowledge.pipeline.runner import ingest_item
    from personalclaw.knowledge.store import KnowledgeStore

    ensure_nodes_registered()
    s = KnowledgeStore(str(tmp_path / "k.db"))
    IntentStore(tmp_path / "intents.json").upsert(Intent(id="topics", goal="track topics"))
    iid = s.create_typed_item(
        item_type="note", title="N", content="A note about astronomy and telescopes."
    )
    resp = '{"relevant": true, "takeaway": "astronomy", "fields": []}'
    _run(ingest_item(s, iid, insights_pool=_StubPool(resp)))
    outcomes = s.outcomes_for_item(iid)
    assert len(outcomes) == 1 and outcomes[0]["intent_id"] == "topics"
    assert outcomes[0]["takeaway"] == "astronomy"


def test_reingest_clears_stale_outcome_when_no_longer_relevant(tmp_path):
    """If edited content no longer matches an intent it once did, the stale outcome
    from the old content must be removed on re-ingest — not linger."""
    from personalclaw.knowledge.pipeline import ensure_nodes_registered
    from personalclaw.knowledge.pipeline.runner import ingest_item
    from personalclaw.knowledge.store import KnowledgeStore

    ensure_nodes_registered()
    s = KnowledgeStore(str(tmp_path / "k.db"))
    IntentStore(tmp_path / "intents.json").upsert(Intent(id="topics", goal="track topics"))
    iid = s.create_typed_item(item_type="note", title="N", content="about astronomy")
    _run(
        ingest_item(
            s, iid, insights_pool=_StubPool('{"relevant": true, "takeaway": "astro", "fields": []}')
        )
    )
    assert len(s.outcomes_for_item(iid)) == 1
    # Re-ingest with content that no longer matches → outcome cleared, none re-recorded.
    s.update_item(iid, content="unrelated grocery list")
    s.db.commit()
    _run(ingest_item(s, iid, insights_pool=_StubPool('{"relevant": false}')))
    assert s.outcomes_for_item(iid) == []


def test_reingest_does_not_duplicate_outcome(tmp_path):
    """Re-ingesting still-relevant content replaces (not duplicates) the outcome."""
    from personalclaw.knowledge.pipeline import ensure_nodes_registered
    from personalclaw.knowledge.pipeline.runner import ingest_item
    from personalclaw.knowledge.store import KnowledgeStore

    ensure_nodes_registered()
    s = KnowledgeStore(str(tmp_path / "k.db"))
    IntentStore(tmp_path / "intents.json").upsert(Intent(id="topics", goal="track topics"))
    iid = s.create_typed_item(item_type="note", title="N", content="about astronomy")
    match = '{"relevant": true, "takeaway": "astro", "fields": []}'
    _run(ingest_item(s, iid, insights_pool=_StubPool(match)))
    _run(ingest_item(s, iid, insights_pool=_StubPool(match)))
    assert len(s.outcomes_for_item(iid)) == 1  # not 2


# ── the POST endpoint's create-vs-edit split ──


def _upsert(knowledge_store, body):
    """Drive `POST /api/knowledge/intents` the way the panel does."""
    import json
    from types import SimpleNamespace

    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import knowledge as H

    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=knowledge_store)
    req = make_mocked_request("POST", "/api/knowledge/intents", app=app)

    async def _json():
        return body

    req.json = _json
    resp = _run(H.upsert_intent(req))
    return resp, json.loads(resp.body)


@pytest.fixture
def knowledge_store(tmp_path):
    from personalclaw.knowledge.store import KnowledgeStore

    return KnowledgeStore(tmp_path / "k.db")


def test_creating_a_colliding_goal_is_a_typed_409_not_a_201(knowledge_store):
    """A body with no id is a create, and the derived slug can land on a stranger's
    intent. It used to overwrite that intent and answer 201, so the UI reported success
    for a write that destroyed data. The message must name the OTHER goal — the id is
    derived and never shown, so quoting it would be unactionable."""
    first, second = "track homelab drive health", "Track homelab drive health!"
    resp, _ = _upsert(knowledge_store, {"goal": first})
    assert resp.status == 201

    resp, body = _upsert(knowledge_store, {"goal": second})

    assert resp.status == 409
    assert body["error"]["code"] == "intent_id_taken"
    assert first in body["error"]["message"]


def test_editing_an_intent_may_still_change_its_goal(knowledge_store):
    """The refusal above must not block the edit path: a body carrying an explicit id
    means that row, and rewording the goal is the whole point of the edit."""
    resp, body = _upsert(knowledge_store, {"goal": "track drive health"})
    intent_id = body["id"]

    resp, _ = _upsert(knowledge_store, {"id": intent_id, "goal": "track drive health, weekly"})

    assert resp.status == 201
    from personalclaw.knowledge.intents import IntentStore

    rows = IntentStore(knowledge_store.db_path.parent / "intents.json").load()
    assert len(rows) == 1 and rows[0].goal == "track drive health, weekly"


# ── a model failure is an error, not a 0-match (#759) ─────────────────────────


def _run_intent(knowledge_store, intent_id, pool):
    """Drive `POST /api/knowledge/intents/{id}/run` with a given pool."""
    import json
    from types import SimpleNamespace

    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import knowledge as H

    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=knowledge_store)
    app["knowledge_llm_pool"] = pool
    req = make_mocked_request(
        "POST", f"/api/knowledge/intents/{intent_id}/run", app=app, match_info={"id": intent_id}
    )
    resp = _run(H.run_intent(req))
    return resp, json.loads(resp.body)


def _real_pool_with_a_dead_model(monkeypatch):
    """A REAL `LLMPool` of REAL `ProviderWorker`s whose provider call fails.

    Deliberately not a stub that raises `WorkerError` itself: that would assert the
    counter can add up a raised error while leaving the actual question — does a
    provider failure ever BECOME one — untested. The original bug lived precisely in
    that gap, so the chain has to be driven end to end from the provider call outward.
    """
    import asyncio as _asyncio

    from personalclaw import llm_helpers
    from personalclaw.knowledge.llm_pool import LLMPool, ProviderWorker

    async def _boom(prompt, use_case=""):
        raise RuntimeError("no model bound")

    monkeypatch.setattr(llm_helpers, "one_shot_completion", _boom)
    pool = LLMPool(pool_size=1)
    pool._started = True
    pool._workers.append(ProviderWorker())
    pool._available.put_nowait(0)
    assert isinstance(pool._semaphore, _asyncio.Semaphore)  # guard the internals we poke
    return pool


def test_a_model_failure_is_counted_as_an_error_not_a_zero_match(knowledge_store, monkeypatch):
    """The whole point of the `errors` counter, which could never leave 0.

    `ProviderWorker.send_message` swallowed every timeout and provider error to `""`,
    and `""` parses to "not relevant" — so a cold model produced `matched: 0,
    errors: 0`, which the UI reports as "No matches in your existing items". Identical
    to a genuine no-match, and the opposite of what happened.
    """
    for i in range(3):
        knowledge_store.create_typed_item(item_type="note", title=f"N{i}", content=f"body {i}")
    resp, body = _upsert(knowledge_store, {"goal": "track anything about drives"})
    intent_id = body["id"]

    resp, body = _run_intent(knowledge_store, intent_id, _real_pool_with_a_dead_model(monkeypatch))

    assert resp.status == 200
    assert body["evaluated"] == 3
    assert body["errors"] == 3, "a model failure must not read as 'nothing matched'"
    assert body["matched"] == 0
    assert body["outcomes"] == []


def test_a_working_pool_that_finds_nothing_still_reports_zero_errors(knowledge_store):
    """The other side of the distinction, and the reason this cannot be fixed by simply
    counting every 0-match as an error: a model that ran fine and found nothing is a
    legitimate 0/0, and the UI's "No matches in your existing items" is correct for it.
    """

    class _NotRelevantPool:
        async def send(self, prompt, timeout=None):
            return '{"relevant": false}'

    knowledge_store.create_typed_item(item_type="note", title="N", content="body")
    _, body = _upsert(knowledge_store, {"goal": "track anything about drives"})

    _, body = _run_intent(knowledge_store, body["id"], _NotRelevantPool())

    assert body["evaluated"] == 1
    assert body["errors"] == 0
    assert body["matched"] == 0
