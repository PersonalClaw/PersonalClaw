"""The BYO runner data catalog — definitions, measured health evidence, adapter verification.

EXECUTION-ISOLATION §3.1/§3.2 (EI-5). A *runner* is an external agent CLI the ACP
layer can drive (Claude Code, Codex, Gemini CLI, Kiro, or one a user brings). Runner
*registration* is unchanged — an app bundle still publishes an ``acp:<cli>``
:class:`~personalclaw.llm.registry.ProviderEntry` through
:mod:`personalclaw.acp_bundles._register`. What this module adds is the **catalog**:
the data a runner is described BY, and the evidence a runner is trusted ON.

Three parts, deliberately separate:

1. **Definitions** — declarative rows (:class:`RunnerDefinition`): which binary names
   to look for, which env var overrides resolution, which npm adapter package (if any)
   carries the ACP shim, which dialect the transport speaks. The shipped rows live in
   the sibling ``runner_catalog.json`` **data** file, not in code, and a user drops
   their own into ``$PERSONALCLAW_HOME/runners/<id>.json`` (same schema; same id
   replaces a shipped row). This module carries no vendor branching — every vendor
   value is a field read from data, which is what keeps the catalog a *catalog* and
   not a second registration path.

2. **Health evidence** (:class:`HealthEvidence`) — persisted per runner in the
   ``agent-metadata/<id>.runner.json`` sidecar. Every field is MEASURED or ``None``:
   ``latency_ms`` is a real elapsed measurement of a real probe, ``version`` is parsed
   out of the CLI's own output, and ``error`` is the probe's OWN text, verbatim. A
   value that was not measured stays ``None`` so the surface can say "unknown" — a
   fabricated ``0`` reads as an answer and is worse than an absent one.

3. **Adapter verification** (:func:`verify_adapter`) — provenance for the npm ACP
   adapter a runner launches through. Verified means: the adapter resolves to a real
   on-disk binary AND the PersonalClaw-managed install prefix has a recorded
   provenance entry whose integrity still matches what npm reports on disk. The
   ``npx -y`` last resort can neither be pinned nor checksummed, so it is never
   verified. :func:`guard_unattended_spawn` is the enforcement point: with
   ``agents.unattended_requires_verified_adapter`` on, an unattended spawn against
   anything but a verified adapter is refused (fail closed — an uncataloged runner is
   unverifiable, so it is refused too). "Unattended" is not a caller's self-report:
   :mod:`personalclaw.session` derives it from the session key through
   :func:`personalclaw.guardrails.policy.is_unattended_session`, the same vocabulary the
   guardrail layer resolves safety profiles with, so cron fires, loop-cycle workers, the
   background/heartbeat key, inbox/side sweeps, channel deliveries and sessionless
   trigger dispatches are all covered without each one opting in.

Probe posture: the health probe runs ``<bin> --version`` (or the row's
``version_args``) and nothing else. It never opens a session, never passes a prompt,
and writes nothing outside the sidecar — so probing the catalog cannot touch a
workspace.

Shipped-data honesty note: ``gemini-cli``'s ``acp_args`` (``--experimental-acp``) and
``kiro``'s bin names are declared from the vendors' documented flags, not measured
here; the health probe below only proves the binary's ``--version`` behaviour. The
adapter ``version``/``integrity`` pins ship EMPTY on purpose — inventing digests we
have not verified would make :func:`verify_adapter` lie. Provenance is instead
recorded at provision time (trust-on-provision) and re-checked on every read, which
catches an adapter that changed underneath an install.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "AdapterPin",
    "AdapterVerification",
    "HealthEvidence",
    "RunnerDefinition",
    "UnverifiedAdapterError",
    "adapter_lock_path",
    "catalog",
    "evidence_is_stale",
    "guard_unattended_spawn",
    "health_check_interval_secs",
    "load_evidence",
    "probe_runner",
    "record_capabilities",
    "record_provenance",
    "runner_rows",
    "runtime_id_for_agent",
    "verify_adapter",
]

_BUILTIN_CATALOG = Path(__file__).resolve().parent / "runner_catalog.json"

#: The user-drop directory for BYO runner definitions (under PERSONALCLAW_HOME).
USER_CATALOG_DIR_NAME = "runners"

#: Provenance ledger inside the managed adapter prefix. Mirrors the skills
#: marketplace's ``.pclaw-lock.json`` per-artifact digest precedent.
ADAPTER_LOCK_NAME = ".pclaw-lock.json"

_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SEMVER_RE = re.compile(r"\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?")

#: Wall-clock budget for one ``--version`` probe. A CLI that cannot print its own
#: version inside this is reported as a timeout with its own text, not guessed at.
PROBE_TIMEOUT_SECS = 12.0


class UnverifiedAdapterError(RuntimeError):
    """An unattended spawn was refused because the runner's adapter is unverified."""


