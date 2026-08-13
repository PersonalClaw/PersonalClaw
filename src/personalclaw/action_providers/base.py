"""Abstract base for action providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from personalclaw.errors import AgentError


@dataclass
class ActionContext:
    """Per-fire data passed to providers.

    `event` is one of the names defined in `personalclaw.hooks.HOOK_EVENTS`.
    `context` is free-form text passed via `$PERSONALCLAW_HOOK_CONTEXT` —
    most providers should prefer `payload` for structured access.
    `payload` is the structured event dict (written to bash STDIN as JSON;
    webhooks send it as the request body).
    """

    event: str
    context: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionResult:
    """Provider-agnostic outcome of executing an action."""

    success: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    duration_ms: int = 0
    # ACP semantic: PreToolUse exit_code 2 is a block signal. Providers that
    # support blocking (e.g. bash) set this; non-blocking providers (e.g.
    # webhook) leave it false.
    blocked: bool = False
    # Optional success refinement for scheduled runs:
    #   ""        — normal synchronous success (the action's work completed)
    #   "skip"    — succeeded silently; suppress delivery (run-script skip status)
    #   "done"    — one-shot; remove the job after this run (run-script done status)
    #   "launched"— the action only STARTED background work (a fire-and-forget
    #               spawn: run-prompt / run-workflow / invoke-agent). The turn's
    #               real outcome is NOT known yet, so the run record says
    #               "launched", not "succeeded" — honest "started ≠ succeeded"
    #               status (T7). The spawned turn records its own outcome.
    #   "queued"  — the action persisted a durable intent and started NOTHING
    #               (run-workflow under `on_overlap: queue`, held behind a run
    #               already in flight). Distinct from both neighbours on purpose:
    #               "skip" would under-report a real run record, and "launched"
    #               would claim work that has not begun. Every reader of this
    #               field maps it to `Outcome.DEFERRED` — a status this
    #               vocabulary does not recognise is recorded as FAILED
    #               (`triggers.executor._record_fire_outcome`), so adding a
    #               member here means adding it to those maps in the same change.
    outcome: str = ""
    # PLATFORM-LEGIBILITY §2: the WHAT/WHY/FIX envelope for a failed action. The
    # three dispatch seams wrap an uncaught provider exception into one, so
    # app-contributed providers inherit the envelope without knowing it exists.
    agent_error: "AgentError | None" = None
    # AUTONOMY-GUARDRAILS §5.2: an opaque handle that identifies what this action
    # created, so it can be taken back — the ``auto_with_undo`` rung's whole point.
    # Provider-supplied because only the provider knows what "undo" means for its own
    # effect (``create-task`` returns the task row it filed). A provider that cannot
    # reverse itself leaves it EMPTY, and the seam then records the execution without
    # offering an undo: `guardrails.rungs.record_reversal` skips the notification
    # entirely rather than promise a reversal that cannot happen.
    reversal: str = ""


def provider_failure(provider_name: str, exc: BaseException) -> AgentError:
    """The generic WHAT/WHY/FIX envelope a dispatch seam wraps an uncaught provider
    exception into (PLATFORM-LEGIBILITY §2).

    A well-behaved provider returns ``ActionResult(success=False, error=…)``; a
    misbehaving (often app-contributed) one *raises*. The three dispatch seams
    (hooks, cron, event-triggers) funnel that raise through here so every provider
    — including ones that never heard of the envelope — surfaces the same coded,
    actionable failure instead of a bare ``str(exc)``.
    """
    return AgentError(
        code="ERR_ACTION_PROVIDER_FAILED",
        what=f"action provider {provider_name!r} failed: {type(exc).__name__}: {exc}",
        why="the provider raised an exception instead of returning a result",
        fix=(
            "check the action config against the provider's expected fields, "
            "or inspect the provider's logs for the underlying cause"
        ),
    )


class ActionProvider(ABC):
    """Pluggable execution backend for a trigger's action."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier (e.g. ``bash``, ``webhook``)."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable label."""
        ...

    @property
    def supports_blocking(self) -> bool:
        """Whether the provider can short-circuit a tool call (PreToolUse)."""
        return False

    @property
    def supports_dry_run(self) -> bool:
        """Whether the provider honors ``action_config["dry_run"]`` with a real
        observe-mode execution (write-capable tools preview instead of executing).

        Only the spawn-based LLM providers (run-prompt / run-workflow) can — their
        turn runs with observe-mode tools, so the preview is meaningful AND safe.
        Deterministic providers (bash, run-script, webhook, …) execute their config
        directly and have no observe mode: a dry-run dispatch would run the REAL
        side effects while the UI promises none. The dispatcher refuses to execute
        a dry run against a provider that returns False here and records a preview
        of what WOULD run instead (T9 honesty)."""
        return False

    @abstractmethod
    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        """Run the action with provider-specific config + the shared context.

        `action_config` is the per-action payload (e.g. `{"command": "..."}` for
        bash, `{"url": "...", "method": "POST"}` for webhook). Providers
        validate fields they need; missing keys should produce an error
        result rather than raising.
        """
        ...
