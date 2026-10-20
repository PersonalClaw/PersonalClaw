"""What core does for an app outside PersonalClaw, held to what the app's manifest declared.

Two things an agent app gets done by core rather than by its own code: core npm-installs the ACP
adapter it asks for (``acp.cli_resolve.provision_acp_adapter``), and core starts the agent CLI it
registers, for every chat on that runtime (``acp_bundles._register.register_acp_cli_entry``).
Install consent names both from the manifest — ``dependencies.npmPackages`` and ``launches``
(``apps.disclosure``) — so each of those two seams refuses what the calling app did not declare,
and the review stays the whole of what core does for it. An app's own code can still start
anything it likes, since it runs as the owner; the review says that in its own sentence
(``disclosure._runs_as_you``).

The caller is read off the stack (``app_code.owner``, the same authority that takes an app's
registrations back when it is unloaded), never from a name the app passes, and its manifest is
the one its providers were loaded from. A call core makes itself is not an app's, so it is not
held to a manifest. A call from an app's code whose manifest is not loaded reads as one from an
app that declares nothing: refused.
"""

from __future__ import annotations

from personalclaw import app_code
from personalclaw.apps.manifest import PROGRAM_YOU_NAME, AppManifest

#: How every refusal ends: why it matters, in the words the provider's card shows.
UNNAMED = "so its install review never named it"


class NotDeclared(Exception):
    """Core was asked to do, for an app, something its manifest does not declare. The message is
    the sentence the app's provider card shows (Settings → Providers)."""


def _calling_manifest() -> tuple[bool, AppManifest | None]:
    """``(is_an_app, manifest)``: whether an app's code made the current call, and the manifest
    its providers were loaded from (``None`` when none is loaded for it)."""
    app = app_code.owner()
    if app is None:
        return False, None
    from personalclaw.providers.registry import get_provider_registry

    record = get_provider_registry().get(app)
    return True, (record.manifest if record is not None else None)


def declares_npm_package(package: str) -> bool:
    """Whether core may install (or fetch with ``npx``) *package* for the caller: the calling
    app lists it under ``dependencies.npmPackages``, or core itself is the caller."""
    is_app, manifest = _calling_manifest()
    if not is_app:
        return True
    return manifest is not None and package in manifest.dependencies.npmPackages


#: The package managers whose registry a declared npm package is fetched from.
_NPM_PROGRAMS = frozenset({"npm", "npx", "pnpm", "pnpx", "yarn", "bun", "bunx"})


def declared_hosts(app: str, program: str) -> tuple[str, ...]:
    """The hosts *app*'s manifest says *program* reaches: its ``launches`` entry's ``hosts``, and,
    for an npm program, the npm registry when the app declares ``dependencies.npmPackages`` or
    its entry names the package it runs (``npmPackage``): install consent names those packages,
    and says they are fetched from there. None for an app whose manifest is not loaded."""
    from personalclaw.command_effects import NPM_HOSTS, YARN_HOSTS
    from personalclaw.providers.registry import get_provider_registry

    record = get_provider_registry().get(app)
    manifest = record.manifest if record is not None else None
    if manifest is None:
        return ()
    entries = [launch for launch in manifest.launches if launch.program == program]
    hosts = [h for launch in entries for h in launch.hosts]
    named = manifest.dependencies.npmPackages or any(launch.npmPackage for launch in entries)
    if program in _NPM_PROGRAMS and named:
        hosts.extend(YARN_HOSTS if program == "yarn" else NPM_HOSTS)
    return tuple(dict.fromkeys(hosts))


def declared_programs() -> set[str] | None:
    """The programs the calling app lists under ``launches``, or ``None`` when core itself is the
    caller (and so no manifest applies). An entry for the programs the owner names for the app
    (``*``) names none, so it is not among them: it lets core start nothing."""
    is_app, manifest = _calling_manifest()
    if not is_app:
        return None
    if manifest is None:
        return set()
    return {p.program for p in manifest.launches if p.program != PROGRAM_YOU_NAME}


__all__ = [
    "UNNAMED",
    "NotDeclared",
    "declared_hosts",
    "declared_programs",
    "declares_npm_package",
]
