"""What work prints of a secret it was handed is masked before anything keeps or shows it.

An automation's action and a workflow's step are handed a ``{{secret:NAME}}`` filled in, and what
they run can print it: an ``echo``, a ``curl -v`` trace, an error that quotes its arguments, a
script's answer. What came back was kept as it came, so the automation's run history, its last
error, the note that reported the run, the answer Run now gives, a step's output and the run's
ledger held the secret's value; and so did what the work wrote itself as it ran: the audit row of a
command refused before it ran or of a request, and the gateway's log line for a check that could
not run. Only the agent's ``bash`` tool masked the values it filled in, and it masked none shorter
than eight characters. Measured before this change, every test below that looks for a value found
it.

What the code does now: each dispatch that fills a reference in masks every value it filled out of
what the work returned, before it is kept or shown, by the rule the agent's ``bash`` tool applies
to the command it runs (``security.redact_known_values``): every value, wherever it appears and
however short, as written and as a JSON string writes it; and it holds those values while the work
runs, so the writers of the work's own records mask them too. A value only guessed to be a
credential, from the name of the variable that holds it, is masked from eight characters, so a
setting such as ``none`` in a variable named like a credential does not mask that word everywhere.
Records written before are rewritten when the gateway starts.

Every test writes credentials, so the home is redirected and the redirect asserted first.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from personalclaw.action_providers.base import ActionResult
from personalclaw.config import credentials as cred
from personalclaw.config import loader

NAME = "ORCHARD_PASS"
#: Long, unique and of no shape a pattern knows: only the dispatch that filled it in can mask it.
VALUE = "ov-7c3e5a91-orchard-pass-staple"
#: A stored secret shorter than eight characters, of letters no id or timestamp carries.
SHORT_NAME = "ORCHARD_PIN"
SHORT = "kqZw"
REFERENCE = "{{secret:" + NAME + "}}"
SHORT_REFERENCE = "{{secret:" + SHORT_NAME + "}}"
MASK = "[REDACTED: credential]"


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch) -> Path:
    """The test's own home (the suite redirects it), asserted before anything is written."""
    cfg = loader.config_dir()
    assert loader.env_path() == cfg / ".env", "the .env redirect must hold"
    assert cfg != Path.home() / ".personalclaw"
    monkeypatch.delenv(cred.CREDENTIAL_BACKEND_ENV, raising=False)
    for key in (NAME, SHORT_NAME):
        # Registered first, so monkeypatch removes whatever a save mirrors in.
        monkeypatch.setenv(key, "x")
        monkeypatch.delenv(key)
    for key in ("__wf_depth", "__wf_run_id", "__wf_project_id", "__wf_node_id"):
        monkeypatch.delenv(key, raising=False)
    return cfg


def _store(name: str = NAME, value: str = VALUE) -> None:
    """A secret as Settings → Secrets keeps it, out of this process's environment."""
    cred.save_credential(name, value)
    os.environ.pop(name, None)


def _homes(home: Path) -> list[Path]:
    """The home, and where the Triggers page's handlers keep the trigger store and its history
    (the suite keeps those apart: `conftest._isolate_trigger_store`)."""
    from personalclaw.dashboard.handlers.triggers import _runs_store

    return sorted({home, Path(_runs_store()._dir).parent})


def _holders(home: Path, value: str = VALUE) -> list[str]:
    """Every file that holds *value*, as written or as JSON writes it, but the credential store's
    own: under the home, and where the Triggers page's handlers write."""
    forms = {value.encode(), json.dumps(value)[1:-1].encode()}
    found: list[str] = []
    for root in _homes(home):
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name != ".env":
                data = path.read_bytes()
                if any(form in data for form in forms):
                    found.append(str(path))
    return found


# ── the rule ─────────────────────────────────────────────────────────────────


def test_a_value_shorter_than_eight_characters_is_masked_wherever_it_appears():
    from personalclaw.security import redact_known_values

    said = redact_known_values(f"pin {SHORT} accepted; {SHORT}!", [SHORT])

    assert said == f"pin {MASK} accepted; {MASK}!"


