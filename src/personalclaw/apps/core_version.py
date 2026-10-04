"""An app's versions: the version it declares, and the core it needs.

The rule an app's own ``version`` is held to (:func:`app_version_problem`), and the check of its
``minPersonalClawVersion`` and ``requiresCoreFeatures`` against the running core
(:func:`check_core_compatibility`). Versions are read and ordered by :mod:`personalclaw.versions`,
the one comparison every version check uses, so a release candidate of core is older than its
release (``0.3.0rc1 < 0.3.0``) and a dev build older still. Kept apart from ``apps.manifest`` so
that module stays one reading of ``app.json``; it imports these.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from personalclaw.apps.core_features import core_has
from personalclaw.versions import parse_version


def app_version_problem(version: str) -> str:
    """Why *version* is not an app version, or ``""`` when it is.

    An app's version is ``MAJOR.MINOR.PATCH``, as written: three release numbers, starting with
    a digit (the Store shows it after a ``v``), and read by the one version comparison
    (:mod:`personalclaw.versions`), which is what the Store offers an update by. So a pre-release
    or a dev build is one the comparison orders (``1.0.0-rc.1``, ``1.0.0b2``, ``1.0.0.dev3``), and
    a suffix it cannot read (``1.0.0-foo``) is refused here rather than being an app whose
    updates are never offered.
    """
    as_written = version[:1].isdigit() and version == version.strip()
    parsed = parse_version(version) if as_written else None
    if parsed is None or len(parsed.release) != 3:
        return (
            "version must be MAJOR.MINOR.PATCH, optionally with a pre-release such as "
            f"1.0.0-rc.1, got: {version!r}"
        )
    return ""


# ---------------------------------------------------------------------------
# Core compatibility — the ``minPersonalClawVersion`` and ``requiresCoreFeatures`` gate
# ---------------------------------------------------------------------------

# Four states rather than a boolean, because each refusal says something different:
#
#   ok                    nothing declared, or the running core is at least the floor
#   invalid               a floor was declared that is not a version
#   unknown_host_version  the floor is a version, the RUNNING core's own version is not
#   incompatible          the running core is older than the floor, or the app needs a core
#                         feature (``apps.core_features``) this core does not offer
#
# A missing feature is ``incompatible`` whatever the floor says: every core built between two
# releases reads the same version, so a feature is the only thing that tells such cores apart. A
# feature name this core has never heard of is one from a newer core, so it refuses as well.
#
# Only ``ok`` admits. A version that cannot be read fails CLOSED, with a sentence naming both
# versions: a gate that cannot read its operands has no answer, and admitting on no answer is how
# every release candidate came to wave every floor through, when the old rule could not read
# ``0.3.0rc1`` and the check was skipped. Refusing says what to fix; admitting lets an app built
# for a newer core run on one without what it needs. Neither state comes from a working install:
# the comparison reads every version a release, a candidate, a dev build or a source checkout
# reports, and every floor spelled as a version (``0.3``, ``v0.3.0``, ``0.3.0-rc.1``).
CORE_COMPAT_OK = "ok"
CORE_COMPAT_INVALID = "invalid"
CORE_COMPAT_UNKNOWN_HOST = "unknown_host_version"
CORE_COMPAT_INCOMPATIBLE = "incompatible"


def host_core_version() -> str:
    """The running core's version — what a declared floor is compared against.

    Read from the root package at every call, never bound at import, so every check compares
    the version the package reports (and a test pins it in that one place)."""
    from personalclaw import __version__

    return str(__version__)


@dataclass(frozen=True)
class CoreCompatibility:
    """Whether the running core can host an app: its declared ``minPersonalClawVersion``, and the
    core features it declares it needs (``requiresCoreFeatures``)."""

    state: str = CORE_COMPAT_OK
    required: str = ""  # the floor the manifest declared ("" when it declared none)
    host: str = ""  # the running core's version, as reported
    #: The declared core features this core does not offer, in the order the app declared them.
    missing: tuple[str, ...] = ()

    @property
    def admits(self) -> bool:
        """Whether an app carrying this verdict may be installed / enabled / started."""
        return self.state == CORE_COMPAT_OK

    @property
    def reason(self) -> str:
        """A user-facing sentence, or ``""`` for :data:`CORE_COMPAT_OK`.

        Names BOTH versions in every refusal, and every core feature the app needs that this core
        lacks, and says what to do next. Prefixed with the app name by the caller, which is the
        layer that knows it."""
        if self.state == CORE_COMPAT_INCOMPATIBLE:
            lacks = _lacking(self.missing)
            if _below_floor(self.required, self.host):
                needs = (
                    f"requires PersonalClaw {self.required} or newer, but this core is {self.host}"
                )
                needs += f" and {lacks}" if lacks else ""
            else:
                needs = f"needs a newer PersonalClaw: this core ({self.host}) {lacks}"
            return f"{needs}. Upgrade the core — run `personalclaw update` — then try again."
        if self.state == CORE_COMPAT_INVALID:
            return (
                f"declares minPersonalClawVersion {self.required!r}, which is not a version, so "
                f"this core ({self.host}) cannot tell whether it is new enough. The app's "
                "app.json must name a version there, such as 0.3.0."
            )
        if self.state == CORE_COMPAT_UNKNOWN_HOST:
            return (
                f"requires PersonalClaw {self.required} or newer, but this core reports its "
                f"version as {self.host!r}, which is not a version, so it cannot tell whether it "
                "is new enough. Reinstall PersonalClaw, then try again."
            )
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "required": self.required,
            "host": self.host,
            "missing": list(self.missing),
            "reason": self.reason,
        }


def _below_floor(required: str, host: str) -> bool:
    """Whether both versions read and *host* is older than the floor *required*."""
    want, have = parse_version(required), parse_version(host)
    return want is not None and have is not None and have < want


def _lacking(missing: tuple[str, ...]) -> str:
    """``does not have the core feature 'x' it relies on``, or ``""`` when nothing is missing."""
    if not missing:
        return ""
    quoted = [repr(name) for name in missing]
    if len(quoted) == 1:
        return f"does not have the core feature {quoted[0]} it relies on"
    named = ", ".join(quoted[:-1]) + f" and {quoted[-1]}"
    return f"does not have the core features {named} it relies on"


def check_core_compatibility(
    required: str = "", features: Sequence[str] = (), *, host: str | None = None
) -> CoreCompatibility:
    """Evaluate what an app needs from core against the running core: its declared
    ``minPersonalClawVersion`` (*required*) and the core features it declares it needs
    (*features*, its ``requiresCoreFeatures``).

    THE one owner of this decision. Every path that puts an app into effect (review, install,
    update, enable, gateway boot) and the Store's card route here rather than re-deriving the
    comparison, so there is a single place where "which way does an unreadable version fall" is
    answered: it refuses. A feature this core does not offer refuses whatever the floor says (the
    four-state note above). ``host`` is injectable for tests; production passes ``None``.
    """
    host_version = host_core_version() if host is None else str(host)
    declared = (required or "").strip()
    missing = tuple(f for f in dict.fromkeys(features) if not core_has(f))
    if missing:
        return CoreCompatibility(CORE_COMPAT_INCOMPATIBLE, declared, host_version, missing)
    if not declared:
        # An app that declares nothing must keep working — silence is not a floor.
        return CoreCompatibility(CORE_COMPAT_OK, "", host_version)
    want = parse_version(declared)
    if want is None:
        return CoreCompatibility(CORE_COMPAT_INVALID, declared, host_version)
    have = parse_version(host_version)
    if have is None:
        return CoreCompatibility(CORE_COMPAT_UNKNOWN_HOST, declared, host_version)
    if have < want:
        return CoreCompatibility(CORE_COMPAT_INCOMPATIBLE, declared, host_version)
    return CoreCompatibility(CORE_COMPAT_OK, declared, host_version)
