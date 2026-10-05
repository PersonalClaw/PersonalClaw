"""Only she replaces a lesson she taught: what is learned beside it never retires it.

A lesson the owner taught (its row's source is one a person wrote: ``user_explicit`` from her own
routes and tools, ``vault_edit`` from her vault) was retired by any lesson something else learned
that the dedup pass judged to replace it: a chat's consolidation, the after-turn review, the
contradiction judge's verdict. The store's own rule, that only a person overwrites what a person
wrote, held for a write under the same key alone. And the pass judged two lessons the same rule by
the share of the SMALLER one's words they held, so one shared word retired a short rule: "Never
deploy on Fridays" went to "Deploy the docs site from the release branch", on the word "deploy".

Now nothing learned retires, replaces or rewrites a lesson she taught. Where it would have, hers
stays as it is, nothing of the learned one is saved, and she is asked which to keep: a learning
proposal, in the Inbox and on the Learning page, naming both. Accept keeps the learned one in place
of hers, as a lesson of hers; Reject keeps hers, and the learned one is not offered again. Two
lessons are one rule said again only when they share at least two of their words and half of all
the words either holds; the framing a correction lesson opens with is no word of its rule.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw import memory_formation as mf
from personalclaw import vector_memory
from personalclaw.after_turn_review import CORRECTION_PREFIX
from personalclaw.context import ContextBuilder
from personalclaw.dashboard.chat_persistence import save_session_to_history
from personalclaw.dashboard.chat_runner import _maybe_after_turn_review
from personalclaw.dashboard.chat_utils import _history_key_for
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog, HistoryConsolidator
from personalclaw.inbox import InboxStore
from personalclaw.learning import installers, proposals
from personalclaw.learning.lesson_confidence import get_store
from personalclaw.memory import MemoryStore
from personalclaw.memory_service import service_for
from personalclaw.skills import SkillsLoader
from personalclaw.vector_memory import VectorMemoryStore

#: The rule she taught, and what consolidation learns that would replace it: the same rule, said
#: wider.
HERS = "Never deploy on Fridays"
WIDER = "Do not deploy on Fridays or weekends"
#: What consolidation learns that shares one word with hers and is another rule altogether.
ONE_WORD = "Deploy the docs site from the release branch"


@pytest.fixture
def gw(tmp_path: Path):
    """A gateway's memory wiring over the test's home: the chat state that saves transcripts, the
    global memory (markdown and memory database) the context builder and the consolidator share,
    and a consolidation model that answers each chat with what it keeps from it."""
    log = ConversationLog()
    log.init()
    main = MemoryStore()
    main.init()
    store = VectorMemoryStore()
    store.init()
    main.vector_store = store
    builder = ContextBuilder(
        memory=main,
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )
    builder.conversation_log = log
    consolidator = HistoryConsolidator(
        log=log, memory=main, vector_store=store, migrated=True, history_idle_secs=0
    )
    answers: dict[str, dict] = {}
    model = AsyncMock(side_effect=lambda _prompt, key: json.loads(json.dumps(answers.get(key, {}))))
    consolidator._call_llm = model  # type: ignore[method-assign]
    sessions = MagicMock(count=0)
    sessions.remove = AsyncMock()
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        context_builder=builder,
        conversation_log=log,
        consolidator=consolidator,
    )
    yield SimpleNamespace(
        tmp=tmp_path,
        main=main,
        store=store,
        svc=service_for(main),
        consolidator=consolidator,
        answers=answers,
        state=state,
    )
    store.close()


def _chat(gw: SimpleNamespace, *, lessons: list[str] = (), folder: str = "") -> tuple[str, object]:
    """A chat, saved as the dashboard saves a running chat, whose consolidation keeps *lessons*.
    Returns ``(history key, session)``."""
    session = gw.state.get_or_create_session(name=None, workspace_dir=folder or None)
    session.append("user", "Here is how we ship the site.", broadcast=False)
    session.append("assistant", "Noted.", broadcast=False)
    session.drain()
    save_session_to_history(gw.state, session, force=True)
    key = _history_key_for(session.key)
    gw.answers[key] = {
        "history_entry": "Talked through how the site ships.",
        "lessons": [{"rule": rule, "category": "knowledge"} for rule in lessons],
    }
    return key, session


async def _consolidate(gw: SimpleNamespace, *lessons: str) -> str:
    key, _session = _chat(gw, lessons=list(lessons))
    assert await gw.consolidator.consolidate_session(key)
    return key


def _row(vs: VectorMemoryStore, text: str) -> dict | None:
    """The lesson row that holds *text*, live or not."""
    row = vs.db.execute(
        "SELECT * FROM semantic_memory WHERE key LIKE 'lesson.%' AND value_json = ?",
        (json.dumps(text),),
    ).fetchone()
    return dict(row) if row else None


def _live(vs: VectorMemoryStore) -> list[str]:
    return sorted(str(json.loads(r["value_json"])) for r in vs.get_lessons())


def _as_taught(vs: VectorMemoryStore, text: str) -> None:
    """Her lesson is live, replaced by nothing, seen once, uncontradicted, and in every prompt."""
    row = _row(vs, text)
    assert row is not None and row["is_deleted"] == 0, "her lesson was retired"
    assert row["superseded_by"] is None and row["invalidated_at"] is None
    assert row["source"] == "user_explicit"
    evidence = get_store(vs.db_path.parent).evidence_for(row["key"])
    assert evidence.observations == 1 and evidence.contradictions == 0
    assert text in vs.get_lessons_context()
    retired = [
        e
        for e in vs.get_events(limit=100)
        if e["event_type"] == "supersede" and e["memory_key"] == row["key"]
    ]
    assert retired == [], "the history says her lesson was replaced"


def _conflicts() -> list[proposals.Proposal]:
    """The pending proposals that ask her to keep her lesson or a learned one."""
    return [
        p
        for p in proposals.list_pending(proposals.Kind.LESSON_BATCH.value)
        if "lesson_conflict" in (p.tags or [])
    ]


def _asked(*, hers: str, learned: str) -> proposals.Proposal:
    """The one question waiting about *learned* replacing *hers*, which names both, in the Inbox
    too."""
    asked = [p for p in _conflicts() if f"Learned: “{learned}”" in p.body]
    assert len(asked) == 1, [p.body for p in _conflicts()]
    prop = asked[0]
    assert f"Yours: “{hers}”" in prop.body
    assert f"Yours: “{hers}”" in prop.source_excerpt
    assert f"Learned: “{learned}”" in prop.source_excerpt
    inbox = InboxStore()
    inbox.load()
    rows = [i for i in inbox.items.values() if i.refs.get("learning_proposal") == prop.id]
    assert len(rows) == 1, "the question is not in the Inbox"
    return prop


def _accept(gw: SimpleNamespace, prop: proposals.Proposal) -> None:
    """Her Accept, through the installer the Learning page and the Inbox both bind: over the
    global memory's service (``handlers.learning._installer_for``)."""
    from personalclaw.dashboard.handlers.memory import _global_service

    service = _global_service(gw.state)
    proposals.accept(prop.id, installer=installers.installer_for(service=service))


