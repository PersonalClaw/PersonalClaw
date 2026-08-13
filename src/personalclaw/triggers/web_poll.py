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

**Fetching goes through the egress chokepoint** — never `urllib`/`httpx` directly. The plain tier is
`net.fetch`; the opt-in headless tier is `web.render.render_url`. BOTH apply host classification,
private-IP denial and the redirect-hop re-check (`render_url` runs `net.guard.evaluate` before it
navigates, precisely because a browser does its own DNS and would otherwise bypass the pin). A watch
pointed at `http://169.254.169.254/` is an SSRF against the machine's own metadata service, and both
tiers refuse it; re-implementing a fetch here would bypass every one of those controls.

**A daily request budget, enforced.** §3 asks for one, and without it a `poll_interval` of 60 on a
handful of watches is a few thousand requests a day at someone else's server. The budget is counted
in the sidecar and refuses with a ledger-visible reason rather than silently skipping.

**State is a SIDECAR**, matching `file_poll`'s reasoning exactly: a seen-set is high-churn runtime
state, and writing it onto the trigger entity would rewrite `triggers.json` on every poll and race
every unrelated edit.

**The headless-browser escalation tier IS built** (§3). A page that answers 200 with an empty JS
shell defeats the plain fetch — `extract_items` finds nothing on a real success, the escalation
signal. When a watch opts in (`escalate_headless: true`, default OFF so existing watches are
byte-unchanged), the poll escalates to `render_url`, which drives a headless Chromium through the
SAME egress guard and returns post-JS HTML for the shared extractor. A render is far costlier than a
fetch, so escalations spend their own bounded budget (`max_headless_requests`), accounted win-or-
lose and refused with a ledger-visible reason. When Playwright is absent `render_url` reports
`unavailable` and the poll serves the plain result rather than crashing.

**Fresh items land in the knowledge store as user items** — searchable bookmarks, not memory. The
seen-set still gates "new", so only genuinely-new items are written, and the write is injectable so
tests never touch the real store.

**What this does NOT own:** the LLM turn (the executor, injected as a runner by the gateway) and
item extraction beyond the two shipped strategies.
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

#: Headless renders one watch may perform in a rolling day. FAR lower than the plain-fetch cap: a
#: render launches a whole browser, executes arbitrary page JS and settles the network — an order of
#: magnitude more expensive in CPU, memory and time than a `net.fetch`. Its own counter so an
#: escalating watch cannot burn the machine down retrying a JS shell every interval; the plain-fetch
#: budget still bounds the poll itself. Overridable per-watch via `max_headless_requests`.
MAX_HEADLESS_REQUESTS_PER_DAY = 24  # ~hourly, well under the plain floor

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
    #: Headless renders spent in the same day bucket. Separate counter, same bucket, because the two
    #: budgets share a day but not a limit — a render is the expensive tier.
    headless_today: int = 0
    last_polled_at: float = 0.0
    last_status: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "seeded": self.seeded,
            "seen": list(self.seen),
            "day": self.day,
            "requests_today": self.requests_today,
            "headless_today": self.headless_today,
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
            headless_today=int(raw.get("headless_today", 0) or 0),
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


def headless_budget_for(trigger: Any) -> int:
    """The headless-render cap for this watch: the author's `max_headless_requests` if a sane
    positive int, else the default. Clamped to the plain-fetch cap — a headless budget above the
    number of polls in a day is nonsense, and the poll count is the real ceiling on escalations."""
    spec = trigger.spec if isinstance(trigger.spec, dict) else {}
    try:
        requested = int(spec.get("max_headless_requests", 0) or 0)
    except (TypeError, ValueError):
        requested = 0
    limit = requested if requested > 0 else MAX_HEADLESS_REQUESTS_PER_DAY
    return min(limit, MAX_REQUESTS_PER_DAY)


def headless_budget_remaining(state: WatchState, *, now: float, limit: int) -> int:
    """Headless renders left in the rolling day. Resets with the same day bucket the plain budget
    uses, so the two never disagree about which day it is."""
    if state.day != _day_of(now):
        return limit
    return max(0, limit - state.headless_today)


