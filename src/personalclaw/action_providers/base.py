"""Abstract base for action providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from personalclaw.errors import AgentError

if TYPE_CHECKING:
    from personalclaw.tool_providers.base import RiskLevel


@dataclass
class ActionContext:
    """Per-fire data passed to providers.

    `event` is one of the names defined in `personalclaw.hooks.HOOK_EVENTS`.
    `context` is free-form text passed via `$PERSONALCLAW_HOOK_CONTEXT` —
    most providers should prefer `payload` for structured access.
    `payload` is the structured event dict (written to bash STDIN as JSON;
    webhooks send it as the request body).
    `status_url` is the in-app route (`#/…`) to the automation that dispatched this
    action, when the dispatching seam knows it — the store-trigger fire path sets it
    to the trigger's own row. A provider whose effect IS a notification attaches it, so
    the note links back to what produced it. Kept out of `payload` on purpose: payload
    is the event itself, and it reaches bash stdin and webhook bodies verbatim.
    `answer` is what a person answered when this step last parked on them
    (`outcome="needs_input"`), set only on the ONE dispatch that answer started — a workflow
    step's re-run, or a trigger's (`triggers.parks`) — so a browse step asked "sign in, then
    confirm" gets `True` once the user has confirmed. None everywhere else. Out of `payload`
    for the same reason as `status_url`, and one more: a trigger's payload is third-party event
    data, and a fact only the engine may state must not be something a webhook body can spell.
    `trigger_id` is the store id of the trigger whose fire this is, set by the two store-trigger
    dispatches (its own fire, and Run now), or ``lifecycle:<id>`` for a lifecycle hook's fire and
    its Test (`hooks.run_script_hook`). A provider that starts an agent hands it on
    (`SubagentManager.spawn(trigger_id=…)`), so an approval that agent asks for, and the note it
    leaves if nobody answers, can name the trigger and offer to run it again.
    `fire_facts` is what started this fire, in the words the agent a provider starts is told it
    (`triggers.fire_facts`): the store dispatches compose it from the event before its payload is
    fenced, every value from outside inside a fence, and a provider that starts an agent adds it
    to the agent's task. `fire_files` are the files the fire is about (the file that arrived),
    which that agent's file tools may read. Both are the dispatch's alone, out of `payload` for
    `answer`'s reason: third-party event data must not be able to spell them.
    `project_id` is the project the workflow run this action is a step of belongs to, from the
    run's record (``engine.dispatch_action`` sets it), or "" — a trigger's fire, a hook and a tile
    run in no project. A provider that starts an agent hands it on
    (`SubagentManager.spawn(project_id=…)`), so that agent's work is the project's: its shell fills
    a ``{{secret:NAME}}`` from that project's secrets first. Out of `payload` for `answer`'s reason:
    a step's config or an event must not be able to name another project.
    `secret_references` names the secrets whose ``{{secret:NAME}}`` references the config holds
    for a provider that hands its config on to a run (`ActionProvider.hands_config_to_a_run`): the
    ones its author wrote, in an automation's action or in a workflow step (directly, or through an
    input of the step's run that was handed one), and never text an event or a step's output
    carried in. The run fills those in where its steps use them, and any other text that reads as a
    reference stays text. Set by the dispatches that fill references (a trigger's fire, Run now and
    a workflow step), empty from every other. Out of `payload` for `answer`'s reason.
    """

    event: str
    context: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    status_url: str = ""
    answer: Any = None
    trigger_id: str = ""
    fire_facts: str = ""
    fire_files: tuple[str, ...] = ()
    project_id: str = ""
    secret_references: tuple[str, ...] = ()


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
    #   "skip"    — succeeded silently with nothing to do; suppress delivery (run-script
    #               skip status). The run writes no execution row to the security log
    #               (`guardrails.rungs.record_execution`), except an action that runs code:
    #               a script that answers "skip" still ran.
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
    #               (`triggers.executor.classify`), so adding a
    #               member here means adding it to those maps in the same change.
    #   "needs_input" — the action stopped on something only a person can lift
    #               (BROWSE-AUTOMATION §5.2/§7.2: a sign-in page, max_steps, the
    #               model budget) and PRESERVED what it produced. Distinct from all
    #               three neighbours: "skip" would discard a real partial result,
    #               "launched"/"queued" both claim work that continues elsewhere,
    #               and a FAILED result would bury the partial under a red error
    #               and invite a retry that pays for the whole task again to reach
    #               the same ceiling. In a workflow, `engine.dispatch_action` maps it
    #               to a WAITING instance that ASKS the person, like a gate: the
    #               step's output is kept, `stderr` (or the card under the
    #               output's `needs_input` key) is the question, approving runs the
    #               step again with `ActionContext.answer` set, and denying ends it
    #               as declined. From a trigger, its run is recorded `waiting` and
    #               asks the same way (`triggers.parks`): approving runs the action
    #               again with the answer set. `triggers.executor.STATUS_TO_OUTCOME`
    #               maps it to `Outcome.DEFERRED` — both readers exist, per the rule
    #               above.
    #   "degraded"— the action did its work at its no-model floor
    #               (`resilience/degraded.py`): something it needs was unavailable, so it
    #               delivered less — a digest without its synthesis — and `summary` says
    #               what it went without. A trigger's run records `degraded`
    #               (`Outcome.DEGRADED`), and a workflow step is DEGRADED, a success with
    #               that reason: neither a plain success nor a failure a retry would repeat.
    outcome: str = ""
    # The WHAT/WHY/FIX envelope for a failed action. The
    # three dispatch seams wrap an uncaught provider exception into one, so
    # app-contributed providers inherit the envelope without knowing it exists.
    agent_error: "AgentError | None" = None
    # An opaque handle that identifies what this action
    # created, so it can be taken back — the ``auto_with_undo`` rung's whole point.
    # Provider-supplied because only the provider knows what "undo" means for its own
    # effect (``create-task`` returns the task row it filed). A provider that cannot
    # reverse itself leaves it EMPTY and claims no `reversal_kinds`, and the ladder then never
    # runs it "with undo" (`guardrails.rungs.route_action_type`): a run that kept no undo is
    # recorded at the rung it had, with no notification promising a reversal that cannot happen.
    reversal: str = ""
    # Why a FAILED action failed, decided by the provider that saw the cause, in the workflow
    # failure vocabulary: "user" (a config or input the user must change), "permission" (a
    # credential), "network", "transient" (5xx, a rate limit, a provider slow to answer),
    # "timeout", "budget", "protocol", "internal". Only "network" and "transient" offer Retry,
    # so this is what decides whether a run page offers one. Empty = the provider did not say:
    # the engine reads `error` through the same taxonomy's text rules and never assumes a retry
    # can help (`workflows.failure_taxonomy.classify_action_result`). The fix sentence — what to
    # change and where — rides on `agent_error.fix`.
    failure_class: str = ""
    # Seconds until a retry can run, for a failure a retry clears only after a wait: a model
    # provider's open circuit breaker refuses every call until it lapses. 0 = no wait known.
    retry_after: float = 0.0
    # What the action did, in a sentence for a person: the line its run's history row shows
    # ("Browse finished in 3 steps at example.com. Noted: …"). `stdout` stays the output a machine
    # reads (a workflow step's value, the history row's trace); a provider whose stdout is JSON
    # writes this so that the row a person reads is not the JSON. Empty = nothing to add, and the
    # row shows `stdout` (`schedule_history.summary_for_result`).
    summary: str = ""
    # The work a `launched` (or `queued`) action started and that ends later: `subagent:<id>` for
    # the agent a run-prompt or invoke-agent action starts, `workflow:<id>` for the run a
    # run-workflow action starts or queues. The run's history row keeps it, so when that work ends
    # the row says how it went instead of "launched" forever, and its trigger counts the ending
    # (`schedule_history.ScheduleRunStore.settle_sync`, `triggers.settle`). Empty for everything
    # else.
    work_id: str = ""


def provider_failure(provider_name: str, exc: BaseException) -> AgentError:
    """The generic WHAT/WHY/FIX envelope a dispatch seam wraps an uncaught provider
    exception into (PLATFORM-LEGIBILITY §2).

    A well-behaved provider returns ``ActionResult(success=False, error=…)``; a
    misbehaving (often app-contributed) one *raises*. The three dispatch seams
    (hooks, cron, event-triggers) funnel that raise through here so every provider
    — including ones that never heard of the envelope — surfaces the same coded,
    actionable failure instead of a bare ``str(exc)``. A spend ceiling's refusal is no fault of
    the provider's, and is said as itself: which ceiling, and where it is lifted.
    """
    from personalclaw.guardrails.failure import budget_refusal

    refusal = budget_refusal(exc)
    if refusal is not None:
        return refusal.envelope()
    return AgentError(
        code="ERR_ACTION_PROVIDER_FAILED",
        what=f"action provider {provider_name!r} failed: {type(exc).__name__}: {exc}",
        why="the provider raised an exception instead of returning a result",
        fix=(
            "check the action config against the provider's expected fields, "
            "or inspect the provider's logs for the underlying cause"
        ),
    )


def site_of(url: str) -> str:
    """`host[:port]` of a URL, for a sentence a provider writes about where it went — never its
    path, query or credentials, which a history row has no business repeating."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url or "")
        host, port = parsed.hostname or "", parsed.port
    except ValueError:
        return ""
    return f"{host}:{port}" if host and port else host


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
    def internal(self) -> bool:
        """Whether this provider is one step of a system-owned automation rather than an action a
        person picks for a trigger of their own.

        An internal provider stays registered and dispatchable — the automation that names it has
        to keep firing — and stays in `/api/action-providers`, so a row already naming it still
        renders its label. What it is kept out of is the create form's picker: the Self-QA steps
        take config a reconciler writes (a commit sha, a run workspace, a scenario id), so offering
        them to a person offers an action they cannot configure.
        """
        return False

    @property
    def hands_config_to_a_model(self) -> bool:
        """Whether this provider's action IS a model turn: its config becomes text an agent's model
        is handed (a task, a saved prompt's variables, a question for a second model).

        A ``{{secret:KEY}}`` in such a config is therefore never filled in at dispatch
        (``triggers.secrets.resolve``): it stays the name, and the agent's own tools fill it when
        they run (the ``bash`` tool does), so the value never reaches the model. False by default,
        because every other provider runs its config itself, and resolving there is the point.
        """
        return False

    @property
    def hands_config_to_a_run(self) -> bool:
        """Whether this provider's action starts a workflow run with its config's ``inputs`` as the
        run's inputs, naming the run's project in its ``project_id`` ("" for none).

        A ``{{secret:KEY}}`` there is never filled in at dispatch either: the run's record would
        hold the value. The dispatch checks that the run can read each one, hands the config on
        unfilled and names its references in ``ActionContext.secret_references``; the run keeps
        them as references and fills them where its steps use them (``workflows.input_secrets``).
        False by default.
        """
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

    def effect(self, action_config: dict[str, Any]) -> "RiskLevel":
        """What running this action with *action_config* does, declared the way a tool declares
        it (:class:`~personalclaw.tool_providers.base.RiskLevel`): ``SAFE`` when its one effect is
        a read, ``DESTRUCTIVE`` when it deletes, and a change (``CAUTION``) when the provider
        declares nothing — the default, so an undeclared action is never taken for a read.

        Read wherever how much an action may do unasked is decided: a workflow plan's
        confirmations (`workflows.autonomy`), and for a provider whose effect depends on its
        config, whether a trigger may fire it without the owner's grant
        (`triggers.screen.provider_is_read_only`). Never inferred from the provider's name.
        """
        from personalclaw.tool_providers.base import RiskLevel

        return RiskLevel.CAUTION

    @property
    def reversal_kinds(self) -> tuple[str, ...]:
        """The ``ActionResult.reversal`` handle kinds this provider can take back.

        A handle is ``<kind>:<provider-defined rest>`` and the KIND is the only part any
        shared code reads: it is how the undo executor
        (:func:`personalclaw.guardrails.ladder.reverse_action`) finds a provider willing to
        reverse one. Empty by default — a provider that cannot undo its own effect says so
        by staying silent, and its executions are then recorded without an undo offer
        rather than with one that would refuse.
        """
        return ()

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

    async def reverse(self, handle: str) -> ActionResult:
        """Undo what an earlier execution created, identified by its own handle.

        The other half of ``ActionResult.reversal`` (AUTONOMY-GUARDRAILS §6.1). Called ONLY
        with a handle this provider itself produced, whose kind it claimed in
        :attr:`reversal_kinds`, and only from a persisted reversal record — never with a
        client-supplied string.

        A provider MUST resolve the handle against what actually exists and return
        ``success=False`` with a reason when it does not (already deleted, changed hands,
        unparseable): the caller treats a failed reversal as a refusal that leaves the
        action's autonomy untouched, so an optimistic "sure, done" here would take away a
        user's undo AND their evidence in one call. Like ``execute``, it should return an
        error result rather than raise.

        The default is a refusal, which is the correct answer for every provider that
        never sets ``reversal``.
        """
        return ActionResult(
            success=False,
            error=f"{self.name} cannot undo its own actions",
        )
