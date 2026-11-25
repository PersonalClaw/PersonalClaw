"""The checks the engine runs for a workflow's judge, and what it hands the judge of them.

A judge cannot run a command. A judge stage is a research step, whose tools only read (a shell call
is refused), and a judge gate is one model call with no tools at all. So every check a judge is to
rely on is run by the engine, just before the judge, through ``loop.gates.run_verify_command``
(screened by both denylists and the destructive-command screen, bounded, tristate), and what each
printed reaches the judge under ``{{run.checks}}``: a measurement it cites, not a command it is
told to run.

A judge's checks are the ones its step declares (``config.checks``: a command, or
``{"command", "cwd", "label"}``) and the ones its run declares for every judge
(``runtime_hints.judge``: ``proof_command`` and ``hidden_validation_commands``). Those last are
shown only to a judge: a worker that can read the hidden checks satisfies them specifically.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, replace
from typing import Any

from personalclaw.safety_flags import yes_or_no
from personalclaw.security import redact_known_values
from personalclaw.workflows.bindings import BindingContext, BindingError, refs_in, resolve
from personalclaw.workflows.command_inputs import Chosen
from personalclaw.workflows.judge_contract import JudgeHints
from personalclaw.workflows.models import GateKind, Node, NodeKind

logger = logging.getLogger(__name__)

#: How a judge's checks open the end of its prompt. A reference rather than the text itself: the
#: prompt is a template its bindings resolve, so output spliced in as text would be read as
#: bindings wherever it printed `{{`, while a value is handed over as written.
CHECKS_BLOCK = (
    "\n\n---\n\nThe engine ran these checks just before you, and recorded what each printed. "
    "They are measurements: cite their output. You cannot run a command yourself.\n"
    "{{run.checks}}"
)


#: What a check's command and folder may be written from: the run's inputs, and of the run's own
#: facts the path of its document (`run.document`). A step's output is what its worker or model
#: produced, and read into a command the engine runs it would let the work being judged choose
#: the check it is judged by. The run's other facts are text, none of it a command: the files
#: dropped into it (`run.dropped`, whoever dropped them), what its checks printed (`run.checks`)
#: and the owner's guidance (`run.guidance`).
CHECK_INPUTS = "inputs"
#: A binding that reads one of the owner's secrets (``{{secret:NAME}}``, `bindings.resolve_expr`).
SECRET_REF = "secret:"
CHECK_RUN_FIELDS = frozenset({"document"})


def read_of_the_work(text: Any) -> str:
    """The first binding in *text* that reads anything but the run's inputs
    (:data:`CHECK_INPUTS`) and the run's fields in :data:`CHECK_RUN_FIELDS`, or ""."""
    for expr in refs_in(text):
        path = expr.split("|", 1)[0].strip()
        root, _, field = path.partition(".")
        root = root.split("[", 1)[0].strip()
        if root == CHECK_INPUTS or (root == "run" and field.strip() in CHECK_RUN_FIELDS):
            continue
        if root.startswith(SECRET_REF):
            # A secret the owner keeps, handed to the check by reference and masked in what it
            # prints (`run_secrets`): hers, never the work's.
            continue
        return expr
    return ""


@dataclass(frozen=True)
class Check:
    """One check a judge's step or run declares: its command, the folder it runs in ("" for the
    run's own) and what it is called."""

    command: str
    cwd: str = ""
    label: str = "check"


def is_judge(node: Node) -> bool:
    """A judge: a gate of kind judge, or a stage whose output is held to the judge contract."""
    cfg = node.config or {}
    if node.kind == NodeKind.GATE:
        return str(cfg.get("kind", "") or "") == GateKind.JUDGE.value
    return node.kind == NodeKind.STAGE and yes_or_no(cfg.get("judge_contract")) is True


def declared(node: Node, hints: JudgeHints | None) -> list[Check]:
    """The checks *node* is to be handed, in the order they are shown: its own, then its run's.
    None for a step that is not a judge, whatever it declares."""
    if not is_judge(node):
        return []
    checks: list[Check] = []
    raw = (node.config or {}).get("checks")
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, str) and entry.strip():
            checks.append(Check(command=entry))
        elif isinstance(entry, dict) and str(entry.get("command") or "").strip():
            checks.append(
                Check(
                    command=str(entry["command"]),
                    cwd=str(entry.get("cwd") or ""),
                    label=str(entry.get("label") or "check"),
                )
            )
    if hints is not None:
        if hints.proof_command.strip():
            checks.append(Check(command=hints.proof_command, label="proof"))
        checks.extend(
            Check(command=c, label="hidden check") for c in hints.hidden_validation_commands
        )
    return checks


