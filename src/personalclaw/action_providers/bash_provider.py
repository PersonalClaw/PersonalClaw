"""Bash hook provider — executes a shell command with templated env vars.

Runs ``/bin/sh -c <command>`` with ``PERSONALCLAW_HOOK_EVENT`` and
``PERSONALCLAW_HOOK_CONTEXT`` env vars, the structured event dict piped to
STDIN as JSON, process-group isolation, and timeout-driven SIGKILL.
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)

logger = logging.getLogger(__name__)

# The child environment is built by ALLOWLIST — `sandbox.build_child_env` — so a hook
# command like `env` or `printenv` cannot read the gateway's credentials at all. This
# replaced a name-pattern denylist (PHF-4): the denylist kept everything it did not
# recognise, which on a real gateway meant ~121 inherited variables minus the shapes the
# pattern happened to know, and it could never see a credential named in a shape nobody
# had thought of. The allowlist inverts the default.

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


#: Env vars a PAYLOAD KEY may never set (§7/R4 rule e — S129).
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
#: A DENYLIST rather than an allowlist here, deliberately and unlike S126's payload keys:
#: `$variables` are the trigger's documented user-facing surface (`$now`, `$job_id`,
#: `$last_result`, plus every key a kind's payload carries), so an allowlist would have to
#: enumerate them all and would silently drop a new kind's variables. The dangerous set,
#: by contrast, is small, well-known and stable.
PROTECTED_ENV_NAMES: frozenset[str] = frozenset(
    {
        # resolution + loader
        "PATH",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        # interpreter entry points
        "BASH_ENV",
        "ENV",
        "SHELL",
        "IFS",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "PERL5LIB",
        "NODE_OPTIONS",
        "NODE_PATH",
        "RUBYOPT",
        "RUBYLIB",
        "GIT_SSH",
        "GIT_SSH_COMMAND",
        "GIT_EXTERNAL_DIFF",
        # roots the harness reads back
        "HOME",
        "TMPDIR",
        "PERSONALCLAW_HOME",
        "PERSONALCLAW_WORKSPACE",
        "PERSONALCLAW_HOOK_EVENT",
        "PERSONALCLAW_HOOK_CONTEXT",
    }
)


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


def _sel_refusal(command: str, reason: str, ctx: "ActionContext") -> None:
    """Audit a refused bash action, using the same SEL shape `security.py` writes.

    Best-effort on purpose: an audit fault must never turn a refusal into a run. It is logged at
    WARNING rather than swallowed, so a control that stopped being recorded is visible.
    """
    try:
        import uuid
        from datetime import datetime, timezone

        from personalclaw.sel import SecurityEvent, SecurityEventLog

        SecurityEventLog().log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type="action_refused",
                caller_identity="",
                agent="personalclaw",
                source="action_provider",
                operation="bash_action_screened",
                tool_kind="execute_bash",
                outcome="denied",
                resources=reason,
                # The command is the evidence, and the SEL is local-only.
                metadata={
                    "provider": "bash",
                    "event": getattr(ctx, "event", ""),
                    "command": command[:400],
                },
            )
        )
    except Exception:  # noqa: BLE001 - never let auditing decide whether a refusal holds
        logger.warning("bash action refused (%s) but the SEL row failed", reason, exc_info=True)


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
        from personalclaw import security

        refusal = security.is_sensitive_bash_command(command)
        if refusal:
            _sel_refusal(command, refusal, ctx)
            return ActionResult(success=False, error=refusal)

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
            build_child_env,
            create_subprocess_limited,
            wrap_argv,
        )

        start = time.monotonic()
        # The allowlisted base, plus the values this site COMPUTES (never inherits): the
        # trigger's `$variables` and the hook event/context the contract promises. The
        # payload keys have already passed `PROTECTED_ENV_NAMES`, so a payload cannot
        # shadow PATH or a loader variable; `build_child_env` applies the credential floor
        # to these too, so it cannot set an AWS session either.
        env = build_child_env(
            site="bash-action",
            extra={
                **_payload_env(ctx),
                "PERSONALCLAW_HOOK_EVENT": ctx.event,
                "PERSONALCLAW_HOOK_CONTEXT": ctx.context,
            },
        )
        argv = ["/bin/sh", "-c", command]
        wrapped_argv, cleanup_path = wrap_argv(argv)

        proc = None
        try:
            # Resource ceiling (PHF-1): a hook/cron bash command is agent-influenced —
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
            import signal

            try:
                if proc is not None and proc.returncode is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    await proc.communicate()
            except Exception:
                pass
            return ActionResult(
                success=False,
                error=f"Timed out after {timeout}s",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
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


def create_provider(config: dict[str, Any] | None = None) -> "BashActionProvider":
    return BashActionProvider()
