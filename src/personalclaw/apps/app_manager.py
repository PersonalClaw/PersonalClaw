"""App Platform lifecycle — install / enable / disable / uninstall (A1).

The runtime is PClaw-native, built on top of
the existing manifest (:mod:`apps.manifest`) + storage primitives
(:mod:`apps.manager`). Turns "read what's present" into a real, safety-gated
lifecycle:

* **install(source)** — copy → **stage in quarantine** → validate manifest →
  **scan staged content** (the shared :class:`SkillScanner` gate; ``dangerous``
  is terminal, non-overridable) → require consent for risky verdicts → run
  ``setup.onInstall`` (bounded subprocess) → register providers → write
  ``installed.json``.
* **enable / disable(name)** — run ``setup.onEnable``/``onDisable`` (bounded),
  flip the provider registration.

Removal is a THREE-rung ladder, and each rung is a different promise about the
user's ``data/`` — the notes they wrote, a campaign's ledger, an incident log:

* **uninstall(name)** — DEACTIVATE. Nothing leaves disk; the app is turned off.
* **uninstall_keep_data(name)** — the app's files go, ``data/`` is KEPT (parked at
  ``apps/.{name}.data``) and a later ``install`` of the same name puts it back. It
  REFUSES while an earlier unconsumed copy of that ``data/`` is still on disk, rather
  than deleting or overwriting one — :func:`_unconsumed_data_copies` owns that question.
* **force_uninstall(name)** — everything goes, ``data/`` included.

The middle rung exists because the first two alone force a choice between leaving a
dead app installed forever and destroying the user's data (issue #2541).

Every lifecycle action is SEL-audited. Executing a third-party ``setup`` hook is
RCE-by-design, so a hook only runs after the scanner passes (or the caller gives
explicit consent for a ``warning``) — never on a ``dangerous`` verdict, and never
auto-forced for an unattended/agent-initiated install.

Atomic update + rollback is A2; the dependency ledger is A3; this is the core.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personalclaw.apps import disclosure as app_disclosure
from personalclaw.apps.manager import (
    APP_MANIFEST_FILENAME,
    INSTALLED_META_FILENAME,
    InstalledApp,
    _now_iso,
    _read_installed,
    _validate_app_name,
    _write_installed,
    app_dir,
    apps_dir,
)
from personalclaw.apps.manifest import AppManifest
from personalclaw.atomic_write import atomic_write
from personalclaw.sel import sel
from personalclaw.signing import SignatureInfo, SignatureState, verify_bundle
from personalclaw.supply_chain import ScanReport, TrustTier, Verdict, default_scanner

if TYPE_CHECKING:
    from packaging.requirements import Requirement

logger = logging.getLogger(__name__)

_QUARANTINE_DIRNAME = ".quarantine"
_HOOK_DEFAULT_TIMEOUT = 60  # seconds; setup.onInstall/onUpdate cap
_ROLLBACK_SUFFIX = ".rollback"  # ~/.personalclaw/apps/.{name}.rollback during update
_APP_DATA_DIRNAME = "data"  # app-scoped state preserved across updates
#: ``~/.personalclaw/apps/.{name}.data`` — where a keep-data uninstall parks the
#: app's ``data/`` so the next install of the same name can put it back.
_PRESERVED_DATA_SUFFIX = ".data"
#: Quarantine staging slot holding that copy while the app tree is being removed.
_DATA_STAGE_SUFFIX = ".data.staged"


class AppLifecycleError(Exception):
    """A lifecycle operation failed (validation, scan refusal, hook error).

    ``log_excerpt`` carries the bounded tail of the underlying subprocess output
    (a failed ``pip`` dependency install or a ``setup`` hook) when there is one, so
    the install result can surface it to the UI's "Fix with AI" affordance (APE-8).
    It is UNTRUSTED: a malicious app's build can emit attacker-controlled text.
    """

    def __init__(self, message: str, *, log_excerpt: str = "") -> None:
        super().__init__(message)
        self.log_excerpt = log_excerpt


@dataclass
class InstallResult:
    """Outcome of an install attempt, or of a :func:`preview` — surfaced to the API/UI."""

    ok: bool
    name: str = ""
    scan: ScanReport | None = None
    error: str = ""
    # Nothing was committed: the owner must review `disclosure` + `scan` and consent to
    # exactly the bundle whose digest is `consent`.
    needs_consent: bool = False
    restart_required: bool = (
        False  # an app package the gateway had already loaded was replaced; restart to reload it
    )
    # P21 platform gate: set when the app can't be server-installed here (installMode=client,
    # or this OS isn't in the app's `os` list). The install did NOT commit; the UI shows the
    # copy-paste client-install one-liner instead. `client_install` = {shell, postInstall}.
    needs_client_install: bool = False
    client_install: dict[str, Any] | None = None
    # APE-8 "Fix with AI": the bounded tail of the failing subprocess output (a pip
    # dependency install or a setup hook) when the install failed with one available.
    # UNTRUSTED — a malicious app's build can emit attacker-controlled text — so it is
    # never dropped raw into a prompt; `fix_prompt` fences it (see the property).
    log_excerpt: str = ""
    # What the owner consents OVER, read from the staged bytes (never from the catalog):
    # the manifest's own name and version, `disclosure.describe` of it, and — for an update —
    # the installed version's disclosure to compare against. `consent` is the staged bundle's
    # digest: echo it back and the install commits only if the bytes are still those.
    display_name: str = ""
    version: str = ""
    disclosure: dict[str, Any] | None = None
    previous: dict[str, Any] | None = None
    consent: str = ""

    @property
    def fix_prompt(self) -> str:
        """A ready-to-send chat seed for debugging a failed install, or ``""``.

        Built HERE (backend), not the FE, because the fence is the security control:
        the install log is untrusted text and must be wrapped in the
        ``<untrusted_content>`` fence — which only the Python :func:`fence_untrusted`
        can produce — before it ever reaches a chat prompt (APE-8). The FE button just
        passes this string to ``launchChat({prompt})``. Empty on success or when there
        is no captured log, so the FE shows the button only when it can act.
        """
        if self.ok or not self.log_excerpt.strip():
            return ""
        from personalclaw.security import fence_untrusted

        fenced = fence_untrusted(
            self.log_excerpt,
            source=f"app_install_log:{self.name}",
            source_type="app_install_log",
            source_id=self.name,
        )
        return (
            f"Installing the app '{self.name}' failed. Here is the install log — "
            f"help me figure out why and how to fix it:\n\n{fenced}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "name": self.name,
            "error": self.error,
            "needs_consent": self.needs_consent,
            "restart_required": self.restart_required,
            "needs_client_install": self.needs_client_install,
            "client_install": self.client_install,
            "scan": self.scan.to_dict() if self.scan else None,
            "log_excerpt": self.log_excerpt,
            "fix_prompt": self.fix_prompt,
            "displayName": self.display_name,
            "version": self.version,
            "disclosure": self.disclosure,
            "previous": self.previous,
            "consent": self.consent,
        }


#: How many distinct rule ids a scan annotation names before it degrades to the count
#: alone. The SEL `resources` field is truncated at write time, so an app tripping many
#: rules must not push `consent=true` — the fact an incident asks for — off the end.
_AUDIT_MAX_RULES = 6


def _scan_detail(report: "ScanReport | None", *, consent: bool) -> str:
    """The scanner outcome of one lifecycle decision, as ``resources`` key=value text.

    Renders ``verdict=…``, the rule ids that produced it, and ``consent=true`` when a
    human overrode a WARNING gate. Consent is claimed ONLY for a verdict that actually
    gated: `confirm=True` on a clean bundle authorized nothing, and a log that called
    that an override would make every pre-confirmed install look like a waved-through
    one — the exact question this annotation exists to answer.

    Rule ids and a count only. A finding's ``evidence`` is the matched source snippet,
    and the SEL log is durable and exportable, so the snippet never goes in.
    """
    if report is None:
        return ""
    parts = [f"verdict={report.verdict.value}"]
    rules = sorted({f.rule for f in report.findings if f.rule})
    if rules:
        named = rules[:_AUDIT_MAX_RULES]
        parts.append(f"rules={','.join(named)}")
        if len(rules) > len(named):
            parts.append(f"rules_total={len(rules)}")
    if consent and report.verdict is Verdict.WARNING:
        parts.append("consent=true")
    return " ".join(parts)


def _audit(
    operation: str,
    outcome: str,
    name: str,
    *,
    caller: str = "app_manager",
    error: str = "",
    detail: str = "",
) -> None:
    try:
        sel().log_api_access(
            caller=caller,
            operation=f"app.{operation}",
            outcome=outcome,
            source="app_platform",
            resources=f"app={name} {detail}".rstrip(),
            error=error,
        )
    except Exception:  # noqa: BLE001 — audit must never break the lifecycle
        logger.debug("app lifecycle audit failed", exc_info=True)


def _quarantine_dir() -> Path:
    d = apps_dir() / _QUARANTINE_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tier_for_origin(origin: str) -> TrustTier:
    """Map an install origin to the scanner trust tier."""
    return {
        "builtin": TrustTier.BUILTIN,
        "registry": TrustTier.OFFICIAL,
        "local": TrustTier.COMMUNITY,
        "external": TrustTier.COMMUNITY,
    }.get(origin, TrustTier.COMMUNITY)


def _signature_gate(staged: Path, origin: str) -> tuple[SignatureInfo, TrustTier]:
    """Verify the STAGED bundle's signature and derive the trust tier from it (SH-3).

    Runs before the content scan and long before the commit, so the answer is known for
    the exact bytes that will land: the staged tree is the one the commit step moves into
    place, and nothing re-fetches in between.

    * ``invalid`` → the caller REFUSES (terminal, ``confirm`` does not override).
    * ``signed`` by an in-tree key → tier is raised to ``official`` when the origin would
      otherwise be ``community``. A verified maintainer signature is exactly the
      provenance ``official`` already means for the curated registry. It never *lowers*
      an origin's tier: ``builtin`` stays ``builtin``, and an unsigned bundle keeps the
      tier its origin earned — signing only ever adds trust it can prove.
    * ``unsigned`` → unchanged. Community-tier installable, per C2's graduated trust.
    """
    info = verify_bundle(staged)
    tier = _tier_for_origin(origin)
    if info.state is SignatureState.SIGNED and tier is TrustTier.COMMUNITY:
        tier = TrustTier.OFFICIAL
    return info, tier


def _run_hook(cmd: str, *, cwd: Path, timeout: int, env_name: str) -> None:
    """Run a setup hook as a bounded subprocess. Raises on failure/timeout.

    Mirrors the run-script/bash bounded discipline: a timeout-bounded subprocess
    in the app's own dir. The scanner has already vetted the staged content
    before this ever runs (install gate); a hook that errors aborts the op.

    The hook's environment names the app packages on ``PYTHONPATH``
    (``app_python.hook_env``), so a hook that runs Python can import what the app
    declared — the dependency step runs before ``onInstall``/``onUpdate`` for that.
    """
    if not cmd.strip():
        return
    from personalclaw.apps import app_python

    try:
        proc = subprocess.run(  # noqa: S602 — intentional: vetted third-party setup hook
            cmd,
            shell=True,
            cwd=str(cwd),
            timeout=max(1, timeout),
            capture_output=True,
            text=True,
            env=app_python.hook_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise AppLifecycleError(f"{env_name} hook timed out after {timeout}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise AppLifecycleError(
            f"{env_name} hook exited {proc.returncode}: {tail}", log_excerpt=tail
        )


def _core_requirement_pins() -> dict[str, "Requirement"]:
    """Canonical name → the requirement **core itself** declares, extras EXCLUDED.

    Read from the installed ``personalclaw`` distribution's metadata rather than
    ``pyproject.toml``, which a wheel does not ship.

    Excluding extras is load-bearing, not a nicety: ``openai``, ``anthropic``,
    ``boto3``, ``slack-sdk``, ``faster-whisper``, ``piper-tts``,
    ``sentence-transformers``, ``faiss-cpu`` and ``huggingface-hub`` are all
    ``extra ==`` entries, and 19 of the 20 first-party apps that declare
    ``pythonDependencies`` pin exactly those. Treating an extra as core would
    refuse almost every provider app in the Store.
    """
    from importlib.metadata import requires

    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    pins: dict[str, Requirement] = {}
    for spec in requires("personalclaw") or []:
        req = Requirement(spec)
        # An `extra == "..."` marker means the dependency is opt-in, not core.
        if req.marker is not None and "extra ==" in str(req.marker):
            continue
        pins[canonicalize_name(req.name)] = req
    return pins


def _reject_core_dependency_conflicts(manifest: AppManifest, reqs: list[str]) -> None:
    """Refuse an app that pins a core gateway dependency to a version core does not run (EI-12 D3).

    App packages install into ``<home>/app-python`` (``apps/app_python.py``), which the gateway
    loads AFTER its own environment — so core's installed copy of a package always wins the
    import, and a pin it does not satisfy could never take effect: the app would run on core's
    version whatever it asked for. The rule is exactly that property: for any app requirement
    naming a core-declared dependency, the version **currently installed** must satisfy the
    app's specifier. Anything else is refused before pip runs, with the reason in the sentence.
    (pip itself runs with every distribution the gateway can import pinned, so a TRANSITIVE
    dependency that needs another version of one is refused too — by the resolver, named in
    ``app_python.explain_failure``.)

    Fail-closed on purpose. A requirement that names a core dependency and cannot
    be *proven* compatible is refused, not installed: an unparseable specifier (which
    ``AppManifest.validate()`` does not vet) and a core name whose installed version
    cannot be read both deny. Requirements that do not collide with a core name are
    untouched — the guard's whole population is the collision set.
    """
    if not reqs:
        return
    try:
        from packaging.requirements import InvalidRequirement, Requirement
        from packaging.utils import canonicalize_name

        core = _core_requirement_pins()
    except Exception as exc:  # noqa: BLE001 — no evaluator ⇒ cannot clear a core pin
        raise AppLifecycleError(
            f"cannot verify app {manifest.name}'s python dependencies against core's "
            f"({exc}); refusing rather than install packages the gateway cannot check"
        ) from exc

    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _dist_version

    for spec in reqs:
        try:
            req = Requirement(spec)
        except InvalidRequirement as exc:
            raise AppLifecycleError(
                f"app {manifest.name} declares an unparseable python dependency " f"{spec!r}: {exc}"
            ) from exc
        pin = core.get(canonicalize_name(req.name))
        if pin is None:
            continue  # not a core-owned name — nothing of the gateway's to move
        try:
            have = _dist_version(req.name)
        except PackageNotFoundError as exc:
            raise AppLifecycleError(
                f"app {manifest.name} pins {spec!r}, a dependency core itself declares "
                f"({pin}), but its installed version cannot be read; refusing rather "
                f"than let the install resolve a core dependency"
            ) from exc
        if not req.specifier.contains(have, prereleases=True):
            raise AppLifecycleError(
                f"app {manifest.name} pins {spec!r}, which conflicts with the "
                f"{req.name} {have} this gateway runs (core declares {pin}). An app's "
                f"packages load after PersonalClaw's own, so that pin could never take "
                f"effect, and the install is refused. A newer version of the app may fix this."
            )


def describe_python_dependencies(manifest: AppManifest) -> list[dict[str, Any]]:
    """The app's declared ``pythonDependencies``, each tagged with whether CORE owns it —
    the install-consent disclosure for :func:`_install_python_deps`.

    Installing an app runs ``pip install`` into ``<home>/app-python``, and the gateway loads
    what lands there into its OWN process (after its own packages, so nothing it already uses
    is replaced). That is materially more consequential than most of what the consent dialog
    already enumerates, and the dialog said nothing about it: it listed gateway permissions,
    app messaging, desktop and network reach and dashboard code, and never that a
    third-party package lands in the interpreter holding the owner's credentials,
    filesystem and network. ``docs/security/limitations.md`` §3 documents the
    behaviour, which does not discharge the duty of the surface where consent is
    actually given — a user clicking through a modal does not read the threat model.
    So this exists to put the real specifiers on that screen.

    ``coreOwned`` is the distinction that makes the disclosure honest rather than
    alarming, and it is read from :func:`_core_requirement_pins` — the SAME authority
    :func:`_reject_core_dependency_conflicts` gates on, never a hand-kept list:

      * ``False`` — core does not declare this name, so pip may genuinely install new code
        the gateway's interpreter will load. The provider SDKs (``openai``, ``anthropic``,
        ``slack-sdk``) land here: they are core *extras*, which
        :func:`_core_requirement_pins` excludes on purpose.
      * ``True`` — core declares it (``Pillow``, ``numpy``). Nothing new enters: the
        guard admits the pin only while the version already installed satisfies it and
        refuses the install otherwise, so this reads as "a version you already have is
        acceptable", not as "new code enters your interpreter".

    ``True`` is claimed only when PROVEN. Best-effort by design — this runs per app on
    every catalog scan, so a shape surprise must not break the Store — and an
    unreadable core pin set degrades every spec to ``False``, i.e. to the LOUDER of the
    two disclosures. That is the same fail-closed direction the guard takes, one
    surface along: over-disclosing a package is safe, under-disclosing one is the
    defect being fixed. The specs themselves are always returned verbatim, because
    losing them is the only outcome worse than mis-grouping them.
    """
    reqs = [str(s) for s in manifest.dependencies.pythonDependencies]
    if not reqs:
        return []
    try:
        from packaging.requirements import Requirement
        from packaging.utils import canonicalize_name

        core = _core_requirement_pins()
    except Exception:  # noqa: BLE001 — no evaluator ⇒ every spec reads as new code
        logger.debug("consent: cannot read core's own pins for %s", manifest.name, exc_info=True)
        return [{"spec": s, "coreOwned": False} for s in reqs]

    out: list[dict[str, Any]] = []
    for spec in reqs:
        try:
            owned = canonicalize_name(Requirement(spec).name) in core
        except Exception:  # noqa: BLE001 — an unparseable spec is refused at install
            owned = False
        out.append({"spec": spec, "coreOwned": owned})
    return out


def _install_python_deps(manifest: AppManifest) -> bool:
    """Make an app's declared ``pythonDependencies`` importable. Core ships lean; the app that
    needs a heavy lib brings it — into ``<home>/app-python``, never into the environment the
    gateway runs from (``apps/app_python.py`` owns where, how, and why).

    Admission-gated before pip runs: :func:`_reject_core_dependency_conflicts` refuses a pin on
    a dependency core itself declares that the installed version does not satisfy. An app may
    bring any library core does not own; it may not re-pin one core does.

    A no-op — no pip, no network — when the gateway or an installed app already provides every
    requirement. Returns True iff the gateway must RESTART for the change to take effect (a
    package it had already loaded was replaced); a first install is importable in place.
    Failures raise :class:`AppLifecycleError` whose message is the sentence the user reads and
    whose ``log_excerpt`` is set only when pip's log is the useful next step.
    """
    reqs = list(manifest.dependencies.pythonDependencies)
    if not reqs:
        return False

    # Before anything is installed: refuse a pin on a CORE dependency that core's installed
    # version does not satisfy — it could never take effect (EI-12 D3).
    _reject_core_dependency_conflicts(manifest, reqs)

    from personalclaw.apps import app_python

    try:
        return app_python.ensure(manifest.name, reqs, label=manifest.displayName or manifest.name)
    except app_python.PackageInstallError as exc:
        raise AppLifecycleError(str(exc), log_excerpt=exc.log_excerpt) from exc


def _collect_app_packages() -> None:
    """Drop app packages no installed app still needs. Best-effort: a lifecycle step that
    already succeeded (or already failed for its own reason) must not fail on the cleanup."""
    try:
        from personalclaw.apps import app_python

        app_python.collect()
    except Exception:  # noqa: BLE001 — the next collection (at boot, at latest) retries it
        logger.warning("collecting unused app packages failed", exc_info=True)


def _core_version_gate(manifest: AppManifest, *, action: str) -> None:
    """Raise when the running core is older than the app's declared floor (#1778).

    ``minPersonalClawVersion`` is a compat gate; before this it was declared, validated
    and round-tripped but read by nothing, so an app built against a newer SDK surface
    installed happily and then failed at runtime inside the app backend — surfacing as an
    app bug rather than a version mismatch.

    Only ``incompatible`` refuses. A malformed floor or an unmeasurable host fails OPEN
    with a warning — see the four-state note in :mod:`personalclaw.apps.manifest`, which
    owns the decision itself so no path re-derives the comparison."""
    compat = manifest.core_compatibility()
    if not compat.admits:
        raise AppLifecycleError(f"{action} refused: {manifest.name!r} {compat.reason}")
    if compat.reason:
        logger.warning("app %s: %s", manifest.name, compat.reason)


def _load_staged_manifest(staged: Path, *, action: str = "install") -> AppManifest:
    """Parse + gate the manifest at ``staged``. THE chokepoint every write path crosses.

    ``install`` calls this for the source peek AND the staged copy; ``update`` does the
    same — so the core-version gate lives here rather than as a per-entry-point copy that
    can drift. ``enable`` and the boot backend launcher ask
    :meth:`AppManifest.core_compatibility` directly (their manifest is already installed,
    so there is nothing to stage)."""
    mpath = staged / APP_MANIFEST_FILENAME
    if not mpath.is_file():
        raise AppLifecycleError(f"no {APP_MANIFEST_FILENAME} in source")
    try:
        manifest = AppManifest.from_json_file(mpath)
    except Exception as exc:  # noqa: BLE001
        raise AppLifecycleError(f"invalid manifest: {exc}") from exc
    errors = manifest.validate()
    if errors:
        raise AppLifecycleError(f"manifest validation failed: {'; '.join(errors)}")
    _core_version_gate(manifest, action=action)
    return manifest


def _provider_registry():
    from personalclaw.providers.registry import get_provider_registry

    return get_provider_registry()


def _start_backend(manifest: AppManifest) -> None:
    """Launch the app's backend subprocess (if declared) for the reverse-proxy."""
    if not manifest.backend.entryPoint:
        return
    try:
        from personalclaw.apps.backend_runtime import get_backend_supervisor

        get_backend_supervisor().start(manifest)
    except Exception:
        logger.debug("app %s: backend start failed", manifest.name, exc_info=True)