def test_a_value_is_masked_as_a_json_string_writes_it():
    """A command that prints JSON writes a quote or a backslash in the value escaped."""
    from personalclaw.security import redact_known_values

    value = 'ov"7c3e\\orchard-pass'
    printed = json.dumps({"token": value, "ok": True})

    said = redact_known_values(printed, [value])

    assert json.loads(said) == {"token": MASK, "ok": True}


def test_masking_twice_changes_nothing():
    """A value whose text is part of the mask itself does not unpick a mask already there."""
    from personalclaw.security import redact_known_values

    once = redact_known_values("signed in as credential", ["credential"])

    assert once == f"signed in as {MASK}"
    assert redact_known_values(once, ["credential"]) == once


def test_text_with_no_value_in_it_is_kept_as_written():
    from personalclaw.security import redact_known_values

    text = "orchard synced 3 rows in 0.4s\nnothing else to do"

    assert redact_known_values(text, [VALUE, SHORT]) == text


def test_a_value_only_guessed_by_its_name_is_masked_from_eight_characters(monkeypatch):
    """The agent's shell masks every credential-named variable the gateway holds; a short one is a
    setting (``none``, ``1``), so it is not masked everywhere its text appears."""
    from personalclaw.agents.native.builtin_tools import _environment_credentials

    monkeypatch.setenv("ORCHARD_AUTH_MODE", "none")
    monkeypatch.setenv("ORCHARD_API_TOKEN", "plainword-with-no-shape-5530")

    guessed = _environment_credentials()

    assert "plainword-with-no-shape-5530" in guessed
    assert "none" not in guessed


def test_a_stored_credential_in_the_environment_is_masked_however_short(home, monkeypatch):
    """A variable named for a credential the owner stored holds a known secret, not a guess."""
    from personalclaw.agents.native.builtin_tools import _environment_credentials

    _store(SHORT_NAME, SHORT)
    monkeypatch.setenv(SHORT_NAME, SHORT)

    assert SHORT in _environment_credentials()


def test_an_app_process_keeps_a_short_setting_its_environment_names_like_a_credential():
    from personalclaw.child_output import ChildOutput

    output = ChildOutput(
        app="orchard",
        process="backend",
        pid=1,
        env={"ORCHARD_AUTH_MODE": "none", "ORCHARD_API_TOKEN": "plainword-with-no-shape-5530"},
    )
    output.line("stdout", "auth none; token plainword-with-no-shape-5530")

    assert output.lines() == [f"auth none; token {MASK}"]


def test_the_agents_shell_masks_a_short_value_it_filled_in(home, tmp_path):
    import asyncio

    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
    from personalclaw.agents.native.tools import format_tool_result

    _store(SHORT_NAME, SHORT)
    tools = NativeBuiltinToolProvider(tmp_path, sandbox_mode="off")

    ran = asyncio.run(tools.invoke("bash", {"command": f"printf 'pin %s\\n' '{SHORT_REFERENCE}'"}))

    assert ran.success, ran.error
    handed = format_tool_result(ran)
    assert f"pin {MASK}" in handed and SHORT not in handed, handed


# ── an automation's action ───────────────────────────────────────────────────


