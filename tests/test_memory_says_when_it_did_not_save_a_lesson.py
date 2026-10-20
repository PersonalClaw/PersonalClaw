"""A lesson memory refuses is answered as not saved, and everything a lesson stores is scanned.

``POST /api/lessons`` answered ``{"ok": true}`` whatever the memory store did with the lesson, so a
lesson its rules refused (the wording memory refuses, its size limit) read as saved on the Memory
page, and the agent's ``memory_remember`` said "Saved lesson" for a lesson nothing kept. Now a
lesson the store refuses is answered 422 ``lesson_refused`` with the store's reason, and nothing in
memory changes: the lesson it would have replaced stays, and is recalled.

The route scanned the rule it stores and not what not to do beside it, which the agent's tool sends
too, and which a lesson written as the owner's never passes the store's own scan for. Both are
scanned now: ordinary text is kept, and text the scanner flags is refused before anything is
written.
"""

from __future__ import annotations

import json

import pytest
from test_lessons_memory_reroute import _req, _state_with_record_store
from test_memory_is_read_only_where_its_work_may_read_it import HERE, _call, _gateway

from personalclaw import supply_chain, vector_memory
from personalclaw.dashboard.handlers.schedule import api_lessons, api_lessons_create

TAUGHT = "Keep the seed trays on the north bench"
#: An update past the size memory keeps for one lesson.
TOO_LONG = TAUGHT + ", " + "and check the soil along every row of the allotment, " * 90
#: The words the stand-in rules below refuse; which words the real rules refuse is theirs to say.
FLAGGED = "out of the wind"


async def _body(resp) -> dict:
    return json.loads(resp.body)


async def _listed(state) -> list[str]:
    return [lesson["rule"] for lesson in (await _body(await api_lessons(_req(state))))["lessons"]]


@pytest.mark.asyncio
async def test_an_update_the_store_refuses_is_answered_as_not_saved(tmp_path):
    """🔴 Red on integration: 200 {"ok": true}, and her lesson was gone from the list."""
    state, _vs = _state_with_record_store(tmp_path, with_embedder=False)
    saved = await api_lessons_create(_req(state, body={"rule": TAUGHT}))
    assert saved.status == 200

    refused = await api_lessons_create(_req(state, body={"rule": TOO_LONG}))

    assert refused.status == 422
    error = (await _body(refused))["error"]
    assert error["code"] == "lesson_refused"
    assert error["reason"] == "value_size"
    assert error["message"].startswith("Lesson not saved, and nothing in memory changed: ")
    assert await _listed(state) == [TAUGHT]


@pytest.mark.asyncio
async def test_a_lesson_memory_already_holds_is_answered_as_saved(tmp_path):
    state, _vs = _state_with_record_store(tmp_path, with_embedder=False)
    assert (await api_lessons_create(_req(state, body={"rule": TAUGHT}))).status == 200

    again = await api_lessons_create(_req(state, body={"rule": TAUGHT}))

    assert again.status == 200 and (await _body(again)) == {"ok": True}
    assert await _listed(state) == [TAUGHT]


def _flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """The memory-write scan reads text carrying :data:`FLAGGED` as dangerous."""
    scan = supply_chain.default_scanner.scan_text

    def stand_in(text: str, *, surface: str = "manifest") -> supply_chain.ScanReport:
        if FLAGGED not in text:
            return scan(text, surface=surface)
        finding = supply_chain.Finding(
            surface=surface,
            severity=supply_chain.Verdict.DANGEROUS,
            rule="stand_in_rule",
            path="",
            evidence=FLAGGED,
        )
        return supply_chain.ScanReport(verdict=supply_chain.Verdict.DANGEROUS, findings=[finding])

    monkeypatch.setattr(supply_chain.default_scanner, "scan_text", stand_in)


@pytest.mark.asyncio
async def test_what_not_to_do_is_scanned_like_the_rule(tmp_path, monkeypatch):
    """🔴 Red on integration: the rule was scanned, and what not to do beside it was stored
    unread."""
    _flag(monkeypatch)
    state, vs = _state_with_record_store(tmp_path, with_embedder=False)
    rule = "Shelter the seedlings at night"

    refused = await api_lessons_create(
        _req(state, body={"rule": rule, "negative": f"leave them {FLAGGED}"})
    )

    assert refused.status == 400
    assert "scanner flagged" in (await _body(refused))["error"]
    assert await _listed(state) == []
    # Ordinary text beside the rule is kept, as it always was.
    kept = await api_lessons_create(
        _req(state, body={"rule": rule, "negative": "leave them on the bench"})
    )
    assert kept.status == 200
    assert await _listed(state) == [f"{rule} — NOT: leave them on the bench"]


@pytest.mark.asyncio
async def test_the_agent_is_told_its_update_was_not_saved_and_still_recalls_the_lesson(
    tmp_path, monkeypatch
):
    """🔴 Red on integration: "Saved lesson (global): …" for an update nothing kept, and her
    lesson was recalled nowhere."""
    monkeypatch.setattr(vector_memory, "_contains_injection", lambda text: FLAGGED in text)
    update = f"{TAUGHT}, {FLAGGED}"
    async with _gateway(tmp_path, monkeypatch):
        taught_ok, taught = await _call(
            "memory_remember", {"rule": TAUGHT, "category": "knowledge"}, asked_from=HERE
        )
        update_ok, answered = await _call(
            "memory_remember", {"rule": update, "category": "knowledge"}, asked_from=HERE
        )
        recall_ok, recalled = await _call(
            "memory_recall", {"query": "seed trays north bench"}, asked_from=HERE
        )

    assert taught_ok, taught
    assert not update_ok, answered
    assert answered.startswith("Lesson not saved, and nothing in memory changed: "), answered
    assert recall_ok, recalled
    assert TAUGHT in recalled and update not in recalled
