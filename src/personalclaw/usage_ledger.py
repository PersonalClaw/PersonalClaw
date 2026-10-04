"""The per-turn cost/token ledger (C1).

The durable answer to "what did this cost me?" — a fail-open, append-only JSONL of
one :class:`TurnUsage` row per model call (a turn, or a call made around one: a title, a
judge, a digest), with rollups by model / source / agent / provider / day. This module owns
the STORE; the write sites feed it through :func:`record_from_event`, or :func:`recorder`
for a call made for an :class:`Attribution`.

Soul guardrails this module enforces:
- **Observation only, never enforcement.** A ledger records; it can never block,
  throttle, or refuse a turn. Budget caps live in ``guardrails`` (``SpendMeter``).
- **Honest zero over invented precision.** A turn nothing prices (no reported cost,
  and no rate for its model: ``routing.rates.price_call``) records its tokens with
  ``cost_usd = 0.0`` and ``priced = False`` — a caller MUST render "unpriced", never
  ``$0.00``. A rollup whose total mixes any unpriced row reports ``priced = False`` so a
  partial total can't present as complete.
- **Fail-open.** :func:`record_turn` never raises into a turn — a ledger write
  failure degrades to a DEBUG log. This is a user-facing availability surface, not
  a security control.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from personalclaw import bounded_log, spend_day
from personalclaw.turn_streams import closing_stream

logger = logging.getLogger(__name__)

# Newest-N kept when the log exceeds 2× (the house convention — mirrors feedback.py).
_CAP = 50_000

_GROUP_KEYS = ("model", "source", "agent", "provider", "day")


@dataclass
class TurnUsage:
    """One model turn's token + cost accounting (§C1).

    ``priced`` is False ⇒ the provider reported no cost AND no rate prices the model
    (``routing.rates.price_call``), so ``cost_usd`` is an honest 0.0 that MUST render
    "unpriced".
    """

    ts: str  # ISO-UTC, matching the SEL timestamp convention
    session_key: str
    source: str  # chat | room | loop | cron | subagent | channel | cli | background | eval
    agent: str  # "" = the default agent
    # The provider entry the answer came from (``FakeUp`` of ``FakeUp:gpt-4o``); for an ACP agent
    # CLI, which names none, the runtime it ran on (``acp:claude-code``).
    provider: str
    model: str  # the resolved model id the turn was priced by
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    priced: bool = True
    # Whether the model that answered runs on this machine, priced at its known $0 by the rule
    # the spend caps charge by (``routing.rates``, ``llm.registry.served_on_this_machine``), as it
    # was when the turn ran: what the Usage page's "ran locally at $0" share counts, whatever the
    # prices and instances configured since say.
    local: bool = False
    duration_ms: int = 0
    # The ``audit_id`` of each guarded model call this row's tokens came from, as
    # ``model_calls.jsonl`` records it (``LLMEvent.audit_ids``): the join that lets the usage fold
    # leave those calls out of its census of the log (``routing.usage.audit_census``). Empty for a
    # turn no guard wrapped (the interactive chat, an ACP agent CLI).
    audit_ids: list[str] = field(default_factory=list)
    # A call billed in another unit than tokens (``routing.rates.UNITS``: an image, a second of
    # video, a minute of audio, a character of speech) names it, and how many of it the call was
    # billed for; its token counts are zero. ``""`` and 0 for a call billed by its tokens.
    unit: str = ""
    quantity: float = 0


@dataclass(frozen=True)
class Attribution:
    """Whose spend a model call is: the ledger columns only the code that asked for it knows.

    A one-shot completion (``llm_helpers.one_shot_completion(usage=…)``) resolves its own model,
    so its caller names the rest of the row: the ``source``, the session the call was made for,
    and the agent that made it.
    """

    source: str
    session_key: str = ""
    agent: str = ""


#: Whose spend a model call is when the code that made it names nobody: unattended background
#: work. A one-shot completion (``llm_helpers.one_shot_completion``) is never an interactive turn,
#: so that is what such a call is, rather than a guess, and it is counted instead of dropped.
UNATTENDED = Attribution(source="background")


def recorder(provider: object, who: Attribution) -> Callable[[object], None]:
    """The ``on_complete`` that writes one row for a call made through *provider*, for *who*.

    The row names the model that answered when the event says (a native runtime's
    ``served_model_ref``), and otherwise the one *provider* was built for: the
    ``"<entry>:<model>"`` its build stamped (``ModelProvider.served_ref``).

    Fail-open, as :func:`record_turn` is: writing the row never breaks the call it records.
    """
    entry, _, model = str(getattr(provider, "served_ref", "") or "").partition(":")

    def record(event: object) -> None:
        try:
            record_from_event(
                event,
                source=who.source,
                session_key=who.session_key,
                agent=who.agent,
                provider=entry,
                model=model,
            )
        except Exception:  # noqa: BLE001 — the never-raises contract
            logger.debug("usage row for a %s call not written", who.source, exc_info=True)

    return record


async def spent_rows(
    events: AsyncIterator[Any], record: Callable[[object], None]
) -> AsyncIterator[Any]:
    """*events*, a turn's stream, each passed on as it came, with the usage row of a turn that
    ends in an error written from the ``EVENT_SPENT`` it sends before the error (*record*, the
    turn's row writer, :func:`recorder`): the calls the turn made are in Usage, as the meter and
    the model-call log already hold them. A turn that completes writes its row from its
    ``EVENT_COMPLETE``, where its consumer reads it."""
    from personalclaw.llm.events import EVENT_SPENT

    async with closing_stream(events) as turn:
        async for event in turn:
            if getattr(event, "kind", "") == EVENT_SPENT:
                record(event)
            yield event


def _path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / "usage" / "turns.jsonl"


def record_turn(u: TurnUsage) -> None:
    """Append one row. Best-effort and NEVER raises into a turn (§2.7) — a ledger
    write failure degrades to a DEBUG log, never breaks the user's conversation."""
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(u), ensure_ascii=False) + "\n")
        _maybe_trim(p)
    except Exception:  # noqa: BLE001 — the never-raises contract
        logger.debug("usage ledger append failed", exc_info=True)


