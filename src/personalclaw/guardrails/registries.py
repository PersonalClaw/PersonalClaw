"""Enforcer-owned registries for the governance ceiling (PLATFORM-HARDENING-FLOORS §5).

Matchers and ordinal scales live HERE, in code, and are never sourced from a governed
file. That is the whole point: if a ceiling (or a profile) could name its own matcher
implementation or reorder a strictness scale, it could redefine what "stricter" means and
widen itself while looking narrower. A ceiling may only *reference* a matcher/scale by
name; an unknown name is a boot abort (:class:`UnknownRegistryEntry`), never a fallback.

**The path-matcher rule — our own recorded landmine, encoded here once.** Normalize only
the QUERIED ITEM (expand ``~``/``$VAR``, then ``abspath``, which anchors a relative path
and collapses ``.``/``..``). NEVER run the *pattern* through ``normpath``: ``normpath``
treats ``*``/``**`` as ordinary path segments and collapses an adjacent ``..`` against
them, so ``/a/**/../b`` becomes ``/a/b`` — silently dropping the ``**`` and widening an
allow (or shrinking a deny). Two properties this buys:

1. ``~/ws/../.bashrc`` collapses to ``~/.bashrc`` and no longer matches an allow of
   ``~/ws/**``;
2. an agent-supplied relative ``../../etc/passwd`` is absolutized, so it cannot dodge a
   deny of ``/etc/**`` by failing to match as a string.

``tests/test_guardrails_path_matcher.py`` is the table-driven encoding of the rule: an
implementation that normpaths the pattern reds it.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Callable
from functools import lru_cache

from personalclaw.errors import AgentError

#: A matcher answers "does this item match this pattern?" — pure, no I/O.
Matcher = Callable[[str, str], bool]


class UnknownRegistryEntry(Exception):
    """A ceiling referenced a matcher or ordinal scale the enforcer does not own.

    Carries an :class:`~personalclaw.errors.AgentError` so the boot abort renders
    WHAT/WHY/FIX. Raised by :func:`get_matcher` / :func:`get_scale` — never swallowed
    into a default, because a silently-substituted matcher is a widened rule.
    """

    def __init__(self, error: AgentError) -> None:
        super().__init__(error.render())
        self.error = error


# ── path matcher ──────────────────────────────────────────────────────────────


def normalize_item(item: str) -> str:
    """Normalize a QUERIED path: expand ``~``/``$VAR``, then absolutize.

    ``abspath`` anchors a relative path against the cwd and collapses ``.``/``..``
    lexically. It deliberately does NOT resolve symlinks — a matcher must be pure and
    must not stat the filesystem (a rule has to be checkable for a path that does not
    exist yet, e.g. a write destination).
    """
    expanded = os.path.expandvars(os.path.expanduser((item or "").strip()))
    return os.path.abspath(expanded)


@lru_cache(maxsize=512)
def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """Compile a path glob to a regex WITHOUT ever normpath-ing the pattern.

    ``**`` crosses separators, ``*``/``?`` do not. A pattern that is not anchored
    (``/``, ``~``, ``$VAR`` or a leading wildcard) is treated as matching at any depth —
    i.e. implicitly ``**/``-prefixed — so an operator's ``id_rsa`` deny cannot be dodged
    by depth. That direction fails CLOSED for a deny, which is why it is the default.
    """
    pat = os.path.expandvars(os.path.expanduser((pattern or "").strip()))
    if not pat.startswith(("/", "*")):
        pat = f"**/{pat}"
    out: list[str] = []
    i = 0
    while i < len(pat):
        ch = pat[i]
        if ch == "*":
            if pat.startswith("**/", i):
                # '**/' also matches zero segments: '**/x' matches '/x'.
                out.append(r"(?:.*/)?")
                i += 3
                continue
            if pat.startswith("**", i):
                out.append(r".*")
                i += 2
                continue
            out.append(r"[^/]*")
            i += 1
            continue
        if ch == "?":
            out.append(r"[^/]")
            i += 1
            continue
        out.append(re.escape(ch))
        i += 1
    return re.compile(f"^{''.join(out)}$")


def path_glob(item: str, pattern: str) -> bool:
    """Match a filesystem path against a glob. ITEM is normalized; PATTERN is not."""
    if not pattern:
        return False
    return bool(_pattern_regex(pattern).match(normalize_item(item)))


def name_glob(item: str, pattern: str) -> bool:
    """Match a bare NAME (a tool name, a provider key) — no path semantics at all.

    Separate from :func:`path_glob` because a tool name is not a path: absolutizing
    ``bash`` against the cwd would be nonsense, and ``*`` must match freely.
    """
    return fnmatch.fnmatchcase((item or "").strip(), (pattern or "").strip())


def exact(item: str, pattern: str) -> bool:
    """Literal equality — the matcher with no metacharacters at all."""
    return (item or "").strip() == (pattern or "").strip()


MATCHER_PATH_GLOB = "path_glob"
MATCHER_NAME_GLOB = "name_glob"
MATCHER_EXACT = "exact"

_MATCHERS: dict[str, Matcher] = {
    MATCHER_PATH_GLOB: path_glob,
    MATCHER_NAME_GLOB: name_glob,
    MATCHER_EXACT: exact,
}


def matcher_names() -> tuple[str, ...]:
    """Every matcher name a ceiling may reference (sorted, for the error's suggestions)."""
    return tuple(sorted(_MATCHERS))


def get_matcher(name: str) -> Matcher:
    """Resolve a matcher by name. Unknown → :class:`UnknownRegistryEntry` (fail closed)."""
    matcher = _MATCHERS.get(name)
    if matcher is None:
        raise UnknownRegistryEntry(
            AgentError(
                code="ERR_GOVERNANCE_UNKNOWN_MATCHER",
                what=f"The governance ceiling names a matcher {name!r} that does not exist.",
                why=(
                    "Matchers are owned by the enforcer, not by the ceiling file — a rule that "
                    "could name its own matching implementation could redefine what it matches "
                    "and widen itself. An unrecognized matcher therefore aborts boot instead of "
                    "falling back to a default that would silently match differently."
                ),
                fix=(
                    'Set the rule\'s "matcher" to one of: '
                    f"{', '.join(matcher_names())} — or remove the key to take the scope's "
                    "default matcher."
                ),
                suggestions=matcher_names(),
            )
        )
    return matcher


# ── ordinal scales (loosest → strictest) ──────────────────────────────────────

#: How much a run may decide alone. ``auto`` = no gate, ``hook_based`` = the security
#: hook decides, ``ask`` = a human must approve. Strictness ascends left to right, so
#: strictest-of a ceiling ``ask`` and a profile ``auto`` is ``ask``.
SCALE_APPROVAL = ("auto", "hook_based", "ask")

#: Secret-scan posture: warn (audit only) → redact (rewrite) → block (refuse).
SCALE_SCAN = ("warn", "redact", "block")

#: Egress reach, by REACHABLE HOST SET containment as the enforcer implements it:
#: ``all`` = every public host; ``registry`` = the shipped package-registry preset UNION
#: the operator's ``security.egress.allow_hosts``; ``listed`` = the operator's allow_hosts
#: only; ``off`` = nothing. ``listed ⊆ registry ⊂ all`` holds because
#: ``net.policy.egress_policy_for`` unions the operator's hosts into every base, so the
#: order is a real containment chain rather than a naming convention.
SCALE_EGRESS = ("all", "registry", "listed", "off")

#: A ruleset's default stance: ``open`` allows unless denied; ``closed`` denies unless
#: allowed. Closed is strictly stricter, so strictest-of always yields ``closed``.
SCALE_RULESET_MODE = ("open", "closed")

SCALE_NAMES: dict[str, tuple[str, ...]] = {
    "approval": SCALE_APPROVAL,
    "scan": SCALE_SCAN,
    "egress": SCALE_EGRESS,
    "ruleset_mode": SCALE_RULESET_MODE,
}


def scale_names() -> tuple[str, ...]:
    return tuple(sorted(SCALE_NAMES))


def get_scale(name: str) -> tuple[str, ...]:
    """Resolve an ordinal scale by name. Unknown → :class:`UnknownRegistryEntry`."""
    scale = SCALE_NAMES.get(name)
    if scale is None:
        raise UnknownRegistryEntry(
            AgentError(
                code="ERR_GOVERNANCE_UNKNOWN_SCALE",
                what=f"The governance ceiling names an ordinal scale {name!r} that does not exist.",
                why=(
                    "Strictness scales are owned by the enforcer so no governed file can reorder "
                    "them; reordering a scale would turn a narrowing into a widening while every "
                    "rule still read as 'strictest wins'."
                ),
                fix=f"Use one of the enforcer's scales: {', '.join(scale_names())}.",
                suggestions=scale_names(),
            )
        )
    return scale


def rank(scale: tuple[str, ...], value: str) -> int:
    """The strictness rank of ``value`` on ``scale``. Unknown value → strictest.

    An unrecognized VALUE cannot fail open: a ceiling that says ``egress: "banana"``
    must not become "unbounded". Parsing rejects unknown values up front
    (WHAT/WHY/FIX), so this branch is the belt to that suspenders — and it clamps to
    the strictest rung.
    """
    try:
        return scale.index(value)
    except ValueError:
        return len(scale) - 1


def strictest(scale: tuple[str, ...], *values: str) -> str:
    """The strictest of ``values`` on ``scale`` — the ordinal compose function."""
    present = [v for v in values if v]
    if not present:
        return scale[0]
    return max(present, key=lambda v: rank(scale, v))