def _stop_backend(name: str) -> None:
    try:
        from personalclaw.apps.backend_runtime import get_backend_supervisor

        get_backend_supervisor().stop(name)
    except Exception:
        logger.debug("app %s: backend stop failed", name, exc_info=True)


def _stop_worker(name: str) -> None:
    """Stop *name*'s background worker, then reap anything a prior gateway orphaned.

    APE-3's V1 clause is "uninstall leaves no orphan worker", and the sweep alone cannot
    deliver it: the sweep stops workers whose app went away, but a process re-parented to
    init by an ungraceful gateway exit is in no supervisor's table, so nothing would ever
    look for it once the app directory is gone. This is called on the same disable/uninstall
    path as ``_stop_backend`` — while the entry path is still resolvable.

    Best-effort by construction: an app being turned off must not fail because its worker
    was already dead."""
    try:
        from personalclaw.apps.background import WORKER_ENTRY_POINT
        from personalclaw.apps.worker_runtime import get_worker_supervisor

        sup = get_worker_supervisor()
        sup.stop(name)  # `worker=None` stops every worker this app has
        # Then the orphans: `reap_orphans` needs the entry path, and it is only resolvable
        # while the app directory still exists — which is exactly why this runs here rather
        # than being left to the next sweep, by which time the directory may be gone.
        sup.reap_orphans(name, (app_dir(name) / WORKER_ENTRY_POINT).resolve())
    except Exception:
        logger.debug("app %s: worker stop failed", name, exc_info=True)


def _register_mcp(manifest: AppManifest) -> None:
    """Wire the app's declared mcpServers into the live MCP config."""
    if not manifest.mcpServers:
        return
    try:
        from personalclaw.apps import mcp_bridge

        mcp_bridge.register_app_mcp_servers(manifest)
    except Exception:
        logger.debug("app %s: MCP register failed", manifest.name, exc_info=True)


def _deregister_mcp(name: str) -> None:
    try:
        from personalclaw.apps import mcp_bridge

        mcp_bridge.deregister_app_mcp_servers(name)
    except Exception:
        logger.debug("app %s: MCP deregister failed", name, exc_info=True)


def _register_proposal_kinds(manifest: AppManifest, name: str) -> None:
    """Register the app's declared ``permissions.proposals`` kinds (INU-7).

    At enable time, so a declared kind is REGISTERED before the app can post one — the
    ``POST /api/inbox/proposals`` 403 reads the manifest, and delivery policy reads the
    registry, and neither works if the pair was never minted.
    """
    if not manifest.permissions.proposals:
        return
    try:
        from personalclaw.proposals_contract import register_app_proposal_kinds

        register_app_proposal_kinds(name, manifest)
    except Exception:
        logger.debug("app %s: proposal kind register failed", name, exc_info=True)


def _deregister_proposal_kinds(manifest: AppManifest, name: str) -> None:
    """Drop the app's proposal kinds so a disabled app leaves no phantom kind."""
    if not manifest.permissions.proposals:
        return
    try:
        from personalclaw.proposals_contract import deregister_app_proposal_kinds

        deregister_app_proposal_kinds(name, manifest)
    except Exception:
        logger.debug("app %s: proposal kind deregister failed", name, exc_info=True)


def _seed_app_prompts(manifest: AppManifest, name: str) -> None:
    """Seed the app's declared prompts/snippets into the native store (an app OWNS
    its prompts). Best-effort: a seeding failure never breaks the lifecycle."""
    if not manifest.prompts:
        return
    try:
        from personalclaw.apps.prompt_seed import seed_app_prompts

        seed_app_prompts(manifest, app_dir(name))
    except Exception:
        logger.debug("app %s: prompt seed failed", name, exc_info=True)


