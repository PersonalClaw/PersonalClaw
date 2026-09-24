"""The onboarding-import vocabulary — scan results, items, and write outcomes.

One shape for "something another local agent tool has that PersonalClaw could adopt"
(:class:`ImportItem`), one shape for what a scan found (:class:`ScanResult`), and one
closed four-value outcome vocabulary for what a writer did (:class:`WriteOutcome`).

Two properties are load-bearing and live here rather than in each scanner:

- **Fingerprint idempotence.** An item's identity is ``sha256(source\\0category\\0key)``
  — stable across re-scans and independent of the item's body, so re-importing an
  edited file updates nothing it already owns and never creates a second copy.
- **Secret-free payloads.** An item carries a redacted body and a secret-stripped
  structured payload. A scanner that finds a credential *counts* it
  (``ScanResult.secrets_skipped``) and drops it; the value never reaches an item, a
  note, a log line, or an error message.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum


class ImportCategory(str, Enum):
    """What KIND of thing an item is — and therefore which writer owns it.

    Closed on purpose: every member has a scanner that produces it AND a writer
    that consumes it (``writers._WRITERS`` is exhaustive over this enum and raises
    on an unmapped member). A category with no destination is not declared here.
    """

    INSTRUCTIONS = "instructions"  # CLAUDE.md / AGENTS.md → memory store
    MEMORIES = "memories"  # a tool's own memory notes → memory store
    MCP_SERVERS = "mcp_servers"  # .mcp.json / config.toml → ~/.personalclaw/mcp.json
    SKILLS = "skills"  # skills/<name>/ → skills/imported/<source>/<name>/
    SETTINGS = "settings"  # foreign settings → the review queue, never live config


class WriteOutcome(str, Enum):
    """What a writer did with one item. Four values, no fifth.

    ``imported`` — the item reached its destination.
    ``existing`` — this exact item is already there; nothing was written.
    ``conflict`` — something of the same name is there and DIFFERS. The existing
    thing is kept untouched and the conflict is reported for human review; a
    writer never resolves a conflict by overwriting.
    ``rejected`` — a security floor refused it (sensitive path, or the skill
    supply-chain scan blocked it).
    """

    IMPORTED = "imported"
    EXISTING = "existing"
    CONFLICT = "conflict"
    REJECTED = "rejected"


def fingerprint_of(source: str, category: ImportCategory | str, key: str) -> str:
    """``sha256(source\\0category\\0key)`` — the idempotence key.

    NUL-joined so no combination of source/category/key can collide with another by
    concatenation. Truncated to 16 hex chars: enough to be collision-free over a
    machine's config files, short enough to read in a UI.
    """
    cat = category.value if isinstance(category, ImportCategory) else str(category)
    raw = "\0".join((source, cat, key)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass(frozen=True)
class ImportItem:
    """One importable thing found by a scanner. Pure data — no store, no session.

    ``text`` is the redacted body (instructions/memories); ``payload`` is the
    secret-stripped structured value (mcp_servers/settings); ``path`` is the source
    directory for skills. Exactly one of the three is populated per category.
    """

    source: str
    category: ImportCategory
    key: str
    title: str = ""
    text: str = ""
    payload: dict = field(default_factory=dict)
    path: str = ""
    #: How many credential/exfiltration-URL redactions were applied to ``text``.
    #: A count, never the matched value.
    redactions: int = 0

    @property
    def fingerprint(self) -> str:
        return fingerprint_of(self.source, self.category, self.key)

    def to_dict(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "source": self.source,
            "category": self.category.value,
            "key": self.key,
            "title": self.title or self.key,
            "redactions": self.redactions,
        }


def withheld_notes(*, secrets_skipped: int, redactions: int) -> list[str]:
    """The ONE place the two withheld-credential sentences are composed.

    They were written twice — here (via :meth:`ScanResult.note_withheld`) and again inline in
    ``writers.import_report`` — and the two copies said the same thing in the same words, which is
    exactly how a copy stops being the same thing. Both classes carry the identical
    ``secrets_skipped`` / ``redactions`` / ``notes`` fields, so a free function serves both without
    either one growing a base class.

    🔑 The counts are stated with real plurals rather than a ``(s)`` hedge, and the VERB agrees too.
    Two nouns joined by "or" still take a number: at one it is "1 credential value or file **was**
    skipped", at three "3 credential values or files **were** skipped". A parenthetical would have
    hidden that second disagreement entirely.

    🪤 The sentence is deliberately identical in shape to the pre-import one the frontend
    composes (`app/onboarding/ImportStep.tsx`: "N credential values or files will not be
    imported") because a user meets both in one flow — the warning before, this note after. If
    one is reworded, reword both, and note that the frontend's is a SEPARATE producer (a React
    expression, not this function): the two cannot be shared across the language boundary, only
    kept in step.

    Says how much was withheld. Never what — no value, path or key appears here.
    """
    notes: list[str] = []
    if secrets_skipped:
        plural = secrets_skipped != 1
        notes.append(
            f"{secrets_skipped} credential value{'s' if plural else ''} or "
            f"file{'s' if plural else ''} {'were' if plural else 'was'} skipped and not imported."
        )
    if redactions:
        plural = redactions != 1
        notes.append(
            f"{redactions} credential-like string{'s' if plural else ''} "
            f"{'were' if plural else 'was'} redacted from imported text."
        )
    return notes


@dataclass
class ScanResult:
    """What one source's scanner found. Serializable, secret-free, comparable.

    ``present`` distinguishes "the tool isn't installed" (no root) from "the tool is
    installed but has nothing to import" — the onboarding step words those very
    differently.
    """

    source: str
    display_name: str
    root: str
    present: bool
    items: list[ImportItem] = field(default_factory=list)
    #: Credential-bearing files refused unread + secret config keys dropped. A count
    #: the user is shown so they learn something was withheld.
    secrets_skipped: int = 0
    #: Redactions applied to text that WAS imported (the body kept, the secret gone).
    redactions: int = 0
    #: Human-readable, value-free explanations ("skipped 1 credential file").
    notes: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        """Per-category item counts — what the onboarding checkboxes show."""
        counter = Counter(item.category.value for item in self.items)
        return {cat.value: counter.get(cat.value, 0) for cat in ImportCategory}

    def by_category(self, category: ImportCategory) -> list[ImportItem]:
        return [item for item in self.items if item.category is category]

    def fingerprints(self) -> set[str]:
        return {item.fingerprint for item in self.items}

    def note_withheld(self) -> None:
        """Say that something was withheld, and how much. Never what.

        Every scanner ends with this so the user always learns a credential existed
        without the credential appearing in a note, a log, or a UI string.
        """
        self.notes.extend(
            withheld_notes(secrets_skipped=self.secrets_skipped, redactions=self.redactions)
        )

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "display_name": self.display_name,
            "root": self.root,
            "present": self.present,
            "counts": self.counts(),
            "items": [item.to_dict() for item in self.items],
            "secrets_skipped": self.secrets_skipped,
            "redactions": self.redactions,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class WriteResult:
    """What happened to one item at its destination."""

    fingerprint: str
    source: str
    category: ImportCategory
    key: str
    outcome: WriteOutcome
    destination: str = ""
    #: Why — value-free ("mcp server 'x' already configured with a different command").
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "source": self.source,
            "category": self.category.value,
            "key": self.key,
            "outcome": self.outcome.value,
            "destination": self.destination,
            "detail": self.detail,
        }


@dataclass
class ImportReport:
    """The result of one import run — per-item outcomes plus the withheld counts."""

    results: list[WriteResult] = field(default_factory=list)
    secrets_skipped: int = 0
    redactions: int = 0
    notes: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        counter = Counter(r.outcome.value for r in self.results)
        return {out.value: counter.get(out.value, 0) for out in WriteOutcome}

    def conflicts(self) -> list[WriteResult]:
        return [r for r in self.results if r.outcome is WriteOutcome.CONFLICT]

    def to_dict(self) -> dict:
        return {
            "counts": self.counts(),
            "results": [r.to_dict() for r in self.results],
            "secrets_skipped": self.secrets_skipped,
            "redactions": self.redactions,
            "notes": list(self.notes),
        }
