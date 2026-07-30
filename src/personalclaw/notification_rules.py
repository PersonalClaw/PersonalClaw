"""Per-(source, kind) notification rules (INBOX-NOTIFICATIONS-UNIFICATION C2/C3).

Before this, notification policy was one global switch: mute-all, a minimum severity, and
quiet hours. Every emitter was treated identically, so "stop telling me about heartbeats
but always interrupt me when a loop needs input" was inexpressible — the only way to
quieten one noisy kind was to raise the floor for everything.

This module adds the second axis. Each ``(source, kind)`` from
:mod:`personalclaw.notification_kinds` gets a rule:

* **mode** — ``never`` (drop), ``badge`` (persist, no toast), ``immediate`` (today's
  behavior), ``digest`` (batch for the scheduled summary).
* **targets** — where an ``immediate`` goes: ``dashboard`` today, ``channel_dm`` when a
  channel is configured, ``push``/``native`` reserved for the mobile and desktop plans.
* **conditions** — keywords / name-mention that ESCALATE a quieter mode to ``immediate``.

**The global gate still runs first, unchanged.** ``notification_allowed()`` in
`providers/entity_routes.py` remains the outermost check: mute-all means mute, whatever a
rule says. Rules refine *delivery* for notifications that already passed the gate; they
never resurrect a suppressed one. Keeping that order means the existing settings keep
their meaning and this file cannot become a way to bypass them.

**Conditions generalize the inbox's own alert fields.** The keyword and name-mention
semantics are lifted verbatim from ``inbox.evaluate_alert`` — case-insensitive substring
for keywords, whole-word matching on name parts of 3+ characters — so the behavior users
already configured survives the move to rules (S3 backfills those fields into conditions).

**Every failure path is fail-OPEN.** A missing file, malformed JSON, a bad mode, a corrupt
rule: all fall back to the registry default, which is ``immediate``. This mirrors the
existing gate ("a broken settings file must not silence the system") and is the
deliberate choice for an attention system — a notification shown when it could have been
batched is a small annoyance, while one silently dropped can lose a loop that needed an
answer. The one exception is ``mode: never``, which is only ever honored when it was read
cleanly from a well-formed rule.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personalclaw import notification_kinds as nk
from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

#: Delivery targets. ``push``/``native`` are accepted and persisted but inert until
#: MOBILE-COMPANION / DESKTOP-CAPABILITIES land — storing them now means a user's choice
#: survives, rather than being silently dropped and needing re-entry later.
TARGETS: tuple[str, ...] = ("dashboard", "channel_dm", "push", "native")
DEFAULT_TARGETS: tuple[str, ...] = ("dashboard",)

#: Digest defaults. 08:00 local, matching the plan's morning-digest intent.
DEFAULT_DIGEST_SCHEDULE = "0 8 * * *"

#: The digest queue. Append-only JSONL, trimmed at 2x the cap so a digest that never runs
#: (cron disabled, machine asleep) cannot grow without bound.
DIGEST_QUEUE_NAME = "digest_queue.jsonl"
DIGEST_QUEUE_CAP = 500

_RULES_ENTITY = "notification_rules"

#: Minimum length of a name part that may trigger a name-mention match. Lifted from
#: ``inbox.evaluate_alert``: initials and particles ("de", "van") false-positive badly.
_MIN_NAME_PART = 3


@dataclass(frozen=True)
class Conditions:
    """Escalation conditions. Empty conditions never escalate."""

    keywords: tuple[str, ...] = ()
    name_mention: bool = False

    def matches(self, text: str, user_name: str = "") -> str:
        """Why this escalates, or "" — the same shape as ``inbox.evaluate_alert``."""
        low = (text or "").lower()
        if not low:
            return ""
        for kw in self.keywords:
            k = str(kw).strip().lower()
            if k and k in low:
                return f"keyword: {kw}"
        if self.name_mention and user_name.strip():
            for part in user_name.strip().lower().split():
                if len(part) >= _MIN_NAME_PART and re.search(rf"\b{re.escape(part)}\b", low):
                    return "name mention"
        return ""


@dataclass(frozen=True)
class Rule:
    """The resolved policy for one ``(source, kind)``."""

    source: str
    kind: str
    mode: str = "immediate"
    targets: tuple[str, ...] = DEFAULT_TARGETS
    conditions: Conditions = field(default_factory=Conditions)

    @property
    def key(self) -> str:
        return f"{self.source}/{self.kind}"

    def escalated(self) -> "Rule":
        """This rule at ``immediate``.

        Escalation is capped at ``immediate`` rather than promoting targets: a keyword hit
        means "show me now", not "also text me" — adding delivery channels the user never
        selected for this kind would be a surprise, and channel_dm leaves the machine.
        """
        if self.mode == "immediate":
            return self
        return Rule(self.source, self.kind, "immediate", self.targets, self.conditions)


def _rules_path() -> Path:
    return config_dir() / "entity_settings" / f"{_RULES_ENTITY}.json"


def digest_queue_path() -> Path:
    return config_dir() / DIGEST_QUEUE_NAME


def _coerce_targets(raw: Any) -> tuple[str, ...]:
    """Known targets from *raw*, order-preserving and de-duplicated.

    An unknown target is dropped rather than rejected: a rules file written by a NEWER
    build (one that knows a target this build doesn't) must still load its other targets
    instead of failing the whole rule closed.
    """
    if not isinstance(raw, list):
        return DEFAULT_TARGETS
    out: list[str] = []
    for t in raw:
        name = str(t).strip().lower()
        if name in TARGETS and name not in out:
            out.append(name)
    return tuple(out) if out else DEFAULT_TARGETS


def _coerce_conditions(raw: Any) -> Conditions:
    if not isinstance(raw, dict):
        return Conditions()
    kws_raw = raw.get("keywords")
    keywords = (
        tuple(str(k).strip() for k in kws_raw if str(k).strip())
        if isinstance(kws_raw, list)
        else ()
    )
    return Conditions(keywords=keywords, name_mention=bool(raw.get("name_mention")))


def _coerce_rule(source: str, kind: str, raw: Any, default_mode: str) -> Rule:
    """One rule from its persisted form, falling back to *default_mode* per field.

    Per-FIELD fallback, not per-rule: a rule with a good mode and a malformed targets
    list keeps its mode. Failing the whole rule back to defaults would discard a
    deliberate ``never`` because of an unrelated typo.
    """
    if not isinstance(raw, dict):
        return Rule(source, kind, default_mode)
    mode = str(raw.get("mode", default_mode)).strip().lower()
    if mode not in nk.MODES:
        logger.warning(
            "notification rule %s/%s has unknown mode %r — using %s",
            source,
            kind,
            raw.get("mode"),
            default_mode,
        )
        mode = default_mode
    return Rule(
        source=source,
        kind=kind,
        mode=mode,
        targets=_coerce_targets(raw.get("targets")),
        conditions=_coerce_conditions(raw.get("conditions")),
    )


def load_rules() -> dict[str, Any]:
    """The raw rules document, or ``{}`` when absent/unreadable (fail-open).

    Runs the inbox-alert backfill first, so the very first read after an upgrade already
    reflects the keyword/name-mention alerts the user had configured.
    """
    _backfill_inbox_alerts()
    path = _rules_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("notification_rules.json unreadable — using registry defaults")
        return {}
    return data if isinstance(data, dict) else {}


def _backfill_inbox_alerts() -> None:
    """Project legacy ``inbox.json`` alert fields onto the channel-message rules, once.

    The inbox used to carry its own two-field alert config (``alert_keywords``,
    ``alert_on_name_mention``) evaluated at ingestion. Rules generalize exactly that, so
    those fields move here rather than being dropped — a user who configured "alert me when
    someone says deploy" must keep that behavior across the upgrade without re-entering it.

    **Idempotent by data inspection, not by a version number** (there is no schema version
    for entity settings, and inventing one is the machinery the doctrine rejects): the
    backfill runs only when the rules file is ABSENT and the legacy fields are still
    present. Writing the rules file is itself the marker that it has run, so a user who
    later clears their keywords does not get them resurrected.

    Deliberately does NOT delete the legacy fields here — a read path must not mutate a
    different store. `load_inbox_settings()` stops returning them in the same change, which
    is what actually retires them.
    """
    if _rules_path().is_file():
        return
    try:
        from personalclaw.providers.entity_routes import legacy_inbox_alert_fields

        legacy = legacy_inbox_alert_fields()
    except Exception:
        logger.debug("inbox alert backfill: legacy read failed", exc_info=True)
        return
    keywords = [str(k).strip() for k in (legacy.get("alert_keywords") or []) if str(k).strip()]
    name_mention = bool(legacy.get("alert_on_name_mention"))
    if not keywords and not name_mention:
        return

    conditions = {"keywords": keywords, "name_mention": name_mention}
    # Both kinds, because an alert was about the MESSAGE arriving and a mention is the same
    # event seen from the other side; splitting them would silently narrow what the user set.
    doc: dict[str, Any] = {
        "rules": {
            "inbox/alert": {"mode": "immediate", "conditions": dict(conditions)},
            "agent/message": {"mode": "immediate", "conditions": dict(conditions)},
        }
    }
    try:
        save_rules(doc)
        logger.info(
            "migrated inbox alert config to notification rules (%d keyword(s), name_mention=%s)",
            len(keywords),
            name_mention,
        )
    except OSError:
        logger.warning("inbox alert backfill: write failed", exc_info=True)


def save_rules(doc: dict[str, Any]) -> None:
    path = _rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(doc, indent=2) + "\n")


def resolve_rule(source: str, kind: str) -> Rule:
    """The effective rule for ``(source, kind)`` — registry default when unset."""
    registered = nk.resolve_kind(source, kind)
    stored = load_rules().get("rules")
    raw = stored.get(registered.key) if isinstance(stored, dict) else None
    return _coerce_rule(registered.source, registered.kind, raw, registered.default_mode)


def resolve_rule_for_legacy(flat_kind: str) -> Rule:
    """The effective rule for a flat pre-registry kind string (the wire format)."""
    registered = nk.kind_for_legacy(flat_kind)
    return resolve_rule(registered.source, registered.kind)


def digest_settings() -> dict[str, Any]:
    """The digest schedule block, with defaults filled in."""
    raw = load_rules().get("digest")
    schedule = DEFAULT_DIGEST_SCHEDULE
    if isinstance(raw, dict):
        candidate = str(raw.get("schedule", "")).strip()
        if len(candidate.split()) == 5:
            schedule = candidate
        elif candidate:
            logger.warning("digest schedule %r is not a 5-field cron — using default", candidate)
    return {"schedule": schedule}


def rules_document() -> dict[str, Any]:
    """The full effective document: every registered kind with its resolved rule.

    This is what the settings matrix renders — the registry is the row list, so a kind
    with no stored rule still appears (showing its default) rather than being invisible
    until someone edits it.
    """
    stored = load_rules().get("rules")
    stored = stored if isinstance(stored, dict) else {}
    rows = []
    for registered in nk.all_kinds():
        rule = _coerce_rule(
            registered.source,
            registered.kind,
            stored.get(registered.key),
            registered.default_mode,
        )
        rows.append(
            {
                "key": rule.key,
                "source": rule.source,
                "kind": rule.kind,
                "label": registered.label,
                "severity": registered.default_severity,
                "mode": rule.mode,
                "default_mode": registered.default_mode,
                "configured": registered.key in stored,
                "targets": list(rule.targets),
                "conditions": {
                    "keywords": list(rule.conditions.keywords),
                    "name_mention": rule.conditions.name_mention,
                },
            }
        )
    return {"rules": rows, "digest": digest_settings(), "targets": list(TARGETS)}


def queue_for_digest(note: dict[str, Any]) -> None:
    """Append *note* to the digest queue, trimming at 2x the cap.

    Best-effort by design: a digest is a convenience, and failing a notification's
    delivery because a queue file is unwritable would trade a small feature for a real
    loss. The exception is logged and swallowed.
    """
    path = digest_queue_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(note, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("digest queue append failed", exc_info=True)
        return
    _trim_digest_queue(path)


def _trim_digest_queue(path: Path) -> None:
    """Keep the newest ``DIGEST_QUEUE_CAP`` entries once the file exceeds 2x that.

    Trimming at 2x rather than every append keeps the common path one write.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= DIGEST_QUEUE_CAP * 2:
            return
        keep = lines[-DIGEST_QUEUE_CAP:]
        atomic_write(path, "\n".join(keep) + "\n")
    except OSError:
        logger.warning("digest queue trim failed", exc_info=True)


def drain_digest_queue() -> list[dict[str, Any]]:
    """Read and clear the digest queue, returning its entries oldest-first.

    Read-then-truncate rather than read-then-delete: the file keeps its permissions and a
    concurrent appender's write lands in the next digest instead of recreating a file this
    process just unlinked. A malformed line is skipped, not fatal — one bad append must
    not strand every queued notification.
    """
    path = digest_queue_path()
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("digest queue read failed", exc_info=True)
        return []
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("skipping malformed digest queue line")
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    try:
        atomic_write(path, "")
    except OSError:
        logger.warning("digest queue truncate failed", exc_info=True)
    return out
