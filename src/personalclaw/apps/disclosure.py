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
from pathlib import Path, PurePosixPath
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
      while the app is enabled. ``backendSandbox`` names the tier it launches inside
      (``backend.sandbox``), or is ``""`` when it runs on the host.
    * ``providers`` — each provider module, the ``module:factory`` entry point the gateway
      loads and whether it runs ``in-process`` (imported into the gateway itself) or as a
      ``sidecar`` child process.
    * ``onInstall`` / ``onUpdate`` / ``onEnable`` / ``onDisable`` / ``onUninstall`` — the
      shell command each lifecycle hook runs in the app's folder, verbatim.
    * ``cliSetup`` / ``cliDoctor`` — the ``module:function`` it runs when you run
      ``personalclaw setup`` or ``personalclaw doctor``.
    * ``sources`` — each connector-pack parser script, which the gateway runs on what the
      pack's sources fetch.
    * ``mcpServers`` — each MCP server it adds to the assistant's tools, and what that
      server starts or connects to.
    * ``skills`` — the skills it installs for your agents to follow, by the name each one
      installs under.
    * ``runsAsYou`` — the sentence that says which of the above run as you, composed from this
      projection itself so it can never name a kind the lists do not, or miss one they do.
      ``""`` when the app brings no such code.

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
    d: dict[str, Any] = {
        "permissions": perms,
        "crons": _crons(m),
        "pythonDependencies": _python_dependencies(m),
        "hasUI": bool(m.ui.pages),
        "uiComponents": m.ui.components,
        "hasBackend": bool(m.backend.entryPoint),
        "backendSandbox": _backend_sandbox(m),
        "providers": _providers(m),
        "onInstall": m.setup.onInstall,
        "onUpdate": m.setup.onUpdate,
        "onEnable": m.setup.onEnable,
        "onDisable": m.setup.onDisable,
        "onUninstall": m.setup.onUninstall,
        "cliSetup": m.cli.setup,
        "cliDoctor": m.cli.doctor,
        "sources": _sources(m),
        "mcpServers": _mcp_servers(m),
        "skills": _skills(m),
    }
    d["runsAsYou"] = _runs_as_you(d)
    return d


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
    files included. The stager keeps a link to one of the bundle's own files as that link
    (:mod:`apps.staging`), so a link is hashed as what it is — its path and its text, never
    followed — and a link and a copy of the file it names are different bundles."""
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


def _backend_sandbox(m: AppManifest) -> str:
    """The sandbox tier the app's server launches inside, or ``""`` when it runs on the host —
    no server, no tier named, or the ``none`` builtin, which is the host by definition
    (``apps/backend_runtime.py`` refuses a named tier it cannot provide rather than falling
    back, so a named one is where the server really runs)."""
    if not m.backend.entryPoint:
        return ""
    from personalclaw.sandbox_providers.none import NONE_PROVIDER_NAME

    tier = (m.backend.sandbox or "").strip()
    return "" if tier in ("", NONE_PROVIDER_NAME) else tier


def _providers(m: AppManifest) -> list[dict[str, str]]:
    """Each provider module: what it provides, the entry point loaded, and where it runs."""
    try:
        return [
            {"type": p.type, "implementation": p.implementation, "execution": p.execution}
            for p in m.all_providers()
        ]
    except Exception:  # noqa: BLE001 — best-effort, see `describe`
        logger.debug("disclosure: providers unreadable for %s", m.name, exc_info=True)
        return []


def _sources(m: AppManifest) -> list[dict[str, str]]:
    """Each connector-pack parser: the source it parses for, and the script that does it."""
    return [{"name": s.name, "script": s.script} for s in m.sources if s.script]


def _skills(m: AppManifest) -> list[str]:
    """The skills the app installs, by the name each one lands under — the declared folder's
    own name, which is what ``apps/skill_seed.py`` installs it as."""
    out: list[str] = []
    for sk in m.skills:
        rel = str(sk.path or "").strip().strip("/")
        if rel:
            out.append(PurePosixPath(rel).name)
    return out


#: The lifecycle hooks, in the order they happen, and the word for when each runs.
_HOOKS = (
    ("install", "onInstall"),
    ("update", "onUpdate"),
    ("enable", "onEnable"),
    ("disable", "onDisable"),
    ("uninstall", "onUninstall"),
)


def _joined(items: list[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _counted(n: int, one: str, many: str) -> tuple[str, bool]:
    return (one, False) if n == 1 else (f"its {n} {many}", True)


def _runs_as_you(d: dict[str, Any]) -> str:
    """The sentence install consent leads with: WHICH of the app's code runs as you, and that the
    permissions above do not bound it (``docs/security/limitations.md`` §7).

    Composed from the projection, never from the manifest a second time, so it names exactly the
    kinds the lists below it name. A server that launches inside a sandbox tier is left out: the
    tier confines it instead (``apps/backend_runtime.py::build_backend_sandbox_spec``). A Python
    package core already pins is left out too — the install guard refuses to move a core
    dependency, so no new code arrives with it. ``""`` when nothing is left."""
    parts: list[tuple[str, bool]] = []
    if d["hasBackend"] and not d["backendSandbox"]:
        parts.append(("its server", False))
    if d["providers"]:
        parts.append(_counted(len(d["providers"]), "its provider module", "provider modules"))
    if d["mcpServers"]:
        parts.append(_counted(len(d["mcpServers"]), "its MCP server", "MCP servers"))
    hooks = [when for when, key in _HOOKS if d[key]]
    if hooks:
        plural = len(hooks) > 1
        parts.append((f"the command{'s' if plural else ''} it runs at {_joined(hooks)}", plural))
    # Plain words, no markup: the dialog shows this sentence as text.
    cli = [
        f"personalclaw {c}" for c, key in (("setup", "cliSetup"), ("doctor", "cliDoctor")) if d[key]
    ]
    if cli:
        plural = len(cli) > 1
        parts.append((f"the step{'s' if plural else ''} it adds to {_joined(cli)}", plural))
    if d["sources"]:
        parts.append(_counted(len(d["sources"]), "its source parser", "source parsers"))
    new_packages = [p for p in d["pythonDependencies"] if not p.get("coreOwned")]
    if new_packages:
        n = len(new_packages)
        parts.append(
            ("the Python package it installs", False)
            if n == 1
            else (f"the {n} Python packages it installs", True)
        )
    if not parts:
        return ""
    plural = len(parts) > 1 or parts[0][1]
    subject = _joined([text for text, _ in parts])
    return (
        f"{subject[:1].upper()}{subject[1:]} {'run' if plural else 'runs'} as you on this machine. "
        f"{'They' if plural else 'It'} can read and change your files, your PersonalClaw settings "
        "included, and the permissions listed here limit what the app asks the gateway for, not "
        "what this code does."
    )


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