def _escalate_enabled(trigger: Any) -> bool:
    """Whether this watch opted into the headless tier. Default OFF — an existing watch that never
    set the key must poll byte-for-byte as before."""
    spec = trigger.spec if isinstance(trigger.spec, dict) else {}
    return bool(spec.get("escalate_headless", False))


def _await_maybe(result: Any) -> Any:
    """Resolve `result` to a value whether it is a coroutine or already concrete.

    `poll_one` runs on a worker thread (the poll loop calls it via `asyncio.to_thread`), so there is
    no running loop and `asyncio.run` is the right bridge; the `ThreadPoolExecutor` fallback covers
    a future caller that DOES hold a running loop so it can never deadlock. This mirrors
    `triggers/tools.py::_default_cadence_to_cron` rather than inventing a third bridge — and it is
    shared by BOTH async seams here (the default `net.fetch` and the headless `render_url`), each a
    coroutine in production and a plain value when a test injects a sync fake.
    """
    import asyncio

    if not asyncio.iscoroutine(result):
        return result
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(result)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, result).result(timeout=120)


def _poll_egress_policy(trigger_id: str) -> Any:
    """The egress policy ONE poll may use, or `None` when the run may not egress at all.

    Three layers, tightest wins: the SOURCE surface profile (a poll is a knowledge scrape on
    a timer) → the operator's `security.egress` allow/deny/private config → the run's
    `SafetyProfile.egress_tier`, resolved for a poll's sessionless unattended identity and
    therefore bounded by the governance ceiling. A poll ran on a bare `STRICT` before, which
    is why an operator's `deny_hosts` did not reach the headless tier and the egress tier
    reached nothing at all.
    """
    from personalclaw.guardrails.policy import profile_for_session, unattended_dispatch_key
    from personalclaw.net.policy import SOURCE, egress_policy_for, egress_policy_for_profile

    tier = profile_for_session(unattended_dispatch_key(f"trigger:{trigger_id}")).egress_tier
    return egress_policy_for_profile(egress_policy_for(SOURCE), tier)


def _render_headless(url: str, renderer: Any, policy: Any) -> Any:
    """Drive the headless tier and return its `RenderResult`. NEVER raises.

    `renderer` defaults to `web.render.render_url` (async); an injected sync fake (the tests)
    returns a `RenderResult` directly. Coroutine bridge: `_await_maybe` (shared with `_fetch`).

    `policy` is the poll's RESOLVED egress policy (`_poll_egress_policy`), not a hardcoded
    `STRICT`: the hardcoded one skipped both the operator's `security.egress` layering (so a
    configured `deny_hosts` was honoured on the plain tier and ignored here) and the run's
    egress tier.
    """
    from personalclaw.web.render import RenderResult

    try:
        if renderer is None:
            from personalclaw.web.render import render_url

            renderer = render_url
        return _await_maybe(renderer(url, policy=policy))
    except Exception as exc:  # noqa: BLE001 - a render crash is a reason, not a dead poll loop
        return RenderResult(
            ok=False, url=url, error=f"headless render raised ({type(exc).__name__}: {exc})"
        )


def _with_escalation(base: str, escalation: str) -> str:
    """Fold an escalation marker into a non-firing reason so the ledger's skipped row shows it —
    §7 criterion 8 bans a silent escalation as much as a silent skip."""
    return f"{base} [{escalation}]" if escalation else base


