"""Per-scope trust records — the earned-autonomy ledger behind the rung store (ES-13).

One JSON record per action-type scope, persisted under ``~/.personalclaw/evals/trust/``.
Each record captures the evidence that licensed the scope's current rung — the same
inputs :func:`personalclaw.guardrails.autonomy.resolve_rung` already consumes (grant
rung, grant time, evidence window, demotion history) plus the eligibility counters
observed at grant time. The record is written through from ``grant_rung``/``demote``
and read back by ``resolve_rung`` on every decision: a record marked ``revoked``
clamps its scope to the floor until a human re-grants.

Two deliberate rails:

- **One rung dialect.** This module mints NO rung vocabulary. Every rung name it
  stores or validates comes from :data:`personalclaw.guardrails.autonomy.RUNGS`; a
  record carrying an unknown rung is treated as absent (fail-safe), never coerced.
- **One authority per field.** Grant/demotion mechanics (cooldowns, clamps) stay in
  ``autonomy.py``'s store. The record adds what that store does not have — durable
  per-scope evidence and a ``revoked`` flag — and never overrides a grant upward.

Write failures never block a grant (the grant is the user's decision; the ledger is
its receipt). Read failures never license anything (an unreadable record reads as
revoked=False, absent evidence — exactly the pre-ES-13 behavior).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir
from personalclaw.record_ids import record_path

logger = logging.getLogger(__name__)

_MAX_CAUSE_CHARS = 200
_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


@dataclass(frozen=True)
class TrustEvidence:
    """The eligibility inputs observed when the current rung was granted."""

    clean_approvals: int = 0
    rejections: int = 0
    observed_days: float = 0.0
    evidence_window: str = ""


@dataclass(frozen=True)
class TrustRecord:
    """The persisted trust state for one action-type scope.

    ``rung`` is always a name from ``autonomy.RUNGS`` — validated on load, never
    invented here. ``revoked`` is the one field ``resolve_rung`` acts on directly:
    a revoked scope resolves at its floor regardless of any standing grant, until a
    fresh grant (which clears the flag) is accepted by a human.
    """

    key: str
    rung: str
    granted_at: str = ""
    granted_by: str = "user"
    evidence: TrustEvidence = field(default_factory=TrustEvidence)
    demotion_count: int = 0
    last_demotion_cause: str = ""
    revoked: bool = False
    revoked_cause: str = ""


def trust_dir() -> Path:
    """``~/.personalclaw/evals/trust/`` — created on first reference."""
    d = config_dir() / "evals" / "trust"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(key: str) -> str:
    """A filesystem-safe, collision-resistant name for one scope key."""
    cleaned = _SLUG_RE.sub("_", key.lower()).strip("_") or "scope"
    # Length-suffix disambiguates keys that clean to the same slug.
    return f"{cleaned[:80]}-{len(key)}"


def _record_path(key: str) -> Path:
    return record_path(trust_dir(), _slug(key))


def _valid_rung(rung: object) -> bool:
    from personalclaw.guardrails.autonomy import rung_rank

    return isinstance(rung, str) and rung_rank(rung) >= 0


def load_record(key: str) -> TrustRecord | None:
    """The stored record for ``key``, or ``None`` when absent or unreadable.

    Fail-safe by construction: a corrupt file, a key mismatch, or a rung outside
    ``autonomy.RUNGS`` all read as *no record* — nothing is licensed by a record
    this module cannot vouch for.
    """
    path = _record_path(key)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("trust record unreadable for %s — treated as absent", key, exc_info=True)
        return None
    if not isinstance(raw, dict) or raw.get("key") != key:
        logger.warning("trust record for %s is malformed — treated as absent", key)
        return None
    if not _valid_rung(raw.get("rung")):
        logger.warning(
            "trust record for %s carries unknown rung %r — treated as absent "
            "(guardrails/autonomy.py is the only rung dialect)",
            key,
            raw.get("rung"),
        )
        return None
    ev_raw = raw.get("evidence")
    ev: dict = ev_raw if isinstance(ev_raw, dict) else {}
    try:
        return TrustRecord(
            key=key,
            rung=raw["rung"],
            granted_at=str(raw.get("granted_at", "")),
            granted_by=str(raw.get("granted_by", "user")),
            evidence=TrustEvidence(
                clean_approvals=int(ev.get("clean_approvals", 0)),
                rejections=int(ev.get("rejections", 0)),
                observed_days=float(ev.get("observed_days", 0.0)),
                evidence_window=str(ev.get("evidence_window", "")),
            ),
            demotion_count=int(raw.get("demotion_count", 0)),
            last_demotion_cause=str(raw.get("last_demotion_cause", "")),
            revoked=bool(raw.get("revoked", False)),
            revoked_cause=str(raw.get("revoked_cause", "")),
        )
    except (TypeError, ValueError):
        logger.warning("trust record for %s has malformed fields — treated as absent", key)
        return None


def _save(record: TrustRecord) -> None:
    try:
        atomic_write(_record_path(record.key), json.dumps(asdict(record), indent=1))
    except OSError:
        logger.warning("trust record write failed for %s", record.key, exc_info=True)


def record_grant(
    key: str,
    rung: str,
    *,
    granted_at: str,
    evidence_window: str = "",
    clean_approvals: int = 0,
    rejections: int = 0,
    observed_days: float = 0.0,
) -> None:
    """Write the receipt for an accepted grant. Clears any standing revocation.

    Called from ``autonomy.grant_rung`` AFTER the grant is stored — the record is
    the durable evidence trail, not a second gate. An invalid rung is refused here
    too (the caller validated already; this keeps the file trustworthy on its own).
    """
    if not _valid_rung(rung):
        logger.warning("trust record refused unknown rung %r for %s", rung, key)
        return
    prior = load_record(key)
    _save(
        TrustRecord(
            key=key,
            rung=rung,
            granted_at=granted_at,
            granted_by="user",
            evidence=TrustEvidence(
                clean_approvals=clean_approvals,
                rejections=rejections,
                observed_days=observed_days,
                evidence_window=evidence_window[:_MAX_CAUSE_CHARS],
            ),
            demotion_count=prior.demotion_count if prior else 0,
            last_demotion_cause=prior.last_demotion_cause if prior else "",
            revoked=False,
            revoked_cause="",
        )
    )


def record_demotion(key: str, *, floor: str, cause: str, at: str) -> None:
    """Write the receipt for a demotion: rung drops to the floor, revoked is set.

    Revocation is the fail-safe direction, so it needs no human (mirrors the plan's
    §4.2 ruling); only :func:`record_grant` — a human click — clears it.
    """
    if not _valid_rung(floor):
        logger.warning("trust record refused unknown floor %r for %s", floor, key)
        return
    prior = load_record(key)
    _save(
        TrustRecord(
            key=key,
            rung=floor,
            granted_at=at,
            granted_by="system",
            evidence=prior.evidence if prior else TrustEvidence(),
            demotion_count=(prior.demotion_count if prior else 0) + 1,
            last_demotion_cause=cause[:_MAX_CAUSE_CHARS],
            revoked=True,
            revoked_cause=cause[:_MAX_CAUSE_CHARS],
        )
    )


def is_revoked(key: str) -> bool:
    """Does a standing revocation apply to ``key``? Absent/unreadable → ``False``.

    ``False`` here restores exactly the pre-record behavior — the flat rung store
    (with its own cooldown clamp) remains the authority on what is granted.
    """
    record = load_record(key)
    return record is not None and record.revoked
