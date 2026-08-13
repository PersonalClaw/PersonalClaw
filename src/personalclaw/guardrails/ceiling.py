"""The governance CEILING — level one of "two levels, one rule: tightest wins"
(PLATFORM-HARDENING-FLOORS §5, AUTONOMY-GUARDRAILS S5.2).

Level 1 is this :class:`Ceiling`, loaded ONCE at boot from an operator-owned path the
running agent does not own. Level 2 is the existing :class:`~personalclaw.guardrails.
policy.SafetyProfile`, which may only **narrow**. Effective posture =
``resolve(ceiling, profile)``, and there is no path through this module by which a
profile can hand a run more reach than the ceiling allows.

**Four archetypes, one compose function each.** :func:`compose_ordinal`,
:func:`compose_ruleset`, :func:`compose_gate`, :func:`compose_map`. The evaluator
dispatches on the scope's ARCHETYPE, never on its name — which is what makes adding a
governed scope *data* (one :class:`ScopeSpec` row in :data:`CEILING_SCOPES`) rather than
engine code. A ScopeSpec naming an archetype with no compose function aborts boot.

**Enforcer-owned registries.** Matchers and ordinal scales come from
:mod:`personalclaw.guardrails.registries` and are never sourced from the governed file;
an unknown matcher/scale/value aborts governance boot with a WHAT/WHY/FIX error rather
than falling back to a default that would match differently (see that module's header for
the path-matcher landmine this closes).

**Where the file lives, and what the layer does and does not buy.** Default
``$PERSONALCLAW_HOME/governance/ceiling.json``, overridable to an absolute path with
``PERSONALCLAW_CEILING_FILE`` (the option that gives a real trust root: put it on a
root-owned ``0444`` file outside the agent's home). On a single-user machine the agent
runs as the user, so no in-process check can make a file unwritable. What this layer DOES
buy, and each is verified by a test:

* **No API write surface.** The ceiling is not config: it is absent from the dashboard's
  ``_EDITABLE_CONFIG`` PATCH allowlist and has no PUT/POST of its own, so nothing the
  agent can reach over HTTP edits it.
* **Agent write paths refuse it.** ``governance/`` is in the built-in sensitive-path
  denylist, so ``security.is_sensitive_path`` — which the action denylist, the files area
  and the bash hooks all consult — refuses reads and writes of it.
* **No mid-run widening.** It is read once and cached; a successful tamper cannot widen
  the process that is already running, only a later restart, which is a restart an
  operator can see.
* **Tamper evidence.** Boot SEL-audits the resolved source + digest, so a changed ceiling
  is attributable after the fact.

What it does NOT buy: OS-level immutability against a process running as the operator.
That requires the file to live outside the home, owned by another uid.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from personalclaw.errors import AgentError
from personalclaw.guardrails.budgets import Budget
from personalclaw.guardrails.registries import (
    MATCHER_NAME_GLOB,
    MATCHER_PATH_GLOB,
    UnknownRegistryEntry,
    get_matcher,
    get_scale,
    strictest,
)

if TYPE_CHECKING:
    from personalclaw.guardrails.policy import SafetyProfile

logger = logging.getLogger(__name__)

#: The env var that repoints the ceiling at an operator-owned path outside the agent's
#: home — the only way to get a trust root the agent's own uid cannot rewrite.
CEILING_PATH_ENV = "PERSONALCLAW_CEILING_FILE"

#: Directory (under the PersonalClaw home) that holds the ceiling. Also registered in
#: ``security._SENSITIVE_HOME_DIRS`` so every agent-reachable path check refuses it.
GOVERNANCE_DIRNAME = "governance"
CEILING_FILENAME = "ceiling.json"


class GovernanceBootError(Exception):
    """Governance could not be established, so nothing may run — fail CLOSED.

    Raised for a corrupt/unreadable ceiling, an unknown scope/key/value, an unknown
    matcher or scale, or a scope table naming an archetype with no compose function.
    Carries an :class:`AgentError` so every surface renders the same WHAT/WHY/FIX lines.
    """

    def __init__(self, error: AgentError) -> None:
        super().__init__(error.render())
        self.error = error


def _boot_error(code: str, what: str, why: str, fix: str, **kw: Any) -> GovernanceBootError:
    return GovernanceBootError(AgentError(code=code, what=what, why=why, fix=fix, **kw))


# ── the four archetypes ───────────────────────────────────────────────────────

ARCHETYPE_ORDINAL = "ordinal_control"
ARCHETYPE_RULESET = "scoped_ruleset"
ARCHETYPE_GATE = "capability_gate"
ARCHETYPE_MAP = "scoped_map"


@dataclass(frozen=True)
class OrdinalControl:
    """A single value on an enforcer-owned strictness scale (approval, scan, egress)."""

    scale: str
    value: str


@dataclass(frozen=True)
class ScopedRuleset:
    """``{mode, allow[], deny[]}`` over one namespace, matched by a named matcher.

    ``mode`` is the default stance: ``open`` allows unless denied, ``closed`` denies
    unless allowed. ``allow`` only has meaning when closed (parsing rejects an open
    ruleset carrying allow entries rather than silently ignoring them).
    """

    mode: str = "open"
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    matcher: str = MATCHER_PATH_GLOB


@dataclass(frozen=True)
class CapabilityGate:
    """``enabled`` (AND-composed) plus a ruleset scoping what the capability covers."""

    enabled: bool = True
    rules: ScopedRuleset = field(default_factory=lambda: ScopedRuleset(matcher=MATCHER_NAME_GLOB))


@dataclass(frozen=True)
class ScopedMap:
    """Named numeric caps composed per key (a budget). ``0`` means unlimited."""

    values: Mapping[str, float] = field(default_factory=dict)


def compose_ordinal(ceiling: OrdinalControl, profile: OrdinalControl) -> OrdinalControl:
    """Strictest-of on the enforcer's scale. A profile can only move it stricter."""
    scale = get_scale(ceiling.scale)
    return OrdinalControl(scale=ceiling.scale, value=strictest(scale, ceiling.value, profile.value))


