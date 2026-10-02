"""The escalation record is the ONLY account a failed run has (issue 565).

The frontend half of #565 (a panel that renders the record) rests on two facts about the
server, and neither is obvious from reading the handler:

1. **`GET /api/workflows/runs/{id}` returns `attention` for a terminal run.** `service.status`
   reads `run.attention` unconditionally, so the data was never missing from the wire — the
   client simply never read it. If that ever changes, the panel goes blank and only this test
   would say why.

2. **`error` names the step that failed and its cause, and `attention` keeps the rest.** A node
   that exhausts its retries makes the frontier's outcome FAILED, and the run goes terminal
   through the completion path, whose ending names the failed step and why
   (`ending_sentence.for_failures`): "“i” failed: ConnectionError: network down." That field
   used to be empty on exactly these runs — the completion path's terminal write took no
   `error` — so the run page's one explanation slot (`run.error && <p>`) rendered a failed run
   with a blank reason. The escalation on `attention` still holds what the sentence does not:
   the reason, the per-attempt evidence and the engine's fix instruction.

Measured here rather than asserted from reading. The cause now appears twice on the wire, in the
sentence and as the escalation's `detail`, and the panel's detail-deduplication
(`EscalationPanel`'s `runError` prop) is what keeps it from appearing twice on the page.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun
from personalclaw.workflows.resilience import ESCALATION_OPTIONS

pytestmark = pytest.mark.anyio

#: Resolved from THIS file, the way every other source-reading test here does it — the
#: package's own `__file__` points into site-packages under a non-editable install, where
#: `web/` does not exist.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "personalclaw"
_META = _REPO_ROOT / "web" / "src" / "pages" / "workflows" / "attentionMeta.ts"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


async def _run_that_escalates(max_attempts: int = 2) -> WorkflowRun:
    """A run whose only node fails every attempt, so the retry budget is spent."""

    async def always_down(prompt, *, use_case="background", output_type=None):
        raise ConnectionError("network down")

    spec = {
        "name": "esc",
        "root": {
            "kind": "infer",
            "id": "i",
            "config": {"prompt": "go", "retry": {"max_attempts": max_attempts}},
        },
    }
    run = store.create(WorkflowRun(id="", workflow_name="esc"))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices(completion=always_down))
    status = await controller.run_to_completion(timeout=25)
    assert status == RunStatus.FAILED, f"the run did not fail: {status}"
    return controller.run


async def test_the_run_names_the_failed_step_and_attention_keeps_the_whole_account():
    """🔑 Both halves in one place: the sentence the run page renders, and the record under it."""
    run = await _run_that_escalates()
    payload = service.status(run.id)

    # Half one: the field the run page renders names the step and its cause. It was empty.
    attention = payload["attention"]
    assert payload["error"] == f"“i” failed: {attention['detail']}.", payload["error"]

    # Half two: the whole account, on a field the client received and discarded.
    assert attention["kind"] == "escalation"
    assert attention["reason"] == "retries_exhausted"
    assert attention["detail"], "the cause is what makes the reason actionable"
    assert [a["attempt"] for a in attention["attempts"]] == [1, 2]
    assert all(
        a["fix_instruction"] for a in attention["attempts"]
    ), "the engine's derived next move is the single most actionable field in the record"


async def test_a_terminal_run_still_serves_the_record():
    """The panel renders on a TERMINAL run — that is the whole point — so a filter added here
    later (mirroring the one the frontend fold used to apply) would empty it silently."""
    run = await _run_that_escalates()
    assert run.status == RunStatus.FAILED
    assert service.status(run.id)["attention"] is not None


async def test_the_reason_vocabulary_is_what_the_frontend_maps():
    """The TS `ESCALATION_REASON` map is keyed on these tokens, and a token with no sentence
    falls through to the raw word. Pinned here so the two `_escalate` call sites cannot add a
    reason the panel has never heard of without something going red.

    Derived from the source rather than restated: `resilience.check_breaker`'s verdicts plus
    `loop/tick.py`'s convergence reasons are the two producers, and both are one edit away
    from growing.

    The controller is a THIRD producer that this scan cannot parse — it spells its tokens as plain
    `reason=` keyword arguments to `_escalate`/`loop_convergence.surface_loop` rather than in either
    producer's shape — so its tokens are listed literally below. That is a gap in the scan, not a
    second vocabulary: a token added there is invisible here, which is exactly how
    `iterations_failed` could have shipped without a sentence. Listed rather than parsed because a
    `reason="…"` regex over the controller also matches the tokens it merely FORWARDS from the other
    two producers, so it would report every breaker verdict as a controller token and prove nothing
    about either.
    """
    resilience_src = (_SRC / "workflows" / "resilience.py").read_text()
    tick_src = (_SRC / "loop" / "tick.py").read_text()
    meta = _META.read_text()

    breaker = set(re.findall(r'BreakerVerdict\(\s*True,\s*"([a-z_]+)"', resilience_src))
    assert len(breaker) >= 4, f"the breaker verdicts moved: {breaker}"
    convergence = set(re.findall(r'reason="([a-z_]+)"', tick_src))
    assert convergence, "no convergence reasons parsed — loop/tick.py moved"

    #: The controller's own tokens — see this test's docstring on why they are listed, not parsed.
    #: Each is asserted to really be in the controller's source (`controller.py` and its
    #: responsibility modules), so a rename there reds here instead of leaving a sentence for a
    #: token nothing produces.
    from tests.controller_sources import controller_source

    controller_src = controller_source()
    controller = {"retries_exhausted", "not_retried", "iterations_failed", "judge_escalated"}
    for token in sorted(controller):
        assert f'"{token}"' in controller_src, (
            f"{token!r} is listed here as a controller token but the controller no longer spells "
            "it — either it was renamed (update this set) or it is gone (drop it and its sentence)"
        )

    for reason in sorted(breaker | convergence | controller):
        assert f"{reason}:" in meta, (
            f"{reason!r} can reach an escalation artifact but attentionMeta.ts has no sentence "
            "for it — the panel would print the raw token"
        )


async def test_the_ledger_keeps_the_record_too():
    """`attention` is the live copy and the ledger is the durable one. Both, deliberately:
    `attention` is overwritten by the next gate, and a later reader (the flywheel, a bug
    report) needs the escalation after the run is long over."""
    run = await _run_that_escalates()
    rows = [r for r in J.ledger(run.id) if r["kind"] == J.STEP_ESCALATED]
    assert len(rows) == 1
    assert rows[0]["reason"] == "retries_exhausted"
    assert list(rows[0]["options"]) == list(ESCALATION_OPTIONS)
