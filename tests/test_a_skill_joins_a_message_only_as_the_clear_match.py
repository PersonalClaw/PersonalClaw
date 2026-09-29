"""A skill joins a message on meaning alone only when it is the clear match.

A skill whose description sat 0.552 cosine from "It's ~/Notes/Garden/Home/kitchen-reno.md." —
0.002 over the floor, 0.009 ahead of the next skill — had its whole body wrapped around that
message: a family-trip planning skill, attached to an answer about a kitchen note. The floor
was the whole test, and with a small local embedder it is the noise level: "yes, go ahead"
cleared it for three skills and a request to file PDFs for ten.

What separates a real match is its LEAD over the next skill, measured in the spread of that
message's scores across the library. The numbers below are MEASURED: the cosines a bound local
embedding model gave between each message and the 23 skills of one library (seventeen bundled,
six imported from other agents). Every message that asks for a skill gets that one skill;
every one that asks for none gets none.
"""

from __future__ import annotations

import pytest

import personalclaw.skills.surfacing as surf
from personalclaw.skills.surfacing import _EmbedCache, surface_skills

#: The library, in the order of every score string below.
_SKILLS = (
    "artifacts",
    "best-of-n",
    "check-work",
    "delegation",
    "document-authoring",
    "editorial-document",
    "grill",
    "infographic-syntax",
    "knowledge-grounding",
    "loop-worker",
    "memory-discipline",
    "pclaw-api",
    "pclaw-features",
    "research-campaign",
    "task-and-project",
    "visual-output",
    "web-verify",
    "imported/claude_code/feedsmith-release",
    "imported/claude_code/incident-writeup",
    "imported/claude_code/postgres-migration-review",
    "imported/claude_code/trip-research",
    "imported/claude_code/yt-transcript",
    "imported/codex/feedsmith-bench",
)

#: message → (the skill it asks for, or None; its measured cosine to each of `_SKILLS`).
_MEASURED: dict[str, tuple[str | None, str]] = {
    "Research a family trip to Lisbon in October, trains only": (
        "imported/claude_code/trip-research",
        "0.400 0.515 0.279 0.324 0.441 0.437 0.464 0.459 0.505 0.211 0.310 0.478"
        " 0.464 0.539 0.509 0.434 0.233 0.299 0.372 0.349 0.657 0.396 0.398",
    ),
    "release feedsmith 0.9.0": (
        "imported/claude_code/feedsmith-release",
        "0.520 0.472 0.369 0.291 0.435 0.426 0.401 0.408 0.439 0.281 0.339 0.489"
        " 0.473 0.403 0.442 0.454 0.364 0.759 0.483 0.461 0.352 0.396 0.548",
    ),
    "Cut 0.9 of feedsmith and draft the release notes": (
        "imported/claude_code/feedsmith-release",
        "0.477 0.506 0.382 0.328 0.587 0.584 0.453 0.484 0.501 0.279 0.334 0.509"
        " 0.474 0.470 0.496 0.492 0.392 0.884 0.583 0.436 0.449 0.491 0.556",
    ),
    "Turn these incident notes into a blameless postmortem": (
        "imported/claude_code/incident-writeup",
        "0.481 0.455 0.428 0.369 0.559 0.580 0.512 0.534 0.560 0.310 0.397 0.507"
        " 0.499 0.517 0.507 0.492 0.330 0.457 0.862 0.424 0.381 0.514 0.455",
    ),
    "Review alembic/versions/0042_add_carrier_index.py for lock risk": (
        "imported/claude_code/postgres-migration-review",
        "0.402 0.416 0.354 0.279 0.353 0.352 0.381 0.327 0.413 0.225 0.289 0.487"
        " 0.442 0.386 0.390 0.337 0.252 0.408 0.401 0.688 0.345 0.292 0.410",
    ),
    "What does this video say? https://youtu.be/dQw4w9WgXcQ": (
        "imported/claude_code/yt-transcript",
        "0.474 0.490 0.435 0.307 0.468 0.455 0.483 0.506 0.533 0.285 0.319 0.475"
        " 0.503 0.536 0.454 0.524 0.363 0.381 0.462 0.278 0.330 0.617 0.393",
    ),
    "Benchmark feedsmith fetch against main before I merge the store change": (
        "imported/codex/feedsmith-bench",
        "0.575 0.586 0.406 0.361 0.495 0.498 0.527 0.560 0.620 0.263 0.392 0.570"
        " 0.551 0.594 0.513 0.528 0.456 0.605 0.549 0.464 0.430 0.498 0.830",
    ),
    "It's ~/Notes/Garden/Home/kitchen-reno.md.": (
        None,
        "0.501 0.478 0.327 0.278 0.543 0.527 0.460 0.472 0.535 0.275 0.399 0.466"
        " 0.470 0.474 0.487 0.502 0.336 0.421 0.524 0.322 0.552 0.438 0.380",
    ),
    "When a new PDF lands in ~/Documents/Home/Kitchen, summarise it into my kitchen note.": (
        None,
        "0.584 0.531 0.380 0.350 0.692 0.725 0.524 0.617 0.638 0.293 0.447 0.522"
        " 0.541 0.558 0.591 0.570 0.370 0.494 0.644 0.410 0.477 0.589 0.536",
    ),
    (
        "Please set that up as an automation: when a new PDF lands in "
        "~/Documents/Home/Kitchen, summarise it into ~/Notes/Garden/Home/kitchen-reno.md."
    ): (
        None,
        "0.507 0.424 0.346 0.354 0.608 0.619 0.442 0.514 0.536 0.392 0.384 0.488"
        " 0.479 0.457 0.534 0.457 0.334 0.452 0.572 0.342 0.414 0.497 0.478",
    ),
    "yes, go ahead": (
        None,
        "0.519 0.557 0.393 0.365 0.516 0.478 0.551 0.479 0.535 0.274 0.324 0.540"
        " 0.568 0.544 0.525 0.512 0.330 0.383 0.450 0.359 0.407 0.462 0.391",
    ),
    "the second one": (
        None,
        "0.509 0.611 0.430 0.379 0.538 0.501 0.525 0.506 0.555 0.240 0.363 0.520"
        " 0.548 0.575 0.513 0.526 0.352 0.373 0.488 0.352 0.390 0.470 0.442",
    ),
    "~/Notes/Garden/Trips/porto.md": (
        None,
        "0.467 0.493 0.318 0.298 0.490 0.492 0.472 0.511 0.539 0.239 0.360 0.514"
        " 0.503 0.510 0.522 0.491 0.268 0.350 0.493 0.322 0.573 0.469 0.382",
    ),
    (
        "Draft my async standup for #team-ingest. Yesterday means the last working day "
        "before today."
    ): (
        None,
        "0.556 0.541 0.392 0.485 0.623 0.594 0.583 0.586 0.537 0.403 0.339 0.573"
        " 0.596 0.525 0.673 0.567 0.358 0.465 0.626 0.388 0.450 0.501 0.503",
    ),
}


