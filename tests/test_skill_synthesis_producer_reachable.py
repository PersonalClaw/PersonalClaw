"""The ``("skill_synthesis", <key>)`` identity now has a WRITER, so its gate is reachable (#1783).

``feedback.ENFORCED_SUPPRESSION_KINDS`` names exactly one kind — ``skill_synthesis`` — as the one
whose suppression has a real effect, and ``skills.surfacing.surface_skills`` implements it: a
matched skill whose identity is in ``feedback.suppressed_producers()`` is withheld from the turn.
Every piece of that was finished except the first one. No production site ever *recorded* a
verdict against that identity, so the pair could never appear in ``producer_stats()``, so it
could never appear in the withholding set, so the branch at ``surfacing.py:304`` was dead by
construction and a persistently wrong synthesized skill kept surfacing forever.

The missing writer has two halves and these tests pin both AT THE CALL SITE, not at a helper:

  1. **Provenance.** ``GET /api/skills`` stamps ``feedback_producer`` on a synthesized (``auto``)
     skill — and on nothing else. A helper that returns the right dict proves nothing here; the
     defect was precisely that nobody called one.
  2. **A recordable verdict.** ``synthesized_skill`` is in the closed ``TARGET_KINDS`` vocabulary,
     so ``record_feedback`` accepts it instead of dropping it with a warning.

and then the consequence the clause is actually about: 👎s recorded through that pair reach
``suppressed_producers()`` and ``surface_skills`` withholds the skill. That last test is the one
that would have failed before this change with everything else in place.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw import feedback as fb
from personalclaw.dashboard.handlers import skills as skills_h
from personalclaw.skills import ephemeral
from personalclaw.skills.loader import AUTO_SKILL_SOURCE_VALUE
from personalclaw.skills.surfacing import _EmbedCache, surface_skills


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Feedback store + entity settings under tmp — never the real home."""
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    fb._invalidate()
    yield tmp_path
    fb._invalidate()


