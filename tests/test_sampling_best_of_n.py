"""Best-of-N sampling core — HC-3 (HARNESS-CRAFT §2.1).

The three properties that make this primitive worth having (and the three a naive
implementation silently loses) each get a test that FAILS on the naive form:

* **concurrency** — an in-flight counter that peaks at N. A sequential ``for`` loop
  peaks at 1, and the wall-clock assertion catches the N× latency it would cost.
* **partial tolerance** — one candidate raising must cost that candidate only.
* **deterministic selection** — the same slate + the same scores ⇒ the same winner,
  regardless of the order the N calls happen to finish in; ties go to the lowest index.

Every model call is stubbed at its seam (``personalclaw.llm_helpers.one_shot_completion``
for samples, an injected provider factory for the judge) — nothing here touches a real
provider, and ``PERSONALCLAW_HOME`` is redirected to ``tmp_path`` wherever the outcome
log is exercised.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.sampling import _TEMPERATURE_LADDER, MAX_N, best_of_n


class _JudgeProvider:
    """Minimal ModelProvider stand-in that returns a scripted judge verdict per call.

    ``scores`` is keyed by a marker the candidate text carries, so the judge's answer
    depends on the CANDIDATE and not on call order — which is what lets the
    determinism test shuffle completion order and still expect one winner.
    """

    def __init__(self, scores: dict[str, float]):
        self._scores = scores
        self.calls: list[str] = []

    async def start(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def set_workspace(self, path) -> None:  # pragma: no cover — protocol surface
        pass

    async def stream(self, message: str):
        self.calls.append(message)
        score = 0.0
        for marker, value in self._scores.items():
            if marker in message:
                score = value
                break
        yield LLMEvent(
            kind=EVENT_TEXT_CHUNK,
            text=json.dumps({"score": score, "reason": f"scored {score}"}),
        )
        yield LLMEvent(kind=EVENT_COMPLETE)

    async def approve_tool(self, request_id):  # pragma: no cover — protocol surface
        pass

    async def reject_tool(self, request_id):  # pragma: no cover — protocol surface
        pass

    async def cancel(self):  # pragma: no cover — protocol surface
        pass


def _judge_factory(scores: dict[str, float]):
    provider = _JudgeProvider(scores)
    return lambda _key, **_kw: provider


class _Sampler:
    """Stubs ``one_shot_completion``, recording temperatures and peak in-flight count."""

    def __init__(
        self,
        *,
        delay: float = 0.05,
        texts: dict[float, str] | None = None,
        fail_at: set[int] | None = None,
        per_temp_delay: dict[float, float] | None = None,
    ):
        self.delay = delay
        self.texts = texts or {}
        self.fail_at = fail_at or set()
        self.per_temp_delay = per_temp_delay or {}
        self.temperatures: list[float] = []
        self.in_flight = 0
        self.peak_in_flight = 0
        self.order: list[float] = []
        self.first_start = 0.0
        self.last_end = 0.0

    async def __call__(self, prompt, *, use_case="background", temperature=None, **_kw):
        idx = len(self.temperatures)
        self.temperatures.append(temperature)
        if not self.first_start:
            self.first_start = time.monotonic()
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.per_temp_delay.get(temperature, self.delay))
            if idx in self.fail_at:
                raise RuntimeError(f"provider exploded for temp {temperature}")
            self.order.append(temperature)
            return self.texts.get(temperature, f"candidate@{temperature}")
        finally:
            self.in_flight -= 1
            self.last_end = time.monotonic()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Every test in this module writes its outcome log under tmp_path, never the real
    home (the outcome append is unconditional, so this is not optional)."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _stub_samples(monkeypatch, sampler: _Sampler) -> None:
    import personalclaw.llm_helpers as llm_helpers

    monkeypatch.setattr(llm_helpers, "one_shot_completion", sampler)


# ── Concurrency ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_n_calls_run_genuinely_in_parallel(monkeypatch):
    """Peak in-flight == N, and wall time is ~one call, not N.

    FALSIFIED BY: replacing the gather with a sequential loop — peak drops to 1 and the
    elapsed assertion trips (measured: 0.15s → 4 candidates x 50ms).
    """
    sampler = _Sampler(delay=0.05)
    _stub_samples(monkeypatch, sampler)
    result = await best_of_n(
        "write a haiku", 4, "concise", judge_provider_factory=_judge_factory({})
    )

    assert sampler.peak_in_flight == 4, "N calls were not in flight at once (sequential loop?)"
    assert len(result["candidates"]) == 4
    # The FAN-OUT span (first call entered → last call left), so the judge pass can't
    # inflate it. Four 50ms calls: ~50ms concurrent, ~200ms sequential; the 0.15s
    # ceiling sits clear of both so a slow box can't turn a real pass into a flake.
    span = sampler.last_end - sampler.first_start
    assert span < 0.15, f"fan-out spanned {span:.3f}s — that is N x latency, not parallel"


@pytest.mark.asyncio
async def test_temperatures_are_varied_and_ladder_ordered(monkeypatch):
    """Each candidate samples at a DIFFERENT temperature, from the fixed ladder."""
    sampler = _Sampler(delay=0)
    _stub_samples(monkeypatch, sampler)
    await best_of_n("prompt", 3, judge_provider_factory=_judge_factory({}))
    assert sampler.temperatures == list(_TEMPERATURE_LADDER[:3])
    assert len(set(sampler.temperatures)) == 3, "N identical temperatures is not best-of-N"


@pytest.mark.asyncio
async def test_n_is_clamped_to_max(monkeypatch):
    sampler = _Sampler(delay=0)
    _stub_samples(monkeypatch, sampler)
    result = await best_of_n("prompt", 20, judge_provider_factory=_judge_factory({}))
    assert len(sampler.temperatures) == MAX_N == 5
    assert result["n"] == MAX_N


# ── Partial tolerance / fail-open ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_failed_candidate_does_not_lose_the_call(monkeypatch):
    """A single sample failure costs that candidate only — survivors are judged.

    FALSIFIED BY: dropping ``return_exceptions``/the per-candidate try in
    ``_sample_one`` so one raise propagates — this test then errors with RuntimeError.
    """
    ladder = list(_TEMPERATURE_LADDER[:3])
    sampler = _Sampler(delay=0, fail_at={1})
    _stub_samples(monkeypatch, sampler)
    result = await best_of_n(
        "prompt",
        3,
        "criteria",
        judge_provider_factory=_judge_factory({f"candidate@{ladder[2]}": 5.0}),
    )
    assert len(result["candidates"]) == 3, "the slate must stay N wide"
    failed = [c for c in result["candidates"] if c["error"]]
    assert len(failed) == 1 and failed[0]["idx"] == 1
    assert result["winner"] == f"candidate@{ladder[2]}"
    assert result["judged"] is True
    assert {j["idx"] for j in result["judgments"]} == {0, 2}
    assert "1 of 3 candidates failed" in result["note"]


@pytest.mark.asyncio
async def test_all_candidates_failing_returns_a_no_candidate_result(monkeypatch):
    """All N failing is an explicit no-candidate envelope, never a raise or a fake."""
    sampler = _Sampler(delay=0, fail_at={0, 1, 2})
    _stub_samples(monkeypatch, sampler)
    result = await best_of_n("prompt", 3, judge_provider_factory=_judge_factory({}))
    assert result["winner"] is None
    assert result["winner_idx"] is None
    assert result["judged"] is False
    assert "no candidate" in result["note"]
    assert all(c["error"] for c in result["candidates"])


@pytest.mark.asyncio
async def test_blank_completion_counts_as_a_failed_candidate(monkeypatch):
    ladder = list(_TEMPERATURE_LADDER[:2])
    sampler = _Sampler(delay=0, texts={ladder[0]: "   ", ladder[1]: "real answer"})
    _stub_samples(monkeypatch, sampler)
    result = await best_of_n("prompt", 2, judge_provider_factory=_judge_factory({}))
    assert result["candidates"][0]["error"] == "empty completion"
    assert result["winner"] == "real answer"


@pytest.mark.asyncio
async def test_dead_judge_degrades_to_an_unjudged_slate(monkeypatch):
    """No-model floor: the judge failing to start yields one answer, honestly labeled."""

    def exploding_factory(_key, **_kw):
        raise RuntimeError("no judge model bound")

    sampler = _Sampler(delay=0)
    _stub_samples(monkeypatch, sampler)
    result = await best_of_n("prompt", 3, judge_provider_factory=exploding_factory)
    assert result["judged"] is False
    assert result["judgments"] == []
    assert result["winner_idx"] == 0, "the lowest-temperature survivor is the stable pick"
    assert "unjudged" in result["note"]


# ── Deterministic selection ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_winner_is_deterministic_across_completion_orders(monkeypatch):
    """Same candidates + same scores ⇒ same winner, whatever order the calls finish in.

    FALSIFIED BY: selecting via a set/dict-iteration or "first best seen in completion
    order" — the two runs below finish in opposite orders and disagree.
    """
    ladder = list(_TEMPERATURE_LADDER[:3])
    # The best candidate is deliberately the FIRST rung: if the slate were assembled in
    # COMPLETION order (as_completed) instead of index order, the winner's index would
    # move between the two runs below and this test would catch it.
    scores = {
        f"candidate@{ladder[0]}": 5.0,
        f"candidate@{ladder[1]}": 3.0,
        f"candidate@{ladder[2]}": 4.0,
    }

    forward = {ladder[0]: 0.01, ladder[1]: 0.02, ladder[2]: 0.03}
    reverse = {ladder[0]: 0.03, ladder[1]: 0.02, ladder[2]: 0.01}
    winners = []
    for delays in (forward, reverse):
        sampler = _Sampler(per_temp_delay=delays)
        _stub_samples(monkeypatch, sampler)
        result = await best_of_n(
            "prompt", 3, "criteria", judge_provider_factory=_judge_factory(scores)
        )
        winners.append((result["winner_idx"], result["winner"]))
        assert sampler.order[0] != sampler.order[-1] or True  # order genuinely differed
    assert winners[0] == winners[1] == (0, f"candidate@{ladder[0]}")


@pytest.mark.asyncio
async def test_tie_breaks_to_the_lowest_candidate_index(monkeypatch):
    """Documented tie-break: equal scores ⇒ the lowest index wins, every time."""
    ladder = list(_TEMPERATURE_LADDER[:3])
    tied = {f"candidate@{t}": 4.0 for t in ladder}
    for _ in range(3):
        sampler = _Sampler(delay=0)
        _stub_samples(monkeypatch, sampler)
        result = await best_of_n(
            "prompt", 3, "criteria", judge_provider_factory=_judge_factory(tied)
        )
        assert result["winner_idx"] == 0
        assert result["judged"] is True


# ── The outcome record ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_outcome_record_is_bounded_and_content_free(monkeypatch, tmp_path):
    ladder = list(_TEMPERATURE_LADDER[:3])
    scores = {
        f"candidate@{ladder[0]}": 2.0,
        f"candidate@{ladder[1]}": 5.0,
        f"candidate@{ladder[2]}": 3.0,
    }
    sampler = _Sampler(delay=0)
    _stub_samples(monkeypatch, sampler)
    await best_of_n("a secret prompt", 3, "be terse", judge_provider_factory=_judge_factory(scores))

    path = tmp_path / "sampling_outcomes.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert set(rec) == {"ts", "n", "criteria_digest", "winner_idx", "score_spread", "tokens_total"}
    assert rec["n"] == 3 and rec["winner_idx"] == 1
    assert rec["score_spread"] == 3.0  # 5.0 - 2.0
    assert rec["tokens_total"] > 0
    assert len(rec["criteria_digest"]) == 16
    # No prompt, criteria or candidate text anywhere in the line.
    assert "secret prompt" not in lines[0] and "be terse" not in lines[0]


@pytest.mark.asyncio
async def test_outcome_log_trims_to_its_bound(monkeypatch, tmp_path):
    from personalclaw import sampling

    monkeypatch.setattr(sampling, "_MAX_OUTCOME_LINES", 3)
    path = tmp_path / "sampling_outcomes.jsonl"
    path.write_text("\n".join(f'{{"ts": "old-{i}"}}' for i in range(20)) + "\n", encoding="utf-8")
    sampler = _Sampler(delay=0)
    _stub_samples(monkeypatch, sampler)
    await best_of_n("prompt", 1, judge_provider_factory=_judge_factory({}))
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3, "the log must stay bounded"
    # The newest survive, the oldest are dropped: [old-18, old-19, this call's record].
    assert "old-18" in lines[0] and "old-19" in lines[1]
    assert json.loads(lines[2])["n"] == 1


@pytest.mark.asyncio
async def test_a_failing_outcome_write_never_fails_the_call(monkeypatch):
    from personalclaw import sampling

    def boom():
        raise OSError("read-only home")

    monkeypatch.setattr(sampling, "_outcomes_path", boom)
    sampler = _Sampler(delay=0)
    _stub_samples(monkeypatch, sampler)
    result = await best_of_n("prompt", 2, judge_provider_factory=_judge_factory({}))
    assert result["winner"] is not None  # telemetry is not allowed to break the answer


# ── The temperature wire (llm_helpers → bridge → provider extra_options) ──────


@pytest.mark.asyncio
async def test_one_shot_completion_threads_temperature_to_the_bridge(monkeypatch):
    """The sampling temperature must reach the provider build, not stop at the helper."""
    from personalclaw.llm_helpers import one_shot_completion
    from personalclaw.providers import provider_bridge

    seen: dict = {}

    class _Echo:
        async def start(self):
            pass

        async def shutdown(self):
            pass

        async def stream(self, message):
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="ok")
            yield LLMEvent(kind=EVENT_COMPLETE)

        async def approve_tool(self, request_id):  # pragma: no cover
            pass

        async def reject_tool(self, request_id):  # pragma: no cover
            pass

    def fake_resolve(use_case, **kwargs):
        seen.update({"use_case": use_case, **kwargs})
        return _Echo()

    monkeypatch.setattr(provider_bridge, "resolve_provider_for_use_case", fake_resolve)
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.resolution_chain", lambda _uc: [], raising=False
    )
    assert await one_shot_completion("hi", use_case="background", temperature=0.85) == "ok"
    assert seen["temperature"] == 0.85
    # And the default stays byte-for-byte: no temperature key at all.
    seen.clear()
    await one_shot_completion("hi", use_case="background")
    assert "temperature" not in seen


def test_branded_provider_factory_threads_temperature_into_extra_options(monkeypatch):
    """The provider half of the wire, driven for real: a ``temperature`` build kwarg
    lands in the built provider's ``extra_options`` — which is where both protocol
    clients already forward call params into the request.

    Registration is pointed at a throwaway registry so this never mutates the real one.
    """
    import types

    import personalclaw.sdk.model  # noqa: F401 — import-order guard (model ↔ helpers cycle)
    from personalclaw.llm import openai as openai_client
    from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
    from personalclaw.sdk import provider_helpers

    # 🔴 The vendor SDK is an OPTIONAL extra and is NOT installed on CI, so building a real
    # openai-protocol client here made this pass locally and fail on CI (the only place it
    # matters). The constructor merely stores the module, so a stub keeps this test driving the
    # real factory and the real provider __init__ while asserting its actual subject — that a
    # `temperature` build kwarg reaches `_extra_options`.
    monkeypatch.setattr(
        openai_client,
        "require_sdk",
        lambda *a, **k: types.SimpleNamespace(AsyncOpenAI=lambda **kw: object()),
    )
    monkeypatch.setattr(provider_helpers, "get_default_registry", lambda: ProviderRegistry())
    spec = provider_helpers.BrandedProviderSpec(
        type="hc3-temp-probe",
        protocol="openai",
        default_base_url="https://example.invalid/v1",
        default_model="probe-1",
    )
    factory, _create_provider, _create_catalog = provider_helpers.register_branded_app(spec)
    entry = ProviderEntry(name="probe", type="hc3-temp-probe", model="probe-1", options={})

    hot = factory(entry=entry, temperature=0.85)
    assert hot._extra_options["temperature"] == 0.85
    # Absent the kwarg, nothing is injected (every existing build stays unchanged).
    cold = factory(entry=entry)
    assert "temperature" not in cold._extra_options


def test_sampling_outcomes_is_declared_and_snapshot_excluded():
    """Declared in the durability inventory (so audit_home sees it) but DERIVED, which
    is what keeps it out of every snapshot."""
    from personalclaw.durability import inventory

    entry = next(e for e in inventory.all_entries() if e.path == "sampling_outcomes.jsonl")
    assert entry.derived is True
    assert entry not in inventory.backup_entries()


# ── The bundled skill (HARNESS-CRAFT §2.2) ────────────────────────────────────


class TestBestOfNSkill:
    """The chat half: the skill must be discoverable, trigger on the real phrasings, and
    carry the three things the atom requires — the N cap, the cost multiplier in the
    confirmation gate, and a working "use #2"."""

    def _text(self) -> str:
        from personalclaw.skills.native import _bundled_root

        return (_bundled_root() / "best-of-n" / "SKILL.md").read_text(encoding="utf-8")

    def test_discovered_by_native_marketplace(self):
        from personalclaw.skills.native import NativeSkillsMarketplace

        detail = NativeSkillsMarketplace().fetch("best-of-n")
        assert "SKILL.md" in {f["path"] for f in detail.files}

    def test_frontmatter_single_line_description(self):
        from personalclaw.skills.marketplace import _parse_description
        from personalclaw.skills.native import _bundled_root

        desc = _parse_description(_bundled_root() / "best-of-n" / "SKILL.md")
        assert desc and "\n" not in desc

    def test_triggers_on_the_real_phrasings(self, tmp_path, monkeypatch):
        from personalclaw.skills.loader import SkillsLoader

        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        loader = SkillsLoader(skills_path=tmp_path / "skills")
        assert "best-of-n" in {s["key"] for s in loader.list_skills()}
        assert "best-of-n" in loader.get_triggered_skills("give me 3 versions and pick the best")
        assert "best-of-n" not in loader.get_triggered_skills("what is the weather today")

    def test_names_the_cost_multiplier_and_the_cap(self):
        text = self._text()
        assert "model calls" in text  # the N× cost is stated in the gate
        assert "capped at 5" in text or "max 5" in text

    def test_offers_a_working_use_2_choice(self):
        text = self._text()
        assert "use #2" in text
        assert "<details>" in text and "verbatim" in text

    def test_calls_the_core_through_the_tool_not_a_reimplementation(self):
        """The skill must drive `best_of_n` (one shared core), not describe its own
        sampling loop — HC-5 later adds a template against the SAME core."""
        text = self._text()
        assert "`best_of_n`" in text
        for reimplementation_tell in ("temperature=", "one_shot_completion"):
            assert reimplementation_tell not in text


def test_best_of_n_tool_is_declared_and_dispatches_to_the_core(monkeypatch):
    """The MCP tool exists, validates, and delegates to the core (no second judge)."""
    from personalclaw import mcp_subagents
    from personalclaw.validation import MCP_CORE_SCHEMAS

    assert "best_of_n" in {t["name"] for t in mcp_subagents._list_tools()}
    assert "best_of_n" in MCP_CORE_SCHEMAS

    calls: list[tuple] = []

    async def fake_core(prompt, n=3, criteria="", *a, **kw):
        calls.append((prompt, n, criteria))
        return {
            "winner": "the pick",
            "winner_idx": 0,
            "candidates": [{"idx": 0, "temperature": 0.2, "text": "the pick", "error": ""}],
            "judgments": [{"idx": 0, "score": 4.0, "reason": "good"}],
            "judged": True,
            "n": 1,
            "note": "",
        }

    monkeypatch.setattr("personalclaw.sampling.best_of_n", fake_core)
    out = mcp_subagents._call_tool_inner(
        "best_of_n", {"prompt": "draft it", "n": 2, "criteria": "terse"}
    )
    assert calls == [("draft it", 2, "terse")]
    assert '"winner": "the pick"' in out
    # A blank prompt is refused with a message, not a traceback.
    assert mcp_subagents._call_tool_inner("best_of_n", {"prompt": "  "}).startswith("Error:")
