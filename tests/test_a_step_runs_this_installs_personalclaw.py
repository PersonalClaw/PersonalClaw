"""A step names PersonalClaw ``personalclaw``, and that is this install's own program.

🔴 Before: the bundled ``optimize-harness`` template ran each step as
``python3 -m personalclaw.evals.optimize <step>``, so it worked only where the ``python3`` on the
gateway's ``PATH`` had PersonalClaw installed in it. In a ``uv tool`` install (the documented
default) and in the desktop app it has not, and the template failed at its first step on every
run.

Now a bash step's command says ``personalclaw <command>``, and the bash action resolves that name
to ``self_update.cli_argv()`` before ``PATH`` is asked: the command every child that runs this
install's CLI uses (the frozen bundle's executable in the desktop app, this interpreter's
``-m personalclaw`` everywhere else). The optimize steps are ``personalclaw optimize-harness
<step>``, a command of the CLI that its help does not list (``cli.HIDDEN_COMMANDS``).

Every test runs on a ``PATH`` holding the two programs a step's shell would otherwise find: a
``python3`` with no PersonalClaw in it, and a ``personalclaw`` of another install.
"""

from __future__ import annotations

import asyncio
import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from personalclaw import __version__, self_update
from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.bash_provider import BashActionProvider

#: What the other install's ``personalclaw`` on ``PATH`` prints, so a step that reached it shows.
ANOTHER_INSTALL = "another install of personalclaw"


def _program(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def isolated_install(tmp_path, monkeypatch) -> Path:
    """This test's homes, and a ``PATH`` whose ``python3`` has no PersonalClaw and whose
    ``personalclaw`` is another install's. The homes are set in the environment because the
    steps run PersonalClaw in a child process, which no in-process redirect reaches."""
    bin_dir = tmp_path / "path-bin"
    bin_dir.mkdir()
    for name in ("python3", "python"):
        _program(bin_dir / name, "echo \"No module named 'personalclaw'\" >&2\nexit 1\n")
    _program(bin_dir / "personalclaw", f'echo "{ANOTHER_INSTALL}"\n')
    home = tmp_path / "pc-home"
    home.mkdir()
    user = tmp_path / "user-home"
    user.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(user))
    return home


def _ctx() -> ActionContext:
    return ActionContext(event="workflow_node", context="", payload={})


def _bash(command: str) -> Any:
    return asyncio.run(
        BashActionProvider().execute({"command": command, "timeout": 120}, _ctx(), timeout=120)
    )


# ── a bash step's `personalclaw` ─────────────────────────────────────────────


def test_personalclaw_in_a_bash_step_is_this_installs_own_cli(isolated_install) -> None:
    """🔴 Red before: the step ran the other install's ``personalclaw`` on ``PATH``."""
    result = _bash("personalclaw --version")

    assert result.success, (result.stdout, result.stderr, result.error)
    assert result.stdout.strip() == f"personalclaw {__version__}"


@pytest.fixture
def stand_in_cli(tmp_path, monkeypatch) -> Path:
    """``cli_argv()`` answering a frozen bundle's shape, one program and no ``-m``, at a path with
    a space in it as the desktop app's can have. The stand-in prints each argument it gets on a
    line of its own, and then what it read on its input."""
    folder = tmp_path / "Personal Claw.app" / "backend"
    folder.mkdir(parents=True)
    bundle = folder / "personalclaw-backend"
    _program(bundle, 'for a in "$@"; do echo "arg:$a"; done\necho "input:$(cat)"\n')
    monkeypatch.setattr(self_update, "cli_argv", lambda: [str(bundle)])
    return bundle


@pytest.mark.parametrize(
    "command",
    [
        "personalclaw optimize-harness 'a b' </dev/null",
        "personalclaw optimize-harness 'a b' </dev/null | cat",
        "( personalclaw optimize-harness 'a b' </dev/null )",
        "echo \"$(personalclaw optimize-harness 'a b' </dev/null)\"",
        "true && personalclaw optimize-harness 'a b' </dev/null",
    ],
)
def test_a_step_runs_exactly_what_cli_argv_says_wherever_its_shell_runs_it(
    isolated_install, stand_in_cli, command
) -> None:
    """The name is this install's program in the step's own shell: alone, in a pipeline, a
    subshell, a substitution and after another command, with every argument as it was quoted."""
    result = _bash(command)

    assert result.success, (result.stdout, result.stderr, result.error)
    assert result.stdout.splitlines() == ["arg:optimize-harness", "arg:a b", "input:"]