class _State:
    """A dashboard state that keeps what `notify` was called with, and what it pushed to the open
    pages."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.pushed: list[Any] = []

    def notify(self, kind, title, body, *, meta=None):
        self.sent.append({"kind": kind, "title": title, "body": body, "meta": meta or {}})
        return True

    def broadcast_ws(self, frame: Any, payload: Any = None) -> None:
        self.pushed.append([frame, payload])

    def push_refresh(self, *kinds: str) -> None:
        self.pushed.append(list(kinds))


def _automation(dispatch: str, provider: str, config: dict[str, Any], tid: str) -> Any:
    """An automation the dispatch will find in the store it records into, granted its action."""
    from personalclaw.dashboard.handlers.triggers import _trigger_store
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    store = _trigger_store() if dispatch == "run-now" else TriggerStore(loader.config_dir())
    store.upsert(
        Trigger(
            id=tid,
            name="Orchard sync",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 3600},
            delivery="inbox",
            capabilities={"providers": [provider]},
            workflow={"inline": {"provider": provider, "config": config}},
        )
    )
    return store.get(tid).trigger


async def _fire(trigger: Any, state: _State) -> tuple[bool, str]:
    """The automation's scheduled fire, through the gateway's one dispatch for it."""
    from personalclaw.gateway import GatewayOrchestrator

    orch = object.__new__(GatewayOrchestrator)
    orch.dashboard_state = state
    await orch._fire_store_trigger(trigger, {"trigger_id": trigger.id})
    return True, ""


async def _run_now(trigger: Any, state: _State) -> tuple[bool, str]:
    """Run now on the Triggers page, through the dispatch every run by hand takes."""
    from personalclaw.dashboard.handlers.trigger_runs import _dispatch_store_action

    return await _dispatch_store_action(trigger, {}, state=state)


DISPATCHES = {"its-fire": _fire, "run-now": _run_now}


def _history(home: Path, tid: str) -> str:
    """What the automation's history holds, wherever its dispatch recorded it."""
    paths = [root / "cron-history" / f"{tid}.jsonl" for root in _homes(home)]
    return "".join(p.read_text(encoding="utf-8") for p in paths if p.exists())


def _bash(dispatch: str, command: str, tid: str) -> Any:
    return _automation(dispatch, "bash", {"command": command}, tid)


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch", sorted(DISPATCHES))
async def test_an_automations_command_that_prints_its_secret_is_kept_masked(home, dispatch):
    _store()
    trigger = _bash(dispatch, f"printf 'signed in with %s\\n' '{REFERENCE}'", "clock:orchard-in")

    ok, _note = await DISPATCHES[dispatch](trigger, _State())

    assert ok
    assert f"signed in with {MASK}" in _history(home, trigger.id), "vacuity: the row keeps it"
    assert _holders(home) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch", sorted(DISPATCHES))
async def test_a_failing_command_that_prints_its_secret_is_kept_and_told_masked(home, dispatch):
    _store()
    command = f"printf 'refused for %s\\n' '{REFERENCE}' >&2; exit 3"
    trigger = _bash(dispatch, command, "clock:orchard-refused")
    state = _State()

    ok, note = await DISPATCHES[dispatch](trigger, state)

    told = json.dumps(state.sent)
    assert f"refused for {MASK}" in told, "vacuity: the note says why it failed"
    assert VALUE not in told
    if dispatch == "run-now":
        assert not ok and f"refused for {MASK}" in note and VALUE not in note, note
    assert f"refused for {MASK}" in _history(home, trigger.id)
    assert _holders(home) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch", sorted(DISPATCHES))
async def test_a_short_secret_a_command_prints_is_kept_masked(home, dispatch):
    _store(SHORT_NAME, SHORT)
    trigger = _bash(dispatch, f"printf 'pin %s ok\\n' '{SHORT_REFERENCE}'", "clock:orchard-pin")

    await DISPATCHES[dispatch](trigger, _State())

    history = _history(home, trigger.id)
    assert f"pin {MASK} ok" in history and SHORT not in history, history


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch", sorted(DISPATCHES))
async def test_what_a_command_prints_with_no_secret_in_it_is_kept_as_printed(home, dispatch):
    """The positive control: a command handed a secret that prints none of it is kept whole."""
    _store()
    command = f"test -n '{REFERENCE}' && printf 'orchard synced 3 rows in 0.4s\\n'"
    trigger = _bash(dispatch, command, "clock:orchard-plain")

    await DISPATCHES[dispatch](trigger, _State())

    history = _history(home, trigger.id)
    assert "orchard synced 3 rows in 0.4s" in history and MASK not in history, history


