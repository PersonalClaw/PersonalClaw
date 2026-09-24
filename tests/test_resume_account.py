"""CE2-10 — the resumed session's DERIVED account of what already happened.

The defect under test is behavioural, not cosmetic: a resumed turn re-runs a step the record
already shows finished. So the assertions here are about what a resumed turn is TOLD and what it
is refused, never about "the renderer produced a string".

Three of these tests are the atom's load-bearing negatives and are written so that the obvious
wrong implementation goes red:

* ``test_a_failed_step_is_never_reported_as_done`` — fold ``step_failed`` into ``done`` and it reds.
* ``test_nothing_reaches_the_account_that_is_not_in_a_record`` — compose the account from prose and
  it reds.
* ``test_a_record_contradicting_the_tree_refuses_the_resume`` — let an inconsistent tree proceed and
  it reds.
* ``test_the_account_survives_a_compaction_verbatim`` — drop the block from the carried set and it
  reds.

The ledger halves drive the REAL ``workflows.journal.Journal`` emitters rather than hand-written
dicts, because the reader is only worth anything if it reads what the writer writes — a reader of a
key nothing emits is the inert-control shape, and hand-rolled fixtures cannot tell the two apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw import resume_account as ra
from personalclaw import turn_checkpoints as tc
from personalclaw.context import ContextBuilder
from personalclaw.context_compaction import compact, is_resume_account
from personalclaw.context_headroom import count_tokens
from personalclaw.ledger.reader import read_events
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import ownership
from personalclaw.workflows import store as run_store
from personalclaw.workflows.models import (
    Failure,
    FailureClass,
    InstanceState,
    WorkflowRun,
)

# The five-step task every resume test interrupts. Steps 1-3 finished, step 4 FAILED, step 5 was
# never reached — so the only correct place for a resumed turn to continue is step 4.
PLAN = ("fetch/inputs", "transform/rows", "write/report", "publish/upload", "notify/owner")
INTERRUPTED_AT = "publish/upload"


def _record_interrupted_run() -> str:
    """Drive the real journal emitters for the interrupted task; return the run id."""
    run = run_store.create(WorkflowRun(id="", workflow_name="ce210-interrupted"))
    j = journal_mod.Journal(run.id)
    for i, step in enumerate(PLAN[:3]):
        j.step_started(step, step.split("/")[-1], epoch=1, lane="main")
        j.step_completed(
            step,
            step.split("/")[-1],
            epoch=1,
            cache_key=f"{step}|1|k{i}",
            state=InstanceState.DONE,
        )
    j.step_started(INTERRUPTED_AT, "upload", epoch=1, lane="main")
    j.step_failed(
        INTERRUPTED_AT,
        "upload",
        epoch=1,
        failure=Failure(
            failure_class=FailureClass.NETWORK,
            cause_plain="the endpoint returned 503",
        ),
        attempt=1,
        retries_exhausted=True,
    )
    # PLAN[4] is deliberately never journaled: an un-attempted step must be absent, not "pending".
    return run.id


def _account_for_run(run_id: str) -> ra.ResumeAccount:
    return ra.derive_account(
        ledger_events=read_events(run_store, run_id, kinds=set(ra.ACCOUNT_KINDS)),
        tool_messages=ra.NOT_CONSULTED,
        checkpoint_entries=ra.NOT_CONSULTED,
    )


def _subjects(account: ra.ResumeAccount, status: str) -> set[str]:
    return {f.subject for f in account.by_status(status)}


# ── the behavioural claim: a resumed turn continues at the correct step ──


def test_a_resumed_interrupted_task_points_at_the_step_that_did_not_finish():
    """The atom's stated validation: resume a real interrupted multi-step task and assert the
    record the next turn reads identifies the correct step to continue at.

    Asserted on the record's own structure rather than on prose, because that is what the next
    turn acts on: the first plan step the account does NOT show as done is the resume point.
    """
    run_id = _record_interrupted_run()
    account = _account_for_run(run_id)

    done = _subjects(account, "done")
    assert done == set(PLAN[:3]), f"the ledger's three finished steps should be done, got {done}"

    resume_point = next(step for step in PLAN if step not in done)
    assert resume_point == INTERRUPTED_AT, (
        "the resumed turn must continue at the step that did not finish, not at the first "
        f"step and not past the failure — got {resume_point}"
    )
    # And the step that was never attempted is absent entirely: inventing a status for it would
    # be the same fabrication as inventing a completion.
    assert PLAN[4] not in {f.subject for f in account.facts}


# ── the load-bearing negative: FAILED is never folded into done ──


def test_a_failed_step_is_never_reported_as_done():
    """A step recorded attempted-and-failed is carried as FAILED.

    This is the assertion the atom calls out: a false completion is strictly worse than a
    forgotten one. Mapping ``step_failed`` to ``done`` in ``_facts_from_ledger`` reds every
    branch below.
    """
    account = _account_for_run(_record_interrupted_run())

    assert INTERRUPTED_AT in _subjects(account, "failed")
    assert INTERRUPTED_AT not in _subjects(account, "done")

    rendered = ra.render_account(account)
    failed_lines = [ln for ln in rendered.splitlines() if INTERRUPTED_AT in ln]
    assert failed_lines, "the failed step must appear in the rendered account"
    for line in failed_lines:
        assert "FAILED" in line, f"the failed step must be labelled FAILED, got: {line}"
        assert "DONE" not in line, f"the failed step must never read as done, got: {line}"
    assert "the endpoint returned 503" in rendered, "the recorded cause is part of the fact"


def test_a_tool_call_with_no_recorded_result_is_attempted_not_done():
    """The interrupted case. A call whose result was never recorded is the one a resumed model most
    wants to call finished, so it is the one that must be labelled ATTEMPTED."""
    account = ra.derive_account(
        ledger_events=ra.NOT_CONSULTED,
        checkpoint_entries=ra.NOT_CONSULTED,
        tool_messages=[
            {
                "role": "assistant",
                "content": "publishing",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "shell", "arguments": '{"cmd": "make dist"}'}}
                ],
            },
            # no {"role": "tool", "tool_call_id": "c1"} — the turn died mid-call
        ],
    )
    assert _subjects(account, "attempted") == {"shell"}
    assert not account.by_status("done")
    assert "ATTEMPTED" in ra.render_account(account)


def test_a_denied_tool_call_is_neither_done_nor_failed():
    """WF2LEA-13's distinction, reused rather than re-derived: a denial is a standing policy, not
    a broken tool. Either way it is not a completion."""
    from personalclaw import security

    _recoverable, observation = security.classify_denial(
        "hard_denylist", "matched the deny list", "shell"
    )
    assert security.is_denial_observation(observation), "the fixture must be a real denial"
    account = ra.derive_account(
        ledger_events=ra.NOT_CONSULTED,
        checkpoint_entries=ra.NOT_CONSULTED,
        tool_messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "shell", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": observation},
        ],
    )
    assert _subjects(account, "denied") == {"shell"}
    assert not account.by_status("done")


def test_an_effect_that_only_attempted_is_not_a_completion():
    """The ledger's own words: an ATTEMPTED effect is "unknown, possibly fired". Reading it as
    done is exactly the invented completion the account forbids."""
    run = run_store.create(WorkflowRun(id="", workflow_name="ce210-effect"))
    j = journal_mod.Journal(run.id)
    j.effect("send/mail", idempotency_key="k1", effect_status="attempted", node_id="mail")
    j.effect("write/db", idempotency_key="k2", effect_status="committed", node_id="db")
    j.effect("post/hook", idempotency_key="k3", effect_status="compensated", node_id="hook")
    account = _account_for_run(run.id)
    assert _subjects(account, "attempted") == {"send/mail"}
    assert _subjects(account, "done") == {"write/db"}
    assert _subjects(account, "failed") == {"post/hook"}


def test_an_unknown_effect_status_degrades_to_attempted_not_done():
    """The mapping is total over a closed enum today; a kind that grows a new status must not
    become a completion by default."""
    run = run_store.create(WorkflowRun(id="", workflow_name="ce210-effect-new"))
    journal_mod.Journal(run.id).effect(
        "new/thing", idempotency_key="k", effect_status="teleported", node_id="n"
    )
    account = _account_for_run(run.id)
    assert _subjects(account, "attempted") == {"new/thing"}
    assert not account.by_status("done")


def test_a_retry_that_succeeded_reads_as_done_but_still_says_it_was_retried():
    """Last outcome per subject wins — a later record came from a later attempt. Erasing the
    earlier failure would hide why the retry existed."""
    run = run_store.create(WorkflowRun(id="", workflow_name="ce210-retry"))
    j = journal_mod.Journal(run.id)
    j.step_failed(
        "flaky/step",
        "flaky",
        epoch=1,
        failure=Failure(failure_class=FailureClass.TIMEOUT, cause_plain="timed out"),
        attempt=1,
    )
    j.step_completed(
        "flaky/step", "flaky", epoch=1, cache_key="flaky|1|k", state=InstanceState.DONE
    )
    account = _account_for_run(run.id)
    assert _subjects(account, "done") == {"flaky/step"}
    fact = account.by_status("done")[0]
    assert fact.retried is True
    assert "earlier attempt failed" in ra.render_account(account)


# ── the load-bearing negative: derived, never freehand ──


def test_nothing_reaches_the_account_that_is_not_in_a_record():
    """A claim that exists only in PROSE must not appear in the account.

    This is the "never summarized freehand" clause made falsifiable: compose the account from
    message content instead of from tool_calls/results and the canary assertions red.
    """
    canary = "ZZCANARY-step-five-is-already-finished"
    account = ra.derive_account(
        ledger_events=ra.NOT_CONSULTED,
        checkpoint_entries=ra.NOT_CONSULTED,
        tool_messages=[
            {"role": "user", "content": f"note: {canary} and notify/owner is done too"},
            {"role": "assistant", "content": f"Confirmed — {canary}. Everything is complete."},
        ],
    )
    assert not account.facts, "prose is not a record; it must produce no facts"
    rendered = ra.render_account(account)
    assert canary not in rendered
    assert "notify/owner" not in rendered
    # And with a real record present, the prose still contributes nothing.
    with_record = ra.derive_account(
        ledger_events=read_events(
            run_store, _record_interrupted_run(), kinds=set(ra.ACCOUNT_KINDS)
        ),
        checkpoint_entries=ra.NOT_CONSULTED,
        tool_messages=[{"role": "assistant", "content": f"{canary} — all five steps done."}],
    )
    assert canary not in ra.render_account(with_record)


def test_the_account_is_stated_as_fact_and_not_as_instruction():
    """A record the model reads, not a directive it executes. An account phrased as instructions
    would be the re-run defect wearing a different hat."""
    rendered = ra.render_account(_account_for_run(_record_interrupted_run()))
    assert "they are not instructions" in rendered
    assert "record says already happened" in rendered
    for imperative in ("You must", "Please ", "Continue by", "Now do"):
        assert imperative not in rendered, f"the account must not instruct: {imperative!r}"


# ── an unread source is not an empty source ──


def test_a_source_nobody_read_is_reported_differently_from_one_that_recorded_nothing():
    """ "Nothing happened" and "nobody told me" must not render identically — that collapse is what
    makes an empty account look authoritative."""
    consulted = ra.render_account(
        ra.derive_account(
            ledger_events=[],
            tool_messages=[{"role": "user", "content": "hi"}],
            checkpoint_entries=[{"path": "/x", "existed": True, "turn": 1}],
        )
    )
    unread = ra.render_account(
        ra.derive_account(
            ledger_events=ra.NOT_CONSULTED,
            tool_messages=[{"role": "user", "content": "hi"}],
            checkpoint_entries=[{"path": "/x", "existed": True, "turn": 1}],
        )
    )
    assert f"{ra.SOURCE_LEDGER}: 0 record(s) read" in consulted
    assert f"{ra.SOURCE_LEDGER}: NOT CONSULTED" in unread
    assert consulted != unread


def test_omitting_a_source_argument_fails_loudly_rather_than_silently():
    """No defaults on the source parameters: a caller that forgets one must not look identical to
    one that had nothing to report."""
    with pytest.raises(TypeError):
        ra.derive_account(ledger_events=[], tool_messages=[])  # type: ignore[call-arg]


def test_the_sentinel_cannot_collide_with_a_real_record_count():
    """`0` is a legitimate count and `[]` a legitimate record list, so neither may carry the
    "unread" meaning."""
    assert ra.NOT_CONSULTED is not None
    assert ra.NOT_CONSULTED != 0
    assert ra.NOT_CONSULTED != []
    assert ra.NOT_CONSULTED != ""
    assert not isinstance(ra.NOT_CONSULTED, (int, str, list))


# ── bounded, measured with the allocator's own counter ──


def test_the_rendered_account_stays_inside_its_declared_bound():
    """A thousand long-named facts still fit the bound, and the truncation is ANNOUNCED — a
    silently shortened account is a forgotten completion nothing says was forgotten."""
    run = run_store.create(WorkflowRun(id="", workflow_name="ce210-huge"))
    j = journal_mod.Journal(run.id)
    for i in range(1000):
        j.step_completed(
            f"very/deeply/nested/path/segment/{i}/" + "x" * 200,
            "n" * 200,
            epoch=1,
            cache_key=f"k{i}",
            state=InstanceState.DONE,
        )
    account = _account_for_run(run.id)
    rendered = ra.render_account(account)

    assert len(rendered) <= ra.MAX_ACCOUNT_CHARS, f"{len(rendered)} chars exceeds the cap"
    # The ALLOCATOR's counter (context_headroom delegates to learning.surfacing) — this module
    # adds no second one, so the bound is expressed in the same tokens the headroom contract uses.
    measured = count_tokens(rendered)
    assert measured <= ra.MAX_ACCOUNT_TOKENS, f"{measured} tokens exceeds the declared ceiling"
    assert account.omitted_facts > 0
    assert "omitted" in rendered
    assert rendered.startswith(ra.FENCE_START)
    assert rendered.rstrip().endswith(ra.FENCE_END), "truncation must never eat the closing fence"


def test_an_account_with_nothing_recorded_renders_nothing():
    """An always-present empty block is prompt weight that trains the reader to skip the fence."""
    assert (
        ra.render_account(
            ra.derive_account(ledger_events=[], tool_messages=[], checkpoint_entries=[])
        )
        == ""
    )


# ── the load-bearing negative: it survives compaction, beside the live task ──


def _folded_conversation(account_block: str | None) -> list[dict]:
    msgs: list[dict] = [{"role": "user", "content": f"head {i}"} for i in range(3)]
    if account_block is not None:
        msgs.append({"role": "user", "content": account_block})
    for i in range(6):
        msgs.append(
            {
                "role": "assistant",
                "content": f"middle {i}",
                "tool_calls": [
                    {
                        "id": f"m{i}",
                        "function": {"name": "file_write", "arguments": f'{{"path": "mid{i}.py"}}'},
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"m{i}", "content": "ok " * 400})
    msgs += [{"role": "user", "content": f"tail {i}"} for i in range(8)]
    return msgs


def test_the_account_survives_a_compaction_verbatim():
    """A block already in the history is CARRIED, not folded into the summary.

    Its facts came from messages the compaction has already discarded, so it cannot be
    re-derived — dropping it from the carried set loses them permanently, which is why this is a
    load-bearing assertion rather than a nice-to-have.
    """
    block = ra.render_account(_account_for_run(_record_interrupted_run()))
    assert block, "the fixture must produce a real account or this test proves nothing"
    before = _folded_conversation(block)
    assert any(is_resume_account(m) for m in before[3:-8]), (
        "the account must start INSIDE the folded region — if it sat in the protected head this "
        "test would pass without the carry rule existing at all"
    )

    after = compact(before, protect_head=3, protect_tail=8)

    carried = [m for m in after if block in str(m.get("content", ""))]
    assert len(carried) == 1, "the account must survive compaction exactly once, verbatim"
    assert INTERRUPTED_AT in carried[0]["content"]
    assert "FAILED" in carried[0]["content"]


def test_compaction_does_not_let_the_account_evict_the_live_task():
    """Bounded AND non-evicting: the verbatim tail is what the turn is acting on, so it must come
    through untouched no matter how much account weight rides along."""
    block = ra.render_account(_account_for_run(_record_interrupted_run()))
    before = _folded_conversation(block)
    tail_before = before[-8:]

    after = compact(before, protect_head=3, protect_tail=8)

    assert after[-8:] == tail_before, "the live task's messages must be byte-identical"
    assert after[:3] == before[:3], "the protected head must be byte-identical"
    account_chars = sum(len(str(m.get("content", ""))) for m in after if is_resume_account(m))
    assert account_chars <= 2 * ra.MAX_ACCOUNT_CHARS, "carried + fresh is the declared ceiling"


def test_compaction_derives_a_fresh_account_for_the_region_it_folds():
    """A compacted session carries an account even when it had none before: the folded region's
    tool_calls/results are recorded facts, and leaving them to the summary is the defect."""
    before = _folded_conversation(None)
    after = compact(before, protect_head=3, protect_tail=8)
    fresh = [m for m in after if is_resume_account(m)]
    assert len(fresh) == 1
    assert "file_write" in fresh[0]["content"]
    assert ra.SOURCE_TOOL_HISTORY in fresh[0]["content"]


def test_compaction_reads_the_original_tool_results_not_the_pruned_digests():
    """`prune_tool_outputs` rewrites a long result to `[pruned tool result — …]`, which does not
    start with `Error:`. Deriving from the pruned copy would turn a FAILED call into a done one —
    a false completion manufactured by the compressor itself."""
    from personalclaw.context_compaction import (
        _KEEP_RECENT_TOOL_RESULTS,
        _TOOL_RESULT_PRUNE_OVER,
        prune_tool_outputs,
    )

    long_error = "Error: the build exploded\n" + ("stack frame\n" * 200)
    assert len(long_error) > _TOOL_RESULT_PRUNE_OVER, "the fixture must exceed the threshold"
    msgs: list[dict] = [{"role": "user", "content": f"head {i}"} for i in range(3)]
    msgs.append(
        {
            "role": "assistant",
            "content": "building",
            "tool_calls": [{"id": "b1", "function": {"name": "shell", "arguments": "{}"}}],
        }
    )
    msgs.append({"role": "tool", "tool_call_id": "b1", "content": long_error})
    # The pre-pass keeps the most recent `_KEEP_RECENT_TOOL_RESULTS` results FULL, so the error
    # must be pushed out of that window or the pruned and original copies are identical and this
    # test proves nothing. Vacuity is asserted below, not assumed.
    for i in range(_KEEP_RECENT_TOOL_RESULTS + 1):
        msgs.append(
            {
                "role": "assistant",
                "content": f"later {i}",
                "tool_calls": [
                    {"id": f"l{i}", "function": {"name": "read_file", "arguments": "{}"}}
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"l{i}", "content": "y" * 900})
    msgs += [{"role": "user", "content": f"tail {i}"} for i in range(8)]

    pruned = prune_tool_outputs(msgs)
    assert not str(pruned[4]["content"]).startswith("Error:"), (
        "the pre-pass must actually have digested the error result — otherwise the pruned and "
        "original copies agree and this test is vacuous"
    )

    after = compact(msgs, protect_head=3, protect_tail=8)
    fresh = [m for m in after if is_resume_account(m)]
    assert len(fresh) == 1
    body = fresh[0]["content"]
    shell_lines = [ln for ln in body.splitlines() if "shell" in ln]
    assert shell_lines
    for line in shell_lines:
        assert "FAILED" in line, f"a long error result must still read as FAILED, got: {line}"


# ── the load-bearing negative: an inconsistent tree refuses the resume ──


def _checkpoint_a_write(session_key: str, target: Path, ws: Path) -> None:
    """Record a real pre-edit capture through the live store, then mutate the file."""
    assert tc.capture_pre_edit(session_key, target, cwd=ws) == "captured"
    target.write_text("mutated\n", encoding="utf-8")


def test_a_record_contradicting_the_tree_refuses_the_resume(tmp_path: Path):
    """The record says a file exists; it does not. The resume STOPS.

    A refusal, not a warning: continuing would hand the model an account it has already been
    shown to be wrong about. Letting the inconsistent case proceed reds this test.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "report.md"
    target.write_text("original\n", encoding="utf-8")
    key = "dashboard:ce210-inconsistent"
    _checkpoint_a_write(key, target, ws)
    assert any(
        e["path"] == str(target) and e.get("existed") is True for e in tc.recorded_file_entries(key)
    ), "the live checkpoint store must hold the claim, or the refusal is testing nothing"

    # Consistent first: the same record against the tree it describes must NOT refuse.
    ra.verify_resume_state(session_key=key, tree_root=ws, tool_messages=ra.NOT_CONSULTED)

    target.unlink()  # the tree moved under the record

    with pytest.raises(ra.ResumeStateInconsistent) as excinfo:
        ra.verify_resume_state(session_key=key, tree_root=ws, tool_messages=ra.NOT_CONSULTED)
    assert "report.md" in str(excinfo.value)
    assert excinfo.value.reasons