@dataclass(frozen=True)
class AdapterPin:
    """The npm ACP adapter a runner launches through, plus its (optional) pin."""

    npm_pkg: str
    env_var: str = ""
    bin_names: tuple[str, ...] = ()
    version: str = ""
    integrity: str = ""

    @property
    def pinned(self) -> bool:
        """True when the definition declares BOTH an exact version and a digest."""
        return bool(self.version and self.integrity)


@dataclass(frozen=True)
class RunnerDefinition:
    """One catalog row: how to find a runner, and what it speaks."""

    id: str
    display_name: str
    runtime_id: str
    bin_names: tuple[str, ...]
    env_var: str = ""
    version_args: tuple[str, ...] = ("--version",)
    acp_args: tuple[str, ...] = ()
    dialect: str = ""
    adapter: AdapterPin | None = None
    source: str = "builtin"


@dataclass(frozen=True)
class HealthEvidence:
    """Measured evidence from one probe. ``None`` means NOT measured, never zero."""

    ok: bool
    probe: str
    checked_at: str
    version: str | None = None
    latency_ms: int | None = None
    error: str | None = None
    resolved_command: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "probe": self.probe,
            "checked_at": self.checked_at,
            "version": self.version,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "resolved_command": list(self.resolved_command),
        }


@dataclass(frozen=True)
class AdapterVerification:
    """Verdict for a runner's adapter provenance.

    ``state`` is one of:

    ``verified``
        Resolves to a real on-disk adapter and its recorded provenance still matches
        what is installed (and the declared pin, when the row declares one).
    ``no_adapter``
        The row launches its own binary directly — there is no npm adapter to verify.
    ``absent``
        Nothing resolves; the adapter is not installed anywhere we can see.
    ``unverified``
        Resolvable but unprovable: the ``npx -y`` fallback, a missing provenance
        record, or an integrity/version mismatch. ``detail`` says which.
    """

    state: str
    detail: str
    resolved_command: tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        # ``no_adapter`` counts as verified: there is no unpinnable third party in the
        # launch path at all, which is a STRONGER position than a pinned adapter — the
        # gate exists to keep an unproven npm shim out of unattended work.
        return self.state in ("verified", "no_adapter")


# ── definitions ───────────────────────────────────────────────────────────────


def _as_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if str(v))
    if isinstance(value, str) and value:
        return (value,)
    return ()


def _adapter_from(raw: Any) -> AdapterPin | None:
    if not isinstance(raw, dict):
        return None
    pkg = str(raw.get("npm_pkg") or "").strip()
    if not pkg:
        return None
    return AdapterPin(
        npm_pkg=pkg,
        env_var=str(raw.get("env_var") or ""),
        bin_names=_as_tuple(raw.get("bin_names")),
        version=str(raw.get("version") or ""),
        integrity=str(raw.get("integrity") or ""),
    )


