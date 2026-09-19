"""#3166 — the run ledger must persist the prompt the PROVIDER received, not the one composed.

**The defect.** The outbound secret/PII scan substitutes at the model-call seam
(``ModelCallGuard._prescan``), which returns the cleaned text *to the provider*. The layer that
journals a prompt is the workflow engine's dispatcher, which holds the text it composed. So every
replay, eval and judge bench read text the model never saw, and nothing — no ledger field, no run
surface — said a substitution had happened. Measured in #3111: the recorded prompt artifact for a
judge node still carried ``127.0.0.1`` while the model was handed ``[REDACTED_PHONE]``.

**Why the evidence here is the persisted BYTES.** A rail that asserts a function's return value
while the artifact on disk differs is exactly this defect in test form — the old behaviour would
have passed any such rail, because ``_prescan`` always returned the right thing; the wrong thing
was what got written. So every assertion below reads ``runs/<id>/outputs/<path>::prompt`` off the
filesystem, and the ledger row out of ``events.jsonl``.

**Non-vacuity is asserted, not assumed.** ``security.redact_credentials`` leaves plenty of
credential-LOOKING strings untouched (``password=hunter2`` sails straight through), so a planted
token nothing was going to match would make the whole file vacuous. The first test proves the
planted shape is one the scan really substitutes before any other test claims it is absent.

The planted value is AWS's own documentation example key. It is not a credential, and no test here
prints a real one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from personalclaw.guardrails.model_call import wrap_model_call_guard
from personalclaw.guardrails.scan import scan_outbound
from personalclaw.guardrails.wire import capture_wire_prompt
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent, ModelProvider
from personalclaw.llm_helpers import stream_and_collect
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


#: AWS's published example access key id — a documentation placeholder, matched by
#: `_CREDENTIAL_PATTERNS` when it follows a credential-named key. Same value the existing
#: guardrails scan tests plant.
_EXAMPLE_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
_PLANTED = f"aws_secret_access_key: {_EXAMPLE_KEY}"

_PROMPT = f"Summarize the deploy log. The operator pasted {_PLANTED} into the ticket."


class _FakeProvider(ModelProvider):
    """Records the message it was handed, so the WIRE text is observable independently."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def start(self) -> None:  # pragma: no cover - trivial
        pass

    async def shutdown(self) -> None:  # pragma: no cover - trivial
        pass

    async def approve_tool(self, request_id: str | int) -> None:  # pragma: no cover - unused
        pass

    async def reject_tool(self, request_id: str | int) -> None:  # pragma: no cover - unused
        pass

    def context_usage_pct(self) -> float | None:  # pragma: no cover - unused
        return None

    async def stream(self, message: str):
        self.seen.append(message)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text='{"summary": "one deploy, clean"}')
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=11, output_tokens=7)


@contextlib.contextmanager
def _isolated(home: Path) -> Iterator[None]:
    """Point the run store at a tmp home and put it back.

    The destructive-test rule: nothing here may touch the real `~/.personalclaw`. Patching
    `store.config_dir` is the same seam `test_ledger_golden` uses for the same reason.
    """
    home.mkdir(parents=True, exist_ok=True)
    original = store.config_dir
    store.config_dir = lambda: home  # type: ignore[assignment]
    try:
        yield
    finally:
        store.config_dir = original  # type: ignore[assignment]


def _guarded_completion(provider: _FakeProvider, *, scan_mode: str) -> Any:
    """A `completion` callable whose provider is wrapped in the REAL `ModelCallGuard`.

    The engine resolves `one_shot_completion` in production, which reaches the provider bridge and
    the bridge's own `wrap_model_call_guard`. Injecting the wrap here rather than mocking the whole
    bridge keeps the thing under test real: the guard, its `_prescan`, `scan_outbound`, and
    `stream_and_collect`'s consumption of the guarded stream are all production code. Only provider
    RESOLUTION is stood in for, and resolution is not where the defect lived.

    `provider_name` is deliberately not loopback-shaped: `_is_local_provider` forces `warn` for a
    local provider, which would silently make every redact-mode assertion vacuous.
    """
    guarded = wrap_model_call_guard(
        provider,
        use_case="background",
        provider_name="fake-remote",
        model="fake-model-1",
        scan_mode=scan_mode,
    )

    async def completion(prompt: str, **_kw: Any) -> str:
        return await stream_and_collect(guarded, prompt)

    return completion


