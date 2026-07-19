"""Shared bundle mechanism: register an ``acp:<cli>`` AgentProvider entry.

Every ``acp:<cli>`` bundle resolves a launch ``argv`` + a dialect id + spawn env,
then needs the *same* final step: publish an ``acp_agent``
:class:`~personalclaw.llm.registry.ProviderEntry` named ``acp:<cli>`` into the
default registry. That entry is the single source of truth an ``acp:<cli>``
agent resolves through:

* :func:`personalclaw.dashboard.handlers.providers.api_agent_providers_list`
  enumerates ``acp_agent`` entries and probes each via
  :meth:`AcpAgentProvider.probe_readiness` → the readiness chip / Sign-in seam.
* An agent whose ``provider == "acp:<cli>"`` resolves to this entry through the
  provider-bridge config-registry fallback → ``registry.build`` →
  ``acp_agent._factory`` (reading ``options.command`` + ``options.dialect``).

This keeps the wiring identical to a hand-written ``config.json`` ``providers[]``
``acp_agent`` entry — the bundle just *computes* the entry from a resolved CLI
instead of the user typing the argv. The bundle factory itself returns ``None``
(agents are config/registry-based, exactly like the ``native-agents`` bundle);
registration is the side effect here.

When the CLI binary cannot be resolved the bundle registers **nothing** and the
provider simply does not appear in the agent-provider list — the absent-binary
case is surfaced cleanly as "not available" rather than a hard error, matching
the readiness-probe philosophy (present → enable, absent → skip).
"""

from __future__ import annotations

import logging

from personalclaw.llm.acp_agent import ACP_AGENT_CAPABILITY
from personalclaw.llm.registry import ProviderEntry, get_default_registry

logger = logging.getLogger(__name__)


def register_acp_cli_entry(
    *,
    cli: str,
    dialect: str,
    command: list[str] | None,
    model: str = "",
    env: dict[str, str] | None = None,
    session_files_dir: str | None = None,
    extension: str | None = None,
    login_command: list[str] | None = None,
    requires_executable: dict[str, str] | None = None,
) -> ProviderEntry | None:
    """Register (idempotently) an ``acp_agent`` entry named ``acp:<cli>``.

    Parameters
    ----------
    cli:
        The CLI suffix of the runtime id (``acp:<cli>``), e.g. ``"claude-code"``.
    dialect:
        The ACP dialect id the core registry dispatches (``options["dialect"]``).
        This MUST be explicit — ``provider_id`` derives from the command
        basename, which for an ``npx`` launch would be ``acp:npx``, so dialect
        selection never relies on the basename.
    command:
        The resolved launch argv, or ``None`` when the CLI is unavailable. When
        ``None`` (or empty) no entry is registered and ``None`` is returned.
    model:
        Optional default model hint stored on the entry.
    env:
        Optional extra environment variables forwarded to the spawned process
        (e.g. ``CLAUDE_CONFIG_DIR`` isolation, ``CLAUDE_CODE_EXECUTABLE``).
    session_files_dir:
        Optional on-disk session-files directory for agents that persist tool
        results to JSONL.
    extension:
        The bundle/extension name that owns this runtime (e.g.
        ``"claude-code-agent"``). The Agent Providers UI joins the readiness
        row back to its extension card (enable/config) by this name, so the
        bundle is the single source of truth for that linkage.
    login_command:
        Optional *suggested* sign-in argv the Sign-in terminal pre-types when
        the runtime needs authentication (e.g. ``["claude", "/login"]``). This
        is vendor-specific knowledge that lives ONLY in the bundle; the core
        never names a CLI's auth flow. The terminal is freeform, so this is
        only a starting suggestion the user can edit.
    requires_executable:
        Optional declaration that this runtime's ACP adapter is a thin shim that
        delegates the model turn to a *separate* engine CLI which must also be
        present (e.g. ``codex-acp`` → ``codex``; ``claude-agent-acp`` →
        ``claude``). Shape: ``{"label": "codex", "env_var": "CODEX_EXECUTABLE",
        "path": "<resolved path or ''>"}``. The vendor-neutral readiness probe
        enforces it: a successful ACP ``initialize`` is NOT sufficient when the
        declared engine is absent, so the runtime probes ``not_found`` instead of
        a misleading ``ready`` that would die on the first real turn. Which CLI
        delegates to what is vendor knowledge, so it lives ONLY in the bundle;
        the probe just honours the declaration. Runtimes whose binary *is* the
        engine (a self-contained ACP CLI) declare nothing.

    Returns
    -------
    The registered :class:`ProviderEntry`, or ``None`` if the CLI was
    unavailable.
    """
    if not command:
        logger.info(
            "acp:%s bundle: CLI not resolved on this machine — provider not "
            "registered (will probe as unavailable).",
            cli,
        )
        return None

    name = f"acp:{cli}"
    options: dict[str, object] = {"command": list(command), "dialect": dialect}
    if env:
        options["env"] = dict(env)
    if session_files_dir:
        options["session_files_dir"] = session_files_dir
    if extension:
        options["extension"] = extension
    if login_command:
        options["login_command"] = list(login_command)
    if requires_executable:
        options["requires_executable"] = dict(requires_executable)

    entry = ProviderEntry(
        name=name,
        type=ACP_AGENT_CAPABILITY.type,  # "acp_agent"
        model=model,
        options=options,
        credential=None,
        declared_capabilities=ACP_AGENT_CAPABILITY.capabilities,
    )

    registry = get_default_registry()
    # Idempotent: enable/disable cycles and re-imports must not raise the
    # duplicate-name guard. Replace any prior entry of the same name.
    registry.unregister_entry(name)
    try:
        registry.register_entry(entry)
    except Exception:  # noqa: BLE001 - never let a bundle break startup
        logger.warning("acp:%s bundle: failed to register provider entry", cli, exc_info=True)
        return None
    logger.info("acp:%s bundle: registered AgentProvider (dialect=%s)", cli, dialect)
    return entry


def unregister_acp_cli_entry(cli: str) -> None:
    """Remove the ``acp:<cli>`` entry (bundle disable / teardown)."""
    get_default_registry().unregister_entry(f"acp:{cli}")