def _midband_embedder():
    """Embeds over a small vocabulary, so two related rules land in the band the contradiction
    judge is asked about (more alike than unrelated rules, less than one rule said again)."""
    vocab = ["deploy", "friday", "weekend", "morning", "release", "ship", "code", "review"]

    def emb(text: str) -> list[float]:
        lowered = text.lower()
        vec = [1.0 if word in lowered else 0.0 for word in vocab]
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    return emb


# ── what is learned leaves the lesson she taught as it is, and asks her ─────────────────────


@pytest.mark.asyncio
async def test_consolidation_leaves_her_lesson_as_it_is_and_asks_her(gw) -> None:
    """🔴 Red on integration: the consolidated lesson retired hers."""
    assert gw.svc.write_lesson(HERS) is True

    await _consolidate(gw, WIDER)

    _as_taught(gw.store, HERS)
    assert _row(gw.store, WIDER) is None, "the learned lesson was saved over hers"
    prop = _asked(hers=HERS, learned=WIDER)
    assert prop.source_cadence == "consolidation"


@pytest.mark.asyncio
async def test_a_learned_lesson_that_shares_one_word_with_hers_is_kept_beside_it(gw) -> None:
    """🔴 Red on integration: "Deploy the docs site from the release branch" retired "Never deploy
    on Fridays" on the one word they share."""
    assert gw.svc.write_lesson(HERS) is True

    await _consolidate(gw, ONE_WORD)

    _as_taught(gw.store, HERS)
    assert _live(gw.store) == sorted([HERS, ONE_WORD])
    assert _conflicts() == [], "two different rules were asked about as one"


