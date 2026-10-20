"""A workflow reads a boolean an agent writes as text as the word it spells.

An agent writes a workflow's definitions and its edits, and a boolean as text as often as not;
``bool("false")`` is True. So:

* a step configured ``"redo_effects": "false"`` fired its external effect a second time when the
  run was rewound, the double-fire the effect ledger exists to stop;
* a rewind op sent ``"force": "false"`` threw away the region's cached outputs and ran it again,
  and ``"redo_effects": "false"`` went into the run's history as a request to fire its effects
  again;
* ``"require_hitl": "true"`` asked nobody: an identity check read the word as no;
* ``"allow_failure": "false"`` let what follows a failed check run anyway;
* ``"self_judge": "false"`` let a stage be judged in its own session;
* ``"persists_memory": "true"`` let a run that may write no memory write it;
* ``"judge_contract": "false"`` put a stage under the judge contract;
* a verify criterion ``"hard": "false"`` failed the run;
* the loop judge's ``"done": "true"`` read as not done, so a finished loop kept running cycles.

Each is read through ``safety_flags.yes_or_no`` with the safe value of what it guards. A rewind
request is held stricter, as every request body is (``request_validation.bool_field``); it is a
door of ``test_a_request_boolean_is_a_json_boolean``.

The spellings are rows of the one table in ``test_safety_flags_reject_truthy_strings``. Each
setting here is read through its real reader three times, once per answer, each a row of one of
the three tests below.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import NamedTuple

import pytest
from table_rows import row
from test_safety_flags_reject_truthy_strings import WORD_NEITHER, WORD_NO, WORD_YES

from personalclaw.workflows import mutations

pytestmark = pytest.mark.security


# ── an edit op: redo_effects and force ───────────────────────────────────────────────────────


def _rewind_op(flag: str) -> Callable[[object], object]:
    def read(sent: object) -> object:
        """What the op asks for, and whether the run's history records it."""
        op = mutations.Op.from_dict({"op": "rewind", "node_id": "publish", flag: sent})
        asked = getattr(op, flag)
        assert (flag in op.to_dict()) is asked, "the history records a request nobody made"
        return asked

    return read


def _rewind_epoch(sent: object) -> int:
    """What `force` decides: a forced rewind starts a new epoch, so nothing cached is reused."""
    from personalclaw.workflows.models import NodeInstance

    instances = {"root.children[0]": NodeInstance(path="root.children[0]", epoch=2)}
    op = mutations.Op.from_dict({"op": "rewind", "node_id": "publish", "force": sent})
    return mutations.next_epoch(instances, ["root.children[0]"], force=op.force)


# ── a step's redo_effects: the boundary on an effect already committed ─────────────────────────


def _redo_blocked(sent: object) -> bool:
    from personalclaw.workflows.effects import EffectRecord, redo_blocked

    committed = EffectRecord(idempotency_key="k", epoch=0)
    return redo_blocked({"redo_effects": sent}, committed, epoch=1)


# ── what a definition's steps declare ────────────────────────────────────────────────────────


def _requires_hitl(sent: object) -> bool:
    from personalclaw.workflows.confirmation import requires_hitl

    return requires_hitl({"require_hitl": sent})


def _attention(sent: object) -> str:
    """The autonomy plan reads the step's gate the same way."""
    from personalclaw.workflows.autonomy import type_attention

    spec = {
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "stage", "id": "draft", "config": {"prompt": "x", "require_hitl": sent}}
            ],
        }
    }
    return type_attention(spec, hits=[])["draft"].value


def _tolerates_failure(sent: object) -> bool:
    """A check step of a definition (its tests must pass), configured as an agent wrote it."""
    from personalclaw.workflows.gate_answers import tolerates_failure
    from personalclaw.workflows.models import InstanceState, Node
    from personalclaw.workflows.tick import tolerate_failures

    check = {"kind": "verify_command", "command": "pytest -q", "allow_failure": sent}
    gate = Node.from_dict({"kind": "gate", "id": "tests", "config": check})
    tolerated = tolerates_failure(gate)
    after = InstanceState.DEGRADED if tolerated else InstanceState.FAILED
    assert tolerate_failures([gate], [InstanceState.FAILED]) == [after]
    return tolerated


def _fresh_judge(sent: object) -> bool:
    from personalclaw.workflows.verify import requires_fresh_judge

    return requires_fresh_judge({"self_judge": sent})


def _skipped_without_memory(sent: object) -> bool:
    from personalclaw.workflows.ownership import MemoryMode, skips_node

    skipped, why = skips_node({"persists_memory": sent}, MemoryMode.INCOGNITO)
    assert skipped == ("persists_memory" in why)
    return skipped


def _under_judge_contract(sent: object) -> bool:
    from personalclaw.workflows.engine import NodeResult, apply_judge_contract
    from personalclaw.workflows.models import InstanceState, Node
    from personalclaw.workflows.versions import _static_signals

    node = Node.from_dict({"kind": "stage", "id": "judge", "config": {"judge_contract": sent}})
    judged = apply_judge_contract(node, NodeResult(InstanceState.DONE, {"verdict": "PASS"}), None)
    under = "contract_valid" in judged.output
    spec = {"root": {"kind": "sequence", "id": "s", "children": [node.to_dict()]}}
    assert _static_signals(spec)["has_gate_or_judge"] is under
    return under