class _Echoes:
    """An app's action that answers with what its config says, in every part of its answer."""

    def __init__(self, *, fails: bool = False, raises: bool = False) -> None:
        self.fails = fails
        self.raises = raises

    async def execute(self, config: dict[str, Any], ctx: Any, timeout: int = 30) -> ActionResult:
        said = str(config.get("say", ""))
        if self.raises:
            raise RuntimeError(f"could not reach the orchard with {said}")
        if self.fails:
            return ActionResult(success=False, stderr=f"stderr {said}", error=f"error {said}")
        return ActionResult(success=True, stdout=f"stdout {said}", summary=f"summary {said}")


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch", sorted(DISPATCHES))
@pytest.mark.parametrize("answer", ["succeeds", "fails", "raises"])
async def test_any_action_handed_a_secret_is_kept_masked(
    home, monkeypatch, caplog, dispatch, answer
):
    """The mask is the dispatch's, not the provider's: an app's action, a script and a request
    are masked by the same rule as a command, whether the action succeeds, fails or raises, and
    a raise the gateway logs is logged masked."""
    import logging

    import personalclaw.action_providers as AP
    from personalclaw.action_providers import registry

    _store()
    provider = _Echoes(fails=answer == "fails", raises=answer == "raises")
    monkeypatch.setattr(AP, "get_action_provider", lambda name: provider)
    monkeypatch.setattr(registry, "get_action_provider", lambda name: provider)
    # An action the ladder runs unasked, so the fire reaches it.
    trigger = _automation(dispatch, "create-task", {"say": REFERENCE}, f"clock:echo-{answer}")
    state = _State()

    with caplog.at_level(logging.DEBUG):
        ok, note = await DISPATCHES[dispatch](trigger, state)

    history = _history(home, trigger.id)
    assert MASK in history, "vacuity: the row keeps what the action said"
    if dispatch == "run-now" and answer != "succeeds":
        assert not ok and MASK in note, note
    if dispatch == "its-fire" and answer == "raises":
        assert f"could not reach the orchard with {MASK}" in caplog.text, "vacuity: it is logged"
    assert VALUE not in note and VALUE not in json.dumps([state.sent, state.pushed], default=str)
    assert VALUE not in caplog.text
    assert _holders(home) == []


# ── a workflow's step ────────────────────────────────────────────────────────


class _Supervisor:
    """Starts each run as the gateway's supervisor does, with the shipped actions and the verifier
    its checks run through."""

    def __init__(self) -> None:
        self.controllers: list[Any] = []

    async def launch(self, run: Any, spec: dict[str, Any], *, depth: int = 0) -> Any:
        from personalclaw.workflows.controller import EngineServices, RunController
        from personalclaw.workflows.verify import run_verify_block

        services = EngineServices(supervisor=self, verify=run_verify_block)
        controller = RunController(run, spec, services=services, depth=depth)
        self.controllers.append(controller)
        await controller.start()
        return controller

    async def settle(self) -> None:
        done = 0
        while done < len(self.controllers):
            await self.controllers[done].wait_for_terminal(timeout=20)
            done += 1


@pytest.fixture
def runs(home, monkeypatch) -> _Supervisor:
    from personalclaw.action_providers import registry
    from personalclaw.action_providers import services as services_mod

    registry._ensure_default_providers_registered()
    supervisor = _Supervisor()
    monkeypatch.setattr(
        services_mod,
        "_services",
        services_mod.ActionServices(state=MagicMock(), workflows=supervisor),
    )
    return supervisor


def _workflow(*steps: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "orchard-steps",
        "version": 1,
        "root": {"kind": "sequence", "id": "s", "children": list(steps)},
    }


def _bash_step(command: str, step_id: str = "push") -> dict[str, Any]:
    return {
        "kind": "action",
        "id": step_id,
        "config": {"provider": "bash", "with": {"command": command}},
    }


async def _start(spec: dict[str, Any], runs: _Supervisor) -> Any:
    """A run started by hand, as the Run button's start does once its checks pass."""
    from personalclaw.workflows import store
    from personalclaw.workflows.models import WorkflowRun

    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs={}))
    store.write_spec(run.id, spec)
    await runs.launch(run, spec)
    await runs.settle()
    return store.get(run.id)


