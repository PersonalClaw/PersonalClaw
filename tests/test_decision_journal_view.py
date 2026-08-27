"""The Decision Journal's HTTP read surface (PROACTIVE-ASSISTANT §2.5/§5.3, atom PA-6).

`PA-4`'s execution log recorded that `PA-6` was "frontend-only" because ``calibration()``
already computes the strip. It is not: ``decision_list``/``decision_resolve`` are *chat tools*,
and nothing served a decision or a calibration bucket over HTTP, so the view had no read path at
all. This file covers the one route that closes that — and, more importantly, the property that
made a route necessary rather than letting the browser aggregate raw rows itself:

**there is exactly ONE definition of how well-calibrated the user is.** Every assertion below
compares the payload to ``decisions.calibration``/``decisions.list_decisions`` *by value*, so a
handler that recomputed anything — a filtered aggregate, a rate rounded differently, a
``count_honest`` derived against a locally-spelled ten — goes red instead of quietly giving a
second answer to the question the strip asks.

Stores are ``tmp_path``: ``decisions`` takes ``store`` as a parameter, and the ONE path that
reaches for a live singleton (``_knowledge_store(None)``, which the handler necessarily uses) is
redirected here with an assertion that the redirect actually took — a patch that missed would
otherwise read the developer's real ``~/.personalclaw`` and still pass.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers.decisions import api_decision_journal
from personalclaw.decisions import (
    CALIBRATION_MIN_N,
    calibration,
    horizon_from_days,
    list_decisions,
    log_decision,
    resolve_decision,
)
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.triggers.store import TriggerStore

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"


@pytest.fixture
def store(tmp_path: Path) -> KnowledgeStore:
    return KnowledgeStore(os.path.join(tmp_path, "knowledge.db"))


@pytest.fixture
def triggers(tmp_path: Path) -> TriggerStore:
    return TriggerStore(base_dir=tmp_path)


@pytest.fixture
def memory(tmp_path: Path):
    from personalclaw.memory_service import MemoryService
    from personalclaw.vector_memory import VectorMemoryStore

    vs = VectorMemoryStore(db_path=tmp_path / "memory.db")
    vs.init()
    return MemoryService.over_vector_store(vs)


@pytest.fixture
def served(monkeypatch, store):
    """Point the handler's live-singleton read at the tmp store, and PROVE it landed.

    ``decisions._knowledge_store`` late-imports ``personalclaw.knowledge.get_knowledge_store``
    inside the call, so patching that module attribute is what reaches the handler. The
    assertion below is the point: without it a patch aimed at the wrong binding leaves this
    whole file reading the real home, where every count is somebody's actual journal.
    """
    import personalclaw.knowledge as knowledge_pkg
    from personalclaw.decisions import _knowledge_store

    monkeypatch.setattr(knowledge_pkg, "get_knowledge_store", lambda *a, **k: store)
    assert (
        _knowledge_store() is store
    ), "the singleton redirect did not take — this suite would read the real home"
    return store


async def _get(**params) -> tuple[int, dict]:
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    req = make_mocked_request("GET", f"/api/knowledge/decisions{'?' + qs if qs else ''}")
    resp = await api_decision_journal(req)
    return resp.status, json.loads(resp.body)


def _log(store, triggers, **kw) -> dict:
    args = {
        "summary": "Take the contract over the salaried role",
        "content": "Reasoning: optionality now, less security.",
        "expectation": "I will earn more and regret the lost benefits by month six",
        "confidence": 0.7,
        "domain": "career",
        "review_horizon": horizon_from_days(90),
    }
    args.update(kw)
    return log_decision(store=store, trigger_store=triggers, **args)


def _resolve(store, triggers, memory, item_id: str, grade: str) -> None:
    resolve_decision(
        item_id,
        outcome=f"it turned out {grade}",
        grade=grade,
        store=store,
        trigger_store=triggers,
        memory=memory,
    )


# ── the route exists and is reachable ────────────────────────────────────────


class TestRegistration:
    def test_the_route_is_registered_and_points_at_this_handler(self) -> None:
        """A handler nothing routes to is the defect this repo keeps finding: its own tests are
        green and the surface is unreachable. Asserted against ``server.py`` because that is the
        one file that decides whether a URL exists."""
        src = (SRC / "dashboard" / "server.py").read_text()
        assert 'add_get("/api/knowledge/decisions", handlers.api_decision_journal)' in src
        # Vacuity floor: if the probe string above ever stops matching the file's real spelling,
        # this line says so instead of the assertion silently passing on a renamed route.
        assert src.count("/api/knowledge/decisions") == 1

    def test_the_handler_is_exported_from_the_facade(self) -> None:
        """``server.py`` reaches handlers as ``handlers.X``, so an unexported handler is an
        AttributeError at route-registration time — i.e. at gateway boot, not in a test."""
        from personalclaw.dashboard import handlers

        assert handlers.api_decision_journal is api_decision_journal


# ── one payload, one definition of the numbers ───────────────────────────────


class TestOneReadPath:
    @pytest.mark.asyncio
    async def test_the_rows_and_the_strip_arrive_together(
        self, served, store, triggers, memory
    ) -> None:
        """ONE request carries both. Two routes would let a client render eleven resolved
        decisions above a rate computed from ten — two answers to one question, from two
        fetches that raced."""
        row = _log(store, triggers)
        _resolve(store, triggers, memory, row["id"], "as_expected")
        status, body = await _get()
        assert status == 200
        assert [d["id"] for d in body["decisions"]] == [row["id"]]
        assert body["calibration"]["career"]["n"] == 1

    @pytest.mark.asyncio
    async def test_the_payload_is_the_owning_modules_own_answer(
        self, served, store, triggers, memory
    ) -> None:
        """By VALUE against ``calibration()`` and ``list_decisions()``. This is the assertion a
        handler that re-aggregated — even correctly, even once — could not satisfy forever, which
        is the whole reason the FE consumes this rather than the raw item rows."""
        for grade in ("better", "as_expected", "worse", "as_expected"):
            _resolve(store, triggers, memory, _log(store, triggers)["id"], grade)
        _, body = await _get()
        assert body["calibration"] == calibration(store=store)
        assert body["decisions"] == list_decisions(store=store, limit=200)

    @pytest.mark.asyncio
    async def test_the_strip_ignores_the_list_filter(self, served, store, triggers, memory) -> None:
        """``status``/``domain`` narrow the LIST only. The strip is the user's calibration across
        everything they resolved; recomputing it per filter would silently redefine the claim it
        makes as the user clicked around — the same number meaning different things."""
        _resolve(store, triggers, memory, _log(store, triggers)["id"], "as_expected")
        _log(store, triggers, domain="health")
        status, body = await _get(status="pending")
        assert status == 200
        assert [d["status"] for d in body["decisions"]] == ["pending"]
        # The resolved career decision is absent from the list and PRESENT in the strip.
        assert "career" in body["calibration"]

    @pytest.mark.asyncio
    async def test_the_threshold_is_forwarded_not_respelled(
        self, served, store, triggers, memory
    ) -> None:
        """The view needs the number to word its caveat ("3 of 10"). Two spellings of ten and the
        strip could caveat a bucket the backend had already called honest."""
        _resolve(store, triggers, memory, _log(store, triggers)["id"], "worse")
        _, body = await _get()
        assert body["calibration_min_n"] == CALIBRATION_MIN_N

    def test_the_handler_does_not_carry_its_own_copy_of_the_threshold(self) -> None:
        """The static half of the test above: a literal ten in the handler is a second spelling
        even while the values agree, and it agrees only until somebody tunes one of them."""
        src = (SRC / "dashboard" / "handlers" / "decisions.py").read_text()
        assert "CALIBRATION_MIN_N" in src
        body = src.split("def api_decision_journal")[1]
        assert "10" not in body, "the handler spells the threshold itself instead of forwarding it"

    @pytest.mark.asyncio
    async def test_the_vocabularies_ride_the_payload(self, served, store, triggers) -> None:
        """A client with its own copy of the domain list offers a filter the server rejects."""
        from personalclaw.decisions import CALIBRATED_GRADES, DECISION_DOMAINS, DECISION_STATUSES

        _, body = await _get()
        assert body["statuses"] == list(DECISION_STATUSES)
        assert body["domains"] == list(DECISION_DOMAINS)
        assert body["grades"] == list(CALIBRATED_GRADES)


# ── the three calibration states, distinguishable at the wire ────────────────


class TestThreeStatesAtTheWire:
    """The strip has three things it may say, and the payload has to keep them apart.

    The failure this guards is not a wrong number — it is two DIFFERENT truths arriving as the
    same bytes, so the view cannot tell them apart no matter how carefully it renders.
    """

    @pytest.mark.asyncio
    async def test_no_resolved_decision_is_an_empty_strip_not_a_zero(
        self, served, store, triggers
    ) -> None:
        """Eight pending decisions and nothing resolved: the strip must have NOTHING, so the view
        is forced to say so. A bucket with ``n: 0`` and ``as_expected_rate: 0.0`` here would let
        the strip draw a flat bar, which reads as perfect calibration — the strongest possible
        claim where the truth is that nobody knows yet."""
        _log(store, triggers)
        _, body = await _get()
        assert body["calibration"] == {}
        assert body["decisions"], "vacuity floor: an empty journal would satisfy the line above"

    @pytest.mark.asyncio
    async def test_under_the_threshold_is_marked_dishonest_with_its_real_count(
        self, served, store, triggers, memory
    ) -> None:
        """Three resolved: a bucket EXISTS (so the view knows there is data) and
        ``count_honest`` is False (so the view knows not to draw a rate)."""
        for _ in range(3):
            _resolve(store, triggers, memory, _log(store, triggers)["id"], "as_expected")
        _, body = await _get()
        bucket = body["calibration"]["career"]
        assert bucket["n"] == 3
        assert bucket["count_honest"] is False

    @pytest.mark.asyncio
    async def test_at_the_threshold_it_becomes_honest(
        self, served, store, triggers, memory
    ) -> None:
        for _ in range(CALIBRATION_MIN_N):
            _resolve(store, triggers, memory, _log(store, triggers)["id"], "as_expected")
        _, body = await _get()
        assert body["calibration"]["career"]["count_honest"] is True

    @pytest.mark.asyncio
    async def test_the_three_states_are_three_DIFFERENT_payloads(
        self, served, store, triggers, memory
    ) -> None:
        """The discrimination leg, and it is deliberately not a count.

        Three inputs — nothing resolved, three resolved, ten resolved — must produce three
        pairwise-distinct calibration payloads. A count of states would pass while two of them
        serialized identically, which is precisely the bug: the view would render one sentence
        for two different truths and there would be no way to tell from the screen.
        """
        seen: list[str] = []
        _log(store, triggers)
        _, body = await _get()
        seen.append(json.dumps(body["calibration"], sort_keys=True))
        for _ in range(3):
            _resolve(store, triggers, memory, _log(store, triggers)["id"], "as_expected")
        _, body = await _get()
        seen.append(json.dumps(body["calibration"], sort_keys=True))
        for _ in range(CALIBRATION_MIN_N - 3):
            _resolve(store, triggers, memory, _log(store, triggers)["id"], "as_expected")
        _, body = await _get()
        seen.append(json.dumps(body["calibration"], sort_keys=True))
        assert len(set(seen)) == 3, f"two states serialize identically: {seen}"

    @pytest.mark.asyncio
    async def test_a_declined_verdict_is_not_scored_as_one(
        self, served, store, triggers, memory
    ) -> None:
        """``mixed`` is excluded from the calibration grades, so resolving everything as mixed
        leaves the strip empty rather than inventing the verdict the user declined to give."""
        for _ in range(CALIBRATION_MIN_N):
            _resolve(store, triggers, memory, _log(store, triggers)["id"], "mixed")
        _, body = await _get()
        assert body["calibration"] == {}
        assert len(body["decisions"]) == CALIBRATION_MIN_N


# ── failure reads as failure ─────────────────────────────────────────────────


class TestFailureIsNotEmptiness:
    @pytest.mark.asyncio
    async def test_an_unknown_status_is_a_422_carrying_the_vocabulary(self, served, store) -> None:
        status, body = await _get(status="nonsense")
        assert status == 422
        assert body["error"]["code"] == "invalid_request"
        # The message is composed ONCE, by `decisions`, so it names the accepted values.
        assert "pending" in body["error"]["message"]

    @pytest.mark.asyncio
    async def test_a_broken_store_is_an_error_never_an_empty_journal(self, monkeypatch) -> None:
        """ "You have never decided anything" is the most confident possible way to say the
        opposite of what is known. A read that raises must not be able to render as a journal."""
        import personalclaw.knowledge as knowledge_pkg

        def boom(*a, **k):
            raise RuntimeError("knowledge.db is locked")

        monkeypatch.setattr(knowledge_pkg, "get_knowledge_store", boom)
        status, body = await _get()
        assert status == 500
        assert body["error"]["code"] == "decision_journal_unreadable"
        assert "decisions" not in body

    @pytest.mark.asyncio
    async def test_every_failure_uses_the_structured_envelope(self, served, store) -> None:
        """`AGENTS.md` §"Shared conventions": the flat ``{"error": "<prose>"}`` shape is a
        ratcheted, shrinking population, so a new route emitting it would be a new site a client
        can only branch on by matching prose."""
        _, body = await _get(domain="not-a-domain")
        assert isinstance(body["error"], dict) and "code" in body["error"]


# ── criterion 5's grep audit: neither store writes the other ─────────────────


class TestStoresStayUncoupled:
    """The `done_when`'s "grep-audit confirms neither store writes the other" — a SOURCE audit,
    which is a different leg from ``test_decision_journal.py``'s behavioural one. That test
    proves the memory row it produced holds no back-pointer; this one proves no code path could
    write one, including paths no test happens to drive.
    """

    def test_the_journal_writes_memory_only_through_write_lesson(self) -> None:
        src = (SRC / "decisions.py").read_text()
        writes = [
            ln.strip()
            for ln in src.splitlines()
            if ".write_" in ln and not ln.strip().startswith(("#", "*", '"'))
        ]
        assert writes, "vacuity floor: the audit found no memory write calls at all"
        for ln in writes:
            assert "write_lesson" in ln, f"a second memory write path: {ln}"

    def test_the_memory_side_never_reaches_for_the_knowledge_store(self) -> None:
        for name in ("memory_service.py", "vector_memory.py"):
            src = (SRC / name).read_text()
            assert "get_knowledge_store" not in src, f"{name} reaches into the knowledge store"
            assert "from personalclaw.decisions" not in src, f"{name} imports the journal"
        # Vacuity floor: the files were actually read and are the real ones.
        assert "def write_lesson" in (SRC / "memory_service.py").read_text()