def _remove_app_prompts(manifest: AppManifest, name: str) -> None:
    """Remove the app's own seeded prompts + unregister its prompt use-cases."""
    try:
        from personalclaw.apps.prompt_seed import remove_app_prompts

        remove_app_prompts(manifest, app_dir(name))
    except Exception:
        logger.debug("app %s: prompt remove failed", name, exc_info=True)


def _origin_of(name: str) -> str:
    """The recorded install origin for an app (default ``local`` if absent)."""
    meta = _read_installed(name)
    return getattr(meta, "origin", "") or "local" if meta is not None else "local"


def trust_tier_of(name: str) -> str:
    """The supply-chain trust tier of an INSTALLED app, as a plain string.

    The one read every surface that discloses provenance after the fact should use —
    #2627: the Tools page badged an installed community bundle ``built-in``, the word
    core's own first-party providers get, erasing the "Unsigned — community tier" the
    install dialog had just made the user consent to.

    Prefers the tier the gate RECORDED (``installed.json``), because that is the only
    value that knows whether a maintainer signature raised the bundle above what its
    origin alone earns. Falls back to :func:`_tier_for_origin` for an app installed
    before the field existed — which can only ever UNDERSTATE trust (a signed local
    bundle reads ``community``), never overstate it. An unknown app is ``community``:
    "we cannot establish provenance" must not render as shipped-with-the-product.
    """
    meta = _read_installed(name)
    if meta is None:
        return TrustTier.COMMUNITY.value
    recorded = getattr(meta, "tier", "") or ""
    if recorded:
        return recorded
    return _tier_for_origin(getattr(meta, "origin", "") or "local").value


def _seed_app_skills(manifest: AppManifest, name: str, *, origin: str | None = None) -> None:
    """Seed the app's declared SKILL.md skills THROUGH the supply-chain chokepoint
    (an app OWNS its skills; the gate is never bypassed). Best-effort: a seeding
    failure never breaks the lifecycle."""
    if not manifest.skills:
        return
    try:
        from personalclaw.apps.skill_seed import seed_app_skills

        seed_app_skills(manifest, app_dir(name), origin=origin or _origin_of(name))
    except Exception:
        logger.debug("app %s: skill seed failed", name, exc_info=True)


def _remove_app_skills(manifest: AppManifest, name: str) -> None:
    """Remove the app's own seeded skills (provenance-keyed, never a user's skill)."""
    try:
        from personalclaw.apps.skill_seed import remove_app_skills

        remove_app_skills(manifest, app_dir(name))
    except Exception:
        logger.debug("app %s: skill remove failed", name, exc_info=True)


@dataclass
class _Reviewed:
    """A staged bundle past the terminal gates, with everything consent is given over."""

    manifest: AppManifest
    report: ScanReport
    digest: str
    disclosure: dict[str, Any]


@dataclass
class _Refused:
    """A terminal gate outcome no consent overrides, and what the audit row should say."""

    result: InstallResult
    audit_error: str
    #: The refusal came from the SCAN, so its report belongs in the audit detail. An
    #: invalid signature never reached the scanner, and a verdict it never computed must
    #: not be logged as if it had.
    scanned: bool


def _review(staged: Path, *, origin: str, action: str) -> "_Reviewed | _Refused":
    """Gate ``staged`` up to the point of consent — manifest, signature, scan — the ONE
    way :func:`install`, :func:`update` and :func:`preview` read a bundle, so the review
    a consent dialog shows and the check a commit makes cannot disagree about it.

    Audits nothing: whether this was a real attempt is the caller's to say. Raises
    :class:`AppLifecycleError` for an unusable manifest."""
    manifest = _load_staged_manifest(staged, action=action)
    what = app_disclosure.describe(manifest)
    facts: dict[str, Any] = {
        "name": manifest.name,
        "display_name": manifest.displayName or manifest.name,
        "version": manifest.version,
        "disclosure": what,
    }
    # The signature BEFORE the scan (SH-3): "someone tampered with a signed artifact" is
    # not a risk the user is in a position to accept, so nothing overrides it. Unsigned is
    # not invalid — it installs at community tier.
    signature, tier = _signature_gate(staged, origin)
    if signature.is_invalid:
        return _Refused(
            InstallResult(
                ok=False,
                scan=ScanReport(tier=tier, signature=signature),
                error=f"{action} refused: invalid signature — {signature.reason}",
                **facts,
            ),
            audit_error=f"signature: {signature.reason}",
            scanned=False,
        )
    report = default_scanner.scan(staged, tier)
    report.signature = signature
    if report.verdict is Verdict.DANGEROUS:
        return _Refused(
            InstallResult(
                ok=False,
                scan=report,
                error=f"{action} refused: scanner flagged dangerous content",
                **facts,
            ),
            audit_error="scan: dangerous",
            scanned=True,
        )
    return _Reviewed(manifest, report, app_disclosure.bundle_digest(staged), what)


def _awaiting_consent(
    r: _Reviewed, *, error: str, needed: bool = True, previous: dict[str, Any] | None = None
) -> InstallResult:
    """What the owner is asked to consent to — nothing committed. ``needed`` is whether a
    commit will require it (an update that changes nothing it gets does not)."""
    return InstallResult(
        ok=False,
        name=r.manifest.name,
        scan=r.report,
        needs_consent=needed,
        error=error,
        display_name=r.manifest.displayName or r.manifest.name,
        version=r.manifest.version,
        disclosure=r.disclosure,
        previous=previous,
        consent=r.digest,
    )


def _consent_error(action: str, report: ScanReport, *, stale: bool) -> str:
    """The API ``error`` for a commit refused for want of consent — one clause per cause."""
    if stale:
        return f"{action} needs consent again: the app changed after it was reviewed"
    if report.verdict is Verdict.WARNING:
        return f"{action} needs consent: scanner raised warnings"
    if action == "update":
        return "update needs consent: it changes what the app gets"
    return "install needs consent: review what the app gets first"


def _client_install_directive(r: _Reviewed) -> InstallResult | None:
    """P21 Gap B: an app that must be installed on the user's own machine
    (``installMode="client"``), or that does not support THIS server's OS, cannot be
    server-installed here — hand back the copy-paste one-liner instead, committing nothing.
    That shell runs on the user's machine, OUTSIDE the scanner, so it is surfaced as
    trusted-by-inspection copy-paste and never auto-run. ``None`` for a server-installable app."""
    import sys as _sys

    platform_cfg = r.manifest.platform
    if platform_cfg is None or (
        platform_cfg.installMode != "client" and platform_cfg.supports_platform(_sys.platform)
    ):
        return None
    name = r.manifest.name
    return InstallResult(
        ok=False,
        name=name,
        scan=r.report,
        needs_client_install=True,
        client_install=platform_cfg.clientInstall.to_dict() or {},
        error=(
            f"'{name}' installs on your local machine, not this server"
            if platform_cfg.installMode == "client"
            else f"'{name}' does not support this server's platform ({_sys.platform})"
        ),
        display_name=r.manifest.displayName or name,
        version=r.manifest.version,
        disclosure=r.disclosure,
    )


def _installed_disclosure(name: str) -> dict[str, Any] | None:
    """What the INSTALLED copy of ``name`` gets, or ``None`` when its manifest is unreadable."""
    manifest = _manifest_of(name)
    return app_disclosure.describe(manifest) if manifest is not None else None


def preview(source: str | Path, *, origin: str = "local", name: str | None = None) -> InstallResult:
    """What installing ``source`` — or, given ``name``, updating that installed app to it —
    puts in front of the owner, WITHOUT committing, auditing or running anything it ships.

    The same staging and gates as :func:`install` / :func:`update` (:func:`_review`), so
    the review a consent dialog shows is the one the commit checks: ``disclosure`` comes
    from the staged manifest, ``scan`` from the staged bytes, and ``consent`` is their
    digest. ``needs_consent`` says whether a commit will require it — always for an
    install; for an update only when it changes what the app gets, or the scan warns.

    A terminal outcome (invalid signature, ``dangerous``) comes back as its refusal with
    the scan; a P21 client-install app as its directive; a bundle that cannot be offered at
    all (bad manifest, too-new core, already installed / not installed) as ``ok=False``
    with only ``error`` set."""
    src = Path(source)
    if not src.is_dir():
        return InstallResult(ok=False, error=f"source is not a directory: {source}")
    action = "update" if name else "install"
    try:
        peek = _load_staged_manifest(src, action=action)
    except AppLifecycleError as exc:
        return InstallResult(ok=False, error=str(exc))
    target = name or peek.name
    if name:
        if _read_installed(name) is None:
            return InstallResult(
                ok=False, name=name, error=f"app {name!r} is not installed (use install)"
            )
        if peek.name != name:
            return InstallResult(
                ok=False, name=name, error=f"manifest name {peek.name!r} ≠ target {name!r}"
            )
    elif app_dir(target).exists():
        return InstallResult(
            ok=False, name=target, error=f"app {target!r} already installed (use update)"
        )
    # A slot of its own, so a preview never collides with a concurrent install's staging.
    slot = Path(tempfile.mkdtemp(prefix=f"{target}.preview-", dir=_quarantine_dir()))
    staged = slot / target
    try:
        shutil.copytree(src, staged)
        gate = _review(staged, origin=origin, action=action)
        if isinstance(gate, _Refused):
            return gate.result
        if not name:
            directive = _client_install_directive(gate)
            if directive is not None:
                return directive
            return _awaiting_consent(gate, error="")
        previous = _installed_disclosure(name)
        needed = gate.report.verdict is Verdict.WARNING or app_disclosure.changed(
            previous, gate.disclosure
        )
        return _awaiting_consent(gate, error="", needed=needed, previous=previous)
    except AppLifecycleError as exc:
        return InstallResult(ok=False, name=target, error=str(exc))
    finally:
        shutil.rmtree(slot, ignore_errors=True)


