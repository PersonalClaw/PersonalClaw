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

import json
import logging

from personalclaw import app_code
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
    self_sandboxing: bool = False,
    compacts_itself: bool = False,
    env_passthrough: list[str] | None = None,
    session_meta: dict[str, object] | None = None,
    credential_files: tuple[str, ...] = (),
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
    env_passthrough:
        Optional names of variables the CLI reads to pick its provider, its region or its
        model (e.g. ``CLAUDE_CODE_USE_BEDROCK``, ``AWS_PROFILE``, ``AWS_REGION``,
        ``ANTHROPIC_MODEL``), handed to it from the gateway's environment when set there. A CLI
        gets no other variable of the gateway's (`sandbox.build_child_env`), so without the
        declaration an owner's provider selection never reaches it. Never a credential: a
        credential-shaped name is refused here with a warning, and again at every spawn
        (`sandbox.app_env_name_refusal`), because the CLI reads its keys from its own config or
        credential files. Which variables a CLI reads is vendor knowledge, so the list lives
        ONLY in the bundle.
    session_meta:
        Optional ``_meta`` object the CLI, or its ACP adapter, reads on ``session/new`` and
        ``session/load`` — its per-session options, such as which of the CLI's own
        configuration sources a session loads. Core sends it as declared on every session it
        opens from this entry (the runtime factory, a resumed session, the readiness probe and
        a pooled connection), so no path opens a session with the adapter's defaults instead.
        Which keys an adapter reads is vendor knowledge, so the object lives ONLY in the
        bundle. It must be a JSON object; anything else raises :class:`ValueError` and
        registers nothing, because a declaration that cannot be sent must not quietly become
        none.
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
    self_sandboxing:
        Optional declaration that this runtime applies its OWN OS-level sandbox to
        the child it spawns, so the host's path sandbox cannot nest around it. Set
        it and the entry carries ``sandbox_mode="off"``; leave it and the host wrap
        applies as usual.

        Same fact/policy split as ``requires_executable``: *whether a given CLI
        sandboxes itself* is vendor knowledge and lives ONLY in the bundle, while
        *what to do about it* stays here. A bundle therefore never picks a sandbox
        level — it states a property of its binary and the core derives the mode.

        This is not a theoretical knob. The host's generated seatbelt profile refuses
        a nested ``sandbox_apply`` (measured: ``Operation not permitted``; nesting is
        not refused *per se* — a bare ``(allow default)`` profile nests fine, it is
        the profile's ``deny`` rules that forbid it). So wrapping such a CLI in
        ``sandbox-exec`` kills it during startup: kiro-cli exits 1 having written
        nothing to stdout, and the host sees only an ``ACP stdout EOF`` handshake
        failure whose real cause ("sandbox initialization failed: Operation not
        permitted") is on the child's stderr.
        Turning the host wrap off for such a runtime does not leave it unconfined:
        the CLI's own sandbox still applies, and so do the host's PreToolUse deny
        gate and four-tier approval, which are where ACP tool authority actually
        lives.
    compacts_itself:
        Optional declaration that this CLI compacts its own conversation, on its own, when its
        context fills: a summary replaces its older turns and the session goes on. Set it and
        core leaves the runtime's sessions alone at the Settings threshold
        (``session.autocompact_pct``), so a session keeps what the CLI holds, the results of
        its earlier tool calls included. Leave it and core restarts a session that crosses the
        threshold, since core cannot compact a conversation that lives inside the CLI: the next
        turn starts a fresh session from the chat's own history, and the chat is told so.

        Same fact/policy split as ``self_sandboxing``: whether a CLI compacts itself, and at
        what limit, is vendor knowledge and lives ONLY in the bundle, read from the CLI's own
        behaviour rather than assumed. No ACP handshake says it, so the bundle is where it is
        said. Core reads it on every path that opens a session from this entry (the runtime
        factory and a pooled connection, ``acp_agent.options_compacts_itself``).

    A CLI's own agent-config directory is deliberately NOT a parameter here.
    ACP-AGENT-PARITY §2.1 measured all three shipped CLIs honouring the
    protocol's ``session/new`` ``mcpServers`` array (prong A,
    :mod:`personalclaw.acp.mcp_servers`), so seeding a vendor config file is
    unnecessary as well as unwired — see the `AAP-4` DEVIATION 2 log entry.

    This parameter used to cite `K6` — that kiro reads only ``<cwd>/.kiro/agents``
    and ``~/.kiro/agents``, so the config the host writes under
    ``$PERSONALCLAW_HOME/agents/`` is never seen. That measurement still holds; it
    just does not imply seeding is needed. It is a fact about a config **file
    path**, and the protocol array is an independent channel: `K54`/`K100` watched
    a kiro session receive the whole ``personalclaw-core`` surface with nothing in
    ``~/.kiro`` naming us. Don't re-add the parameter on the strength of `K6`.

    Returns
    -------
    The registered :class:`ProviderEntry`, or ``None`` if the CLI was
    unavailable.

    Raises
    ------
    personalclaw.apps.declared.NotDeclared
        When an app's code registers a CLI its manifest does not declare: core starts this
        command for every chat on the runtime, so it is held to what the app's install review
        named (:func:`_held_to_the_review`). The message is the provider card's sentence.
    """
    if not command:
        logger.info(
            "acp:%s bundle: CLI not resolved on this machine — provider not "
            "registered (will probe as unavailable).",
            cli,
        )
        return None
    if credential_files:
        from personalclaw.security import register_agent_sign_in_files

        register_agent_sign_in_files(cli, credential_files)
    _held_to_the_review(command, requires_executable)
    meta = _session_meta(cli, session_meta)

    name = f"acp:{cli}"
    options: dict[str, object] = {"command": list(command), "dialect": dialect}
    if meta:
        options["session_meta"] = meta
    if env:
        options["env"] = dict(env)
    passthrough = _passthrough_names(cli, env_passthrough)
    if passthrough:
        options["env_passthrough"] = passthrough
    if session_files_dir:
        # A declared directory is PROVISIONED here, not merely recorded: the readers
        # (``AcpClient``'s ``_meta`` session-file hint, ``AcpSession``'s JSONL
        # tool-result tail) both probe for files inside it, and a path that does not
        # exist makes every probe a silent miss the bundle cannot distinguish from an
        # empty directory. Creation failure downgrades to "not declared" rather than
        # advertising a directory nothing can read.
        from pathlib import Path

        try:
            Path(session_files_dir).mkdir(parents=True, exist_ok=True)
            options["session_files_dir"] = session_files_dir
        except OSError:
            logger.warning(
                "acp:%s bundle: could not create session_files_dir %s — option dropped",
                cli,
                session_files_dir,
                exc_info=True,
            )
    if extension:
        options["extension"] = extension
    if login_command:
        options["login_command"] = list(login_command)
    if requires_executable:
        options["requires_executable"] = dict(requires_executable)
    if self_sandboxing:
        # Bundle states the fact (this CLI sandboxes itself); core picks the policy.
        options["sandbox_mode"] = "off"
    if compacts_itself:
        # The same split: the bundle says the CLI compacts itself, and the session manager
        # leaves its sessions alone at the threshold (``AcpAgentProvider.compacts_automatically``).
        options["compacts_itself"] = True

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
    # The runtime leaves with the app that registered it: disabled, removed or updated, its
    # `acp:<cli>` entry must not keep resolving to the command the old version computed.
    app_code.keep(lambda: _forget(entry))
    logger.info("acp:%s bundle: registered AgentProvider (dialect=%s)", cli, dialect)

    return entry


def _held_to_the_review(command: list[str], requires_executable: dict[str, str] | None) -> None:
    """Refuse a CLI registration the calling app's install review did not name.

    The review names the programs the app's manifest lists under ``launches`` and the npm
    packages under ``dependencies.npmPackages`` (``apps.disclosure``). So an app that registers
    an agent CLI must list a program; the engine an adapter hands each turn to
    (``requires_executable``) must be one of them, being the program that runs as the owner with
    its own sign-in; and an adapter run through ``npx``, which fetches it first, must be a listed
    package. A registration core makes itself is not an app's and is not checked."""
    from personalclaw.acp.cli_resolve import npx_package
    from personalclaw.apps.declared import (
        UNNAMED,
        NotDeclared,
        declared_programs,
        declares_npm_package,
    )

    programs = declared_programs()
    if programs is None:
        return
    engine = str((requires_executable or {}).get("label") or "").strip()
    if engine and engine not in programs:
        reason = f"it starts {engine}, and its manifest does not list that program under launches"
    elif not programs:
        reason = "it starts an agent CLI, and its manifest lists no program under launches"
    elif (package := npx_package(command)) and not declares_npm_package(package):
        reason = (
            f"it fetches {package} with npx and runs it, and its manifest does not list that "
            "package under dependencies.npmPackages"
        )
    else:
        return
    raise NotDeclared(f"Not started: {reason}, {UNNAMED}.")


def _session_meta(cli: str, declared: object) -> dict[str, object]:
    """*declared* as the entry keeps it: a JSON object, copied, so a later change to the app's
    own dict cannot change what its sessions are sent. Anything else is refused."""
    if declared is None:
        return {}
    try:
        if not isinstance(declared, dict):
            raise TypeError(f"a {type(declared).__name__}")
        copied = json.loads(json.dumps(declared, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"acp:{cli}: session_meta must be a JSON object to send on session/new and "
            f"session/load ({exc})"
        ) from None
    return copied


def _passthrough_names(cli: str, names: list[str] | None) -> list[str]:
    """*names* as the entry keeps them: sorted, once each, and none an app may not declare."""
    from personalclaw.sandbox import app_env_name_refusal

    kept: set[str] = set()
    for raw in names or []:
        name = str(raw).strip()
        why = app_env_name_refusal(name)
        if why:
            logger.warning(
                "acp:%s bundle: env_passthrough names %r, which is %s; it is not passed",
                cli,
                name,
                why,
            )
            continue
        kept.add(name)
    return sorted(kept)


def _forget(entry: ProviderEntry) -> None:
    registry = get_default_registry()
    if registry._entries.get(entry.name) is entry:  # noqa: SLF001 — take back only this one
        registry.unregister_entry(entry.name)


def unregister_acp_cli_entry(cli: str) -> None:
    """Remove the ``acp:<cli>`` entry (bundle disable / teardown).

    Registry-only: a disabled bundle leaves nothing of ours behind because
    nothing of ours was ever written into the CLI's own config. The MCP surface
    an ACP session sees is passed per ``session/new`` (prong A), so it vanishes
    with the session rather than needing a teardown.
    """
    from personalclaw.security import unregister_agent_sign_in_files

    unregister_agent_sign_in_files(cli)
    get_default_registry().unregister_entry(f"acp:{cli}")