_BY_TEXT = {
    q: dict(zip(_SKILLS, map(float, scores.split()), strict=True))
    for q, (_want, scores) in _MEASURED.items()
}


@pytest.fixture
def library(monkeypatch, tmp_path):
    """The measured library behind the real embedder seam: a message embeds as itself, a skill
    as its description (its key here), and a cosine is the one measured for that pair."""

    def embed(text: str) -> list[str]:
        return [text]

    def cosine(query_vec: list[str], skill_vec: list[str]) -> float:
        # A skill with triggers embeds as `description\ntriggers`; its score is its description's.
        return _BY_TEXT[query_vec[0]][skill_vec[0].split("\n", 1)[0]]

    monkeypatch.setattr(surf, "_active_embedder", lambda: (embed, "local:measured"))
    monkeypatch.setattr(surf, "_cosine", cosine)
    skills = []
    for key in _SKILLS:
        path = tmp_path / key / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        skills.append(
            {
                "key": key,
                "name": key,
                "description": key,
                "triggers": "",
                "path": str(path),
                "always": False,
                "use_count": 0,
            }
        )
    cache = _EmbedCache(path=tmp_path / ".emb.json")
    return skills, cache


def test_a_bare_answer_naming_a_path_carries_no_skill(library):
    skills, cache = library
    assert (
        surface_skills(
            "It's ~/Notes/Garden/Home/kitchen-reno.md.", skills, max_skills=3, embed_cache=cache
        )
        == []
    )


@pytest.mark.parametrize("message", list(_MEASURED))
def test_each_message_gets_the_skill_it_asks_for_or_none(library, message):
    skills, cache = library
    want = _MEASURED[message][0]
    got = surface_skills(message, skills, max_skills=3, embed_cache=cache)
    assert got == ([want] if want else []), f"{message!r} surfaced {got}"


def test_the_doctor_says_why_the_close_skill_stayed_out(library):
    skills, cache = library
    rows = surface_skills(
        "It's ~/Notes/Garden/Home/kitchen-reno.md.",
        skills,
        max_skills=3,
        embed_cache=cache,
        explain=True,
    )
    trip = next(r for r in rows if r["key"] == "imported/claude_code/trip-research")
    assert trip["included"] is False
    assert trip["sem_score"] >= trip["threshold_sem"], "it DID clear the floor — that is the point"
    assert trip["lead"] is not None and trip["lead"] < trip["threshold_lead"]
    assert "leads" in trip["reason"] and "only" in trip["reason"]
    # Only the closest skill carries a lead; the rest say nothing about one.
    assert [r["key"] for r in rows if r["lead"] is not None] == [trip["key"]]


def test_a_declared_trigger_still_fires_where_meaning_picks_nothing(library):
    """Triggers are the author's own words: the lead rule is for meaning, not for them."""
    skills, cache = library
    for s in skills:
        if s["key"] == "task-and-project":
            s["triggers"] = "async standup"
    got = surface_skills(
        "Draft my async standup for #team-ingest. Yesterday means the last working day before "
        "today.",
        skills,
        max_skills=3,
        embed_cache=cache,
    )
    assert got == ["task-and-project"]


def test_a_library_too_small_to_measure_a_lead_picks_nothing_on_meaning(library, tmp_path):
    skills, cache = library
    few = [s for s in skills if s["key"].startswith("imported/claude_code/")][:3]
    assert surface_skills("release feedsmith 0.9.0", few, max_skills=3, embed_cache=cache) == []
    rows = surface_skills(
        "release feedsmith 0.9.0", few, max_skills=3, embed_cache=cache, explain=True
    )
    assert all(
        "too few to tell a clear match" in r["reason"] for r in rows if r["sem_score"] >= 0.55
    )
