#!/usr/bin/env python3
"""Generate roadmap-dashboard.html — one self-contained page for owner visibility.

Design goal (owner ask, 2026-08-05, refined 2026-08-06): LOW COGNITIVE LOAD. The default
view is ~one screen and answers three questions with no scrolling:

  1. How much is done?    — a hero band: plans-done % AND atoms-done %, each a 4-color
                            stacked status bar with counts.
  2. What's happening now? — the live execution stack + next-up queue (from
                            `.roadmap-exec-state.json`), with branch + PR links.
  3. Where does the work live? — a compact pillar grid, one tile per plan, click to drill in.

Everything heavier — per-plan atom lists, dependency tiers, the full execution-order prose,
the engine session queue — lives inside `<details>` elements that are CLOSED by default.
No JS framework: native `<details>/<summary>` plus a tiny expand/collapse-all helper.

Derives ENTIRELY from files already maintained as the source of truth, so it never drifts:
  * docs/roadmap/atomic/dag.json  — 602 atoms + the authoritative DAG (ready_frontier,
                                    cycles, dangling, unresolved). The backbone: every
                                    count and color comes from here.
  * docs/roadmap/roadmap.md       — the Plans-by-Pillar tables (pillar grouping, names, waves)
  * docs/roadmap/plans/*.md       — each plan's **Status:** line (shown inside a drilled tile)
  * workspace ROADMAP.md §5       — the full execution-order prose (collapsed)
  * workspace .roadmap-exec-state.json — the live "working now" stack + next_up
  * docs/roadmap/WF2-SESSION-QUEUE.md  — engine session tallies (collapsed)
  * git + gh                      — open-PR lookup for working-now branch links (best-effort)

Run from anywhere: `python3 PersonalClaw/tools/gen_roadmap_dashboard.py`.
Writes `<workspace>/roadmap-dashboard.html`. No third-party deps.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ── locate the repos relative to this file ──
CORE = Path(__file__).resolve().parents[1]  # …/PersonalClaw/PersonalClaw
WORKSPACE = CORE.parent  # …/PersonalClaw
ROADMAP_MD = CORE / "docs/roadmap/roadmap.md"
WORKSPACE_ROADMAP = WORKSPACE / "ROADMAP.md"
PLANS_DIR = CORE / "docs/roadmap/plans"
WF2_QUEUE = CORE / "docs/roadmap/WF2-SESSION-QUEUE.md"
DAG_JSON = CORE / "docs/roadmap/atomic/dag.json"
EXEC_STATE = WORKSPACE / ".roadmap-exec-state.json"
OUT = WORKSPACE / "roadmap-dashboard.html"
REPO_URL = "https://github.com/PersonalClaw/PersonalClaw"


def sh(cmd: str, cwd: Path = CORE, timeout: int = 20) -> str:
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip()
    except Exception:
        return ""


def logger_warn(msg: str) -> None:
    print(f"  warning: {msg}", file=sys.stderr)


def esc(s: str) -> str:
    return html.escape(s or "")


def _tip(s: str) -> str:
    """Collapse whitespace so a title="" attribute stays on one line (keeps the file small)."""
    return esc(" ".join((s or "").split()))[:300]


# ── data model ──


@dataclass
class Plan:
    number: str
    name: str
    slug: str  # plan filename stem == dag.json plan name (join key), "" if unresolved
    sessions: str
    wave: str
    pillar: str
    status_kind: str = "unknown"  # done | in_progress | proposed | deferred | unknown
    status_line: str = ""

    @property
    def path(self) -> Path | None:
        p = PLANS_DIR / f"{self.slug}.md"
        return p if self.slug and p.exists() else None


PILLAR_RE = re.compile(r"^### (Pillar [A-Z][^\n]*)", re.M)
ROW_RE = re.compile(
    r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$", re.M
)
LINK_RE = re.compile(r"\[[^\]]+\]\(plans/([A-Za-z0-9._-]+)\.md\)")


def parse_pillars() -> list[Plan]:
    text = ROADMAP_MD.read_text(encoding="utf-8")
    plans: list[Plan] = []
    parts = PILLAR_RE.split(text)  # [pre, pillar1, body1, pillar2, body2, …]
    for i in range(1, len(parts), 2):
        pillar = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        for m in ROW_RE.finditer(body):
            num, name, link, sessions, wave = m.groups()
            if not num.isdigit():
                continue
            slug_m = LINK_RE.search(link)
            plans.append(
                Plan(
                    number=num,
                    name=re.sub(r"\*\*|`", "", name).strip(),
                    slug=slug_m.group(1) if slug_m else "",
                    sessions=sessions.strip(),
                    wave=wave.strip(),
                    pillar=pillar,
                )
            )
    return plans


STATUS_MAP = [
    ("done", re.compile(r"\bDONE\b", re.I)),
    ("in_progress", re.compile(r"\bIN PROGRESS\b|\bPARTIAL\b|\bUNDERWAY\b", re.I)),
    ("proposed", re.compile(r"\bPROPOSED\b|\bDRAFT\b|\bNOT STARTED\b|\bTODO\b", re.I)),
    ("deferred", re.compile(r"\bDEFERRED\b|\bOWNER-GATED\b|\bBLOCKED\b|\bVETO", re.I)),
]


def classify_status(line: str) -> str:
    for kind, rx in STATUS_MAP:
        if rx.search(line):
            return kind
    return "unknown"


def enrich_plan(plan: Plan) -> None:
    path = plan.path
    if not path:
        plan.status_kind = "missing"
        return
    text = path.read_text(encoding="utf-8")
    sm = re.search(r"^\*\*Status:\*\*\s*(.+?)(?:\n\n|\n##|\n\*\*)", text, re.S | re.M)
    if sm:
        line = " ".join(sm.group(1).split())
        plan.status_kind = classify_status(line)
        # de-markdown for display: [text](url) → text, drop ** and backticks
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"\*\*|`", "", line)
        plan.status_line = line[:320]


# ── WF2 session queue (surfaced only in a collapsed section) ──


@dataclass
class QueueStats:
    total: int = 0
    done: int = 0
    pending_pr: int = 0
    todo: int = 0
    blocked: int = 0


def parse_queue() -> QueueStats:
    if not WF2_QUEUE.exists():
        return QueueStats()
    text = WF2_QUEUE.read_text(encoding="utf-8")
    q = QueueStats()
    rows = re.findall(
        r"^\|\s*(S?\d+)\s*\|(.+?)\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$", text, re.M
    )
    for _sid, _subj, _group, status in rows:
        s = status.upper()
        q.total += 1
        if "PENDING_PR" in s:
            q.pending_pr += 1
            q.done += 1
        elif "DONE" in s or "✅" in status:
            q.done += 1
        elif "BLOCKED" in s:
            q.blocked += 1
        elif "TODO" in s:
            q.todo += 1
    return q


# ── git / PR state (best-effort; drives working-now branch links) ──


def git_state() -> dict:
    ahead = sh("git log --oneline origin/main..HEAD 2>/dev/null")
    n_ahead = len([x for x in ahead.splitlines() if x.strip()])
    branch = sh("git branch --show-current")
    prs_raw = sh(
        "gh pr list --state open --limit 80 --json number,headRefName 2>/dev/null",
        timeout=25,
    )
    pr_by_branch: dict[str, int] = {}
    try:
        for p in json.loads(prs_raw or "[]"):
            pr_by_branch[p["headRefName"]] = p["number"]
    except Exception:
        pass
    return {"branch": branch, "ahead": n_ahead, "pr_by_branch": pr_by_branch}


# ── live execution state (the owner's "what am I doing now") ──


def parse_exec_state() -> dict:
    if not EXEC_STATE.exists():
        return {}
    try:
        return json.loads(EXEC_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger_warn(f"could not read {EXEC_STATE}")
        return {}


# ── full execution order (from workspace ROADMAP §5; collapsed) ──


def parse_next() -> list[dict]:
    if not WORKSPACE_ROADMAP.exists():
        return []
    text = WORKSPACE_ROADMAP.read_text(encoding="utf-8")
    m = re.search(r"^## 5\. Recommended execution order.*?(?=^## 6\.)", text, re.S | re.M)
    if not m:
        return []
    body = m.group(0)
    sections = []
    for sm in re.finditer(r"^### (.+?)\n(.*?)(?=^### |\Z)", body, re.S | re.M):
        title = re.sub(r"\*\*|`", "", sm.group(1)).strip()
        items = []
        for bm in re.finditer(r"^[-*]\s+(.+?)(?=\n[-*]\s|\n\n|\Z)", sm.group(2), re.S | re.M):
            item = " ".join(re.sub(r"\*\*|`|🔴|🟢|🟡|⚠️|✅", "", bm.group(1)).split())
            if item:
                items.append(item[:240])
        if items:
            sections.append({"title": title, "items": items[:8]})
    return sections[:8]


# ── atomic-plan catalog + DAG ──


def parse_atoms() -> dict:
    """The atomic-plan catalog + DAG, when it exists.

    Returns ``{}`` when the decomposition has not landed yet, so the dashboard still renders
    (degraded to plan-level status) rather than failing.
    """
    if not DAG_JSON.exists():
        return {}
    try:
        data = json.loads(DAG_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger_warn(f"could not read {DAG_JSON}")
        return {}
    atoms: dict[str, dict] = {}
    for plan in data.get("plans", []):
        for atom in plan.get("atoms", []):
            atom = dict(atom)
            atom["plan"] = plan.get("plan", "")  # long name == roadmap slug (join key)
            atom["plan_code"] = plan.get("code", "")
            atoms[atom.get("id", "")] = atom
    atoms.pop("", None)
    # The workflow returns {plans, dag:{...}}; read the DAG fields from the nested `dag`
    # object, falling back to the top level so a hand-flattened dag.json also works.
    d = data.get("dag") if isinstance(data.get("dag"), dict) else data
    return {
        "atoms": atoms,
        "ready": d.get("ready_frontier", []),
        "topo": d.get("topo_order", []),
        "cycles": d.get("cycles", []),
        "dangling": d.get("dangling", []),
        "unresolved": d.get("unresolved", []),
        "edges": d.get("edge_count", 0),
    }


def dag_layers(atoms: dict[str, dict]) -> list[list[dict]]:
    """Group atoms into dependency tiers (longest-path layering).

    Tier 0 has no unsatisfied deps; tier N depends only on tiers < N. Unknown dep ids are
    ignored rather than pushing an atom to infinity — a dangling edge is a data problem the
    validation panel reports, and it must not make the whole graph unrenderable.
    """
    depth: dict[str, int] = {}

    def resolve(aid: str, seen: frozenset[str]) -> int:
        if aid in depth:
            return depth[aid]
        if aid in seen or aid not in atoms:
            return 0  # cycle or dangling → floor it; the panels report the real problem
        deps = [d for d in (atoms[aid].get("deps") or []) if d in atoms]
        d = 0 if not deps else 1 + max(resolve(x, seen | {aid}) for x in deps)
        depth[aid] = d
        return d

    for aid in atoms:
        resolve(aid, frozenset())
    if not depth:
        return []
    layers: list[list[dict]] = [[] for _ in range(max(depth.values()) + 1)]
    for aid, d in sorted(depth.items()):
        layers[d].append(atoms[aid])
    return layers


# DAG status vocabulary — ONE source of truth shared by every bar and chip.
# done / in_progress come straight from the atom; a `todo` atom is `ready` when it is in the
# authoritative ready-frontier, else `blocked`. Labels, CSS classes, and colors travel together.
DAG_STATES = (
    ("done", "st-done", "#3fb950", "done"),
    ("in_progress", "st-inprog", "#d29922", "in progress"),
    ("ready", "st-ready", "#58a6ff", "startable"),
    ("blocked", "st-blocked", "#6e7681", "blocked"),
)
_DAG_CLASS = {k: c for k, c, _, _ in DAG_STATES}
_DAG_COLOR = {k: col for k, _, col, _ in DAG_STATES}
_DAG_LABEL = {k: lbl for k, _, _, lbl in DAG_STATES}

# When the DAG is absent, fall back to plan-level status → one of the four states.
_STATUS_TO_STATE = {
    "done": "done",
    "in_progress": "in_progress",
    "proposed": "ready",
    "deferred": "blocked",
    "unknown": "blocked",
    "missing": "blocked",
}


def classify_atom(atom: dict, ready_ids: set[str]) -> str:
    """One of done | in_progress | ready | blocked.

    done/in_progress come from the atom; a `todo` atom is `ready` iff it is in the DAG's
    authoritative ready-frontier (which resolves cross-plan EXT edges to concrete atoms),
    else `blocked`. Using the frontier keeps every blue count identical to the "Startable
    now" frontier length.
    """
    st = atom.get("status", "todo")
    if st in ("done", "in_progress"):
        return st
    return "ready" if atom.get("id") in ready_ids else "blocked"


def _natkey(aid: str):
    m = re.match(r"([A-Za-z]+)-?(\d+)", aid or "")
    return (m.group(1), int(m.group(2))) if m else (aid or "", 0)


# ── bar / caption helpers ──


def _bar(counts: dict[str, int], total: int, height: int, labels: bool = False) -> str:
    """A single stacked horizontal bar over the four states (widths = share of total)."""
    if not total:
        return f'<div class="statbar" style="height:{height}px"></div>'
    segs = ""
    for key, cls, _c, lbl in DAG_STATES:
        n = counts.get(key, 0)
        if not n:
            continue
        pc = n / total * 100
        text = ""
        if labels:
            text = f"{n} {lbl}" if pc >= 12 else (str(n) if pc >= 5 else "")
        segs += (
            f'<i class="{cls}" style="width:{pc:.3f}%" title="{n} {lbl}">{text}</i>'
        )
    return f'<div class="statbar" style="height:{height}px">{segs}</div>'


def _caption(counts: dict[str, int]) -> str:
    """e.g. '214 done · 8 in progress · 153 startable · 227 blocked' (nonzero states only)."""
    return " · ".join(
        f"{counts.get(k, 0)} {lbl}" for k, _c, _col, lbl in DAG_STATES if counts.get(k, 0)
    )


def _count_states(atom_list: list[dict], ready_ids: set[str]) -> dict[str, int]:
    c = {k: 0 for k, *_ in DAG_STATES}
    for a in atom_list:
        c[classify_atom(a, ready_ids)] += 1
    return c


def _plan_state(counts: dict[str, int]) -> str:
    tot = sum(counts.values())
    if not tot:
        return "empty"
    if counts["done"] == tot:
        return "done"
    if counts["done"] or counts["in_progress"]:
        return "in_progress"
    if counts["ready"]:
        return "ready"
    return "blocked"


# ── rendering ──


def render(
    plans: list[Plan], queue: QueueStats, git: dict, nxt: list[dict], dag: dict, exec_state: dict
) -> str:
    def pct(a, b):
        return round(100 * a / b) if b else 0

    ready = dag.get("ready") or []
    ready_ids = {r.get("id") for r in ready}
    atoms = dag.get("atoms") or {}

    # index atoms by their (long) plan name for the join with roadmap pillar rows
    atoms_by_plan: dict[str, list[dict]] = {}
    for a in atoms.values():
        atoms_by_plan.setdefault(a.get("plan") or a.get("plan_code") or "?", []).append(a)

    # ---- build per-plan records grouped by pillar ----
    pillars: dict[str, list[dict]] = {}
    seen: set[str] = set()

    def _rec(name, code, slug, num, status_line, atom_list):
        counts = _count_states(atom_list, ready_ids)
        state = _plan_state(counts)
        if state == "empty":  # DAG absent → colour by plan-level status
            state = _STATUS_TO_STATE.get(status_kind_for.get(slug, "unknown"), "blocked")
        # pair each atom with its authoritative state so the tile dots match the frontier
        atoms_sorted = sorted(atom_list, key=lambda x: _natkey(x.get("id", "")))
        return {
            "name": name,
            "code": code,
            "slug": slug,
            "num": num,
            "status_line": status_line,
            "atoms": [(a, classify_atom(a, ready_ids)) for a in atoms_sorted],
            "counts": counts,
            "total": sum(counts.values()),
            "state": state,
        }

    status_kind_for = {p.slug: p.status_kind for p in plans}
    for p in plans:
        atom_list = atoms_by_plan.get(p.slug, [])
        code = atom_list[0].get("plan_code", "") if atom_list else ""
        pillars.setdefault(p.pillar, []).append(
            _rec(p.name, code, p.slug, p.number, p.status_line, atom_list)
        )
        seen.add(p.slug)

    # atom-bearing DAG plans that aren't in the roadmap pillar tables land under "Other"
    other = [
        _rec(name, (al[0].get("plan_code", "") if al else ""), name, "—", "", al)
        for name, al in atoms_by_plan.items()
        if name not in seen and al
    ]
    if other:
        pillars["Pillar Z — Other (not in pillar tables)"] = sorted(
            other, key=lambda r: r["name"]
        )

    all_recs = [r for recs in pillars.values() for r in recs]

    # ---- hero band: plans + atoms, each a 4-color stacked bar ----
    plan_counts = {k: 0 for k, *_ in DAG_STATES}
    for r in all_recs:
        plan_counts[r["state"]] += 1
    plan_total = sum(plan_counts.values())

    atom_counts = {k: 0 for k, *_ in DAG_STATES}
    for a in atoms.values():
        atom_counts[classify_atom(a, ready_ids)] += 1
    atom_total = sum(atom_counts.values())

    def herocard(title, counts, total):
        return (
            f'<div class="herocard"><div class="herohead">'
            f'<span class="herotitle">{esc(title)}</span>'
            f'<span class="herobig">{pct(counts["done"], total)}<small>%</small></span>'
            f'<span class="herosub">{counts["done"]}/{total} done</span></div>'
            f'{_bar(counts, total, 30, labels=True)}'
            f'<div class="cap">{esc(_caption(counts))}</div></div>'
        )

    hero = (
        '<section class="hero">'
        + herocard("Plans", plan_counts, plan_total)
        + (herocard("Atoms", atom_counts, atom_total) if atom_total else "")
        + "</section>"
    )

    legend = '<div class="legend">' + "".join(
        f'<span><b style="background:{col}"></b>{lbl}</span>'
        for _k, _c, col, lbl in DAG_STATES
    ) + '<span class="hint">click any tile to drill into its atoms</span></div>'

    # ---- working now (live stack + next-up), from .roadmap-exec-state.json ----
    working = _render_working(exec_state, git, ready)

    # ---- validation strip (cycles / unresolved / dangling) ----
    validation = _render_validation(dag)

    # ---- pillar grid (compact tiles; each is a closed <details>) ----
    grid_sections = ""
    for pillar, recs in pillars.items():
        letter = pillar.split("—")[0].replace("Pillar", "").strip()
        rest = pillar.split("—", 1)[1].strip() if "—" in pillar else pillar
        recs_sorted = sorted(
            recs, key=lambda r: (999 if r["num"] == "—" else int(r["num"]))
        )
        pc = {k: 0 for k, *_ in DAG_STATES}
        for r in recs_sorted:
            for k in pc:
                pc[k] += r["counts"].get(k, 0)
        ptot = sum(pc.values())
        tiles = "".join(_tile(r, pct) for r in recs_sorted)
        grid_sections += (
            f'<section class="pillar"><h2 class="pillar-h">'
            f'<span class="pl-letter">{esc(letter)}</span>'
            f'<span class="pl-name">{esc(rest)}</span>'
            f'<span class="pl-count">{pc["done"]}/{ptot} atoms · '
            f'{pct(pc["done"], ptot)}%</span></h2>'
            f'<div class="grid">{tiles}</div></section>'
        )

    # ---- collapsed extras ----
    extras = _render_extras(dag, nxt, queue, ready)

    return _PAGE.format(
        css=_CSS,
        stamp=time.strftime("%Y-%m-%d %H:%M", time.localtime()),
        plan_pct=pct(plan_counts["done"], plan_total),
        atom_pct=pct(atom_counts["done"], atom_total) if atom_total else 0,
        atom_total=atom_total,
        hero=hero,
        legend=legend,
        working=working,
        validation=validation,
        grid=grid_sections,
        extras=extras,
    )


def _tile(r: dict, pct) -> str:
    state = r["state"]
    total = r["total"]
    p = pct(r["counts"]["done"], total)
    bar = _bar(r["counts"], total, 8) if total else (
        f'<div class="statbar" style="height:8px"><i class="{_DAG_CLASS[state]}" '
        f'style="width:100%"></i></div>'
    )
    atomrows = "".join(
        f'<li><span class="atom {_DAG_CLASS[st]}" title="{esc(_DAG_LABEL[st])}"></span>'
        f'<code>{esc(a.get("id", ""))}</code> {esc(a.get("title", ""))}</li>'
        for a, st in r["atoms"]
    ) or "<li class='muted'>No atoms decomposed for this plan yet.</li>"
    status_html = (
        f'<p class="tstatus">{esc(r["status_line"])}</p>' if r["status_line"] else ""
    )
    link = (
        f'<a class="tlink" href="PersonalClaw/docs/roadmap/plans/{esc(r["slug"])}.md" '
        f'target="_blank">open plan file →</a>'
        if r["slug"]
        else ""
    )
    numlabel = f'{esc(r["num"])}. ' if r["num"] != "—" else ""
    return (
        f'<details class="tile {_DAG_CLASS[state]}"><summary>'
        f'<span class="tcode">{esc(r["code"] or "?")}</span>'
        f'<span class="tname" title="{numlabel}{esc(r["name"])}">{esc(r["name"])}</span>'
        f'<span class="tbar">{bar}</span>'
        f'<span class="tpct">{p}<small>%</small></span></summary>'
        f'<div class="tbody">{status_html}<ul class="atomlist">{atomrows}</ul>{link}</div>'
        f"</details>"
    )


def _render_working(exec_state: dict, git: dict, ready: list) -> str:
    pr_by_branch = git.get("pr_by_branch") or {}
    stack = exec_state.get("stack") or []
    next_up = exec_state.get("next_up") or []

    def pr_link(entry) -> str:
        n = entry.get("pr") or pr_by_branch.get(entry.get("branch") or "")
        if not n:
            return ""
        return f'<a class="prlink" href="{REPO_URL}/pull/{n}" target="_blank">PR #{n}</a>'

    if stack or next_up:
        rows = ""
        for e in stack:
            branch = e.get("branch") or ""
            branch_html = f'<code class="br">{esc(branch)}</code>' if branch else ""
            rows += (
                f'<li class="wn-live"><span class="wn-badge live">{esc(e.get("status", "in flight"))}</span>'
                f'<code>{esc(e.get("atom", ""))}</code> {esc(e.get("title", ""))} '
                f"{branch_html} {pr_link(e)}</li>"
            )
        for e in next_up[:5]:
            recon = e.get("recon")
            recon_html = f'<span class="wn-recon">recon: {esc(recon)}</span>' if recon else ""
            rows += (
                f'<li><span class="wn-badge next">next</span>'
                f'<code>{esc(e.get("atom", ""))}</code> {esc(e.get("title", ""))} {recon_html}</li>'
            )
        head = exec_state.get("head_branch") or ""
        head_html = (
            f'<span class="wn-meta">head-of-stack <code>{esc(head)}</code> · '
            f'main @ <code>{esc(exec_state.get("main_at", ""))}</code></span>'
            if head
            else ""
        )
        return (
            f'<section class="box wn"><h2 class="section">Working now'
            f"{head_html}</h2><ul class='wn-list'>{rows}</ul></section>"
        )

    # fallback: no exec-state file → show the DAG's ready frontier top 5
    rows = "".join(
        f'<li><span class="wn-badge next">startable</span>'
        f'<code>{esc(r.get("id", ""))}</code> {esc(r.get("title", ""))} '
        f'<span class="wn-recon">{esc(r.get("plan", ""))}</span></li>'
        for r in ready[:5]
    ) or "<li class='muted'>Nothing in flight and nothing startable.</li>"
    return (
        f'<section class="box wn"><h2 class="section">Working now '
        f'<span class="wn-meta">no exec-state file — showing DAG ready frontier</span></h2>'
        f"<ul class='wn-list'>{rows}</ul></section>"
    )


def _render_validation(dag: dict) -> str:
    cycles = dag.get("cycles") or []
    dangling = dag.get("dangling") or []
    unresolved = dag.get("unresolved") or []
    if not (cycles or dangling or unresolved):
        return (
            '<section class="box val ok"><span class="valdot ok"></span>'
            "DAG clean — no cycles, no dangling edges, no unresolved cross-plan refs.</section>"
        )
    parts = ""
    if cycles:
        items = "".join(f"<li>{esc(' → '.join(c))}</li>" for c in cycles[:8])
        parts += f'<div class="valblk"><b>Cycles ({len(cycles)})</b><ul>{items}</ul></div>'
    if dangling:
        items = "".join(
            f'<li>{esc(d.get("atom", ""))} → {esc(d.get("dep", ""))}</li>' for d in dangling[:8]
        )
        parts += f'<div class="valblk"><b>Dangling deps ({len(dangling)})</b><ul>{items}</ul></div>'
    if unresolved:
        items = "".join(
            f'<li>{esc(u.get("atom", ""))} → {esc(u.get("ext_ref", ""))}</li>'
            for u in unresolved[:8]
        )
        parts += (
            f'<div class="valblk"><b>Unresolved cross-plan refs ({len(unresolved)})</b>'
            f"<ul>{items}</ul></div>"
        )
    return (
        '<section class="box val warn"><span class="valdot warn"></span>'
        f'<div class="valbody"><b class="valtitle">DAG validation</b>{parts}</div></section>'
    )


def _render_extras(dag: dict, nxt: list[dict], queue: QueueStats, ready: list) -> str:
    out = '<section class="extras"><div class="xhead">'
    out += (
        '<button id="xall" class="xbtn">Expand all plan tiles</button>'
        '<button id="call" class="xbtn">Collapse all</button></div>'
    )

    # full execution order (prose)
    if nxt:
        secs = ""
        for sec in nxt:
            items = "".join(f"<li>{esc(it)}</li>" for it in sec["items"])
            secs += f'<h4>{esc(sec["title"])}</h4><ol>{items}</ol>'
        out += (
            '<details class="xd"><summary>Full execution order '
            "(workspace ROADMAP §5)</summary>"
            f'<div class="xbody nextprose">{secs}</div></details>'
        )

    # startable frontier (authoritative)
    if ready:
        items = "".join(
            f'<li><code>{esc(r.get("id", ""))}</code> {esc(r.get("title", ""))}'
            f'<span class="rf-plan">{esc(r.get("plan", ""))}</span></li>'
            for r in ready
        )
        out += (
            f'<details class="xd"><summary>Startable now — full frontier ({len(ready)})'
            "</summary>"
            f'<p class="xhint">Every dependency satisfied; begin any of these without putting '
            f'another plan in flight.</p><ol class="rf-list">{items}</ol></details>'
        )

    # dependency tiers
    if dag:
        out += _render_tiers(dag)

    # engine session queue
    if queue.total:
        out += (
            f'<details class="xd"><summary>Engine session queue '
            f"({queue.done}/{queue.total} done)</summary>"
            f'<div class="xbody"><p class="xhint">From WF2-SESSION-QUEUE.md — the on-disk '
            f'engine work log.</p><ul class="qstats"><li>{queue.done} done '
            f"({queue.pending_pr} awaiting PR merge)</li><li>{queue.todo} TODO</li>"
            f"<li>{queue.blocked} blocked</li></ul></div></details>"
        )

    out += "</section>"
    return out


def _render_tiers(dag: dict) -> str:
    atoms = dag["atoms"]
    ready_ids = {r.get("id") for r in (dag.get("ready") or [])}
    state = {aid: classify_atom(a, ready_ids) for aid, a in atoms.items()}
    layers = dag_layers(atoms)
    tiers = ""
    for i, layer in enumerate(layers):
        tcounts = {k: 0 for k, *_ in DAG_STATES}
        chips = ""
        for a in sorted(layer, key=lambda x: _natkey(x.get("id", ""))):
            s = state.get(a.get("id", ""), "blocked")
            tcounts[s] += 1
            deps = ", ".join(a.get("deps") or []) or "no deps"
            chips += (
                f'<span class="atomchip {_DAG_CLASS[s]}" '
                f'title="{_tip(a.get("id", "") + " — " + a.get("title", ""))} | '
                f'{_DAG_LABEL[s]} | deps: {_tip(deps)}">{esc(a.get("id", ""))}</span>'
            )
        barsegs = "".join(
            f'<i class="{cls}" style="width:{tcounts[k] / len(layer) * 100:.3f}%"></i>'
            for k, cls, _c, _l in DAG_STATES
            if tcounts[k]
        )
        tiers += (
            f'<div class="tier"><div class="tier-n">tier {i}<span class="tier-c">'
            f'{len(layer)}</span><span class="tier-bar">{barsegs}</span></div>'
            f'<div class="tier-atoms">{chips}</div></div>'
        )
    return (
        f'<details class="xd"><summary>Dependency tiers ({len(layers)} tiers · '
        f'{dag.get("edges", 0)} edges)</summary>'
        f'<p class="xhint">Tier 0 depends on nothing outstanding; each higher tier depends '
        f"only on lower ones. Hover an atom for its scope and deps.</p>"
        f'<div class="tiers">{tiers}</div></details>'
    )


_CSS = """
  :root {
    --bg:#0d1117; --panel:#161b22; --border:#30363d; --text:#e6edf3; --muted:#8b949e;
    --accent:#58a6ff;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
    font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }
  .wrap { max-width:1180px; margin:0 auto; padding:26px 22px 80px; }
  h1 { font-size:20px; margin:0 0 2px; }
  .sub { color:var(--muted); font-size:12px; margin-bottom:20px; }
  code { background:#21262d; padding:1px 5px; border-radius:4px; font-size:11.5px; }
  /* status colors — one source of truth */
  .st-done { background:#3fb950; } .st-inprog { background:#d29922; }
  .st-ready { background:#58a6ff; } .st-blocked { background:#6e7681; }
  /* hero band */
  .hero { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:14px; }
  @media (max-width:720px){ .hero { grid-template-columns:1fr; } }
  .herocard { background:var(--panel); border:1px solid var(--border); border-radius:12px;
    padding:16px 18px; }
  .herohead { display:flex; align-items:baseline; gap:10px; margin-bottom:10px; }
  .herotitle { font-size:13px; text-transform:uppercase; letter-spacing:.08em;
    color:var(--muted); font-weight:600; }
  .herobig { font-size:34px; font-weight:800; letter-spacing:-1px; margin-left:auto; }
  .herobig small { font-size:16px; font-weight:600; color:var(--muted); }
  .herosub { font-size:12px; color:var(--muted); font-variant-numeric:tabular-nums; }
  .statbar { display:flex; width:100%; border-radius:6px; overflow:hidden; background:#21262d; }
  .statbar i { display:flex; align-items:center; justify-content:center; height:100%;
    font-size:10.5px; font-weight:700; color:#0d1117; min-width:0; overflow:hidden;
    white-space:nowrap; }
  .statbar i.st-blocked { color:#c9d1d9; }
  .cap { color:var(--muted); font-size:12px; margin-top:8px; font-variant-numeric:tabular-nums; }
  /* legend */
  .legend { display:flex; flex-wrap:wrap; gap:14px; margin:2px 0 18px; font-size:11.5px;
    color:var(--muted); align-items:center; }
  .legend span { display:inline-flex; align-items:center; gap:5px; }
  .legend b { width:11px; height:11px; border-radius:3px; display:inline-block; }
  .legend .hint { margin-left:auto; font-style:italic; }
  /* generic box */
  .box { background:var(--panel); border:1px solid var(--border); border-radius:12px;
    padding:14px 16px; margin-bottom:14px; }
  .section { font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted);
    margin:0 0 10px; font-weight:600; display:flex; align-items:baseline; gap:10px; }
  /* working now */
  .wn-meta { font-size:11px; text-transform:none; letter-spacing:0; color:var(--muted);
    font-weight:400; margin-left:auto; }
  .wn-meta code { font-size:10.5px; }
  .wn-list { list-style:none; margin:0; padding:0; }
  .wn-list li { padding:6px 0; border-top:1px solid var(--border); font-size:12.5px;
    display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  .wn-list li:first-child { border-top:none; }
  .wn-badge { font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.04em;
    padding:2px 7px; border-radius:5px; color:#0d1117; white-space:nowrap; }
  .wn-badge.live { background:#d29922; }
  .wn-badge.next { background:#30363d; color:var(--muted); }
  .wn-live { background:rgba(210,153,34,.07); }
  .br { color:var(--accent); }
  .prlink { color:var(--accent); text-decoration:none; font-size:11.5px; }
  .prlink:hover { text-decoration:underline; }
  .wn-recon { color:var(--muted); font-size:11px; }
  .muted { color:var(--muted); }
  /* validation */
  .val { display:flex; gap:11px; align-items:flex-start; font-size:12.5px; }
  .val.ok { color:#3fb950; align-items:center; }
  .valdot { width:10px; height:10px; border-radius:50%; flex:0 0 10px; margin-top:4px; }
  .valdot.ok { background:#3fb950; } .valdot.warn { background:#d29922; }
  .val.warn { border-color:#d29922; }
  .valtitle { display:block; margin-bottom:6px; color:var(--text); }
  .valblk { margin-bottom:8px; } .valblk:last-child { margin-bottom:0; }
  .valblk b { color:#f0883e; font-size:12px; }
  .valblk ul { margin:3px 0 0; padding-left:18px; color:var(--muted); font-size:12px; }
  .valblk li { margin-bottom:3px; }
  /* pillar grid */
  .pillar { margin:0 0 18px; }
  .pillar-h { display:flex; align-items:center; gap:10px; margin:0 0 10px; font-size:14px;
    font-weight:600; }
  .pl-letter { width:24px; height:24px; border-radius:7px; background:#21262d;
    color:var(--accent); display:grid; place-items:center; font-weight:800; font-size:13px;
    flex:0 0 24px; }
  .pl-name { flex:1; }
  .pl-count { color:var(--muted); font-size:11.5px; font-weight:500;
    font-variant-numeric:tabular-nums; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:8px; }
  .tile { background:var(--panel); border:1px solid var(--border); border-radius:9px;
    border-left:3px solid var(--border); overflow:hidden; }
  .tile.st-done { border-left-color:#3fb950; }
  .tile.st-inprog { border-left-color:#d29922; }
  .tile.st-ready { border-left-color:#58a6ff; }
  .tile.st-blocked { border-left-color:#6e7681; }
  .tile > summary { display:flex; align-items:center; gap:9px; padding:9px 12px;
    cursor:pointer; list-style:none; }
  .tile > summary::-webkit-details-marker { display:none; }
  .tile > summary:hover { background:#1b2028; }
  .tcode { font-size:10px; font-weight:800; letter-spacing:.03em; color:var(--muted);
    background:#21262d; border-radius:4px; padding:2px 5px; flex:0 0 auto; min-width:34px;
    text-align:center; }
  .tname { flex:1; font-size:12.5px; font-weight:600; overflow:hidden; text-overflow:ellipsis;
    white-space:nowrap; }
  .tbar { flex:0 0 90px; }
  .tpct { flex:0 0 44px; text-align:right; font-size:12px; color:var(--muted);
    font-variant-numeric:tabular-nums; }
  .tpct small { font-size:9px; }
  .tbody { padding:2px 14px 13px; border-top:1px solid var(--border); }
  .tstatus { font-size:12px; color:var(--muted); background:#0d1117; border:1px solid var(--border);
    border-radius:7px; padding:8px 10px; margin:11px 0 10px; }
  .atomlist { list-style:none; margin:0 0 8px; padding:0; }
  .atomlist li { font-size:12px; padding:3px 0; display:flex; align-items:flex-start; gap:7px; }
  .atomlist .atom { width:9px; height:9px; border-radius:3px; flex:0 0 9px; margin-top:4px; }
  .atomlist code { flex:0 0 auto; }
  .tlink { color:var(--accent); font-size:11.5px; text-decoration:none; }
  .tlink:hover { text-decoration:underline; }
  /* collapsed extras */
  .extras { margin-top:24px; }
  .xhead { display:flex; gap:8px; margin-bottom:12px; }
  .xbtn { background:#21262d; color:var(--text); border:1px solid var(--border); border-radius:7px;
    padding:5px 11px; font-size:11.5px; cursor:pointer; }
  .xbtn:hover { border-color:var(--accent); }
  .xd { background:var(--panel); border:1px solid var(--border); border-radius:10px;
    margin-bottom:10px; padding:0 16px; }
  .xd > summary { cursor:pointer; font-weight:600; font-size:13px; padding:12px 0; }
  .xbody { padding-bottom:14px; }
  .xhint { color:var(--muted); font-size:11.5px; margin:0 0 10px; }
  .nextprose h4 { font-size:12px; margin:12px 0 4px; color:var(--accent); }
  .nextprose ol { margin:0 0 6px; padding-left:20px; color:var(--muted); font-size:12px; }
  .nextprose li { margin-bottom:4px; }
  .rf-list { columns:2; column-gap:26px; padding-left:20px; margin:0 0 12px; font-size:12px; }
  @media (max-width:720px){ .rf-list { columns:1; } }
  .rf-list li { margin-bottom:4px; break-inside:avoid; }
  .rf-plan { color:var(--muted); font-size:10.5px; margin-left:6px; }
  .qstats { margin:0; padding-left:18px; font-size:12.5px; color:var(--muted); }
  .qstats li { margin-bottom:3px; }
  /* dependency tiers */
  .tiers { padding-bottom:6px; }
  .tier { display:flex; gap:11px; align-items:flex-start; padding:7px 0;
    border-top:1px solid var(--border); }
  .tier:first-child { border-top:none; }
  .tier-n { flex:0 0 96px; font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; }
  .tier-c { display:block; color:var(--text); font-size:14px; font-weight:700; }
  .tier-bar { display:flex; height:4px; border-radius:2px; overflow:hidden; margin-top:4px;
    background:#21262d; }
  .tier-bar i { display:block; height:100%; }
  .tier-atoms { display:flex; flex-wrap:wrap; gap:4px; }
  .atomchip { color:#0d1117; font-weight:700; font-size:10.5px; padding:2px 6px; border-radius:5px;
    cursor:help; }
  .atomchip.st-blocked { color:#c9d1d9; background:#30363d; }
"""

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PersonalClaw Roadmap</title>
<style>{css}</style></head>
<body><div class="wrap">
  <h1>PersonalClaw Roadmap</h1>
  <div class="sub">Generated {stamp} · {plan_pct}% of plans done · {atom_pct}% of {atom_total} atoms done · derived from dag.json, ROADMAP.md, plan files &amp; live git/exec state</div>
  {hero}
  {legend}
  {working}
  {validation}
  {grid}
  {extras}
</div>
<script>
  const q = s => Array.from(document.querySelectorAll(s));
  const xa = document.getElementById('xall'), ca = document.getElementById('call');
  if (xa) xa.onclick = () => q('details.tile').forEach(d => d.open = true);
  if (ca) ca.onclick = () => q('details.tile').forEach(d => d.open = false);
</script>
</body></html>"""


def main() -> int:
    plans = parse_pillars()
    for p in plans:
        enrich_plan(p)
    queue = parse_queue()
    git = git_state()
    nxt = parse_next()
    dag = parse_atoms()
    exec_state = parse_exec_state()
    html_out = render(plans, queue, git, nxt, dag, exec_state)
    OUT.write_text(html_out, encoding="utf-8")

    print(f"wrote {OUT}")
    if dag:
        atoms = dag["atoms"]
        ready_ids = {r.get("id") for r in (dag.get("ready") or [])}
        counts = {k: 0 for k, *_ in DAG_STATES}
        for a in atoms.values():
            counts[classify_atom(a, ready_ids)] += 1
        print(
            f"  atoms: {len(atoms)} total · {counts['done']} done · "
            f"{counts['in_progress']} in progress · {counts['ready']} startable · "
            f"{counts['blocked']} blocked (sum {sum(counts.values())})"
        )
        print(
            f"  ready_frontier: {len(dag.get('ready') or [])} · cycles "
            f"{len(dag.get('cycles') or [])} · dangling {len(dag.get('dangling') or [])} · "
            f"unresolved {len(dag.get('unresolved') or [])}"
        )
    else:
        print("  DAG: docs/roadmap/atomic/dag.json not present (degraded to plan-status view)")
    stack = len(exec_state.get("stack") or [])
    print(
        f"  plans: {len(plans)} · engine queue {queue.done}/{queue.total} · "
        f"exec-state stack {stack} · open PRs {len(git['pr_by_branch'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