def test_command_personalclaw_still_asks_path(isolated_install, stand_in_cli) -> None:
    """CONTROL: what makes the difference is the name the step's shell is given. ``command``
    skips it on purpose, and then ``PATH``'s program is the one that runs."""
    result = _bash("command personalclaw --version")

    assert result.stdout.strip() == ANOTHER_INSTALL


# ── the optimize steps are a command of the CLI ──────────────────────────────


def test_the_optimize_steps_are_a_hidden_command_of_the_cli() -> None:
    """🔴 Red before: the CLI had no such command, and the steps were reachable only as a module
    of whatever interpreter ran them."""
    from personalclaw.cli import HIDDEN_COMMANDS, build_parser

    assert "optimize-harness" in HIDDEN_COMMANDS
    args = build_parser().parse_args(["optimize-harness", "scope-check"])
    assert (args.command, args.step) == ("optimize-harness", "scope-check")
    assert "optimize-harness" not in build_parser().format_help()


def test_this_installs_cli_answers_an_unknown_step_in_json(isolated_install) -> None:
    """The steps answer the engine in JSON on stdout, a refusal included, through the CLI too."""
    done = subprocess.run(
        [*self_update.cli_argv(), "optimize-harness", "no-such-step"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert done.returncode == 2, (done.stdout, done.stderr)
    answer = json.loads(done.stdout)
    assert answer["ok"] is False
    assert answer["commands"] == ["adjudicate", "experience", "file", "preflight", "scope-check"]


def test_the_optimize_module_is_not_a_second_entry_point() -> None:
    """One way in: the CLI's command. The module no longer runs on its own under ``-m``."""
    import ast

    import personalclaw.evals.optimize as optimize

    tree = ast.parse(Path(optimize.__file__).read_text(encoding="utf-8"))
    mains = [
        node
        for node in tree.body
        if isinstance(node, ast.If) and "__main__" in ast.unparse(node.test)
    ]
    assert mains == []


# ── a step runs where the home cannot gain an entry ──────────────────────────


def test_reading_the_results_ledger_makes_no_folder(isolated_install) -> None:
    """🔴 Red before: the first step reads the results ledger, and the read made ``evals/`` in
    the home. A bash step's sandbox on Linux holds the home's top level read-only, so on any home
    without one the step died there with a traceback and no answer."""
    from personalclaw.evals import optimize, store

    assert store.read_results() == []
    assert optimize.capture_best_ever("code-project").rows_considered == 0
    assert not (isolated_install / "evals").exists()


def test_a_step_that_cannot_write_answers_in_json(tmp_path, monkeypatch, capsys) -> None:
    """🔴 Red before: a step whose sandbox could not be made raised a traceback, which leaves the
    node's output empty, where every other reason it stops comes back as JSON."""
    import io

    from personalclaw.evals import optimize
    from tests.test_optimize_harness_runs_to_its_proposal import TARGET, _seed_target_runs

    home = tmp_path / "pc-home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    # A target the search could score, so the preflight reaches the sandbox it cannot make.
    _seed_target_runs()
    live = tmp_path / "live"
    live.mkdir()
    (live / "workflow.json").write_text("{}", encoding="utf-8")
    not_a_folder = tmp_path / "a-file"
    not_a_folder.write_text("", encoding="utf-8")
    payload = {
        "subject": TARGET,
        "live_target": str(live),
        "sandbox": str(not_a_folder / "sandbox"),
        "run_id": "run-1",
        "stops": {"budget_usd": "1"},
    }

    code = optimize.main(["preflight"], stdin=io.StringIO(json.dumps(payload)))

    assert code == 1
    answer = json.loads(capsys.readouterr().out)
    assert answer["ok"] is False and "a-file" in answer["error"]


# ── the template's own steps, run by the engine ──────────────────────────────

#: What the template's model step answers, in the shape its prompt asks for.
MODEL_ANSWERS: dict[str, dict[str, Any]] = {
    "propose": {
        "fix_fingerprint": "name-the-handoff-criteria",
        "diff_text": "--- a/workflow.json\n+++ b/workflow.json\n",
        "rationale": "the handoff never says what it checks the work against",
        "ops": [
            {
                "op": "update_node",
                "node_id": "handoff",
                "fields": {"prompt": "Summarize the work for review. PASSES=3"},
            }
        ],
    },
}


def _model_steps_answered(spec: dict[str, Any]) -> dict[str, Any]:
    """The template as it ships, every action, gate, loop and binding as they are, with each model
    step (a ``stage``) replaced by a zero-token transform answering what a model would."""

    def walk(node: dict[str, Any]) -> dict[str, Any]:
        if node.get("kind") == "stage":
            return {
                "kind": "transform",
                "id": node["id"],
                "config": {"expr": MODEL_ANSWERS[node["id"]]},
            }
        out = dict(node)
        if "children" in node:
            out["children"] = [walk(child) for child in node["children"]]
        if isinstance(node.get("body"), dict):
            out["body"] = walk(node["body"])
        return out

    replaced = copy.deepcopy(spec)
    replaced["root"] = walk(replaced["root"])
    return replaced


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_the_optimize_harness_template_runs_its_steps_in_an_isolated_install(
    isolated_install, tmp_path, monkeypatch
) -> None:
    """🔴 Red before: the first step ran ``python3``, which has no PersonalClaw, and the run
    failed there. Now every step runs, through the real bash action, and the search ends.

    The target has the finished runs a candidate is scored against, and the scoring step's
    model is a priced stand-in, as the template's own end-to-end test sets them up."""
    from personalclaw.action_providers.registry import _ensure_default_providers_registered
    from personalclaw.evals import optimize
    from personalclaw.workflows import store
    from personalclaw.workflows.bundled_defs import bundled_root
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import RunStatus, WorkflowRun, spec_path, walk
    from tests.test_optimize_harness_runs_to_its_proposal import (
        _bind_scripted_eval,
        _seed_target_runs,
    )

    _ensure_default_providers_registered()
    _bind_scripted_eval(monkeypatch)
    _seed_target_runs()
    live = tmp_path / "live" / "code-project"
    live.mkdir(parents=True)
    (live / "workflow.json").write_text(json.dumps({"name": "code-project"}), encoding="utf-8")
    (live / optimize.LOCK_NAME).write_text(json.dumps({"hashes": {}}), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in live.iterdir()}
    sandbox = tmp_path / "sandbox"

    shipped = bundled_root() / "optimize-harness" / "workflow.json"
    spec = _model_steps_answered(json.loads(shipped.read_text(encoding="utf-8")))
    inputs = {
        "target_template": "code-project",
        "live_target": str(live),
        "sandbox": str(sandbox),
        "budget_usd": 1.0,
        "suite_threshold": 0.8,
        "hypothesis_abandon_after": 1,
        "no_improvement_halt": 5,
        "max_iterations": 1,
    }
    run = store.create(
        WorkflowRun(id="", workflow_name=spec["name"], mode="background", inputs=inputs)
    )
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices())
    await controller.start()
    deadline = asyncio.get_running_loop().time() + 240
    while not controller.run.is_terminal:
        assert asyncio.get_running_loop().time() < deadline, controller.run.status
        await asyncio.sleep(0.2)

    nodes = {path: node for path, node in walk(controller.root)}
    # (state, output, why it failed): a red names the step's own reason, its error output.
    steps = {
        nodes[spec_path(path)].id: (
            inst.state.value,
            store.read_output(run.id, path),
            inst.failure.cause_plain if inst.failure else "",
        )
        for path, inst in controller.instances.items()
        if nodes[spec_path(path)].kind.value == "action"
    }
    assert controller.run.status == RunStatus.COMPLETE, (controller.run.error_message, steps)
    names = ("preflight", "experience", "scope_check", "score", "adjudicate", "file")
    assert set(steps) == set(names)
    preflight, experience, scope_check, score, adjudicate, filed = (steps[n] for n in names)
    assert preflight[0] == "done" and preflight[1]["ok"] is True, preflight
    assert preflight[1]["witnessed_files"] == 2 and preflight[1]["rows_considered"] == 0
    assert preflight[1]["cases"] == 3, preflight
    assert experience[0] == "done" and experience[1]["candidates"] == [], experience
    assert scope_check[0] == "done" and scope_check[1]["outcome"] == "clean", scope_check
    assert score[0] == "done" and score[1]["score"] == 1.0, score
    assert adjudicate[0] == "done", adjudicate
    assert (
        adjudicate[1]["outcome"] == "admitted" and adjudicate[1]["halt"] == "iterations_exhausted"
    )
    assert filed[0] == "done" and filed[1]["filed"] is True, filed
    index = json.loads((sandbox / optimize.EXPERIENCE_DIR / "index.json").read_text())
    assert [(row["iteration"], row["outcome"]) for row in index] == [(1, "admitted")]
    assert index[0]["ops"] == MODEL_ANSWERS["propose"]["ops"]
    assert {p.name: p.read_bytes() for p in live.iterdir()} == before, "the live artifact changed"
