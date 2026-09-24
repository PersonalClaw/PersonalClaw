#!/usr/bin/env python3
"""Render the ACP not-gateable residual registry INTO the parity doc (AAP-5 §2.2).

``ACP-AGENT-PARITY.md`` §2.2 requires that the residual not-gateable set be
enumerated in ONE place — :data:`personalclaw.acp.permission_authority.NOT_GATEABLE`
— and that §2.7's parity doc (``docs/agents/acp-parity.md``) **render** that
registry rather than re-derive it in prose. A hand-written table beside the
registry is exactly the drift the requirement exists to prevent, and it had
already happened: the doc's third column carried sweep prose
("44 audited tool events…") that appears nowhere in the registry, and the
per-entry ``observation`` — the field whose whole job is to name the measurement
that PROVED the residue — was not in the doc at all.

Mechanism (the house generator idiom — ``scripts/generate_*_baseline.py`` plus a
companion pytest ratchet): this script owns one marker-delimited block inside the
doc. Everything between :data:`MARKER_BEGIN` and :data:`MARKER_END` is generated;
everything outside it is hand-written prose that states MECHANISM, never the
enumeration.

    python scripts/render_acp_parity_residual.py            # write the block
    python scripts/render_acp_parity_residual.py --check     # fail on drift

``tests/test_acp_parity_residual_render.py`` is the always-on rail: it fails when
the doc and the registry disagree, and it carries a vacuity floor so a rail that
matches NOTHING cannot read as clean.

Deliberately shape-LOOSE. The registry is co-owned and still moving (a provider,
a field, or a third residual state may land at any time), so a renderer that
enumerates field names by hand would just relocate the drift. Instead every
*scalar* dataclass field is rendered under its own name and every
*collection-shaped* field is treated as matching machinery and skipped — so a new
``str`` field, or a new ``Enum``-valued state, appears in the doc on the next run
with no edit here.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

from personalclaw.acp import permission_authority

MARKER_BEGIN = (
    "<!-- BEGIN GENERATED: not-gateable-registry (scripts/render_acp_parity_residual.py) -->"
)
MARKER_END = "<!-- END GENERATED: not-gateable-registry -->"

#: ``ProviderCoverage.provider`` restates the registry key, which is already the
#: bullet's label — rendering it twice is noise, not drift protection.
_REDUNDANT_FIELDS = frozenset({"provider"})


def doc_path() -> Path:
    """Repo-root location of the §2.7 parity doc."""
    return Path(__file__).resolve().parents[1] / "docs" / "agents" / "acp-parity.md"


def _flatten(value: object) -> str:
    """One-line text for a scalar registry value (``''`` when there is none).

    Registry prose is written as implicitly-concatenated multi-line literals, so
    the newlines are an authoring artifact and are collapsed. ``Enum`` members
    render as their value, which keeps a NEW residual state readable instead of
    printing ``<ResidualState.FOO: 'foo'>``.
    """
    if value is None:
        return ""
    unwrapped = getattr(value, "value", value)
    return " ".join(str(unwrapped).split())


def _scalar_fields(obj: object, *, skip: frozenset[str] = frozenset()) -> list[tuple[str, str]]:
    """``(label, text)`` for every non-empty scalar dataclass field of ``obj``.

    Collection-shaped fields (``entries``, ``title_patterns``) are structure or
    matching machinery, not prose, and are skipped by SHAPE rather than by name —
    that is what lets a sibling add a field without touching this renderer.
    """
    if not dataclasses.is_dataclass(obj) or isinstance(obj, type):
        raise TypeError(
            f"{type(obj).__name__} is not a dataclass instance; the NOT_GATEABLE "
            "registry changed shape and this renderer must be revisited"
        )
    out: list[tuple[str, str]] = []
    for f in dataclasses.fields(obj):
        if f.name in _REDUNDANT_FIELDS or f.name in skip:
            continue
        value = getattr(obj, f.name, None)
        if isinstance(value, (list, tuple, set, frozenset, dict)):
            continue
        text = _flatten(value)
        if not text:
            continue
        out.append((f.name.replace("_", " ").capitalize(), text))
    return out


def _entry_label(entry: object) -> tuple[str, frozenset[str]]:
    """``(label, fields consumed by the label)`` for one residual entry.

    The consumed set is returned rather than hard-coded so the label's own field
    is not ALSO rendered as a bullet under it — and so an entry that stops
    carrying a ``tool`` still gets a label from whatever field it does carry.
    """
    tool = _flatten(getattr(entry, "tool", None))
    if tool:
        return f"`{tool}`", frozenset({"tool"})
    for f in dataclasses.fields(entry) if dataclasses.is_dataclass(entry) else ():
        text = _flatten(getattr(entry, f.name, None))
        if text and not isinstance(getattr(entry, f.name), (list, tuple, set, frozenset, dict)):
            return text, frozenset({f.name})
    return "(unlabelled entry)", frozenset()


def render_block() -> str:
    """The generated markdown between the markers (markers NOT included)."""
    registry = permission_authority.NOT_GATEABLE
    lines: list[str] = [
        "<!-- Regenerate with: python scripts/render_acp_parity_residual.py -->",
        "",
    ]
    if not registry:
        lines.append(
            "**The registry is EMPTY.** No provider has been measured, so this "
            "document makes no residual claim about any of them."
        )
        return "\n".join(lines)

    for key in sorted(registry):
        coverage = registry[key]
        entries = tuple(getattr(coverage, "entries", ()) or ())
        count = len(entries)
        noun = "entry" if count == 1 else "entries"
        lines.append(f"- **`{key}`** — {count} declared residual {noun}.")
        for label, text in _scalar_fields(coverage):
            lines.append(f"  - {label}: {text}")
        for entry in entries:
            entry_label, consumed = _entry_label(entry)
            lines.append(f"  - {entry_label}")
            for label, text in _scalar_fields(entry, skip=consumed):
                lines.append(f"    - {label}: {text}")
    return "\n".join(lines)


def render_document(text: str) -> str:
    """``text`` with the marker-delimited block replaced by :func:`render_block`.

    Raises when the markers are missing or malformed — a splice that silently
    no-ops would make the whole rail vacuous.
    """
    begin = text.find(MARKER_BEGIN)
    end = text.find(MARKER_END)
    if begin < 0 or end < 0:
        raise ValueError(
            f"{doc_path().name} is missing the generated-block markers "
            f"({MARKER_BEGIN!r} / {MARKER_END!r}); the §2.2 render contract cannot hold"
        )
    if end < begin:
        raise ValueError(f"{doc_path().name} has the generated-block markers out of order")
    if text.count(MARKER_BEGIN) != 1 or text.count(MARKER_END) != 1:
        raise ValueError(
            f"{doc_path().name} has duplicate generated-block markers; exactly one "
            "block may be generated"
        )
    head = text[: begin + len(MARKER_BEGIN)]
    tail = text[end:]
    return f"{head}\n{render_block()}\n{tail}"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    check_only = "--check" in args
    path = doc_path()
    current = path.read_text(encoding="utf-8")
    rendered = render_document(current)
    if current == rendered:
        print(f"acp-parity residual block is in sync with NOT_GATEABLE ({path})")
        return 0
    if check_only:
        print(
            f"DRIFT: {path} does not match acp/permission_authority.NOT_GATEABLE.\n"
            "Run: python scripts/render_acp_parity_residual.py",
            file=sys.stderr,
        )
        return 1
    path.write_text(rendered, encoding="utf-8")
    print(f"rewrote the residual block in {path} from NOT_GATEABLE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