def install(
    source: str | Path,
    *,
    origin: str = "local",
    confirm: bool = False,
    consent: str = "",
    caller: str = "app_manager",
    source_ref: str | None = None,
) -> InstallResult:
    """Install an app from a local directory ``source`` (path/git → A4 fetch).

    Staged → gated (signature, scan) → CONSENT → onInstall → registered.

    🔑 EVERY install needs consent. A clean scan says the content looks safe; it does not
    say the owner agreed to what the app is granted and will run — and treating it as if
    it did is how an app with API reach, an agent grant and a daily cron installed in one
    click while the consent screen appeared only for scanner warnings. ``confirm=True`` is
    the owner's agreement, to the grants and to any scanner warning alike. ``consent`` is
    that agreement bound to BYTES: the digest :func:`preview` returned for the copy the
    owner reviewed. A non-empty ``consent`` is itself the agreement, and it commits only if
    the staged bundle still has that digest — a source that changed after review (a git
    remote serving a different tree on the second fetch) gets a fresh review, never the
    first one's yes. Without either, the result carries the disclosure, scan and digest —
    the review :func:`preview` returns — and nothing is committed.

    A ``dangerous`` verdict or an invalid signature is terminal: nothing overrides it.

    ``source_ref`` is the provenance recorded in ``installed.json`` — the ORIGINAL
    source string (e.g. the git URL), not the resolved local dir. A git clone
    resolves to a throwaway temp path; recording that is useless for grouping the
    Store by source, so the handler passes the URL here. Defaults to ``source``.
    """
    src = Path(source)
    if not src.is_dir():
        _audit("install", "error", str(source), caller=caller, error="source not a directory")
        return InstallResult(ok=False, error=f"source is not a directory: {source}")

    # 1. Stage in quarantine FIRST — dangerous content never touches the live tree.
    staged_root = _quarantine_dir()
    try:
        manifest_peek = _load_staged_manifest(src)
    except AppLifecycleError as exc:
        _audit("install", "error", str(source), caller=caller, error=str(exc))
        return InstallResult(ok=False, error=str(exc))
    name = manifest_peek.name
    staged = staged_root / name
    if staged.exists():
        shutil.rmtree(staged, ignore_errors=True)
    shutil.copytree(src, staged)

    granted = confirm or bool(consent)
    try:
        # 2-4. Manifest (source of truth is the staged copy), signature, scan — terminal
        # refusals first, before anything the owner could be asked to accept.
        gate = _review(staged, origin=origin, action="install")
        if isinstance(gate, _Refused):
            _audit(
                "install",
                "refused",
                name,
                caller=caller,
                error=gate.audit_error,
                detail=_scan_detail(gate.result.scan, consent=granted) if gate.scanned else "",
            )
            return gate.result
        manifest = gate.manifest
        report = gate.report

        # 4.5 Platform gate (P21 Gap B) — a directive, not an install, so it needs no consent.
        directive = _client_install_directive(gate)
        if directive is not None:
            platform_cfg = manifest.platform
            _audit(
                "install",
                "client_install_required",
                name,
                caller=caller,
                error=f"installMode={platform_cfg.installMode} os={platform_cfg.os}",
            )
            return directive

        dest = app_dir(name)
        if dest.exists():
            _audit("install", "error", name, caller=caller, error="already installed")
            return InstallResult(
                ok=False,
                name=name,
                scan=report,
                error=f"app {name!r} already installed (use update)",
            )

        # 4.9 Consent — for EVERY install, and bound to these bytes when a digest was given.
        stale = bool(consent) and consent != gate.digest
        if stale or not granted:
            _audit(
                "install",
                "needs_consent",
                name,
                caller=caller,
                detail=_scan_detail(report, consent=False),
            )
            return _awaiting_consent(gate, error=_consent_error("install", report, stale=stale))

        # 5. Commit: move staged → live app dir. These are the exact bytes the signature
        # covered, the scanner read and the owner consented to — nothing re-fetches between.
        shutil.move(str(staged), str(dest))

        # Put back a data/ that an earlier keep-data uninstall parked for this name,
        # BEFORE any hook runs — same ordering and same precedence as the update path.
        # COPIED, not moved: the rollbacks below still `rmtree(dest)`, and a rolled-back
        # install must not take the user's only copy of their data with it. `parked` is
        # dropped further down, once the install is past its last rollback.
        data_fact, parked = _restore_preserved_data(name, dest)

        # Ensure the app's data/ dir exists BEFORE any hook runs — apps write
        # state there (it's the dir preserved across updates), and an onInstall
        # hook commonly seeds it.
        (dest / _APP_DATA_DIRNAME).mkdir(parents=True, exist_ok=True)

        # 5a. Install declared python deps into <home>/app-python (core is lean; the
        # app brings its heavy libs). Before the onInstall hook so a hook can import
        # them. The rollback collects whatever a half-finished pip run left behind.
        try:
            restart_required = _install_python_deps(manifest)
        except AppLifecycleError as exc:
            shutil.rmtree(dest, ignore_errors=True)  # roll back the commit
            _collect_app_packages()
            _audit("install", "error", name, caller=caller, error=str(exc))
            return InstallResult(
                ok=False, name=name, scan=report, error=str(exc), log_excerpt=exc.log_excerpt
            )

        # 5b. Run onInstall (bounded) — only after the gate passed.
        try:
            _run_hook(
                manifest.setup.onInstall,
                cwd=dest,
                timeout=_HOOK_DEFAULT_TIMEOUT,
                env_name="onInstall",
            )
        except AppLifecycleError as exc:
            shutil.rmtree(dest, ignore_errors=True)  # roll back the commit
            _collect_app_packages()  # …and the packages only this app needed
            _audit("install", "error", name, caller=caller, error=str(exc))
            return InstallResult(
                ok=False, name=name, scan=report, error=str(exc), log_excerpt=exc.log_excerpt
            )

        # 6. Persist installed.json + register providers.
        meta = InstalledApp(
            name=name,
            version=manifest.version,
            displayName=manifest.displayName or name,
            enabled=True,
            installedAt=_now_iso(),
            updatedAt=_now_iso(),
            source=str(source_ref if source_ref is not None else source),
            origin=origin if origin in {"builtin", "registry", "local", "external"} else "local",
            # The tier the gate above settled on for these exact bytes — recorded so
            # every later provenance surface (the Tools badge, #2627) reads the SAME
            # value the install dialog just disclosed, rather than re-deriving one.
            tier=report.tier.value,
        )
        _write_installed(name, meta)
        if manifest.all_providers():
            try:
                _provider_registry().register(manifest, enabled=True)
            except Exception:
                logger.exception("app %s: provider registration failed", name)
        # Seed the app's own prompts/snippets into the native store (idempotent,
        # non-clobbering) so an app OWNS the prompts it ships.
        _seed_app_prompts(manifest, name)
        # Seed the app's own skills through the supply-chain chokepoint (scan at the
        # app's trust tier) — an app skill never bypasses the gate (§4.1).
        _seed_app_skills(manifest, name, origin=meta.origin)
        # Record this app against each shared dependency it declares (A3 ledger),
        # so a later uninstall can tell removable from shared.
        try:
            from personalclaw.apps import dependency_ledger

            dependency_ledger.record_install(manifest)
        except Exception:
            logger.debug("app %s: dependency-ledger record failed", name, exc_info=True)
        _register_mcp(manifest)
        _start_backend(manifest)
        # Past every rollback now — the parked copy has served its purpose and holding
        # it any longer would let a LATER force-uninstall miss it.
        if parked is not None:
            shutil.rmtree(parked, ignore_errors=True)
        # The scanner outcome rides the SUCCESS event too. A warning the user overrode by
        # confirming is the most security-relevant decision in this flow and the first
        # thing an incident asks about; without it here, a waved-through install is
        # byte-identical in the log to one that scanned clean. `preserved_data=` rides it
        # for the same reason on the data side: whether this install put a previous
        # install's data back is not reconstructible after the fact.
        _audit(
            "install",
            "ok",
            name,
            caller=caller,
            detail=" ".join(x for x in (_scan_detail(report, consent=granted), data_fact) if x),
        )
        # Named for the person told about it: "Installed Growth Tracker." — not the slug, and
        # not whatever the install surface had to go on (a pasted URL, for one).
        return InstallResult(
            ok=True,
            name=name,
            scan=report,
            restart_required=restart_required,
            display_name=manifest.displayName or name,
            version=manifest.version,
        )
    except AppLifecycleError as exc:
        _audit("install", "error", name, caller=caller, error=str(exc))
        return InstallResult(ok=False, name=name, error=str(exc))
    finally:
        shutil.rmtree(staged, ignore_errors=True)  # GC quarantine (success moved it)


def _rollback_dir(name: str) -> Path:
    """The mid-update rollback copy of an app: ``apps/.{name}.rollback``.

    Guards the name itself, with the same kebab rule ``app_dir``/``app_data_dir`` use.
    ``update()`` already refuses a name that is not installed, so today nothing
    path-shaped reaches here — but this expression is a ``shutil.move``/``rmtree``
    target, and a guard that lives in the caller is one refactor away from being gone
    (#455's class). The rule belongs on the expression that builds the path.
    """
    return apps_dir() / f".{_validate_app_name(name)}{_ROLLBACK_SUFFIX}"


def _preserved_data_dir(name: str) -> Path:
    """Where a keep-data uninstall parks an app's ``data/``: ``apps/.{name}.data``.

    Guards the name on the EXPRESSION that builds the path, for the same reason
    :func:`_rollback_dir` does: this is an ``rmtree``/``move`` target, and a guard
    that lives only in the caller is one refactor away from being gone.

    Dot-prefixed and carrying no ``installed.json``, so :func:`~apps.manager.list_apps`
    (which skips any dir without one) never reports a parked copy as an installed app
    — the app really is gone from every surface, which is the whole point of the rung.
    """
    return apps_dir() / f".{_validate_app_name(name)}{_PRESERVED_DATA_SUFFIX}"


def _data_stage_dir(name: str) -> Path:
    """The quarantine slot a keep-data uninstall copies ``data/`` into before parking it.

    Extracted from :func:`uninstall_keep_data` so the name is guarded on the EXPRESSION
    that builds the path, the same rule :func:`_preserved_data_dir` and
    :func:`_rollback_dir` follow — this is an ``rmtree``/``rename`` target too, and it
    was the one of the three built inline.

    Reached only by that one function, which is exactly what made it dangerous: nothing
    else creating it meant nothing else had to reason about finding one already there
    (#2585).
    """
    return _quarantine_dir() / f"{_validate_app_name(name)}{_DATA_STAGE_SUFFIX}"


def _unconsumed_data_copies(name: str) -> list[Path]:
    """THE predicate: every directory holding a copy of *name*'s ``data/`` that nothing
    else on disk holds. Empty list ⇒ the keep-data path is free to run.

    ONE owner for the question "is there an earlier copy of this app's data still here?"
    Before this, that question was answered implicitly in three places by call ORDER
    rather than by looking — and each of the three answers was wrong in a state the
    product can actually reach (#2585):

    * :func:`uninstall_keep_data` treated an existing STAGE as leftover garbage and
      ``rmtree``'d it. Only that function ever writes that path, and it leaves one behind
      in exactly one case: a park that failed, where the stage is the user's LAST copy
      (#2574). So the one state the sweep could ever find was the one it must not touch.
    * the same function ``rmtree``'d an existing PARK before moving the new copy over it.
      A park coexists with an installed app only when an earlier restore FAILED, so that
      copy is data the user has never seen — and the delete was ``ignore_errors=True``,
      so when it silently failed instead, ``shutil.move`` found a directory at the
      destination and moved the stage INSIDE it, reporting success.
    * :func:`force_uninstall` — which the middle rung calls to do its removal — discards
      any park for the name. Reached from ``uninstall_keep_data``, that wipes the earlier
      unconsumed copy before the new one is even made, and returns ``True``.

    Both paths are checked together because both are the same kind of thing (a copy of
    the user's ``data/`` that no live app tree holds) and the caller's decision is the
    same for both: do not proceed, name them, let the user resolve it. Consulting one and
    not the other is how the family got three members.

    A name that cannot mint a path cannot hold a copy under one either, so it has none.
    """
    try:
        candidates = (_preserved_data_dir(name), _data_stage_dir(name))
    except ValueError:
        return []
    return [p for p in candidates if p.is_dir()]


def _dir_entry_count(path: Path) -> int:
    """Top-level entries in *path*, or 0 if it cannot be read."""
    try:
        return sum(1 for _ in path.iterdir())
    except OSError:
        return 0


# A LIVE app's ``data/`` can be changing underneath the one copy that stands between the
# user and losing it. ``shutil.copytree`` enumerates a directory with ``os.scandir`` and
# copies each entry afterwards, so anything that disappears inside that window makes it
# raise ``shutil.Error`` — and both callers below read any ``OSError`` as "fail closed,
# remove nothing". The user then sees a refusal they did not cause and cannot act on.
#
# MEASURED, not hypothesised (#3324). The notes fixture's app tool commits into a git repo
# under ``data/``, and ``git commit`` ends by spawning ``git maintenance run --auto --quiet
# --detach``. That child is DETACHED, so it outlives the ``git commit`` the app waited for,
# and it holds ``.git/objects/maintenance.lock`` — created and removed inside our walk.
# Confirmed on git 2.54.0 by polling for the file, and it is the exact path main's `Full`
# run 35764976454 failed two macOS legs on. Nothing about it is test-only: any app whose
# ``data/`` holds a git checkout, an SQLite WAL or its own lockfile has the same exposure.
#
# WAIT FOR THE TREE TO SETTLE, and deliberately NOT "skip whatever vanished". A vanished
# entry is usually a lock file we would rightly ignore, but it is also what a git repack
# looks like from outside: loose objects are RENAMED into a new packfile, so a walk that
# misses them both ways round yields a ``.git`` whose objects are simply gone. Skipping
# would trade a loud refusal for a silently corrupt copy of the user's work, which is the
# one outcome this ladder exists to prevent. So the copy is retried whole, and if the tree
# never settles the caller still fails closed on the original error.
_LIVE_COPY_ATTEMPTS = 4
_LIVE_COPY_SETTLE_SECS = 0.25  # doubles per attempt: 0.25 → 0.5 → 1.0


def _only_vanished_sources(exc: shutil.Error) -> bool:
    """True when EVERY entry ``copytree`` failed on is no longer at its source path.

    Structural, never a string match on the message. ``shutil.Error`` flattens each
    per-entry cause to ``str``, so the errno is gone by the time we see it — but "is it
    there now" is a question the filesystem answers directly. ENOSPC, EACCES and EIO all
    leave the source in place, so they answer False and fail closed immediately, which is
    what keeps the errno diagnosis the callers log worth reading.
    """
    entries = exc.args[0] if exc.args else None
    if not isinstance(entries, list) or not entries:
        return False
    for entry in entries:
        if not isinstance(entry, tuple) or len(entry) != 3:
            return False
        src = Path(entry[0])
        # ``is_symlink`` as well as ``exists``: a dangling symlink is still an entry that
        # is present and failed for its own reason, not one that vanished.
        if src.exists() or src.is_symlink():
            return False
    return True


def _copy_live_tree(src: Path, dst: Path) -> None:
    """``shutil.copytree``, retried while the source tree is still settling.

    Re-raises the last error once the attempts run out, so every caller's fail-closed
    branch stays exactly as loud as it was.
    """
    for attempt in range(_LIVE_COPY_ATTEMPTS):
        try:
            shutil.copytree(src, dst)
            return
        except shutil.Error as exc:
            if attempt == _LIVE_COPY_ATTEMPTS - 1 or not _only_vanished_sources(exc):
                raise
            logger.info(
                "copying app data %s -> %s raced a concurrent writer (%s); the tree is "
                "still settling, retrying (attempt %d of %d)",
                src,
                dst,
                exc,
                attempt + 2,
                _LIVE_COPY_ATTEMPTS,
            )
            # The failed attempt left a PARTIAL tree behind; the retry must start clean or
            # `copytree` would refuse the existing destination.
            shutil.rmtree(dst, ignore_errors=True)
            time.sleep(_LIVE_COPY_SETTLE_SECS * (2**attempt))