def _route_to_knowledge(trigger: Any, url: str, fresh_raw: list[str], store: Any) -> int:
    """Write each genuinely-new item to the KNOWLEDGE store as a searchable user bookmark — NOT the
    memory subsystem. The seen-set already gated "new", so only fresh items reach here; returns the
    count written.

    NEVER raises: a knowledge-store failure must not kill the poll (the never-die contract) — it is
    logged and the fire still happens. `store` is injected in tests; in production it defaults to
    the process-wide `KnowledgeStore` (opened `check_same_thread=False`, so the poll worker may
    write it).
    """
    written = 0
    try:
        if store is None:
            from personalclaw.knowledge import get_knowledge_store

            store = get_knowledge_store()
        for raw in fresh_raw:
            item_url = raw if raw[:4].lower() == "http" else ""
            title = raw if len(raw) <= 200 else raw[:197] + "..."
            store.create_typed_item(
                item_type="bookmark",
                title=title,
                url=item_url,
                provider="web_watch",
                summary=f"New item from web watch {trigger.name!r} ({url})",
                tags=["web-watch", trigger.name],
            )
            written += 1
    except Exception:  # noqa: BLE001 - see the docstring
        logger.debug(
            "web_poll: could not route digest to knowledge for %s", trigger.id, exc_info=True
        )
    return written


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
    #: The headless-escalation marker, when this poll attempted one. Set whether the escalation
    #: fired, was refused by its budget, failed, or found the tier unavailable — §7 criterion 8
    #: bans a silent escalation as much as a silent skip. On a NON-firing poll it is mirrored into
    #: `reason` (→ the ledger's skipped row); on a firing poll it rides in the payload under the
    #: `escalation` key (the fired payload is a dict literal below, keys hardcoded).
    escalation: str = ""