def _served(event: object) -> tuple[str, str]:
    """``(provider entry, model id)`` of the model a turn's terminal event says answered it.

    The native loop names that model on the event (``served_model_ref``, ``"<entry>:<model>"``):
    a later model of the turn's chain when the turn fell back, or the one serving in place of a
    model that could not run. ``("", "")`` when the event names none (an ACP agent CLI).
    """
    served = getattr(event, "served_model_ref", "")
    if not isinstance(served, str) or not served:
        return "", ""
    # The entry name holds no colon and a model id may (``gpt-oss:20b``): split on the first.
    entry, colon, model_id = served.partition(":")
    return (entry, model_id) if colon else ("", served)


def answered_model(event: object, asked: str = "") -> str:
    """The model id a turn is priced and recorded by: the one that ANSWERED it.

    The id half of the served ref (:func:`_served`) is what this ledger's ``model`` and the price
    table key on; the ref itself is neither. A backend that names none (an ACP agent CLI) leaves
    ``asked``, the model the caller chose.
    """
    return _served(event)[1] or asked


def answered_provider(event: object, asked: str = "") -> str:
    """The provider entry a turn's answer came from: the entry half of the served ref.

    That entry (the ``FakeUp`` of ``FakeUp:gpt-4o``) is what this ledger's ``provider`` names, and
    what a ``provider:model`` ref in ``active_models.json`` spells. A backend that names none (an
    ACP agent CLI) leaves ``asked``, the runtime the caller ran it on (``acp:claude-code``).
    """
    return _served(event)[0] or asked


def _audit_ids(event: object) -> list[str]:
    """The guarded calls *event* says its usage came from (``LLMEvent.audit_ids``); none from an
    event of another shape."""
    named = getattr(event, "audit_ids", ())
    return [str(i) for i in named if i] if isinstance(named, (list, tuple)) else []


def record_from_event(
    event: object,
    *,
    source: str,
    session_key: str = "",
    agent: str = "",
    provider: str = "",
    model: str = "",
) -> None:
    """Record one ledger row from a terminal ``EVENT_COMPLETE`` LLM event (C2).

    The one seam every write-site shares: it reads the token counts + provider cost off the
    event and prices the turn through the one pricing function (``routing.rates.price_event``):
    the provider's reported cost when it reported one, else its tokens at the effective rate —
    a rate the owner set, a local model's known zero, a rate the serving app declared, the
    shipped table. ``priced`` is False only when nothing prices it; then ``cost_usd`` is an
    honest 0.0 the UI renders "unpriced". Fail-open through :func:`record_turn`.

    ``model`` is the model the caller chose and ``provider`` the runtime it ran on; the row is
    written for the model that answered, and the provider entry it came from, whenever the event
    names them (:func:`answered_model`, :func:`answered_provider`), and priced by them.

    A caller that already priced the turn (the chat write-site, for its cost line) wrote the
    price into ``event.cost_usd``, so it reads here as the figure and is not priced twice.

    The row keeps the ids of the guarded model calls the event says its usage came from
    (``LLMEvent.audit_ids``), so the usage fold's census of ``model_calls.jsonl`` does not
    count those calls a second time.
    """
    from datetime import datetime, timezone

    from personalclaw.routing.rates import price_event

    model = answered_model(event, model)
    provider = answered_provider(event, provider)
    input_tokens = int(getattr(event, "input_tokens", 0) or 0)
    output_tokens = int(getattr(event, "output_tokens", 0) or 0)
    cache_read = int(getattr(event, "cache_read_tokens", 0) or 0)
    cache_creation = int(getattr(event, "cache_creation_tokens", 0) or 0)
    price = price_event(event, provider=provider, model=model)
    record_turn(
        TurnUsage(
            ts=datetime.now(timezone.utc).isoformat(),
            session_key=session_key,
            source=source,
            agent=agent,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            cost_usd=price.dollars,
            priced=price.priced,
            local=price.source == "local",
            duration_ms=int(getattr(event, "duration_ms", 0) or 0),
            audit_ids=_audit_ids(event),
        )
    )