def _data_fact(key: str, path: Path | None) -> str:
    """``key=absent`` | ``key=empty`` | ``key=N`` — three DISTINCT facts, never merged.

    "the app had no ``data/`` at all" and "the app had a ``data/`` and it was empty"
    are different facts about the user's state, and a log that renders both as
    "nothing to keep" cannot answer the only question an incident asks: was there
    something, and did we keep it? So absence of the directory and emptiness of the
    directory get their own tokens, and a count gets a number.
    """
    if path is None or not path.is_dir():
        return f"{key}=absent"
    n = _dir_entry_count(path)
    return f"{key}=empty" if n == 0 else f"{key}={n}"


def _discard_preserved_data(name: str) -> None:
    """Drop any parked ``data/`` held for *name*. Never raises."""
    try:
        target = _preserved_data_dir(name)
    except ValueError:  # not a mintable app name ⇒ nothing was ever parked under it
        return
    shutil.rmtree(target, ignore_errors=True)


def _restore_preserved_data(name: str, dest: Path) -> tuple[str, Path | None]:
    """Put a parked ``data/`` back into a freshly installed tree at *dest*.

    Returns ``(audit fact, the parked dir to drop once the install is past rollback)``.
    ``None`` for the second element means "nothing to drop" — either nothing was
    parked, or the restore failed and the parked copy must be LEFT where it is.

    Precedence is the update path's: the user's data replaces whatever the incoming
    tree ships under ``data/``. A bundle's shipped ``data/`` is seed content; the
    parked copy is the user's own work, and the user's own work wins.

    Treating the parked copy as AUTHORITATIVE is licensed by exactly one thing: the park
    is a single :meth:`~pathlib.Path.rename` within one filesystem, so the directory
    exists only if the whole copy landed (#2585). That is the guarantee, it lives on that
    one line in :func:`uninstall_keep_data`, and ``tests/test_app_data_copy_owner.py``
    fails if any other site learns to write or destroy that path. It is NOT "the parked
    dir is non-empty, so it must be finished" — this function cannot check completeness
    and does not try to.

    A restore failure does not abort the install (the user asked for the app), but it
    is never silent: the fact is ``preserved_data=restore_failed`` and the parked copy
    stays on disk, so it is recoverable rather than lost. A keep-data uninstall of the
    same app then REFUSES rather than parking over that copy.
    """
    try:
        parked = _preserved_data_dir(name)
    except ValueError:
        return _data_fact("preserved_data", None), None
    if not parked.is_dir():
        return _data_fact("preserved_data", None), None
    fact = _data_fact("preserved_data", parked)
    target = dest / _APP_DATA_DIRNAME
    try:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(parked, target)
    except OSError:
        logger.warning("app %s: could not restore preserved data/", name, exc_info=True)
        return "preserved_data=restore_failed", None
    logger.info("app %s: restored preserved data/ from %s", name, parked)
    return fact, parked


def update(
    source: str | Path,
    name: str | None = None,
    *,
    origin: str = "local",
    confirm: bool = False,
    consent: str = "",
    caller: str = "app_manager",
) -> InstallResult:
    """Atomically update an installed app to new code at ``source`` (A2).

    Consent is the same contract as :func:`install` (``confirm``, or a ``consent`` digest
    bound to the reviewed bytes), required when the update CHANGES what the app gets —
    any grant, scheduled job, package, hook, server or dashboard code
    (:func:`disclosure.changed` against the installed copy) — or the scan warns. An update
    that changes none of that needs none: nothing new is being agreed to.

    State machine, rollback on ANY failure:

      stage+scan new  →  preserve old data/  →  move live → .{name}.rollback
                      →  swap new in  →  run onUpdate
        success:  drop .rollback, re-register, write installed.json
        failure:  restore .rollback → live, re-register OLD, drop the failed new

    The new code is scanned BEFORE the swap (an update is a fresh fetch of mutable
    content), so a now-dangerous update never lands — and the old app is untouched
    if it's refused. A leftover ``.{name}.rollback`` dir signals an update that
    crashed mid-swap; :func:`recover_interrupted_updates` reconciles it at startup.
    """
    src = Path(source)
    if not src.is_dir():
        return InstallResult(ok=False, error=f"source is not a directory: {source}")
    try:
        peek = _load_staged_manifest(src, action="update")
    except AppLifecycleError as exc:
        return InstallResult(ok=False, error=str(exc))
    name = name or peek.name
    if _read_installed(name) is None:
        return InstallResult(
            ok=False, name=name, error=f"app {name!r} is not installed (use install)"
        )

    staged_root = _quarantine_dir()
    staged = staged_root / f"{name}{_ROLLBACK_SUFFIX}.new"
    if staged.exists():
        shutil.rmtree(staged, ignore_errors=True)
    shutil.copytree(src, staged)

    live = app_dir(name)
    rollback = _rollback_dir(name)
    try:
        manifest = _load_staged_manifest(staged, action="update")
        if manifest.name != name:
            return InstallResult(
                ok=False,
                name=name,
                scan=None,
                error=f"manifest name {manifest.name!r} ≠ target {name!r}",
            )
        # The FULL install gate on the new content — an update is a fresh fetch of mutable
        # content, so skipping the signature or the scan here would make "update" the way
        # around both.
        granted = confirm or bool(consent)
        gate = _review(staged, origin=origin, action="update")
        if isinstance(gate, _Refused):
            _audit(
                "update",
                "refused",
                name,
                caller=caller,
                error=gate.audit_error,
                detail=_scan_detail(gate.result.scan, consent=granted) if gate.scanned else "",
            )
            return gate.result
        report = gate.report
        previous = _installed_disclosure(name)
        needed = report.verdict is Verdict.WARNING or app_disclosure.changed(
            previous, gate.disclosure
        )
        stale = bool(consent) and consent != gate.digest
        if stale or (needed and not granted):
            _audit(
                "update",
                "needs_consent",
                name,
                caller=caller,
                detail=_scan_detail(report, consent=False),
            )
            return _awaiting_consent(
                gate,
                error=_consent_error("update", report, stale=stale),
                previous=previous,
            )

        # The new version's python deps, BEFORE anything of the installed version is touched.
        # This used to run after the swap, with the rollback already dropped, so a dependency
        # failure left the new code live, installed.json un-bumped and the result `ok=False`.
        # Here a failure refuses the update and the installed version is exactly as it was.
        try:
            restart_required = _install_python_deps(manifest)
        except AppLifecycleError as exc:
            _collect_app_packages()
            _audit("update", "error", name, caller=caller, error=str(exc))
            return InstallResult(
                ok=False, name=name, scan=report, error=str(exc), log_excerpt=exc.log_excerpt
            )

        # Preserve the old app's data/ into the new tree (state survives updates).
        old_data = live / _APP_DATA_DIRNAME
        if old_data.is_dir():
            new_data = staged / _APP_DATA_DIRNAME
            if new_data.exists():
                shutil.rmtree(new_data, ignore_errors=True)
            # Same exposure as the keep-data rung, and worse consequences: this copy runs
            # against a LIVE app's data/ while it is still installed, and the swap below
            # makes this copy the surviving one (#3324).
            _copy_live_tree(old_data, new_data)
        # Preserve installed.json (gateway-written metadata, not part of the app
        # source) so the swapped-in tree keeps its install record + enabled state.
        old_meta_file = live / INSTALLED_META_FILENAME
        if old_meta_file.is_file():
            shutil.copy2(old_meta_file, staged / INSTALLED_META_FILENAME)

        # Deregister old providers before the swap so the registry never points at
        # a half-swapped dir.
        old_manifest = _manifest_of(name)
        _stop_backend(name)  # old backend must release the port before the swap
        _deregister_mcp(name)  # drop old app's MCP servers before the swap
        if old_manifest is not None and old_manifest.all_providers():
            _provider_registry().disable(name)
        # Drop the OLD app's prompt files before the swap (the new tree re-seeds
        # them) so a prompt renamed/removed between versions doesn't linger.
        if old_manifest is not None:
            _remove_app_prompts(old_manifest, name)
            # Same for the old app's skills — removed pre-swap so the new version's
            # skills re-seed cleanly through the scan (§4.1 "/update re-passes").
            _remove_app_skills(old_manifest, name)

        # ── the swap: live → .rollback, new → live ──
        if rollback.exists():
            shutil.rmtree(rollback, ignore_errors=True)
        shutil.move(str(live), str(rollback))
        try:
            shutil.move(str(staged), str(live))
            (live / _APP_DATA_DIRNAME).mkdir(parents=True, exist_ok=True)
            _run_hook(
                manifest.setup.onUpdate,
                cwd=live,
                timeout=_HOOK_DEFAULT_TIMEOUT,
                env_name="onUpdate",
            )
        except Exception as exc:  # noqa: BLE001 — ANY swap/hook failure → restore
            # Restore: drop the failed new, move .rollback back to live.
            shutil.rmtree(live, ignore_errors=True)
            if rollback.exists():
                shutil.move(str(rollback), str(live))
            if old_manifest is not None and old_manifest.all_providers():
                _provider_registry().register(old_manifest, enabled=True)
            if old_manifest is not None:
                _register_mcp(old_manifest)  # restore old app's MCP servers
                _start_backend(old_manifest)  # bring the old backend back up
                _seed_app_prompts(old_manifest, name)  # restore old app's prompts
                _seed_app_skills(old_manifest, name)  # restore old app's skills
            _collect_app_packages()  # what only the refused new version needed
            _audit("update", "error", name, caller=caller, error=str(exc))
            return InstallResult(
                ok=False,
                name=name,
                scan=report,
                error=f"update failed, rolled back: {exc}",
                # An onUpdate-hook failure carries the subprocess tail; a swap OSError
                # does not (getattr → ""). Surfaces the same Fix-with-AI seed.
                log_excerpt=getattr(exc, "log_excerpt", ""),
            )

        # Success: drop the rollback, re-register new, bump installed.json.
        shutil.rmtree(rollback, ignore_errors=True)
        # Only now: the old version's tree (and the packages only it needed) is gone.
        _collect_app_packages()
        meta = _read_installed(name)
        if meta is not None:
            meta.version = manifest.version
            meta.updatedAt = _now_iso()
            # The update re-ran the signature gate on the NEW bytes, so the tier it
            # produced is the one that now describes what is installed. Leaving the old
            # value would let a version that dropped its signature keep reading `official`.
            meta.tier = report.tier.value
            _write_installed(name, meta)
        if manifest.all_providers():
            _provider_registry().register(manifest, enabled=bool(meta and meta.enabled))
        # Re-seed the NEW app's prompts (only when it stays enabled; a disabled app
        # carries no live prompts). Non-clobbering, so a user edit survives.
        if meta is None or meta.enabled:
            _seed_app_prompts(manifest, name)
            _seed_app_skills(manifest, name)  # re-seed the new app's skills (re-scans)
            _register_mcp(manifest)  # wire the new app's MCP servers
            _start_backend(manifest)  # launch the new backend (skip if disabled)
        # Same gap as install: an update that re-passed the gate only because the user
        # confirmed a warning has to say so on its success event.
        _audit("update", "ok", name, caller=caller, detail=_scan_detail(report, consent=granted))
        return InstallResult(
            ok=True,
            name=name,
            scan=report,
            restart_required=restart_required,
            display_name=manifest.displayName or name,
            version=manifest.version,
        )
    except AppLifecycleError as exc:
        _audit("update", "error", name, caller=caller, error=str(exc))
        return InstallResult(ok=False, name=name, error=str(exc))
    finally:
        shutil.rmtree(staged, ignore_errors=True)


_SEED_MARKER_FILENAME = ".seeded-builtins.json"


def _seed_marker_path() -> Path:
    return apps_dir() / _SEED_MARKER_FILENAME


def _read_seed_marker() -> set[str]:
    """Names of builtin apps that have ALREADY been seeded (so a user uninstall
    is permanent — a seeded-then-removed app must not resurrect on restart)."""
    p = _seed_marker_path()
    if not p.is_file():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {str(n) for n in data.get("seeded", [])}
    except (json.JSONDecodeError, OSError):
        return set()


def _write_seed_marker(seeded: set[str]) -> None:
    p = _seed_marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps({"seeded": sorted(seeded)}, indent=2) + "\n")


