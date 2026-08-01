"""Run-start preflight — fail before spending, not at node 7 (WF2-R12).

A workflow that needs a credential it does not have should refuse to start. The
alternative — discovering it seven nodes in — has already paid for six nodes of model
calls, and the user reads a mid-run failure as a bug rather than a missing setting.

Four checks, all cheap and none of them instantiating anything:

* **credentials** — declared in `metadata.requirements.credentials`, plus every
  `{{secret:KEY}}` the spec actually references. The declaration is what an author
  promised; the references are what the engine will really resolve, and a spec can
  reference a key nobody declared.
* **binaries** — `shutil.which`, so a template needing `git` or `gh` says so up front.
* **models** — the `can_resolve_use_case` probe, deliberately reusing the same
  no-instantiate check behind onboarding's `needs_model`. A private capability check here
  could disagree with what the bridge can actually resolve, and then preflight would pass
  a run the engine cannot execute.
* **action providers** — every `action` node's provider must be registered. An unknown
  provider is a guaranteed node failure that costs nothing to catch now.

**Missing is an ERROR; unverifiable is a WARNING.** If a check cannot run (no credential
store, no registry) preflight must not claim the requirement is absent — refusing a run
because the checker was unavailable is its own outage. That distinction is the whole
reason findings are typed rather than a bare list of strings.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from typing import Any

from personalclaw.workflows.models import Node, NodeKind, walk

logger = logging.getLogger(__name__)

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


@dataclass
class Finding:
    """One unmet (or unverifiable) requirement.

    `remediation` is separate from `message` on purpose: the message says what is wrong,
    the remediation says what to do, and collapsing them leaves the user with a diagnosis
    and no next step.
    """

    code: str
    message: str
    remediation: str = ""
    severity: str = SEVERITY_ERROR
    kind: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "remediation": self.remediation,
            "severity": self.severity,
            "kind": self.kind,
        }


@dataclass
class PreflightResult:
    findings: list[Finding] = field(default_factory=list)
    checked: dict[str, list[str]] = field(default_factory=dict)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == SEVERITY_WARNING]

    @property
    def ok(self) -> bool:
        """Warnings never block. A run that refuses to start because a checker was
        unavailable is an outage of its own."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "findings": [f.to_dict() for f in self.findings],
            "checked": {k: list(v) for k, v in self.checked.items()},
        }


def preflight(
    spec: dict[str, Any],
    *,
    credential_resolver: Any = None,
    which: Any = None,
    model_probe: Any = None,
    provider_lookup: Any = None,
) -> PreflightResult:
    """Check everything a run needs before it starts.

    Every collaborator is injectable so this is unit-testable without a credential store,
    a PATH, or a provider registry — and so a test can prove the unverifiable-vs-missing
    distinction, which is the part most likely to regress.
    """
    result = PreflightResult()
    metadata = spec.get("metadata") or {}
    requirements = metadata.get("requirements") or {}
    if not isinstance(requirements, dict):
        requirements = {}

    _check_credentials(spec, requirements, result, credential_resolver)
    _check_binaries(requirements, result, which)
    _check_models(spec, result, model_probe)
    _check_action_providers(spec, result, provider_lookup)
    return result


# ── credentials ──────────────────────────────────────────────────────────────


def _check_credentials(
    spec: dict[str, Any],
    requirements: dict[str, Any],
    result: PreflightResult,
    resolver: Any,
) -> None:
    from personalclaw.workflows.secrets import secret_keys_referenced

    declared = [str(k) for k in (requirements.get("credentials") or [])]
    # Union of declared and REFERENCED: a spec can reference a key nobody declared, and
    # that reference is what the engine will actually try to resolve.
    needed = sorted(set(declared) | set(secret_keys_referenced(spec)))
    result.checked["credentials"] = needed
    if not needed:
        return

    lookup = resolver
    if lookup is None:
        try:
            from personalclaw.config.loader import config_dir
            from personalclaw.llm.credentials import CredentialStore

            store = CredentialStore(config_dir())

            def lookup(key: str) -> bool:  # type: ignore[misc]
                try:
                    cred = store.resolve(key)
                except KeyError:
                    return False
                return bool(getattr(cred, "secret", ""))

        except Exception:
            # The store itself is unavailable — report as UNVERIFIABLE, never as missing.
            logger.debug("preflight: credential store unavailable", exc_info=True)
            result.findings.append(
                Finding(
                    code="WF_PRE_CREDENTIALS_UNVERIFIABLE",
                    message=(
                        f"could not check {len(needed)} credential(s): the store is " "unavailable"
                    ),
                    remediation="the run may still work; check Settings → Providers if it fails",
                    severity=SEVERITY_WARNING,
                    kind="credentials",
                )
            )
            return

    for key in needed:
        try:
            present = bool(lookup(key))
        except Exception:
            logger.debug("preflight: credential lookup failed for %s", key, exc_info=True)
            continue
        if not present:
            result.findings.append(
                Finding(
                    code="WF_PRE_CREDENTIAL_MISSING",
                    message=f"credential {key!r} is not set",
                    remediation=f"add {key} in Settings → Providers, then start the run again",
                    kind="credentials",
                )
            )


# ── binaries ─────────────────────────────────────────────────────────────────


