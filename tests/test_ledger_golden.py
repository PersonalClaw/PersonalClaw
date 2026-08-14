"""Golden-file proof that the ledger machinery writes the same bytes it always did (PP-4).

PP-4 moves append/redact/stamp/spill and the kind registry out of `workflows/journal.py` into a
`personalclaw/ledger/` package. "Pure extraction" is a claim a passing test suite cannot settle:
the suite asserts the properties someone thought to assert, and the whole risk of moving a writer
is the property nobody wrote down — a key order, a rounding, a stub field, which kinds mirror to
`events.jsonl`. So the bar is the bytes.

Two captures, both committed as fixtures BEFORE the extraction and diffed after:

* **`run_*.jsonl`** — a real three-node engine run through the real `RunController`, whose middle
  node spills oversize and whose last node spills binary. This is the atom's stated bar: a real
  run's `journal.jsonl` + `events.jsonl`.
* **`emitters_*.jsonl`** — one raw `write()` per registered kind, then the field-shaping emitters a
  transform-only run never reaches (`step_completed` with an `InstanceState`, `step_failed` with a
  `Failure`, the outcome pair, a nested-redaction `workspace_provisioned`) and all three
  `store_output` paths. A transform run touches four kinds; the registry has sixty. This capture is
  what makes the *mirroring table* — which kinds land in `events.jsonl` — part of the proof rather
  than an assumption, and it goes red if a constant stops being re-exported at all.

Only provably-nondeterministic fields are normalized (`ts`, the run-id half of `event_id`, wall
durations). Everything else — key order, numeric rounding, redaction, spill stubs, seq numbering —
is compared byte for byte.

Regenerating these fixtures is a deliberate act, not a convenience: run this module as a script
(`python tests/test_ledger_golden.py`). There is no environment variable that rewrites them, because
a golden file rewritten by the run under test blesses whatever that run did.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import (
    Failure,
    FailureClass,
    InstanceState,
    RunStatus,
    WorkflowRun,
)
from personalclaw.workflows.native_defs import register_native_provider

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "ledger_golden"

#: Big enough to cross `MAX_INLINE_OUTPUT_BYTES` (64KB) so the middle node spills `oversize` and
#: leaves a head+tail preview stub, which is the branch with the most fields to get wrong.
_OVERSIZE_BODY = "spill" * 20_000

#: A base64 PNG header — `is_binary_payload` matches on content, so this spills `binary` (no
#: preview) despite being tiny. Both spill reasons in one run.
_BINARY_BODY = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGP4//8/AAX+Av7czFnnAAAAAElFTkSuQmCC"  # noqa: E501

#: Three transforms with a real binding chain, so the run journals a dependency-ordered sequence
#: rather than three independent nodes.
GOLDEN_SPEC: dict[str, Any] = {
    "name": "ledger-golden",
    "root": {
        "kind": "sequence",
        "id": "main",
        "children": [
            {"kind": "transform", "id": "gather", "config": {"expr": "gathered"}},
            {
                "kind": "transform",
                "id": "bulk",
                "config": {"expr": "{{nodes.gather.output}}" + _OVERSIZE_BODY},
            },
            {"kind": "transform", "id": "shot", "config": {"expr": _BINARY_BODY}},
        ],
    },
}


# ── normalization ────────────────────────────────────────────────────────────


def _normalize(records: list[dict[str, Any]], run_id: str) -> list[str]:
    """Serialize records for comparison, blanking only what cannot be deterministic.

    `ts` is a wall clock, `event_id` embeds the store-assigned run id, and the duration fields are
    measured elapsed time. Nothing else is touched: key order, rounding, redaction markers, stub
    shapes and `seq` are all part of what this file is asserting.
    """
    out: list[str] = []
    for rec in records:
        norm = dict(rec)
        if "ts" in norm:
            norm["ts"] = "TS"
        for wall in ("duration_secs", "elapsed_secs"):
            if wall in norm:
                norm[wall] = 0.0
        line = json.dumps(norm, sort_keys=True, ensure_ascii=False)
        if run_id:
            line = line.replace(run_id, "RUN")
        out.append(line)
    return out


# ── capture: a real engine run ───────────────────────────────────────────────


@contextlib.contextmanager
def _isolated(home: Path) -> Iterator[None]:
    """Point the run store at `home` and put it back afterwards.

    A context manager rather than pytest's `monkeypatch` because the same capture code runs under
    the regeneration entry point below, where there is no fixture — and a store left pointing at a
    temp directory would silently corrupt every later test in the session.
    """
    home.mkdir(parents=True, exist_ok=True)
    original = store.config_dir
    store.config_dir = lambda: home  # type: ignore[assignment]
    try:
        yield
    finally:
        store.config_dir = original  # type: ignore[assignment]


async def _capture_run(home: Path) -> dict[str, list[str]]:
    """Drive the real controller over `GOLDEN_SPEC` and return both files, normalized."""
    from personalclaw.workflows import defs as defs_mod

    saved = dict(defs_mod._providers)
    defs_mod._providers.clear()
    register_native_provider()
    try:
        with _isolated(home):
            run = store.create(WorkflowRun(id="", workflow_name="ledger-golden"))
            store.write_spec(run.id, GOLDEN_SPEC)
            controller = RunController(run, GOLDEN_SPEC, services=EngineServices())
            status = await controller.run_to_completion(timeout=60)
            assert status is RunStatus.COMPLETE, f"golden run did not complete: {status}"
            return {
                "journal": _normalize(store.read_jsonl(run.id, journal_mod.JOURNAL_FILE), run.id),
                "events": _normalize(store.read_jsonl(run.id, journal_mod.EVENTS_FILE), run.id),
            }
    finally:
        defs_mod._providers.clear()
        defs_mod._providers.update(saved)


# ── capture: every kind + the field-shaping emitters ─────────────────────────


def _registered_kinds() -> list[str]:
    """Every kind constant the journal module exposes, read off the module itself.

    Derived rather than listed so a constant that stops being exported (or one that is added
    without a golden line) shows up as a diff instead of passing unnoticed.
    """
    return sorted(
        value
        for name, value in vars(journal_mod).items()
        if name.isupper() and isinstance(value, str) and not name.endswith("_FILE")
    )


def _capture_emitters(home: Path) -> dict[str, list[str]]:
    with _isolated(home):
        return _drive_emitters()


def _drive_emitters() -> dict[str, list[str]]:
    run = store.create(WorkflowRun(id="", workflow_name="emitters"))
    j = journal_mod.Journal(run.id)

    # One raw write per registered kind: proves the mirroring table (which kinds reach
    # `events.jsonl`) and the stamping applied to every one of them.
    for kind in _registered_kinds():
        j.write(kind, marker=f"probe:{kind}")

    # The typed emitters whose job is field SHAPING — enum unwrapping, rounding, tristates,
    # nested redaction. A transform-only run reaches none of them.
    j.step_completed(
        "main.children[0]",
        "gather",
        epoch=2,
        cache_key="main.children[0]|2|abc|def",
        state=InstanceState.DEGRADED,
        duration_secs=1.23456,
        tokens=42,
        retries=1,
        model="m",
        provider="p",
        cost_usd=0.1234567,
        degraded_reason="token absent",
        resolved_prompt_ref="prompts/gather.txt",
        output_ref="outputs/gather.json",
    )
    j.step_failed(
        "main.children[1]",
        "bulk",
        epoch=2,
        failure=Failure(
            failure_class=FailureClass.NETWORK,
            cause_plain="connection reset",
            remediation="retry",
            recoverable=True,
        ),
        attempt=2,
        retries_exhausted=True,
        signature={"class": "network"},
    )
    j.task_verified("main.children[0]", "gather", task_id="t1", passed=None, criterion="builds")
    pending = j.pending_outcome(
        "main.children[0]",
        "gather",
        epoch=1,
        subject="ship it",
        metric="latency",
        horizon_secs=3.14159,
        baseline=1.2345678,
    )
    j.outcome_resolved(
        "main.children[0]",
        "gather",
        pending_event_id=str(pending.get("event_id", "")),
        subject="ship it",
        metric="latency",
        baseline=1.2345678,
        measured=None,
        score=0.9876543,
        resolution="inconclusive",
    )
    # Nested redaction: the secret must not survive into either file at any depth.
    j.workspace_provisioned(
        {"mode": "worktree", "env": {"token": "sk-ant-api03-DEADBEEFdeadbeefDEADBEEFdeadbeef"}}
    )

    # All three `store_output` paths, each carrying a credential so redaction is proven on the
    # spill path too (the offloaded body is redacted before it is written, not after).
    #
    # Captured as its OWN golden because a spill stub never appears in a journal line: a run
    # journals the `output_ref` and the stub rides in the node's inline output. Diffing only the
    # two .jsonl files would leave `result_omitted`, `bytes` and the head+tail preview — three of
    # the four things the atom says move — unproven.
    spill: list[str] = []
    for i, (case, payload) in enumerate(
        (
            ("inline", {"ok": True, "key": "AKIAIOSFODNN7EXAMPLE"}),
            ("oversize", _OVERSIZE_BODY),
            ("binary", _BINARY_BODY),
        )
    ):
        ref, inline = j.store_output(f"main.children[{i}]", payload)
        spill.append(json.dumps({"case": case, "ref": ref, "inline": inline}, sort_keys=True))

    return {
        "journal": _normalize(store.read_jsonl(run.id, journal_mod.JOURNAL_FILE), run.id),
        "events": _normalize(store.read_jsonl(run.id, journal_mod.EVENTS_FILE), run.id),
        "spill": spill,
    }


# ── the tests ────────────────────────────────────────────────────────────────


def _assert_golden(name: str, actual: list[str]) -> None:
    path = GOLDEN_DIR / f"{name}.jsonl"
    assert path.exists(), f"missing golden fixture {path} — regenerate with `python {__file__}`"
    expected = path.read_text(encoding="utf-8").splitlines()
    if actual != expected:
        # Report the FIRST divergence with both lines: a whole-file dump of sixty JSON lines
        # buries the one that moved.
        for i, (want, got) in enumerate(zip(expected, actual)):
            if want != got:
                raise AssertionError(
                    f"{name}.jsonl diverged at line {i + 1}\n  golden: {want}\n  actual: {got}"
                )
        raise AssertionError(
            f"{name}.jsonl length changed: golden has {len(expected)} lines, "
            f"run wrote {len(actual)}"
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_a_real_run_writes_the_golden_journal_and_events(tmp_path):
    """The atom's stated bar: a real multi-node run's two files, byte for byte."""
    captured = await _capture_run(tmp_path / "home")
    _assert_golden("run_journal", captured["journal"])
    _assert_golden("run_events", captured["events"])


def test_every_registered_kind_and_the_shaping_emitters_are_byte_identical(tmp_path):
    """The registry sweep + the emitters a transform run never reaches."""
    captured = _capture_emitters(tmp_path / "home")
    _assert_golden("emitters_journal", captured["journal"])
    _assert_golden("emitters_events", captured["events"])
    _assert_golden("emitters_spill", captured["spill"])


def test_the_golden_run_exercised_both_spill_reasons_and_the_ledger_mirror():
    """A vacuity guard: goldens that captured nothing interesting would still compare equal.

    Assert the fixtures actually contain the machinery this atom moves — both spill reasons, a
    redaction marker, and an `events.jsonl` that is a strict non-empty subset of the journal.
    """
    spill = (GOLDEN_DIR / "emitters_spill.jsonl").read_text(encoding="utf-8")
    assert '"reason": "oversize"' in spill, "the golden never crossed MAX_INLINE_OUTPUT_BYTES"
    assert '"reason": "binary"' in spill, "the golden never tripped magic-prefix detection"
    assert '"head"' in spill and '"tail"' in spill, "the oversize preview was never captured"
    assert '"result_omitted": true' in spill

    emitters = (GOLDEN_DIR / "emitters_journal.jsonl").read_text(encoding="utf-8")
    for secret in ("sk-ant-api03-DEADBEEF", "AKIAIOSFODNN7EXAMPLE"):
        assert secret not in emitters, f"{secret} reached the golden journal"
        assert secret not in spill, f"{secret} reached the golden spill stub"

    j_lines = (GOLDEN_DIR / "run_journal.jsonl").read_text(encoding="utf-8").splitlines()
    e_lines = (GOLDEN_DIR / "run_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert j_lines and e_lines
    assert len(e_lines) < len(j_lines), "events.jsonl should be the ledger SUBSET of the journal"
    kinds = {json.loads(line)["kind"] for line in e_lines}
    assert kinds <= journal_mod.LEDGER_KINDS
    assert journal_mod.STEP_COMPLETED in kinds


def test_the_ledger_package_does_not_import_the_workflow_engine():
    """The seam guarantee, as a rail rather than a convention.

    `personalclaw.ledger` exists so a SECOND producer can carry a ledger. The moment anything under
    it imports `personalclaw.workflows`, that stops being true — a loop emitter would have to pull
    the workflow engine in to journal a cycle, which is the dependency direction the extraction
    exists to reverse. Checked statically (an AST scan, not an import probe) because a lazy
    function-local import is exactly how this would creep back in and would not show up at import
    time.
    """
    pkg = Path(journal_mod.__file__).parent.parent / "ledger"
    modules = sorted(pkg.glob("*.py"))
    assert len(modules) >= 6, f"expected the ledger package's modules, found {modules}"

    offenders: list[str] = []
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.startswith("personalclaw.workflows"):
                    offenders.append(f"{path.name}:{node.lineno} imports {name}")
    assert not offenders, "personalclaw.ledger must not depend on the engine: " + "; ".join(
        offenders
    )


# ── deliberate regeneration ──────────────────────────────────────────────────


def _regenerate() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="ledger-golden-"))
    try:
        home = tmp / "run-home"
        home.mkdir()
        run = asyncio.run(_capture_run(home))
        emitters = _capture_emitters(tmp / "emitters-home")
        for name, lines in (
            ("run_journal", run["journal"]),
            ("run_events", run["events"]),
            ("emitters_journal", emitters["journal"]),
            ("emitters_events", emitters["events"]),
            ("emitters_spill", emitters["spill"]),
        ):
            (GOLDEN_DIR / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"wrote goldens to {GOLDEN_DIR}")


if __name__ == "__main__":
    _regenerate()
