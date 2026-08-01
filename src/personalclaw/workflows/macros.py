"""Template macros — one-liner orchestration patterns that expand into core nodes.

The engine deliberately has twelve node kinds and no more. Every pattern the ultracode
harness reaches for — a judge panel, adversarial verification, intent routing, a multi-modal
research sweep — is a COMPOSITION of those kinds, not a new kind. That is the whole bet of
§"Agent Orchestration as Compositions": adding `judge_panel` as a thirteenth node kind would
mean a scheduler case, a dispatcher, a resume path, a rewind story and a widget row for it.
Expanding it at definition time means none of that exists.

**Expansion is at definition time, not run time.** A macro is sugar over the spec, so by the
time a run starts there are no macros left — only `parallel`, `infer`, `transform`, `branch`,
`foreach`. Three consequences that are the reason for the choice:

* the journal, the resume cache and the rewind cascade see ordinary nodes, so none of them
  needs to know macros exist;
* a user can EXPAND a macro and then hand-edit the result, which is how they graduate from
  the pattern to their own variant — a run-time macro would regenerate over their edit;
* the validator checks the expansion, so a malformed macro fails at save with a real node
  path rather than at run with a synthetic one.

**Every macro's scoring leg is `infer`, never `stage`** (WF2-R16). A judge that only has to
read text and return a score does not need a session, tools, or a lane slot — using `stage`
for it costs a subagent launch per judge and turns a five-judge panel into five concurrent
sessions. `infer` is one bounded model call.
"""

from __future__ import annotations

import copy
from typing import Any

#: The `macro` key a node uses to invoke one. Chosen over overloading `kind` so a spec that
#: reaches the engine with a macro still in it fails as an unknown KIND (loud) rather than
#: being silently treated as an unexpanded container (quiet).
MACRO_KEY = "macro"


class MacroError(ValueError):
    """A macro invocation the expander cannot turn into nodes.

    A ValueError subclass so `save_def`'s existing "unusable spec" path reports it without a
    new error channel — the author needs the message, not a taxonomy.
    """


def expand_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``spec`` with every macro expanded into core nodes.

    Deep-copied, never mutated in place: the caller may be holding the author's original (a
    save path re-reads it for the response), and expanding under them would make the stored
    spec and the returned one silently differ.

    Recursive, and expansion output is re-walked — a macro may legitimately expand into a
    container holding another macro (a `research_sweep` whose verify leg is a `verify_panel`).
    Bounded by :data:`MAX_DEPTH` so a macro that expanded into itself fails loudly instead of
    recursing until the stack dies.
    """
    out = copy.deepcopy(spec)
    root = out.get("root")
    if isinstance(root, dict):
        out["root"] = _expand_node(root, depth=0)
    return out


#: Expansion-recursion ceiling. A macro expanding into itself is an authoring bug; hitting a
#: RecursionError instead would report it as a crash with no spec path in it.
MAX_DEPTH = 12


def _expand_node(node: dict[str, Any], *, depth: int) -> dict[str, Any]:
    if depth > MAX_DEPTH:
        raise MacroError(
            f"macro expansion exceeded {MAX_DEPTH} levels — a macro probably expands into itself"
        )
    if not isinstance(node, dict):
        raise MacroError(f"a node must be an object, got {type(node).__name__}")

    name = node.get(MACRO_KEY)
    if isinstance(name, str) and name:
        fn = _MACROS.get(name)
        if fn is None:
            raise MacroError(f"unknown macro {name!r} — available: {', '.join(sorted(_MACROS))}")
        # The expansion is re-walked, so a macro may produce another macro.
        return _expand_node(fn(node), depth=depth + 1)

    # An ordinary node: expand its children in place.
    for key in ("children",):
        kids = node.get(key)
        if isinstance(kids, list):
            node[key] = [_expand_node(k, depth=depth + 1) for k in kids]
    body = node.get("body")
    if isinstance(body, dict):
        node["body"] = _expand_node(body, depth=depth + 1)
    cases = node.get("cases")
    if isinstance(cases, dict):
        node["cases"] = {k: _expand_node(v, depth=depth + 1) for k, v in cases.items()}
    default = node.get("default")
    if isinstance(default, dict):
        node["default"] = _expand_node(default, depth=depth + 1)
    return node


def macro_names() -> list[str]:
    """The available macro names — for the manifest and an author's error message."""
    return sorted(_MACROS)