def compose_ruleset(ceiling: ScopedRuleset, profile: ScopedRuleset) -> ScopedRuleset:
    """Tightest-wins over a ruleset: stance strictest, deny UNION, allow INTERSECTION.

    The allow intersection is what makes widening impossible: an entry the ceiling never
    allowed cannot appear because a profile listed it. A side with no allow entries is
    "no allow restriction from me", so it contributes nothing rather than emptying the
    intersection — the empty-set-means-everything trap. The intersection is computed by
    MATCHING (the ceiling's matcher), not by string equality, so a profile allow of
    ``~/ws/src/**`` survives a ceiling allow of ``~/ws/**`` while ``/etc/**`` does not.
    """
    mode = strictest(get_scale("ruleset_mode"), ceiling.mode, profile.mode)
    matcher_name = ceiling.matcher or profile.matcher
    matcher = get_matcher(matcher_name)
    deny = tuple(dict.fromkeys([*ceiling.deny, *profile.deny]))
    if not ceiling.allow:
        allow = profile.allow
    elif not profile.allow:
        allow = ceiling.allow
    else:
        # Keep a profile entry only when the ceiling already allows it; keep a ceiling
        # entry only when it is not made narrower by a profile entry that covers it.
        kept = [p for p in profile.allow if any(matcher(p, c) for c in ceiling.allow)]
        allow = tuple(dict.fromkeys(kept))
        mode = "closed"
    return ScopedRuleset(mode=mode, allow=allow, deny=deny, matcher=matcher_name)


def compose_gate(ceiling: CapabilityGate, profile: CapabilityGate) -> CapabilityGate:
    """AND on ``enabled``; the nested ruleset composes by :func:`compose_ruleset`."""
    return CapabilityGate(
        enabled=bool(ceiling.enabled and profile.enabled),
        rules=compose_ruleset(ceiling.rules, profile.rules),
    )


