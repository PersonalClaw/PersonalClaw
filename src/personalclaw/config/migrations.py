"""Config migrations — the one place a pending config upgrade is APPLIED, and the
one place it is PERSISTED.

Two functions, deliberately separated (PHF-15):

* :func:`apply_config_migrations` mutates a parsed :class:`AppConfig` **in memory** and
  reports whether anything changed. ``AppConfig.load()`` calls it, so every reader gets a
  config of the current shape.
* :func:`load_and_persist_migrations` is the only caller that writes. It is invoked from
  the gateway's own boot path (``cli_server._boot_config``).

Why the split exists at all. ``AppConfig.load()`` used to back the file up with
``shutil.copy2`` and call ``cfg.save()`` inline, which made a *read* of the config a
*write* of the user's ``config.json``. That is not a theoretical hazard: it reached the
real ``~/.personalclaw/config.json`` on CI, from a module-level constant resolved during
pytest **collection** — before any fixture exists to redirect the home. It was invisible
on developer machines because the write only fires when ``config.json`` exists *and* is
pre-migration, and a developer's own config is already migrated. And it under-reported,
because ``copy2`` preserves the source mtime, so the ``.bak`` looked older than the run.

The migration itself is unchanged and still load-bearing for real upgrades; only the
decision of *who may write* moved here.
"""

from __future__ import annotations

import logging
import shutil
from typing import TYPE_CHECKING

from personalclaw.config.loader import config_path

if TYPE_CHECKING:
    from personalclaw.config.loader import AppConfig

logger = logging.getLogger(__name__)


