"""The `revise{step_ref, comment}` answer verb on a waiting gate (UP / WF2UNI-8).

A reviewer who wants ONE step changed used to have only two answers: approve the plan as
written, or reject it and re-run — which re-rolls every stage nobody complained about (the
argument `revision.py` makes about regeneration, applied to a live run). `revise` is the
third answer: patch exactly one node, then re-ask.

The load-bearing claims, each one a test below:

* **exactly one node, or nothing.** A `step_ref` that names no step, or names two, is
  refused with a typed code — an ambiguous ref would edit whichever copy the walk reached
  first and leave the other running the text the reviewer rejected.
* **a rejected revise does NOT consume the token.** Same reasoning as
  `validate_answer`'s ordering (`controller.resume`'s docstring): the reviewer is still
  deciding, so a refusal must leave them able to answer.
* **a revise is not an approval.** Nothing is marked approved, the gate is not resolved,
  and `gate_stats` must not count it as a said-no.
* **what runs IS what was recorded.** The revision goes through `_commit_mutation`, the
  single writer of `spec.json` + `spec_history/`, so the spec the engine executes and the
  spec on disk are one document rather than two that agree by convention.
* **the comment reaches the PROMPT.** An annotation the worker never reads would leave the
  step re-running the text the reviewer just objected to.
* **the revised gate re-asks rather than replaying its old answer** — the cache key hashes
  the node's own spec region, and the prompt changed.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.workflows import human_input as HI
from personalclaw.workflows import introspection
from personalclaw.workflows import journal as J
from personalclaw.workflows import revision, store
from personalclaw.workflows.controller import EngineServices, RunController, _parse_revise
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


def _spec() -> dict:
    """A prompt-bearing step followed by the gate that reviews it — the shape a revise is
    for. The `draft` step's prompt is what a reviewer's comment has to reach."""
    return {
        "name": "reviewed",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "transform", "id": "draft", "config": {"expr": {"v": 1}, "prompt": "w"}},
                {
                    "kind": "gate",
                    "id": "approve",
                    "config": {"kind": "approval", "prompt": "Ship it?", "timeout_secs": 0},
                },
            ],
        },
    }


async def _blocked():
    """A real run parked on a real gate, with a real spec on disk."""
    spec = _spec()
    run = store.create(WorkflowRun(id="", workflow_name="reviewed", mode="background"))
    store.write_spec(run.id, spec)
    c = RunController(run, spec, services=EngineServices())
    await c.run_to_completion(timeout=20)
    return c, HI.list_continuations(run.id)[0].token


def _revise(step_ref: str, comment: str) -> dict:
    return {"revise": {"step_ref": step_ref, "comment": comment}}


# ── the grammar ──────────────────────────────────────────────────────────────


class TestGrammar:
    def test_the_nested_and_flat_spellings_both_parse(self) -> None:
        """`answer` is untyped by contract, so both shapes a caller reaches for are read."""
        assert _parse_revise({"revise": {"step_ref": "a", "comment": "b"}}) == ("a", "b")
        assert _parse_revise({"revise": True, "step_ref": "a", "comment": "b"}) == ("a", "b")

    @pytest.mark.parametrize(
        "answer", [True, False, "some prose about revising the plan", {"approved": True}, None]
    )
    def test_an_ordinary_answer_is_not_a_revise(self, answer) -> None:
        """Recognised structurally, by the key. Sniffing for the WORD would hijack a text
        gate's legitimate prose."""
        assert _parse_revise(answer) is None

    def test_a_falsy_flag_is_not_a_revise(self) -> None:
        """`{"revise": false}` against an approval gate is a clumsy rejection; the approval
        path's own validator is the right thing to answer it."""
        assert _parse_revise({"revise": False}) is None


class TestStepRefResolution:
    def test_a_unique_id_resolves(self) -> None:
        node_id, code, _msg = revision.resolve_step_ref(_spec()["root"], "draft")
        assert (node_id, code) == ("draft", "")

    def test_an_unknown_ref_is_refused_and_lists_the_real_steps(self) -> None:
        _id, code, message = revision.resolve_step_ref(_spec()["root"], "ghost")
        assert code == "WF_REVISE_UNKNOWN_STEP"
        assert "draft" in message and "approve" in message

    def test_a_duplicated_id_is_refused_rather_than_half_patched(self) -> None:
        """The risk a set-membership check cannot see: patching an ambiguous ref would edit
        one copy and leave the other running the rejected text."""
        root = {
            "kind": "sequence",
            "id": "s",
            "children": [{"kind": "transform", "id": "w"}, {"kind": "transform", "id": "w"}],
        }
        assert revision.resolve_step_ref(root, "w")[1] == "WF_REVISE_AMBIGUOUS_STEP"

    def test_an_empty_ref_is_refused(self) -> None:
        assert revision.resolve_step_ref(_spec()["root"], "  ")[1] == "WF_REVISE_NO_STEP_REF"


