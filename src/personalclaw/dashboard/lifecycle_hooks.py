"""The gateway's start and stop hooks: what the dashboard starts when it starts, and stops when
it stops.

``start_dashboard`` calls :func:`register_lifecycle_hooks` once, while it builds the app and before
``runner.setup()`` freezes ``on_startup`` and ``on_cleanup``. aiohttp runs the ``on_startup`` hooks
in the order they were appended when the gateway starts, and the ``on_cleanup`` hooks in that order
when it stops, so the order below is the order they run. A service that only starts after the
freeze (the durability loop, the artifact change relay) has its stop registered here all the same,
and the stop reads what it stops off the app's state.
"""

import asyncio
import logging

from aiohttp import web

from personalclaw.cancellation import cancel_and_wait

logger = logging.getLogger(__name__)


def register_lifecycle_hooks(app: web.Application) -> None:
    """Append the gateway's start and stop hooks to *app*, in the order they run."""

    async def _transports_startup(app_: web.Application) -> None:
        """Register the always-present in-app Web UI transport at boot.

        Extension-backed transports (Slack, and future Telegram/Discord) are
        registered by the provider registry's ChannelTypeHandler when their
        extension is enabled — one source of truth, no parallel startup path.
        """
        from personalclaw.channel_transports import register_default_transports

        try:
            register_default_transports()
        except Exception:
            logger.exception("Failed to register the Web UI channel transport")

    app.on_startup.append(_transports_startup)

    async def _control_bridge_startup(app_: web.Application) -> None:
        """Bind the loopback control bridge on its own random port (EXTERNAL-ACCESS §4).

        Its OWN runner, not a route here: the dashboard's port is knowable and a control
        surface on a knowable port is a port-scan away from being probed. A mount refusal
        is normal (the surface is off by default) and must never block gateway startup —
        so this swallows, logs, and leaves no discovery file behind.
        """
        from personalclaw.inbound import bridge as _bridge

        try:
            await _bridge.start(app_["state"])
        except Exception:
            logger.warning("control bridge failed to start", exc_info=True)
            try:
                _bridge.remove_discovery()
            except Exception:
                pass

    app.on_startup.append(_control_bridge_startup)

    async def _control_bridge_shutdown(app_: web.Application) -> None:
        """Tear the bridge down and DELETE its discovery file: a file naming a dead
        port is worse than no file, because a client trusts it and hangs."""
        from personalclaw.inbound import bridge as _bridge

        try:
            await _bridge.stop()
        except Exception:
            logger.debug("control bridge shutdown failed", exc_info=True)

    app.on_cleanup.append(_control_bridge_shutdown)

    async def _settle_outside_home_startup(app_: web.Application) -> None:
        """Once per home: copy home the skills earlier releases installed in ~/.agents/skills,
        and let go of a saved workspace pointer that is only the old default. Writes nothing
        outside the home (``outside_home.settle_previous_locations``)."""
        from personalclaw.outside_home import settle_previous_locations

        try:
            report = await asyncio.to_thread(settle_previous_locations)
            if report.get("skills_copied") or report.get("workspace_pointer_dropped"):
                logger.info("Settled what earlier releases kept outside the home: %s", report)
        except Exception:
            logger.exception("Failed to settle what earlier releases kept outside the home")

    app.on_startup.append(_settle_outside_home_startup)

    async def _settle_learning_from_text_not_typed_startup(app_: web.Application) -> None:
        """Take back what learning read out of text nobody typed, before it read only a person's
        own words: retract the correction lessons quoted from that text, and offer what only may
        have come from it for review (``learning.composed_text``). Changes nothing once there is
        nothing left to take back."""
        from personalclaw.learning import composed_text

        builder = getattr(app_["state"], "context_builder", None)
        main = getattr(getattr(builder, "memory", None), "vector_store", None)
        try:
            report = await asyncio.to_thread(composed_text.settle, main)
            if report["retracted"] or report["offered"]:
                logger.info("Took back what was learned from text nobody typed: %s", report)
        except Exception:
            logger.exception("Could not take back what was learned from text nobody typed")

    app.on_startup.append(_settle_learning_from_text_not_typed_startup)

    async def _record_running_version_startup(app_: web.Application) -> None:
        """RUM-9: remember which version ran last, so a rollback has a target.

        The ONE writer of ``updates.last_version``. It fires here — once per gateway
        start, before anything can serve ``/api/update/check`` — because a version
        change is only ever observable across a restart, and because this is the one
        place that sees the change no matter HOW it happened: our own apply, a
        container recreated onto a new image tag, a desktop app replaced by its own
        installer, or a plain ``pip install -U personalclaw`` typed by hand.

        Writes nothing on the first recorded start (there is no earlier version to
        offer) and nothing when the version is unchanged, so the steady state is a
        single cheap file read.
        """
        from personalclaw import __version__ as _running_version
        from personalclaw import self_update as _self_update

        try:
            _self_update.record_running_version(_running_version)
        except Exception:
            # A missed rollback offer is a cosmetic loss; a failed gateway start is not.
            logger.debug("could not record the running version", exc_info=True)

    app.on_startup.append(_record_running_version_startup)

    async def _action_providers_startup(app_: web.Application) -> None:
        """Register the bundled action providers (bash, webhook, run-script, …)."""
        from personalclaw.action_providers.registry import _ensure_default_providers_registered

        try:
            _ensure_default_providers_registered()
        except Exception:
            logger.exception("Failed to register action providers")

    app.on_startup.append(_action_providers_startup)

    async def _prompt_providers_startup(app_: web.Application) -> None:
        """Register the bundled native filesystem prompt provider."""
        from personalclaw.prompt_providers.registry import _ensure_default_providers_registered

        try:
            _ensure_default_providers_registered()
        except Exception:
            logger.exception("Failed to register prompt providers")

    app.on_startup.append(_prompt_providers_startup)

    async def _projection_rules_startup(app_: web.Application) -> None:
        """Install the user's tool-output projection rules (TokenJuice OP6) into the
        projection engine so a large output of a user-taught type keeps its salient
        slice instead of a blunt cut. Fail-soft — a bad rule is skipped, never fatal."""
        try:
            from personalclaw.config.loader import AppConfig
            from personalclaw.tool_providers import projection

            projection.set_user_rules(
                [
                    projection.ProjectionRule(
                        name=r.name,
                        match_regex=r.match_regex,
                        strategy=r.strategy,
                        head=r.head,
                        tail=r.tail,
                        keep=r.keep,
                        skip=r.skip,
                        count=r.count,
                    )
                    for r in AppConfig.load().tools.projection_rules
                ]
            )
        except Exception:
            logger.exception("Failed to install tool-output projection rules")

    app.on_startup.append(_projection_rules_startup)

    async def _skill_catalogs_startup(app_: web.Application) -> None:
        """Register the operator's configured skill catalogs (``packs.skill_catalogs``,
        AP-6) on the shared skills registry so the Skills store can browse them.

        Each catalog registers at COMMUNITY tier and installs through the same
        ``install_guarded`` chokepoint as every other marketplace. Fail-soft per
        catalog inside ``register_skill_catalogs``; a total failure is logged, never
        fatal — an unreachable catalog must not cost the bundled marketplaces."""
        try:
            from personalclaw.packs.catalog_marketplace import register_skill_catalogs

            names = register_skill_catalogs()
            if names:
                logger.info("Registered %d skill catalog(s): %s", len(names), ", ".join(names))
        except Exception:
            logger.exception("Failed to register configured skill catalogs")

    app.on_startup.append(_skill_catalogs_startup)

    async def _app_sources_seed_startup(app_: web.Application) -> None:
        """Seed the shipped app-registry git source into ``app-sources.json`` — once, ever
        (ECOSYSTEM-TOOLING T2.2).

        This is the "first run" site: the seed writes one removable row and a marker, so
        removing the source in the Store persists across every later start. Gated by
        ``apps.registry_source_enabled``. Store LISTING only — it adds no install path, and
        installing from it still goes through the scanner gate. Fail-soft: a sources-file
        problem must never cost the gateway its boot."""
        try:
            from personalclaw.apps.catalog import seed_default_git_sources

            seeded = await asyncio.to_thread(seed_default_git_sources)
            if seeded:
                logger.info("Seeded default app source(s): %s", ", ".join(seeded))
        except Exception:
            logger.exception("Failed to seed default app sources")

    app.on_startup.append(_app_sources_seed_startup)

    async def _context_engine_startup(app_: web.Application) -> None:
        """Install the configured context engine (#1783) — the installer the seam lacked.

        ``set_engine`` had no production caller other than its own quarantine path, so
        ``DefaultContextEngine`` was the only engine that could ever be active and the
        whole swappable seam was unreachable. This reads ``session.context_engine`` ONCE,
        here, and resolves it against the registry.

        Registered AFTER the provider/source hooks above so anything they register is in
        the registry before a name is resolved.

        ``install_engine`` fails closed to the default on an unknown name, a factory that
        raises, or an instance that misses a hook, so this cannot darken chat; the
        try/except only covers an unreadable config. It logs the engine actually
        installed, never the one requested."""
        try:
            from personalclaw.config.loader import AppConfig
            from personalclaw.context_engine import DEFAULT_ENGINE_NAME, install_engine

            active = install_engine(AppConfig.load().session.context_engine)
            if active != DEFAULT_ENGINE_NAME:
                logger.info("Context engine: %s", active)
        except Exception:
            logger.exception("Failed to install the configured context engine")

    app.on_startup.append(_context_engine_startup)

    async def _model_providers_startup(app_: web.Application) -> None:
        """Register config model-managers as local providers; retry the legacy migration.

        config.json ``providers[]`` are NOT replayed here. That happens exactly once, in
        ``start_dashboard``'s synchronous body, because ``setup_knowledge_routes`` builds the
        knowledge embedder during app construction — before any on_startup hook — and
        would otherwise see an empty registry. A second replay used to sit here and was
        measured returning 0 entries on every boot: the body call is unguarded, so it has
        either registered everything already or taken the boot down with it, leaving this
        one nothing to do. ``migrate_legacy_bindings`` DOES belong here as a retry: it
        unlinks the legacy file only on success, so a partial failure of the (silently
        swallowed) body call leaves real work, and this copy logs it.
        """
        from personalclaw.providers.use_cases import migrate_legacy_bindings

        try:
            migrate_legacy_bindings()
        except Exception:
            logger.exception("Failed to migrate legacy use-case bindings")
        try:
            from personalclaw.local_models.registry import register_config_model_managers

            register_config_model_managers()
        except Exception:
            logger.exception("Failed to register config model-managers as local providers")

    app.on_startup.append(_model_providers_startup)

    async def _embedding_watch_startup(app_: web.Application) -> None:
        """Watch the embedding binding (``embedding_reindex.watch_embedding_binding``).

        Its first pass is the start's check: a gateway that died mid-re-index (crash/kill/OOM)
        leaves knowledge items with text but no embedding, or an old wrong-width vector, and memory
        vectors of the previous model; an update that started recording each memory vector's model
        leaves every memory vector naming none. Either way the store is read by keyword against the
        model bound now, and the check finishes the re-index. After that it takes the one path
        whenever the binding changes by a way this process did not make and whenever a model that
        was not ready is due another look, so a model bound before its provider was up — at this
        start or later — is re-indexed once it is. In the background, so a slow provider's probe
        never holds the start; runs AFTER _model_providers_startup, so its first pass sees the
        providers that registers."""
        registry = app_["state"].embedding_reindex()
        if registry.watch is None:
            from personalclaw.dashboard.handlers.embedding_reindex import watch_embedding_binding

            registry.watch = asyncio.ensure_future(watch_embedding_binding(app_))

    app.on_startup.append(_embedding_watch_startup)

    async def _embedding_watch_shutdown(app_: web.Application) -> None:
        """Stop the watch on gateway stop, so it does not outlive the gateway that started it."""
        registry = app_["state"].embedding_reindex()
        task, registry.watch = registry.watch, None
        # Bounded: a re-index runs the bound embedding provider's code, which may be starting a
        # process that a cancel cannot always interrupt (`cancel_and_wait`).
        await cancel_and_wait([task], what="embedding binding watch")

    app.on_cleanup.append(_embedding_watch_shutdown)

    async def _acp_pool_startup(app_: web.Application) -> None:
        """Install the ACP connection pool: the shared connections a user's concurrent chats
        open sessions on, and its runner-lease sweep. It starts NO agent CLI — PersonalClaw
        never starts another agent's CLI unless the user asks, so an installed runtime runs
        for the first chat that uses it, or for the Test on its card. Best-effort — failures
        never affect the gateway."""
        try:
            import asyncio as _asyncio

            from personalclaw.acp.connection_pool import init_acp_pool

            st = app_.get("state")
            start_sem = getattr(getattr(st, "sessions", None), "_start_sem", None)
            if start_sem is None:
                start_sem = _asyncio.Semaphore(4)
            await init_acp_pool(start_sem)
        except Exception:
            logger.debug("ACP pool startup failed", exc_info=True)

    app.on_startup.append(_acp_pool_startup)

    async def _acp_pool_shutdown(app_: web.Application) -> None:
        """Drain + shut down all pooled ACP connections on gateway stop."""
        try:
            from personalclaw.acp.connection_pool import get_acp_pool, set_acp_pool

            pool = get_acp_pool()
            if pool is not None:
                await pool.shutdown()
                set_acp_pool(None)
        except Exception:
            logger.debug("ACP pool shutdown failed", exc_info=True)

    app.on_cleanup.append(_acp_pool_shutdown)

    async def _warm_provider_availability(app_: web.Application) -> None:
        """Measure every provider's availability once at boot, in the background.

        The measurement runs in the availability child process (providers/availability.py),
        so this costs the loop nothing; it exists so Settings → Providers opens on answers
        rather than on a page of "checking" cards."""
        try:
            from personalclaw.providers.availability import get_availability_board
            from personalclaw.providers.registry import get_provider_registry

            names = sorted({ext.name for ext in get_provider_registry().list_extensions()})
            get_availability_board().warm(names)
        except Exception:
            logger.debug("provider availability warm failed", exc_info=True)

    app.on_startup.append(_warm_provider_availability)

    async def _provider_availability_shutdown(app_: web.Application) -> None:
        """Kill a still-running availability child on gateway stop."""
        try:
            from personalclaw.providers.availability import get_availability_board

            await get_availability_board().shutdown()
        except Exception:
            logger.debug("provider availability shutdown failed", exc_info=True)

    app.on_cleanup.append(_provider_availability_shutdown)

    def _relay_voice_settings(use_case: str) -> None:
        """Text-to-speech's settings were saved, whoever saved them: every open chat re-reads its
        voice settings on the ``refresh`` frame naming ``voice``, so "Speak replies aloud" reaches a
        chat that is already open, in both directions."""
        if use_case == "tts":
            app["state"].push_refresh("voice")

    async def _voice_settings_relay_startup(app_: web.Application) -> None:
        from personalclaw.providers import use_cases

        use_cases.subscribe_settings_saved(_relay_voice_settings)

    app.on_startup.append(_voice_settings_relay_startup)

    async def _voice_settings_relay_shutdown(app_: web.Application) -> None:
        """Stop relaying when this gateway stops, so a later one in the same process is told."""
        from personalclaw.providers import use_cases

        use_cases.unsubscribe_settings_saved(_relay_voice_settings)

    app.on_cleanup.append(_voice_settings_relay_shutdown)

    def _relay_mcp_status(_server: str) -> None:
        """What an MCP server's card says may have changed (`mcp_status.announce`): every open page
        that shows servers re-reads them on the ``refresh`` frame naming ``mcp``, instead of
        polling while one starts."""
        app["state"].push_refresh("mcp")

    async def _mcp_status_relay_startup(app_: web.Application) -> None:
        from personalclaw import mcp_status

        mcp_status.subscribe(_relay_mcp_status)

    app.on_startup.append(_mcp_status_relay_startup)

    async def _mcp_status_relay_shutdown(app_: web.Application) -> None:
        """Stop relaying when this gateway stops, so a later one in the same process is told."""
        from personalclaw import mcp_status

        mcp_status.unsubscribe(_relay_mcp_status)

    app.on_cleanup.append(_mcp_status_relay_shutdown)

    def _notify_mcp_descriptions(server: str, tools: tuple[str, ...]) -> None:
        """A server whose read-only labels the owner does not trust changed what some of its tools
        say (`mcp_read_only_trust.observe`): she is told quietly, once per change, since a
        description is text the model reads. Its kind delivers as a badge unless her rule says
        otherwise, and opens the Tools page on that server."""
        from urllib.parse import quote

        from personalclaw import notification_kinds
        from personalclaw.mcp_read_only_trust import description_notice

        title, body = description_notice(server, tools)
        app["state"].notify(
            notification_kinds.MCP_DESCRIPTION_CHANGED,
            title,
            body,
            meta={
                "statusUrl": f"#/tools?q={quote(server)}",
                "server": server,
                "tools": list(tools),
            },
        )

    async def _mcp_descriptions_relay_startup(app_: web.Application) -> None:
        from personalclaw import mcp_read_only_trust

        mcp_read_only_trust.subscribe(_notify_mcp_descriptions)

    app.on_startup.append(_mcp_descriptions_relay_startup)

    async def _mcp_descriptions_relay_shutdown(app_: web.Application) -> None:
        """Stop telling when this gateway stops, so a later one in this process tells instead."""
        from personalclaw import mcp_read_only_trust

        mcp_read_only_trust.unsubscribe(_notify_mcp_descriptions)

    app.on_cleanup.append(_mcp_descriptions_relay_shutdown)

    async def _mcp_client_shutdown(app_: web.Application) -> None:
        """Stop the idle sweeper + drain all live MCP connections on gateway stop
        (rel-mcp-server-pooling #46)."""
        try:
            from personalclaw.mcp_client import get_mcp_client_registry

            await get_mcp_client_registry().shutdown_all()
        except Exception:
            logger.debug("MCP client shutdown failed", exc_info=True)

    app.on_cleanup.append(_mcp_client_shutdown)

    async def _app_processes_shutdown(app_: web.Application) -> None:
        """Terminate every app backend and worker on gateway stop. Without this the
        backends (snippet-lab/standup-notes/… server.py) were spawned on enable but
        never reaped on shutdown — so each gateway restart ORPHANED another set
        (reparented to init), leaking dozens of processes over a dev session. The
        workers were never stopped here at all: each kept running, re-parented to init,
        until the next boot reaped it.

        The watchdogs boot started go FIRST (``app_runtime.stop_processes`` does both, in
        that order): left running, the backend one revived every backend terminated here
        30s later, and all three outlived the gateway that started them — each boot in one
        process adding three sweepers that never ended."""
        try:
            from personalclaw.apps.app_runtime import stop_processes

            stop_processes()
        except Exception:
            logger.debug("app process shutdown failed", exc_info=True)

    app.on_cleanup.append(_app_processes_shutdown)

    async def _discovery_shutdown(app_: web.Application) -> None:
        """Send the mDNS goodbye and release the socket on gateway stop (COMPANION-APPS C3).

        Without it, a restart leaves other devices caching this gateway's address for two
        minutes pointing at a port nothing is listening on. Registered HERE rather than beside
        the advertiser's start, because ``runner.setup()`` freezes ``on_cleanup`` before the
        bind host — and therefore the start decision — is known. A no-op when nothing is
        advertising, which is the default."""
        try:
            from personalclaw.companion import discovery

            discovery.shutdown()
        except Exception:
            logger.debug("LAN discovery shutdown failed", exc_info=True)

    app.on_cleanup.append(_discovery_shutdown)

    async def _auth_tally_shutdown(app_: web.Application) -> None:
        """Write the summary row of every still-open authentication window on gateway stop, so
        the successes of the last quarter hour are counted rather than lost with the process."""
        try:
            from personalclaw.dashboard.token_auth import flush_success_tally

            flush_success_tally()
        except Exception:
            logger.debug("auth success tally flush failed", exc_info=True)

    app.on_cleanup.append(_auth_tally_shutdown)

    async def _durability_shutdown(app_: web.Application) -> None:
        """Stop the durability loop, and time-travel's debouncer with it, on gateway stop.

        The service starts after ``runner.setup()`` froze ``on_cleanup``, so its stop is
        registered here and reads the service off the state. Nothing stopped it before. The
        debouncer is process-wide and runs its own thread, so it outlived the gateway that
        installed it: the commits it still held were never flushed, and the next gateway in the
        same process was handed the old debouncer, bound to the old home."""
        svc = getattr(app_["state"], "_durability_svc", None)
        if svc is None:
            return
        try:
            svc.stop()
        except Exception:
            logger.debug("durability shutdown failed", exc_info=True)

    app.on_cleanup.append(_durability_shutdown)

    async def _artifact_refresh_shutdown(app_: web.Application) -> None:
        """Stop relaying artifact writes to this gateway's pages when it stops. Subscribed after
        ``runner.setup()`` froze ``on_cleanup``, beside the artifact mirror, so its end is here."""
        from personalclaw.artifacts import changes as artifact_changes

        artifact_changes.unsubscribe(app_["state"].announce_artifact_change)

    app.on_cleanup.append(_artifact_refresh_shutdown)
