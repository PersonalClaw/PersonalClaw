"""SDLC stage vocabulary for the code loop kind — the canonical ladder + the lateral
entry types. Pure data, no deps; lives in the unified ``loop`` package so the code kind
+ its classifier don't reach back into the legacy ``code`` package (cutover Slice 2e —
making ``loop/`` self-contained before the legacy engines are deleted)."""

from __future__ import annotations

# The canonical SDLC ladder — the ordered stages a greenfield/full run walks.
SDLC_STAGES: tuple[str, ...] = (
    "ideation",  # shape a raw idea into a problem statement
    "requirements",  # BRD — what must be true for users/business
    "design",  # TRD / tech design — how it'll be built
    "decomposition",  # break the design into ordered, executable tasks
    "implementation",  # write the code
    "verification",  # tests / QA — prove it works
    "review",  # code review / address CR comments
)

# Lateral entry types — tasks that don't begin at ideation and run a tailored,
# shorter stage plan (the classifier may still expand them).
LATERAL_ENTRIES: frozenset[str] = frozenset({"bugfix", "cr_comments", "refactor", "investigation"})

# Valid entry stages = the ladder ∪ the lateral entries.
ENTRY_STAGES: frozenset[str] = frozenset(SDLC_STAGES) | LATERAL_ENTRIES

# Greenfield needs a fresh workspace dir created for it; brownfield binds an
# existing directory the user picks with the filesystem navigator.
PROJECT_KINDS: frozenset[str] = frozenset({"greenfield", "brownfield"})