class TestCommentPatch:
    def test_the_comment_lands_in_the_prompt_the_worker_reads(self) -> None:
        """An annotation only a reviewer reads would leave the step re-running the text the
        reviewer objected to."""
        patch = revision.comment_patch(_spec()["root"], "draft", "be terser")
        assert patch is not None and patch.op == "replace"
        assert "be terser" in patch.node["config"]["prompt"]
        assert patch.node["config"]["prompt"].startswith("w")  # the original survives

    def test_the_comment_is_marked_so_it_reads_as_a_correction(self) -> None:
        patch = revision.comment_patch(_spec()["root"], "draft", "be terser")
        assert revision.REVISE_MARKER in patch.node["config"]["prompt"]

    def test_a_promptless_step_takes_the_note_alone(self) -> None:
        """Inventing a `prompt` key on a node whose kind never reads one would be a silent
        no-op dressed as an edit."""
        patch = revision.comment_patch(_spec()["root"], "approve", "ask more clearly")
        assert patch is not None
        # The gate's own `prompt` is the QUESTION it asks, not an instruction a worker
        # follows, so it is left exactly as authored.
        assert patch.node["config"]["prompt"] == "Ship it?"
        assert patch.node["extra"]["review_notes"][0]["comment"] == "ask more clearly"

    def test_an_empty_comment_produces_no_patch(self) -> None:
        assert revision.comment_patch(_spec()["root"], "draft", "   ") is None


# ── the resume path ──────────────────────────────────────────────────────────


class TestReviseOnAWaitingGate:
    async def test_a_revise_patches_one_step_and_leaves_the_rest_untouched(self) -> None:
        c, token = await _blocked()
        result = c.resume(token, _revise("draft", "be terser"))
        assert result["ok"] and result["revised"] and result["step_ref"] == "draft"
        children = c.spec["root"]["children"]
        assert "be terser" in children[0]["config"]["prompt"]
        # The gate itself — the node nobody complained about — is byte-identical.
        assert children[1] == _spec()["root"]["children"][1]

    async def test_a_revise_is_not_an_approval(self) -> None:
        """The distinction the whole verb rests on: the reviewer has not decided yet."""
        c, token = await _blocked()
        result = c.resume(token, _revise("draft", "be terser"))
        assert result["approved"] is False
        assert not [e for e in J.ledger(c.run.id) if e.get("kind") == J.GATE_RESOLVED]

    async def test_a_revise_does_not_count_as_a_said_no(self) -> None:
        """Folding it into `gate_resolved` would report a reviewer who asked for a wording
        change as one who declined the work."""
        c, token = await _blocked()
        c.resume(token, _revise("draft", "be terser"))
        stats = introspection.gate_stats(J.ledger(c.run.id))
        assert stats.get("approve") is None or stats["approve"].rejects == 0

    async def test_the_gate_re_asks_rather_than_holding_its_answer(self) -> None:
        """awaiting_review → running, in the engine's own state names: the gate goes back
        to PENDING so it asks against the revised step."""
        c, token = await _blocked()
        assert c.instances["root.children[1]"].state == InstanceState.WAITING
        c.resume(token, _revise("draft", "be terser"))
        assert c.instances["root.children[1]"].state == InstanceState.PENDING
        assert c.run.status == RunStatus.RUNNING

    async def test_the_run_leaves_needs_input(self) -> None:
        c, token = await _blocked()
        assert c.run.status == RunStatus.NEEDS_INPUT
        c.resume(token, _revise("draft", "be terser"))
        assert c.run.status == RunStatus.RUNNING

    async def test_the_revise_is_journaled_as_its_own_kind(self) -> None:
        c, token = await _blocked()
        c.resume(token, _revise("draft", "be terser"))
        revised = [e for e in J.ledger(c.run.id) if e.get("kind") == J.GATE_REVISED]
        assert len(revised) == 1
        assert revised[0]["step_ref"] == "draft" and revised[0]["comment"] == "be terser"

    async def test_a_second_revise_off_one_ask_is_refused(self) -> None:
        """A revise answers the gate as surely as an approval does; a live token would let
        the second land on an already-revised step."""
        c, token = await _blocked()
        assert c.resume(token, _revise("draft", "a"))["ok"]
        assert c.resume(token, _revise("draft", "b"))["code"] == "WF_RESUME_UNKNOWN_TOKEN"


