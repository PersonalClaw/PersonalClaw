"""Generative-UI catalog — the SERVER-SIDE authoring authority (AMBIENT-SURFACES §5).

The FE `web/src/ui/genui/registry.ts` owns RENDERING (the typed registry + the
React components + drop-invalid validation). This module owns PROMPTING: the same
small component vocabulary as a machine-readable catalog, plus ``library_prompt()``
which derives the authoring section mechanically from it. It exists server-side
because the prompts that instruct a model to emit genui — the ``visualize`` primitive
and any workflow node — are built here, not in the browser.

Hand-maintained component docs are banned (they drift): the prompt is DERIVED from
``CORE_COMPONENTS`` below, and ``/api/genui/library`` serves the exact same derived
text to the FE / the visual-output skill so every consumer embeds the CURRENT set.
The FE registry mirrors this vocabulary for rendering; the core set is deliberately
small (every component costs prompt space) so the two stay legibly in step.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GenUiArg:
    """One declared arg of a component. ``key`` order is the positional contract."""

    key: str
    type: str
    required: bool = False
    note: str = ""


@dataclass(frozen=True)
class GenUiComponent:
    """A catalog entry: what a model may emit + how the prompt describes it. The
    RENDER lives in the FE registry under the same name."""

    name: str
    group: str
    description: str
    args: tuple[GenUiArg, ...]


#: The bundled core set — kept byte-for-byte in step with the FE registry's
#: `registerCoreGenUiComponents` (same names, groups, arg keys + required flags).
CORE_COMPONENTS: tuple[GenUiComponent, ...] = (
    GenUiComponent(
        "Stack",
        "Layout",
        "Vertical/horizontal stack of children",
        (
            GenUiArg("body", "refs", True, "child line ids, in order"),
            GenUiArg("gap", "string", note="s | m | l"),
            GenUiArg("direction", "string", note="column (default) | row"),
        ),
    ),
    GenUiComponent(
        "Card",
        "Layout",
        "Titled card wrapping children",
        (GenUiArg("body", "refs", True, "child line ids"), GenUiArg("title", "string")),
    ),
    GenUiComponent(
        "StatTile",
        "Data",
        "One metric with optional % delta",
        (
            GenUiArg("label", "string", True),
            GenUiArg("value", "string", True),
            GenUiArg("delta", "number", note="signed percent change"),
        ),
    ),
    GenUiComponent(
        "Table",
        "Data",
        "Header row + body rows",
        (
            GenUiArg("columns", "string[]", True),
            GenUiArg("rows", "rows", True, "array of row arrays"),
        ),
    ),
    GenUiComponent(
        "List",
        "Data",
        "Bulleted list of strings",
        (GenUiArg("items", "string[]", True),),
    ),
    GenUiComponent(
        "Bar",
        "Charts",
        "Bar chart of one numeric series",
        (
            GenUiArg("data", "number[]", True),
            GenUiArg("labels", "string[]", note="per-bar labels"),
        ),
    ),
    GenUiComponent(
        "Callout",
        "Feedback",
        "Tinted note band",
        (
            GenUiArg("text", "string", True),
            GenUiArg("tone", "string", note="info | ok | warn | danger | neutral"),
        ),
    ),
    GenUiComponent(
        "Badge",
        "Feedback",
        "Small status chip",
        (GenUiArg("text", "string", True), GenUiArg("tone", "string")),
    ),
    GenUiComponent(
        "ProgressBar",
        "Feedback",
        "Determinate 0..100 progress bar",
        (GenUiArg("value", "number", True), GenUiArg("label", "string")),
    ),
)

#: Group render order — matches the FE prompt so the two produce the same sections.
_GROUP_ORDER = ("Layout", "Data", "Charts", "Feedback")


def _signature(comp: GenUiComponent) -> str:
    args = ", ".join(f"{a.key}{'' if a.required else '?'}: {a.type}" for a in comp.args)
    desc = f" — {comp.description}" if comp.description else ""
    return f"  {comp.name}({args}){desc}"


def library_prompt() -> str:
    """The authoring section, derived mechanically from ``CORE_COMPONENTS`` — the
    single string an author/model reads to know what it may emit. Kept in step with
    the FE ``library.prompt()`` so the endpoint and the FE agree."""
    lines = [
        'Generative-UI components you may emit inside a <widget kind="genui"> block.',
        "DSL: one line per component — `id = Component(key: value, …)`. "
        "Forward references are legal.",
        "Compose children with a `refs`/`ref` arg holding other line ids (e.g. children: [a, b]).",
        "",
    ]
    for group in _GROUP_ORDER:
        defs = [c for c in CORE_COMPONENTS if c.group == group]
        if not defs:
            continue
        lines.append(f"{group}:")
        lines.extend(_signature(c) for c in defs)
        lines.append("")
    lines.extend(
        [
            "Example:",
            '  root = Stack(gap: "m", body: [stat, note])',
            '  stat = StatTile(label: "Revenue", value: "$1.2M", delta: 12)',
            '  note = Callout(tone: "info", text: "Up 12% vs last quarter.")',
        ]
    )
    return "\n".join(lines)


def library_manifest() -> dict:
    """The machine-readable catalog (for ``/api/genui/library`` + tests). Generated
    from ``CORE_COMPONENTS``, never hand-written."""
    return {
        "components": [
            {
                "name": c.name,
                "group": c.group,
                "description": c.description,
                "args": [
                    {"key": a.key, "type": a.type, "required": a.required, "note": a.note}
                    for a in c.args
                ],
            }
            for c in CORE_COMPONENTS
        ],
        "prompt": library_prompt(),
    }