def record_call(
    *,
    source: str,
    session_key: str,
    provider: str,
    model: str,
    input_tokens: int,
    cost_usd: float,
    priced: bool,
    local: bool,
    duration_ms: int = 0,
    audit_ids: list[str] | None = None,
) -> None:
    """Record one ledger row for a call billed by its tokens that streams no event to read them
    off: an embedding call (``embedding_providers.registry``), priced by its maker through the one
    pricing function (``routing.rates.price_call``). ``audit_ids`` names the call's
    ``model_calls.jsonl`` row, so the usage fold does not count it a second time. Fail-open
    through :func:`record_turn`."""
    from datetime import datetime, timezone

    record_turn(
        TurnUsage(
            ts=datetime.now(timezone.utc).isoformat(),
            session_key=session_key,
            source=source,
            agent="",
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            cost_usd=cost_usd,
            priced=priced,
            local=local,
            duration_ms=duration_ms,
            audit_ids=list(audit_ids or []),
        )
    )


def record_units(
    *,
    source: str,
    session_key: str,
    provider: str,
    model: str,
    unit: str,
    quantity: float,
    cost_usd: float,
    priced: bool,
    local: bool,
    duration_ms: int = 0,
) -> None:
    """Record one ledger row for a call billed by its unit (``guardrails.media_call``): an image,
    a second of video, a minute of audio or the characters a voice spoke, priced by the one
    pricing function for that unit (``routing.rates.price_units``). Fail-open through
    :func:`record_turn`."""
    from datetime import datetime, timezone

    record_turn(
        TurnUsage(
            ts=datetime.now(timezone.utc).isoformat(),
            session_key=session_key,
            source=source,
            agent="",
            provider=provider,
            model=model,
            cost_usd=cost_usd,
            priced=priced,
            local=local,
            duration_ms=duration_ms,
            unit=unit,
            quantity=quantity,
        )
    )


def _maybe_trim(p: Path) -> None:
    """Trim to the newest ``_CAP`` turns by each one's ``ts`` when the file exceeds 2×
    (``bounded_log``: an atomic rewrite, in time order)."""
    try:
        bounded_log.trim_jsonl(p, _CAP, at="ts")
    except OSError:
        logger.debug("usage ledger trim failed", exc_info=True)


def _iter_rows() -> list[dict]:
    """Every ledger row as a dict (tolerant: a corrupt line is skipped, fail-open)."""
    p = _path()
    if not p.is_file():
        return []
    out: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                if isinstance(d, dict):
                    out.append(d)
            except (json.JSONDecodeError, ValueError):
                continue  # tolerant read — skip the bad line, keep the rest
    except OSError:
        logger.debug("usage ledger read failed", exc_info=True)
    return out


def _in_window(ts: str, since: str, until: str) -> bool:
    if since and ts < since:
        return False
    if until and ts >= until:
        return False
    return True


def _blank_agg() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cost_usd": 0.0,
        "turns": 0,
        "priced": True,
        # How much of each unit other than tokens the calls were billed for: ``{"image": 3}``.
        "units": {},
    }