def poll_one(
    trigger: Any,
    *,
    now: float,
    base_dir: Path | str | None = None,
    fetcher: Any = None,
    renderer: Any = None,
    knowledge_store: Any = None,
) -> PollOutcome:
    """Poll one `web_watch` trigger. Returns a payload only when NEW items appeared.

    `fetcher` is injected — defaulting to the `net.fetch` egress chokepoint — so every test drives
    the real novelty/budget/seed logic without a network call. The seam is the same one the executor
    and `ScheduleService` use for their runners.

    `renderer` is the headless-escalation seam (defaulting to `web.render.render_url`), injected the
    same way so a test drives the escalation branch without a browser. `knowledge_store` is the
    KnowledgeStore fresh items are written to (defaulting to the process-wide store); injected so a
    test asserts the digest lands in KNOWLEDGE, never memory, without touching the real store.
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

    # The run's egress posture, resolved BEFORE the request is spent (PHF-8). A tier of
    # "off" refuses visibly — the reason travels to the ledger row, like the budget refusals
    # above — rather than making a request the run's posture forbids.
    egress = _poll_egress_policy(str(getattr(trigger, "id", "") or ""))
    if egress is None:
        return PollOutcome(
            reason="this run's safety profile denies all network egress (egress tier 'off')"
        )

    body, status, error = _fetch(url, fetcher, egress)

    # Accounted whether or not the fetch SUCCEEDED. A failing url that did not count toward the
    # budget would retry forever at full rate, which is the shape that gets a user's IP blocked.
    if state.day != _day_of(now):
        state.day, state.requests_today, state.headless_today = _day_of(now), 0, 0
    state.requests_today += 1
    state.last_polled_at = now
    state.last_status = status

    if error:
        save_state(trigger.id, state, base_dir=base_dir)
        return PollOutcome(reason=error, fetched=True)

    novelty_key = str(spec.get("novelty_key", ""))
    raw_items = extract_items(body, novelty_key=novelty_key)

    # 🔴 ESCALATION. A real 200 whose plain fetch extracts NOTHING is the JS-shell signal: the page
    # built its content client-side, so `net.fetch` saw only the empty shell. When the watch opted
    # in, escalate to the headless tier — `render_url`, which runs the SAME egress guard before it
    # navigates, so the SSRF invariant holds — and re-extract from the post-JS HTML. Default OFF, so
    # a watch that never set the key is byte-for-byte unchanged.
    escalation = ""
    if not raw_items and _escalate_enabled(trigger):
        limit = headless_budget_for(trigger)
        if headless_budget_remaining(state, now=now, limit=limit) <= 0:
            # Refused, VISIBLY (§7 criterion 8). A render is the expensive tier; spent, it stops and
            # says so rather than launching a browser it has no budget for.
            escalation = f"headless escalation budget spent ({limit} renders); resumes tomorrow"
        else:
            result = _render_headless(url, renderer, egress)
            if getattr(result, "unavailable", False):
                # No render happened, so nothing is charged — and it can never succeed until the
                # dependency is installed, so charging it would only burn the budget for nothing.
                escalation = "headless tier unavailable; install personalclaw[js-render]"
            else:
                # A real attempt. Charged WIN-OR-LOSE: a failed render that did not count would
                # retry every interval forever, the exact runaway the plain budget also guards.
                state.headless_today += 1
                if getattr(result, "ok", False):
                    rendered = extract_items(
                        str(getattr(result, "html", "") or ""), novelty_key=novelty_key
                    )
                    if rendered:
                        raw_items = rendered
                        escalation = f"escalated to headless; extracted {len(rendered)} item(s)"
                    else:
                        escalation = "escalated to headless; still no items after JS render"
                else:
                    escalation = (
                        f"headless render failed: {getattr(result, 'error', '') or 'unknown'}"
                    )

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
        return PollOutcome(
            reason=_with_escalation(f"seeded {len(keys)} item(s) without firing", escalation),
            fetched=True,
            escalation=escalation,
        )

    save_state(trigger.id, state, base_dir=base_dir)

    if not fresh:
        return PollOutcome(
            reason=_with_escalation("no new items", escalation),
            fetched=True,
            escalation=escalation,
        )

    # Fresh items land in the KNOWLEDGE store as searchable user bookmarks — not memory. The
    # seen-set already gated "new", so only genuinely-new items are written.
    fresh_raw = [raw for _, raw in fresh]
    _route_to_knowledge(trigger, url, fresh_raw, knowledge_store)

    return PollOutcome(
        payload={
            "trigger_id": trigger.id,
            "trigger_name": trigger.name,
            "kind": "web_watch",
            "url": url,
            "new_count": len(fresh),
            # The escalation marker rides in the fired payload too, so a headless-sourced fire is
            # not a silent escalation (§7 criterion 8) — the non-firing path folds it into reason.
            "escalation": escalation,
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
                for raw in fresh_raw[:20]
            ],
        },
        fetched=True,
        escalation=escalation,
    )


def _fetch(url: str, fetcher: Any, policy: Any) -> tuple[str, int, str]:
    """`(body, status, error)`. Never raises — a bad url is a reason, not an exception.

    Routed through `net.fetch` by default: that is where host classification, private-IP denial,
    redirect-hop re-checks, the byte cap and the timeout live. A watch pointed at
    `http://169.254.169.254/` is an SSRF against the machine's own metadata service, and this is the
    layer that already refuses it — a direct `urllib` call here would bypass all of it.

    `policy` is the poll's resolved egress policy (`_poll_egress_policy`): the SOURCE surface
    profile, layered with the operator's `security.egress` config and narrowed by the run's
    egress tier. Passing it explicitly is what makes an operator `deny_hosts` and a narrowed
    ceiling apply to a poll — `net.fetch`'s own default is a bare `STRICT` that layers neither.
    """
    try:
        if fetcher is None:
            from personalclaw.net import fetch as net_fetch

            # `net.fetch` is a COROUTINE — `poll_one` runs on a worker thread with no loop, so it
            # must be driven through the shared `_await_maybe` bridge. Without it the default path
            # returned an un-awaited coroutine → `status`/`body` read as 0/empty and EVERY
            # default-fetcher web_watch silently no-oped (pre-existing bug; the suite was blind
            # because tests inject a sync `fetcher`). A sync fake passes through `_await_maybe`.
            response = _await_maybe(net_fetch(url, policy=policy))
        else:
            response = _await_maybe(fetcher(url))
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
    renderer: Any = None,
    knowledge_store: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Poll every enabled web watch once. Returns `(payloads, skipped)`.

    `skipped` carries `{trigger_id, reason}` so the caller can write the ledger rows §7 criterion 8
    requires. One watch's failure never strands the rest: a poll loop that died on one unreachable
    host would silently stop every other watch the user has.

    `renderer`/`knowledge_store` are the escalation and digest-routing seams, forwarded to
    `poll_one`; both default to the production tiers and are injected only by tests.
    """
    payloads: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for trigger in web_watch_triggers(store):
        try:
            outcome = poll_one(
                trigger,
                now=now,
                base_dir=base_dir,
                fetcher=fetcher,
                renderer=renderer,
                knowledge_store=knowledge_store,
            )
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