def test_a_recorded_create_that_never_landed_does_not_refuse(tmp_path: Path):
    """`existed: False` records a CREATE. If that write then failed the file is legitimately
    absent, and refusing over it would stop a resume for work that correctly did not happen."""
    ws = tmp_path / "ws"
    ws.mkdir()
    key = "dashboard:ce210-create"
    assert tc.capture_pre_edit(key, ws / "brand-new.py", cwd=ws) == "absent"
    ra.verify_resume_state(session_key=key, tree_root=ws, tool_messages=ra.NOT_CONSULTED)


def test_a_recorded_delete_does_not_refuse():
    """A deleted file is legitimately gone; presence-checking it would refuse every resume that
    tidied up after itself."""
    account = ra.derive_account(
        ledger_events=ra.NOT_CONSULTED,
        checkpoint_entries=ra.NOT_CONSULTED,
        tool_messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "d1",
                        "function": {
                            "name": "delete_file",
                            "arguments": '{"path": "gone.py"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "d1", "content": "removed"},
        ],
    )
    assert account.files_deleted == ("gone.py",)
    assert account.expected_present() == ()
    assert ra.check_tree_consistency(account, "/nonexistent-root-for-ce210") == ()


def test_a_failed_write_is_not_a_presence_claim():
    """A write whose recorded result was an error never enters the presence set, so a rolled-back
    attempt cannot manufacture a refusal."""
    account = ra.derive_account(
        ledger_events=ra.NOT_CONSULTED,
        checkpoint_entries=ra.NOT_CONSULTED,
        tool_messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "w1",
                        "function": {"name": "file_write", "arguments": '{"path": "never.py"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "w1", "content": "Error: permission denied"},
        ],
    )
    assert _subjects(account, "failed") == {"file_write"}
    assert account.files_written == ()
    assert account.expected_present() == ()