def _hard_criterion(sent: object) -> bool:
    from personalclaw.workflows.verify import run_ladder

    ladder = run_ladder([{"name": "tests pass", "threshold": 1, "hard": sent}], {"tests pass": 0})
    [result] = ladder.results
    return result.hard_fail


# ── the loop judge's verdict ─────────────────────────────────────────────────────────────────


def _verdict(field: str) -> Callable[[object], bool]:
    def read(sent: object) -> bool:
        from personalclaw.loop.judge import _parse_verdict

        verdict = _parse_verdict(json.dumps({"done_reason": "the report is written", field: sent}))
        return getattr(verdict, field)

    return read


class Setting(NamedTuple):
    """A boolean a workflow's definition, edit or judge carries, and what each answer does."""

    name: str
    read: Callable[[object], object]
    yes: object
    no: object
    neither: object


#: Every setting the defect reached. What spells neither is the setting's safe value: no second
#: firing, a human asked, failure held, a fresh judge, a step skipped, a hard criterion.
SETTINGS = [
    Setting("rewind op redo_effects", _rewind_op("redo_effects"), True, False, False),
    Setting("rewind op force", _rewind_op("force"), True, False, False),
    Setting("rewind op force, the epoch", _rewind_epoch, 3, 2, 2),
    Setting("a step's redo_effects", _redo_blocked, False, True, True),
    Setting("a step's require_hitl", _requires_hitl, True, False, True),
    Setting("a step's require_hitl, the plan", _attention, "hitl", "afk", "hitl"),
    Setting("a check's allow_failure", _tolerates_failure, True, False, False),
    Setting("a stage's self_judge", _fresh_judge, False, True, True),
    Setting("a step's persists_memory", _skipped_without_memory, True, False, True),
    Setting("a stage's judge_contract", _under_judge_contract, True, False, False),
    Setting("a criterion's hard", _hard_criterion, True, False, True),
    Setting("the loop judge's done", _verdict("done"), True, False, False),
    Setting("the loop judge's regressed", _verdict("regressed"), True, False, False),
]


def _answers(subtests, answer: str) -> None:
    """Each setting sent the word for *answer* and, for a yes or a no, the real boolean too."""
    assert len(SETTINGS) == 13, "a setting was dropped from the table"
    assert len(SETTINGS) == len({setting.name for setting in SETTINGS}), "two settings share a name"
    sent = {"yes": (WORD_YES, True), "no": (WORD_NO, False), "neither": WORD_NEITHER}[answer]
    for setting in SETTINGS:
        for value in sent:
            with row(subtests, setting=setting.name, sent=repr(value)):
                got, expected = setting.read(value), getattr(setting, answer)
                assert (
                    got == expected
                ), f"{setting.name} sent {value!r} read {got!r}, not {expected!r}"


def test_every_workflow_setting_does_what_a_yes_says(subtests):
    _answers(subtests, "yes")


def test_every_workflow_setting_does_what_a_no_says(subtests):
    _answers(subtests, "no")


def test_every_workflow_setting_reads_what_spells_neither_as_its_safe_value(subtests):
    _answers(subtests, "neither")


def test_a_step_that_declares_no_gate_asks_nobody():
    from personalclaw.workflows.confirmation import requires_hitl

    assert requires_hitl({}) is False and requires_hitl({"require_hitl": None}) is False


# ── a rewound run, end to end ────────────────────────────────────────────────────────────────


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def run_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


@pytest.mark.anyio
async def test_a_rewound_step_whose_effect_committed_does_not_fire_it_again(run_home):
    """The run end to end: the step's notification was sent in the first epoch, the run was
    rewound into a second, and a step that said ``redo_effects: "false"`` is held at the boundary
    rather than sending it again."""
    from personalclaw.workflows import store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.effects import EffectStatus, idempotency_key
    from personalclaw.workflows.journal import Journal
    from personalclaw.workflows.models import InstanceState, NodeInstance, RunStatus, WorkflowRun

    spec = {
        "name": "announce",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {
                    "kind": "action",
                    "id": "send",
                    "config": {"provider": "notify", "redo_effects": WORD_NO},
                }
            ],
        },
    }
    run = store.create(WorkflowRun(id="", workflow_name="announce"))
    store.write_spec(run.id, spec)
    path = "root.children[0]"
    Journal(run.id).effect(
        path,
        idempotency_key=idempotency_key(run.id, path, 0),
        effect_status=EffectStatus.COMMITTED.value,
        epoch=0,
        node_id="send",
        provider="notify",
        output_id="message-1",
        compensation_ref="",
    )
    store.write_state(run.id, {path: NodeInstance(path=path, epoch=1)})
    fired: list[dict] = []

    class _Sent:
        success, stdout, outcome, error, exit_code, stderr, agent_error = (
            True,
            json.dumps({"id": "message-2"}),
            "",
            "",
            0,
            "",
            None,
        )

    class _Notify:
        async def execute(self, cfg, ctx, timeout=30):
            fired.append(cfg)
            return _Sent()

    controller = RunController(
        run, spec, services=EngineServices(get_provider=lambda name: _Notify())
    )
    assert await controller.run_to_completion(timeout=20) == RunStatus.FAILED
    assert fired == [], "the notification went out a second time"
    held = store.read_state(run.id)[path]
    assert held.state == InstanceState.BLOCKED
    assert held.failure is not None and held.failure.terminal_reason == "committed_effect"