def _definition_from(
    raw: dict[str, Any], *, source: str, fallback_id: str = ""
) -> RunnerDefinition:
    rid = str(raw.get("id") or fallback_id).strip().lower()
    if not _SAFE_ID_RE.fullmatch(rid):
        raise ValueError(f"Invalid runner id: {rid!r}")
    bins = _as_tuple(raw.get("bin_names"))
    if not bins:
        raise ValueError(f"Runner {rid!r} declares no bin_names")
    return RunnerDefinition(
        id=rid,
        display_name=str(raw.get("display_name") or rid),
        runtime_id=str(raw.get("runtime_id") or f"acp:{rid}"),
        bin_names=bins,
        env_var=str(raw.get("env_var") or ""),
        version_args=_as_tuple(raw.get("version_args")) or ("--version",),
        acp_args=_as_tuple(raw.get("acp_args")),
        dialect=str(raw.get("dialect") or ""),
        adapter=_adapter_from(raw.get("adapter")),
        source=source,
    )


def user_catalog_dir() -> Path:
    """The BYO definition directory (not created — absence just means no BYO rows)."""
    from personalclaw.config.loader import config_dir

    return config_dir() / USER_CATALOG_DIR_NAME


def catalog() -> dict[str, RunnerDefinition]:
    """Return ``{id: RunnerDefinition}`` — shipped rows overlaid with BYO rows.

    Read fresh every call: a user can drop a definition in while the gateway runs and
    the next Settings load must see it (there is no cache to invalidate, and the read
    is two small JSON files).
    """
    rows: dict[str, RunnerDefinition] = {}
    try:
        payload = json.loads(_BUILTIN_CATALOG.read_text(encoding="utf-8"))
    except Exception:
        # The shipped data file is packaged (pyproject package-data). A wheel that
        # somehow lacks it must not take the gateway down — it degrades to BYO-only.
        logger.warning("runner catalog: shipped %s unreadable", _BUILTIN_CATALOG, exc_info=True)
        payload = {}
    for raw in payload.get("runners") or []:
        try:
            defn = _definition_from(raw, source="builtin")
        except Exception:
            logger.warning("runner catalog: skipping invalid shipped row %r", raw, exc_info=True)
            continue
        rows[defn.id] = defn

    try:
        user_dir = user_catalog_dir()
        files = sorted(user_dir.glob("*.json")) if user_dir.is_dir() else []
    except Exception:
        files = []
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            defn = _definition_from(raw, source="user", fallback_id=path.stem)
        except Exception:
            logger.warning("runner catalog: skipping invalid BYO row %s", path, exc_info=True)
            continue
        rows[defn.id] = defn
    return rows


def definition_for_runtime(runtime_id: str) -> RunnerDefinition | None:
    """The catalog row whose ``runtime_id`` (``acp:<cli>``) is *runtime_id*."""
    want = (runtime_id or "").strip()
    if not want:
        return None
    for defn in catalog().values():
        if defn.runtime_id == want:
            return defn
    return None


# ── health evidence (measured, persisted) ─────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sidecar_path(runner_id: str) -> Path:
    """``agent-metadata/<id>.runner.json`` — the structured sidecar for *runner_id*."""
    from personalclaw import agent_metadata

    if not _SAFE_ID_RE.fullmatch(runner_id or ""):
        raise ValueError(f"Invalid runner id: {runner_id!r}")
    return agent_metadata.metadata_dir() / f"{runner_id}.runner.json"