def test_the_contradiction_judge_leaves_her_lesson_as_it_is_and_asks_her(gw) -> None:
    """🔴 Red on integration: the judge's verdict retired her lesson and counted a contradiction
    against it, which also drops it out of every prompt."""
    hers, learned = "Never deploy code on Fridays", "Deploy on Friday mornings is fine"
    gw.store.embed_fn = _midband_embedder()
    judged: list[tuple[str, str]] = []

    def judge(new: str, old: str) -> bool:
        judged.append((new, old))
        return True

    gw.store.contradiction_judge = judge
    assert gw.svc.write_lesson(hers) is True

    assert gw.svc.write_lesson(learned, source="consolidation") is False

    assert judged == [(learned, hers)], "the judge was not asked about her lesson"
    _as_taught(gw.store, hers)
    assert _row(gw.store, learned) is None, "the learned lesson was saved before she answered"
    _asked(hers=hers, learned=learned)


@pytest.mark.parametrize(
    "said, learned",
    [
        (
            "No, never deploy on Fridays, I told you",
            # The veto the preference detector reads out of it, and the correction lesson
            # that quotes it.
            [
                "Never deploy on Fridays, I told you",
                f"{CORRECTION_PREFIX}No, never deploy on Fridays, I told you",
            ],
        ),
        (
            "Never deploy on Fridays or weekends",
            [
                "Never deploy on Fridays or weekends",
                f"{CORRECTION_PREFIX}Never deploy on Fridays or weekends",
            ],
        ),
    ],
)
def test_the_after_turn_review_leaves_her_lesson_as_it_is_and_asks_her(said, learned, gw) -> None:
    """🔴 Red on integration: what the review learned from her message retired her lesson."""
    assert gw.svc.write_lesson(HERS) is True
    _key, session = _chat(gw)

    _maybe_after_turn_review(
        gw.state, session, user_message=said, assistant_text="Understood.", tool_calls=0
    )

    _as_taught(gw.store, HERS)
    assert _live(gw.store) == [HERS], "what the review learned was saved over her lesson"
    for each in learned:
        _asked(hers=HERS, learned=each)
    assert len(_conflicts()) == len(learned)


def test_a_veto_held_for_her_answer_is_not_announced_as_learned(gw) -> None:
    """🔴 Red on integration: the chip said "Learned" for a veto memory did not keep."""
    from personalclaw.after_turn_review import capture_preference_facet

    assert gw.svc.write_lesson(HERS) is True

    assert capture_preference_facet(gw.svc, "Never deploy on Fridays or weekends") is None
    assert _conflicts(), "she was not asked"


def test_a_folder_chats_learning_asks_her_about_the_lesson_she_taught_there(gw, tmp_path) -> None:
    """The question names the memory her lesson is in, and her Accept keeps the learned one
    there."""
    folder = tmp_path / "site"
    folder.mkdir()
    folder_svc = service_for(ContextBuilder.get_memory_for(str(folder), writes=True))
    assert folder_svc.write_lesson(HERS) is True
    _key, session = _chat(gw, folder=str(folder))

    _maybe_after_turn_review(
        gw.state, session, user_message=WIDER, assistant_text="Understood.", tool_calls=0
    )

    folder_vs = folder_svc._vs
    _as_taught(folder_vs, HERS)
    learned = f"{CORRECTION_PREFIX}{WIDER}"
    prop = _asked(hers=HERS, learned=learned)
    assert f"in the memory of {folder}" in prop.body
    assert _row(gw.store, HERS) is None, "premise: her lesson is in the folder's memory alone"

    _accept(gw, prop)

    kept = _row(folder_vs, learned)
    assert kept is not None and kept["is_deleted"] == 0 and kept["source"] == "user_explicit"
    assert _row(folder_vs, HERS)["superseded_by"] == kept["key"]
    assert _row(gw.store, learned) is None, "her answer reached another memory"


