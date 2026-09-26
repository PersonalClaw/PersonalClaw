"""Staged pack triggers — the enable path into Automations (AGENT-PACKS §3.1/§4, AP-7).

The direct sibling of :mod:`packs.roster`'s ``always``-tier deploy. Pack import stages every
trigger DISABLED at ``packs/staged/<pack>/triggers/<id>.json`` (see
:func:`packs.import_._commit_file_component`, the ``trigger`` branch) because a pack must never
arm automation on install — §3.1's propose-don't-write applied to distribution. But a staged
trigger nobody can reach is inert: the install toast promises the user can "enable them" later,
and until this module NOTHING read that subtree, so there was no path that turned a staged
trigger into a manageable one.

:func:`deploy_triggers` is that path, mirroring :func:`packs.roster.deploy_roster` exactly, with
one load-bearing safety rule that never bends: **a deployed trigger lands in the live store
DISABLED.** The deploy makes the pack's staged triggers appear in Automations (``#/triggers``)
as ordinary rows the user can review and arm one at a time; it does not arm any of them. Forcing
``enabled=False`` HERE — rather than trusting the staged bytes — is what guarantees a pack cannot
auto-arm automation even by way of its own enable path. A pack trigger arriving enabled would be
an automation the user never switched on.

A staged file whose bytes no longer parse to a runnable trigger (``parse_trigger`` returns an
error-severity issue, or the JSON is unreadable) is SKIPPED and reported, never raised: one
corrupt staged file must not fail the whole deploy, exactly as ``deploy_roster`` reports a missing
persona rather than aborting.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.triggers.models import Trigger

logger = logging.getLogger(__name__)

#: The staged-triggers subtree under a pack's staging area — the mirror of
#: :data:`packs.roster.ROSTER_FILE`, but a directory of one file per trigger rather than a single
#: document (the importer writes ``packs/staged/<pack>/triggers/<id>.json``).
TRIGGERS_SUBDIR = "triggers"


def staged_triggers_dir(home: Path, stage: str) -> Path:
    """Where a pack's staged triggers live (``packs/staged/<pack>/triggers/``)."""
    return home / "packs" / "staged" / stage / TRIGGERS_SUBDIR


def staged_trigger_ids(stage: str, home: Path | None = None) -> list[str]:
    """The ids of a pack's staged triggers — the filenames, sorted. The deploy gate reads this.

    A missing staging dir reads as no staged triggers (fail soft, exactly like
    :func:`packs.roster.load_roster`): a deploy surface that 500s because a pack shipped no
    triggers is worse than one that shows nothing to add.
    """
    if home is None:
        from personalclaw.config.loader import config_dir

        home = config_dir()
    return sorted(p.stem for p in staged_triggers_dir(home, stage).glob("*.json"))


def deploy_triggers(stage: str, home: Path | None = None) -> dict[str, list[str]]:
    """Make a pack's staged triggers visible in Automations — DISABLED (§3.1, AP-7).

    The trigger sibling of :func:`packs.roster.deploy_roster`. Reads every
    ``packs/staged/<stage>/triggers/*.json`` the import staged, and for each one that still
    parses to a runnable trigger, upserts it into the live ``triggers.json`` store with
    ``enabled=False``. Returns ``{"deployed": [ids], "skipped": [ids]}``:

    * **deployed** — ids now present in the live store, every one ``enabled=False``. They show up
      in Automations (``#/triggers``); the user arms each there. Nothing here arms one.
    * **skipped** — a staged file whose JSON is unreadable or whose bytes ``parse_trigger``
      rejects with an error-severity issue. Reported, not raised: one broken staged file must not
      fail the deploy (mirrors ``deploy_roster``'s ``missing`` list — a reported non-action, not a
      500).

    🔴 THE SAFETY INVARIANT: a deployed trigger ALWAYS lands ``enabled=False``, regardless of the
    staged bytes. The importer stages a pack's triggers disabled, but this does not TRUST that —
    it forces the flag — so a pack can never arm automation through its enable path. Landing a
    pack trigger enabled would be switching on work the user never chose.

    Idempotent: re-deploying upserts the same ids (the store is keyed by id), never a duplicate.
    """
    from personalclaw.triggers.models import parse_trigger
    from personalclaw.triggers.store import TriggerStore

    if home is None:
        from personalclaw.config.loader import config_dir

        home = config_dir()

    store = TriggerStore(home)
    deployed: list[str] = []
    skipped: list[str] = []
    for path in sorted(staged_triggers_dir(home, stage).glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            # Unreadable bytes are reported, never raised — a corrupt staged file cannot arm and
            # must not take the rest of the deploy down with it.
            skipped.append(path.stem)
            continue
        trigger, issues = parse_trigger(raw)
        if any(i.severity == "error" for i in issues):
            # A staged file the entity refuses to run is reported, never armed — the same
            # visible-and-inert answer `parse_trigger` gives a broken row (it forces enabled=False
            # on a fatal parse), surfaced here as a skip rather than a silent write.
            skipped.append(trigger.id or path.stem)
            continue
        # 🔴 Force disabled — never trust the staged `enabled`. This is the one line that keeps a
        # pack from arming automation through its own enable path.
        trigger.enabled = False
        store.upsert(trigger)
        deployed.append(trigger.id)

    _audit_deploy(stage, deployed, skipped)
    return {"deployed": deployed, "skipped": skipped}


def deployed_triggers(stage: str, home: Path | None = None) -> list["Trigger"]:
    """The live Automations rows a pack's trigger deploy added, still in the store.

    Read the way :func:`deploy_triggers` writes them: each staged file's own trigger id, looked up
    in the live store. A staged file that does not parse named nothing (the deploy skipped it), and
    an id that is no longer in the store is a row the user already removed.
    """
    from personalclaw.triggers.models import parse_trigger
    from personalclaw.triggers.store import TriggerStore

    if home is None:
        from personalclaw.config.loader import config_dir

        home = config_dir()

    store = TriggerStore(home)
    live: list[Trigger] = []
    for path in sorted(staged_triggers_dir(home, stage).glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        trigger, _issues = parse_trigger(raw)
        loaded = store.get(trigger.id) if trigger.id else None
        if loaded is not None:
            live.append(loaded.trigger)
    return live


def _audit_deploy(stage: str, deployed: list[str], skipped: list[str]) -> None:
    """SEL-audit the deploy: it makes automations visible + manageable, which is a state change
    worth a trail (mirrors :func:`packs.roster._audit_deploy`)."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="packs.triggers",
            operation="pack_triggers_deploy",
            outcome="completed" if deployed else "noop",
            source="dashboard",
            resources=f"{stage}: deployed={','.join(deployed)} skipped={len(skipped)}",
            error=f"skipped: {','.join(skipped)}" if skipped else "",
        )
    except Exception:  # pragma: no cover - audit is best-effort
        logger.debug("pack triggers deploy audit failed", exc_info=True)
