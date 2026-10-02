"""Versions: how an app's version and the core version it needs are read and compared.

The one app-version comparator (:func:`version_tuple`), the strict reading of a version
(:func:`strict_version_tuple`) and the check of an app's ``minPersonalClawVersion`` against the
running core (:func:`check_core_version`), beside the shape every manifest version is validated
against (:data:`SEMVER_RE`). Kept apart from ``apps.manifest`` so that module stays one reading of
``app.json``; it imports these.
"""

import re
from dataclasses import dataclass
from typing import Any

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([+-]|$)")


def version_tuple(v: str) -> tuple[int, ...]:
    """Parse an app semver string to a numeric tuple for comparison (best-effort).

    Manifests are validated against ``SEMVER_RE`` (``MAJOR.MINOR.PATCH`` with an optional
    ``+build``/``-pre`` suffix), so the core is the dotted release. The pre-release/build
    suffix is dropped (SemVer pre-release ordering is out of scope for "is a newer release
    available"), and a leading ``v`` is tolerated. A value that can't be parsed sorts as
    ``(0,)`` so a malformed version never falsely reads as an available update.

    This is the ONE app-version comparator (``apps.catalog`` reuses it) — the version
    field and ``SEMVER_RE`` both live here, so the comparator does too, rather than
    inverting the apps→dashboard layering to borrow the self-updater's tag comparator.
    """
    core = (v or "").strip()
    core = core[1:] if core[:1] == "v" else core
    core = core.split("+", 1)[0].split("-", 1)[0]
    try:
        return tuple(int(x) for x in core.split("."))
    except (ValueError, AttributeError):
        return (0,)


def strict_version_tuple(v: str) -> tuple[int, ...] | None:
    """``v`` as a comparable tuple, or ``None`` when it is not a valid app semver.

    :func:`version_tuple` is deliberately best-effort — it sorts junk as ``(0,)`` so a
    malformed version never reads as an available update. That collapse is exactly wrong
    for a *floor*: a floor of ``(0,)`` is satisfied by every core, so the same helper
    would turn a typo into a silently-disabled gate. This variant keeps the parse and the
    verdict separate: ``None`` means "unmeasurable", and the caller decides which way
    that falls. Shape is ``SEMVER_RE`` (``MAJOR.MINOR.PATCH`` + optional ``-pre``/
    ``+build``), with the same leading-``v`` tolerance and the same suffix drop, so there
    is still ONE notion of an app version string in this module.
    """
    core = (v or "").strip()
    core = core[1:] if core[:1] == "v" else core
    if not SEMVER_RE.match(core):
        return None
    return version_tuple(core)


# ---------------------------------------------------------------------------
# Core-version compatibility — the ``minPersonalClawVersion`` gate
# ---------------------------------------------------------------------------

# Four states rather than a boolean, because "I could not measure the host" is a
# different answer from "this app is incompatible" and must not be conflated:
#
#   ok                    nothing declared, or the running core satisfies the floor
#   invalid               a floor was declared but is not a parseable app semver
#   unknown_host_version  the floor parses, the RUNNING core's own version does not
#   incompatible          both parse and the running core is OLDER than the floor
#
# Only ``incompatible`` refuses. The other two FAIL OPEN, with a warning, on purpose:
#
# * ``invalid`` — a typo in one advisory metadata field must not brick an app whose code
#   is fine, and a floor is not a security control (a hostile app simply omits it), so
#   there is nothing to protect by refusing. It is not *silently* permissive either: the
#   state is distinct, logged, and carried on the Store card, which is the substance of
#   "a malformed value must not read as satisfied".
# * ``unknown_host_version`` — an unmeasurable host is not a reason to refuse an
#   otherwise-fine install. This is the normal state of a source checkout / editable dev
#   install, whose ``__version__`` can read like ``0.2.0.dev3+g9a1c``; refusing there
#   would make every contributor's tree reject every app that declares a floor at all.
CORE_COMPAT_OK = "ok"
CORE_COMPAT_INVALID = "invalid"
CORE_COMPAT_UNKNOWN_HOST = "unknown_host_version"
CORE_COMPAT_INCOMPATIBLE = "incompatible"


def host_core_version() -> str:
    """The running core's version — what a declared floor is compared against.

    Imported lazily so this module keeps its stdlib-only import surface (it is parsed
    by the CLI scaffolder and by catalog scans that must not pull the world in)."""
    from personalclaw import __version__

    return str(__version__)


@dataclass(frozen=True)
class CoreCompatibility:
    """Whether the running core satisfies an app's declared ``minPersonalClawVersion``."""

    state: str = CORE_COMPAT_OK
    required: str = ""  # the floor the manifest declared ("" when it declared none)
    host: str = ""  # the running core's version, as reported

    @property
    def admits(self) -> bool:
        """Whether an app carrying this verdict may be installed / enabled / started."""
        return self.state != CORE_COMPAT_INCOMPATIBLE

    @property
    def reason(self) -> str:
        """A user-facing sentence, or ``""`` for :data:`CORE_COMPAT_OK`.

        Names BOTH versions in every non-``ok`` state, and — where the user can act —
        says what to do next. Prefixed with the app name by the caller, which is the
        layer that knows it."""
        if self.state == CORE_COMPAT_INCOMPATIBLE:
            return (
                f"requires PersonalClaw {self.required} or newer, but this core is "
                f"{self.host}. Upgrade the core — run `personalclaw update` — then "
                f"try again."
            )
        if self.state == CORE_COMPAT_INVALID:
            return (
                f"declares minPersonalClawVersion {self.required!r}, which is not a "
                f"MAJOR.MINOR.PATCH version, so its core-version floor cannot be "
                f"checked against this core ({self.host}) and is being ignored. Fix the "
                f"value in app.json to restore the gate."
            )
        if self.state == CORE_COMPAT_UNKNOWN_HOST:
            return (
                f"requires PersonalClaw {self.required} or newer; this core reports "
                f"{self.host!r}, which is not a comparable version, so the floor cannot "
                f"be checked and is being allowed."
            )
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "required": self.required,
            "host": self.host,
            "reason": self.reason,
        }


def check_core_version(required: str, host: str | None = None) -> CoreCompatibility:
    """Evaluate a declared ``minPersonalClawVersion`` against the running core.

    THE one owner of this decision. Every path that puts an app into effect
    (install, update, enable, gateway boot) routes here rather than re-deriving the
    comparison, so there is a single place where "which way does an unparseable value
    fall" is answered. ``host`` is injectable for tests; production passes ``None``.
    """
    host_version = host_core_version() if host is None else str(host)
    declared = (required or "").strip()
    if not declared:
        # An app that declares nothing must keep working — silence is not a floor.
        return CoreCompatibility(CORE_COMPAT_OK, "", host_version)
    want = strict_version_tuple(declared)
    if want is None:
        return CoreCompatibility(CORE_COMPAT_INVALID, declared, host_version)
    have = strict_version_tuple(host_version)
    if have is None:
        return CoreCompatibility(CORE_COMPAT_UNKNOWN_HOST, declared, host_version)
    if have < want:
        return CoreCompatibility(CORE_COMPAT_INCOMPATIBLE, declared, host_version)
    return CoreCompatibility(CORE_COMPAT_OK, declared, host_version)