def has_macros(spec: dict[str, Any]) -> bool:
    """True when ``spec`` still contains an unexpanded macro.

    Used by the save path to record whether a stored spec was authored with macros, and by a
    test asserting that what reaches the engine never does.
    """
    found = False

    def walk(node: Any) -> None:
        nonlocal found
        if found or not isinstance(node, dict):
            return
        if isinstance(node.get(MACRO_KEY), str) and node[MACRO_KEY]:
            found = True
            return
        for k in node.get("children") or []:
            walk(k)
        walk(node.get("body"))
        for v in (node.get("cases") or {}).values():
            walk(v)
        walk(node.get("default"))

    walk(spec.get("root") if "root" in spec else spec)
    return found


# ── helpers ─────────────────────────────────────────────────────────────────


def _need(node: dict[str, Any], key: str, macro: str) -> Any:
    cfg = node.get("config") or {}
    value = cfg.get(key)
    if value in (None, "", [], {}):
        raise MacroError(f"macro {macro!r} needs `config.{key}`")
    return value


def _id_of(node: dict[str, Any], fallback: str) -> str:
    raw = node.get("id")
    return str(raw) if isinstance(raw, str) and raw else fallback


def _tier(node: dict[str, Any], default: str) -> str:
    cfg = node.get("config") or {}
    tier = cfg.get("model_tier")
    return str(tier) if tier in ("reasoning", "standard", "fast") else default


# ── judge_panel ─────────────────────────────────────────────────────────────


def _judge_panel(node: dict[str, Any]) -> dict[str, Any]:
    """N independent judges scoring one subject, then a ranked synthesis.

    `parallel[infer × N]` → `transform`. The judges are INDEPENDENT on purpose: a panel whose
    members can see each other's scores converges on the first one, which is the failure mode
    the panel exists to avoid.

    Each judge gets its own LENS rather than the same prompt N times. Redundancy catches a
    flaky answer; diversity catches a whole failure mode the others are blind to — and the
    latter is what makes a panel worth 3× the tokens.
    """
    mid = _id_of(node, "judge_panel")
    subject = _need(node, "subject", "judge_panel")
    lenses = _need(node, "lenses", "judge_panel")
    if not isinstance(lenses, list):
        raise MacroError("macro 'judge_panel' needs `config.lenses` as a list")
    criteria = (node.get("config") or {}).get("criteria") or ""
    tier = _tier(node, "standard")

    judges: list[dict[str, Any]] = []
    for lens in lenses:
        label = lens if isinstance(lens, str) else str((lens or {}).get("name", ""))
        guidance = "" if isinstance(lens, str) else str((lens or {}).get("prompt", ""))
        if not label:
            raise MacroError("macro 'judge_panel': every lens needs a name")
        judges.append(
            {
                "kind": "infer",
                "id": f"{mid}_{_slug(label)}",
                "config": {
                    "model_tier": tier,
                    "prompt": (
                        f"Judge the following through the {label} lens"
                        + (f". {guidance}" if guidance else ".")
                        + (f"\n\nCriteria: {criteria}" if criteria else "")
                        + '\n\nReturn JSON: {"score": 0-10, "lens": "'
                        + label
                        + '", '
                        '"findings": [Finding], "summary": "one line"}.\n\n'
                        # The shared block, not a fourth hand-written copy of the Finding record.
                        # Resolved right after expansion, so the macro emits the REFERENCE and one
                        # definition governs every review stage in the library.
                        "{{block:finding-record}}\n\nSubject:\n" + str(subject)
                    ),
                    "schema": {
                        "score": "number",
                        "lens": "string",
                        "findings": "array",
                        "summary": "string",
                    },
                },
            }
        )

    return {
        "kind": "sequence",
        "id": mid,
        "children": [
            {"kind": "parallel", "id": f"{mid}_judges", "children": judges},
            {
                "kind": "transform",
                "id": f"{mid}_synthesis",
                "config": {
                    # Zero-token: ranking N scores is arithmetic, and spending a model call on
                    # it would make the panel's cheapest step its most expensive.
                    "expr": {
                        "scores": [f"{{{{nodes.{j['id']}.output.score}}}}" for j in judges],
                        "by_lens": {
                            str(j["id"]): f"{{{{nodes.{j['id']}.output}}}}" for j in judges
                        },
                        "findings": [f"{{{{nodes.{j['id']}.output.findings}}}}" for j in judges],
                    }
                },
            },
        ],
    }