def _check_binaries(requirements: dict[str, Any], result: PreflightResult, which: Any) -> None:
    needed = [str(b) for b in (requirements.get("binaries") or [])]
    result.checked["binaries"] = needed
    if not needed:
        return
    finder = which or shutil.which
    for binary in needed:
        try:
            found = finder(binary)
        except Exception:
            logger.debug("preflight: which(%s) failed", binary, exc_info=True)
            continue
        if not found:
            result.findings.append(
                Finding(
                    code="WF_PRE_BINARY_MISSING",
                    message=f"required binary {binary!r} is not on PATH",
                    remediation=f"install {binary}, or edit the workflow to not need it",
                    kind="binaries",
                )
            )


# ── models ───────────────────────────────────────────────────────────────────


def _check_models(spec: dict[str, Any], result: PreflightResult, probe: Any) -> None:
    """Every use case the spec's LLM nodes will resolve must be resolvable NOW.

    Uses `can_resolve_use_case` — the same no-instantiate probe behind onboarding's
    `needs_model`. A private capability check could disagree with what the bridge actually
    resolves, and then preflight would greenlight a run the engine cannot execute.
    """
    from personalclaw.workflows.engine import DEFAULT_MODEL_TIERS

    root = _root_of(spec)
    if root is None:
        return
    tiers = dict(DEFAULT_MODEL_TIERS)
    defaults = spec.get("defaults") or {}
    tiers.update({str(k): str(v) for k, v in (defaults.get("model_tiers") or {}).items()})

    use_cases: set[str] = set()
    for _path, node in walk(root):
        if node.kind not in (NodeKind.STAGE, NodeKind.INFER):
            continue
        tier = str((node.config or {}).get("model_tier", "standard") or "standard")
        use_cases.add(tiers.get(tier, "background"))
    # A judge gate reasons, so it resolves on the reasoning tier (engine.dispatch_gate).
    for _path, node in walk(root):
        if node.kind == NodeKind.GATE and str((node.config or {}).get("kind", "")) == "judge":
            declared_tier = (node.config or {}).get("model_tier")
            use_cases.add(
                tiers.get(str(declared_tier), "background") if declared_tier else "reasoning"
            )

    result.checked["models"] = sorted(use_cases)
    if not use_cases:
        return

    check = probe
    if check is None:
        try:
            from personalclaw.providers.provider_bridge import can_resolve_use_case

            check = can_resolve_use_case
        except Exception:
            logger.debug("preflight: model probe unavailable", exc_info=True)
            result.findings.append(
                Finding(
                    code="WF_PRE_MODELS_UNVERIFIABLE",
                    message="could not check model availability",
                    remediation="the run may still work; check Settings → Models if it fails",
                    severity=SEVERITY_WARNING,
                    kind="models",
                )
            )
            return

    for use_case in sorted(use_cases):
        try:
            resolvable = bool(check(use_case))
        except Exception:
            logger.debug("preflight: probe failed for %s", use_case, exc_info=True)
            continue
        if not resolvable:
            result.findings.append(
                Finding(
                    code="WF_PRE_MODEL_UNRESOLVED",
                    message=f"no model resolves for the {use_case!r} use case",
                    remediation=(
                        f"select a model for {use_case} in Settings → Models, or change the "
                        "node's model_tier"
                    ),
                    kind="models",
                )
            )


# ── action providers ─────────────────────────────────────────────────────────


def _check_action_providers(spec: dict[str, Any], result: PreflightResult, lookup: Any) -> None:
    root = _root_of(spec)
    if root is None:
        return
    names: set[str] = set()
    for _path, node in walk(root):
        if node.kind != NodeKind.ACTION:
            continue
        provider = (node.config or {}).get("provider")
        # A BOUND provider name is resolved at dispatch, so it cannot be checked here.
        # Skipping it is correct; guessing at the binding's future value is not.
        if isinstance(provider, str) and provider and "{{" not in provider:
            names.add(provider)
    result.checked["action_providers"] = sorted(names)
    if not names:
        return

    getter = lookup
    if getter is None:
        try:
            from personalclaw.action_providers.registry import (
                _ensure_default_providers_registered,
                get_action_provider,
            )

            _ensure_default_providers_registered()
            getter = get_action_provider
        except Exception:
            logger.debug("preflight: action registry unavailable", exc_info=True)
            result.findings.append(
                Finding(
                    code="WF_PRE_PROVIDERS_UNVERIFIABLE",
                    message="could not check action providers",
                    remediation="the run may still work; check the installed apps if it fails",
                    severity=SEVERITY_WARNING,
                    kind="action_providers",
                )
            )
            return

    for name in sorted(names):
        try:
            found = getter(name)
        except Exception:
            logger.debug("preflight: provider lookup failed for %s", name, exc_info=True)
            continue
        if found is None:
            result.findings.append(
                Finding(
                    code="WF_PRE_PROVIDER_UNKNOWN",
                    message=f"action provider {name!r} is not registered",
                    remediation=(
                        f"install the app that provides {name}, or point the node at a "
                        "registered provider"
                    ),
                    kind="action_providers",
                )
            )


def _root_of(spec: dict[str, Any]) -> Node | None:
    raw = (spec or {}).get("root")
    if not isinstance(raw, dict):
        return None
    try:
        return Node.from_dict(raw)
    except (ValueError, TypeError):
        # An unparseable spec is the validator's finding, not preflight's — reporting it
        # twice under two vocabularies would just be noise.
        return None