def _resync_native_bundle(name: str, src_dir: "Path") -> list[str]:
    """Refresh an already-seeded native app's PACKAGED FILES from the wheel's copy.

    Every file the bundle ships — ``app.json`` and, under the native capability contract
    (APE-5, ``apps/native_contract.py``), the app's own Python modules and any other
    packaged asset — is owned by the packaged source, not by the user. User-owned state
    lives in exactly two places this function never touches: ``data/`` (config, which
    ``install``/``update`` also preserve) and ``installed.json`` (enabled state, origin,
    tier). Returns the app-relative paths it rewrote, so a caller can log what moved.

    🔴 THIS USED TO BE MANIFEST-ONLY, and that made a bundled app's CODE unfixable.
    ``app.json`` alone was enough while every native app was a thin manifest over a core
    factory — a core fix rode the wheel's own module and only the schema needed copying
    (bug #24: the create-task schema fix #21 never propagated). APE-5 broke that
    assumption by letting a bundle own its `provider.py`, and seeding is once-only, so a
    provider fix shipped in a new wheel reached a FRESH home and no existing one. The two
    bundles that own code (``personalclaw-ui-docs`` and ``ollama-models``) would each have
    been permanently frozen at whatever release first seeded them, with no in-band repair:
    ``POST /api/apps/{name}/update`` is the push path for an ordinary app, and a native app
    is locked against it.

    Deliberately ADD-OR-OVERWRITE, never delete. A file the wheel stopped shipping is left
    behind because nothing distinguishes it from something a user or another tool put there,
    and an orphan module is inert — the loader imports only the module the manifest's
    ``implementation`` names. Byte-compares before writing so a steady state is churn-free.
    """
    dest_dir = app_dir(name)
    if not dest_dir.is_dir():
        return []  # not installed on disk (e.g. seeded marker but dir gone) — leave it
    rewrote: list[str] = []
    try:
        sources = sorted(p for p in src_dir.rglob("*") if p.is_file())
    except OSError:
        logger.debug("Could not read the packaged bundle for %s", name, exc_info=True)
        return []
    for src in sources:
        rel = src.relative_to(src_dir)
        if "__pycache__" in rel.parts or rel.parts[0] == _APP_DATA_DIRNAME:
            continue  # build cache, and the user's own config dir
        dest = dest_dir / rel
        try:
            src_bytes = src.read_bytes()
            if dest.is_file() and dest.read_bytes() == src_bytes:
                continue  # already current — no churn
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src_bytes)
            rewrote.append(str(rel))
        except OSError:
            logger.debug("Could not re-sync %s for native app %s", rel, name, exc_info=True)
    if rewrote:
        logger.info("Re-synced native app %r from packaged source: %s", name, ", ".join(rewrote))
    return rewrote


def seed_builtin_apps() -> list[str]:
    """Seed every ``native`` manifest (from ``apps/native/``) as a real
    installed app.

    A native app is visible + configurable in the Apps UI (seeded through the
    installed-app path) but LOCKED ON — disable/uninstall/force-uninstall are
    refused (see the guards in ``disable``/``uninstall``/``force_uninstall``). On
    first run we copy its ``apps/native/<name>/`` dir into
    ``~/.personalclaw/apps/<name>/`` and write an ``installed.json`` (origin
    ``builtin``, enabled), so discovery picks it up through the installed-app path.

    Seed-ONCE by a persisted marker: a name we've seeded before is never re-seeded.
    (Because native apps can't be uninstalled, the marker just avoids clobbering a
    user's config edits on restart.) Returns the names newly seeded this run.
    Called once at startup, BEFORE extension discovery.
    """
    from personalclaw.providers.loader import BUNDLED_DIR

    if not BUNDLED_DIR.is_dir():
        return []
    seeded = _read_seed_marker()
    newly: list[str] = []
    changed = False
    for entry in sorted(BUNDLED_DIR.iterdir()):
        manifest_file = entry / APP_MANIFEST_FILENAME if entry.is_dir() else None
        if not manifest_file or not manifest_file.is_file():
            continue
        try:
            manifest = AppManifest.from_json_file(manifest_file)
        except Exception:
            logger.warning("seed: failed to parse native manifest %s", entry.name, exc_info=True)
            continue
        if not manifest.native:
            continue
        name = manifest.name
        # Seed-once for INSTALL, but re-sync the PACKAGED FILES on every boot. A native app
        # is locked (can't be disabled/uninstalled/edited by the user) and everything the
        # wheel ships for it — app.json's schema/description/capabilities AND, under APE-5,
        # the bundle's own provider module — is packaged-source-owned. User config lives
        # separately in data/config.json, which we never touch. So a fix in apps/native/
        # MUST reach an existing install; the old seed-once-skip stranded it forever (bug
        # #24: the create-task assignee/due/labels schema fix #21 never propagated), and the
        # manifest-only resync that replaced it stranded every code fix the same way.
        if name in seeded:
            _resync_native_bundle(name, entry)
            continue
        seeded.add(name)
        changed = True
        dest = app_dir(name)
        if dest.exists():
            # Already present (e.g. a prior partial run) — just mark it seeded.
            newly.append(name)
            continue
        try:
            # `__pycache__` is excluded for the same reason `_resync_native_bundle` skips it:
            # it is bytecode compiled by whoever imported the packaged module (a dev tree's
            # test run, or an earlier gateway importing it out of site-packages), not a file
            # the app ships. Copying it seeds one machine's `.pyc` into the home, where a
            # stale entry sits beside the source it no longer matches. Measured on a fresh
            # isolated home: the seeded `ollama-models` dir carried a `__pycache__` written
            # by this checkout's pytest run.
            shutil.copytree(entry, dest, ignore=shutil.ignore_patterns("__pycache__"))
            (dest / _APP_DATA_DIRNAME).mkdir(parents=True, exist_ok=True)
            meta = InstalledApp(
                name=name,
                version=manifest.version,
                displayName=manifest.displayName or name,
                enabled=True,
                installedAt=_now_iso(),
                updatedAt=_now_iso(),
                source="builtin",
                origin="builtin",
                # A bundled app IS the platform — record it explicitly so the Tools badge
                # reads `built-in` off the recorded tier rather than off an origin fallback.
                tier=TrustTier.BUILTIN.value,
            )
            _write_installed(name, meta)
            _audit("seed", "ok", name)
            newly.append(name)
        except Exception as exc:  # noqa: BLE001 — one bad seed must not block the rest
            logger.warning("seed: failed to seed builtin app %s: %s", name, exc)
            shutil.rmtree(dest, ignore_errors=True)
    if changed:
        _write_seed_marker(seeded)
    # The seeding above is FORWARD-ONLY: it walks what the wheel still ships. The reverse
    # direction is what `retire_orphaned_builtins` supplies — a name this home seeded whose
    # packaged source is gone. Ordered after the forward pass so `present` is the full set.
    retire_orphaned_builtins(seeded, _bundled_native_names())

    return newly


def _bundled_native_names() -> set[str]:
    """Every name the wheel currently ships as a NATIVE app."""
    from personalclaw.providers.loader import BUNDLED_DIR

    names: set[str] = set()
    if not BUNDLED_DIR.is_dir():
        return names
    for entry in sorted(BUNDLED_DIR.iterdir()):
        manifest_file = entry / APP_MANIFEST_FILENAME if entry.is_dir() else None
        if not manifest_file or not manifest_file.is_file():
            continue
        try:
            manifest = AppManifest.from_json_file(manifest_file)
        except Exception:
            continue
        if manifest.native:
            names.add(manifest.name)
    return names


def _core_factory_is_gone(name: str) -> bool:
    """True when this app's provider points at a CORE factory that no longer exists.

    A native app is a thin manifest over an implementation that lives in core, so retiring the
    core factory retires the app — and that is exactly the state this detects. Deliberately
    limited to `personalclaw.*` modules: a de-cored app's implementation lives in its own files,
    and importing THOSE at seed time would execute app code during boot for no reason. An
    unreadable or non-core implementation therefore answers False (not a dead core factory),
    which routes the app down the gentler de-core branch.
    """
    import importlib

    manifest = _manifest_of(name)
    provider = getattr(manifest, "provider", None) if manifest is not None else None
    impl = str(getattr(provider, "implementation", "") or "")
    module_path, _, func_name = impl.rpartition(":")
    if not module_path or not func_name or not module_path.startswith("personalclaw."):
        return False
    try:
        module = importlib.import_module(module_path)
    except Exception:
        # The module itself is gone — the factory certainly is.
        return True
    return not hasattr(module, func_name)


def _holds_user_data(name: str) -> bool:
    """True when the app's `data/` directory has anything in it."""
    data_dir = app_dir(name) / _APP_DATA_DIRNAME
    try:
        return data_dir.is_dir() and any(data_dir.iterdir())
    except OSError:
        # Unreadable — assume it holds something rather than deleting it.
        return True


def _clear_native_flag(name: str) -> None:
    """Drop `native: true` from an installed app's manifest, in place.

    The other half of unlocking a retired built-in: `_is_native()` reads the manifest flag first,
    so a retired app that still declares itself native stays locked however its origin reads.
    Best-effort — an unwritable or unparseable manifest leaves the origin change standing rather
    than aborting the sweep.
    """
    path = app_dir(name) / APP_MANIFEST_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("native"):
            return
        data["native"] = False
        atomic_write(path, json.dumps(data, indent=2) + "\n")
    except Exception:
        logger.debug("could not clear the native flag on %s", name, exc_info=True)


def retire_orphaned_builtins(seeded: set[str], present: set[str]) -> list[str]:
    """Reconcile seed-marker names whose packaged native source is gone. Returns what changed.

    🔴 THREE STATES USED TO DISAGREE (issues 334, 368). The ScheduleService retirement deleted
    `create_schedule_provider` and stopped bundling `personalclaw-schedule-tools`, but nothing
    removed it from an existing install. Measured on a home upgraded across six `main` SHAs: the
    app stayed installed and enabled, `_is_native()` locked it against disable AND uninstall
    (`origin="builtin"`), `GET /api/apps` rendered it beside 28 working built-ins with an
    "Installed" badge and no error, `DELETE` answered `404 not installed` while the list said it
    was, and every gateway boot logged an `AttributeError` from `load_factory`. The only escape
    was hand-editing the home.

    The precedent was already here as a hardcoded one-shot for `ollama-models`, which this
    replaces: that app is one instance of the general rule, so a second retirement would have
    needed a second one-shot. Nothing is left beside this — the block is deleted, not bypassed.

    Two outcomes, decided by whether the app can still run:

    * **Dead** — its provider named a core factory that no longer exists, so the app cannot work
      and the platform installed it without asking. Unlock it, disable it, and remove the
      directory — but ONLY when it holds no user data. An app whose `data/` has contents is
      unlocked and disabled and left in place: a retirement must not delete something the user
      may want, and an unlocked app is removable in one click.
    * **De-cored** — the packaged source is gone but the implementation is not a dead core
      factory (it moved into the app's own files, `ollama-models`' history). Unlock only; it
      still works, so it keeps running as an ordinary user-manageable app.

    Either way the name leaves the marker, so this is idempotent and a re-seed cannot resurrect
    a retired built-in.
    """
    orphans = sorted(seeded - present)
    if not orphans:
        return []
    changed: list[str] = []
    for name in orphans:
        meta = _read_installed(name)
        dead = _core_factory_is_gone(name)
        if meta is not None:
            # Unlocking is the half BOTH outcomes need, and it takes BOTH writes: `_is_native()`
            # answers True on the manifest's `native` flag OR the `builtin` origin, so clearing one
            # leaves the app locked. Measured while writing this — `origin="local"` alone kept
            # `_is_native()` True, because the INSTALLED app.json still claimed native.
            meta.origin = "local"
            if dead:
                meta.enabled = False
            meta.updatedAt = _now_iso()
            _write_installed(name, meta)
            _clear_native_flag(name)
        if dead and not _holds_user_data(name):
            shutil.rmtree(app_dir(name), ignore_errors=True)
            _audit("retire", "ok", name)
            logger.info("retired de-bundled builtin app %s (its core factory is gone)", name)
        elif dead:
            logger.info(
                "de-bundled builtin app %s is disabled and unlocked; its data/ is kept for you "
                "to remove",
                name,
            )
        else:
            logger.info("unlocked de-cored builtin app %s (builtin→local)", name)
        seeded.discard(name)
        changed.append(name)
    _write_seed_marker(seeded)
    return changed


def start_enabled_app_backends() -> list[str]:
    """Launch the backend subprocess for every enabled installed app that
    declares one (called once at gateway startup). Backends are subprocesses —
    they don't survive a gateway restart, so an enabled app would otherwise show
    'backend down' until manually re-enabled. Returns the names started.

    Gated by ``PERSONALCLAW_SKIP_APP_BACKENDS`` (set by the test suite): a test
    that exercises the extension loader must not spawn — or reap — the real
    user's app backends."""
    import os

    from personalclaw.apps.manager import list_apps

    if os.environ.get("PERSONALCLAW_SKIP_APP_BACKENDS"):
        return []

    started: list[str] = []
    for app_info in list_apps():
        if not app_info.get("enabled", False):
            continue
        manifest_data = app_info.get("manifest", {})
        if not manifest_data.get("backend", {}).get("entryPoint"):
            continue
        name = app_info.get("name", "")
        try:
            manifest = AppManifest.from_dict(manifest_data)
            # Core-version gate (#1778) — the boot-load path for an app that is ALREADY
            # installed and enabled. Reached after a core downgrade (or an install that
            # predates this gate): spawning the backend would produce exactly the
            # arbitrary runtime failure inside the app that the gate exists to prevent,
            # so it is skipped with a legible reason instead.
            compat = manifest.core_compatibility()
            if not compat.admits:
                logger.warning("app %s: backend not started — %s", name, compat.reason)
                continue
            from personalclaw.apps.backend_runtime import get_backend_supervisor

            sup = get_backend_supervisor()
            # Reap any orphans a prior gateway left running for this app (crash /
            # kill -9 / force-exit) BEFORE spawning a fresh one — otherwise each
            # ungraceful restart stacks another backend (reparented to init).
            entry = (app_dir(name) / manifest.backend.entryPoint).resolve()
            sup.reap_orphans(name, entry)
            if sup.start(manifest) is not None:
                started.append(name)
        except Exception:
            logger.warning("app %s: startup backend launch failed", name, exc_info=True)
    return started