# ── verify_panel ────────────────────────────────────────────────────────────


def _verify_panel(node: dict[str, Any]) -> dict[str, Any]:
    """Adversarial verification of a finding list: refute each, keep what survives.

    `foreach(pipeline)` of `infer(refute)` → `transform(filter)`. The prompt asks the model to
    REFUTE, and defaults to refuted when uncertain — a verifier asked "is this real?" agrees
    with the finding, because agreeing is the locally plausible answer. Asking it to attack
    the finding is what makes the survival signal mean anything.

    `pipeline: true` so item 3 verifies while item 1 is still going: the items are independent
    and a barrier here would waste the difference between the fastest and slowest.
    """
    mid = _id_of(node, "verify_panel")
    findings = _need(node, "findings", "verify_panel")
    tier = _tier(node, "standard")
    extra = str((node.get("config") or {}).get("guidance", "") or "")

    return {
        "kind": "sequence",
        "id": mid,
        "children": [
            {
                "kind": "foreach",
                "id": f"{mid}_verify",
                "config": {
                    "items": findings,
                    "pipeline": True,
                    # A verifier that errors is not evidence the finding is fake, so the item
                    # is skipped rather than sinking the whole sweep.
                    "on_item_error": "skip",
                },
                "body": {
                    "kind": "infer",
                    "id": f"{mid}_refute",
                    "config": {
                        "model_tier": tier,
                        "prompt": (
                            "Try to REFUTE this finding. Look for reasons it is wrong, already "
                            "handled, or not reachable in practice. Default to refuted=true "
                            "when you are uncertain — a finding that survives a genuine attempt "
                            "to disprove it is worth acting on; one that merely sounds "
                            "plausible is not."
                            + (f"\n\n{extra}" if extra else "")
                            + '\n\nReturn JSON: {"refuted": bool, "reason": "why", '
                            '"severity": "Critical|Major|Minor|Nit"}.'
                            "\n\nFinding:\n{{item}}"
                        ),
                        "schema": {
                            "refuted": "boolean",
                            "reason": "string",
                            "severity": "string",
                        },
                    },
                },
            },
            {
                "kind": "transform",
                "id": f"{mid}_confirmed",
                "config": {
                    "expr": {
                        "verdicts": f"{{{{nodes.{mid}_refute.output}}}}",
                        "source_findings": findings,
                    }
                },
            },
        ],
    }


# ── route ───────────────────────────────────────────────────────────────────


def _route(node: dict[str, Any]) -> dict[str, Any]:
    """Classify, then dispatch — the routing pattern as a one-liner (WF2-R17).

    `infer(classify into enum)` → `branch(on: that output)`. The classifier is `infer` at the
    FAST tier by default: deciding which of three paths to take is a cheap judgment, and
    paying reasoning-tier prices to route into a reasoning-tier branch doubles the cost of the
    decision for no gain.

    The enum is passed to the branch so the validator's coverage check fires at SAVE time — a
    route with an uncovered case would otherwise raise a binding error mid-run, after the
    classifier already spent its tokens.
    """
    mid = _id_of(node, "route")
    subject = _need(node, "subject", "route")
    cases = node.get("cases")
    if not isinstance(cases, dict) or not cases:
        raise MacroError("macro 'route' needs `cases` mapping each category to a node")
    labels = list(cases)
    criteria = str((node.get("config") or {}).get("criteria", "") or "")
    tier = _tier(node, "fast")

    out: dict[str, Any] = {
        "kind": "sequence",
        "id": mid,
        "children": [
            {
                "kind": "infer",
                "id": f"{mid}_classify",
                "config": {
                    "model_tier": tier,
                    "prompt": (
                        "Classify the following into EXACTLY one category.\n"
                        f"Categories: {', '.join(labels)}\n"
                        + (f"How to decide: {criteria}\n" if criteria else "")
                        + '\nReturn JSON: {"category": "<one of the above>", '
                        '"why": "one line"}.\n\nSubject:\n' + str(subject)
                    ),
                    "schema": {"category": "string", "why": "string"},
                },
            },
            {
                "kind": "branch",
                "id": f"{mid}_dispatch",
                "config": {
                    "on": f"{{{{nodes.{mid}_classify.output.category}}}}",
                    # Declared so validation catches an uncovered category at save time.
                    "enum": labels,
                },
                "cases": cases,
            },
        ],
    }
    if isinstance(node.get("default"), dict):
        out["children"][1]["default"] = node["default"]
    return out


