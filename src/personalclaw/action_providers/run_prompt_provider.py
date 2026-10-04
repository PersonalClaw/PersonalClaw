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
        "prompt_id": "daily-standup",   # saved prompt name; empty → `message`, else loop.md
        "message": "Remind me to ...",  # the prompt itself, when no saved prompt is named
        "vars": {"team": "infra"},      # optional: render-time variable values
        "cwd": "/path/to/project",      # optional: run dir + project loop.md lookup
        "agent": "PersonalClaw",        # optional child agent name
        "model": "...",                  # optional model override
        "max_turns": 20,                 # optional
        "session": "cron:standup",       # optional: pinned session for continuity
                                          # (default: a fresh ephemeral session);
                                          # an automation's own, never a workflow step's
        "writes": ["~/Notes/standup.md"] # optional: the files its job changes (`write_scope`);
                                          # the run changes these and nothing else
    }

When ``prompt_id`` is empty the action runs its ``message`` — the prompt an automation the
chat makes carries — and with neither, the **default-recurring-prompt** file ``loop.md``
(project ``<cwd>/loop.md`` > user ``config_dir()/loop.md``), read fresh each fire — PClaw's
analogue of Claude Code's ``loop.md``.

**A workflow step's agent answers to no chat.** ``session`` names the session the agent works in:
an automation the owner set up may pin one, and a chat named there lends the agent that chat's
Trust and posts its results into it. A workflow step's arguments may not name one, since a
template can be written or edited by a model: its agent works for the step's own run, as an agent
``invoke-agent`` starts does, and a step that names a session is refused before any agent starts,
in the words its template is refused with when it is saved (``workflows.step_arguments``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
    is_workflow_step,
    run_identity,
)
from personalclaw.action_providers.services import (
    get_action_services,
    spawn_refusal,
    validate_spawn_cwd,
)
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
    def hands_config_to_a_model(self) -> bool:
        """The variables render into the agent's prompt, so a ``{{secret:KEY}}`` stays a name."""
        return True

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
        if is_workflow_step(ctx):
            # Its agent works for the step's run: a session its arguments name is refused, before
            # anything is rendered or started.
            from personalclaw.workflows.step_arguments import dispatch_refusal

            if refused := dispatch_refusal(self.name, action_config):
                return ActionResult(success=False, error=refused, failure_class="user")
        prompt_id = str(action_config.get("prompt_id") or "").strip()
        cwd = (action_config.get("cwd") or "").strip()

        values = action_config.get("vars")
        # The Triggers UI persists empty optional fields as "" (not omitted), so an
        # empty string here means "no vars" — treat it as unset rather than a type
        # error. Only a non-empty non-dict is a genuine misconfiguration.
        if values in (None, "", {}):
            values = None
        elif not isinstance(values, dict):
            return ActionResult(success=False, error="run-prompt 'vars' must be an object")

        # No prompt_id → the action's own `message`, when it has one: what a chat-made automation
        # carries (`triggers.tools.create`'s `message`, read as this action's prompt by the
        # Triggers page and `schedule_view` alike). Measured on `main`: this provider read no
        # `message` at all, so every automation the chat made — "remind me at 5pm" included —
        # failed on its first fire with "no prompt_id and no loop.md", or ran the owner's loop.md
        # in its place.
        #
        # Neither → run the project/user default-recurring-prompt (loop.md), the thin convenience
        # that makes 'every 20m, run my loop' work with no saved-prompt id (T3). The file is the
        # prompt source; everything else (framing, spawn) is identical to a named prompt.
        message = str(action_config.get("message") or "").strip()
        source_label = f"prompt {prompt_id!r}"
        if not prompt_id and message:
            rendered, source_label = message, "the automation's message"
        elif not prompt_id:
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
        # auto-approve + T5 unattended toolset so it can't wedge. After the instruction comes what
        # started this run (`ActionContext.fire_facts`): the file that arrived, the message that
        # came. Without it, a file trigger's run read its own instruction as a request to set up
        # the automation, and reported the automation already there.
        task = with_autonomous_framing(
            f"{rendered}\n\n{ctx.fire_facts}" if ctx.fire_facts else rendered
        )
        from personalclaw import write_scope

        # The files it may change, checked again here: a scope no save would take, written some
        # other way, changes nothing.
        scope_refused = write_scope.problem(write_scope.entries(action_config))
        if scope_refused:
            return ActionResult(success=False, error=f"run-prompt: {scope_refused}")
        # What the run is called: its trigger's name, else the prompt it runs — never the framing
        # above, which is what every run started this way used to be named by.
        from personalclaw.triggers.store import run_title

        title = run_title(ctx.trigger_id, rendered)

        services = get_action_services()
        if services is None or services.subagents is None:
            return ActionResult(success=False, error="run-prompt: subagent manager unavailable")

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
        # Continuity: a session an automation's config pins accrues state across fires; the default
        # is a fresh ephemeral subagent session per fire. Never one a payload names (event data or a
        # template's own), nor a workflow step's (refused above): a chat named there would lend the
        # agent that chat's Trust.
        parent_key = str(action_config.get("session") or "").strip()

        dry_run = bool(action_config.get("dry_run", False))

        # What its agent may do, as its Allow said it (`automation_posture.fire_policy`, the check
        # invoke-agent builds its run from too): the run is built from the same mapping the Allow's
        # sentence is. §4.1 creation-time write grant: this fire is auto-fired, so it is read-only
        # unless the automation was created with ``capability: "mutating"``, and a working folder
        # (a project ``<cwd>/loop.md``, project scripts) the owner has not trusted holds it to
        # reading too: a write grant cannot silently execute project scripts there.
        from personalclaw.automation_posture import fire_policy

        policy = fire_policy(self.name, action_config)

        # Fire-and-forget: spawn() schedules the turn and returns at once. A spawn it refuses
        # there (no memory, an incident, the budget, the fan-out stop) is this fire's failure:
        # the fire says nothing when it only launched, so a refusal nobody reported would leave
        # the automation silent.
        try:
            info = services.subagents.spawn(
                task=task,
                parent_session_key=parent_key,
                agent=agent,
                max_turns=max_turns,
                model=model,
                cwd=cwd,
                approval_mode=policy.approval_mode,
                capability_class=policy.capability_class,
                silent=False,
                dry_run=dry_run,
                # The trigger whose fire this is (`ActionContext.trigger_id`): a call the agent
                # is denied names it and can be run again from the Inbox, and the agent says how
                # it went on the trigger's route when it ends.
                trigger_id=ctx.trigger_id,
                title=title,
                may_read=ctx.fire_files,
                may_change=policy.may_change,
                held_back=policy.held_back,
                # The project the step's run belongs to (from the run's record): the agent's work
                # is the project's. "" for a trigger's fire, which runs in no project.
                project_id=ctx.project_id,
                # The run whose step this is, from its dispatch ("" for a trigger's fire): a
                # workflow the agent starts is held to what that run may start
                # (`automation_version.bound_for`).
                workflow_run=run_identity(ctx, "run_id"),
            )
        except Exception as exc:  # noqa: BLE001 - a spawn that raises is this fire's failure
            logger.warning("run-prompt: spawn failed", exc_info=True)
            return ActionResult(success=False, error=f"run-prompt: the agent did not start: {exc}")
        refused = spawn_refusal(info)
        if refused or info is None:  # `spawn_refusal` names why a None did not start
            return ActionResult(success=False, error=f"run-prompt: {refused}")
        from personalclaw.subagent import agent_work_id

        # "launched", not "succeeded": we only started the background turn (T7 honesty). The
        # run's row names the agent, and says how it went when it ends.
        return ActionResult(
            success=True,
            exit_code=0,
            stdout=f"launched {source_label}",
            outcome="launched",
            work_id=agent_work_id(info.id),
        )


def create_provider(config: dict[str, Any] | None = None) -> "RunPromptActionProvider":
    return RunPromptActionProvider()