async def _printed(check: Check, ctx: BindingContext, folder: str, chosen: Chosen) -> str:
    """What running *check* in its folder (else *folder*) gave, as the judge reads it: never run
    when its command or folder reads an input as the run's owner did not choose it (*chosen*)."""
    from personalclaw.loop.gates import CheckReport, for_a_judge, refusal, run_verify_command

    if read := read_of_the_work(check.command) or read_of_the_work(check.cwd):
        return (
            f"The {check.label} check did not run: it reads {{{{{read}}}}}, and a check's command "
            "and folder come from the run's inputs and its document's path, never from what a "
            "step produced or other text the run holds."
        )
    if unchosen := chosen.refusal(check.command, "its command") or chosen.refusal(
        check.cwd, "its folder"
    ):
        return f"The {check.label} check did not run: {unchosen}"
    try:
        command = str(resolve(check.command, ctx) or "").strip()
        cwd = str(resolve(check.cwd, ctx) or "").strip() or folder
    except BindingError as exc:
        return f"The {check.label} check did not run: its command or folder names {exc}."
    # The command as its definition writes it, a secret it names kept as the reference: the
    # judge reads this, and the run fills the value only into what it runs. Whatever the check
    # prints is masked of each value filled in (`ctx.filled`) for the same reason.
    filled = [value for value in ctx.filled if value]
    # Never the filled command: with no command of its own written, the check is named by its
    # label, so nothing the run filled in reaches what the judge reads or what is logged.
    shown = check.command.strip() or check.label
    where = redact_known_values(cwd, filled)
    if not command:
        return f"The {check.label} check did not run: its command is empty for this run."
    if not cwd:
        return f"`{shown}` did not run: this run has no folder to run it in."
    if refused := refusal(command, cwd=cwd):
        return f"`{shown}` did not run: {redact_known_values(refused, filled)}"
    report = CheckReport()
    try:
        ok = await run_verify_command(command, cwd, label=f"judge {check.label}", report=report)
    except Exception as exc:  # noqa: BLE001 - a runner that raised gave no result, which is said
        # The check's label and the error's kind only: the error, and a traceback of it, can
        # hold the command as it ran, with each value the run filled in.
        logger.debug("judge check %r raised %s", check.label, type(exc).__name__)
        ok = None
    if ok is True:
        said = f"`{shown}` in {where}: passed (exit 0)."
    elif ok is False:
        said = f"`{shown}` in {where}: failed (exit {report.exit_code})."
    else:
        not_run = redact_known_values(report.not_run or "it could not start", filled)
        said = f"`{shown}` in {where}: did not run ({not_run})."
    if report.output:
        printed, _ = for_a_judge(
            redact_known_values(report.output, filled),
            what=f"What `{shown}` printed",
            source="check",
            source_type="output",
        )
        said += f"\nThe end of what it printed:\n{printed}"
    return said


async def measured(
    node: Node, ctx: BindingContext, *, hints: JudgeHints | None, folder: str, chosen: Chosen
) -> tuple[Node, BindingContext]:
    """*node* and *ctx* with the checks the judge *node* declares run, and what they printed at
    the end of its prompt. Unchanged for a step that is not a judge or declares none. *chosen* is
    the run's inputs as its owner chose them, which a check's command is held to
    (`command_inputs.Chosen`)."""
    checks = declared(node, hints)
    prompt = (node.config or {}).get("prompt")
    if not checks or not isinstance(prompt, str) or not prompt:
        return node, ctx
    printed = [await _printed(check, ctx, folder, chosen) for check in checks]
    out = copy.deepcopy(node)
    out.config["prompt"] = f"{prompt.rstrip()}{CHECKS_BLOCK}"
    # The judge is handed what its checks printed, never their commands: a check that may not
    # run is said above, and left in it would be bound as the step's own config, where a
    # reference this run cannot fill fails the step before its judge is asked.
    out.config.pop("checks", None)
    return out, replace(ctx, run_checks="\n\n".join(printed))
