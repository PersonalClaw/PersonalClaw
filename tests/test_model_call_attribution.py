"""ACP-AGENT-PARITY `G47` — a model call is attributable to its caller, and a dead pass says so.

Two halves of one defect, each tested against the property and not against the mechanism:

1. **Telling callers apart.** ``model_calls.jsonl`` and ``/api/models/telemetry`` key on
   ``(use_case, query_class)``, and FOUR unattended subsystems share the ``background`` axis —
   so an attempt row named the axis and never the asker. These tests drive the REAL guard
   (``ModelCallGuard`` → ``record_attempt`` → the JSONL) under two different callers and assert
   the two populations are *separable on a read surface*. Asserting "the field exists" would
   pass with a writer that stamps a constant.
2. **Legibility of a pass that produced nothing.** The measured case: a skill-ladder pass that
   died as ``provider_error`` at 60,010 ms was logged at DEBUG, and the common
   ``action == "none"`` exit logged nothing at all. So these tests assert the pass is visible
   **at the level a default install actually shows** — read from ``AgentConfig.log_level``'s
   default rather than hardcoded, because that default is ``WARNING`` and an INFO-only fix
   would have shipped inert.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from personalclaw import after_turn_review as atr
from personalclaw.guardrails.audit import (
    CALLERS,
    UNATTRIBUTED,
    AttemptRecord,
    caller_scope,
    current_caller,
    read_recent,
    record_attempt,
    set_current_caller,
)
from personalclaw.guardrails.health import provider_health
from personalclaw.guardrails.model_call import ModelCallGuard
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from tests.test_guardrails_model_call import FakeProvider as _BaseFake

_SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"


class FakeProvider(_BaseFake):
    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=self._text)
        yield LLMEvent(
            kind=EVENT_COMPLETE, input_tokens=self._tokens[0], output_tokens=self._tokens[1]
        )


class BrokenProvider(_BaseFake):
    """A provider whose stream dies — the shape of the measured 60,010 ms ladder timeout."""

    async def stream(self, prompt: str):
        raise RuntimeError("provider timeout")
        yield  # pragma: no cover - makes this an async generator


async def _drain(agen):
    out = ""
    async for ev in agen:
        if ev.kind == EVENT_TEXT_CHUNK:
            out += ev.text
        elif ev.kind == EVENT_COMPLETE:
            break
    await agen.aclose()
    return out


async def _one_call(caller: str, *, provider=None) -> None:
    """One guarded model call attributed to ``caller`` (or unattributed when ``caller`` is "")."""
    guard = ModelCallGuard(
        provider or FakeProvider(), use_case="background", provider_name="P", model="m"
    )
    await guard.start()
    with caller_scope(caller):
        try:
            await _drain(guard.stream("summarize this"))
        except Exception:
            pass  # a failed attempt is still an audited attempt — that is the point


def _callers_by_name(payload: dict) -> dict[str, dict]:
    return {row["name"]: row for row in payload["callers"]}


class TestCallersAreToldApart:
    """The property: one caller's calls can be distinguished from another's on a read surface."""

    @pytest.mark.asyncio
    async def test_two_background_callers_are_separable(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        await _one_call("skill_ladder")
        await _one_call("inbox_triage")
        await _one_call("inbox_triage")

        rows = read_recent()
        assert len(rows) == 3
        # Same use_case on every row — which is exactly why the axis could not answer this.
        assert {r["use_case"] for r in rows} == {"background"}

        by_caller = _callers_by_name(provider_health())
        assert by_caller["inbox_triage"]["calls"] == 2
        assert by_caller["skill_ladder"]["calls"] == 1
        assert "conflict_merge" not in by_caller  # a caller that made no call invents no row

    @pytest.mark.asyncio
    async def test_a_dead_caller_is_visible_where_the_provider_aggregate_hides_it(
        self, tmp_path, monkeypatch
    ):
        """The whole point of the field: one subsystem at 0% inside a healthy provider.

        This is the measured production shape — a learning pass failing every time while the
        provider it uses is fine — and it is the case the per-provider rollup structurally
        cannot report, because it averages the two callers together.
        """
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        await _one_call("inbox_triage")
        await _one_call("skill_ladder", provider=BrokenProvider())

        payload = provider_health()
        provider_row = next(p for p in payload["providers"] if p["name"] == "P")
        assert provider_row["pass_rate"] == 0.5, "the provider view averages the two callers"

        by_caller = _callers_by_name(payload)
        assert by_caller["inbox_triage"]["pass_rate"] == 1.0
        assert by_caller["skill_ladder"]["pass_rate"] == 0.0
        assert by_caller["skill_ladder"]["failed"] == 1
        assert by_caller["skill_ladder"]["failure_modes"], "a dead caller names WHY it is dead"

    @pytest.mark.asyncio
    async def test_an_unbound_call_reads_as_unattributed_not_as_someone_else(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        await _one_call("")
        by_caller = _callers_by_name(provider_health())
        assert list(by_caller) == [UNATTRIBUTED]
        assert by_caller[UNATTRIBUTED]["calls"] == 1


class TestTheVocabularyIsClosed:
    def test_an_unknown_caller_is_refused_at_the_binding_seam(self):
        with pytest.raises(ValueError, match="unknown model-call caller"):
            set_current_caller("skill-ladder")  # the hyphenated near-miss spelling
        with pytest.raises(ValueError):
            with caller_scope("learning"):  # plausible, and not in the vocabulary
                pass
        assert current_caller() == "", "a refused bind must not leave a caller bound"

    def test_clearing_a_binding_is_not_a_typo(self):
        with caller_scope("skill_ladder"):
            with caller_scope(""):
                assert current_caller() == ""
            assert current_caller() == "skill_ladder", "a nested scope restores its parent"

    def test_an_unknown_caller_is_never_written_to_the_ledger(self, tmp_path, monkeypatch, caplog):
        """A record built by hand cannot smuggle a fifth spelling into the file either."""
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        with caplog.at_level(logging.WARNING, logger="personalclaw.guardrails.audit"):
            record_attempt(
                AttemptRecord(
                    audit_id="a1",
                    ts=0.0,
                    use_case="background",
                    provider="P",
                    model="m",
                    attempt=1,
                    caller="skillLadder",
                )
            )
        assert read_recent()[0]["caller"] == "", "an unknown caller is dropped, not stored"
        assert any("unknown caller" in r.message for r in caplog.records)

    def test_every_member_of_the_vocabulary_has_a_production_binder(self):
        """No declared-but-unbound member. A vocabulary entry nothing binds is an inert
        surface, and this repo's whole inert-surface census exists because that keeps
        happening. Readers in ``tests/`` deliberately do not count."""
        found: set[str] = set()
        for path in _SRC.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for name in CALLERS:
                if f'caller_scope("{name}")' in text:
                    found.add(name)
        assert found, "vacuity guard: the scan matched nothing at all"
        assert found == set(CALLERS), f"unbound caller(s): {sorted(set(CALLERS) - found)}"


def _shipped_default_level() -> int:
    """The log level a DEFAULT install shows, read from config rather than assumed.

    ``AgentConfig.log_level`` defaults to ``"WARNING"`` and ``cli.py`` applies it whenever
    ``--verbose`` is absent. Read here so this rail keeps meaning "visible as shipped" if that
    default ever changes — and so it reds today for an INFO-only line, which is what `G47`
    literally asked for and would have been inert.
    """
    from personalclaw.config.loader import AgentConfig

    return getattr(logging, str(AgentConfig.__dataclass_fields__["log_level"].default))


class TestADeadLadderPassIsLegible:
    LOGGER = "personalclaw.after_turn_review"

    @pytest.mark.asyncio
    async def test_a_pass_that_died_is_visible_at_the_shipped_default_level(self, caplog):
        async def boom(_prompt: str) -> str:
            raise TimeoutError("provider timeout after 60010 ms")

        with caplog.at_level(_shipped_default_level(), logger=self.LOGGER):
            out = await atr.run_skill_ladder_review(
                session_key="s1",
                user_message="do the thing",
                assistant_text="did the thing",
                loaded_skills=[],
                completion=boom,
            )
        assert out is None
        records = [r for r in caplog.records if r.name == self.LOGGER]
        assert len(records) == 1, f"exactly one line per pass, got {[r.message for r in records]}"
        assert records[0].levelno >= _shipped_default_level()
        text = records[0].getMessage()
        assert "provider_error" in text and "TimeoutError" in text
        assert "skill-ladder review" in text and "ms" in text

    @pytest.mark.asyncio
    async def test_a_pass_that_decided_nothing_is_still_logged(self, caplog):
        """The common exit. It logged NOTHING before, so a live ladder and a ladder that was
        never scheduled were the same observation."""

        async def none_action(_prompt: str) -> str:
            return '{"action": "none"}'

        with caplog.at_level(logging.INFO, logger=self.LOGGER):
            await atr.run_skill_ladder_review(
                session_key="s2",
                user_message="do the thing",
                assistant_text="did the thing",
                loaded_skills=[],
                completion=none_action,
            )
        records = [r for r in caplog.records if r.name == self.LOGGER]
        assert len(records) == 1
        assert "no_action" in records[0].getMessage()
        # …and a healthy pass stays quiet on a default install: this is the spam bound.
        assert records[0].levelno < _shipped_default_level()

    @pytest.mark.asyncio
    async def test_garbage_from_the_model_is_a_failed_pass_not_a_silent_one(self, caplog):
        async def garbage(_prompt: str) -> str:
            return "I would love to help with that!"

        with caplog.at_level(_shipped_default_level(), logger=self.LOGGER):
            await atr.run_skill_ladder_review(
                session_key="s3",
                user_message="do the thing",
                assistant_text="did the thing",
                loaded_skills=[],
                completion=garbage,
            )
        records = [r for r in caplog.records if r.name == self.LOGGER]
        assert len(records) == 1 and "unparsable" in records[0].getMessage()

    @pytest.mark.asyncio
    async def test_the_ladders_model_call_is_attributed(self):
        """The ladder's own attempt rows carry its name — the other half of `G47`."""
        seen: list[str] = []

        async def spy(_prompt: str) -> str:
            seen.append(current_caller())
            return '{"action": "none"}'

        await atr.run_skill_ladder_review(
            session_key="s4",
            user_message="do the thing",
            assistant_text="did the thing",
            loaded_skills=[],
            completion=spy,
        )
        assert seen == ["skill_ladder"]
        assert current_caller() == "", "the scope is released when the pass ends"


