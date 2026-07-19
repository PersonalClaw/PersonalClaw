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
    ActionResult,
    ActionProvider,
)

logger = logging.getLogger(__name__)

# Scrub secret-shaped env vars before exec'ing bash hooks so a hook command
# like ``env`` or ``printenv`` cannot trivially exfiltrate API keys held in
# the gateway's process environment. The hook user already has
# dashboard-token-level trust, but reducing the easy-to-extract surface is
# defense-in-depth — full RCE would still require crafting a real exploit
# rather than running ``printenv ANTHROPIC_API_KEY``.
#
# Heuristic: drop env vars whose name contains any of these tokens
# case-insensitively. False-negatives (e.g. ``MY_GITHUB_PAT`` is kept) are
# accepted as a tradeoff against false-positives (e.g. dropping
# ``PERSONALCLAW_HOOK_EVENT`` would break the feature contract).
_SECRET_NAME_PATTERNS = re.compile(
    r"(?:_KEY|_SECRET|_TOKEN|_PASSWORD|_PASSPHRASE|_CREDENTIAL|_PRIVATE|"
    r"^API_KEY$|^API_TOKEN$|^OPENAI_|^ANTHROPIC_|^AWS_|^AZURE_|^GCP_|"
    r"^GOOGLE_|^SLACK_|^GITHUB_|^GITLAB_|^GH_TOKEN|^NPM_TOKEN|"
    r"^GIT_SSH_COMMAND|^SSH_AUTH_SOCK|^GPG_)",
    re.IGNORECASE,
)
# Always keep these even if they match the secret pattern — needed by the
# hook contract or by common Unix tooling.
_KEEP_NAMES = frozenset({
    "PATH", "HOME", "USER", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "PWD", "TZ", "TMPDIR",
    "PERSONALCLAW_HOOK_EVENT", "PERSONALCLAW_HOOK_CONTEXT",
    "PERSONALCLAW_HOME", "PERSONALCLAW_WORKSPACE",
})


def _scrub_env(env: dict[str, str]) -> dict[str, str]:
    """Return *env* with secret-shaped variables stripped."""
    out: dict[str, str] = {}
    for name, val in env.items():
        if name in _KEEP_NAMES:
            out[name] = val
            continue
        if _SECRET_NAME_PATTERNS.search(name):
            continue
        out[name] = val
    return out


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _payload_env(ctx: ActionContext) -> dict[str, str]:
    """The trigger ``$variables`` (``$now``, ``$job_id``, ``$EVENT``…) as env
    vars, so a shell command resolves them natively. Env (not string-templating
    the command) on purpose: a payload value like ``last_result`` can hold
    arbitrary text — substituting it into the command line would be a shell
    injection vector. Keys that aren't valid shell identifiers are skipped."""
    out = {"EVENT": ctx.event, "CONTEXT": ctx.context}
    for k, v in (ctx.payload or {}).items():
        if _ENV_NAME.match(k):
            out[k] = str(v)
    return out


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
            return ActionResult(
                success=False, error="Bash hook is missing 'command' field"
            )

        from personalclaw.sandbox import wrap_argv

        start = time.monotonic()
        env = _scrub_env({
            **os.environ,
            **_payload_env(ctx),
            "PERSONALCLAW_HOOK_EVENT": ctx.event,
            "PERSONALCLAW_HOOK_CONTEXT": ctx.context,
        })
        argv = ["/bin/sh", "-c", command]
        wrapped_argv, cleanup_path = wrap_argv(argv)

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *wrapped_argv,
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