def apply_config_migrations(cfg: "AppConfig") -> bool:
    """Bring ``cfg`` up to the current shape IN MEMORY. Returns True if anything changed.

    Pure with respect to the filesystem: it touches ``cfg`` and nothing else. Idempotent —
    a second call on the same object returns False. Every branch below is add-if-missing or
    repair-if-invalid, never overwrite-a-user-edit.
    """
    # Imported lazily: `loader` reaches into this module from `load()`, so a module-level
    # import of the dataclass would close the cycle.
    from personalclaw.config.loader import AgentProfile

    needs_migration = False

    # The in-process native loop is the default runtime; ACP must be
    # opted into explicitly with an ``acp:<cli>`` provider. When the
    # global default is ``acp``, flip it to native and clear the
    # ``personalclaw`` modeId on empty-provider agents (which would
    # otherwise route them to an external CLI). Only applied to an
    # ``acp``-default config — an already-native config is left
    # untouched, since "personalclaw" may be a real ACP modeId there.
    if getattr(cfg.agent, "provider", "") == "acp":
        cfg.agent.provider = "native"
        needs_migration = True
        for _prof in (cfg.agents or {}).values():
            if (
                not getattr(_prof, "provider", "")
                and getattr(_prof, "provider_agent", "") == "personalclaw"
            ):
                _prof.provider = "native"
                _prof.provider_agent = ""

    # Create default agent when none exists. The default is the
    # in-process NATIVE PersonalClaw agent (governed by Settings →
    # Models) — no external CLI required for first-run chat. ACP agents
    # are created only when the user explicitly adds an acp:<cli> one.
    if not cfg.agents:
        from personalclaw.agents.defaults import (
            DEFAULT_NATIVE_AGENT_NAME,
            make_default_native_profile,
        )

        cfg.agents[DEFAULT_NATIVE_AGENT_NAME] = make_default_native_profile(AgentProfile)
        needs_migration = True

    # Seed the built-in goal-loop worker if absent. Idempotent
    # (add-if-missing, never overwrite a user edit) so it ships with the
    # package whenever the gateway runs — inert until a loop invokes it.
    # Kept out of the `if not cfg.agents` block so existing configs gain
    # it on next load.
    from personalclaw.agents.defaults import (
        CODE_PLANNER_AGENT_NAME,
        CODER_AGENT_NAME,
        LITE_AGENT_NAME,
        LOOP_PLANNER_AGENT_NAME,
        LOOP_WORKER_AGENT_NAME,
        TEMPLATE_REFINER_AGENT_NAME,
        make_code_planner_profile,
        make_coder_profile,
        make_lite_agent_profile,
        make_loop_planner_profile,
        make_loop_worker_profile,
        make_template_refiner_profile,
    )

    if LOOP_WORKER_AGENT_NAME not in cfg.agents:
        cfg.agents[LOOP_WORKER_AGENT_NAME] = make_loop_worker_profile(AgentProfile)
        needs_migration = True

    # Seed the built-in Code worker (the SDLC engine) if absent. Same
    # idempotent add-if-missing contract — ships with the package, inert
    # until a code project invokes it.
    if CODER_AGENT_NAME not in cfg.agents:
        cfg.agents[CODER_AGENT_NAME] = make_coder_profile(AgentProfile)
        needs_migration = True

    # Seed the built-in Code DEEP PLANNER (agentic intake planner, C163) if
    # absent. Tool-equipped so it investigates real context before planning;
    # inert until a code project requests a deep plan.
    if CODE_PLANNER_AGENT_NAME not in cfg.agents:
        cfg.agents[CODE_PLANNER_AGENT_NAME] = make_code_planner_profile(AgentProfile)
        needs_migration = True

    # Seed the built-in goal-planner (intake brain) if absent. Same
    # idempotent add-if-missing contract — ships with the package, inert
    # until intake invokes it.
    if LOOP_PLANNER_AGENT_NAME not in cfg.agents:
        cfg.agents[LOOP_PLANNER_AGENT_NAME] = make_loop_planner_profile(AgentProfile)
        needs_migration = True

    # Seed the built-in lite background worker if absent. Same idempotent
    # add-if-missing contract as the loop worker — the background chores
    # (titles/suggestions/consolidation) resolve a real profile instead
    # of falling through to an unnamed default.
    if LITE_AGENT_NAME not in cfg.agents:
        cfg.agents[LITE_AGENT_NAME] = make_lite_agent_profile(AgentProfile)
        needs_migration = True

    # Seed the built-in propose-only template refiner (WF2LEA-6) if absent. Same
    # idempotent add-if-missing contract — ships with the package, inert until the
    # `refine-template` workflow runs it over a template's run ledger.
    if TEMPLATE_REFINER_AGENT_NAME not in cfg.agents:
        cfg.agents[TEMPLATE_REFINER_AGENT_NAME] = make_template_refiner_profile(AgentProfile)
        needs_migration = True

    # Prune retired system agents left behind in an existing config.json.
    # These pre-rename system agents have no profile in source anymore, so an
    # orphaned key just resolves to nothing. Scoped to the reserved
    # `personalclaw-` namespace (RETIRED_AGENT_NAMES) so a user-created agent is
    # never touched. One-time: the key is gone after the first write-back.
    from personalclaw.agents.defaults import RETIRED_AGENT_NAMES

    for _retired in RETIRED_AGENT_NAMES & set(cfg.agents):
        del cfg.agents[_retired]
        logger.info("Config migration: pruned retired system agent %r", _retired)
        needs_migration = True

    if not cfg.default_agent or cfg.default_agent not in cfg.agents:
        # Prefer "default" if it exists, otherwise use first available agent
        if "default" in cfg.agents:
            cfg.default_agent = "default"
        elif cfg.agents:
            cfg.default_agent = next(iter(cfg.agents))
        else:
            cfg.default_agent = "default"
        needs_migration = True

    return needs_migration


def load_and_persist_migrations() -> "AppConfig":
    """Load the config and PERSIST any pending migration. The only writing entry point.

    Returns the loaded config either way, so the boot path parses the file once. When a
    migration did apply, the original is copied aside to ``config.json.bak`` first.

    Best-effort by design: a failed write-back degrades to "migrated in memory this run"
    and never blocks startup. Callers other than the gateway boot path should use
    ``AppConfig.load()``, which is a pure read.
    """
    from personalclaw.config.loader import AppConfig

    cfg, migrated = AppConfig.load_with_migration_state()
    if not migrated:
        return cfg
    path = config_path()
    try:
        if path.exists():
            backup = path.with_suffix(".json.bak")
            shutil.copy2(path, backup)
            logger.info("Config migrated — backup saved to %s", backup)
        cfg.save()
    except Exception as e:  # noqa: BLE001 — write-back is best-effort; never block startup.
        logger.warning("Config write-back failed: %s", e)
    return cfg
