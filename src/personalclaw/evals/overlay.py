"""Child-process component overlays (EVALUATION-SUBSTRATE §3.1 step 2).

The ablation runner measures a harness component by running the same benchmark with
that component ON and OFF. The obvious implementation — edit the live spec/config,
run, edit it back — is FORBIDDEN: a crash between the two edits leaves the operator's
configuration silently altered, and "the runner never edits anything" (§3.1 step 4) is
the plan's rule.

So a toggle is an **overlay that exists only inside the child process**:

1. the parent serializes a :class:`ComponentOverlay` into the child's spawn env
   (:data:`OVERLAY_ENV`) — on the ``os.environ.copy()`` the matrix runner already
   builds for ``PERSONALCLAW_WORKSPACE``/``PERSONALCLAW_HOME``, so the parent's own
   env is untouched;
2. the child calls :func:`apply_in_child`, which writes ONLY into the throwaway
   per-cell ``PERSONALCLAW_HOME`` the parent handed it (a temp dir) or sets an env var
   in its OWN process.

No code path here can reach the live home. :func:`apply_in_child` refuses outright when
``PERSONALCLAW_HOME`` still points at the default home — the negative rail, because a
misconfigured spawn is exactly how a "temporary" overlay becomes a permanent edit.

The arm vocabulary is closed (:data:`ARMS`) and validated on the way in: a typo'd arm
that silently toggled nothing would report a delta of 0.0, indistinguishable from a
component that does nothing — the exact conclusion the runner exists to draw.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: Env var carrying the JSON overlay from parent to child. Read once, in the child.
OVERLAY_ENV = "PERSONALCLAW_ABLATION_OVERLAY"

#: Env var the ``skill`` overlay sets in the CHILD's own process so the skills loader's
#: suppression choke point (``personalclaw.skills.suppression``) can see it.
SUPPRESSED_SKILLS_ENV = "PERSONALCLAW_SUPPRESSED_SKILLS"

#: Env var the ``surfacing_heuristic`` overlay sets so the surfacing allocator ablates
#: one named heuristic for this process only.
ABLATE_SURFACING_ENV = "PERSONALCLAW_ABLATE_SURFACING"

# ── the closed arm vocabulary ────────────────────────────────────────────────
#: The component is present exactly as it ships — the baseline arm.
ARM_ON = "on"
#: The component is removed. The delta between this and ``on`` is the component's payoff.
ARM_OFF = "off"
#: The component is present but in a deliberately cheaper form (§6's tier table). A
#: ``cheap`` arm that matches ``on`` is what makes the ``lighten`` verdict reachable —
#: without this arm there is no third measurement and the verdict is unreachable.
ARM_CHEAP = "cheap"
ARMS: tuple[str, ...] = (ARM_ON, ARM_OFF, ARM_CHEAP)

#: The matrix axis name the arms travel on (§3.1 step 2's ``arm_mask``).
ARM_AXIS = "arm_mask"

# ── the closed component-kind vocabulary ─────────────────────────────────────
#: A named skill's body. OFF = the body never reaches the prompt (§3.3's suppressed arm).
KIND_SKILL = "skill"
#: A named surfacing heuristic from ``learning.surfacing.ABLATABLE``.
KIND_SURFACING = "surfacing_heuristic"
#: A boolean/scalar config field (a runtime hint, a §2.4-slot allocator stage — each is
#: reached through the config field that switches it). OFF = the field's ``off_value``.
KIND_CONFIG_FLAG = "config_flag"
KINDS: tuple[str, ...] = (KIND_SKILL, KIND_SURFACING, KIND_CONFIG_FLAG)


class OverlayRefusedError(RuntimeError):
    """The child refused to apply an overlay because it could have escaped its cell.

    Raised when ``PERSONALCLAW_HOME`` is absent or still resolves to the operator's real
    home. Deliberately fatal: the cell becomes ``VERIFIER_ABSENT`` (an honest "could not
    measure") rather than a measurement taken by mutating live state.
    """


@dataclass(frozen=True)
class ComponentOverlay:
    """One component's toggle for one arm. JSON round-trips through the spawn env."""

    component_id: str
    kind: str
    #: What the kind names: a skill name, a heuristic name, or a dotted config path.
    target: str
    arm: str = ARM_ON
    #: For ``config_flag``: the value that means OFF (default ``False``).
    off_value: object = False
    #: For ``config_flag``: the value that means CHEAP. ``None`` ⇒ the kind has no cheap
    #: form and a ``cheap`` arm is a configuration error, not a silent no-op.
    cheap_value: object = None
    #: Free-form provenance for the report (which template/registry row asked for this).
    notes: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown component kind {self.kind!r} — expected one of {KINDS}")
        if self.arm not in ARMS:
            raise ValueError(f"unknown arm {self.arm!r} — expected one of {ARMS}")
        if not self.target:
            raise ValueError("overlay target must be non-empty")

    def to_dict(self) -> dict:
        return {
            "component_id": self.component_id,
            "kind": self.kind,
            "target": self.target,
            "arm": self.arm,
            "off_value": self.off_value,
            "cheap_value": self.cheap_value,
            "notes": dict(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ComponentOverlay":
        return cls(
            component_id=str(data.get("component_id", "")),
            kind=str(data.get("kind", "")),
            target=str(data.get("target", "")),
            arm=str(data.get("arm", ARM_ON)),
            off_value=data.get("off_value", False),
            cheap_value=data.get("cheap_value"),
            notes=dict(data.get("notes") or {}),
        )

    def for_arm(self, arm: str) -> "ComponentOverlay":
        """This overlay rebound to another arm (the runner builds one per arm)."""
        return ComponentOverlay(
            component_id=self.component_id,
            kind=self.kind,
            target=self.target,
            arm=arm,
            off_value=self.off_value,
            cheap_value=self.cheap_value,
            notes=dict(self.notes),
        )


def encode(overlay: ComponentOverlay) -> str:
    """Render the overlay for :data:`OVERLAY_ENV` (compact, sorted, stable)."""
    return json.dumps(overlay.to_dict(), separators=(",", ":"), sort_keys=True)


def decode(text: str) -> ComponentOverlay | None:
    """Parse :data:`OVERLAY_ENV`. Absent/garbage ⇒ ``None`` (no overlay, plain cell)."""
    if not text:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        logger.warning("unparseable ablation overlay in env; running cell unmodified")
        return None
    if not isinstance(data, dict):
        return None
    try:
        return ComponentOverlay.from_dict(data)
    except ValueError:
        logger.warning("invalid ablation overlay in env; running cell unmodified")
        return None


def from_env(env: dict | None = None) -> ComponentOverlay | None:
    """The overlay this process was spawned with, if any."""
    source = os.environ if env is None else env
    return decode(str(source.get(OVERLAY_ENV) or ""))


def spawn_env_for(base_env: dict, overlay: ComponentOverlay | None) -> dict:
    """``base_env`` PLUS the overlay — on the COPY the caller already made.

    Returns a new dict; ``base_env`` is not mutated, and neither is ``os.environ``.
    """
    env = dict(base_env)
    if overlay is not None:
        env[OVERLAY_ENV] = encode(overlay)
    return env


# ── the child side ───────────────────────────────────────────────────────────


def throwaway_home() -> Path:
    """The throwaway per-cell home this child was pointed at — or refuse.

    The refusal is the load-bearing rail: an overlay applied against the operator's real
    home is precisely the "live config mutated" failure §3.1 forbids, and it is a spawn
    bug (missing env), not something the child can safely paper over.

    PUBLIC because a second child-side stager needs the identical rail: ES-6's gate arm
    (:mod:`personalclaw.evals.gate`) writes a candidate artifact into the same throwaway home,
    and a private copy of this check would be a second answer to "may I write here" — which is
    exactly one answer too many for a guard whose whole job is to have no exceptions.
    """
    raw = os.environ.get("PERSONALCLAW_HOME", "")
    if not raw:
        raise OverlayRefusedError(
            "refusing to apply an ablation overlay: PERSONALCLAW_HOME is unset, so the "
            "overlay would land in the operator's real home"
        )
    home = Path(raw).expanduser()
    default_home = Path.home() / ".personalclaw"
    try:
        same = home.resolve() == default_home.resolve()
    except OSError:  # pragma: no cover - resolve on a vanished parent
        same = str(home) == str(default_home)
    if same:
        raise OverlayRefusedError(
            "refusing to apply an ablation overlay: PERSONALCLAW_HOME resolves to the "
            f"default home {default_home} — a cell must run in a throwaway home"
        )
    return home


def config_field_exists(dotted: str) -> bool:
    """Does ``dotted`` name a real field on the loaded ``AppConfig``?

    Found by driving the real CLI: a ``config_flag`` overlay whose target names a field that
    does not exist wrote the key into the cell's ``config.json``, and ``AppConfig.load()``
    then DROPPED it during normalization. The arm ran with the component fully ON and scored
    identically to the baseline — a 0.0 delta indistinguishable from a component that does
    nothing, which is the exact conclusion the runner exists to draw. So a typo'd target has
    to be a refusal, not a silent no-op (the same rule ``surfacing._check_ablate`` applies to
    heuristic names).

    Checked against the live dataclass schema rather than a hardcoded list, so a new config
    field is ablatable the moment it exists.
    """
    parts = [p for p in str(dotted).split(".") if p]
    if not parts:
        return False
    try:
        from personalclaw.config.loader import AppConfig

        cursor: object = AppConfig.load()
    except Exception:
        logger.debug("config-target validation could not load config", exc_info=True)
        # Fail OPEN on an unreadable config: the pin's own load will fail louder a moment
        # later, and refusing here would blame the target for the loader's problem.
        return True
    for part in parts:
        if not hasattr(cursor, part):
            return False
        cursor = getattr(cursor, part)
    return True


def _patch_child_config(dotted: str, value: object) -> str:
    """Write ``dotted`` = ``value`` into the CHILD home's ``config.json``.

    The file is the throwaway home's, created if absent. Nested paths are created as
    needed, so a field the fixture home never wrote is still overridable.
    """
    home = throwaway_home()
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.json"
    data: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
    parts = [p for p in dotted.split(".") if p]
    if not parts:
        raise ValueError("config_flag target must be a dotted config path")
    cursor = data
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return f"config.json:{dotted}={value!r}"


def apply_in_child(overlay: ComponentOverlay | None) -> list[str]:
    """Apply ``overlay`` to THIS process and its throwaway home. Returns what changed.

    An ``on`` arm changes nothing by construction (it is the ships-as-is baseline), so it
    returns ``[]`` — which is also the honest answer for "what did the baseline mutate".

    Raises :class:`OverlayRefusedError` when the process is not pointed at a throwaway
    home, and :class:`ValueError` when a ``cheap`` arm is requested for a component that
    declares no cheaper form (a silent no-op there would be reported as "the cheap
    variant matches", i.e. a fabricated ``lighten``).
    """
    if overlay is None or overlay.arm == ARM_ON:
        return []
    # Verified BEFORE any write, for every non-baseline arm and every kind — including the
    # env-only kinds, so a cell spawned against the real home fails loudly rather than
    # running a measurement whose isolation was never established.
    throwaway_home()

    if overlay.kind == KIND_SKILL:
        if overlay.arm == ARM_CHEAP:
            raise ValueError(
                f"skill component {overlay.component_id!r} has no cheap arm: a skill body "
                "is either surfaced or suppressed"
            )
        existing = [s for s in os.environ.get(SUPPRESSED_SKILLS_ENV, "").split(",") if s]
        if overlay.target not in existing:
            existing.append(overlay.target)
        os.environ[SUPPRESSED_SKILLS_ENV] = ",".join(existing)
        return [f"env:{SUPPRESSED_SKILLS_ENV}={os.environ[SUPPRESSED_SKILLS_ENV]}"]

    if overlay.kind == KIND_SURFACING:
        if overlay.arm == ARM_CHEAP:
            raise ValueError(
                f"surfacing heuristic {overlay.component_id!r} has no cheap arm: a "
                "heuristic is either applied or ablated"
            )
        from personalclaw.learning.surfacing import ABLATABLE

        if overlay.target not in ABLATABLE:
            raise ValueError(
                f"unknown surfacing heuristic {overlay.target!r} — expected one of {ABLATABLE}"
            )
        os.environ[ABLATE_SURFACING_ENV] = overlay.target
        return [f"env:{ABLATE_SURFACING_ENV}={overlay.target}"]

    # KIND_CONFIG_FLAG
    if not config_field_exists(overlay.target):
        raise ValueError(
            f"component {overlay.component_id!r} targets config field {overlay.target!r}, which "
            "does not exist — normalization would drop it and the arm would score identically "
            "to the baseline, reporting a fabricated zero delta"
        )
    if overlay.arm == ARM_CHEAP:
        if overlay.cheap_value is None:
            raise ValueError(
                f"component {overlay.component_id!r} declares no cheap_value; a cheap arm "
                "that changed nothing would be reported as 'the cheap variant matches'"
            )
        return [_patch_child_config(overlay.target, overlay.cheap_value)]
    return [_patch_child_config(overlay.target, overlay.off_value)]