def recover_interrupted_updates() -> list[str]:
    """Reconcile leftover ``.{name}.rollback`` dirs from an update that crashed
    mid-swap (called at startup). If ``live`` is missing/empty, restore from the
    rollback; otherwise the swap completed and the rollback is stale — drop it.
    Returns the names recovered."""
    recovered: list[str] = []
    root = apps_dir()
    if not root.is_dir():
        return recovered
    for entry in root.iterdir():
        if not (
            entry.is_dir() and entry.name.startswith(".") and entry.name.endswith(_ROLLBACK_SUFFIX)
        ):
            continue
        name = entry.name[1 : -len(_ROLLBACK_SUFFIX)]
        live = app_dir(name)
        try:
            if not live.exists() or not any(live.iterdir()):
                # Crash between "move live→rollback" and "move new→live": restore.
                if live.exists():
                    shutil.rmtree(live, ignore_errors=True)
                shutil.move(str(entry), str(live))
                recovered.append(name)
                _audit("update_recover", "restored", name)
            else:
                shutil.rmtree(entry, ignore_errors=True)  # stale rollback
                _audit("update_recover", "dropped_stale", name)
        except OSError:
            logger.warning("failed to reconcile rollback dir %s", entry, exc_info=True)
    return recovered


def repair_app_packages() -> list[str]:
    """Reinstall whatever the installed apps' Python packages are missing (called at boot).

    ``<home>/app-python`` is a function of the installed apps' manifests AND of the running
    interpreter, so an image upgrade can invalidate it without anything being uninstalled: a new
    Python finds no packages in its own layout (``lib/python3.14`` after ``3.13``), and a core
    dependency an app relied on can be dropped or moved. A restored snapshot carries the apps
    but — deliberately — not their packages. So this reinstalls what is missing in one pip run
    over every installed app, collects what nothing needs any more, and re-enables the providers
    of the apps it repaired: their import failed during discovery, and it now succeeds in place.

    Blocking (it can run pip for minutes), so the gateway calls it on a background thread; with
    nothing missing — every boot of an unchanged image — it only collects, which is cheap.
    Returns the names of the apps it repaired.
    """
    from personalclaw.apps import app_python

    broken = app_python.broken_apps()
    if not broken:
        _collect_app_packages()
        return []
    logger.warning(
        "app packages missing, reinstalling: %s",
        {declared.name: missing for declared, missing in broken},
    )
    try:
        app_python.install_everything()
    except app_python.PackageInstallError as exc:
        for declared, missing in broken:
            reason = (
                f"{declared.label}'s Python packages are missing (the gateway's Python or its own "
                f"packages changed since it was installed), and reinstalling them failed: {exc}"
            )
            logger.error("app %s: %s (missing: %s)", declared.name, reason, ", ".join(missing))
            _mark_provider_error(declared.name, reason)
            _audit("repair_packages", "error", declared.name, error=str(exc))
        _collect_app_packages()
        return []
    _collect_app_packages()
    still_broken = {declared.name for declared, _ in app_python.broken_apps()}
    repaired = [declared.name for declared, _ in broken if declared.name not in still_broken]
    for name in repaired:
        meta = _read_installed(name)
        manifest = _manifest_of(name)
        if meta is not None and meta.enabled and manifest is not None and manifest.all_providers():
            _provider_registry().enable(name)
        _audit("repair_packages", "ok", name)
    return repaired


def _mark_provider_error(name: str, message: str) -> None:
    """Replace a not-enabled provider's raw import error with the sentence that explains it."""
    try:
        primary = _provider_registry().get(name)
        for record in primary.chain() if primary is not None else []:
            if not record.enabled:
                record.error = message
    except Exception:  # noqa: BLE001 — a status annotation must not break the repair
        logger.debug("app %s: provider error annotation failed", name, exc_info=True)


def enable(name: str, *, caller: str = "app_manager") -> bool:
    meta = _read_installed(name)
    if meta is None:
        return False
    manifest = _manifest_of(name)
    if manifest is not None:
        # Core-version gate (#1778). Install-time refusal alone is not enough: the core
        # can be DOWNGRADED under an app that was installed against a newer one, and the
        # app is then already on disk. Checked BEFORE onEnable, so no third-party hook
        # runs for an app this core cannot host.
        compat = manifest.core_compatibility()
        if not compat.admits:
            logger.warning("app %s: enable refused — %s", name, compat.reason)
            _audit("enable", "refused_core_version", name, caller=caller, error=compat.reason)
            return False
        if compat.reason:
            logger.warning("app %s: %s", name, compat.reason)
        try:
            _run_hook(
                manifest.setup.onEnable,
                cwd=app_dir(name),
                timeout=manifest.setup.onEnableTimeout,
                env_name="onEnable",
            )
        except AppLifecycleError as exc:
            _audit("enable", "error", name, caller=caller, error=str(exc))
            return False
    meta.enabled = True
    meta.updatedAt = _now_iso()
    _write_installed(name, meta)
    if manifest is not None and manifest.all_providers():
        _provider_registry().enable(name)
    if manifest is not None:
        _seed_app_prompts(manifest, name)  # the app OWNS its prompts; seed on enable
        _seed_app_skills(manifest, name, origin=meta.origin)  # + its skills (via the gate)
        _register_mcp(manifest)
        _register_proposal_kinds(manifest, name)  # INU-7: declared → registered kind pair
        _start_backend(manifest)
    _audit("enable", "ok", name, caller=caller)
    return True


def _is_native(name: str) -> bool:
    """A native app is locked on — disable/uninstall/force-uninstall refuse.
    Identified by its manifest ``native`` flag (belt-and-suspenders: also the
    ``builtin`` origin, since only native apps seed with that origin)."""
    manifest = _manifest_of(name)
    if manifest is not None and manifest.native:
        return True
    meta = _read_installed(name)
    return meta is not None and getattr(meta, "origin", "") == "builtin"


def disable(name: str, *, caller: str = "app_manager") -> bool:
    meta = _read_installed(name)
    if meta is None:
        return False
    if _is_native(name):
        logger.info("app %s is native (locked) — disable refused", name)
        _audit("disable", "refused_native", name, caller=caller)
        return False
    manifest = _manifest_of(name)
    _stop_backend(name)
    _stop_worker(name)
    _deregister_mcp(name)
    if manifest is not None and manifest.all_providers():
        _provider_registry().disable(name)
    if manifest is not None:
        _remove_app_prompts(manifest, name)  # drop the app's own seeded prompts
        _remove_app_skills(manifest, name)  # drop the app's own seeded skills
        _deregister_proposal_kinds(manifest, name)  # INU-7: no phantom kind survives
        try:
            _run_hook(
                manifest.setup.onDisable,
                cwd=app_dir(name),
                timeout=manifest.setup.onDisableTimeout,
                env_name="onDisable",
            )
        except AppLifecycleError as exc:
            # Already deregistered; log but don't fail the disable (it IS disabled).
            logger.warning("app %s onDisable hook failed: %s", name, exc)
    meta.enabled = False
    meta.updatedAt = _now_iso()
    _write_installed(name, meta)
    _audit("disable", "ok", name, caller=caller)
    return True


def preview_uninstall(name: str) -> list:
    """Read-only: classify each shared dependency this app declares as
    removable / shared / userInstalled (A3), for the uninstall-confirm UI. Empty
    list if the app or its manifest is absent."""
    manifest = _manifest_of(name)
    if manifest is None:
        return []
    from personalclaw.apps import dependency_ledger

    return dependency_ledger.classify_uninstall(manifest)


def describe_app_data(name: str) -> dict[str, Any]:
    """Read-only: what this app's ``data/`` holds, for the removal-confirm UI.

    ``{"present": bool, "entries": int, "path": str}``. ``present`` is whether the
    directory EXISTS — an app with an empty ``data/`` reports ``present=True,
    entries=0``, which is not the same claim as ``present=False`` and must not be
    rendered as one: the first means "you have a data dir and it happens to be empty",
    the second means "this app keeps no data". The confirm dialog needs to promise a
    different thing in each case, so both facts are reported rather than one truthiness.

    ``path`` is where a keep-data uninstall would park it, so the dialog can tell the
    user where their data goes — the recovery information a destructive-action screen
    owes them.

    ``unconsumed`` is the same list :func:`_unconsumed_data_copies` gives the keep-data
    rung: earlier copies of this app's ``data/`` still on disk that nothing consumed. Non-
    empty means a keep-data uninstall will REFUSE (#2585), so the dialog states that and
    where the copies are instead of letting the user press a button whose only feedback is
    a ``False`` the HTTP layer renders as "not installed".

    ``secrets`` counts the credentials this app keeps in the credential store (its settings'
    tokens, its instances' keys). Both removal rungs delete them, the keep-data one included,
    so the dialog can say so before the click. A count of key NAMES — no value is read.
    """
    from personalclaw.config import secret_refs

    try:
        data = app_dir(name) / _APP_DATA_DIRNAME
        parked = str(_preserved_data_dir(name))
        unconsumed = [str(p) for p in _unconsumed_data_copies(name)]
    except ValueError:
        return {"present": False, "entries": 0, "path": "", "unconsumed": [], "secrets": 0}
    present = data.is_dir()
    return {
        "present": present,
        "entries": _dir_entry_count(data) if present else 0,
        "path": parked,
        "unconsumed": unconsumed,
        "secrets": secret_refs.count_owned(secret_refs.app_owned_prefixes(name)),
    }


def uninstall(name: str, *, caller: str = "app_manager") -> bool:
    """Uninstall = DEACTIVATE (keep files). An app the user 'uninstalls' is turned
    OFF, not deleted: its providers deregister, backend stops, MCP servers drop,
    and ``installed.json.enabled`` flips to false — but the files stay on disk so
    it can be re-activated instantly (no re-fetch) and its data/ is preserved.

    Removing the files while KEEPING ``data/`` is :func:`uninstall_keep_data`; total
    removal including ``data/`` is :func:`force_uninstall`. This mirrors how a provider
    app's install IS its on-switch: uninstall is the off-switch, uninstall_keep_data is
    the remove, force-uninstall is the eradicate.

    Kept as DEACTIVATE deliberately (issue #2541): repointing this name at the new
    file-removing rung would silently convert every existing caller — the plain
    ``DELETE /api/apps/{name}`` among them — from "turns the app off" to "deletes the
    app", which is not a change a caller can consent to by not being edited."""
    meta = _read_installed(name)
    if meta is None:
        return False
    if _is_native(name):
        logger.info("app %s is native (locked) — uninstall refused", name)
        _audit("uninstall", "refused_native", name, caller=caller)
        return False
    # Deactivate via the same teardown as disable, but audit it as an uninstall.
    ok = disable(name, caller=caller)
    if ok:
        _audit("uninstall", "ok", name, caller=caller)
    return ok


