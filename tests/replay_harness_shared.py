"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_replay_harness.py``

Its checks:
``tests/rails/test_replay_harness_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import asyncio
import json

from personalclaw.learning import proposals as proposals_mod
from personalclaw.learning import replay as replay_mod
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent


class ScriptedProvider:
    """A zero-cost provider that returns scripted text. Drives the REAL ``LLMJudge``.

    Deliberately not a mock of ``judge_turn``: the composition clause is about
    ``one_shot_completion`` + ``LLMJudge`` working together, so the judge under test is the
    shipped class, streaming through the shipped event protocol and running its own JSON
    parser on the result. A test that stubbed ``judge_turn`` would leave the parse-failure
    contract completely unexercised.
    """

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self._idx = 0
        self.prompts: list[str] = []

    async def start(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def stream(self, message: str):
        self.prompts.append(message)
        text = self._responses[self._idx] if self._idx < len(self._responses) else "{}"
        self._idx += 1
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=text)
        yield LLMEvent(kind=EVENT_COMPLETE)

    async def approve_tool(self, request_id):
        pass

    async def reject_tool(self, request_id):
        pass

    def context_usage_pct(self) -> float:
        return 0.0


def judge_scoring(*scores: float, reason: str = "ok"):
    """A real ``LLMJudge`` whose provider returns these scores in order.

    ``prompt_template`` is left at ``None`` so the judge renders through the ``eval_judge``
    prompt binding — the shipped path, not a test-local string.
    """
    from personalclaw.eval.judge import LLMJudge

    payloads = [json.dumps({"score": s, "reason": reason}) for s in scores]
    provider = ScriptedProvider(payloads)
    seen: list[str] = []

    def factory(session_key: str):
        seen.append(session_key)
        return provider

    judge = LLMJudge(factory)
    judge._test_session_keys = seen  # noqa: SLF001 — read by the binding test below
    judge._test_provider = provider  # noqa: SLF001
    return judge


def completion_returning(*texts: str):
    """A stand-in for ``one_shot_completion`` with the same signature and call shape."""
    calls: list[dict] = []

    async def _completion(prompt: str, *, use_case: str = "background", **kw):
        calls.append({"prompt": prompt, "use_case": use_case})
        idx = len(calls) - 1
        return texts[idx] if idx < len(texts) else "an answer"

    _completion.calls = calls  # type: ignore[attr-defined]
    return _completion


def a_case(session: str = "sess-a", rhash: str = "hash1", *, tool_free: bool = True):
    return replay_mod.ReplayCase(
        session_id=session,
        record_hash=rhash,
        prompt="<untrusted_content>\nrefactor the retry helper\n</untrusted_content>",
        tool_free=tool_free,
        captured_at=1.0,
    )


def a_proposal(pid: str = "skill-ea6test01", kind: str = "skill"):
    return proposals_mod.Proposal(
        id=pid,
        kind=kind,
        title="Promote the retry-helper checklist",
        body="When editing the retry helper, always widen the test first.",
        provenance="inferred",
        # `evidence_refs` is what `Row.bulk_acceptable` requires ("nothing to check" otherwise),
        # so the seam test can assert the measurement changed no eligibility.
        evidence_refs=["run-1"],
        change_manifest={"predicted_fixes": ["stops the flaky retry regression"]},
    )


def run(coro):
    return asyncio.run(coro)