def test_a_path_outside_the_tree_root_is_not_evidence_about_this_tree(tmp_path: Path):
    """A tool may legitimately have written /tmp. Counting that as a contradiction would refuse
    resumes over files the tree never owned."""
    ws = tmp_path / "ws"
    ws.mkdir()
    account = ra.derive_account(
        ledger_events=ra.NOT_CONSULTED,
        checkpoint_entries=[
            {"path": "/definitely/not/under/ws/gone.txt", "existed": True, "turn": 1}
        ],
        tool_messages=ra.NOT_CONSULTED,
    )
    assert account.files_touched_existing
    assert ra.check_tree_consistency(account, ws) == ()


def test_a_manifest_without_an_existed_key_is_not_read_as_a_negative(tmp_path: Path):
    """An absent key is an unrecorded fact, not a false one. Truthiness here would silently shrink
    the refusal's reach."""
    account = ra.derive_account(
        ledger_events=ra.NOT_CONSULTED,
        checkpoint_entries=[{"path": str(tmp_path / "unknown.txt"), "turn": 1}],
        tool_messages=ra.NOT_CONSULTED,
    )
    assert account.files_touched_existing == ()
    assert account.by_status("attempted"), "the entry is still recorded as an attempt"


# ── the assembly seam: the call site, not just the function ──