def _tighter_cap(a: float, b: float) -> float:
    """The tighter of two caps where ``0`` means unlimited (so 0 loses to any real cap)."""
    if a <= 0:
        return b
    if b <= 0:
        return a
    return min(a, b)


def compose_map(ceiling: ScopedMap, profile: ScopedMap) -> ScopedMap:
    """Per-key tightest-wins. A key only the profile sets is kept (it can only narrow)."""
    out: dict[str, float] = {}
    for key in {*ceiling.values, *profile.values}:
        out[key] = _tighter_cap(
            float(ceiling.values.get(key, 0) or 0), float(profile.values.get(key, 0) or 0)
        )
    return ScopedMap(values=out)


#: archetype → its ONE compose function. The evaluator dispatches through this dict and
#: on nothing else; a scope name never appears in a branch.
_COMPOSE: dict[str, Callable[[Any, Any], Any]] = {
    ARCHETYPE_ORDINAL: compose_ordinal,
    ARCHETYPE_RULESET: compose_ruleset,
    ARCHETYPE_GATE: compose_gate,
    ARCHETYPE_MAP: compose_map,
}


# ── the governed scopes (DATA — adding a scope is a row, not engine code) ──────


@dataclass(frozen=True)
class ScopeSpec:
    """One governed scope: which archetype composes it, and which profile fields it
    projects onto. Every field below is data read by the archetype's own handlers."""

    name: str
    archetype: str
    #: ordinal: the enforcer scale name + the SafetyProfile field carrying the value.
    scale: str = ""
    value_field: str = ""
    #: ruleset / gate: the SafetyProfile fields carrying allow + deny entries.
    allow_field: str = ""
    deny_field: str = ""
    matcher: str = MATCHER_PATH_GLOB
    #: gate: the SafetyProfile field whose value decides ``enabled``, and the value that
    #: means "disabled" (i.e. the narrowest grant).
    gate_field: str = ""
    gate_off_value: str = ""
    #: map: the SafetyProfile field holding the object, and the numeric keys on it.
    map_field: str = ""
    map_keys: tuple[str, ...] = ()


CEILING_SCOPES: tuple[ScopeSpec, ...] = (
    ScopeSpec(
        name="approval",
        archetype=ARCHETYPE_ORDINAL,
        scale="approval",
        value_field="approval",
    ),
    ScopeSpec(
        name="scan",
        archetype=ARCHETYPE_ORDINAL,
        scale="scan",
        value_field="scan_mode",
    ),
    ScopeSpec(
        name="egress",
        archetype=ARCHETYPE_ORDINAL,
        scale="egress",
        value_field="egress_tier",
    ),
    ScopeSpec(
        name="paths",
        archetype=ARCHETYPE_RULESET,
        allow_field="path_allowlist",
        deny_field="denylist_extra",
        matcher=MATCHER_PATH_GLOB,
    ),
    ScopeSpec(
        name="tools",
        archetype=ARCHETYPE_GATE,
        allow_field="tool_allowlist",
        matcher=MATCHER_NAME_GLOB,
        gate_field="tool_grants",
        gate_off_value="read",
    ),
    ScopeSpec(
        name="budget",
        archetype=ARCHETYPE_MAP,
        map_field="budget",
        map_keys=("max_tokens", "max_dollars"),
    ),
)

_SCOPES_BY_NAME = {s.name: s for s in CEILING_SCOPES}


