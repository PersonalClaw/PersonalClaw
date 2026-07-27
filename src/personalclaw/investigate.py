"""Investigate Anywhere — one chat-with-context primitive for every entity row (plan 60).

Every entity row gets an **investigate** affordance that opens a chat pre-loaded
with that entity's full context. One shared primitive:

* a per-kind **resolver registry** — the owning module registers a pure-read
  function that composes an :class:`InvestigateContext` (typed envelope: kind, id,
  title, snapshot, back-link, suggested agent + task mode, opening prompt) from
  its own store. A client can't forge a snapshot — composition is server-side.
* ``POST /api/investigate`` creates a fresh chat session in ``ask`` mode (read-only
  investigation — propose-don't-write), stages the envelope on the session, and
  returns the session key.
* at the session's FIRST turn, ``chat_runner._inject_investigate_context`` prepends
  the envelope to the model-bound message — ``fence_untrusted`` wrapped, DATA not
  instructions — exactly like the knowledge/attachment injections. The user's
  visible message stays clean, and the user always fires the first turn.

Soul guardrails: resolvers are PURE READS (investigating never mutates the
entity); the envelope always passes ``fence_untrusted`` before reaching a prompt;
no surface grows its own bespoke "chat about this" wiring.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

# Snapshot cap: a huge entity must not blow the first turn's budget. Truncation
# is VISIBLE (the notice line) so the model knows it saw a partial snapshot.
_SNAPSHOT_CAP = 8_192
_TRUNCATE_NOTICE = "\n…[snapshot truncated — open the source surface for the full entity]"


@dataclass
class InvestigateContext:
    """The typed envelope one investigate action stages onto a chat session."""

    kind: str  # registry key (inbox_item | loop_finding | notification | …)
    id: str  # entity id within the owning store
    title: str  # human label for the chat header chip
    snapshot: str  # composed server-side; capped; fenced at INJECTION (never here)
    back_link: str  # hash route to the source surface (e.g. "#/loops/abc")
    suggested_agent: str = ""  # "" = session default
    suggested_task_mode: str = "ask"  # ask | plan | agent — default ask (read-only)
    opening_prompt: str = ""  # composer pre-fill (editable, never auto-sent)

    def to_dict(self) -> dict:
        return asdict(self)


# (entity_id, dashboard state) -> envelope | None. Registered by owning modules
# at import/boot; pure reads only (no store writes) — tested per kind.
Resolver = Callable[[str, object], "InvestigateContext | None"]

_RESOLVERS: dict[str, Resolver] = {}


def register_investigate_resolver(kind: str, fn: Resolver) -> None:
    """Register the resolver for one entity kind. Last registration wins
    (idempotent across re-imports); the kind key is the registry's vocabulary."""
    _RESOLVERS[kind] = fn


def known_kinds() -> tuple[str, ...]:
    return tuple(sorted(_RESOLVERS))


def resolve(kind: str, entity_id: str, state) -> InvestigateContext | None:
    """Dispatch to the kind's resolver; None = unknown entity. Raises KeyError on
    an unknown KIND (the route maps it to 400 vs the entity 404)."""
    fn = _RESOLVERS[kind]
    ctx = fn(entity_id, state)
    if ctx is None:
        return None
    if len(ctx.snapshot) > _SNAPSHOT_CAP:
        ctx.snapshot = ctx.snapshot[:_SNAPSHOT_CAP] + _TRUNCATE_NOTICE
    return ctx


# ── Core resolvers (the S1 reference pair) ───────────────────────────────────
# Registered here (not in the owning modules) so importing personalclaw.investigate
# is sufficient — the route handler imports this module, guaranteeing registration
# without adding import-order coupling to inbox_service/loop startup.


def _resolve_inbox_item(entity_id: str, state) -> InvestigateContext | None:
    """An inbox item: sender/channel/classification + the message body and thread
    context. The body is EXTERNAL text — it rides the snapshot raw here and is
    fenced once, at injection."""
    try:
        svc = getattr(state, "_inbox_svc", None)
        item = svc.inbox.items.get(entity_id) if svc is not None else None
    except Exception:  # noqa: BLE001
        item = None
    if item is None:
        return None
    lines = [
        f"Inbox item {item.id}",
        f"From: {item.sender_name or item.sender_id}",
        f"Channel: {item.channel_name or item.channel}",
        f"Classification: {item.classification} (confidence: {item.confidence})",
        f"Status: {item.status}",
    ]
    for turn in (item.thread_context or [])[-8:]:
        who = str(turn.get("sender_name") or turn.get("sender") or "someone")
        txt = str(turn.get("text") or "").strip()
        if txt:
            lines.append(f"[thread] {who}: {txt}")
    lines.append(f"Message: {item.message or ''}")
    if item.draft:
        lines.append(f"Drafted reply: {item.draft}")
    return InvestigateContext(
        kind="inbox_item",
        id=entity_id,
        title=f"Inbox: {item.sender_name or item.sender_id}",
        snapshot="\n".join(lines),
        back_link="#/inbox",
        opening_prompt="Help me understand this message — what does it need from me?",
    )


def _resolve_loop_finding(entity_id: str, state) -> InvestigateContext | None:
    """A loop finding, addressed ``<loop_id>:<cycle>`` (the FE's target form) or a
    bare loop id (→ the latest finding). Includes the loop's goal + the finding +
    that cycle's judge verdict when one exists."""
    from personalclaw.loop import store as loop_store

    loop_id, _, cycle_s = entity_id.partition(":")
    loop = loop_store.get(loop_id)
    if loop is None:
        return None
    findings = loop_store.get_findings(loop_id)
    if not findings:
        return None
    finding = None
    if cycle_s:
        try:
            want = int(cycle_s)
            finding = next((f for f in findings if int(f.get("cycle", -1)) == want), None)
        except ValueError:
            finding = None
    if finding is None:
        finding = findings[-1]
    cycle = finding.get("cycle", "?")
    lines = [
        f"Loop: {loop.name or loop.id} (kind: {loop.kind}, status: {loop.status})",
        f"Task: {loop.task}",
        f"Finding (cycle {cycle}):",
    ]
    for key in ("summary", "key_insight", "evidence"):
        val = str(finding.get(key) or "").strip()
        if val:
            lines.append(f"  {key}: {val}")
    try:
        verdicts = loop_store.get_verdicts(loop_id)
        v = next((v for v in verdicts if v.get("cycle") == finding.get("cycle")), None)
        if v:
            lines.append(
                f"Judge verdict (cycle {cycle}): done={v.get('done')} "
                f"quality={v.get('quality_score')} marginal={v.get('marginal_value')} "
                f"reasoning: {v.get('done_reason') or v.get('reasoning') or ''}"
            )
    except Exception:  # noqa: BLE001 — the verdict is enrichment, not a requirement
        pass
    return InvestigateContext(
        kind="loop_finding",
        id=entity_id,
        title=f"Finding · {loop.name or loop.id}",
        snapshot="\n".join(lines),
        back_link=f"#/loops/{loop_id}",
        opening_prompt=(
            "Walk me through this finding — what did the loop discover and does it hold up?"
        ),
    )


register_investigate_resolver("inbox_item", _resolve_inbox_item)
register_investigate_resolver("loop_finding", _resolve_loop_finding)
