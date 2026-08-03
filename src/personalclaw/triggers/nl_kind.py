"""`when: "…"` → a typed trigger kind + spec (§4 `automation_create` — S92).

§4 specifies the NL contract on one line: "NL-friendly: `when: "every weekday at 9"` routes
through `nl_to_cron`; `when: "when a file in ~/notes changes"` → file kind." Two different
destinations from one field, and NOTHING routes between them today.

**🔴 MEASURED FIRST — the failure this module exists to prevent.** The only NL schedule path is
`nl_to_cron`, and it is cron-shaped by construction. Fed criterion 2's own sentence:

    parse_cron_response("when a file in ~/notes changes")
      -> ("", "Could not parse a 5-field cron expression from: 'when a file in ~/note…'")

An error is the GOOD case. The bad case is a model that, asked for a cron expression and handed a
file-watch request, answers `* * * * *` — which validates, schedules, and silently turns "when a
file changes" into a per-minute poll of an LLM turn. The user asked for an event and got a
treadmill. So routing has to happen BEFORE the cron converter is ever consulted: a request that
is not a cadence must never reach a component whose only output shape is a cadence.

**Why lexical rather than a second LLM call.** The routing decision is between a handful of
declared kinds (`models.SPEC_KEYS`), the cues are concrete ("a file in …", "every …"), and a
wrong route is silent — a file request routed to `clock` becomes a poll, not an error. A pure
function is testable at every boundary without a model, and `route()` returning `("", …)` for
anything it cannot place is what keeps an unroutable request from defaulting into a schedule.
Ambiguity resolves to NO route and an explanatory error, never to a guess.

`nl_to_cron` still owns cadence→expr. This module only decides WHICH kind, and for `file`
extracts the paths, because "when a file in ~/notes changes" carries its own glob and asking the
user again for something they already said is the friction §4's one-message bar rules out.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Cues that mean "on a clock". Deliberately checked LAST: "every file in ~/notes" contains
#: "every" and is not a cadence, so a cadence cue only wins when no event cue matched.
_CLOCK_CUES = (
    "every",
    "each",
    "daily",
    "weekly",
    "monthly",
    "hourly",
    "at ",
    "am",
    "pm",
    "weekday",
    "weekend",
    "midnight",
    "noon",
    "morning",
    "evening",
    "night",
    "minutes",
    "hours",
)

#: `file` cues. `changes`/`modified` alone is not enough — "when my calendar changes" is not a
#: filesystem watch — so a path-ish token has to appear too (see `_paths_in`).
_FILE_CUES = ("file", "folder", "directory", "dir ", "path", "saved", "download")

#: Verbs that mean "something about this changed". Kept as ONE list rather than inlined, because
#: they are also the dedup hint's vocabulary: measured, `"the content of ~/notes/todo.md is
#: edited"` failed to route at all when `edited` appeared only in the dedup check and not here.
_CHANGE_CUES = (
    "changes",
    "change",
    "changed",
    "modified",
    "edited",
    "updated",
    "appears",
    "added",
    "new file",
    "gets a",
)

#: Cues for the kinds that need no extraction. Ordered most-specific first: a `run_completed`
#: request ("when my nightly run finishes") also contains "when", so a generic event cue must not
#: claim it. Each entry is (kind, cues).
_KIND_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "run_completed",
        (
            "run finishes",
            "run completes",
            "workflow finishes",
            "after the run",
            "run is done",
            "workflow completes",
        ),
    ),
    (
        "web_watch",
        ("web page", "webpage", "website", "url ", "http://", "https://", "page changes"),
    ),
    ("idle", ("idle", "nothing happens", "no activity", "away from")),
    ("webhook", ("webhook", "incoming request", "posts to")),
    (
        "event",
        ("when i ", "whenever i ", "session", "memory", "subagent", "approval", "compact", "hook"),
    ),
)

#: A glob/path token. `~`-rooted, absolute, or dotted-relative — the three shapes a user actually
#: types. A bare word is NOT a path: "when a file changes" has no path and must ask, because
#: guessing a root (the home dir, the cwd) would silently watch the wrong tree.
_PATH_RE = re.compile(r"(?:~|\.{1,2})?/[^\s,;'\"]+|~[^\s,;'\"]*")

#: A URL, matched whole. Its own pattern rather than a `_PATH_RE` special case, because the two
#: overlap: `_PATH_RE` happily matches the `//example.com/page` inside a URL (the measured
#: mis-route this exists to prevent).
_URL_RE = re.compile(r"https?://[^\s,;'\"]+")

#: What a `file` request means when it names a directory rather than a glob. `~/notes` reads as
#: "things in my notes", so it becomes `~/notes/**` — matching `file_watch`'s glob-root contract
#: rather than watching a single directory inode.
_DIR_SUFFIX = "/**"


@dataclass
class Route:
    """Where one `when:` string sends an `automation_create` call."""

    kind: str = ""
    spec: dict[str, Any] = field(default_factory=dict)
    #: Empty when routed. Non-empty is a REFUSAL to guess, phrased for the user.
    error: str = ""
    #: Set when the kind is `clock`: the cadence text to hand to `nl_to_cron`. This module does
    #: not call the converter — keeping it a pure function is what makes every branch testable
    #: without a model.
    cadence: str = ""
    #: Why this kind was chosen, echoed back to the user. §4 requires agent-created triggers be
    #: "announced to the user on creation", and "routed to file because you named ~/notes" is what
    #: makes a wrong route correctable instead of mysterious.
    because: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.kind) and not self.error

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "spec": dict(self.spec),
            "error": self.error,
            "cadence": self.cadence,
            "because": self.because,
            "ok": self.ok,
        }


def _paths_in(text: str) -> list[str]:
    """Path-ish tokens in a request, normalized to globs.

    A directory becomes `<dir>/**` because `file_watch` expands GLOBS, not directory roots — a
    bare `~/notes` would watch the directory inode and miss every file inside it, which is the
    silent-no-fire this normalization prevents.
    """
    found: list[str] = []
    for raw in _PATH_RE.findall(text):
        token = raw.rstrip(".,;:!?)")
        if not token or token in ("/", "~"):
            continue
        if any(ch in token for ch in "*?["):
            glob = token
        elif re.search(r"\.[A-Za-z0-9]{1,6}$", token):
            glob = token  # a concrete filename — watch exactly it
        else:
            glob = token.rstrip("/") + _DIR_SUFFIX
        if glob not in found:
            found.append(glob)
    return found


def _url_in(text: str) -> str:
    """The first URL in a request, trailing sentence punctuation stripped."""
    match = _URL_RE.search(text)
    return match.group(0).rstrip(".,;:!?)") if match else ""


def _has(text: str, cues: tuple[str, ...]) -> bool:
    return any(cue in text for cue in cues)


def route(when: str) -> Route:
    """`when:` text → a typed `Route`. Pure: no LLM, no store, no side effects.

    Order matters and is the whole design. Event-shaped kinds are checked BEFORE cadence cues,
    because a cadence cue is a substring of many event requests ("every file in ~/notes") while
    the reverse is not true. Checking cadence first would route those to `clock` and produce the
    per-minute-poll defect this module was written to prevent.

    An unroutable request returns `("", error)` rather than defaulting to `clock`. A default of
    "it's probably a schedule" is exactly how "when a file changes" becomes `* * * * *`.
    """
    text = (when or "").strip()
    if not text:
        return Route(error="Say when the automation should run.")
    low = text.lower()

    # 🔴 A URL is checked BEFORE paths. Measured: `_PATH_RE` matches the `//example.com/page` in
    # `https://example.com/page`, so a web-watch request routed to `file` and produced the glob
    # `//example.com/page/**` — a filesystem watch on a path that cannot exist, which would never
    # fire and never explain why. A scheme is unambiguous, so it wins outright.
    if _has(low, ("http://", "https://")):
        return Route(
            kind="web_watch",
            spec={"url": _url_in(text)},
            because="routed to the web_watch kind because you gave a URL",
        )

    # ── file: needs BOTH a file-ish cue and a path, or an unambiguous path alone ──
    paths = _paths_in(text)
    file_cue = _has(low, _FILE_CUES)
    if paths and (file_cue or _has(low, _CHANGE_CUES)):
        spec: dict[str, Any] = {"paths": paths}
        if _has(low, ("content", "text changes", "edited")):
            spec["dedup"] = "content"
        return Route(
            kind="file",
            spec=spec,
            cadence="",
            because="read as a file watch because you named a path",
        )
    if file_cue and not paths:
        # 🔴 Refuse rather than guess a root. Watching the wrong tree is worse than asking:
        # `~/**` is the `broad_watch_glob` failure `automation doctor` flags, and a cwd-rooted
        # guess watches whatever directory the gateway happens to have started in.
        return Route(
            error=(
                "Which path should I watch? Name a folder or glob "
                "(e.g. ~/notes or ~/notes/**/*.md) — I will not guess a root."
            )
        )

    # ── the kinds that need no extraction, most-specific first ──
    for kind, cues in _KIND_CUES:
        if _has(low, cues):
            return Route(
                kind=kind,
                spec={},
                because=f"routed to the {kind} kind from the wording of your request",
            )

    # ── clock LAST: only when nothing event-shaped matched ──
    if _has(low, _CLOCK_CUES) or re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)?\b", low):
        return Route(
            kind="clock",
            spec={},
            cadence=text,
            because="read as a schedule; converting the cadence to a cron expression",
        )

    return Route(
        error=(
            f"I could not tell what should trigger this from {text!r}. "
            "Give a cadence ('every weekday at 9') or an event "
            "('when a file in ~/notes changes')."
        )
    )
