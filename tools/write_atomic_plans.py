#!/usr/bin/env python3
"""Turn the decomposition workflow's output into the atomic plan catalog + DAG.

Input:  a JSON file holding ``{"plans": [...], "dag": {...}}`` exactly as the
        atomic-plan-decomposition workflow returns it.
Output: ``docs/roadmap/atomic/``
          * ``dag.json``            — machine-readable catalog the dashboard renders
          * ``README.md``           — how to read the catalog, and the execution rule
          * ``<CODE>.md``           — one file per source plan, listing its atoms
        Existing plan files under ``docs/roadmap/plans/`` are NOT deleted here. Replacing
        them is a separate, reviewable step (``--retire`` writes the pointer stubs) so a
        bad decomposition can never destroy the source of truth in one command.

The atom contract (owner's rule, 2026-08-05): an atom is ONE coherent feature executable
start-to-finish in a single go. Once you start an atom you must never have to pause it and
go execute another plan — anything that would force that is a separate atom with an
explicit dependency edge.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
ATOMIC = CORE / "docs/roadmap/atomic"
PLANS = CORE / "docs/roadmap/plans"

STATUS_MARK = {"done": "✅", "in_progress": "🟡", "todo": "⬜"}


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "plans" not in data:
        raise SystemExit(f"{path}: no 'plans' key — is this the workflow's output?")
    return data


def write_dag(data: dict) -> int:
    ATOMIC.mkdir(parents=True, exist_ok=True)
    (ATOMIC / "dag.json").write_text(
        json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return sum(len(p.get("atoms") or []) for p in data["plans"])


def write_plan_files(data: dict) -> int:
    """One markdown file per source plan, listing its atoms with deps and done-when."""
    written = 0
    for plan in data["plans"]:
        atoms = plan.get("atoms") or []
        if not atoms:
            continue  # reference-only docs carry no executable work
        code = plan.get("code") or "UNKNOWN"
        lines = [
            f"# {plan.get('plan', code)} — atomic plans",
            "",
            f"**Source plan:** [`{plan.get('plan')}`](../plans/{plan.get('plan')}.md)  ",
            f"**Code:** `{code}`  ",
            f"**Source status:** {plan.get('status', '?')}",
            "",
            plan.get("summary", "").strip(),
            "",
            "Each atom below executes start-to-finish in one go. If an atom lists"
            " dependencies, they must be `done` before it starts — that is the whole point"
            " of the split: no atom should ever need pausing to go execute other work.",
            "",
            "| Atom | Status | Title | Depends on | Done when |",
            "|---|---|---|---|---|",
        ]
        for a in atoms:
            deps = ", ".join(f"`{d}`" for d in (a.get("deps") or [])) or "—"
            mark = STATUS_MARK.get(a.get("status", "todo"), "⬜")
            pr = f" (#{a['pr']})" if a.get("pr") else ""
            title = (a.get("title") or "").replace("|", "\\|")
            done = (a.get("done_when") or "").replace("|", "\\|")
            lines.append(f"| `{a.get('id')}` | {mark}{pr} | {title} | {deps} | {done} |")
        lines += ["", "## Atom scopes", ""]
        for a in atoms:
            lines += [
                f"### `{a.get('id')}` — {a.get('title')}",
                "",
                f"**Status:** {a.get('status')}" + (f" (PR #{a['pr']})" if a.get("pr") else ""),
                "",
                (a.get("scope") or "").strip(),
                "",
                f"**Done when:** {(a.get('done_when') or '').strip()}",
                "",
            ]
        (ATOMIC / f"{code}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        written += 1
    return written


def write_readme(data: dict) -> None:
    dag = data.get("dag") or {}
    plans = data["plans"]
    atoms = [a for p in plans for a in (p.get("atoms") or [])]
    done = sum(1 for a in atoms if a.get("status") == "done")
    ready = dag.get("ready_frontier") or []
    topo = dag.get("topo_order") or []

    lines = [
        "# Atomic plan catalog",
        "",
        "The roadmap's plans were too large and too interdependent: parts of a plan would"
        " finish, then the rest would block on *another* plan, so ten-plus plans sat in"
        " flight at once and no status read was accurate.",
        "",
        "This catalog is the fix. Every plan is decomposed into **atoms**: one coherent"
        " feature, executable start-to-finish in a single go. The cut line is exactly the"
        " dependency seam — anything that would force you to pause an atom and go execute"
        " other work is instead its own atom with an explicit dependency edge.",
        "",
        f"**{len(atoms)} atoms** across **{len(plans)} plans** — {done} done,"
        f" {len(atoms) - done} remaining. {dag.get('edge_count', 0)} dependency edges.",
        "",
        "## How to use it",
        "",
        "1. `dag.json` is the machine-readable source; the roadmap dashboard renders it"
        " (tiers, ready frontier, validation).",
        "2. **Start only from the ready frontier** — atoms whose dependencies are all"
        " `done`. Those need nothing else in flight.",
        "3. One atom per branch/PR. Mark it `done` in `dag.json` when its PR lands.",
        "4. `<CODE>.md` holds the human-readable atoms for one source plan.",
        "",
        "## Startable now",
        "",
    ]
    if ready:
        for r in ready[:20]:
            lines.append(f"- `{r.get('id')}` **{r.get('title')}** — {r.get('plan', '')}")
    else:
        lines.append("- (none — every remaining atom has an unmet dependency)")

    problems = []
    if dag.get("cycles"):
        problems.append(f"- **{len(dag['cycles'])} dependency cycle(s)** — must be broken")
    if dag.get("dangling"):
        problems.append(f"- {len(dag['dangling'])} dangling dependency edge(s)")
    if dag.get("unresolved"):
        problems.append(f"- {len(dag['unresolved'])} unresolved cross-plan reference(s)")
    if problems:
        lines += ["", "## Validation problems", ""] + problems

    if topo:
        lines += [
            "",
            "## Execution order (topological)",
            "",
            "Remaining atoms, dependency-respecting:",
            "",
            "```",
            " → ".join(topo[:60]) + (" → …" if len(topo) > 60 else ""),
            "```",
        ]
    (ATOMIC / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def retire_source_plans(data: dict) -> int:
    """Replace each decomposed source plan with a pointer stub.

    Deliberately opt-in (``--retire``). The full plan text is the accumulated design
    record — execution logs, measured findings, owner rulings — so it is preserved by git
    history, and the stub tells a reader where the executable work now lives.
    """
    n = 0
    for plan in data["plans"]:
        atoms = plan.get("atoms") or []
        name = plan.get("plan")
        if not atoms or not name:
            continue
        path = PLANS / f"{name}.md"
        if not path.exists():
            continue
        original = path.read_text(encoding="utf-8")
        code = plan.get("code")
        header = [
            f"# {name}",
            "",
            f"**Status:** DECOMPOSED — the executable work now lives in"
            f" [`../atomic/{code}.md`](../atomic/{code}.md) as"
            f" {len(atoms)} atomic plan(s).",
            "",
            "This plan was split because parts of it blocked on other plans, which forced"
            " it to sit half-done while other work ran. Each atom below its own file"
            " executes start-to-finish in one go; the dependency graph lives in"
            " [`../atomic/dag.json`](../atomic/dag.json).",
            "",
            "The original design record is kept below — execution logs, measured findings"
            " and owner rulings are the reason this document still matters.",
            "",
            "---",
            "",
        ]
        path.write_text("\n".join(header) + original, encoding="utf-8")
        n += 1
    return n


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        print("usage: write_atomic_plans.py <workflow-output.json> [--retire]")
        return 2
    data = load(Path(argv[1]))
    n_atoms = write_dag(data)
    n_files = write_plan_files(data)
    write_readme(data)
    print(f"wrote {ATOMIC}")
    print(f"  dag.json: {n_atoms} atoms across {len(data['plans'])} plans")
    print(f"  {n_files} per-plan atom files + README.md")
    if "--retire" in argv:
        n = retire_source_plans(data)
        print(f"  retired {n} source plans to pointer stubs (originals kept in git history)")
    else:
        print("  source plans left untouched (pass --retire to add pointer stubs)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
