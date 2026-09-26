"""What installing an app GRANTS it and RUNS for it — the one authority consent reads.

Three readers, one answer. The Store catalog, so a card and its detail panel can say what
an app gets before anyone clicks Install. The install PREVIEW (``app_manager.preview``),
so the consent dialog shows the server's own reading of the exact bytes about to be
installed — the only reading there is for a registry pointer or a pasted URL, whose
manifest the catalog never fetched. And the install GATE, which binds consent to those
bytes (:func:`bundle_digest`) and, for an update, compares what the new version gets with
what the installed one got (:func:`changed`).

A second projection, hand-built at one of those sites, is how a consent surface ends up
disclosing less than the app receives: the catalog once carried permissions and crons and
dropped the Python packages, and until this module existed a clean-scanning app installed
with no disclosure at all. So every key here is read by the same components wherever it
is shown, and the wire names are the catalog entry's own.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from personalclaw.apps.app_crons import schedules
from personalclaw.apps.manifest import AppManifest

logger = logging.getLogger(__name__)


def describe(m: AppManifest) -> dict[str, Any]:
    """Everything installing ``m`` grants and runs, keyed by the catalog-entry field names.

    * ``permissions`` — the grants the gateway enforces (``Permissions.to_dict``).
    * ``crons`` — each declared job, its cadence in words, what it runs, and ``scheduled``:
      whether installing actually turns it on (:func:`app_crons.schedules` — a job without
      the ``cron`` permission is declared but inert, and saying "it runs" would be false).
    * ``pythonDependencies`` — what ``pip install`` puts in ``<home>/app-python``, which the
      gateway loads into its own process (``apps/app_python.py``).
    * ``hasUI`` / ``uiComponents`` — browser code loaded into the dashboard page.
    * ``hasBackend`` — a server process of its own, started on install and kept running
      while the app is enabled.
    * ``onInstall`` / ``onUpdate`` — the shell command the install (or update) runs in the
      app's folder, verbatim.
    * ``mcpServers`` — each MCP server it adds to the assistant's tools, and what that
      server starts or connects to.

    Best-effort per section: a shape surprise empties that section and logs, because this
    runs on every catalog scan and one odd manifest must not break the Store. The install
    gate reads the same function, so an emptied section can never hide anything the
    platform then acts on without disclosing the same emptiness.
    """
    try:
        perms: dict[str, Any] = m.permissions.to_dict() if m.permissions else {}
    except Exception:  # noqa: BLE001 — best-effort, see the docstring
        logger.debug("disclosure: permissions unreadable for %s", m.name, exc_info=True)
        perms = {}
    return {
        "permissions": perms,
        "crons": _crons(m),
        "pythonDependencies": _python_dependencies(m),
        "hasUI": bool(m.ui.pages),
        "uiComponents": m.ui.components,
        "hasBackend": bool(m.backend.entryPoint),
        "onInstall": m.setup.onInstall,
        "onUpdate": m.setup.onUpdate,
        "mcpServers": _mcp_servers(m),
    }


def changed(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    """Whether an update changes what the app gets. ``previous`` is ``None`` when the
    installed manifest could not be read, which counts as a change: "nothing changes" is a
    claim, and it must not be made about an app nobody could read."""
    if previous is None:
        return True
    return _canonical(previous) != _canonical(current)


def bundle_digest(root: Path) -> str:
    """SHA-256 over every file of a STAGED bundle — path and bytes — so consent given to one
    reviewed copy is consent to exactly those bytes and no others.

    Nothing is skipped: the digest covers what the commit step moves into place, hidden
    files included. The stager copies THROUGH symlinks, so a staged tree holds none; one
    found anyway is hashed as the link it is, never followed out of the bundle."""
    h = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            h.update(b"L\0" + rel + b"\0" + os.readlink(path).encode("utf-8") + b"\n")
        elif path.is_file():
            h.update(b"F\0" + rel + b"\0" + hashlib.sha256(path.read_bytes()).digest() + b"\n")
    return h.hexdigest()


def _canonical(d: dict[str, Any]) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)


def _crons(m: AppManifest) -> list[dict[str, Any]]:
    """The declared jobs, as the consent surface reads them. A manifest cron runs an AGENT
    with a MESSAGE (``app_crons`` turns it into an ``invoke-agent`` trigger) — there is no
    command field — so "what it runs" is the agent and its prompt."""
    out: list[dict[str, Any]] = []
    try:
        # Resolved once, and only when a clock-time cron needs it — most apps declare none.
        tz_name = _consent_timezone() if any(c.cron_expr for c in m.crons) else ""
        for c in m.crons:
            out.append(
                {
                    "name": c.name,
                    "every": c.every,
                    "cron_expr": c.cron_expr,
                    # The expression in words; `cron_expr` rides along so the surface can
                    # keep the exact expression as a tooltip. Empty for the `every` form.
                    "cadence": _humanized_cadence(c.cron_expr, tz_name),
                    "agent": c.agent,
                    "message": c.message,
                    "scheduled": schedules(m, c),
                }
            )
    except Exception:  # noqa: BLE001 — best-effort, see `describe`
        logger.debug("disclosure: crons unreadable for %s", m.name, exc_info=True)
        return []
    return out


def _python_dependencies(m: AppManifest) -> list[dict[str, Any]]:
    try:
        # Imported here, not at module scope: the classifier reads the installed
        # ``personalclaw`` distribution's metadata and lives with the installer, and a
        # catalog scan must not pull the install path in just to render a card.
        from personalclaw.apps.app_manager import describe_python_dependencies

        return describe_python_dependencies(m)
    except Exception:  # noqa: BLE001 — best-effort, see `describe`
        logger.debug("disclosure: python deps unreadable for %s", m.name, exc_info=True)
        return []


def _mcp_servers(m: AppManifest) -> list[dict[str, str]]:
    """Each server ``mcp_bridge`` would write into the live MCP config, and what it launches:
    the command line of a stdio server, or the URL of a remote one."""
    servers = m.mcpServers if isinstance(m.mcpServers, dict) else {}
    out: list[dict[str, str]] = []
    for name, spec in sorted(servers.items()):
        if not isinstance(spec, dict):
            continue
        url = str(spec.get("url") or "")
        raw_args = spec.get("args")
        args: list[Any] = raw_args if isinstance(raw_args, list) else []
        command = " ".join(str(p) for p in [spec.get("command") or "", *args] if str(p))
        out.append({"name": str(name), "launches": url or command})
    return out


def _consent_timezone() -> str:
    """The configured timezone a manifest cron's clock times should be read in — the same
    one the Schedule page renders. Empty on any config trouble, which only costs the
    cadence text its timezone suffix."""
    try:
        from personalclaw.config.loader import AppConfig

        return str(AppConfig.load().timezone or "")
    except Exception:  # noqa: BLE001 — the suffix is optional
        return ""


def _humanized_cadence(expr: str, tz_name: str) -> str:
    """The human reading of a 5-field cron expression.

    🔴 DELEGATES to the shipped ``schedule.format_schedule`` for the same reason
    ``triggers/schedule_view.describe_cadence`` does: a second formatter drifts from the
    one the rest of the UI reads, and a consent screen that spells a schedule differently
    from the Schedule page hands the user a third fact to reconcile.

    Empty when the expression cannot be described (``format_schedule`` hands the raw
    expression back), so the caller falls back to showing the expression itself rather
    than inventing a cadence it does not know."""
    if not expr.strip():
        return ""
    try:
        from personalclaw.schedule import ScheduleDefinition, format_schedule

        text = format_schedule(ScheduleDefinition(kind="cron", cron_expr=expr), tz_name=tz_name)
    except Exception:  # noqa: BLE001 — fall back to the raw expression
        logger.debug("could not describe cron cadence %r", expr, exc_info=True)
        return ""
    return "" if text.strip() == expr.strip() else text.strip()