def _builder(tmp_path: Path) -> ContextBuilder:
    return ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "mem"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )


def test_the_resumed_turns_prompt_carries_the_account_as_a_refusable_component(tmp_path: Path):
    """Asserts the CALL SITE and the outcome: a resumed `build_message` labels the account as its
    own component, marks it non-compressible, and the FAILED step is in the text that ships."""
    run_id = _record_interrupted_run()
    key = ownership.owned_key(run_id, "upload")
    components: list = []
    message, _ = _builder(tmp_path).build_message(
        "carry on",
        True,
        session_key=key,
        resumed=True,
        cwd=str(tmp_path),
        components_out=components,
    )
    named = [c for c in components if c.name == "record of already-completed work"]
    assert named, f"no account component in {[c.name for c in components]}"
    assert named[0].compressible is False, "trimming the account is dropping a completion"
    # Asserted on the STEP'S OWN LINE, not on the presence of the word: the block's preamble
    # explains what FAILED means, so `"FAILED" in text` passes even when the failed step has been
    # relabelled done — a vacuous assertion that would have hidden the mutation.
    interrupted_lines = [ln for ln in named[0].text.splitlines() if INTERRUPTED_AT in ln]
    assert interrupted_lines, f"the interrupted step is missing from {named[0].text}"
    for line in interrupted_lines:
        assert "FAILED" in line and "DONE" not in line, line
    assert ra.FENCE_START in message, "the account must reach the prompt that is really sent"
    assert any(PLAN[0] in ln and "DONE" in ln for ln in named[0].text.splitlines())


