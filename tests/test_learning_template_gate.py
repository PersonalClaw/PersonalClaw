"""The ad-hoc→template gate at its CALL SITE (LEARNING-FLYWHEEL §3.2, WF2LEA-7).

``detectors.gate`` shipped with zero production callers: the chain decided nothing, and its typed
``Skip`` reasons — the counts §3.2 says "are what say which gate earns its keep" — had no writer.
The load-bearing tests here are the two that would have caught that:
``test_the_ladder_fifth_branch_reaches_the_gate`` (a real turn reaches the chain) and
``test_every_negative_decision_writes_a_typed_row`` (every refusal leaves a row).

Everything else pins the two properties that make the wiring safe: an accepted candidate is FILED,
never installed, and a recording failure can never break the path that files it.
"""

import asyncio

import pytest

from personalclaw.learning import detectors, staging
from personalclaw.learning.detectors import Candidate, Skip
from personalclaw.learning.staging import FlushOutcome
from personalclaw.learning.template_gate import (
    LEDGER_PREFIX,
    evaluate,
    record_skip,
    skip_counts,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the process-global staging store at tmp_path (same shape as the gate tests)."""
    staging.reset_store()
    s = staging.StagingStore(tmp_path)
    monkeypatch.setattr(staging, "get_store", lambda *a, **k: s)
    yield s
    s.close()
    staging.reset_store()


@pytest.fixture
def no_filing(monkeypatch):
    """Capture enqueue calls instead of touching the real proposals dir."""
    from personalclaw.learning import proposals

    calls: list[dict] = []

    class _Prop:
        id = "prop-1"

    def _fake(**kwargs):
        calls.append(kwargs)
        return proposals.Verdict.NEW, _Prop()

    monkeypatch.setattr(proposals, "enqueue", _fake)
    return calls


def _skipped_rows(store):
    with store._cursor() as cur:
        rows = cur.execute(
            "SELECT cadence, detail FROM flush_records WHERE outcome = ? ORDER BY id;",
            (FlushOutcome.FLUSH_SKIPPED.value,),
        ).fetchall()
    return [{"cadence": r[0], "detail": r[1]} for r in rows]


# ── the clause that ends the inert-module problem ──


def test_the_gate_has_a_production_caller():
    """The regression guard: ``detectors.gate`` must be reached from non-test code.

    Asserted structurally rather than by grep-in-prose because the defect this atom fixes was
    exactly "the chain exists and nothing calls it" — a module can pass every unit test it has while
    being unreachable in production.
    """
    import ast
    import pathlib

    src = pathlib.Path(detectors.__file__).parent / "template_gate.py"
    tree = ast.parse(src.read_text())
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "gate"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "detectors"
    ]
    assert calls, "template_gate must call detectors.gate — that call IS the wiring"


@pytest.mark.parametrize(
    "candidate,expected",
    [
        (Candidate(run_id="r1", steps=["build {{x}}"]), Skip.TOO_FEW_STEPS),
        (
            Candidate(
                run_id="r2",
                steps=["build {{x}}", "deploy the result"],
                template_surfaced=True,
            ),
            Skip.TEMPLATE_EXISTS,
        ),
        (
            Candidate(run_id="r3", steps=["build {{x}}", "deploy the result"], budget_burn=0.95),
            Skip.BUDGET_BURN,
        ),
        (
            Candidate(run_id="r4", steps=["build the thing", "deploy the thing"]),
            Skip.NO_SLOTS,
        ),
    ],
)
def test_every_negative_decision_writes_a_typed_row(store, candidate, expected):
    """§3.2: EVERY refusal leaves a row carrying its TYPED reason, not prose.

    Parametrized across the pre-gates because a reason that never fires is indistinguishable from a
    gate that does not work — each branch needs its own proof that it reaches the ledger.
    """
    outcome = evaluate(candidate)
    assert outcome.decision.skip_reason == expected.value
    assert outcome.recorded is True
    assert outcome.filed is False

    rows = _skipped_rows(store)
    assert len(rows) == 1
    detail = str(rows[0]["detail"])
    # Prefixed AND typed: the ledger is shared with the capture gate's denials, so an untagged
    # "declined" would be unattributable to a gate.
    assert detail.startswith(LEDGER_PREFIX + ":")
    assert expected.value in detail


def test_a_low_score_refusal_is_recorded_with_its_score(store):
    """The LOW_SCORE branch fires after scoring, so it is the one that could be lost."""
    candidate = Candidate(
        run_id="r5",
        steps=["review /Users/x/a.py and https://example.com/b", "check {{x}} at deadbeefcafe12"],
    )
    outcome = evaluate(candidate)
    assert outcome.decision.skip_reason == Skip.LOW_SCORE.value
    assert outcome.recorded is True
    assert Skip.LOW_SCORE.value in str(_skipped_rows(store)[0]["detail"])


def test_a_positive_decision_records_no_skip_row(store, no_filing):
    """Only refusals get a row — otherwise the reason counts are just traffic."""
    candidate = Candidate(
        run_id="r6",
        steps=[
            "fetch {{source_url}} and validate the payload",
            "transform the result into {{format}}",
            "deploy it to {{target}} and verify the output",
        ],
    )
    outcome = evaluate(candidate)
    assert outcome.decision.action == "auto_file"
    assert outcome.recorded is False
    assert _skipped_rows(store) == []


def test_record_skip_refuses_to_log_an_accept_as_a_skip(store):
    """Recording an accept under FLUSH_SKIPPED would corrupt the very counts it feeds."""
    accept = detectors.gate(
        Candidate(
            run_id="r7",
            steps=[
                "fetch {{source_url}} and validate the payload",
                "transform the result into {{format}}",
                "deploy it to {{target}} and verify the output",
            ],
        )
    )
    assert accept.action == "auto_file"
    assert record_skip(accept) is False
    assert _skipped_rows(store) == []


# ── filing is never installing ──


def test_an_accepted_candidate_is_filed_as_a_pending_proposal(store, no_filing):
    """The human-accept invariant: the gate FILES, and filing writes no definition."""
    outcome = evaluate(
        Candidate(
            run_id="r8",
            steps=[
                "fetch {{source_url}} and validate the payload",
                "transform the result into {{format}}",
                "deploy it to {{target}} and verify the output",
            ],
        ),
        session_key="sess-1",
    )
    assert outcome.filed and outcome.proposal_id == "prop-1"
    assert len(no_filing) == 1
    call = no_filing[0]
    assert call["kind"] == "template"
    assert call["run_id"] == "r8"
    assert call["session_key"] == "sess-1"
    # Nothing in the call can install: enqueue's contract is a PENDING row.
    assert "installer" not in call


def test_the_consult_band_files_nothing(store, no_filing):
    """§3.2 pays for a model only in the middle band, and this call site has no model.

    It must not promote on an inconclusive score, and must not record it as a refusal it was not.
    """
    candidate = Candidate(
        run_id="r9", steps=["build {{target}}", "deploy the result", "notify the team"]
    )
    # Pinned, not skipped: a conditional skip here would silently stop covering the band the moment
    # a threshold moved, which is the one branch with no ledger row and no proposal to notice by.
    assert detectors.gate(candidate).action == "consult"
    outcome = evaluate(candidate)
    assert outcome.filed is False
    assert outcome.recorded is False
    assert no_filing == []


def test_filing_survives_a_recording_failure(tmp_path, monkeypatch, no_filing):
    """Observability must never cost a proposal the chain already approved."""
    staging.reset_store()
    monkeypatch.setattr(
        staging, "get_store", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no store"))
    )
    outcome = evaluate(
        Candidate(
            run_id="r10",
            steps=[
                "fetch {{source_url}} and validate the payload",
                "transform the result into {{format}}",
                "deploy it to {{target}} and verify the output",
            ],
        )
    )
    assert outcome.recorded is False
    assert outcome.filed is True


def test_a_refusal_survives_a_recording_failure(tmp_path, monkeypatch):
    """The symmetric half: a dead ledger must not raise into the turn."""
    staging.reset_store()
    monkeypatch.setattr(
        staging, "get_store", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no store"))
    )
    outcome = evaluate(Candidate(run_id="r11", steps=["one step"]))
    assert outcome.decision.skip_reason == Skip.TOO_FEW_STEPS.value
    assert outcome.recorded is False


# ── the reason counts §3.2 tunes against ──


def test_skip_counts_reads_back_only_this_gates_reasons(store):
    """The counts must not be inflated by the capture gate sharing the ledger."""
    evaluate(Candidate(run_id="a", steps=["one step"]))
    evaluate(Candidate(run_id="b", steps=["one step"]))
    evaluate(Candidate(run_id="c", steps=["build the thing", "deploy the thing"]))
    # A capture-gate denial in the same table, with the same outcome, different prefix.
    store.record_flush(
        cadence="per_turn", outcome=FlushOutcome.FLUSH_SKIPPED, detail="not_worthwhile"
    )

    counts = skip_counts()
    assert counts == {Skip.TOO_FEW_STEPS.value: 2, Skip.NO_SLOTS.value: 1}


def test_skip_counts_is_empty_without_a_store(monkeypatch):
    """A statistics read is never worth failing a caller over."""
    staging.reset_store()
    monkeypatch.setattr(
        staging, "get_store", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no store"))
    )
    assert skip_counts() == {}


# ── the ladder's fifth branch ──


def _ladder(raw, monkeypatch, *, defs=None):
    """Drive run_skill_ladder_review with a canned model response."""
    from personalclaw import after_turn_review as atr
    from personalclaw.workflows import service

    async def _completion(_prompt):
        return raw

    async def _list_defs(**_kw):
        return {"ok": True, "defs": defs or []}

    monkeypatch.setattr(service, "list_defs", _list_defs)
    return asyncio.run(
        atr.run_skill_ladder_review(
            session_key="sess-9",
            user_message="please do the thing",
            assistant_text="did the thing",
            loaded_skills=[],
            completion=_completion,
        )
    )


def test_the_ladder_fifth_branch_reaches_the_gate(store, no_filing, monkeypatch):
    """The call site itself: a template-action turn is scored and FILED, with a chip back.

    This is the test that fails if the fifth branch is removed — the clause's whole point is that a
    real turn now reaches ``detectors.gate``.
    """
    raw = (
        '{"action": "template", "slug": "nightly-report", '
        '"description": "build and publish the nightly report", '
        '"steps": ["fetch {{source_url}} and validate the payload", '
        '"transform the result into {{format}}", '
        '"deploy it to {{target}} and verify the output"]}'
    )
    summary = _ladder(raw, monkeypatch)
    assert summary == "Proposed template: nightly-report"
    assert len(no_filing) == 1 and no_filing[0]["kind"] == "template"


def test_the_fifth_branch_records_a_refusal_and_surfaces_no_chip(store, no_filing, monkeypatch):
    """A declined candidate is silent to the user but LOUD in the ledger."""
    raw = (
        '{"action": "template", "slug": "one-liner", "description": "just one step", '
        '"steps": ["deploy the thing"]}'
    )
    assert _ladder(raw, monkeypatch) is None
    assert no_filing == []
    rows = _skipped_rows(store)
    assert len(rows) == 1 and Skip.TOO_FEW_STEPS.value in str(rows[0]["detail"])


def test_template_exists_is_resolved_against_the_real_def_registry(store, no_filing, monkeypatch):
    """``template_surfaced`` must have a real WRITER, not sit at its dataclass default.

    A defaulted field is an unsupplied input: left at False, the TEMPLATE_EXISTS pre-gate could
    never fire in production however well it was unit-tested.
    """
    raw = (
        '{"action": "template", "slug": "nightly-report", "description": "already exists", '
        '"steps": ["fetch {{source_url}} and validate the payload", '
        '"transform the result into {{format}}", '
        '"deploy it to {{target}} and verify the output"]}'
    )
    assert _ladder(raw, monkeypatch, defs=[{"name": "nightly-report"}]) is None
    assert no_filing == []
    assert Skip.TEMPLATE_EXISTS.value in str(_skipped_rows(store)[0]["detail"])


def test_the_first_four_ladder_branches_still_enqueue_skills(monkeypatch):
    """The fifth branch must not have stolen the other four's turns."""
    from personalclaw.skills import proposals as skill_proposals

    seen: list[dict] = []

    class _P:
        slug = "some-skill"

    monkeypatch.setattr(skill_proposals, "enqueue", lambda **kw: (seen.append(kw), _P())[1])
    raw = (
        '{"action": "create", "slug": "some-skill", "description": "a how-to", '
        '"procedure_md": "step one", "triggers": "x"}'
    )
    summary = _ladder(raw, monkeypatch)
    assert summary == "Proposed skill (new skill): some-skill"
    assert len(seen) == 1


def test_a_template_action_without_steps_is_dropped(store, no_filing, monkeypatch):
    """A 'template' claim with no steps is garbage, not a candidate — and not a skip row either."""
    raw = '{"action": "template", "slug": "empty", "description": "nothing"}'
    assert _ladder(raw, monkeypatch) is None
    assert no_filing == [] and _skipped_rows(store) == []


def test_template_steps_are_redacted_before_they_are_scored(store, no_filing, monkeypatch):
    """An accepted candidate becomes a proposal body, so the skill branches' posture applies."""
    raw = (
        '{"action": "template", "slug": "leaky", "description": "has a secret", '
        '"steps": ["fetch {{source_url}} with token sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA", '
        '"transform the result into {{format}}", '
        '"deploy it to {{target}} and verify the output"]}'
    )
    _ladder(raw, monkeypatch)
    assert no_filing, "expected the candidate to be filed"
    body = str(no_filing[0]["body"]) + str(no_filing[0]["source_excerpt"])
    assert "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA" not in body


# ── the MCP tool ──


@pytest.fixture
def no_defs(monkeypatch):
    """No workflow-def providers, so TEMPLATE_EXISTS depends on the candidate alone."""
    from personalclaw.workflows import defs as defs_mod

    monkeypatch.setattr(defs_mod, "list_providers", lambda: [])


def test_the_tool_files_a_draft_proposal(store, no_filing, no_defs):
    """``template_save_from_session`` exists, is dispatched, and files a DRAFT."""
    from personalclaw import mcp_core

    out = mcp_core._call_tool_inner(
        "template_save_from_session",
        {
            "name": "nightly-report",
            "description": "build and publish the nightly report",
            "steps": [
                "fetch {{source_url}} and validate the payload",
                "transform the result into {{format}}",
                "deploy it to {{target}} and verify the output",
            ],
        },
    )
    assert "DRAFT template proposal" in out
    assert "nothing was written to the workflow library" in out
    assert len(no_filing) == 1 and no_filing[0]["kind"] == "template"


def test_the_tool_reports_a_decline_with_its_typed_reason(store, no_filing, no_defs):
    """A silent no teaches the model nothing; the reason is what it can act on."""
    from personalclaw import mcp_core

    out = mcp_core._call_tool_inner(
        "template_save_from_session",
        {"name": "not-a-template", "steps": ["build the thing", "deploy the thing"]},
    )
    assert "Declined" in out and Skip.NO_SLOTS.value in out
    assert no_filing == []
    assert Skip.NO_SLOTS.value in str(_skipped_rows(store)[0]["detail"])


def test_the_tool_resolves_template_exists_against_the_def_registry(store, no_filing, monkeypatch):
    """The tool's own ``template_surfaced`` writer — not the ladder's."""
    from personalclaw import mcp_core
    from personalclaw.workflows import defs as defs_mod

    class _Provider:
        async def list_defs(self, *, limit=200, offset=0):
            return [{"name": "nightly-report"}], 1

    monkeypatch.setattr(defs_mod, "list_providers", lambda: ["fake"])
    monkeypatch.setattr(defs_mod, "get_provider", lambda _n: _Provider())
    out = mcp_core._call_tool_inner(
        "template_save_from_session",
        {
            "name": "nightly-report",
            "steps": [
                "fetch {{source_url}} and validate the payload",
                "transform the result into {{format}}",
                "deploy it to {{target}} and verify the output",
            ],
        },
    )
    assert Skip.TEMPLATE_EXISTS.value in out
    assert no_filing == []


@pytest.mark.parametrize(
    "args,needle",
    [
        ({"steps": ["a", "b"]}, "name is required"),
        ({"name": "x"}, "steps is required"),
        ({"name": "x", "steps": []}, "steps is required"),
    ],
)
def test_the_tool_validates_its_inputs(args, needle):
    from personalclaw import mcp_core

    assert needle in mcp_core._call_tool_inner("template_save_from_session", args)


def test_the_tool_is_declared_in_the_tool_list():
    """A dispatch arm nothing declares is unreachable from the model."""
    from personalclaw import mcp_core

    names = {t["name"] for t in mcp_core._list_tools()}
    assert "template_save_from_session" in names


def test_the_tool_has_manifest_meta():
    """A live tool with no TOOL_META entry reds the manifest-drift gate."""
    from personalclaw.manifest_meta import TOOL_META

    meta = TOOL_META["template_save_from_session"]
    assert meta["examples"] and meta["examples"][0]["summary"].strip()
    # Every example arg must be a real parameter — an invented signature teaches a wrong call.
    from personalclaw import mcp_core

    tool = next(t for t in mcp_core._list_tools() if t["name"] == "template_save_from_session")
    real = set(tool["inputSchema"]["properties"])
    assert set(meta["examples"][0]["args"]) <= real