class TestARejectedReviseKeepsTheToken:
    """Every refusal happens before the token is consumed — the ordering
    `controller.resume` documents, extended to the new verb."""

    async def test_an_unknown_step_ref_leaves_the_token_answerable(self) -> None:
        c, token = await _blocked()
        bad = c.resume(token, _revise("ghost", "be terser"))
        assert bad["code"] == "WF_REVISE_UNKNOWN_STEP"
        assert c.resume(token, True)["ok"]

    async def test_a_missing_comment_leaves_the_token_answerable(self) -> None:
        c, token = await _blocked()
        bad = c.resume(token, _revise("draft", "   "))
        assert bad["code"] == "WF_REVISE_NO_COMMENT"
        assert c.resume(token, True)["ok"]

    async def test_a_missing_step_ref_leaves_the_token_answerable(self) -> None:
        c, token = await _blocked()
        bad = c.resume(token, _revise("", "be terser"))
        assert bad["code"] == "WF_REVISE_NO_STEP_REF"
        assert c.resume(token, True)["ok"]

    async def test_a_stale_epoch_revise_is_refused(self) -> None:
        c, token = await _blocked()
        cont = HI.load_continuation(c.run.id, token)
        c.instances[cont.instance_path].epoch = 5
        assert c.resume(token, _revise("draft", "x"))["code"] == "WF_RESUME_STALE_EPOCH"

    async def test_an_unknown_token_is_refused_before_any_spec_write(self) -> None:
        c, _token = await _blocked()
        before = store.read_spec(c.run.id)
        assert c.resume("nope", _revise("draft", "x"))["code"] == "WF_RESUME_UNKNOWN_TOKEN"
        assert store.read_spec(c.run.id) == before


class TestWhatRunsMatchesWhatWasRecorded:
    """The substantive correctness clause. The revision is written ONCE, by
    `_commit_mutation`, so the executing spec and the persisted one cannot diverge."""

    async def test_the_spec_on_disk_equals_the_spec_the_engine_runs(self) -> None:
        c, token = await _blocked()
        c.resume(token, _revise("draft", "be terser"))
        on_disk = store.read_spec(c.run.id)
        assert on_disk == c.spec
        assert "be terser" in on_disk["root"]["children"][0]["config"]["prompt"]
        # And the tree the scheduler walks is the same document, not a stale parse.
        assert "be terser" in c.root.children[0].config["prompt"]

    async def test_the_recorded_ops_hash_the_spec_that_landed(self) -> None:
        """`spec_history`'s hash is what lets a reader confirm the recorded edit produced
        the spec on disk — the audit half of "what was approved is what runs"."""
        c, token = await _blocked()
        c.resume(token, _revise("draft", "be terser"))
        record = json.loads(
            (
                store.run_dir(c.run.id) / "spec_history" / f"v{c.run.spec_version:03d}.json"
            ).read_text(encoding="utf-8")
        )
        assert record["spec_hash"] == J.hash_value(store.read_spec(c.run.id))
        assert record["raw_ops"][0]["op"] == "revise"
        assert record["raw_ops"][0]["comment"] == "be terser"

    async def test_the_spec_version_advances_so_a_stale_editor_is_caught(self) -> None:
        c, token = await _blocked()
        before = c.run.spec_version
        c.resume(token, _revise("draft", "be terser"))
        assert c.run.spec_version == before + 1

    async def test_the_edit_is_journaled_for_the_refiner(self) -> None:
        """A hand-fix is the learning signal `refiner` clusters on; a revision that skipped
        it would be an edit no template ever learns from."""
        c, token = await _blocked()
        c.resume(token, _revise("draft", "be terser"))
        edits = [e for e in J.ledger(c.run.id) if e.get("kind") == J.USER_EDITED_MID_FLIGHT]
        assert len(edits) == 1 and edits[0]["ops"][0]["node_id"] == "draft"

    async def test_the_revised_step_does_not_serve_its_cached_output(self) -> None:
        """The re-armed-gate risk, answered by a test: the cache key hashes the node's own
        spec region, and the revision changed it — so the old entry cannot match."""
        c, token = await _blocked()
        from personalclaw.workflows.journal import spec_region_hash

        before = spec_region_hash(c.root.children[0].to_dict())
        c.resume(token, _revise("draft", "be terser"))
        assert spec_region_hash(c.root.children[0].to_dict()) != before

    async def test_the_journal_kind_reaches_the_ledger(self) -> None:
        assert J.GATE_REVISED in J.LEDGER_KINDS