def _read_sidecar(runner_id: str) -> dict[str, Any]:
    try:
        return json.loads(sidecar_path(runner_id).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    except Exception:
        logger.debug("runner sidecar unreadable for %s", runner_id, exc_info=True)
        return {}


def _write_sidecar(runner_id: str, payload: dict[str, Any]) -> Path:
    from personalclaw.atomic_write import atomic_write

    path = sidecar_path(runner_id)
    atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def load_evidence(runner_id: str) -> HealthEvidence | None:
    """The last recorded evidence for *runner_id*, or ``None`` if never probed."""
    raw = _read_sidecar(runner_id).get("last_check")
    if not isinstance(raw, dict) or not raw.get("checked_at"):
        return None
    latency = raw.get("latency_ms")
    return HealthEvidence(
        ok=bool(raw.get("ok")),
        probe=str(raw.get("probe") or "unknown"),
        checked_at=str(raw.get("checked_at")),
        version=(str(raw["version"]) if raw.get("version") else None),
        latency_ms=(int(latency) if isinstance(latency, (int, float)) else None),
        error=(str(raw["error"]) if raw.get("error") else None),
        resolved_command=_as_tuple(raw.get("resolved_command")),
    )


def health_check_interval_secs() -> int:
    """``agents.runner_health_check_secs`` — how long evidence counts as current.

    The floor matches the PATCH allowlist's, so a hand-edited config cannot express a
    window the dashboard would refuse. An unreadable config falls back to the field's
    own default rather than to "never stale": treating unknown as fresh would hide the
    one case this value exists to name.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return max(60, int(AppConfig.load().agent.runner_health_check_secs))
    except Exception:
        logger.debug("runner health-check interval unreadable; using the default", exc_info=True)
        return 3600


def evidence_is_stale(
    evidence: HealthEvidence | None, *, interval_secs: int | None = None
) -> bool | None:
    """True when *evidence* is older than the configured check interval.

    ``None`` — unknown — in the two cases where a boolean would be a claim we cannot
    support: there is no evidence at all (a never-probed runner is not "overdue"; the
    row already says it was never probed), or ``checked_at`` will not parse, in which
    case we do not know the reading's age. A tz-naive timestamp is read as UTC, which
    is what :func:`_now_iso` writes.
    """
    if evidence is None:
        return None
    try:
        checked = datetime.fromisoformat(str(evidence.checked_at))
    except (TypeError, ValueError):
        return None
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    window = health_check_interval_secs() if interval_secs is None else max(60, int(interval_secs))
    return (datetime.now(timezone.utc) - checked).total_seconds() > window


def record_evidence(runner_id: str, evidence: HealthEvidence) -> Path:
    """Persist *evidence* as the runner's ``last_check``, preserving capabilities."""
    payload = _read_sidecar(runner_id)
    payload["runner"] = runner_id
    payload["last_check"] = evidence.to_dict()
    return _write_sidecar(runner_id, payload)


def load_capabilities(runner_id: str) -> dict[str, Any] | None:
    """Capabilities persisted from a real ACP handshake, or ``None`` if none yet."""
    caps = _read_sidecar(runner_id).get("capabilities")
    return caps if isinstance(caps, dict) and caps.get("recorded_at") else None


def record_capabilities(
    runtime_id: str,
    *,
    models: list[str] | None = None,
    modes: list[str] | None = None,
    efforts: list[str] | None = None,
) -> Path | None:
    """Persist a runner's capability matrix, as normalized from a real handshake.

    Called from the discovery path (``AcpAgentProvider.agents_from_snapshot``), whose
    input is the runner's own ``session/new`` snapshot — so every value here came off
    the wire. Runners with no catalog row are ignored (nothing to file it under).
    """
    defn = definition_for_runtime(runtime_id)
    if defn is None:
        return None
    try:
        payload = _read_sidecar(defn.id)
        payload["runner"] = defn.id
        payload["capabilities"] = {
            "source": "initialize",
            "recorded_at": _now_iso(),
            "models": list(models or []),
            "permission_modes": list(modes or []),
            "efforts": list(efforts or []),
        }
        return _write_sidecar(defn.id, payload)
    except Exception:
        logger.debug("runner capability persist failed for %s", runtime_id, exc_info=True)
        return None


def resolve_runner_command(defn: RunnerDefinition) -> list[str] | None:
    """Resolve the runner's OWN CLI (not its ACP adapter), or ``None`` if absent.

    Uses the shared vendor-neutral resolver so a CLI installed under a node version
    manager is found from a daemon with a minimal PATH — the same 4-step order the
    ACP bundles resolve through. No npm fallback: the runner binary is the vendor's,
    never something PersonalClaw fetches.
    """
    from personalclaw.acp.cli_resolve import resolve_acp_cli

    return resolve_acp_cli(
        env_var=defn.env_var or f"PERSONALCLAW_RUNNER_{defn.id.upper().replace('-', '_')}_BIN",
        bin_names=list(defn.bin_names),
        npm_pkg=None,
    )


def probe_runner(defn: RunnerDefinition, *, persist: bool = True) -> HealthEvidence:
    """Probe *defn*'s CLI and return MEASURED evidence (also persisted by default).

    The probe is ``<resolved bin> <version_args>`` — a read of the CLI's own version
    string. It spawns nothing else, passes no prompt and writes no workspace file, so
    it is safe to run on every Settings load.

    Failure carries the probe's OWN text: an unresolvable binary yields the resolver's
    verbatim reason, a non-zero exit yields the CLI's stderr verbatim, and a timeout
    or OS error yields the exception verbatim. ``latency_ms`` is populated ONLY when a
    process actually ran and was timed; ``version`` only when the output contained a
    version to parse.
    """
    argv = resolve_runner_command(defn)
    if not argv:
        names = ", ".join(defn.bin_names)
        hint = f"; set {defn.env_var} to override" if defn.env_var else ""
        return _finish(
            defn,
            HealthEvidence(
                ok=False,
                probe="path",
                checked_at=_now_iso(),
                error=f"{defn.bin_names[0]!r} not found on PATH (looked for: {names}){hint}",
            ),
            persist,
        )

    cmd = [*argv, *defn.version_args]
    started = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 - argv resolved from the catalog, never a shell
            cmd,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = int(round((time.monotonic() - started) * 1000))
        return _finish(
            defn,
            HealthEvidence(
                ok=False,
                probe="version",
                checked_at=_now_iso(),
                latency_ms=elapsed,
                error=f"TimeoutExpired: {exc}",
                resolved_command=tuple(argv),
            ),
            persist,
        )
    except OSError as exc:
        return _finish(
            defn,
            HealthEvidence(
                ok=False,
                probe="version",
                checked_at=_now_iso(),
                error=f"{type(exc).__name__}: {exc}",
                resolved_command=tuple(argv),
            ),
            persist,
        )
    elapsed = int(round((time.monotonic() - started) * 1000))
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return _finish(
            defn,
            HealthEvidence(
                ok=False,
                probe="version",
                checked_at=_now_iso(),
                latency_ms=elapsed,
                # The CLI's own words. Prefer stderr; some CLIs report on stdout.
                error=(err or out or f"exited {proc.returncode} with no output"),
                resolved_command=tuple(argv),
            ),
            persist,
        )
    return _finish(
        defn,
        HealthEvidence(
            ok=True,
            probe="version",
            checked_at=_now_iso(),
            version=_parse_version(out or err),
            latency_ms=elapsed,
            resolved_command=tuple(argv),
        ),
        persist,
    )


def _finish(defn: RunnerDefinition, evidence: HealthEvidence, persist: bool) -> HealthEvidence:
    if persist:
        try:
            record_evidence(defn.id, evidence)
        except Exception:
            logger.debug("runner evidence persist failed for %s", defn.id, exc_info=True)
    return evidence


def _parse_version(text: str) -> str | None:
    """Extract a version token from a CLI's ``--version`` output, or ``None``.

    ``None`` (unknown) rather than a placeholder: a CLI whose output we cannot parse
    has NOT told us its version, and printing a made-up one would be a lie the UI
    cannot distinguish from a real reading.
    """
    for line in (text or "").splitlines():
        m = _SEMVER_RE.search(line)
        if m:
            return m.group(0)
    return None


# ── adapter pin + verify ──────────────────────────────────────────────────────


def managed_adapter_prefix() -> Path:
    """The ``npm --prefix`` root PersonalClaw provisions ACP adapters into."""
    from personalclaw.config.loader import config_dir

    return config_dir() / "acp-adapters"


def adapter_lock_path() -> Path:
    """The provenance ledger for provisioned adapters."""
    return managed_adapter_prefix() / ADAPTER_LOCK_NAME


def _read_lock() -> dict[str, Any]:
    try:
        return json.loads(adapter_lock_path().read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    except Exception:
        logger.warning("adapter provenance ledger unreadable", exc_info=True)
        return {}


def installed_adapter_facts(npm_pkg: str) -> dict[str, str]:
    """What npm says is installed for *npm_pkg* in the managed prefix.

    Reads the prefix's ``package-lock.json`` — npm's own record of the resolved
    version and the tarball's Subresource-Integrity digest. Returns ``{}`` when the
    package is not installed there (or npm wrote no lock).
    """
    lock = managed_adapter_prefix() / "package-lock.json"
    try:
        payload = json.loads(lock.read_text(encoding="utf-8"))
    except Exception:
        return {}
    node = (payload.get("packages") or {}).get(f"node_modules/{npm_pkg}")
    if not isinstance(node, dict):
        return {}
    facts = {
        "version": str(node.get("version") or ""),
        "integrity": str(node.get("integrity") or ""),
    }
    return facts if facts["version"] or facts["integrity"] else {}


def record_provenance(npm_pkg: str, *, pin: AdapterPin | None = None) -> bool:
    """Record what was just installed for *npm_pkg*; refuse a pin mismatch.

    Returns True when provenance is on file. When *pin* declares an exact
    version+integrity and the install does not match it, nothing is recorded and
    False is returned — a mismatched adapter must stay unverified rather than be
    blessed by the act of recording it.
    """
    facts = installed_adapter_facts(npm_pkg)
    if not facts:
        return False
    if pin is not None and pin.pinned:
        if facts.get("version") != pin.version or facts.get("integrity") != pin.integrity:
            logger.warning(
                "acp adapter %s: install does not match the declared pin "
                "(installed %s/%s, pinned %s/%s) — provenance NOT recorded",
                npm_pkg,
                facts.get("version"),
                (facts.get("integrity") or "")[:16],
                pin.version,
                pin.integrity[:16],
            )
            return False
    from personalclaw.atomic_write import atomic_write

    ledger = _read_lock()
    ledger[npm_pkg] = {
        "version": facts.get("version", ""),
        "integrity": facts.get("integrity", ""),
        "recorded_at": _now_iso(),
    }
    path = adapter_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    return True


def verify_adapter(defn: RunnerDefinition) -> AdapterVerification:
    """Verify the provenance of *defn*'s ACP adapter."""
    pin = defn.adapter
    if pin is None:
        return AdapterVerification(
            state="no_adapter",
            detail="launches its own binary — no npm ACP adapter in the launch path",
        )

    from personalclaw.acp.cli_resolve import is_npx_fallback, resolve_acp_cli

    argv = resolve_acp_cli(
        env_var=pin.env_var or f"{defn.id.upper().replace('-', '_')}_ACP_BIN",
        bin_names=list(pin.bin_names) or [pin.npm_pkg.rsplit("/", 1)[-1]],
        npm_pkg=pin.npm_pkg,
    )
    if not argv:
        return AdapterVerification(
            state="absent", detail=f"{pin.npm_pkg} is not installed and cannot be resolved"
        )
    if is_npx_fallback(argv):
        return AdapterVerification(
            state="unverified",
            detail=(
                f"resolves via `npx -y {pin.npm_pkg}`, which fetches at launch — "
                "an npx run cannot be pinned or checksum-verified"
            ),
            resolved_command=tuple(argv),
        )

    facts = installed_adapter_facts(pin.npm_pkg)
    recorded = _read_lock().get(pin.npm_pkg)
    if not isinstance(recorded, dict):
        return AdapterVerification(
            state="unverified",
            detail=(
                f"{pin.npm_pkg} resolves on disk but has no recorded provenance — "
                "provision it through PersonalClaw so its integrity is on file"
            ),
            resolved_command=tuple(argv),
        )
    if not facts:
        return AdapterVerification(
            state="unverified",
            detail=(
                f"{pin.npm_pkg} has recorded provenance but npm reports nothing "
                "installed in the managed prefix — the adapter on PATH is a "
                "different install than the one that was verified"
            ),
            resolved_command=tuple(argv),
        )
    if facts.get("integrity") != recorded.get("integrity"):
        return AdapterVerification(
            state="unverified",
            detail=(
                f"{pin.npm_pkg} integrity changed since it was provisioned "
                f"(recorded {str(recorded.get('integrity'))[:24]}, "
                f"installed {str(facts.get('integrity'))[:24]})"
            ),
            resolved_command=tuple(argv),
        )
    if pin.pinned and facts.get("version") != pin.version:
        return AdapterVerification(
            state="unverified",
            detail=(
                f"{pin.npm_pkg} is installed at {facts.get('version')} but the "
                f"catalog pins {pin.version}"
            ),
            resolved_command=tuple(argv),
        )
    return AdapterVerification(
        state="verified",
        detail=(
            f"{pin.npm_pkg}@{facts.get('version')} matches its recorded integrity"
            + (" and the catalog pin" if pin.pinned else "")
        ),
        resolved_command=tuple(argv),
    )


def sha256_file(path: Path) -> str:
    """``sha256:<hex>`` for *path* — the digest form used for a BYO local adapter."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


# ── the unattended-spawn gate ─────────────────────────────────────────────────


def runtime_id_for_agent(agent: str | None) -> str:
    """The ``acp:<cli>`` runtime an *agent* would spawn, or ``""`` for native.

    Same precedence the provider bridge uses: the agent profile's own ``provider``,
    then the global ``agent.provider`` default. A bare ``"acp"`` (no ``:<cli>``) has
    no cataloged runner, so it is returned as-is and the gate treats it as
    unverifiable.
    """
    try:
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load()
        prof = (cfg.agents or {}).get(agent) if agent else None
        kind = (
            (getattr(prof, "provider", "") if prof else "")
            or getattr(cfg.agent, "provider", "")
            or "native"
        )
    except Exception:
        return ""
    kind = str(kind)
    return kind if kind.startswith("acp") else ""


def guard_unattended_spawn(runtime_id: str, *, unattended: bool) -> None:
    """Refuse an unattended spawn whose runner adapter is not verified.

    No-op unless BOTH the spawn is unattended AND
    ``agents.unattended_requires_verified_adapter`` is on. With both true the runner's
    adapter must verify; anything else raises :class:`UnverifiedAdapterError` naming
    the reason. Fail closed: a runtime with no catalog row cannot be verified, so it
    is refused too — the flag's whole promise is that nothing unproven runs while
    nobody is watching.

    ``unattended`` is resolved by the caller, and the ONE caller
    (:meth:`personalclaw.session.SessionManager.get_or_create`) derives it from the
    session key via :func:`personalclaw.guardrails.policy.is_unattended_session` rather
    than trusting a kwarg — a kwarg-only gate covered exactly one of the nine
    unattended session-key families.
    """
    if not unattended or not runtime_id:
        return
    try:
        from personalclaw.config.loader import AppConfig

        enabled = bool(AppConfig.load().agent.unattended_requires_verified_adapter)
    except Exception:
        logger.debug("adapter-verification gate: config unreadable", exc_info=True)
        return
    if not enabled:
        return
    defn = definition_for_runtime(runtime_id)
    if defn is None:
        raise UnverifiedAdapterError(
            f"Unattended spawn refused: {runtime_id!r} has no runner-catalog row, so its "
            "adapter cannot be verified. Add a definition under "
            f"{USER_CATALOG_DIR_NAME}/<id>.json, or turn off "
            "agents.unattended_requires_verified_adapter."
        )
    verdict = verify_adapter(defn)
    if verdict.verified:
        return
    raise UnverifiedAdapterError(
        f"Unattended spawn refused: {defn.display_name} adapter is not verified "
        f"({verdict.state}) — {verdict.detail}. Provision the adapter, or turn off "
        "agents.unattended_requires_verified_adapter."
    )


# ── the API row ───────────────────────────────────────────────────────────────


@dataclass
class RunnerRow:
    """One Settings → Agents row: definition + evidence + capabilities + adapter."""

    definition: RunnerDefinition
    evidence: HealthEvidence | None
    capabilities: dict[str, Any] | None
    adapter: AdapterVerification
    #: EI-6: the WORK-R8 lease currently held on this runner, or None when free. Already
    #: expiry-filtered by ``runner_lifecycle.lease_for`` — a row never carries a holder that
    #: idle-release has taken back.
    lease: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        ev = self.evidence
        return {
            "id": self.definition.id,
            "display_name": self.definition.display_name,
            "runtime_id": self.definition.runtime_id,
            "source": self.definition.source,
            "dialect": self.definition.dialect,
            "bin_names": list(self.definition.bin_names),
            # Health is either measured evidence or explicitly absent. There is no
            # third "assume it's fine" shape: an unprobed runner reports null.
            "health": ev.to_dict() if ev is not None else None,
            # Whether that reading is still current, per agents.runner_health_check_secs.
            # `null` = unknown (never probed, or an unparseable timestamp) — a stale
            # reading and an absent one are different facts and the surface says which.
            "health_stale": evidence_is_stale(ev),
            "capabilities": self.capabilities,
            "adapter": {
                "npm_pkg": self.definition.adapter.npm_pkg if self.definition.adapter else "",
                "pinned": bool(self.definition.adapter and self.definition.adapter.pinned),
                "state": self.adapter.state,
                "verified": self.adapter.verified,
                "detail": self.adapter.detail,
            },
            # Who holds this runner right now (EI-6 §3.1(5)), or null when free. `null` is
            # a positive statement: the lease was read and either absent or past its
            # idle-release window. There is no "maybe held" shape.
            "lease": self.lease,
        }


def runner_rows(*, probe: bool = False) -> list[RunnerRow]:
    """Every catalog row with its evidence. ``probe=True`` re-measures health first.

    Without ``probe`` this is a pure read of persisted evidence (fast, no spawns), so
    the Settings surface paints from the last real measurement instead of stalling on
    four subprocesses.
    """
    rows: list[RunnerRow] = []
    for defn in sorted(catalog().values(), key=lambda d: d.display_name.lower()):
        evidence = probe_runner(defn) if probe else load_evidence(defn.id)
        try:
            verdict = verify_adapter(defn)
        except Exception:
            logger.debug("adapter verification failed for %s", defn.id, exc_info=True)
            verdict = AdapterVerification(state="unverified", detail="verification errored")
        # EI-6: the live lease. Read per row rather than passed in, so every caller of
        # ``runner_rows`` (the Settings endpoint today, anything else tomorrow) shows the
        # same holder — a surface that had to remember to ask separately is a surface that
        # eventually forgets. Lazy import: ``runner_lifecycle`` reaches the workflow lease
        # store, which must not become an import-time dependency of the catalog.
        try:
            from personalclaw.agents import runner_lifecycle

            lease = runner_lifecycle.lease_for(defn.runtime_id)
        except Exception:
            logger.debug("lease read failed for %s", defn.id, exc_info=True)
            lease = None
        rows.append(
            RunnerRow(
                definition=defn,
                evidence=evidence,
                capabilities=load_capabilities(defn.id),
                adapter=verdict,
                lease=lease,
            )
        )
    return rows