# ── her answer ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_her_accept_keeps_the_learned_lesson_as_hers_in_place_of_hers(gw) -> None:
    assert gw.svc.write_lesson(HERS) is True
    await _consolidate(gw, WIDER)
    prop = _asked(hers=HERS, learned=WIDER)

    _accept(gw, prop)

    old, new = _row(gw.store, HERS), _row(gw.store, WIDER)
    assert new is not None and new["is_deleted"] == 0 and new["superseded_by"] is None
    assert new["source"] == "user_explicit", "her answer is a lesson she taught"
    assert old["is_deleted"] == 1 and old["superseded_by"] == new["key"]
    supersede = [
        e
        for e in gw.store.get_events(limit=100)
        if e["event_type"] == "supersede" and e["memory_key"] == old["key"]
    ]
    assert [e["source"] for e in supersede] == ["user_explicit"]
    context = gw.store.get_lessons_context()
    assert WIDER in context and HERS not in context
    assert _conflicts() == []


@pytest.mark.asyncio
async def test_her_reject_keeps_hers_and_the_learned_one_is_not_offered_again(gw) -> None:
    assert gw.svc.write_lesson(HERS) is True
    await _consolidate(gw, WIDER)
    prop = _asked(hers=HERS, learned=WIDER)

    assert proposals.reject(prop.id) is True
    await _consolidate(gw, WIDER)

    _as_taught(gw.store, HERS)
    assert _row(gw.store, WIDER) is None
    assert _conflicts() == [], "the question she answered was asked again"


@pytest.mark.asyncio
async def test_her_accept_after_she_removed_her_lesson_still_keeps_the_learned_one(gw) -> None:
    assert gw.svc.write_lesson(HERS) is True
    await _consolidate(gw, WIDER)
    prop = _asked(hers=HERS, learned=WIDER)
    assert gw.svc.delete_lesson(HERS) is True

    _accept(gw, prop)

    assert _live(gw.store) == [WIDER]


@pytest.mark.asyncio
async def test_an_accept_memory_refuses_changes_nothing_and_the_question_waits(
    gw, monkeypatch
) -> None:
    assert gw.svc.write_lesson(HERS) is True
    await _consolidate(gw, WIDER)
    prop = _asked(hers=HERS, learned=WIDER)
    monkeypatch.setattr(vector_memory, "_contains_injection", lambda text: "weekends" in text)

    with pytest.raises(proposals.AcceptError):
        _accept(gw, prop)

    _as_taught(gw.store, HERS)
    assert _row(gw.store, WIDER) is None
    assert [p.id for p in _conflicts()] == [prop.id], "the question stopped waiting"


def test_a_store_that_is_no_memory_of_hers_asks_nobody_and_keeps_her_lesson(tmp_path) -> None:
    """A scratch or benchmark store has nobody to ask: hers stays, and nothing is filed."""
    store = VectorMemoryStore(db_path=tmp_path / "bench" / "memory.db")
    store.init()
    try:
        assert store.write_lesson(HERS) is True

        assert store.write_lesson(WIDER, source="consolidation") is False

        _as_taught(store, HERS)
        assert _row(store, WIDER) is None
        assert _conflicts() == []
    finally:
        store.close()


def test_her_own_new_lesson_still_replaces_her_older_one(gw) -> None:
    assert gw.svc.write_lesson(HERS) is True

    assert gw.svc.write_lesson(WIDER) is True

    assert _live(gw.store) == [WIDER]
    assert _row(gw.store, HERS)["superseded_by"] == _row(gw.store, WIDER)["key"]
    assert _conflicts() == []


# ── one rule for every write that would replace what she wrote ──────────────────────────────


def test_no_supersession_by_anything_else_retires_what_she_wrote(gw) -> None:
    """🔴 Red on integration: a supersession named by an automated source retired her lesson."""
    assert gw.svc.write_lesson(HERS) is True
    assert gw.svc.write_lesson(ONE_WORD, source="consolidation") is True

    assert (
        gw.store.supersede_semantic(
            _row(gw.store, HERS)["key"], _row(gw.store, ONE_WORD)["key"], "consolidation"
        )
        is False
    )

    _as_taught(gw.store, HERS)