def test_the_fence_survives_prompt_assembly(tmp_path: Path):
    """The carry rule matches on the fence, and the prompt path REWRITES multibyte punctuation.

    `build_message` runs the assembled prompt through `context._MULTIBYTE_TABLE` (em dash → `--`),
    so a fence containing one would reach the history in a form `is_resume_account` cannot match:
    the block would be folded into the summary and the carry rule would never fire while looking
    perfectly implemented. This is the vacuity guard for that.
    """
    from personalclaw.context import _MULTIBYTE_TABLE

    assert ra.FENCE_START.translate(_MULTIBYTE_TABLE) == ra.FENCE_START
    assert ra.FENCE_END.translate(_MULTIBYTE_TABLE) == ra.FENCE_END

    run_id = _record_interrupted_run()
    message, _ = _builder(tmp_path).build_message(
        "carry on",
        True,
        session_key=ownership.owned_key(run_id, "upload"),
        resumed=True,
        cwd=str(tmp_path),
    )
    # The end-to-end proof: the block as it really ships is one the carrier recognises.
    assert is_resume_account({"content": message})


def test_a_non_resumed_turn_does_not_carry_an_account(tmp_path: Path):
    """The block is scoped to the branch that created the defect. A first turn has nothing to
    resume, and injecting the block there would spend prompt weight on every session."""
    run_id = _record_interrupted_run()
    components: list = []
    _builder(tmp_path).build_message(
        "start",
        True,
        session_key=ownership.owned_key(run_id, "upload"),
        resumed=False,
        cwd=str(tmp_path),
        components_out=components,
    )
    assert not [c for c in components if c.name == "record of already-completed work"]


def test_the_assembly_seam_refuses_rather_than_injecting_a_contradicted_account(tmp_path: Path):
    """The stop reaches the seam that builds the prompt, not just the checker."""
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "doomed.md"
    target.write_text("x\n", encoding="utf-8")
    key = "dashboard:ce210-seam"
    _checkpoint_a_write(key, target, ws)
    target.unlink()

    with pytest.raises(ra.ResumeStateInconsistent):
        _builder(tmp_path).build_message(
            "carry on", True, session_key=key, resumed=True, cwd=str(ws)
        )


def test_a_session_with_no_run_says_the_ledger_was_not_consulted(tmp_path: Path):
    """A plain chat key is not run-owned, so there is no ledger to read. Saying NOT CONSULTED is
    honest; rendering "0 records" would assert a run recorded nothing."""
    assert ra.ledger_events_for_session("dashboard:plain") is ra.NOT_CONSULTED
    events = ra.ledger_events_for_session(ownership.owned_key(_record_interrupted_run(), "upload"))
    assert events is not ra.NOT_CONSULTED
    assert events, "a run-owned key must reach the run's real ledger"
