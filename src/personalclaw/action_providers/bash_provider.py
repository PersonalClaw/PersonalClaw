"""Bash hook provider — executes a shell command with templated env vars.

Runs ``/bin/sh -c <command>`` with ``PERSONALCLAW_HOOK_EVENT`` and
``PERSONALCLAW_HOOK_CONTEXT`` env vars, the structured event dict piped to
STDIN as JSON, process-group isolation, and timeout-driven SIGKILL. Each command is a run of
its own (``run_processes``): what it leaves running, a server that detached included, ends when
it exits.
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any

from personalclaw import run_processes
from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from personalclaw.cancellation import kill_timed_out, terminate_and_reap
from personalclaw.env import PROGRAM_RESOLUTION_NAMES

if TYPE_CHECKING:
    from personalclaw.security import DeniedCommand

logger = logging.getLogger(__name__)

# The child environment is built by ALLOWLIST — `sandbox.build_child_env` — so a hook
# command like `env` or `printenv` cannot read the gateway's credentials at all. This
# replaced a name-pattern denylist: the denylist kept everything it did not
# recognise, which on a real gateway meant ~121 inherited variables minus the shapes the
# pattern happened to know, and it could never see a credential named in a shape nobody
# had thought of. The allowlist inverts the default.

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


#: Env vars a PAYLOAD KEY may never set (§7/R4 rule e).
#:
#: 🔴 MEASURED. `_payload_env` merges AFTER the inherited environment (before PHF-4:
#: `os.environ`; now the allowlisted base `sandbox.build_child_env` produces — the merge
#: ORDER, and so this hazard, is unchanged), so a payload key shadows the real variable.
#: Driven end to end: a payload of ``{"PATH": "<dir with a fake `date`>"}``
#: made the command ``date`` print ``HIJACKED``. The whole point of passing the payload as
#: ENV rather than string-templating the command is that a payload value cannot become
#: code — but a payload *key* could change which binary the code resolves to, which is the
#: same outcome by a different route.
#:
#: These are the variables that decide WHAT RUNS or WHERE IT LOOKS: the loader hijacks
#: (`LD_PRELOAD`, `DYLD_INSERT_LIBRARIES`), the resolution paths (`PATH`, `PYTHONPATH`),
#: the interpreter's own entry points (`BASH_ENV`, `PYTHONSTARTUP`), and the home/config
#: roots the harness itself reads back (`PERSONALCLAW_HOME`). A payload naming any of them
#: is either a mistake or an attack, and neither should win over the process environment.
#:
#: A DENYLIST rather than an allowlist here, deliberately and unlike the payload keys:
#: `$variables` are the trigger's documented user-facing surface (`$now`, `$job_id`,
#: `$last_result`, plus every key a kind's payload carries), so an allowlist would have to
#: enumerate them all and would silently drop a new kind's variables. The dangerous set,
#: by contrast, is small, well-known and stable. Its first part is
#: `env.PROGRAM_RESOLUTION_NAMES`, the names no stored secret is mirrored under either.
PROTECTED_ENV_NAMES: frozenset[str] = PROGRAM_RESOLUTION_NAMES | {
    # roots the harness reads back
    "HOME",
    "TMPDIR",
    "PERSONALCLAW_HOME",
    "PERSONALCLAW_WORKSPACE",
    "PERSONALCLAW_HOOK_EVENT",
    "PERSONALCLAW_HOOK_CONTEXT",
    # the run the command is, read back to end what it started
    run_processes.RUN_VARIABLE,
}


def _payload_env(ctx: ActionContext) -> dict[str, str]:
    """The trigger ``$variables`` (``$now``, ``$job_id``, ``$EVENT``…) as env
    vars, so a shell command resolves them natively. Env (not string-templating
    the command) on purpose: a payload value like ``last_result`` can hold
    arbitrary text — substituting it into the command line would be a shell
    injection vector. Keys that aren't valid shell identifiers are skipped.

    A key in ``PROTECTED_ENV_NAMES`` is skipped too, and that is the other half of the
    same defence: passing the payload as env stops a payload VALUE becoming code, and this
    stops a payload KEY changing which code runs. See that constant for the measurement.
    """
    out = {"EVENT": ctx.event, "CONTEXT": ctx.context}
    for k, v in (ctx.payload or {}).items():
        if k in PROTECTED_ENV_NAMES:
            logger.warning(
                "trigger payload key %r would override a protected environment "
                "variable; ignoring it",
                k,
            )
            continue
        if _ENV_NAME.match(k):
            out[k] = str(v)
    return out


#: A `$NAME` the command RUNS as a command, not one it hands to a program as data: the script of a
#: shell's `-c` (`sh -c "$CMD"`), what `eval` evaluates, or a word in command position (the start,
#: or after `;` `&` `|` `(` or a backtick). A workflow passes its check command in this way, so the
#: value never has to be quoted into the command line; `echo "$last_result"` stays data.
_RUN_VARIABLE = re.compile(
    r"""(?:\b(?:ba|z|da|k)?sh\s+(?:-\w+\s+)*-\w*c\w*\s+|\beval\s+|(?:^|[;&|(`])\s*)"""
    r"""["']?\$\{?([A-Za-z_][A-Za-z0-9_]*)"""
)


def _run_values(command: str, env: dict[str, str]) -> list[str]:
    """The payload values *command* runs as commands (:data:`_RUN_VARIABLE`), so they are
    screened as what will RUN, as the command itself is."""
    return [env[name] for name in _RUN_VARIABLE.findall(command) if env.get(name)]


def config_problem(config: dict[str, Any]) -> str:
    """Why a bash action's command could never run, asked when it is SAVED (the Triggers page and
    the chat's and the CLI's door alike); "" when it may. The shell denylist refuses its text, so
    every run would refuse it, and the owner would be asked to allow an automation that never runs.
    What a run is handed (a secret it names, a payload value it runs) is judged as it runs."""
    from personalclaw.security import denied_command

    command = str((config or {}).get("command") or "").strip()
    denied = denied_command(command) if command else None
    return f"Its command would never run: {denied.why()}." if denied is not None else ""


def _refused(
    command: str, refused: "DeniedCommand | str", ctx: "ActionContext", *, control: str = ""
) -> ActionResult:
    """A refused bash action: audited with the control that refused it (the shell denylist's
    answer, or *control*'s sentence), and answered with the reason. Nothing ran, and a retry would
    be refused the same way, so it is the owner's to change (``failure_class="user"``)."""
    from personalclaw.command_audit import audit_command_refusal
    from personalclaw.security import DeniedCommand

    audit_command_refusal(
        command,
        refused,
        source="action_provider",
        operation="bash_action",
        control=control,
        metadata={"provider": "bash", "event": getattr(ctx, "event", "")},
    )
    reason = refused.refusal() if isinstance(refused, DeniedCommand) else refused
    return ActionResult(success=False, error=reason, failure_class="user")


class BashActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "bash"

    @property
    def display_name(self) -> str:
        return "Bash Command"

    @property
    def supports_blocking(self) -> bool:
        return True  # PreToolUse exit-code-2 contract

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        command = (action_config.get("command") or "").strip()
        if not command:
            return ActionResult(success=False, error="Bash hook is missing 'command' field")

        # 🔴 Screen the command the way the INTERACTIVE path does. This provider runs
        # `/bin/sh -c <command>` straight from `action_config`, and nothing on the way in
        # screened it: `hooks.py` and the native bash tool both call
        # `security.is_sensitive_bash_command`, but an ACTION never reached either. So a bash
        # action could read `~/.ssh/id_rsa` where the same command typed at the agent is refused.
        #
        # The import path is why this matters beyond the API: `snapshot._merge_crons` appends an
        # imported job VERBATIM, so restoring an archive installed whatever bash action it carried.
        # Screening here rather than at import covers every creator — import, the HTTP API, the UI,
        # another app — instead of the one that happened to be noticed.
        #
        # Refused, not sanitised: there is no safe rewrite of a command that names a credential.
        # A relative path is read from where the command runs: the gateway's own folder.
        #
        # Then the shell denylist every command path asks (`security.denied_command`), here
        # whoever started the action: a schedule, a hook, Run now, a webhook, a workflow node, an
        # approved proposal. Both judge what will RUN: the command, and a payload value it runs
        # as a command (`_run_values`).
        from personalclaw import security

        payload_env = _payload_env(ctx)
        for text in (command, *_run_values(command, payload_env)):
            refusal = security.is_sensitive_bash_command(text, cwd=os.getcwd())
            if refusal:
                return _refused(command, refusal, ctx, control="sensitive_path")
            if (denied := security.denied_command(text)) is not None:
                return _refused(command, denied, ctx)

        # 🔴 The action's OWN bound wins over the caller's default, matching `run-script`
        # (which has always read `action_config["timeout"]` and preferred it). Measured on the
        # store-backed fire path: a migrated command cron carries `{"command": ..., "timeout": 600}`
        # in its action config — losslessly, the migration keeps it — but `_fire_store_trigger`
        # calls `execute(config, ctx)` with no `timeout=`, so this took the 30s SIGNATURE DEFAULT
        # and killed at 30s what the legacy dispatcher allowed 600s. Driven both ways: a
        # `sleep 3` under `{"timeout": 1}` ran the full 3s (the user's bound ignored), and the
        # 600s allowance a user configured was silently cut to 30.
        try:
            configured = int(action_config.get("timeout", 0) or 0)
        except (ValueError, TypeError):
            configured = 0
        timeout = configured or timeout

        from personalclaw.sandbox import (
            PROFILE_TOOL,
            SandboxEnforcementUnavailable,
            build_child_env,
            create_subprocess_limited,
            wrap_argv,
        )

        start = time.monotonic()
        # The allowlisted base, plus the values this site COMPUTES (never inherits): the
        # trigger's `$variables`, the hook event/context the contract promises and the command's
        # run marker. The payload keys have already passed `PROTECTED_ENV_NAMES`, so a payload
        # cannot shadow PATH or a loader variable; `build_child_env` applies the credential floor
        # to these too, so it cannot set an AWS session either.
        run = run_processes.own()
        env = build_child_env(
            site="bash-action",
            extra={
                **payload_env,
                "PERSONALCLAW_HOOK_EVENT": ctx.event,
                "PERSONALCLAW_HOOK_CONTEXT": ctx.context,
                run_processes.RUN_VARIABLE: run.mark,
            },
        )
        argv = ["/bin/sh", "-c", command]
        try:
            wrapped_argv, cleanup_path = wrap_argv(argv)
        except SandboxEnforcementUnavailable as exc:
            # Refused, and never run outside the sandbox. Not `blocked`: that is a hook's own
            # answer (exit 2), and this command never ran to give one.
            return ActionResult(success=False, error=str(exc), duration_ms=0)

        proc = None
        try:
            # Resource ceiling: a hook/cron bash command is agent-influenced —
            # deliver the ``tool`` ceiling (full caps + OOM bias) via the post-exec shim.
            proc = await create_subprocess_limited(
                *wrapped_argv,
                profile=PROFILE_TOOL,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=json.dumps(ctx.payload).encode()),
                timeout=timeout,
            )
            elapsed = int((time.monotonic() - start) * 1000)
            exit_code = proc.returncode or 0
            return ActionResult(
                success=exit_code == 0,
                exit_code=exit_code,
                stdout=stdout_b.decode(errors="replace").strip(),
                stderr=stderr_b.decode(errors="replace").strip(),
                duration_ms=elapsed,
                blocked=exit_code == 2,
            )
        except asyncio.TimeoutError:
            # kill_timed_out is the ONE owner of this path. It replaced a hand-rolled
            # `os.killpg(proc.pid, SIGKILL)` followed by an UNBOUNDED
            # `await proc.communicate()`. Two things the owner does that the copy did
            # not: it CHECKS group leadership before signalling a group (a bare killpg
            # on a child that is not its own leader signals the gateway itself), and its
            # reap is bounded — an unbounded drain hands the command its own runtime
            # back whenever a grandchild survives the signal, wearing the timeout's name.
            if proc is not None:
                await kill_timed_out(proc)
            return ActionResult(
                success=False,
                error=f"Timed out after {timeout}s",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except asyncio.CancelledError:
            # Cancelled while it runs (its workflow run was stopped, the gateway is stopping): the
            # command is not the task's to abandon, so it is ended and collected first.
            if proc is not None:
                await terminate_and_reap(proc)
            raise
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        finally:
            if cleanup_path:
                try:
                    os.unlink(cleanup_path)
                except OSError:
                    pass
            if proc is not None:
                await run.ended(proc.pid)


def create_provider(config: dict[str, Any] | None = None) -> "BashActionProvider":
    return BashActionProvider()
