"""Enforcer-owned registries for the governance ceiling.

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
allow (or shrinking a deny). What a pattern's ``~`` and ``$VAR`` expand to is not glob text, so
it is tidied as the item is (:func:`expand_pattern`); the rest of the pattern is never touched.
Two properties this buys:

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
    return _one_root(os.path.abspath(expanded))


def _one_root(path: str) -> str:
    """*path* read from ``/`` when it starts with two or more slashes, as the filesystem reads it.
    Applied to the queried path and to a rule alike, so the two agree."""
    return "/" + path.lstrip("/") if path.startswith("//") else path


#: A ``$VAR``, ``${VAR}`` or unclosed ``${...`` reference, read as :func:`os.path.expandvars`
#: reads one (the stdlib's ``posixpath._varpattern``, ``\$(\w+|\{[^}]*\}?)``), so a rule's
#: variables are the ones the queried path's are.
_VAR_REF = re.compile(r"\$(\w+|\{[^}]*\}?)", re.ASCII)


def _tidy(value: str, *, glued_before: bool = False, glued_after: str = "") -> str:
    """What one ``~`` or ``$VAR`` expanded to, collapsed lexically as the queried path is: a
    ``//``, ``/./`` or ``/../`` inside it goes. Text of the pattern's own that is glued to it, with
    no ``/`` between, stays on the segment it was glued to: the value's first segment is kept as
    written when text comes before it (*glued_before*), and its last when text comes after it
    (*glued_after* ``"text"``). A trailing ``/`` it ended with stays, since it marks where the
    folder ends for text that follows, and so does a last ``.`` or ``..`` segment when a ``*`` or
    ``?`` follows (*glued_after* ``"wild"``): ``$VAR**`` with ``/srv/data/`` or ``/srv/data/.`` is
    the tree under ``/srv/data``, not every name that starts ``/srv/data``."""
    if not value:
        return value
    lead, body, tail = "", value, ""
    if glued_after == "text":
        cut = body.rfind("/") + 1
        if cut == 0:
            return value
        body, tail = body[:cut], body[cut:]
    if glued_before:
        cut = body.find("/")
        if cut < 0:
            return value
        lead, body = body[:cut], body[cut:]
    tidy = _one_root(os.path.normpath(body))
    marks_folder = body.endswith("/") or (
        glued_after == "wild" and os.path.basename(body) in (".", "..")
    )
    if marks_folder and not tidy.endswith("/"):
        tidy += "/"
    return lead + tidy + tail


def _glued(piece: str) -> str:
    """How the pattern text *piece* that follows an expansion meets it: ``""`` when it starts with
    a ``/`` or there is none, ``"wild"`` when it starts with a ``*`` or ``?``, else ``"text"``."""
    if not piece or piece.startswith("/"):
        return ""
    return "wild" if piece[0] in "*?" else "text"


def expand_pattern(pattern: str) -> str:
    """*pattern* with its leading ``~`` (or ``~user``) and each ``$VAR`` replaced by what it
    expands to, each expansion tidied (:func:`_tidy`) and joined to the text beside it with one
    ``/`` where both bring one.

    Only the expansions change. The pattern's own text, its glob tokens and any ``..`` it spells
    included, is kept exactly as written: it is never put through ``normpath`` (the rule above).
    Without the tidying, an expansion holding ``//``, ``/./`` or ``/../``, or a variable ending
    in ``/`` as macOS's ``TMPDIR`` does, spelled the pattern one way and the normalized path
    another, and the rule matched nothing. A variable that ends the rule names its folder, so the
    ``/`` it ended with goes there; a variable set to nothing leaves one ``/`` between the text
    around it; and a variable that is not set is left spelled out, as ``expandvars`` leaves it.
    """
    pat = (pattern or "").strip()
    pieces: list[tuple[str, bool]] = []  # (text, whether it is an expansion)
    if pat.startswith("~"):
        head = pat.split("/", 1)[0]
        home = os.path.expanduser(head)
        if home != head:
            # The home's own `$VAR`s expand too, as they do in the queried path.
            pieces.append((os.path.expandvars(home), True))
            pat = pat[len(head) :]
    at = 0
    for ref in _VAR_REF.finditer(pat):
        pieces.append((pat[at : ref.start()], False))
        value = os.path.expandvars(ref.group(0))
        pieces.append((value, value != ref.group(0)))
        at = ref.end()
    pieces.append((pat[at:], False))
    out = ""
    joined_expansion = False
    for i, (text, is_expansion) in enumerate(pieces):
        if is_expansion:
            after = next((t for t, _ in pieces[i + 1 :] if t), "")
            text = _tidy(
                text, glued_before=bool(out) and not out.endswith("/"), glued_after=_glued(after)
            )
        if not text:
            joined_expansion = joined_expansion or is_expansion
            continue
        if (is_expansion or joined_expansion) and out.endswith("/") and text.startswith("/"):
            text = text[1:]
        out += text
        joined_expansion = is_expansion
    if joined_expansion and len(out) > 1 and out.endswith("/"):
        out = out[:-1]
    return _one_root(out)


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """*pattern*'s regex, for the environment it is asked in: the cache is keyed on the expanded
    pattern (:func:`expand_pattern`), so a home or a variable that changes after a first match is
    read as it is now."""
    return _compiled(expand_pattern(pattern))


@lru_cache(maxsize=512)
def _compiled(pat: str) -> re.Pattern[str]:
    """Compile an expanded path glob to a regex WITHOUT ever normpath-ing the pattern.

    ``**`` crosses separators, ``*``/``?`` do not. An expanded pattern that starts with neither
    ``/`` nor ``*`` (a relative one, a relative value of ``~`` or ``$VAR``, a ``$VAR`` that is not
    set, a ``~name`` that names no user) is treated as matching at any depth — i.e. implicitly
    ``**/``-prefixed — so an operator's ``id_rsa`` deny holds at every depth. That direction
    fails CLOSED for a deny, which is why it is the default.
    """
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