def _outputs(run_id: str) -> dict[str, Any]:
    from personalclaw.workflows import store

    folder = store.run_dir(run_id) / "outputs"
    kept = (json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json")))
    return {row["node_path"]: row["output"] for row in kept}


def _ledger(run_id: str) -> str:
    from personalclaw.workflows import journal

    return json.dumps(journal.ledger(run_id), default=str)


@pytest.mark.asyncio
async def test_a_workflow_bash_step_that_prints_its_secret_keeps_the_mask(home, runs):
    _store()

    run = await _start(_workflow(_bash_step(f"printf 'pushed with %s' '{REFERENCE}'")), runs)

    assert run.status == "complete", run.status
    outputs = _outputs(run.id)
    assert outputs["root.children[0]"]["stdout"] == f"pushed with {MASK}", outputs
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_failing_workflow_bash_step_keeps_why_it_failed_masked(home, runs):
    _store()
    command = f"printf 'refused for %s\\n' '{REFERENCE}' >&2; exit 3"

    run = await _start(_workflow(_bash_step(command)), runs)

    assert run.status == "failed", run.status
    assert f"refused for {MASK}" in _ledger(run.id), "vacuity: the ledger says why it failed"
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_step_that_prints_its_secret_as_json_keeps_the_mask(home, runs):
    """A step whose command prints JSON has its output read as JSON, so the mask is on the
    value it printed and on the field the run reads."""
    _store()
    command = f'printf \'{{"token": "%s", "rows": 3}}\' \'{REFERENCE}\''

    run = await _start(_workflow(_bash_step(command)), runs)

    assert run.status == "complete", run.status
    assert _outputs(run.id)["root.children[0]"] == {"token": MASK, "rows": 3}
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_workflow_script_step_keeps_what_its_script_said_masked(home, runs, monkeypatch):
    import personalclaw.schedule_script as ss

    _store()
    monkeypatch.setattr(
        ss,
        "run_script_sandboxed",
        lambda script, job_id, message, timeout: {"status": "ok", "message": f"sent: {message}"},
    )
    step = {
        "kind": "action",
        "id": "script",
        "config": {
            "provider": "run-script",
            "with": {"script": "push.py:run"},
            "context": f"push with {REFERENCE}",
        },
    }

    run = await _start(_workflow(step), runs)

    assert run.status == "complete", run.status
    assert _outputs(run.id)["root.children[0]"]["stdout"] == f"sent: push with {MASK}"
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_transform_step_keeps_the_mask_and_never_the_value(home, runs):
    """A step's output is what its run keeps, so a secret a transform's bindings filled in is
    masked there too; the step that sends it writes the reference itself."""
    _store()
    step = {"kind": "transform", "id": "note", "config": {"expr": {"note": f"pass {REFERENCE} ok"}}}

    run = await _start(_workflow(step), runs)

    assert run.status == "complete", run.status
    assert _outputs(run.id)["root.children[0]"] == {"note": f"pass {MASK} ok"}
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_workflow_step_that_prints_no_secret_keeps_what_it_printed(home, runs):
    """The positive control: what a step prints with no secret in it is its output, whole, and a
    step after it reads it."""
    _store()
    first = _bash_step(f"test -n '{REFERENCE}' && printf 'orchard synced 3 rows'", "sync")
    second = _bash_step("printf 'after: %s' '{{nodes.sync.output.stdout}}'", "after")

    run = await _start(_workflow(first, second), runs)

    assert run.status == "complete", run.status
    outputs = _outputs(run.id)
    assert outputs["root.children[0]"]["stdout"] == "orchard synced 3 rows"
    assert outputs["root.children[1]"]["stdout"] == "after: orchard synced 3 rows"


# ── what the work writes itself while it runs ────────────────────────────────

#: A shell rule the owner added in Settings → Security: a command naming it is refused.
OWNERS_RULE = "pcfixture-orchard-refused"


def _owners_rule() -> None:
    (loader.config_dir() / "config.json").write_text(
        json.dumps({"security": {"denied_commands": [OWNERS_RULE]}}), encoding="utf-8"
    )


def _audit(home: Path, event_type: str) -> list[dict[str, Any]]:
    """The audit log's rows of *event_type*."""
    path = home / "security_events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return [row for row in map(json.loads, lines) if row.get("event_type") == event_type]


@pytest.fixture
def no_sandbox(monkeypatch: pytest.MonkeyPatch) -> str:
    """A host where the sandbox a command runs in cannot start: every command is refused before
    it runs. What it says."""
    from personalclaw import sandbox

    said = "the sandbox cannot start on this machine, so nothing was run"

    def _refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise sandbox.SandboxEnforcementUnavailable(said)

    monkeypatch.setattr(sandbox, "wrap_argv", _refuse)
    return said


@pytest.fixture
def gateway_log() -> Any:
    """The gateway's log as its sinks write it: every record through the masking formatter the
    gateway's file, its console and the Logs page share, as it is written."""
    import io
    import logging

    from personalclaw.security import MaskedStreamHandler, MaskingFormatter

    stream = io.StringIO()
    handler = MaskedStreamHandler(stream)
    handler.setFormatter(MaskingFormatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("personalclaw")
    root.addHandler(handler)
    yield stream
    root.removeHandler(handler)


def test_what_work_writes_is_masked_of_what_its_dispatches_handed_it_while_it_runs():
    """A value filled in after the hold began is held too (a step's bindings resolve inside its
    dispatch), work dispatched inside other work is masked of what both were handed, and nothing is
    masked once the work is over."""
    from personalclaw.filled_secrets import handed, masked_here

    step_values: list[str] = []
    with handed([SHORT]):
        with handed(step_values):
            step_values.append(VALUE)
            assert masked_here(f"{SHORT} and {VALUE}") == f"{MASK} and {MASK}"
        assert masked_here(f"{SHORT} and {VALUE}") == f"{MASK} and {VALUE}"
    assert masked_here(f"{SHORT} and {VALUE}") == f"{SHORT} and {VALUE}"


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch", sorted(DISPATCHES))
async def test_a_command_refused_before_it_ran_is_audited_with_its_secret_masked(
    home, no_sandbox, dispatch
):
    """The audit row of a command the sandbox could not hold quotes the command, and the command
    holds what its dispatch filled in."""
    _store()
    trigger = _bash(dispatch, f"printf 'pushed with %s' '{REFERENCE}'", "clock:orchard-held")

    await DISPATCHES[dispatch](trigger, _State())

    (row,) = _audit(home, "command_refused")
    assert row["metadata"]["command"] == f"printf 'pushed with %s' '{MASK}'", row
    assert no_sandbox in _history(home, trigger.id), "vacuity: the run says why it did not run"
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_step_refused_before_it_ran_is_audited_with_its_secret_masked(
    home, runs, no_sandbox
):
    _store()

    run = await _start(_workflow(_bash_step(f"printf 'pushed with %s' '{REFERENCE}'")), runs)

    assert run.status == "failed", run.status
    (row,) = _audit(home, "command_refused")
    assert row["metadata"]["command"] == f"printf 'pushed with %s' '{MASK}'", row
    assert _holders(home) == []


def _check(command: str) -> dict[str, Any]:
    """A workflow's check: a gate that passes when *command* does."""
    return {
        "kind": "gate",
        "id": "check",
        "config": {"kind": "verify_command", "verify": {"command": command}},
    }


@pytest.mark.asyncio
async def test_a_check_the_owners_rule_refuses_is_audited_with_its_secret_masked(home, runs):
    _store()
    _owners_rule()

    run = await _start(_workflow(_check(f"printf '%s' '{REFERENCE}' | {OWNERS_RULE}")), runs)

    assert run.status == "failed", run.status
    (row,) = _audit(home, "command_refused")
    assert row["metadata"]["command"] == f"printf '%s' '{MASK}' | {OWNERS_RULE}", row
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_check_that_cannot_run_is_logged_with_its_secret_masked(home, runs, gateway_log):
    """A check whose program is not installed is logged with its command and what it printed,
    which both hold what the step was handed."""
    _store()

    run = await _start(_workflow(_check(f"printf 'pin %s\\n' '{REFERENCE}'; exit 127")), runs)

    assert run.status == "failed", run.status
    logged = gateway_log.getvalue()
    assert "not runnable (exit 127" in logged, "vacuity: the log says why the check did not run"
    assert f"pin {MASK}" in logged and VALUE not in logged, logged
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_check_that_prints_no_secret_is_logged_as_it_printed(home, runs, gateway_log):
    """The control: what a check that was handed no secret printed is logged whole."""
    _store()

    run = await _start(_workflow(_check("printf 'orchard pin checked\\n'; exit 127")), runs)

    assert run.status == "failed", run.status
    logged = gateway_log.getvalue()
    assert "orchard pin checked" in logged and MASK not in logged, logged


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch", sorted(DISPATCHES))
async def test_a_request_an_action_was_refused_is_audited_with_its_secret_masked(home, dispatch):
    """A fetch to a host the owner has not allowed is refused before it goes out, and its audit
    row keeps the URL, which holds what the dispatch filled in."""
    _store()
    url = "https://api.example.com/v1/rows?key="
    trigger = _automation(dispatch, "net-fetch", {"url": url + REFERENCE}, "clock:orchard-fetch")

    await DISPATCHES[dispatch](trigger, _State())

    (row,) = [row for row in _audit(home, "api_access") if row["operation"] == "egress_fetch"]
    assert row["outcome"] == "denied" and row["resources"] == url + MASK, row
    assert _holders(home) == []


# ── records written before ───────────────────────────────────────────────────


def _a_printing_run_as_it_was_recorded(spec: dict[str, Any], said: str) -> str:
    """A run of *spec* recorded before this change: its step's output and its ledger hold what
    the step printed, *said*. Its id."""
    from personalclaw.workflows import store
    from personalclaw.workflows.journal import STEP_FAILED, Journal
    from personalclaw.workflows.models import WorkflowRun

    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs={}))
    store.write_spec(run.id, spec)
    store.write_output(run.id, "root.children[0]", {"stdout": said, "exit_code": 0})
    Journal(run.id).write(STEP_FAILED, instance_path="root.children[0]", cause=said)
    return run.id


