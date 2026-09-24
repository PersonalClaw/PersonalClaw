"""Inbound request audit (MCP-READONLY-INBOUND §C1).

Every inbound request writes one JSONL line. This is deliberately its own log
rather than a subsection of the security event log: the SEL is the
security-relevant record (an auth failure, a cap breach), while this is the
complete request trace, including the boring successful reads — which is what you
actually need to answer "what did that client do yesterday?".

Refusals write to BOTH: here for the trace, and to the SEL because a rejected
credential on a network surface is a security event.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_FILE = "inbound_audit.jsonl"
# Trim at 2x so the file is rewritten rarely (mirrors the notifications trim
# mechanic: cheap amortized cost, bounded size).
_MAX_LINES = 5_000


def _audit_path() -> Path:
    from personalclaw.config.loader import config_dir

    home = Path(os.environ.get("PERSONALCLAW_HOME", config_dir()))
    return home / _FILE


def audit(
    surface: str,
    *,
    route: str,
    status: int,
    bytes_in: int = 0,
    bytes_out: int = 0,
    duration_ms: int = 0,
    refused: str = "",
    tool: str = "",
    client_id: str = "",
    rate_limited: bool = False,
) -> None:
    """Append one audit line. Never raises — auditing must not break a response.

    A failure to record is itself logged at debug: losing an audit line is bad,
    but failing the user's request because we couldn't write a log line is worse.

    ``client_id`` is the identity that Settings → External Access renders per-client
    last-seen and request counts FROM — derived from this file rather than collected
    into a second counter, so the number beside a client and the trail behind it
    cannot disagree.
    """
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "surface": surface,
        "client_id": client_id,
        "route": route,
        "status": status,
        "bytes_in": bytes_in,
        "bytes_out": bytes_out,
        "duration_ms": duration_ms,
        "rate_limited": bool(rate_limited),
    }
    if tool:
        row["tool"] = tool
    if refused:
        row["refused_reason"] = refused
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        _maybe_trim(path)
    except Exception:  # noqa: BLE001
        logger.debug("inbound: audit write failed", exc_info=True)

    if refused:
        _sel_refusal(surface, route, refused, client_id)


def _maybe_trim(path: Path) -> None:
    try:
        if path.stat().st_size < 512_000:  # cheap guard before counting lines
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) <= _MAX_LINES * 2:
            return
        keep = lines[-_MAX_LINES:]
        path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.debug("inbound: audit trim failed", exc_info=True)


def _sel_refusal(surface: str, route: str, reason: str, client_id: str = "") -> None:
    """Mirror a refusal into the security event log.

    The caller names the CLIENT when one was resolved. A refusal attributed only to
    `inbound:mcp` cannot answer "which integration is failing auth?", which is the
    first question anyone asks of this log.
    """
    try:
        from personalclaw.sel import sel

        caller = f"inbound:{surface}:{client_id}" if client_id else f"inbound:{surface}"
        sel().log_api_access(
            caller=caller,
            operation=route,
            outcome="denied",
            source="inbound",
            resources=reason[:200],
        )
    except Exception:  # noqa: BLE001
        logger.debug("inbound: SEL refusal log failed", exc_info=True)


def recent(limit: int = 100) -> list[dict]:
    """The most recent audit rows, newest first (operator surfacing)."""
    try:
        lines = _audit_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except (FileNotFoundError, OSError):
        return []
    out: list[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out
