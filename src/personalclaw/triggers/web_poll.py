"""The web_watch poll runtime — what actually FIRES a `web_watch` trigger (§7 item 8 — S121).

**🔴 THE DEFECT THIS CLOSES.** `web_watch` is a declared kind: it is in `KINDS`, `SPEC_KEYS` accepts
`{url, poll_interval, extraction, novelty_key}`, `nl_kind.route()` routes any URL to it, the store
persists it, `/api/triggers` lists it (S94) and the Automations page renders it (S95). Nothing polls
it. Measured before writing a line:

    T.create(store, name="watch pypi", when="watch https://pypi.org/... for changes")
      → ok: True   "Created automation 'watch pypi' (web_watch:watch-pypi), kind web_watch."

    tick()                       → considered: none      (no `next_fire_at`; not a clock kind)
    file_poll.file_triggers()    → ['file:t']            (only `file`)

So a user could ask for exactly what the plan advertises, be told it worked, see it in the UI — and
it would never fire. Same shape as S93's file-watch gap, one kind over: present, listable, inert.

**The seen-set IS the storm guard**, in §3's own words. A page that changes on every fetch (a
timestamp, a rotating ad, a CSRF token) must not produce a fire per poll. So novelty is keyed on
EXTRACTED ITEMS, not on the raw body: a body hash would treat any byte change as news, which is the
failure mode that turns one watch into a notification every minute.

**Fetching goes through `net.fetch`** — the egress chokepoint — never `urllib`/`httpx` directly.
That applies host classification, private-IP denial, the redirect-hop re-check, the byte cap and
the timeout. A watch pointed at `http://169.254.169.254/` is an SSRF against the machine's own
metadata service, and the chokepoint is where that is already refused; re-implementing a fetch here
would bypass every one of those controls.

**A daily request budget, enforced.** §3 asks for one, and without it a `poll_interval` of 60 on a
handful of watches is a few thousand requests a day at someone else's server. The budget is counted
in the sidecar and refuses with a ledger-visible reason rather than silently skipping.

**State is a SIDECAR**, matching `file_poll`'s reasoning exactly: a seen-set is high-churn runtime
state, and writing it onto the trigger entity would rewrite `triggers.json` on every poll and race
every unrelated edit.

**What this does NOT own:** the LLM turn (the executor, injected as a runner by the gateway), item
extraction beyond the two shipped strategies, and the headless-browser escalation tier §3 mentions —
that needs a browser runtime this repo does not have, and a stub would be an inert control.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: How often the poll loop wakes to LOOK for due watches. Not how often a watch is fetched — that is
#: each trigger's own `poll_interval`. Kept at a minute so a 5-minute watch fires within a minute of
#: its slot without the loop itself becoming a busy wait.
POLL_INTERVAL_SECS = 60.0

#: The floor a `poll_interval` is clamped to. §3's R1-class rate floor, applied here because this is
#: the one trigger kind that makes requests to SOMEONE ELSE'S server: a 5-second watch is abusive to
#: the target and indistinguishable from a scraper. S109 recorded that the R1 floor was declared but
#: read by no code, so this one is enforced at the point of use rather than only validated.
MIN_POLL_INTERVAL_SECS = 300.0

#: Requests one watch may make in a rolling day. Without a cap, a handful of minute-interval watches
#: is thousands of daily requests at a third party, from a machine the user left running.
MAX_REQUESTS_PER_DAY = 288  # one per 5 minutes, the floor above

#: How many extracted item keys a watch remembers. Bounded because the seen-set is the storm guard
#: and an unbounded one grows without limit on a busy feed; the newest are kept, so re-appearing old
#: items may re-fire once after a very long absence — which is the correct trade against a file that
#: grows forever.
MAX_SEEN_KEYS = 500

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")

#: Anchor hrefs and RSS/Atom entry ids — the two shapes carrying a stable per-item identity without
#: parsing a whole document. `auto` tries both. Deliberately NOT a general HTML parser: an
#: approximate parser that silently changes what it counts as an "item" between versions would make
#: novelty non-deterministic, and novelty is the entire control here.
_ITEM_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"<(?:guid|id)[^>]*>\s*([^<\s][^<]*?)\s*</(?:guid|id)>", re.I),
    re.compile(r"<a\b[^>]*?\bhref\s*=\s*[\"']([^\"'#\s][^\"']*)[\"']", re.I),
)


@dataclass
class WatchState:
    """One web watch's persisted memory: what it has seen, and what it has spent.

    `seeded` mirrors `file_poll.WatchState`: the FIRST poll of a page records every item without
    firing. A watch that fired on its first look would deliver the entire current front page as
    "new", which is the exact behaviour that makes someone delete the automation.
    """

    seeded: bool = False
    seen: list[str] = field(default_factory=list)
    #: Rolling-day request accounting: the day bucket, and how many requests were made in it.
    day: str = ""
    requests_today: int = 0
    last_polled_at: float = 0.0
    last_status: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "seeded": self.seeded,
            "seen": list(self.seen),
            "day": self.day,
            "requests_today": self.requests_today,
            "last_polled_at": self.last_polled_at,
            "last_status": self.last_status,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> WatchState:
        if not isinstance(raw, dict):
            return cls()
        seen = raw.get("seen")
        return cls(
            seeded=bool(raw.get("seeded", False)),
            seen=[str(s) for s in seen if isinstance(s, str)] if isinstance(seen, list) else [],
            day=str(raw.get("day", "") or ""),
            requests_today=int(raw.get("requests_today", 0) or 0),
            last_polled_at=float(raw.get("last_polled_at", 0.0) or 0.0),
            last_status=int(raw.get("last_status", 0) or 0),
        )


def _state_dir(base_dir: Path | str | None) -> Path:
    from personalclaw.config.loader import config_dir

    root = Path(base_dir) if base_dir else config_dir()
    return root / "trigger-web-watch"


def _state_path(trigger_id: str, base_dir: Path | str | None) -> Path:
    safe = _SAFE_RE.sub("-", trigger_id) or "watch"
    return _state_dir(base_dir) / f"{safe}.json"


def load_state(trigger_id: str, *, base_dir: Path | str | None = None) -> WatchState:
    """This watch's state, or a fresh unseeded one. Never raises.

    A corrupt sidecar reads as unseeded rather than as an error: the cost is one silent re-seed (no
    fire), and the alternative — a poll loop that dies on a bad JSON file — would stop every OTHER
    watch on the machine.
    """
    try:
        return WatchState.from_dict(
            json.loads(_state_path(trigger_id, base_dir).read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError):
        return WatchState()


def save_state(trigger_id: str, state: WatchState, *, base_dir: Path | str | None = None) -> None:
    """Persist atomically. A failure is logged, never raised — see `load_state`."""
    from personalclaw.atomic_write import atomic_write

    path = _state_path(trigger_id, base_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n")
    except OSError:
        logger.debug("web_poll: could not persist watch state for %s", trigger_id, exc_info=True)


def web_watch_triggers(store: Any) -> list[Any]:
    """Every enabled `web_watch` trigger with a url. Broken rows are skipped, not repaired here."""
    out: list[Any] = []
    for row in store.load():
        trigger = row.trigger
        if not getattr(row, "ok", True) or trigger.kind != "web_watch" or not trigger.enabled:
            continue
        spec = trigger.spec if isinstance(trigger.spec, dict) else {}
        if str(spec.get("url", "") or "").strip():
            out.append(trigger)
    return out


def poll_interval_for(trigger: Any) -> float:
    """The trigger's poll interval, clamped to `MIN_POLL_INTERVAL_SECS`.

    Clamped rather than refused: a user who typed 60 wants frequent checks, and the floor gives them
    the most frequent one that is not abusive to the target. Refusing would leave the automation
    dead over a number they can barely see.
    """
    spec = trigger.spec if isinstance(trigger.spec, dict) else {}
    try:
        requested = float(spec.get("poll_interval", 0) or 0)
    except (TypeError, ValueError):
        requested = 0.0
    return max(requested or MIN_POLL_INTERVAL_SECS, MIN_POLL_INTERVAL_SECS)


def extract_items(body: str, *, novelty_key: str = "") -> list[str]:
    """The stable per-item keys in `body`, newest-first order preserved as found.

    🔴 Novelty is keyed on ITEMS, never on the whole body. A body hash treats a rotating ad, a
    timestamp or a CSRF token as news, which turns one watch into a notification every poll — the
    failure `web_watch`'s seen-set exists to prevent.

    `novelty_key` selects an explicit regex group when the author knows their page; `auto` tries
    RSS/Atom ids then anchor hrefs. If nothing matches, the caller gets an empty list and treats the
    poll as "no items found" rather than as "everything is new".
    """
    if not body:
        return []
    patterns: tuple[re.Pattern[str], ...]
    if novelty_key and novelty_key != "auto":
        try:
            patterns = (re.compile(novelty_key, re.I),)
        except re.error:
            # A bad author-supplied regex must not take the watch offline; fall back to auto and let
            # the doctor complain about the pattern.
            logger.debug("web_poll: invalid novelty_key %r; using auto", novelty_key)
            patterns = _ITEM_RES
    else:
        patterns = _ITEM_RES

    found: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(body):
            key = (match.group(1) if match.groups() else match.group(0)).strip()
            if key and key not in found:
                found.append(key)
        if found:
            # First pattern that matches wins, so an RSS feed is keyed by guid rather than by every
            # link in its own description HTML.
            break
    return found


def _digest(key: str) -> str:
    """Items are stored HASHED. A seen-set of raw URLs is a browsing history in a plaintext sidecar
    that snapshots (S113) carry; the control needs identity, not the value."""
    return hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:32]


def _day_of(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def budget_remaining(state: WatchState, *, now: float) -> int:
    """Requests left in the rolling day. Resets when the day bucket rolls over."""
    if state.day != _day_of(now):
        return MAX_REQUESTS_PER_DAY
    return max(0, MAX_REQUESTS_PER_DAY - state.requests_today)


@dataclass
class PollOutcome:
    """One poll's result: a payload to dispatch, or a reason there is none.

    A REASON rather than `None`, because §7 criterion 8 bans silent drops and "the budget was spent"
    is something a user must be able to see — a watch that stopped polling with no explanation is
    indistinguishable from a broken one.
    """

    payload: dict[str, Any] | None = None
    reason: str = ""
    fetched: bool = False


def poll_one(
    trigger: Any,
    *,
    now: float,
    base_dir: Path | str | None = None,
    fetcher: Any = None,
) -> PollOutcome:
    """Poll one `web_watch` trigger. Returns a payload only when NEW items appeared.

    `fetcher` is injected — defaulting to the `net.fetch` egress chokepoint — so every test drives
    the real novelty/budget/seed logic without a network call. The seam is the same one the executor
    and `ScheduleService` use for their runners.
    """
    from personalclaw.security import fence_untrusted

    spec = trigger.spec if isinstance(trigger.spec, dict) else {}
    url = str(spec.get("url", "") or "").strip()
    if not url:
        return PollOutcome(reason="no url")

    state = load_state(trigger.id, base_dir=base_dir)

    interval = poll_interval_for(trigger)
    if state.last_polled_at and now - state.last_polled_at < interval:
        return PollOutcome(reason="not due")

    if budget_remaining(state, now=now) <= 0:
        # Visible, not silent: the reason travels to the ledger row.
        return PollOutcome(
            reason=f"daily request budget spent ({MAX_REQUESTS_PER_DAY} requests); "
            "resumes tomorrow"
        )

    body, status, error = _fetch(url, fetcher)

    # Accounted whether or not the fetch SUCCEEDED. A failing url that did not count toward the
    # budget would retry forever at full rate, which is the shape that gets a user's IP blocked.
    if state.day != _day_of(now):
        state.day, state.requests_today = _day_of(now), 0
    state.requests_today += 1
    state.last_polled_at = now
    state.last_status = status

    if error:
        save_state(trigger.id, state, base_dir=base_dir)
        return PollOutcome(reason=error, fetched=True)

    raw_items = extract_items(body, novelty_key=str(spec.get("novelty_key", "")))
    keys = [_digest(k) for k in raw_items]
    seen = set(state.seen)
    fresh = [(k, raw) for k, raw in zip(keys, raw_items) if k not in seen]

    # Newest kept, oldest dropped past the cap.
    state.seen = (keys + [s for s in state.seen if s not in set(keys)])[:MAX_SEEN_KEYS]

    if not state.seeded:
        # 🔴 The FIRST poll records without firing. Firing here would deliver the entire current page
        # as "new" — the behaviour that makes someone delete the automation on day one. Mirrors
        # `file_poll`'s seeding pass, and the seed is persisted so a restart does not re-seed.
        state.seeded = True
        save_state(trigger.id, state, base_dir=base_dir)
        return PollOutcome(reason=f"seeded {len(keys)} item(s) without firing", fetched=True)

    save_state(trigger.id, state, base_dir=base_dir)

    if not fresh:
        return PollOutcome(reason="no new items", fetched=True)

    return PollOutcome(
        payload={
            "trigger_id": trigger.id,
            "trigger_name": trigger.name,
            "kind": "web_watch",
            "url": url,
            "new_count": len(fresh),
            # The raw item keys the fire is ABOUT, so the action can say what changed. Capped: a
            # payload carrying 400 urls is a prompt nobody can afford.
            #
            # FENCED with provenance at the source (§7/R4 rule c — S127). These strings came off a
            # third-party page, and S126 closed the template sink; fencing HERE additionally means
            # any future consumer of the payload inherits the marker and the origin rather than
            # having to know that `new_items` is untrusted.
            "new_items": [
                fence_untrusted(
                    raw,
                    source=f"web_watch:{trigger.id}",
                    source_type="web_watch",
                    source_id=url,
                    transformation_path="poll:extract-items",
                )
                for _, raw in fresh[:20]
            ],
        },
        fetched=True,
    )


def _fetch(url: str, fetcher: Any) -> tuple[str, int, str]:
    """`(body, status, error)`. Never raises — a bad url is a reason, not an exception.

    Routed through `net.fetch` by default: that is where host classification, private-IP denial,
    redirect-hop re-checks, the byte cap and the timeout live. A watch pointed at
    `http://169.254.169.254/` is an SSRF against the machine's own metadata service, and this is the
    layer that already refuses it — a direct `urllib` call here would bypass all of it.
    """
    try:
        if fetcher is None:
            from personalclaw.net import fetch as net_fetch

            response = net_fetch(url)
        else:
            response = fetcher(url)
    except Exception as exc:  # noqa: BLE001 - an unreachable page must not kill the poll loop
        name = type(exc).__name__
        return "", 0, f"fetch failed ({name}: {exc})"

    status = int(getattr(response, "status", 0) or 0)
    raw = getattr(response, "body", b"") or b""
    body = (
        raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    )
    if status and not 200 <= status < 300:
        return "", status, f"the page answered HTTP {status}"
    return body, status, ""


def poll_all(
    store: Any,
    *,
    now: float,
    base_dir: Path | str | None = None,
    fetcher: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Poll every enabled web watch once. Returns `(payloads, skipped)`.

    `skipped` carries `{trigger_id, reason}` so the caller can write the ledger rows §7 criterion 8
    requires. One watch's failure never strands the rest: a poll loop that died on one unreachable
    host would silently stop every other watch the user has.
    """
    payloads: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for trigger in web_watch_triggers(store):
        try:
            outcome = poll_one(trigger, now=now, base_dir=base_dir, fetcher=fetcher)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.warning("web_watch poll failed for %s", trigger.id, exc_info=True)
            skipped.append({"trigger_id": trigger.id, "reason": "the poll raised"})
            continue
        if outcome.payload is not None:
            payloads.append(outcome.payload)
        elif outcome.reason and outcome.reason != "not due":
            # "not due" is the common case and not worth a row; everything else is a decision the
            # user may need to see.
            skipped.append({"trigger_id": trigger.id, "reason": outcome.reason})
    return payloads, skipped