_SPEC: dict[str, Any] = {
    "name": "wire-order",
    "root": {
        "kind": "sequence",
        "id": "root",
        "children": [
            {
                "kind": "infer",
                "id": "summarize",
                "config": {"model_tier": "fast", "prompt": _PROMPT},
            }
        ],
    },
}


async def _drive(home: Path, *, scan_mode: str) -> tuple[str, _FakeProvider]:
    provider = _FakeProvider()
    with _isolated(home):
        run = store.create(WorkflowRun(id="", workflow_name="wire-order"))
        store.write_spec(run.id, _SPEC)
        controller = RunController(
            run,
            _SPEC,
            services=EngineServices(completion=_guarded_completion(provider, scan_mode=scan_mode)),
        )
        status = await controller.run_to_completion(timeout=60)
        assert status is RunStatus.COMPLETE, f"run did not complete: {status}"
        return run.id, provider


def _persisted_prompt(home: Path, run_id: str) -> str:
    """The stored prompt body, read off DISK — not through any in-memory return value."""
    with _isolated(home):
        body = store.read_output(run_id, "root.children[0]::prompt")
    assert isinstance(body, str), f"no persisted prompt body for run {run_id}: {body!r}"
    return body


def _step_completed(home: Path, run_id: str) -> dict[str, Any]:
    with _isolated(home):
        rows = store.read_jsonl(run_id, journal_mod.EVENTS_FILE)
    done = [r for r in rows if r.get("kind") == journal_mod.STEP_COMPLETED]
    assert done, f"no step_completed row in events.jsonl for {run_id}"
    return done[-1]


# ── the vacuity floor ────────────────────────────────────────────────────────


def test_the_planted_shape_is_one_the_redactor_actually_substitutes() -> None:
    """Without this the rest of the file could pass with the fix reverted.

    `redact_credentials` matches a NAMED key followed by enough non-space characters; plenty of
    credential-looking text (`password=hunter2`) is left alone, and a planted token the scan never
    touches makes "the secret is absent from the persisted prompt" true for the wrong reason.
    """
    result = scan_outbound(_PROMPT, mode="redact")
    assert result.findings >= 1
    assert "credential" in result.categories
    assert result.text != _PROMPT, "the scan did not substitute anything in the planted prompt"
    assert _EXAMPLE_KEY not in result.text


# ── the guard publishes what it produced ─────────────────────────────────────


async def test_the_real_guard_publishes_the_wire_text_to_the_recorder() -> None:
    """The mechanism, at the seam. `_prescan` is the last code before the provider, so the text it
    returns IS the wire text — the fix is that it now also publishes it."""
    provider = _FakeProvider()
    completion = _guarded_completion(provider, scan_mode="redact")
    with capture_wire_prompt() as wire:
        await completion(_PROMPT)

    assert wire.captured, "the guard did not publish — nothing downstream can journal the wire text"
    assert wire.redacted is True
    assert wire.blocked is False
    assert "credential" in wire.categories
    assert _EXAMPLE_KEY not in wire.text
    # Cross-check against the provider's own record of what it was handed: the recorder must agree
    # with the wire, not merely be internally consistent.
    assert provider.seen == [wire.text]


