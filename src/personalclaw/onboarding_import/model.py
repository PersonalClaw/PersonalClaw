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
        if self.secrets_skipped:
            self.notes.append(
                f"{self.secrets_skipped} credential value(s) or file(s) were skipped "
                "and not imported."
            )
        if self.redactions:
            self.notes.append(
                f"{self.redactions} credential-like string(s) were redacted from imported text."
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