def validate_scope_table(scopes: tuple[ScopeSpec, ...] = CEILING_SCOPES) -> None:
    """Every scope must name an archetype that has a compose function. Fail closed.

    Called at governance boot (not only at import) so a scope row added with a typo'd
    archetype aborts the gateway with WHAT/WHY/FIX instead of being skipped — a skipped
    scope is an ungoverned scope.
    """
    for spec in scopes:
        if spec.archetype not in _COMPOSE:
            raise _boot_error(
                "ERR_GOVERNANCE_UNKNOWN_ARCHETYPE",
                what=(
                    f"Governed scope {spec.name!r} declares archetype {spec.archetype!r}, "
                    "which has no compose function."
                ),
                why=(
                    "The ceiling evaluator dispatches on archetype alone, so a scope whose "
                    "archetype is unknown would be silently skipped — leaving that scope "
                    "ungoverned while the ceiling still reported as loaded."
                ),
                fix=(
                    "Set the scope's archetype to one of: "
                    f"{', '.join(sorted(_COMPOSE))} — or add a compose function for it."
                ),
                suggestions=tuple(sorted(_COMPOSE)),
            )


# ── parsing the governed file ─────────────────────────────────────────────────


def _reject_unknown_keys(scope: str, data: Mapping[str, Any], allowed: tuple[str, ...]) -> None:
    extra = [k for k in data if k not in allowed]
    if extra:
        raise _boot_error(
            "ERR_GOVERNANCE_UNKNOWN_KEY",
            what=f"The ceiling's {scope!r} scope carries unknown key(s): {', '.join(extra)}.",
            why=(
                "An unrecognized key would be ignored, so an operator who meant to tighten "
                "something would run wide open while the file looked correct. Governance "
                "therefore refuses to boot on a key it does not enforce."
            ),
            fix=f"Remove the key, or use one of: {', '.join(allowed)}.",
            suggestions=allowed,
        )