# ── research_sweep ──────────────────────────────────────────────────────────


def _research_sweep(node: dict[str, Any]) -> dict[str, Any]:
    """A multi-modal search sweep: N angles in parallel, deduped, then read per source.

    `parallel[stage × modes]` → `transform(dedup)` → `foreach(pipeline)[read, extract]`.

    The modes are the point. One search angle finds what that angle can see; a sweep that runs
    by-content, by-entity and by-time in parallel finds things each single angle is blind to,
    which is why this is a macro rather than "just call search".

    The search legs are `stage` (they need tools); the extraction leg is `infer` (it only
    reads text). Making extraction a `stage` would spend a subagent session per source.
    """
    mid = _id_of(node, "research_sweep")
    question = _need(node, "question", "research_sweep")
    modes = _need(node, "modes", "research_sweep")
    if not isinstance(modes, list):
        raise MacroError("macro 'research_sweep' needs `config.modes` as a list")

    searches = []
    for mode in modes:
        label = str(mode)
        searches.append(
            {
                "kind": "stage",
                "id": f"{mid}_{_slug(label)}",
                "config": {
                    "prompt": (
                        f"Search for material answering the question below, using the {label} "
                        "angle specifically. Return the sources you found with a one-line note "
                        "on what each contains.\n\nQuestion:\n" + str(question)
                    ),
                },
            }
        )

    return {
        "kind": "sequence",
        "id": mid,
        "children": [
            {"kind": "parallel", "id": f"{mid}_sweep", "children": searches},
            {
                "kind": "transform",
                "id": f"{mid}_sources",
                "config": {
                    # Dedup needs ALL angles' results together, which is why this is the one
                    # barrier in the macro.
                    "expr": {
                        "by_mode": {
                            str(s["id"]): f"{{{{nodes.{s['id']}.output}}}}" for s in searches
                        },
                    }
                },
            },
            {
                "kind": "foreach",
                "id": f"{mid}_read",
                "config": {
                    "items": f"{{{{nodes.{mid}_sources.output.by_mode}}}}",
                    "pipeline": True,
                    "on_item_error": "skip",
                },
                "body": {
                    "kind": "infer",
                    "id": f"{mid}_extract",
                    "config": {
                        "model_tier": "standard",
                        "prompt": (
                            "Extract what this source actually says about the question. Quote "
                            "the load-bearing lines rather than paraphrasing, and say plainly "
                            "when it does NOT address the question — a source that turned out "
                            "to be irrelevant is a useful result, not a failure.\n\n"
                            'Return JSON: {"relevant": bool, "claims": ["…"], '
                            '"quotes": ["…"], "gaps": ["…"]}.\n\n'
                            f"Question: {question}\n\nSource:\n{{{{item}}}}"
                        ),
                        "schema": {
                            "relevant": "boolean",
                            "claims": "array",
                            "quotes": "array",
                            "gaps": "array",
                        },
                    },
                },
            },
        ],
    }


def _slug(text: str) -> str:
    """A node-id-safe fragment of a lens or mode name.

    Node ids address the journal and the resume cache, so they must be stable and simple —
    a lens called "UX / accessibility" becomes `ux_accessibility`, not a path-breaking id.
    """
    out = "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")
    while "__" in out:
        out = out.replace("__", "_")
    return out or "lens"


_MACROS: dict[str, Any] = {
    "judge_panel": _judge_panel,
    "verify_panel": _verify_panel,
    "route": _route,
    "research_sweep": _research_sweep,
}
