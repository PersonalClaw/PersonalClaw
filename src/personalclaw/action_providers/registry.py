"""In-process registry of action providers."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.action_providers.base import ActionProvider


_providers: "dict[str, ActionProvider]" = {}


def register_action_provider(provider: "ActionProvider") -> None:
    _providers[provider.name] = provider


def get_action_provider(name: str) -> "ActionProvider | None":
    return _providers.get(name)


def list_action_providers() -> list[str]:
    return list(_providers.keys())


def _ensure_default_providers_registered() -> None:
    """Idempotent registration of the built-in providers.

    Called from `personalclaw.hooks` on first action execution so the providers
    are available even if no startup hook has registered them yet (tests,
    CLI invocations). These are intrinsic actions (not optional add-ons) — the
    script-hooks / triggers runtime resolves them by name (``bash`` is the default
    hook backend, ``run-script`` the deterministic script action) — so they register
    unconditionally and stay core-native.

    ``webhook`` (a self-contained HTTP-POST adapter that NO core runtime depends on)
    moved to a standalone app (apps/webhook-action) and registers via the app loader
    when installed. The four native actions (notify / send-message / create-task /
    invoke-agent) reach in-process services via the action service accessor.
    """
    if "bash" not in _providers:
        from personalclaw.action_providers.bash_provider import BashActionProvider

        register_action_provider(BashActionProvider())
    if "run-script" not in _providers:
        from personalclaw.action_providers.run_script_provider import RunScriptActionProvider

        register_action_provider(RunScriptActionProvider())
    if "notify" not in _providers:
        from personalclaw.action_providers.notify_provider import NotifyActionProvider

        register_action_provider(NotifyActionProvider())
    if "send-message" not in _providers:
        from personalclaw.action_providers.send_message_provider import SendMessageActionProvider

        register_action_provider(SendMessageActionProvider())
    if "create-task" not in _providers:
        from personalclaw.action_providers.create_task_provider import CreateTaskActionProvider

        register_action_provider(CreateTaskActionProvider())
    if "invoke-agent" not in _providers:
        from personalclaw.action_providers.invoke_agent_provider import InvokeAgentActionProvider

        register_action_provider(InvokeAgentActionProvider())
    if "run-prompt" not in _providers:
        from personalclaw.action_providers.run_prompt_provider import RunPromptActionProvider

        register_action_provider(RunPromptActionProvider())
    if "run-workflow" not in _providers:
        from personalclaw.action_providers.run_workflow_provider import RunWorkflowActionProvider

        register_action_provider(RunWorkflowActionProvider())