def _str_tuple(scope: str, key: str, value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _boot_error(
            "ERR_GOVERNANCE_BAD_VALUE",
            what=f"The ceiling's {scope}.{key} must be a list of strings.",
            why=(
                "A rule list of the wrong shape cannot be matched against anything, and "
                "silently treating it as empty would drop the rule the operator wrote."
            ),
            fix=f'Write {key} as a JSON array of strings, e.g. ["~/ws/**"].',
        )
    return tuple(v for v in value if v.strip())


def _parse_ordinal(spec: ScopeSpec, data: Mapping[str, Any]) -> OrdinalControl:
    _reject_unknown_keys(spec.name, data, ("value",))
    scale = get_scale(spec.scale)
    value = str(data.get("value", "") or "")
    if value not in scale:
        raise _boot_error(
            "ERR_GOVERNANCE_BAD_VALUE",
            what=f"The ceiling's {spec.name}.value is {value!r}, which is not on its scale.",
            why=(
                "Values on an enforcer-owned scale are compared by rank; an off-scale value "
                "has no rank, so it could not be composed as 'strictest wins' without "
                "guessing — and a guess here is a widened bound."
            ),
            fix=f"Use one of (loosest → strictest): {', '.join(scale)}.",
            suggestions=scale,
        )
    return OrdinalControl(scale=spec.scale, value=value)


def _parse_ruleset(spec: ScopeSpec, data: Mapping[str, Any]) -> ScopedRuleset:
    _reject_unknown_keys(spec.name, data, ("mode", "allow", "deny", "matcher"))
    mode_scale = get_scale("ruleset_mode")
    mode = str(data.get("mode", "open") or "open")
    if mode not in mode_scale:
        raise _boot_error(
            "ERR_GOVERNANCE_BAD_VALUE",
            what=f"The ceiling's {spec.name}.mode is {mode!r}.",
            why="A ruleset's stance decides what an unmatched item does; an unknown stance "
            "has no defined answer, and defaulting it either way is a policy decision the "
            "enforcer must not make silently.",
            fix=f"Use one of: {', '.join(mode_scale)}.",
            suggestions=mode_scale,
        )
    matcher_name = str(data.get("matcher", "") or spec.matcher)
    get_matcher(matcher_name)  # unknown matcher → boot abort (fail closed)
    allow = _str_tuple(spec.name, "allow", data.get("allow"))
    deny = _str_tuple(spec.name, "deny", data.get("deny"))
    if mode == "closed" and not allow:
        raise _boot_error(
            "ERR_GOVERNANCE_BRICKED_SCOPE",
            what=f"The ceiling's {spec.name} scope is closed but allows nothing.",
            why=(
                "A closed ruleset denies everything it does not allow, so an empty allow list "
                "blocks every item in the scope. That is almost never what an operator meant, "
                "and discovering it as a total outage at the first action is worse than at boot."
            ),
            fix=f'Add at least one allow entry, or set "mode": "open" for a deny-only {spec.name} '
            "ruleset.",
        )
    if mode == "open" and allow:
        raise _boot_error(
            "ERR_GOVERNANCE_INERT_KEY",
            what=f"The ceiling's {spec.name} scope is open but carries allow entries.",
            why=(
                "An open ruleset allows anything it does not deny, so its allow list would "
                "never be consulted — a declared rule with no reader, which is the exact "
                "class of defect this ceiling exists to remove."
            ),
            fix='Set "mode": "closed" to make the allow list the bound, or drop the allow key.',
        )
    return ScopedRuleset(mode=mode, allow=allow, deny=deny, matcher=matcher_name)


def _parse_gate(spec: ScopeSpec, data: Mapping[str, Any]) -> CapabilityGate:
    _reject_unknown_keys(spec.name, data, ("enabled", "allow", "matcher"))
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise _boot_error(
            "ERR_GOVERNANCE_BAD_VALUE",
            what=f"The ceiling's {spec.name}.enabled must be true or false.",
            why="A capability gate is AND-composed, so a non-boolean has no truth value to "
            "AND with — and coercing it would decide the gate by accident.",
            fix="Write enabled as a JSON boolean.",
        )
    matcher_name = str(data.get("matcher", "") or spec.matcher)
    get_matcher(matcher_name)
    allow = _str_tuple(spec.name, "allow", data.get("allow"))
    rules = ScopedRuleset(
        mode="closed" if allow else "open", allow=allow, deny=(), matcher=matcher_name
    )
    return CapabilityGate(enabled=bool(enabled), rules=rules)


def _parse_map(spec: ScopeSpec, data: Mapping[str, Any]) -> ScopedMap:
    _reject_unknown_keys(spec.name, data, spec.map_keys)
    values: dict[str, float] = {}
    for key in spec.map_keys:
        if key not in data:
            continue
        raw = data.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
            raise _boot_error(
                "ERR_GOVERNANCE_BAD_VALUE",
                what=f"The ceiling's {spec.name}.{key} must be a non-negative number.",
                why="Caps compose by taking the tighter value; a non-numeric cap cannot be "
                "compared, and treating it as absent would silently drop the bound.",
                fix=f"Set {key} to a number (0 means unlimited).",
            )
        values[key] = float(raw)
    return ScopedMap(values=values)


_PARSE: dict[str, Callable[["ScopeSpec", Mapping[str, Any]], Any]] = {
    ARCHETYPE_ORDINAL: _parse_ordinal,
    ARCHETYPE_RULESET: _parse_ruleset,
    ARCHETYPE_GATE: _parse_gate,
    ARCHETYPE_MAP: _parse_map,
}


# ── projecting a SafetyProfile into / out of archetype values ─────────────────


def _profile_ordinal(spec: ScopeSpec, profile: "SafetyProfile") -> OrdinalControl:
    return OrdinalControl(scale=spec.scale, value=str(getattr(profile, spec.value_field, "") or ""))


def _overrides_ordinal(spec: ScopeSpec, composed: OrdinalControl) -> dict[str, Any]:
    return {spec.value_field: composed.value}


def _profile_ruleset(spec: ScopeSpec, profile: "SafetyProfile") -> ScopedRuleset:
    allow = tuple(getattr(profile, spec.allow_field, ()) or ())
    deny = tuple(getattr(profile, spec.deny_field, ()) or ())
    return ScopedRuleset(
        mode="closed" if allow else "open", allow=allow, deny=deny, matcher=spec.matcher
    )


def _overrides_ruleset(spec: ScopeSpec, composed: ScopedRuleset) -> dict[str, Any]:
    return {spec.allow_field: composed.allow, spec.deny_field: composed.deny}


def _profile_gate(spec: ScopeSpec, profile: "SafetyProfile") -> CapabilityGate:
    grant = str(getattr(profile, spec.gate_field, "") or "")
    allow = tuple(getattr(profile, spec.allow_field, ()) or ())
    return CapabilityGate(
        enabled=grant != spec.gate_off_value,
        rules=ScopedRuleset(
            mode="closed" if allow else "open", allow=allow, deny=(), matcher=spec.matcher
        ),
    )


def _overrides_gate(spec: ScopeSpec, composed: CapabilityGate) -> dict[str, Any]:
    out: dict[str, Any] = {spec.allow_field: composed.rules.allow}
    if not composed.enabled:
        out[spec.gate_field] = spec.gate_off_value
    elif composed.rules.allow:
        out[spec.gate_field] = "custom"
    return out


def _profile_map(spec: ScopeSpec, profile: "SafetyProfile") -> ScopedMap:
    obj = getattr(profile, spec.map_field, None)
    return ScopedMap(values={k: float(getattr(obj, k, 0) or 0) for k in spec.map_keys})


def _overrides_map(spec: ScopeSpec, composed: ScopedMap) -> dict[str, Any]:
    # Budget is the only map today; its constructor takes the keys by name and an int
    # token cap must stay an int (the meter compares against token counts).
    kwargs: dict[str, Any] = {}
    for key, value in composed.values.items():
        kwargs[key] = int(value) if key == "max_tokens" else float(value)
    return {spec.map_field: Budget(**kwargs)}


_FROM_PROFILE: dict[str, Callable[["ScopeSpec", Any], Any]] = {
    ARCHETYPE_ORDINAL: _profile_ordinal,
    ARCHETYPE_RULESET: _profile_ruleset,
    ARCHETYPE_GATE: _profile_gate,
    ARCHETYPE_MAP: _profile_map,
}
_TO_OVERRIDES: dict[str, Callable[["ScopeSpec", Any], dict[str, Any]]] = {
    ARCHETYPE_ORDINAL: _overrides_ordinal,
    ARCHETYPE_RULESET: _overrides_ruleset,
    ARCHETYPE_GATE: _overrides_gate,
    ARCHETYPE_MAP: _overrides_map,
}


# ── the ceiling itself ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Ceiling:
    """The operator's hard bound: scope name → archetype value. Immutable once loaded."""

    source: str = ""
    digest: str = ""
    controls: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def control(self, scope: str) -> Any:
        return self.controls.get(scope)

    @property
    def is_open(self) -> bool:
        """True when no operator ceiling is in force (no file → the profile governs alone)."""
        return not self.controls


#: No ceiling file present. NOT "everything allowed" — it means "no operator bound beyond
#: the SafetyProfile", which is the posture every release before this one shipped.
OPEN_CEILING = Ceiling()


def parse_ceiling(data: Any, *, source: str = "", digest: str = "") -> Ceiling:
    """Parse a ceiling document. Any problem raises :class:`GovernanceBootError`."""
    validate_scope_table()
    if not isinstance(data, dict):
        raise _boot_error(
            "ERR_GOVERNANCE_CEILING_CORRUPT",
            what=f"The governance ceiling at {source or '<memory>'} is not a JSON object.",
            why="The ceiling is the operator's hard bound on every run; a document that "
            "cannot be parsed cannot be honoured, and continuing without it would run the "
            "agent wider than the operator asked for.",
            fix='Write the file as {"version": 1, "scopes": {...}}, or delete it to run with '
            "no operator ceiling.",
        )
    _reject_unknown_keys("<root>", data, ("version", "scopes"))
    version = data.get("version", 1)
    if version != 1:
        raise _boot_error(
            "ERR_GOVERNANCE_CEILING_VERSION",
            what=f"The governance ceiling declares version {version!r}; this build reads 1.",
            why="A future schema may add scopes this build cannot enforce, so honouring it "
            "partially would report a bound that is not applied.",
            fix='Set "version": 1, or upgrade PersonalClaw to a build that reads this schema.',
        )
    scopes = data.get("scopes") or {}
    if not isinstance(scopes, dict):
        raise _boot_error(
            "ERR_GOVERNANCE_CEILING_CORRUPT",
            what='The governance ceiling\'s "scopes" must be an object.',
            why="Scopes are looked up by name; a non-object cannot be looked up and would "
            "leave every scope ungoverned.",
            fix='Write "scopes" as a JSON object of scope name → rule object.',
        )
    controls: dict[str, Any] = {}
    for name, raw in scopes.items():
        spec = _SCOPES_BY_NAME.get(name)
        if spec is None:
            raise _boot_error(
                "ERR_GOVERNANCE_UNKNOWN_SCOPE",
                what=f"The governance ceiling names an unknown scope {name!r}.",
                why="An unknown scope is enforced by nothing, so an operator who wrote it to "
                "tighten the agent would get no tightening at all — and no warning.",
                fix=f"Use one of the governed scopes: {', '.join(s.name for s in CEILING_SCOPES)}.",
                suggestions=tuple(s.name for s in CEILING_SCOPES),
            )
        if not isinstance(raw, dict):
            raise _boot_error(
                "ERR_GOVERNANCE_CEILING_CORRUPT",
                what=f"The ceiling's {name!r} scope must be an object.",
                why="Each scope's keys are read by its archetype's parser; a scalar carries "
                "none of them.",
                fix=f'Write "{name}" as a JSON object.',
            )
        try:
            controls[name] = _PARSE[spec.archetype](spec, raw)
        except UnknownRegistryEntry as exc:
            # One abort type for every governance failure: a caller (the gateway boot, a
            # dispatch seam) must not have to know that "unknown matcher" arrives as a
            # different exception class than "corrupt file" to fail closed on both.
            raise GovernanceBootError(exc.error) from exc
    return Ceiling(source=source, digest=digest, controls=MappingProxyType(controls))


def ceiling_path() -> Path:
    """Where the ceiling is read from. NEVER a module constant — a frozen ``config_dir()``
    would bind the real home at import time and no test fixture could reach it."""
    override = os.environ.get(CEILING_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    from personalclaw.config.loader import config_dir

    return Path(config_dir()) / GOVERNANCE_DIRNAME / CEILING_FILENAME


def load_ceiling(path: Path | None = None) -> Ceiling:
    """Read + parse the ceiling from disk. Absent file → :data:`OPEN_CEILING`.

    A file that EXISTS but cannot be read or parsed raises — an unreadable ceiling is
    exactly the case that must not fall back to "no bound".
    """
    target = Path(path) if path is not None else ceiling_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        validate_scope_table()
        return OPEN_CEILING
    except OSError as exc:
        raise _boot_error(
            "ERR_GOVERNANCE_CEILING_UNREADABLE",
            what=f"The governance ceiling at {target} exists but could not be read ({exc}).",
            why="An unreadable ceiling cannot be honoured. Falling back to 'no ceiling' would "
            "turn a permissions problem into a silent privilege escalation.",
            fix=f"Make {target} readable by the PersonalClaw process, or remove it to run with "
            "no operator ceiling.",
        ) from exc
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _boot_error(
            "ERR_GOVERNANCE_CEILING_CORRUPT",
            what=f"The governance ceiling at {target} is not valid JSON (line {exc.lineno}).",
            why="A ceiling that cannot be parsed cannot bound anything, and starting without "
            "it would run every unattended action wider than the operator declared.",
            fix=f"Fix the JSON at {target}:{exc.lineno}, or remove the file to run with no "
            "operator ceiling.",
        ) from exc
    return parse_ceiling(data, source=str(target), digest=digest)


_ACTIVE: Ceiling | None = None


def active_ceiling() -> Ceiling:
    """The ceiling in force for this process — read once, cached, never reloaded.

    Caching is the no-mid-run-widening property: a tamper (or a legitimate edit) after
    boot cannot widen a running gateway, only a restart the operator can see. A parse
    failure is NOT cached, so every caller keeps failing closed until it is fixed.
    """
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = load_ceiling()
    return _ACTIVE


def reset_ceiling() -> None:
    """Drop the cached ceiling (tests + a deliberate reload). Process-global by design."""
    global _ACTIVE
    _ACTIVE = None


def ensure_governance_boot() -> Ceiling:
    """Establish governance at gateway boot. Raises :class:`GovernanceBootError` to abort.

    Called FIRST in the gateway's start-up so a corrupt/unknown ceiling stops the process
    before a single service — and therefore a single unattended action — can run. Records
    the resolved source + digest to the SEL so a changed ceiling is attributable later.
    """
    ceiling = active_ceiling()
    if ceiling.is_open:
        logger.info(
            "governance: no operator ceiling at %s — the SafetyProfile is the only bound",
            ceiling_path(),
        )
    else:
        logger.info(
            "governance: ceiling loaded from %s (digest %s) bounding %s",
            ceiling.source,
            ceiling.digest,
            ", ".join(sorted(ceiling.controls)),
        )
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="gateway",
            operation="guardrails.governance_boot",
            outcome="open" if ceiling.is_open else "bounded",
            source="guardrails",
            resources=(
                f"{ceiling.source or ceiling_path()} digest={ceiling.digest or 'none'} "
                f"scopes={','.join(sorted(ceiling.controls)) or 'none'}"
            ),
        )
    except Exception:
        logger.debug("governance boot SEL audit failed", exc_info=True)
    return ceiling


# ── resolution ────────────────────────────────────────────────────────────────

#: Clamps already reported, so the hot path (every dispatch resolves a profile) records
#: each distinct narrowing once per process instead of per action.
_REPORTED_CLAMPS: set[tuple[str, str, str, str]] = set()


def reset_clamp_reports() -> None:
    _REPORTED_CLAMPS.clear()


def _report_clamp(profile_name: str, scope: str, before: Any, after: Any) -> None:
    key = (profile_name, scope, repr(before), repr(after))
    if key in _REPORTED_CLAMPS:
        return
    _REPORTED_CLAMPS.add(key)
    logger.warning(
        "governance: ceiling narrowed %s.%s from %r to %r (a profile may only narrow)",
        profile_name,
        scope,
        before,
        after,
    )
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller=f"profile:{profile_name}",
            operation="guardrails.ceiling_clamp",
            outcome="narrowed",
            source="guardrails",
            resources=f"{scope}: {before!r} → {after!r}",
        )
    except Exception:
        logger.debug("ceiling clamp SEL audit failed", exc_info=True)


def resolve(ceiling: Ceiling, profile: "SafetyProfile") -> "SafetyProfile":
    """Effective posture = ceiling ∩ profile, under tightest-wins.

    Walks :data:`CEILING_SCOPES`, dispatching each governed scope through its archetype's
    compose function. A scope the ceiling does not mention is left to the profile; a
    scope it does mention can only make the profile narrower, and every narrowing is
    logged + SEL-audited once per process (silent downgrades are a standing finding).
    """
    if ceiling.is_open:
        return profile
    validate_scope_table()
    overrides: dict[str, Any] = {}
    for spec in CEILING_SCOPES:
        control = ceiling.control(spec.name)
        if control is None:
            continue
        archetype = spec.archetype
        from_profile = _FROM_PROFILE[archetype](spec, profile)
        composed = _COMPOSE[archetype](control, from_profile)
        if composed != from_profile:
            _report_clamp(profile.name, spec.name, from_profile, composed)
        overrides.update(_TO_OVERRIDES[archetype](spec, composed))
    if not overrides:
        return profile
    return profile.with_overrides(**overrides)
