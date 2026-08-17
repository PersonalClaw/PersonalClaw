"""Chatless tile refresh — the layout/data split, executed (AMBIENT-SURFACES §2.1-§2.4).

A live tile is three things, and the whole cost argument rests on keeping them apart:

* a **skeleton** — an artifact whose body carries ``{{...}}`` binding slots, authored ONCE
  by a model (a chat turn or a workflow stage);
* **data nodes** — the "bound data workflow", whose degenerate and only pre-substrate case
  is a list of action-provider dispatches (§2.1);
* a **render transform** — :func:`render_skeleton`: deterministic, LLM-free interpolation of
  the node outputs into the skeleton, handed to the ``artifact-update`` sink (§2.2).

First generation is creative. Every refresh after it is pure substitution, so a steady-state
refresh costs zero tokens. That is the practitioner finding this implements ("same layout,
new data, no re-prompting") and it is also why the refresh must not be able to reach a model:
the whole cost claim is one accidental `stage` node away from being false.

**Ledger weight, not run weight (§2.3).** A refresh writes ONE
:data:`~personalclaw.ledger.kinds.TILE_REFRESHED` row and creates no run directory — the
"1440 run dirs a day" critique is structurally avoided rather than mitigated. The row carries
tokens, cost, duration and per-node outcomes, because the tile header renders exactly those
(§2.4) and a second status channel would let the chip and the ledger disagree.

**A failed refresh writes nothing (§2.4).** If any data node fails, the render is skipped and
the artifact keeps its last-good body: the tile stays painted and its chip turns red. Writing
a half-rendered body would replace real content with `{{...}}` literals, which is the silent
empty panel this section exists to prevent.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from personalclaw.atomic_write import atomic_write
from personalclaw.dashboard import views_store as store
from personalclaw.ledger import (
    EVENTS_FILE,
    TILE_REFRESHED,
    LedgerStore,
    LedgerWriter,
    read_events,
    stable_json,
)
from personalclaw.workflows.bindings import BindingContext, BindingError, resolve

logger = logging.getLogger(__name__)

#: The home-relative root of the per-tile ledgers. One directory PER TILE (bounded by
#: ``AmbientConfig.max_tiles``), never one per refresh — a directory per fire is exactly the
#: run-weight §2.3 refuses.
LEDGER_DIRNAME = "dashboard_tiles"

#: Action providers a TTL tile may dispatch. An allowlist, not a denylist: a tile fires with
#: no human present, so the safe set is the one that was argued for, and every member here is
#: read-only and zero-token. `bash`, `run-prompt`, `run-workflow`, `invoke-agent` and every
#: other write- or model-capable provider are absent BY CONSTRUCTION — a dashboard panel that
#: could shell out is an unattended-execution surface the user never consented to, and a tile
#: that could spawn a turn would make the zero-LLM claim false.
DATA_PROVIDERS = ("knowledge-retrieve", "knowledge-health", "artifact_inspect")

#: A tile key has to be a directory name, so it is derived and sanitized, never trusted.
_UNSAFE = re.compile(r"[^a-z0-9_-]+")


def tile_key(view_id: str, ref: str) -> str:
    """The ledger id for one tile. Deterministic — the same tile always reads its own history.

    ``__`` joins the two halves because a view id is hex and a ref is ``artifact:<slug>``;
    a single ``-`` would make ``view-a`` + ``b`` collide with ``view`` + ``a-b``.
    """
    view = _UNSAFE.sub("-", view_id.strip().lower()) or "view"
    slug = _UNSAFE.sub("-", ref.strip().lower().removeprefix("artifact:")) or "tile"
    return f"{view}__{slug}"


class _TileLedgerStore:
    """The four-call file store :class:`~personalclaw.ledger.LedgerStore` wants, over
    ``<home>/dashboard_tiles/<tile_key>/``.

    Read tolerates a missing or corrupt file (the storage convention) — a tile whose ledger
    was hand-edited must still render, with no freshness rather than a 500.
    """

    def _dir(self, run_id: str) -> Path:
        from personalclaw.config.loader import config_dir

        return config_dir() / LEDGER_DIRNAME / run_id

    def append_jsonl(self, run_id: str, filename: str, record: dict[str, Any]) -> None:
        d = self._dir(run_id)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / filename, "a", encoding="utf-8") as f:
            f.write(stable_json(record) + "\n")

    def read_jsonl(self, run_id: str, filename: str) -> list[dict[str, Any]]:
        p = self._dir(run_id) / filename
        if not p.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            import json

            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    rows.append(rec)
        except OSError:
            logger.debug("tile ledger unreadable: %s", p, exc_info=True)
        return rows

    def write_output(self, run_id: str, node_path: str, output: Any) -> str:
        rel = f"outputs/{_UNSAFE.sub('-', node_path.lower()) or 'out'}.json"
        target = self._dir(run_id) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, stable_json(output) + "\n")
        return rel

    def write_artifact(self, run_id: str, node_path: str, output: Any) -> str:
        rel = f"artifacts/{_UNSAFE.sub('-', node_path.lower()) or 'out'}.json"
        target = self._dir(run_id) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, stable_json(output) + "\n")
        return rel


_STORE = _TileLedgerStore()


@dataclass
class TileLedger(LedgerWriter):
    """The tile band as the ledger's SECOND producer (PP-4's stated gap).

    One typed emitter, because a refresh has exactly one thing to say. It speaks the shared
    vocabulary rather than minting a `tile_refresh_done` of its own — a private kind here
    would be the "fifth dialect" the extraction exists to prevent.
    """

    _store: ClassVar[LedgerStore] = _STORE  # type: ignore[assignment]

    @classmethod
    def resumed(cls, run_id: str) -> "TileLedger":
        """A writer whose ``seq`` CONTINUES this tile's existing journal.

        🔴 Found on a real drive, not reasoned about. A fresh writer per refresh restarts
        ``seq`` at 1 and re-mints ``<tile>-evt-1`` every time: two refreshes, two rows, one
        event id. ``event_id`` is what makes a re-emit an idempotent no-op, and duplicate ids
        are the exact tell for "N writers over one ledger" — a reader would have to conclude
        something was racing.

        ``_load_cache`` is the writer's OWN recovery pass (it restores ``seq`` in the same read),
        so it is used rather than re-deriving the maximum here: a second copy of that logic is
        precisely what the ledger extraction forbids.
        """
        writer = cls(run_id=run_id)
        writer._load_cache()
        return writer

    def refreshed(
        self,
        *,
        view_id: str,
        ref: str,
        ok: bool,
        duration_ms: int,
        nodes: list[dict[str, Any]],
        version: int = 0,
        rendered_bytes: int = 0,
        error: str = "",
    ) -> dict[str, Any]:
        """Write the one row. ``tokens``/``cost_usd`` are stated EXPLICITLY as zero rather
        than omitted: a reader that has to infer "no tokens" from a missing key cannot tell a
        free refresh from an unrecorded one, and "what did this cost me?" is the question §2.3
        exists to answer honestly."""
        return self.write(
            TILE_REFRESHED,
            view_id=view_id,
            ref=ref,
            ok=ok,
            tokens=0,
            cost_usd=0.0,
            duration_ms=duration_ms,
            nodes=nodes,
            version=version,
            rendered_bytes=rendered_bytes,
            error=error,
        )


@dataclass
class NodeOutcome:
    """One data node's result — the per-source chip's whole content (§2.4)."""

    id: str
    provider: str
    ok: bool
    error: str = ""
    duration_ms: int = 0


@dataclass
class RefreshResult:
    """What a refresh attempt did, and why."""

    #: Did the tile's artifact actually get re-rendered and written?
    refreshed: bool
    #: A machine-readable reason a refresh did NOT happen, "" when it did. Stable strings —
    #: the FE branches on them.
    reason: str = ""
    ok: bool = True
    nodes: list[NodeOutcome] = field(default_factory=list)
    row: dict[str, Any] = field(default_factory=dict)
    content: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "refreshed": self.refreshed,
            "reason": self.reason,
            "ok": self.ok,
            "nodes": [asdict(n) for n in self.nodes],
            "row": self.row,
        }


# ── the render transform ─────────────────────────────────────────────────────


def render_skeleton(skeleton: str, node_outputs: dict[str, Any]) -> str:
    """Interpolate ``{{nodes.<id>.output…}}`` slots into the stored skeleton.

    THE deterministic, LLM-free render transform (§2.1). It is a thin binding of
    :func:`personalclaw.workflows.bindings.resolve` on purpose: a second interpolator would
    be a second answer to what ``{{a.b | default('x')}}`` means, and a dashboard slot that
    resolved differently from a workflow binding is a bug nobody would find twice.

    Two properties are load-bearing and are asserted by tests rather than assumed:

    * **Deterministic** — the same skeleton and the same outputs produce a byte-identical
      body. Nothing here reads a clock or a random source, so a refresh that changed nothing
      writes the same bytes and the artifact store records no version churn.
    * **Total** — an unresolvable ref raises :class:`BindingError` rather than substituting
      an empty string, so a renamed data node produces a red chip instead of a panel that
      silently lost a number.
    """
    return str(resolve(skeleton, BindingContext(node_outputs=dict(node_outputs))))


# ── the refresh ──────────────────────────────────────────────────────────────


def _default_ttl() -> int:
    try:
        from personalclaw.config.loader import AppConfig

        return max(1, int(AppConfig.load().ambient.default_refresh_ttl_secs))
    except Exception:  # noqa: BLE001 — a config read must not break a refresh
        return 900


def last_row(view_id: str, ref: str) -> dict[str, Any]:
    """The newest ledger row for a tile, or ``{}``. What the header renders (§2.4)."""
    rows = read_events(_STORE, tile_key(view_id, ref), kinds={TILE_REFRESHED})
    return rows[-1] if rows else {}


def _epoch(ts: str) -> float:
    """Parse a ledger ``ts`` (``%Y-%m-%dT%H:%M:%SZ``, UTC) to epoch seconds, 0.0 if unusable.

    `calendar.timegm`, not `time.mktime` — the stamp is UTC (`ledger.writer.now()` uses
    `time.gmtime`), and `mktime` reads its struct as LOCAL time. The naive
    `mktime(...) - time.timezone` correction is wrong by an hour under DST, which shifts every
    TTL boundary by 3600s: measured, a 600s TTL fired again after 599s.
    """
    import calendar

    try:
        return float(calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return 0.0


def due(view_id: str, tile: store.DashboardTile, now: float) -> tuple[bool, float]:
    """Is this tile's TTL elapsed? Returns ``(due, age_secs)``.

    The scheduling decision, isolated so it can be tested AT ITS BOUNDARY: a refresh that
    fired on every read and one that never fired both satisfy a naive "it refreshed" check,
    and only the boundary distinguishes them. A tile with NO prior row is due — the first
    render has to happen, and ``age`` is 0.0 because there is nothing to age from.
    """
    ttl = tile.refresh.ttl_secs or _default_ttl()
    row = last_row(view_id, tile.ref)
    if not row:
        return True, 0.0
    age = now - _epoch(str(row.get("ts", "")))
    return age >= ttl, age


async def _run_data_node(
    node: store.TileDataNode, *, session_key: str, timeout: int = 30
) -> tuple[Any, NodeOutcome]:
    """Dispatch one data node through the action-provider registry.

    Returns ``(output, outcome)``. A refused provider is a node FAILURE, not a silent skip:
    the point of the allowlist is that the author finds out at the tile, and a skipped node
    would leave its slot unresolved and take the whole render down anyway — with a worse
    error.

    🔴 This is a FOURTH UNATTENDED DISPATCH SEAM (AUTONOMY-GUARDRAILS §1.2): a TTL tile fires
    with nobody watching, so it carries the same two gates the other three do — the kill switch
    (:func:`incident_active`) and the action denylist (:func:`enforce_action`, threaded with a
    session key so a SafetyProfile's extra globs are not silently skipped). The read-only
    allowlist above narrows WHICH providers can be named; these gate what a named one may touch.
    """
    started = time.monotonic()
    from personalclaw.guardrails.denylist import enforce_action
    from personalclaw.guardrails.incident import incident_active

    if incident_active():
        # FAIL-CLOSED. The kill switch exists to suspend unattended work; a dashboard that kept
        # fetching through an incident would be the quiet exception that makes it useless.
        return None, NodeOutcome(
            id=node.id,
            provider=node.provider,
            ok=False,
            error="incident mode is active — unattended tile refresh is suspended",
        )
    if node.provider not in DATA_PROVIDERS:
        return None, NodeOutcome(
            id=node.id,
            provider=node.provider,
            ok=False,
            error=(
                f"provider {node.provider!r} is not a tile data source — allowed: "
                f"{', '.join(DATA_PROVIDERS)}"
            ),
        )
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )

    # The built-ins register lazily on first action execution (the hooks path does the same
    # call). Resolving without it makes an unattended refresh depend on whether some earlier
    # request in this process happened to run an action — which is a refresh that works in the
    # UI and fails on a cold gateway.
    _ensure_default_providers_registered()
    provider = get_action_provider(node.provider)
    if provider is None:
        return None, NodeOutcome(
            id=node.id, provider=node.provider, ok=False, error="provider is not registered"
        )
    ctx = ActionContext(event="tile_refresh", payload={"node": node.id})
    decision = enforce_action(node.provider, dict(node.config), ctx, session_key=session_key)
    if decision.blocked:
        return None, NodeOutcome(
            id=node.id,
            provider=node.provider,
            ok=False,
            error=f"refused by the action denylist: {decision.reason}",
        )
    try:
        result = await provider.execute(
            dict(node.config),
            ctx,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — a provider that raises must not kill the band
        return None, NodeOutcome(
            id=node.id,
            provider=node.provider,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    ms = result.duration_ms or int((time.monotonic() - started) * 1000)
    if not result.success:
        return None, NodeOutcome(
            id=node.id,
            provider=node.provider,
            ok=False,
            error=result.error or "the data source reported no reason",
            duration_ms=ms,
        )
    return _parse_output(result.stdout), NodeOutcome(
        id=node.id, provider=node.provider, ok=True, duration_ms=ms
    )


def _parse_output(stdout: str) -> Any:
    """A provider's stdout as structured data when it is JSON, else the raw text.

    Both shapes are legitimate — `knowledge-retrieve` returns JSON, a text source returns
    prose — and a skeleton binds to whichever it declared. Guessing wrong in the JSON
    direction is the dangerous one, so a parse failure keeps the exact string.
    """
    import json

    text = stdout or ""
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            return json.loads(stripped)
        except ValueError:
            return text
    return text


async def refresh_tile(
    view_id: str,
    ref: str,
    *,
    force: bool = False,
    now: float | None = None,
) -> RefreshResult:
    """Refresh one tile: gate on TTL → run its data nodes → render → write → ledger one row.

    ``force`` is the tile's refresh BUTTON (a human asked, so the TTL is not a gate). The
    unattended path always passes the gate, which is what keeps a rendered dashboard from
    re-fetching on every paint.
    """
    now = time.time() if now is None else now
    tile = store.find_tile(view_id, ref)
    if tile is None:
        return RefreshResult(refreshed=False, reason="tile_not_found", ok=False)
    binding = tile.refresh
    if binding.mode != "ttl":
        return RefreshResult(refreshed=False, reason="not_bound")
    if not binding.skeleton:
        return RefreshResult(refreshed=False, reason="no_skeleton", ok=False)
    if not force:
        is_due, _age = due(view_id, tile, now)
        if not is_due:
            # The CURRENT row rides back even though nothing ran: this endpoint is what the
            # header polls, and returning an empty row here would make a tile refreshed a
            # minute ago render "never refreshed" on the very next poll.
            return RefreshResult(refreshed=False, reason="within_ttl", row=last_row(view_id, ref))

    started = time.monotonic()
    ledger = TileLedger.resumed(tile_key(view_id, ref))

    from personalclaw.artifacts.registry import get_provider

    artifacts = get_provider()
    skeleton_art = artifacts.get(binding.skeleton) if artifacts is not None else None
    if skeleton_art is None or not (skeleton_art.content or ""):
        outcome = RefreshResult(refreshed=False, reason="skeleton_missing", ok=False)
        outcome.row = ledger.refreshed(
            view_id=view_id,
            ref=ref,
            ok=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            nodes=[],
            error=f"skeleton artifact {binding.skeleton!r} has no body",
        )
        return outcome

    outputs: dict[str, Any] = {}
    outcomes: list[NodeOutcome] = []
    # A synthetic, per-tile session key. NOT "" — `enforce_action`'s empty key classifies the
    # dispatch as ATTENDED and skips the SafetyProfile layer entirely (the PHF-8 defect), and an
    # unattended refresh is exactly what that layer is for. Derived from the tile so a profile can
    # be bound to one tile rather than to the whole band.
    session_key = f"tile:{tile_key(view_id, ref)}"
    for node in binding.data:
        value, node_outcome = await _run_data_node(node, session_key=session_key)
        outcomes.append(node_outcome)
        if node_outcome.ok:
            outputs[node.id] = value

    failed = [o for o in outcomes if not o.ok]
    if failed:
        # LAST-GOOD WINS (§2.4). No render, no write — the tile keeps painting what it has and
        # the chip goes red. A partial render would put `{{...}}` literals where a number was.
        row = ledger.refreshed(
            view_id=view_id,
            ref=ref,
            ok=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            nodes=[asdict(o) for o in outcomes],
            error=failed[0].error,
        )
        return RefreshResult(
            refreshed=False, reason="data_failed", ok=False, nodes=outcomes, row=row
        )

    try:
        content = render_skeleton(skeleton_art.content or "", outputs)
    except BindingError as exc:
        row = ledger.refreshed(
            view_id=view_id,
            ref=ref,
            ok=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            nodes=[asdict(o) for o in outcomes],
            error=f"render transform failed: {exc}",
        )
        return RefreshResult(
            refreshed=False, reason="render_failed", ok=False, nodes=outcomes, row=row
        )

    # The write goes through the SAME sink a workflow uses (§2.2) rather than the artifact
    # store directly: version snapshotting, pruning and redaction are inherited, and the
    # chatless leg cannot drift from the workflow leg.
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )

    _ensure_default_providers_registered()
    sink = get_action_provider("artifact-update")
    slug = ref.removeprefix("artifact:")
    write_error = ""
    version = 0
    if sink is None:
        write_error = "the artifact-update sink is not registered"
    else:
        written = await sink.execute(
            {"slug": slug, "content": content, "kind": "widget"},
            ActionContext(event="tile_refresh", payload={"view_id": view_id, "ref": ref}),
        )
        if not written.success:
            write_error = written.error or "the artifact sink reported no reason"
        else:
            version = _version_of(written.stdout)

    row = ledger.refreshed(
        view_id=view_id,
        ref=ref,
        ok=not write_error,
        duration_ms=int((time.monotonic() - started) * 1000),
        nodes=[asdict(o) for o in outcomes],
        version=version,
        rendered_bytes=len(content.encode("utf-8")),
        error=write_error,
    )
    if write_error:
        return RefreshResult(
            refreshed=False, reason="write_failed", ok=False, nodes=outcomes, row=row
        )
    return RefreshResult(refreshed=True, ok=True, nodes=outcomes, row=row, content=content)


def _version_of(stdout: str) -> int:
    import json

    try:
        return int(json.loads(stdout or "{}").get("version", 0) or 0)
    except (ValueError, AttributeError, TypeError):
        return 0


def ledger_path(view_id: str, ref: str) -> Path:
    """Where a tile's ledger lives — the deep link's backing file (§2.4)."""
    from personalclaw.config.loader import config_dir

    return config_dir() / LEDGER_DIRNAME / tile_key(view_id, ref) / EVENTS_FILE