@pytest.fixture
def skill_root(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr("personalclaw.agent._all_skill_paths", lambda: [str(root)])
    return root


def _make_skill(root: Path, name: str, source_value: str = "") -> None:
    """A skill at *name* (may carry a ``auto/`` namespace) with an optional provenance marker."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    front = f"---\nname: {name.rsplit('/', 1)[-1]}\ndescription: d\n"
    if source_value:
        front += f"source: {source_value}\n"
    (skill_dir / "SKILL.md").write_text(front + "---\n\nbody\n", encoding="utf-8")


def _list() -> list[dict]:
    """The REAL route — a change to the handler is what these measure."""
    resp = asyncio.run(skills_h.api_skills_list(make_mocked_request("GET", "/api/skills")))
    assert resp.status == 200, resp.body
    return json.loads(resp.body.decode())


def _entry(key: str) -> dict:
    return next(s for s in _list() if s["key"] == key)


class TestTheListingStampsTheIdentity:
    def test_a_synthesized_skill_carries_the_producer_pair(self, skill_root):
        """The whole missing half: the key the gate matches on, on the payload the UI renders.

        ``producer_id`` must be the LOADER-relative key (``auto/<slug>``), because that is the
        string ``surface_skills`` compares against — a bare slug would build an identity the gate
        can never match, which is the same class of silent miss as having no writer at all.
        """
        _make_skill(skill_root, "auto/tighten-the-loop", AUTO_SKILL_SOURCE_VALUE)
        entry = _entry("auto/tighten-the-loop")
        assert entry["feedback_producer"] == {
            "producer_kind": "skill_synthesis",
            "producer_id": "auto/tighten-the-loop",
        }
        assert entry["feedback_producer"]["producer_kind"] in fb.PRODUCER_KINDS
        # And it is the kind the constant claims an effect for, not merely a valid one.
        assert entry["feedback_producer"]["producer_kind"] in fb.ENFORCED_SUPPRESSION_KINDS

    def test_a_hand_authored_skill_carries_nothing(self, skill_root):
        """The vacuity control. Without it, a handler that stamped every row would pass above."""
        _make_skill(skill_root, "manual-thing")
        assert "feedback_producer" not in _entry("manual-thing")

    def test_a_taught_skill_carries_nothing(self, skill_root):
        """A taught skill shares the machinery and NOT the attribution: the user reviewed that
        draft and promoted it themselves, so a 👎 on it is a verdict on their own curation. Folding
        the two together would retire the extractor over skills it never chose."""
        _make_skill(skill_root, "termbase-check", ephemeral.TAUGHT_SKILL_SOURCE_VALUE)
        entry = _entry("termbase-check")
        assert entry["provenance"] == "taught"
        assert "feedback_producer" not in entry


class TestTheVerdictIsRecordable:
    def test_synthesized_skill_is_in_the_closed_vocabulary(self):
        """``record_feedback`` drops any ``target_kind`` outside ``TARGET_KINDS`` with a warning
        and returns None, so without this entry the UI's thumbs would post into a black hole."""
        assert "synthesized_skill" in fb.TARGET_KINDS

    def test_a_verdict_on_a_synthesized_skill_persists_with_its_producer(self, isolated):
        rec = fb.record_feedback(
            target_kind="synthesized_skill",
            target_id="auto/tighten-the-loop",
            verdict="down",
            reason="the procedure skips the gate",
            producer_kind="skill_synthesis",
            producer_id="auto/tighten-the-loop",
        )
        assert rec is not None, "the closed vocabulary rejected the new kind"
        assert rec.producer_kind == "skill_synthesis"
        stored = json.loads((isolated / "feedback.jsonl").read_text().splitlines()[0])
        assert stored["target_kind"] == "synthesized_skill"
        assert stored["producer_id"] == "auto/tighten-the-loop"


def _thumb_down(key: str, n: int) -> None:
    """*n* 👎s on *n* DISTINCT targets, all attributed to one synthesizer identity.

    Distinct targets on purpose: verdicts supersede by ``(target_kind, target_id)``, so re-thumbing
    one target leaves ``n == 1`` and the threshold check (``n < min_n``) never fires. Five is the
    shipped ``min_n``.
    """
    for i in range(n):
        assert (
            fb.record_feedback(
                target_kind="synthesized_skill",
                target_id=f"{key}#{i}",
                verdict="down",
                producer_kind="skill_synthesis",
                producer_id=key,
            )
            is not None
        )


class TestTheGateIsNowReachable:
    """The consequence. Before the writer existed, every assertion here was unreachable."""

    KEY = "auto/tighten-the-loop"

    def _candidate(self, tmp_path: Path) -> dict:
        return {
            "key": self.KEY,
            "name": "tighten the loop",
            "description": "tighten the loop",
            "triggers": "tighten the loop",
            "path": str(tmp_path / "SKILL.md"),
            "always": False,
            "use_count": 3,
        }

    def test_persistent_thumbs_down_put_the_synthesizer_in_the_withholding_set(self, isolated):
        _thumb_down(self.KEY, 5)
        stats = fb.producer_stats()
        assert stats[("skill_synthesis", self.KEY)]["n"] == 5
        assert stats[("skill_synthesis", self.KEY)]["accuracy"] == 0.0
        assert ("skill_synthesis", self.KEY) in fb.suppressed_producers()

    def test_the_withheld_skill_stops_surfacing(self, isolated, tmp_path):
        """End to end through the real gate: the keyword match still hits, and the skill is gone."""
        candidate = self._candidate(tmp_path)
        cache = _EmbedCache(tmp_path / "emb.json")
        # Control first — matched and surfaced while nothing is suppressed.
        assert surface_skills("tighten the loop", [candidate], max_skills=5, embed_cache=cache) == [
            self.KEY
        ]
        _thumb_down(self.KEY, 5)
        assert (
            surface_skills(
                "tighten the loop",
                [candidate],
                max_skills=5,
                embed_cache=cache,
                suppressed=fb.suppressed_producers(),
            )
            == []
        ), "the skill still surfaced — the identity recorded is not the one the gate matches"

    def test_a_cleared_synthesizer_surfaces_again(self, isolated, tmp_path):
        """``clear_producer`` is the documented escape hatch ('until the user edits it'). It only
        means anything once something can put the producer in the set in the first place."""
        candidate = self._candidate(tmp_path)
        cache = _EmbedCache(tmp_path / "emb.json")
        _thumb_down(self.KEY, 5)
        fb.clear_producer("skill_synthesis", self.KEY)
        assert ("skill_synthesis", self.KEY) not in fb.suppressed_producers()
        assert surface_skills(
            "tighten the loop",
            [candidate],
            max_skills=5,
            embed_cache=cache,
            suppressed=fb.suppressed_producers(),
        ) == [self.KEY]

    def test_below_min_n_withholds_nothing(self, isolated, tmp_path):
        """The vacuity control on the gate itself: four 👎s are not a retirement signal."""
        _thumb_down(self.KEY, 4)
        assert ("skill_synthesis", self.KEY) not in fb.suppressed_producers()
        assert surface_skills(
            "tighten the loop",
            [self._candidate(tmp_path)],
            max_skills=5,
            embed_cache=_EmbedCache(tmp_path / "emb.json"),
            suppressed=fb.suppressed_producers(),
        ) == [self.KEY]