async def test_warn_mode_publishes_findings_without_claiming_a_redaction() -> None:
    """`warn` sends the ORIGINAL text. Flagging it `redacted` would put a "this was altered" note
    on a verbatim prompt, which is the opposite error and just as misleading."""
    provider = _FakeProvider()
    completion = _guarded_completion(provider, scan_mode="warn")
    with capture_wire_prompt() as wire:
        await completion(_PROMPT)

    assert wire.captured and wire.redacted is False
    assert "credential" in wire.categories, "a warn-mode finding must still be recorded"
    assert wire.text == _PROMPT and provider.seen == [_PROMPT]


# ── the evidence that matters: the persisted artifact ────────────────────────


async def test_the_PERSISTED_prompt_is_the_post_redaction_text(tmp_path: Path) -> None:
    """🔴 The whole issue, read back off disk.

    Before the fix this file contained the planted credential while the provider was handed
    `[REDACTED: credential]` — a replay, an eval or a judge bench reading this artifact was reading
    text the model never saw. The assertion is on the stored BYTES, because the return value of
    `_prescan` was always correct; the artifact was not.
    """
    run_id, provider = await _drive(tmp_path / "home", scan_mode="redact")

    body = _persisted_prompt(tmp_path / "home", run_id)
    assert _EXAMPLE_KEY not in body, (
        "the persisted prompt still carries the credential the provider never received — the "
        "ledger and the wire disagree, which is #3166"
    )
    assert "[REDACTED: credential]" in body, (
        "the persisted prompt is neither the composed text nor the redacted one; the wire text is "
        "what has to be stored, not a third thing"
    )
    assert provider.seen == [body], (
        "the stored prompt is not byte-identical to what the provider was handed: "
        f"stored {len(body)} chars, wire {len(provider.seen[0])} chars"
    )


async def test_the_ledger_row_says_the_prompt_was_redacted(tmp_path: Path) -> None:
    """A post-redaction body with no marker is only half a fix: `[REDACTED: credential]` in a stored
    prompt is indistinguishable from an author who typed that string, so the substitution has to be
    a recorded FACT on the same row as the ref."""
    run_id, _provider = await _drive(tmp_path / "home", scan_mode="redact")

    row = _step_completed(tmp_path / "home", run_id)
    assert row.get("resolved_prompt_redacted") is True
    assert row.get("resolved_prompt_scan") == ["credential"]
    assert row.get("resolved_prompt_ref"), "the row carries no pointer to the prompt it describes"
    # The row's categories name CLASSES only. A matched value here would be the leak the
    # substitution exists to prevent, written down one field over.
    assert _EXAMPLE_KEY not in json.dumps(row)


async def test_a_clean_prompt_is_not_flagged_as_redacted(tmp_path: Path) -> None:
    """The calibration case. A flag that is always True is not a signal, and every prompt would
    carry a redaction badge the moment the engine started reading the recorder."""
    spec = json.loads(json.dumps(_SPEC))
    spec["root"]["children"][0]["config"]["prompt"] = "Summarize the deploy log in one sentence."

    provider = _FakeProvider()
    home = tmp_path / "home"
    with _isolated(home):
        run = store.create(WorkflowRun(id="", workflow_name="wire-order"))
        store.write_spec(run.id, spec)
        controller = RunController(
            run,
            spec,
            services=EngineServices(completion=_guarded_completion(provider, scan_mode="redact")),
        )
        assert await controller.run_to_completion(timeout=60) is RunStatus.COMPLETE
        run_id = run.id

    row = _step_completed(home, run_id)
    assert row.get("resolved_prompt_redacted") is False
    assert row.get("resolved_prompt_scan") == []
    assert _persisted_prompt(home, run_id) == "Summarize the deploy log in one sentence."