def test_formation_keeps_a_fact_she_set_beside_one_it_would_supersede_it_with(gw) -> None:
    """🔴 Red on integration: consolidation's formation retired a fact she set, under another key."""
    assert gw.store.set_semantic("user.editor", "vim", 1.0, "user_explicit") is None
    cands = mf.gather(
        gw.store, [mf.Candidate(index=0, key="user.text_editor", value="helix", confidence=0.9)]
    )
    assert any(o.key == "user.editor" for o in cands[0].overlaps), "premise: Gather saw hers"
    decisions = {0: mf.Decision(index=0, verdict=mf.VERDICT_SUPERSEDE, target="user.editor")}

    report = mf.apply_decisions(gw.store, cands, decisions, source="consolidation:chat-1")

    assert gw.store.get_semantic("user.editor") is not None, "the fact she set was retired"
    assert gw.store.get_semantic("user.text_editor") is not None
    assert report.superseded == 0
    assert report.conflicts == [("user.text_editor", "user.editor")]
    assert [(c["new_key"], c["old_key"]) for c in mf.conflicts(gw.store)] == [
        ("user.text_editor", "user.editor")
    ]


def test_formation_never_removes_a_fact_she_set(gw) -> None:
    """🔴 Red on integration: an extract-phase delete removed a fact she set."""
    assert gw.store.set_semantic("user.pet_name", "Rex", 1.0, "user_explicit") is None
    cands = [mf.Candidate(index=0, key="user.pet_name", value=None, delete=True)]

    report = mf.apply_decisions(gw.store, cands, {}, source="consolidation:chat-1")

    assert gw.store.get_semantic("user.pet_name") is not None
    assert report.superseded == 0


# ── when two lessons are one rule said again ────────────────────────────────────────────────

#: Two lessons consolidation learns that share one word and are different rules.
ONE_SHARED_WORD = [
    (HERS, ONE_WORD),
    ("Use tabs for indentation", "Indentation in YAML files is two spaces"),
    ("Never force push", "Push the tag after the release notes are merged"),
    ("Always run the linter", "Run the migrations before seeding the database"),
]

#: Two lessons consolidation learns that are one rule said again, the second in other words.
RESTATED = [
    ("Use pnpm, not npm, in this repo", "In this repo use pnpm instead of npm"),
    (
        "Write commit messages in the imperative mood",
        "Commit messages should use the imperative mood",
    ),
    ("Keep answers short", "Keep your answers short and direct"),
    (f"{CORRECTION_PREFIX}no, use pnpm not npm", f"{CORRECTION_PREFIX}use pnpm instead of npm"),
]


@pytest.mark.parametrize("first, second", ONE_SHARED_WORD)
def test_one_shared_word_never_merges_two_learned_lessons(first, second, gw) -> None:
    """🔴 Red on integration: the second lesson retired the first on the one word they share."""
    assert gw.svc.write_lesson(first, source="consolidation") is True

    assert gw.svc.write_lesson(second, source="consolidation") is True

    assert _live(gw.store) == sorted([first, second])


def test_two_corrections_about_different_things_are_two_lessons(gw) -> None:
    """🔴 Red on integration: every correction lesson opens with the same framing, and its words
    counted as shared, so the second correction retired the first."""
    first, second = f"{CORRECTION_PREFIX}No, use pnpm", f"{CORRECTION_PREFIX}No, be brief"
    assert gw.svc.write_lesson(first, source="after_turn_review") is True

    assert gw.svc.write_lesson(second, source="after_turn_review") is True

    assert _live(gw.store) == sorted([first, second])


@pytest.mark.parametrize("first, second", RESTATED)
def test_a_learned_lesson_said_again_in_other_words_still_replaces_the_older(
    first, second, gw
) -> None:
    source = "after_turn_review" if first.startswith(CORRECTION_PREFIX) else "consolidation"
    assert gw.svc.write_lesson(first, source=source) is True

    assert gw.svc.write_lesson(second, source=source) is True

    assert _live(gw.store) == [second]
    assert _row(gw.store, first)["superseded_by"] == _row(gw.store, second)["key"]


def test_the_judges_verdict_still_retires_a_lesson_nobody_taught(gw) -> None:
    """Among learned lessons the judge's verdict still decides, after the new one is kept."""
    gw.store.embed_fn = _midband_embedder()
    gw.store.contradiction_judge = lambda new, old: True
    assert gw.svc.write_lesson("Never deploy code on Fridays", source="consolidation") is True

    assert gw.svc.write_lesson("Deploy on Friday mornings is fine", source="consolidation") is True

    assert _live(gw.store) == ["Deploy on Friday mornings is fine"]
    assert _conflicts() == []