def test_a_step_that_printed_its_secret_before_holds_the_reference_once_the_home_is_redacted(
    home,
):
    """The gateway's start takes a printed value out of a run's records, as it takes out a value
    a run was handed, when the run's definition names a secret a step could have printed."""
    from personalclaw.workflows import input_secrets, store

    _store()
    spec = _workflow(_bash_step(f"printf 'pushed with %s' '{REFERENCE}'"))
    run_id = _a_printing_run_as_it_was_recorded(spec, f"pushed with {VALUE}")
    assert _holders(home), "vacuity: the record holds the value as it was printed"

    assert run_id in input_secrets.redact_home()

    assert _holders(home) == []
    assert store.read_output(run_id, "root.children[0]")["stdout"] == f"pushed with {REFERENCE}"
    assert not input_secrets.redact_home(), "a second pass finds nothing"


def test_an_automations_last_error_from_before_holds_the_reference_once_the_home_is_redacted(
    home,
):
    """The last error an automation's row shows was written from its run, so a value a run
    printed before is taken out of it as it is out of the automation's history; a last error
    with no value in it is left as it was."""
    from personalclaw.triggers.store import TriggerStore
    from personalclaw.workflows import input_secrets

    _store()
    store = TriggerStore(loader.config_dir())
    printed = _bash("its-fire", f"printf '%s' '{REFERENCE}'; exit 3", "clock:orchard-printed")
    other = _bash("its-fire", "exit 3", "clock:orchard-other")
    printed.last_error_summary = f"refused for {VALUE}"
    other.last_error_summary = "the orchard host did not answer"
    store.upsert(printed)
    store.upsert(other)
    assert _holders(home), "vacuity: the automation's row holds the value as it was recorded"

    assert "triggers" in input_secrets.redact_home()

    assert _holders(home) == []
    assert store.get(printed.id).trigger.last_error_summary == f"refused for {REFERENCE}"
    assert store.get(other.id).trigger.last_error_summary == "the orchard host did not answer"
    assert not input_secrets.redact_home(), "a second pass finds nothing"


def test_a_run_whose_definition_names_no_secret_is_left_as_it_was(home):
    """The control: what a run that was handed no secret printed is its own."""
    from personalclaw.workflows import input_secrets, store

    _store()
    _a_printing_run_as_it_was_recorded(_workflow(_bash_step("cat notes.txt")), "orchard notes")
    before = {p: p.read_bytes() for p in store.runs_root().rglob("*") if p.is_file()}

    assert not input_secrets.redact_home()

    assert {p: p.read_bytes() for p in store.runs_root().rglob("*") if p.is_file()} == before
