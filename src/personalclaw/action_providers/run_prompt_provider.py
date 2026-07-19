"""``run-prompt`` action provider — run a *saved Prompt* on a trigger's cadence.

PClaw's native equivalent of Claude Code's ``/loop 20m /my-saved-prompt``: a
schedule / lifecycle / event trigger names a saved :class:`PromptSnippet` /
``PromptTemplate``, which is resolved, rendered through the prompt engine
(includes / loops / conditionals), wrapped in the autonomous-run framing, and
run as an unattended subagent turn. The unit-of-recurrence is the *saved
artifact*, decoupled from the runner — the gap E1 closes.

It reuses the same ``services.subagents.spawn`` path the marquee ``invoke-agent``
action uses, so it inherits the recursion-depth cap, the concurrency semaphore,
and the auto-approve + unattended (T5) run mode for free. The only new work is
resolving + rendering the saved prompt.

``action_config`` shape::

    {
        "prompt_id": "daily-standup",   # saved prompt name; empty → run loop.md
        "vars": {"team": "infra"},      # optional: render-time variable values
        "cwd": "/path/to/project",      # optional: run dir + project loop.md lookup
        "agent": "PersonalClaw",        # optional child agent name
        "model": "...",                  # optional model override
        "max_turns": 20,                 # optional
        "session": "cron:standup"        # optional: pinned session for continuity
                                          # (default: a fresh ephemeral session)
    }

When ``prompt_id`` is empty the action runs the **default-recurring-prompt** file
``loop.md`` (project ``<cwd>/loop.md`` > user ``config_dir()/loop.md``), read fresh
each fire — PClaw's analogue of Claude Code's ``loop.md``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from personalclaw.action_providers.services import get_action_services, validate_spawn_cwd
from personalclaw.autonomous_framing import with_autonomous_framing

logger = logging.getLogger(__name__)

# The per-project default-recurring-prompt filename (Claude Code's loop.md
# analogue). A bare run-prompt with no prompt_id runs this file's content.
LOOP_MD_NAME = "loop.md"
# Size cap so a runaway file can't blow up the turn prompt (chars).
_LOOP_MD_MAX = 16_000


def resolve_loop_md(cwd: str | None) -> tuple[str, str] | None:
    """Resolve the default-recurring-prompt file content, project > user.

    Read fresh each call (hot-reload — an edit takes effect on the next fire) and
    size-capped. Precedence (the first that exists + is non-empty wins):

    1. ``<cwd>/loop.md`` — the project-scoped default (matches Claude Code).
    2. ``config_dir()/loop.md`` — the user-global fallback.

    Returns ``(content, source_label)`` or ``None`` when neither exists.
    """
    candidates: list[tuple[Path, str]] = []
    if cwd:
        candidates.append((Path(cwd) / LOOP_MD_NAME, f"project:{cwd}"))
    try:
        from personalclaw.config.loader import config_dir

        candidates.append((config_dir() / LOOP_MD_NAME, "user"))
    except Exception:
        logger.debug("loop.md: config_dir lookup failed", exc_info=True)

    for path, label in candidates:
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text.strip():
            continue
        if len(text) > _LOOP_MD_MAX:
            text = text[:_LOOP_MD_MAX] + "\n…[loop.md truncated]"
        return text, label
    return None


def render_saved_prompt(prompt_id: str, values: dict[str, Any] | None) -> str:
    """Resolve the saved prompt ``prompt_id`` and render it with ``values``.

    Raises ``LookupError`` when no such prompt exists, ``ValueError`` when the
    prompt engine rejects the render (e.g. a required variable is unset). Shared
    with the ``loop.md`` convenience (T3), which renders a file-sourced prompt
    through the same engine.
    """
    from personalclaw.prompt_providers import (
        get_default_provider,
        render_template,
    )
    from personalclaw.prompt_providers.base import PromptRenderError

    provider = get_default_provider()
    if provider is None:
        raise LookupError("no prompt provider is registered")
    template = provider.get_prompt(prompt_id)
    if template is None:
        raise LookupError(f"no saved prompt named {prompt_id!r}")
    # The engine resolves {{> snippet}} includes against the same provider so a
    # prompt that composes shared fragments renders fully. PromptRenderError
    # (e.g. a required variable unset) is NOT a ValueError subclass, so normalize
    # it to ValueError to honor this function's contract — otherwise it escapes
    # the caller's `except ValueError` and breaks the action's error-result path.
    try:
        return render_template(template, values or {}, resolver=provider.get_snippet)
    except PromptRenderError as exc:
        raise ValueError(str(exc)) from exc


class RunPromptActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "run-prompt"

    @property
    def display_name(self) -> str:
        return "Run Prompt"

    @property
    def supports_dry_run(self) -> bool:
        # The spawned turn runs with observe-mode tools (subagent dry_run=True):
        # write-capable tools preview instead of executing.
        return True

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        prompt_id = str(action_config.get("prompt_id") or "").strip()
        cwd = (action_config.get("cwd") or "").strip()

        values = action_config.get("vars")
        # The Triggers UI persists empty optional fields as "" (not omitted), so an
        # empty string here means "no vars" — treat it as unset rather than a type
        # error. Only a non-empty non-dict is a genuine misconfiguration.
        if values in (None, "", {}):
            values = None
        elif not isinstance(values, dict):
            return ActionResult(
                success=False, error="run-prompt 'vars' must be an object"
            )

        # No prompt_id → run the project/user default-recurring-prompt (loop.md),
        # the thin convenience that makes 'every 20m, run my loop' work with no
        # saved-prompt id (T3). The file is the prompt source; everything else
        # (framing, spawn) is identical to a named prompt.
        source_label = f"prompt {prompt_id!r}"
        if not prompt_id:
            loop_md = resolve_loop_md(cwd)
            if loop_md is None:
                return ActionResult(
                    success=False,
                    error=(
                        "run-prompt has no 'prompt_id' and no loop.md was found "
                        "(looked for a project loop.md in cwd, then a user loop.md)"
                    ),
                )
            rendered, where = loop_md
            source_label = f"loop.md ({where})"
        else:
            try:
                rendered = render_saved_prompt(prompt_id, values)
            except LookupError as exc:
                return ActionResult(success=False, error=f"run-prompt: {exc}")
            except ValueError as exc:
                return ActionResult(
                    success=False, error=f"run-prompt: failed to render {prompt_id!r}: {exc}"
                )
            if not rendered.strip():
                return ActionResult(
                    success=False,
                    error=f"run-prompt: prompt {prompt_id!r} rendered empty",
                )

        # The turn runs unattended (no user to answer) — frame it so the model
        # doesn't fall back to questions / option menus, and rely on the spawn's
        # auto-approve + T5 unattended toolset so it can't wedge.
        task = with_autonomous_framing(rendered)

        services = get_action_services()
        if services is None or services.subagents is None:
            return ActionResult(
                success=False, error="run-prompt: subagent manager unavailable"
            )

        # Pre-validate cwd so an out-of-allowlist dir returns an honest error now
        # rather than a false "launched" (the fire-and-forget spawn refuses cwd
        # asynchronously, where the failure wouldn't reach this result).
        cwd_err = validate_spawn_cwd(cwd)
        if cwd_err:
            return ActionResult(success=False, error=f"run-prompt: {cwd_err}")

        agent = (action_config.get("agent") or "").strip()
        model = (action_config.get("model") or "").strip() or None
        try:
            max_turns = int(action_config.get("max_turns", 0) or 0)
        except (ValueError, TypeError):
            max_turns = 0
        # Continuity: a pinned session accrues state across fires; the default is
        # a fresh ephemeral subagent session per fire (mirrors cron invoke-agent).
        parent_key = str(action_config.get("session") or "").strip() or str(
            (ctx.payload or {}).get("session_key", "") or ""
        )

        dry_run = bool(action_config.get("dry_run", False))

        async def _spawn() -> None:
            try:
                services.subagents.spawn(  # type: ignore[union-attr]
                    task=task,
                    parent_session_key=parent_key,
                    agent=agent,
                    max_turns=max_turns,
                    model=model,
                    cwd=cwd,
                    approval_mode="auto",
                    silent=False,
                    dry_run=dry_run,
                )
            except Exception:
                logger.warning("run-prompt: spawn failed", exc_info=True)

        # Fire-and-forget: the trigger returns immediately; the prompt turn runs
        # in the background (spawn() schedules the run internally).
        services.spawn_background(_spawn())
        # "launched", not "succeeded": we only started the background turn; its
        # real outcome is recorded by the spawned run itself (T7 honesty).
        return ActionResult(
            success=True, exit_code=0, stdout=f"launched {source_label}", outcome="launched"
        )


def create_provider(config: dict[str, Any] | None = None) -> "RunPromptActionProvider":
    return RunPromptActionProvider()