async def test_an_unguarded_completion_still_journals_the_composed_prompt(tmp_path: Path) -> None:
    """Fail-SAFE, not fail-open. With no guard in the path nothing publishes, and the composed
    prompt IS what went out — the scan is the only thing that would have altered it. So the
    fallback records the exact truth rather than an empty body."""
    seen: list[str] = []

    async def bare_completion(prompt: str, **_kw: Any) -> str:
        seen.append(prompt)
        return '{"summary": "ok"}'

    home = tmp_path / "home"
    with _isolated(home):
        run = store.create(WorkflowRun(id="", workflow_name="wire-order"))
        store.write_spec(run.id, _SPEC)
        controller = RunController(run, _SPEC, services=EngineServices(completion=bare_completion))
        assert await controller.run_to_completion(timeout=60) is RunStatus.COMPLETE
        run_id = run.id

    assert _persisted_prompt(home, run_id) == _PROMPT == seen[0]
    row = _step_completed(home, run_id)
    assert row.get("resolved_prompt_redacted") is False


async def test_the_inspect_surface_reports_the_redaction(tmp_path: Path) -> None:
    """The product half. A stored prompt nobody can tell is altered is still an evidence problem —
    the drawer that shows a run's prompt has to say the model saw something else."""
    from personalclaw.workflows import service

    run_id, _provider = await _drive(tmp_path / "home", scan_mode="redact")
    with _isolated(tmp_path / "home"):
        result = service.inspect_node(run_id, "summarize")

    assert result.get("ok") is True, result
    assert result["resolved_prompt_redacted"] is True
    assert result["resolved_prompt_scan"] == ["credential"]
    assert _EXAMPLE_KEY not in str(result["resolved_prompt"])


# ── block mode: nothing was sent, so nothing is persisted ────────────────────


async def test_a_blocked_call_persists_no_prompt_body(tmp_path: Path) -> None:
    """`block` mode refuses the call, so there is no wire for a record to agree with — and the
    composed prompt is the text that was refused for carrying a credential. Writing it to
    `runs/<id>/outputs/` would persist to disk exactly the secret the block existed to stop.

    Today the controller only stores a prompt on the SUCCESS branch, so this is belt-and-braces
    rather than a live leak; asserting it keeps a later "journal the prompt on failure too" change
    from quietly reintroducing one.
    """
    home = tmp_path / "home"
    provider = _FakeProvider()
    with _isolated(home):
        run = store.create(WorkflowRun(id="", workflow_name="wire-order"))
        store.write_spec(run.id, _SPEC)
        controller = RunController(
            run,
            _SPEC,
            services=EngineServices(completion=_guarded_completion(provider, scan_mode="block")),
        )
        status = await controller.run_to_completion(timeout=60)
        run_id = run.id

    assert status is not RunStatus.COMPLETE, "a blocked credential must not produce a clean run"
    assert provider.seen == [], "the provider was reached despite block mode"
    with _isolated(home):
        stored = store.read_output(run_id, "root.children[0]::prompt")
        rows = store.read_jsonl(run_id, journal_mod.EVENTS_FILE)
    assert not stored, f"a blocked call persisted a prompt body: {type(stored).__name__}"
    assert _EXAMPLE_KEY not in json.dumps(rows), "the refused credential reached the ledger"


def test_the_wire_recorder_is_isolated_between_concurrent_nodes() -> None:
    """Two nodes dispatched in parallel each get their own recorder.

    The controller runs every node in its own `asyncio.Task`, and a Task gets a COPY of the
    context — which is exactly why the recorder is a caller-bound MUTABLE object rather than a
    value the guard `set`s from inside. If that copy were shared, a fan-out of two `infer` nodes
    would cross-journal each other's prompts.
    """

    async def one(text: str) -> tuple[str, bool]:
        provider = _FakeProvider()
        completion = _guarded_completion(provider, scan_mode="redact")
        with capture_wire_prompt() as wire:
            await completion(text)
            await asyncio.sleep(0)  # force an interleave point inside the bound scope
            await completion(text)
        return wire.text, wire.redacted

    async def both() -> list[tuple[str, bool]]:
        return await asyncio.gather(one(_PROMPT), one("a clean prompt with nothing to substitute"))

    dirty, clean = asyncio.run(both())
    assert dirty[1] is True and _EXAMPLE_KEY not in dirty[0]
    assert clean[1] is False and clean[0] == "a clean prompt with nothing to substitute"