def _fold(agg: dict, row: dict) -> None:
    agg["input_tokens"] += int(row.get("input_tokens", 0) or 0)
    agg["output_tokens"] += int(row.get("output_tokens", 0) or 0)
    agg["cache_read_tokens"] += int(row.get("cache_read_tokens", 0) or 0)
    agg["cache_creation_tokens"] += int(row.get("cache_creation_tokens", 0) or 0)
    agg["cost_usd"] += float(row.get("cost_usd", 0.0) or 0.0)
    agg["turns"] += 1
    unit = str(row.get("unit", "") or "")
    if unit:
        try:
            quantity = float(row.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            quantity = 0.0
        agg["units"][unit] = round(float(agg["units"].get(unit, 0.0)) + quantity, 6)
    # A single unpriced constituent taints the total — it can never present as complete.
    if not row.get("priced", True):
        agg["priced"] = False


#: Separator between a session key and the sub-key a fan-out worker appends. `loop.manager` spells
#: a task worker `f"{session_key(loop_id)}-{task_id}"`, so this is the boundary a prefix query must
#: respect.
_SESSION_KEY_SEP = "-"


def _session_matches(key: str, session_key: str, session_prefix: str) -> bool:
    """Whether ``key`` satisfies the exact-session and prefix-session filters.

    ``session_prefix`` matches the key ITSELF or any key that extends it **at a separator**, never
    a bare ``startswith``. A bare prefix test would make ``loop-abc123`` swallow
    ``loop-abc1234``'s rows and report a plausible, silently-too-large number — the failure shape
    that is worse than an obviously broken one. The guard deliberately does NOT lean on
    ``loop.store._LOOP_ID_RE`` making same-length ids collision-free today: this ledger accepts any
    ``session_key`` string a writer hands it, so the selection has to be unambiguous on its own
    terms rather than borrow safety from a regex in another module.
    """
    if session_key and key != session_key:
        return False
    if session_prefix and not (
        key == session_prefix or key.startswith(session_prefix + _SESSION_KEY_SEP)
    ):
        return False
    return True


def _row_selected(
    row: dict,
    since: str,
    until: str,
    session_key: str,
    session_prefix: str = "",
    key_filter: Callable[[str], bool] | None = None,
) -> bool:
    """Whether a ledger row is in the query window AND (if given) its session."""
    if not _in_window(str(row.get("ts", "")), since, until):
        return False
    key = str(row.get("session_key", ""))
    if key_filter is not None and not key_filter(key):
        return False
    return _session_matches(key, session_key, session_prefix)


def rollup(
    *,
    since: str = "",
    until: str = "",
    group_by: str = "model",
    session_key: str = "",
    session_prefix: str = "",
) -> list[dict]:
    """Aggregate the ledger, grouped by one of ``model|source|agent|provider|day`` (a ``day`` is
    her day, in her timezone, the day the daily cap counts: :mod:`personalclaw.spend_day`).

    Rows carry summed tokens + cost + a ``priced`` flag that is False when ANY
    constituent row was unpriced (so a partially-unpriced group can't look complete).
    Sorted by descending cost then the group key, for a stable, useful default order.
    ``session_key`` (when given) restricts to one session — the session-total surface.
    ``session_prefix`` restricts to a session AND its separator-delimited children — the
    fan-out surface, where one logical unit of work spans several session keys.
    """
    if group_by not in _GROUP_KEYS:
        raise ValueError(f"group_by must be one of {_GROUP_KEYS}, got {group_by!r}")
    groups: dict[str, dict] = {}
    zone = spend_day.zone() if group_by == "day" else None
    for row in _iter_rows():
        if not _row_selected(row, since, until, session_key, session_prefix):
            continue
        ts = str(row.get("ts", ""))
        key = spend_day.day_of(ts, zone) if zone is not None else str(row.get(group_by, ""))
        agg = groups.setdefault(key, _blank_agg())
        _fold(agg, row)
    out = [{group_by: k, **v} for k, v in groups.items()]
    out.sort(key=lambda r: (-r["cost_usd"], str(r[group_by])))
    return out


def models_used(*, since: str = "") -> list[tuple[str, str]]:
    """Each ``(provider, model)`` a row since *since* was spent on, the latest first."""
    seen: dict[tuple[str, str], None] = {}
    for row in reversed(_iter_rows()):
        if not _in_window(str(row.get("ts", "")), since, ""):
            continue
        provider = str(row.get("provider", "") or "")
        model = str(row.get("model", "") or "")
        if provider and model:
            seen.setdefault((provider, model), None)
    return list(seen)


def totals(
    *,
    since: str = "",
    until: str = "",
    session_key: str = "",
    session_prefix: str = "",
    key_filter: Callable[[str], bool] | None = None,
) -> dict:
    """Grand total over the window — the same agg shape, ungrouped. ``session_key``
    (when given) restricts to one session, answering "what did this chat cost?".
    ``session_prefix`` widens that to a session and its separator-delimited children,
    answering "what did this loop cost?" for a loop that fanned out into task workers.
    ``key_filter`` keeps only the rows whose session key it accepts, for an owner of a key shape
    the prefix rule does not fit: a workflow run books its steps under
    ``workflow:<run>:<step>`` (``workflows.ownership.run_spend``)."""
    agg = _blank_agg()
    for row in _iter_rows():
        if _row_selected(row, since, until, session_key, session_prefix, key_filter):
            _fold(agg, row)
    return agg