class TestEveryLadderExitNamesAMappedVerdict:
    """The map and the returns cannot drift apart.

    An unmapped verdict logs at WARNING (loud by design), so drift here would not go silent —
    but it WOULD make a normal outcome shout, and the fix for a noisy alarm is usually to
    lower the alarm. Pinning the two together keeps that pressure off.
    """

    def test_every_returned_verdict_is_mapped_to_a_level(self):
        import ast

        source = (_SRC / "after_turn_review.py").read_text(encoding="utf-8")
        fn = next(
            n
            for n in ast.walk(ast.parse(source))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "_ladder_pass"
        )
        returned: set[str] = set()
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple)):
                continue
            first = node.value.elts[0] if node.value.elts else None
            if isinstance(first, ast.Constant):
                returned.add(str(first.value))
            elif isinstance(first, ast.IfExp):  # the template branch picks between two
                for branch in (first.body, first.orelse):
                    if isinstance(branch, ast.Constant):
                        returned.add(str(branch.value))
        assert len(returned) >= 8, f"vacuity guard: only found {sorted(returned)}"
        assert not returned - set(
            atr._LADDER_VERDICT_LEVEL
        ), f"unmapped verdict(s): {sorted(returned - set(atr._LADDER_VERDICT_LEVEL))}"
        # The one key the pass never returns: the wrapper's initial value, used when the pass
        # raises before naming anything. Pinned so it is not "cleaned up" as dead.
        assert set(atr._LADDER_VERDICT_LEVEL) - returned == {"internal_error"}
