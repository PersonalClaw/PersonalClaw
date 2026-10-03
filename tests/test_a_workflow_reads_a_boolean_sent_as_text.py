"""A workflow reads a boolean an agent writes as text as the word it spells.

An agent writes a workflow's definitions and its edits, and a boolean as text as often as not;
``bool("false")`` is True. So:

* a step configured ``"redo_effects": "false"`` fired its external effect a second time when the
  run was rewound, the double-fire the effect ledger exists to stop;
* a rewind op or a rewind request sent ``"force": "false"`` threw away the region's cached outputs
  and ran it again, and ``"redo_effects": "false"`` went into the run's history as a request to
  fire its effects again;
* ``"require_hitl": "true"`` asked nobody: an identity check read the word as no;
* ``"allow_failure": "false"`` let what follows a failed check run anyway;
* ``"self_judge": "false"`` let a stage be judged in its own session;
* ``"persists_memory": "true"`` let a run that may write no memory write it;
* ``"judge_contract": "false"`` put a stage under the judge contract;
* a verify criterion ``"hard": "false"`` failed the run;
* the loop judge's ``"done": "true"`` read as not done, so a finished loop kept running cycles.

Each is read through ``safety_flags.yes_or_no`` with the safe value of what it guards. A rewind
request is held stricter, as every request body is (``request_validation.bool_field``): a JSON
body can carry a real boolean, so anything else is refused and nothing is rewound.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from personalclaw.workflows import mutations

NO = [False, "false", "False", "no", " NO ", "0", "off"]
YES = [True, "true", "TRUE", "yes", " on ", "1"]
NEITHER = ["", "maybe", 1]


# ── an edit op: redo_effects and force ───────────────────────────────────────────────────────


@pytest.mark.parametrize("flag", ["redo_effects", "force"])
@pytest.mark.parametrize("sent", NO + NEITHER)
def test_a_rewind_op_asks_for_neither_unless_it_says_yes(flag, sent):
    op = mutations.Op.from_dict({"op": "rewind", "node_id": "publish", flag: sent})
    assert getattr(op, flag) is False
    assert flag not in op.to_dict(), "the run's history records a request nobody made"


@pytest.mark.parametrize("flag", ["redo_effects", "force"])
@pytest.mark.parametrize("sent", YES)
def test_a_rewind_op_that_says_yes_asks_for_it(flag, sent):
    op = mutations.Op.from_dict({"op": "rewind", "node_id": "publish", flag: sent})
    assert getattr(op, flag) is True
    assert op.to_dict()[flag] is True


def test_a_rewind_not_forced_keeps_the_regions_cached_outputs():
    """What `force` decides: a forced rewind starts a new epoch, so nothing cached is reused."""
    from personalclaw.workflows.models import NodeInstance

    instances = {"root.children[0]": NodeInstance(path="root.children[0]", epoch=2)}
    for sent, epoch in (("false", 2), ("no", 2), ("true", 3)):
        op = mutations.Op.from_dict({"op": "rewind", "node_id": "publish", "force": sent})
        assert mutations.next_epoch(instances, ["root.children[0]"], force=op.force) == epoch


# ── a rewind request ─────────────────────────────────────────────────────────────────────────


async def _rewind_request(monkeypatch, body: dict) -> tuple[int, dict]:
    """The status the route answers, and what it asked the run to do (nothing when refused)."""
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.request_validation import RequestValidationError
    from personalclaw.workflows import handlers

    seen: dict = {}

    def _rewind(run_id, node_id, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "preview": {}}

    monkeypatch.setattr(handlers.service, "rewind_run", _rewind)
    app = web.Application()
    app["state"] = SimpleNamespace(workflows=None, _restricted=False)
    request = make_mocked_request("POST", "/api/workflows/runs/a1b2c3d4/rewind", app=app)

    async def _json():
        return body

    request.json = _json  # type: ignore[assignment]
    request.match_info["run_id"] = "a1b2c3d4"
    try:
        response = await handlers.api_run_rewind(request)
    except RequestValidationError as exc:
        # Answered by the request boundary every `/api` route runs behind.
        response = exc.response
    return response.status, seen


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["redo_effects", "force"])
@pytest.mark.parametrize("says", ["nothing", "no"])
async def test_a_rewind_request_asks_for_neither_unless_it_says_yes(monkeypatch, flag, says):
    body = {"node_id": "publish", **({flag: False} if says == "no" else {})}
    status, seen = await _rewind_request(monkeypatch, body)
    assert status == 200 and seen[flag] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["redo_effects", "force"])
async def test_a_rewind_request_that_says_yes_asks_for_it(monkeypatch, flag):
    status, seen = await _rewind_request(monkeypatch, {"node_id": "publish", flag: True})
    assert status == 200 and seen[flag] is True


NOT_A_JSON_BOOLEAN = [s for s in NO + YES + NEITHER if not isinstance(s, bool)] + [None]


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["redo_effects", "force"])
@pytest.mark.parametrize("sent", NOT_A_JSON_BOOLEAN)
async def test_a_rewind_request_that_is_not_a_json_boolean_rewinds_nothing(monkeypatch, flag, sent):
    """A word, a number or a null: refused, and the run is not rewound, so its cached outputs
    stay and no request to fire its effects again reaches its history."""
    status, seen = await _rewind_request(monkeypatch, {"node_id": "publish", flag: sent})
    assert status == 400 and seen == {}


# ── a step's redo_effects: the boundary on an effect already committed ─────────────────────────


@pytest.mark.parametrize("sent", NO + NEITHER)
def test_a_committed_effect_stays_a_boundary_unless_the_step_says_yes(sent):
    from personalclaw.workflows.effects import EffectRecord, redo_blocked

    committed = EffectRecord(idempotency_key="k", epoch=0)
    assert redo_blocked({"redo_effects": sent}, committed, epoch=1) is True


@pytest.mark.parametrize("sent", YES)
def test_a_step_that_says_yes_may_fire_it_again(sent):
    from personalclaw.workflows.effects import EffectRecord, redo_blocked

    committed = EffectRecord(idempotency_key="k", epoch=0)
    assert redo_blocked({"redo_effects": sent}, committed, epoch=1) is False


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
@pytest.mark.parametrize("sent", ["false", "no", "0"])
async def test_a_rewound_step_whose_effect_committed_does_not_fire_it_again(run_home, sent):
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
                    "config": {"provider": "notify", "redo_effects": sent},
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


# ── what a definition's steps declare ────────────────────────────────────────────────────────


@pytest.mark.parametrize("sent", NO)
def test_a_step_that_says_no_human_runs_without_one(sent):
    from personalclaw.workflows.confirmation import requires_hitl

    assert requires_hitl({"require_hitl": sent}) is False


@pytest.mark.parametrize("sent", YES + NEITHER)
def test_a_step_that_declares_a_human_gate_in_any_other_word_asks(sent):
    from personalclaw.workflows.confirmation import requires_hitl

    assert requires_hitl({"require_hitl": sent}) is True


def test_a_step_that_declares_no_gate_asks_nobody():
    from personalclaw.workflows.confirmation import requires_hitl

    assert requires_hitl({}) is False and requires_hitl({"require_hitl": None}) is False


@pytest.mark.parametrize("sent, attention", [("true", "hitl"), ("yes", "hitl"), ("false", "afk")])
def test_the_autonomy_plan_reads_the_steps_gate_the_same_way(sent, attention):
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
    assert type_attention(spec, hits=[])["draft"].value == attention


def _check(**config):
    """A check step of a definition (its tests must pass), configured as an agent wrote it."""
    from personalclaw.workflows.models import Node

    check = {"kind": "verify_command", "command": "pytest -q", **config}
    return Node.from_dict({"kind": "gate", "id": "tests", "config": check})


@pytest.mark.parametrize("sent", NO + NEITHER)
def test_a_failed_check_holds_what_follows_unless_it_says_yes(sent):
    from personalclaw.workflows.gate_answers import tolerates_failure
    from personalclaw.workflows.models import InstanceState
    from personalclaw.workflows.tick import tolerate_failures

    gate = _check(allow_failure=sent)
    assert tolerates_failure(gate) is False
    assert tolerate_failures([gate], [InstanceState.FAILED]) == [InstanceState.FAILED]


@pytest.mark.parametrize("sent", YES)
def test_a_failed_check_that_says_yes_lets_what_follows_run(sent):
    from personalclaw.workflows.gate_answers import tolerates_failure
    from personalclaw.workflows.models import InstanceState
    from personalclaw.workflows.tick import tolerate_failures

    gate = _check(allow_failure=sent)
    assert tolerates_failure(gate) is True
    assert tolerate_failures([gate], [InstanceState.FAILED]) == [InstanceState.DEGRADED]


@pytest.mark.parametrize("sent", NO + NEITHER)
def test_a_judge_runs_in_its_own_session_unless_the_step_says_yes(sent):
    from personalclaw.workflows.verify import requires_fresh_judge

    assert requires_fresh_judge({"self_judge": sent}) is True


@pytest.mark.parametrize("sent", YES)
def test_a_step_that_says_yes_may_judge_itself(sent):
    from personalclaw.workflows.verify import requires_fresh_judge

    assert requires_fresh_judge({"self_judge": sent}) is False


@pytest.mark.parametrize("sent", YES + NEITHER)
def test_a_run_that_writes_no_memory_skips_a_step_that_declares_it_does(sent):
    from personalclaw.workflows.ownership import MemoryMode, skips_node

    skipped, why = skips_node({"persists_memory": sent}, MemoryMode.INCOGNITO)
    assert skipped and "persists_memory" in why


@pytest.mark.parametrize("sent", NO)
def test_a_step_that_says_it_writes_no_memory_runs(sent):
    from personalclaw.workflows.ownership import MemoryMode, skips_node

    assert skips_node({"persists_memory": sent}, MemoryMode.INCOGNITO) == (False, "")


@pytest.mark.parametrize(
    "sent, under", [(s, False) for s in NO + NEITHER] + [(s, True) for s in YES]
)
def test_a_stage_is_under_the_judge_contract_only_on_a_yes(sent, under):
    from personalclaw.workflows.engine import NodeResult, apply_judge_contract
    from personalclaw.workflows.models import InstanceState, Node
    from personalclaw.workflows.versions import _static_signals

    node = Node.from_dict({"kind": "stage", "id": "judge", "config": {"judge_contract": sent}})
    judged = apply_judge_contract(node, NodeResult(InstanceState.DONE, {"verdict": "PASS"}), None)
    assert ("contract_valid" in judged.output) is under
    spec = {"root": {"kind": "sequence", "id": "s", "children": [node.to_dict()]}}
    assert _static_signals(spec)["has_gate_or_judge"] is under


@pytest.mark.parametrize(
    "sent, hard", [(s, False) for s in NO] + [(s, True) for s in YES + NEITHER]
)
def test_a_verify_criterion_is_hard_unless_it_says_no(sent, hard):
    from personalclaw.workflows.verify import run_ladder

    ladder = run_ladder([{"name": "tests pass", "threshold": 1, "hard": sent}], {"tests pass": 0})
    [result] = ladder.results
    assert result.hard_fail is hard


# ── the loop judge's verdict ─────────────────────────────────────────────────────────────────


def _verdict(**answer):
    from personalclaw.loop.judge import _parse_verdict

    return _parse_verdict(json.dumps({"done_reason": "the report is written", **answer}))


@pytest.mark.parametrize("sent", YES)
def test_a_judge_that_says_done_is_done(sent):
    assert _verdict(done=sent).done is True


@pytest.mark.parametrize("sent", NO + NEITHER)
def test_a_judge_that_says_anything_else_is_not(sent):
    assert _verdict(done=sent).done is False


@pytest.mark.parametrize(
    "sent, regressed", [(s, True) for s in YES] + [(s, False) for s in NO + NEITHER]
)
def test_a_judge_flags_a_regression_only_on_a_yes(sent, regressed):
    assert _verdict(regressed=sent).regressed is regressed