def uninstall_keep_data(name: str, *, caller: str = "app_manager") -> bool:
    """Remove the app's FILES and KEEP its ``data/`` — the middle lifecycle rung.

    The app is gone from every surface; the data the user made with it is parked at
    ``apps/.{name}.data``, and a later :func:`install` of the same name puts it back.
    Closes the gap in issue #2541: before this, the ladder was "don't remove it"
    (:func:`uninstall`) and "remove everything" (:func:`force_uninstall`), so a user
    who wanted the app gone but their notes kept had no path at all.

    Two pieces of machinery are REUSED rather than reimplemented, because a second
    copy of either is a second thing that can drift:

    * the preserving copy is the one :func:`update` already performs — ``live/data``
      copied out before the tree it lives in is replaced — staged through the same
      quarantine dir install/update stage through;
    * the removal is :func:`force_uninstall` itself, unchanged: its hooks, its
      deregistration, its dependency-ledger accounting, its ``rmtree``.

    FAIL-CLOSED on preservation. If ``data/`` cannot be copied out, NOTHING is
    removed. An operation whose entire promise is "your data survives this" must not
    proceed to the delete having failed to keep that promise.

    And past that point — the removal has happened and the PARK then fails — the staged
    copy is LEFT in quarantine and the audit line names it, because by then it is the
    only copy there is. ``False`` with ``data=park_failed staged_copy=…`` means "the app
    is gone, your data is at that path"; it never means the data is gone (#2574).

    FAIL-CLOSED on an EARLIER copy, too. Both of this rung's own paths can already hold a
    copy of the user's data from a previous run that nothing consumed — a park that failed
    leaves the stage, a restore that failed leaves the park — and every way of proceeding
    over one destroys it. So :func:`_unconsumed_data_copies` is consulted FIRST, before
    anything is copied or removed, and a non-empty answer refuses with
    ``data=unconsumed_copy <paths>`` (#2585). This rung never resolves that itself: the
    "cleanup" for each is a delete in a failure path, which is how #2574 happened.

    EVERY ``False`` NAMES ITSELF. This rung returns a bare ``bool``, so the log line is the
    only place a caller — a user reading ``gateway.log``, or CI reading a red — can learn
    WHY an app it asked to remove is still installed. Two branches used to answer with
    nothing at all (the preservation copy failing, and a name that cannot hold a parked
    copy), which is how a real failure of this rung reached main as ``assert False is True``
    with no cause anywhere in the output. So each refusal/error path of an INSTALLED app
    emits a ``logger.error`` naming the app and the reason, and
    ``tests/test_app_uninstall_preserves_data.py`` holds that as an invariant over the
    branches rather than per-branch — paired with the success path, which must stay silent.

    Because that refusal comes first, the park below needs no ``rmtree`` of its own
    destination — the destination is provably absent — so it is a single
    :meth:`~pathlib.Path.rename`. Both paths live under ``apps/``, always the same
    filesystem, so that rename is ATOMIC: there is no cross-device ``copytree`` fallback
    to fail halfway and no ``shutil.move`` destination-is-a-directory case to nest the
    stage inside an older park. A parked copy is now complete by CONSTRUCTION, which is
    the guarantee :func:`_restore_preserved_data` relies on when it treats one as
    authoritative — previously that guarantee lived only in the order these two functions
    happened to be called in.
    """
    meta = _read_installed(name)
    if meta is None:
        return False
    if _is_native(name):
        logger.info("app %s is native (locked) — uninstall refused", name)
        _audit("uninstall_keep_data", "refused_native", name, caller=caller)
        return False
    # Validate the name ONCE, up front, before anything derives a path from it: both the
    # quarantine stage and the parked dir embed it as a path segment, and both are
    # rmtree/move targets. `list_apps` iterates real on-disk dir names, so a name that is
    # not a mintable app id can reach here — it simply cannot hold a parked copy, so this
    # rung refuses rather than guessing a directory for the user's data. Such an app is
    # still removable through `force_uninstall`.
    try:
        _validate_app_name(name)
    except ValueError as exc:
        logger.error(
            "app %s: this name cannot hold a parked copy of data/, so the keep-data "
            "uninstall is refused; nothing was removed and force_uninstall still works: %s",
            name,
            exc,
        )
        _audit("uninstall_keep_data", "refused", name, caller=caller, error=str(exc))
        return False

    # FAIL-CLOSED on an earlier copy, BEFORE anything is copied or removed (#2585). One
    # predicate answers it for both of this rung's paths; see
    # `_unconsumed_data_copies` for what each leftover means and why proceeding over it
    # destroys it. Nothing is deleted or moved aside here: leaving both copies where they
    # are is the only option that cannot lose the user's work, and the audit line plus a
    # `logger.error` name the paths so the refusal is recovery information rather than a
    # dead end. The way out is by hand, deliberately: this rung is only reachable while the
    # app IS installed, so `install` (which refuses an installed app) cannot be the
    # recovery route — the user moves the copy aside, or removes it, or presses
    # force_uninstall, which discards a park on purpose.
    leftover = _unconsumed_data_copies(name)
    if leftover:
        paths = " ".join(str(p) for p in leftover)
        logger.error(
            "app %s: an earlier unconsumed copy of data/ is still on disk (%s); "
            "keep-data uninstall refused so it cannot be overwritten",
            name,
            paths,
        )
        _audit(
            "uninstall_keep_data",
            "refused",
            name,
            caller=caller,
            error=(
                "an earlier copy of this app's data/ is still on disk and was never "
                f"consumed: {paths}. Nothing was removed. Move it aside (or remove it, if "
                "you have what you need from it) and retry; force_uninstall deletes a "
                "parked copy deliberately."
            ),
            detail=f"data=unconsumed_copy {paths}",
        )
        return False

    live_data = app_dir(name) / _APP_DATA_DIRNAME
    # ABSENT vs EMPTY, kept apart on purpose. No data/ at all ⇒ nothing is staged and
    # no parked dir is created, so the next install starts clean. An EMPTY data/ IS
    # staged (as an empty dir), because "the app had a data dir and it held nothing"
    # is a different fact from "the app never had one", and the next install has to
    # reproduce the one that actually happened rather than a merged approximation.
    had_data = live_data.is_dir()
    staged = _data_stage_dir(name)
    # No pre-emptive `rmtree(staged)`: the refusal above proved nothing is there. Which
    # also means every `rmtree(staged)` below acts on a stage THIS call created, while
    # `live_data` is still on disk — never on one a previous call left as a last copy.
    try:
        if had_data:
            # `_copy_live_tree`, not a bare `copytree`: `live_data` belongs to an app that
            # is still installed and may still be writing (#3324).
            _copy_live_tree(live_data, staged)
    except OSError as exc:
        shutil.rmtree(staged, ignore_errors=True)
        # LOUD, not audit-only. This was the ladder's ONE invisible failure: it returned
        # `False` having written nothing to any log, while every sibling below names itself
        # (`data=unconsumed_copy` and `data=park_failed` both `logger.error`). So the one
        # branch that fires on an unexplained filesystem fault was also the one branch that
        # destroyed the evidence for it — measured on main's `Full` run 35248405420
        # (macos-latest, py3.13), where the whole record of a failed keep-data uninstall was
        # `assert False is True` with no cause anywhere in the output.
        #
        # `errno` rides both the log line and the audit detail because it is the WHOLE
        # diagnosis here: ENOSPC ("free some disk and retry"), EMFILE/EACCES/EIO and a
        # metadata-only `shutil.Error` are four different next actions, and `data=
        # preserve_failed` alone cannot tell them apart. `shutil.copytree` aggregates its
        # per-entry failures into a `shutil.Error` that carries no errno of its own, so
        # `none` is a real, distinct answer: read the entry list in the message.
        code = getattr(exc, "errno", None)
        logger.error(
            "app %s: data/ could not be copied out (%s -> %s), so NOTHING was removed and "
            "the app is intact; errno=%s: %s",
            name,
            live_data,
            staged,
            code,
            exc,
            exc_info=True,
        )
        _audit(
            "uninstall_keep_data",
            "error",
            name,
            caller=caller,
            error=f"could not preserve data/, so nothing was removed: {exc}",
            detail=f"data=preserve_failed errno={code if code is not None else 'none'}",
        )
        return False

    # Staged in QUARANTINE, not in place, because the removal below also drops a stale
    # parked copy for this name — the fresh copy has to wait somewhere that step cannot
    # reach.
    fact = _data_fact("data", staged if had_data else None)
    try:
        if not force_uninstall(name, caller=caller):
            # Nothing was removed, so `live_data` is still there and the stage is a
            # redundant duplicate of it: the one failure below that may still GC.
            shutil.rmtree(staged, ignore_errors=True)
            logger.error(
                "app %s: the removal this rung delegates to refused, so nothing was "
                "deleted and data/ is untouched",
                name,
            )
            _audit(
                "uninstall_keep_data",
                "error",
                name,
                caller=caller,
                error="removal refused; nothing was deleted and data/ is untouched",
                detail=fact,
            )
            return False
        if had_data:
            target = _preserved_data_dir(name)
            # ATOMIC park, and the ONLY write to `target` in the codebase (#2585). The
            # refusal at the top proved `target` does not exist, so there is no `rmtree`
            # of it to swallow an error, and both paths are under `apps/` — one
            # filesystem — so this rename either happens or does not. What that buys:
            # `target` can never hold a HALF copy (the old `shutil.move` fell back to
            # copytree + `rmtree(src)` on any `os.rename` failure, and a copytree that
            # cannot read one file copies the rest and raises, leaving a partial park
            # beside an intact stage), and can never hold the stage NESTED inside an
            # older park (`shutil.move` moves INTO an existing destination directory).
            #
            # NO cleanup of `staged` after this line, on either outcome (#2574).
            #
            # Success needs none: the rename consumed it.
            #
            # Failure must not have any: `force_uninstall` above has already removed the
            # app tree and the `data/` inside it, so `staged` is at that moment the ONLY
            # copy of the user's data on the machine. A `finally: rmtree(staged)` reads
            # as harmless GC and on this branch deletes the last copy — fail-OPEN on the
            # one branch this rung's whole promise is about. Left on disk instead, the
            # way `_restore_preserved_data` leaves a park it could not restore — and the
            # next attempt at this rung now REFUSES rather than sweeping it (#2585).
            staged.rename(target)
    except (OSError, ValueError) as exc:
        # The app is gone and the data is not parked, so name the surviving copy: a fact
        # the user cannot act on is a diagnosis, not recovery information.
        logger.error("app %s: data/ could not be parked; the copy is at %s", name, staged)
        _audit(
            "uninstall_keep_data",
            "error",
            name,
            caller=caller,
            error=f"app removed but data/ could not be parked: {exc}; the copy is at {staged}",
            detail=f"data=park_failed staged_copy={staged}",
        )
        return False

    _audit("uninstall_keep_data", "ok", name, caller=caller, detail=fact)
    return True


def force_uninstall(name: str, *, caller: str = "app_manager") -> bool:
    """Run onUninstall → deregister → consult the dependency ledger → REMOVE FILES.

    The hidden, destructive path (Advanced → Force uninstall): the app's own files
    are removed from disk. Shared dependencies (still needed by another installed
    app) and user-installed ones are LEFT; only deps this app solely owned are
    eligible for removal (the caller/marketplace does the actual dep removal — the
    ledger decides *which*). A force-removed default-seeded app stays gone (the
    seed-once marker is not cleared).

    The app's Python packages go with it: once its tree is removed, every package in
    ``<home>/app-python`` that no remaining app needs is collected. Both removal rungs
    arrive here — :func:`uninstall_keep_data` delegates its removal to this function."""
    meta = _read_installed(name)
    if meta is None:
        return False
    if _is_native(name):
        logger.info("app %s is native (locked) — force-uninstall refused", name)
        _audit("force_uninstall", "refused_native", name, caller=caller)
        return False
    manifest = _manifest_of(name)
    _stop_backend(name)
    _stop_worker(name)
    _deregister_mcp(name)
    if manifest is not None:
        _remove_app_prompts(manifest, name)  # drop the app's own seeded prompts
        _remove_app_skills(manifest, name)  # drop the app's own seeded skills
        _deregister_proposal_kinds(manifest, name)  # INU-7: no phantom kind survives
        try:
            _run_hook(
                manifest.setup.onUninstall,
                cwd=app_dir(name),
                timeout=_HOOK_DEFAULT_TIMEOUT,
                env_name="onUninstall",
            )
        except AppLifecycleError as exc:
            logger.warning("app %s onUninstall hook failed (removing anyway): %s", name, exc)
    if manifest is not None and manifest.all_providers():
        # Forget it entirely (not just disable) so it doesn't linger as a disabled
        # ghost in the providers list until the next restart.
        _provider_registry().deregister(name)
    # Consult + update the dependency ledger BEFORE removing files (so 'removable'
    # reflects this app's departure). Shared/userInstalled deps are kept.
    if manifest is not None:
        try:
            from personalclaw.apps import dependency_ledger

            removed = dependency_ledger.record_uninstall(manifest)
            kept = [
                c.key
                for c in removed
                if c.disposition is not dependency_ledger.DepDisposition.REMOVABLE
            ]
            if kept:
                logger.info("app %s force-uninstall: keeping shared/user deps %s", name, kept)
        except Exception:
            logger.debug("app %s: dependency-ledger uninstall failed", name, exc_info=True)
    shutil.rmtree(app_dir(name), ignore_errors=True)
    # "Force uninstall removes everything" has to include a data/ copy that an earlier
    # keep-data uninstall parked under this name (apps/.{name}.data). Leaving one behind
    # would let the next install resurrect the very data this button exists to destroy.
    # Unchanged for every caller: this path already deletes, and it now deletes the one
    # thing it could previously miss.
    _discard_preserved_data(name)
    _collect_app_packages()
    # The app's secrets — its settings' tokens and its instances' keys — live in the
    # credential store, not in data/, so removing files alone would leave them behind with
    # nothing referencing them. Both removal rungs end here (the keep-data rung delegates its
    # removal to this function), so a keep-data uninstall keeps the user's data and still
    # drops the credentials: its parked settings hold references that resolve to "unset",
    # and a reinstall asks for the tokens again. Deactivate (`uninstall`) keeps them, like
    # it keeps every file.
    from personalclaw.config import secret_refs

    secrets_removed = secret_refs.purge(secret_refs.app_owned_prefixes(name))
    _audit(
        "force_uninstall", "ok", name, caller=caller, detail=f"secrets_removed={secrets_removed}"
    )
    return True


def _manifest_of(name: str) -> AppManifest | None:
    # A path-escaping name (app_dir now rejects '../', '/etc', … — #44) is simply
    # "not an installed app": return None so callers/routes 404 cleanly rather than
    # surfacing the guard's ValueError as an unhandled 500.
    try:
        mpath = app_dir(name) / APP_MANIFEST_FILENAME
    except ValueError:
        return None
    if not mpath.is_file():
        return None
    try:
        return AppManifest.from_json_file(mpath)
    except Exception:  # noqa: BLE001
        logger.debug("app %s: manifest load failed", name, exc_info=True)
        return None
