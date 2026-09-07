#!/usr/bin/env python3
"""Generate roadmap-dashboard.html — one self-contained page for owner visibility.

Design goal (owner ask, 2026-08-05, refined 2026-08-06): LOW COGNITIVE LOAD. The default
view is ~one screen and answers three questions with no scrolling:

  1. How much is done?    — a hero band: plans-done % AND atoms-done %, each a stacked
                            status bar over the states in `DAG_STATES`, with counts.
  2. What's happening now? — the live execution stack + next-up queue (from
                            `.roadmap-exec-state.json`), with branch + PR links.
  3. Where does the work live? — a compact pillar grid, one tile per plan, click to drill in.

And, since 2026-09-07, a fourth question the first three could not answer: WHY is an atom
not moving? Everything not done, not in progress and not on the ready frontier used to
render as one grey `blocked` bucket — dozens of atoms mixing "the owner can sign this off
today" with "needs a second machine nobody here has" with "waiting its turn behind another
atom". Those want different reactions from different people, so the stuck half is split by
CAUSE (`owner` / `environment` / `waiting`) with an `unclassified` state for atoms the rules
cannot place. See `DAG_STATES` for the vocabulary and `AtomClassifier` for the precedence.

And a fifth, once the answer to the fourth turned out to be "almost all of it is the owner's":
WHICH owner action buys the most? `unblock_leverage` counts, per not-done atom, the atoms that
cross from dependency-blocked to dependency-satisfied if it flips — transitively — and
`_render_leverage` ranks the owner's queue by it, naming the freed ids. Read `dep_blocked_ids`
before touching that: the baseline is what stops the count from being a tautology.

How MANY are in each is deliberately not written down anywhere in this file: run it and read
the summary line. That population turns over daily — the same query returned 37 at main
`281d693b1` and 34 three commits later at `50b3671e3`, because CE-10, EI-6 and PEP-16
flipped to done — so any number pinned in prose here would be wrong by the next tick.

Everything heavier — per-plan atom lists, dependency tiers, the full execution-order prose,
the engine session queue — lives inside `<details>` elements that are CLOSED by default.
No JS framework: native `<details>/<summary>` plus a tiny expand/collapse-all helper.

Derives ENTIRELY from files already maintained as the source of truth, so it never drifts:
  * docs/roadmap/atomic/dag.json  — the atom catalog + the authoritative DAG:
                                    ready_frontier, gated_frontier, cycles, dangling,
                                    unresolved, resolved_edges. The backbone: every count
                                    and colour comes from here, computed at run time.
                                    Sizes belong in the output, not in this docstring —
                                    this line read "602 atoms" for months while the file
                                    grew to 684 (measured at 50b3671e3, 2026-09-07), and a
                                    stale number is most dangerous in the file that
                                    computes the real one.
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
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

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
#: Escape hatch for the workspace guard below — a scratch render from a detached worktree.
ALLOW_DETACHED_ENV = "ROADMAP_DASHBOARD_ALLOW_DETACHED"


def detached_workspace() -> str:
    """Empty when `WORKSPACE` is the workspace this page describes; else WHY it is not.

    `WORKSPACE` is derived as `CORE.parent`, so running this from a git worktree parked
    outside the real workspace (`/private/tmp/wt/PersonalClaw`, say) silently re-points every
    workspace-level INPUT and the OUTPUT: no `ROADMAP.md` §5 prose, no
    `.roadmap-exec-state.json` (so "Working now" quietly degrades to the ready frontier), and
    the page is written to `/private/tmp/wt/roadmap-dashboard.html`, where nobody will read
    it. Nothing raises — a plausible-looking, degraded page appears at the wrong path, which
    is the same defect class as the grey `blocked` bucket this module exists to fix: a
    confident answer that is quietly wrong. Measured 2026-09-07: THREE builders regenerated
    from a worktree. Two produced the degraded page and one nearly published it. The third was
    the run that finally landed this guard (#2671) — a 325KB page written to
    `/private/tmp/roadmap-dashboard.html` beside a real 1.2MB one, exit status 0 — caught only
    because that builder happened to compare the two sizes. Silent success on a wrong output
    is the worst diagnostic shape available, which is why this refuses rather than warns.

    `ROADMAP.md` is the marker because it is the one workspace-level file that must exist for
    the page to be complete, and it is versioned (unlike the exec-state file, which is
    legitimately absent between runs and already degrades visibly on the page).
    """
    if WORKSPACE_ROADMAP.exists():
        return ""
    return (
        f"{WORKSPACE_ROADMAP} does not exist, so {WORKSPACE} is not the workspace this page "
        f"describes — most likely {CORE} is a git worktree parked outside it"
    )


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
    rows = re.findall(r"^\|\s*(S?\d+)\s*\|(.+?)\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$", text, re.M)
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
    # --author @me: only OUR PRs. Without it this map (PR-link resolution in
    # "Working now" + the open-PR count) folds in every other contributor's open PR.
    prs_raw = sh(
        "gh pr list --state open --limit 80 --author @me --json number,headRefName 2>/dev/null",
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


# ── autonomous execution state (in-session scheduled ticks) ──

DRIVER_DIR = WORKSPACE / "roadmap-driver"
DRIVER_LOG = DRIVER_DIR / "tick.log"  # launchd-era; kept only for provenance, no longer read
#: This session's implementation-subagent transcripts — the live "is an atom being
#: built right now" signal, since in-session execution has no lock file.
SUBAGENT_DIR = (
    Path("~/.claude/projects/-Users-golani-PersonalProjects-PersonalClaw").expanduser()
    / "2c96fd5d-a253-48ed-8c32-ff5c3e4330df"
    / "subagents"
)
DRIVER_LABEL = "com.keyurgolani.roadmap-driver"


def parse_driver_state() -> dict:
    """Make autonomous execution legible: which driver is actually running the loop, the
    recent tick outcomes, and OUR open PR stack in base→tip order.

    **The driver is the live agent session, not launchd.** The launchd jobs were
    deliberately UNLOADED (owner decision, 2026-08-10) after a guard bug in the headless
    tick killed 37 healthy runs; the loop now runs as scheduled ticks inside a live
    session. So the truth about ticks comes from ``.roadmap-exec-state.json`` — one
    ``tick_<UTC>`` key written per completed tick — and NOT from ``roadmap-driver/tick.log``,
    which froze the moment launchd was unloaded. Reading the stale log here is what made
    this panel report a driver that had not run in hours as though it were live.

    launchd is still probed, but only to assert it is OFF: if a job ever comes back while
    a session is also ticking, two drivers would race for the same lock, and saying so is
    more useful than silently preferring one.
    """
    launchd_loaded = False
    try:
        uid = os.getuid()
        out = sh(f"launchctl print gui/{uid}/{DRIVER_LABEL} 2>/dev/null", timeout=8)
        launchd_loaded = bool(out.strip())
    except Exception:
        launchd_loaded = False

    # Ticks come from exec-state: one key per COMPLETED tick, newest last. Each carries
    # the atom, the outcome, and (for a health fix) what it repaired.
    ticks: list[dict] = []
    try:
        st = parse_exec_state() or {}
        for key in sorted(k for k in st if k.startswith("tick_")):
            v = st[key] if isinstance(st[key], dict) else {}
            stamp = key[len("tick_") :]
            # TWO key shapes live in this file: the current `YYYYMMDDTHHMMSSZ`, and 36
            # legacy `YYYY-MM-DD<letter>` keys from earlier sessions that carry a DAY but
            # no clock. Slicing the legacy shape as if it had a time produced garbage like
            # `0T:05:-3`, so each shape is parsed as what it actually is.
            if len(stamp) >= 15 and stamp[8:9] == "T":
                iso = (
                    f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T"
                    f"{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}Z"
                )
            elif len(stamp) >= 10 and stamp[4] == "-":
                iso = f"{stamp[0:10]} (no clock)"  # legacy day-only key
            else:
                iso = stamp
            outcome = str(v.get("outcome") or "")
            atom = str(v.get("atom") or "")
            # The verb is derived from the outcome text the tick recorded about itself.
            up = outcome.upper()
            if up.startswith("DONE") or " DONE" in up[:12]:
                verb = "DONE"
            elif "FIXED" in up:
                verb = "FIX"
            elif "BLOCKED" in up:
                verb = "BLOCKED"
            elif "PARTIAL" in up:
                verb = "PARTIAL"
            else:
                verb = "TICK"
            ticks.append({"ts": iso, "verb": verb, "msg": f"{atom} — {outcome}"[:200]})
    except Exception as exc:  # noqa: BLE001 — narrow enough: this block only reads JSON
        # Loudly, because a silent [] here is indistinguishable from "the loop never ran"
        # — which is exactly how a typo'd loader name made this panel claim no ticks
        # existed while exec-state held 62 of them.
        logger_warn(f"driver ticks unreadable ({type(exc).__name__}: {exc})")
        ticks = []

    # OUR open PRs (--author @me), ordered base→tip; a chain only if a PR bases on
    # another of ours (not main). Drops any head==base==main corruptor.
    stack: list[dict] = []
    is_chain = False
    try:
        raw = sh(
            "gh pr list --state open --limit 80 --author @me "
            "--json number,title,headRefName,baseRefName,mergeStateStatus,isDraft 2>/dev/null",
            timeout=25,
        )
        prs = [p for p in json.loads(raw or "[]") if p.get("headRefName") != p.get("baseRefName")]
        by_head = {p["headRefName"]: p for p in prs}
        based_on = {p["baseRefName"] for p in prs if p["baseRefName"] in by_head}
        is_chain = bool(based_on)
        tips = [p for p in prs if p["headRefName"] not in based_on]
        ordered: list[dict] = []
        seen_b: set[str] = set()
        for tip in sorted(tips, key=lambda p: p.get("number", 0)):
            chain = []
            cur = tip
            while cur and cur["headRefName"] not in seen_b:
                seen_b.add(cur["headRefName"])
                chain.append(cur)
                cur = by_head.get(cur.get("baseRefName"))
            ordered.extend(reversed(chain))
        for p in sorted(prs, key=lambda p: p.get("number", 0)):
            if p["headRefName"] not in seen_b:
                ordered.append(p)
        stack = ordered
    except Exception:
        stack = []

    # ── LIVE "what is processing RIGHT NOW" ────────────────────────────────────
    # `.roadmap-exec-state.json` is only written AFTER an atom finishes, so it can
    # never answer "what's running now" — it showed stale hand-written text instead.
    # These signals CAN answer it, and are cheap:
    #   * the tick LOCK dir — created at tick start, held for the whole run
    #   * the last FIRE line — when the running tick started
    #   * the newest per-run log — its sink, growing as it works
    #   * the repo's current branch + dirty files — what it is actually building
    # In-session execution has no lock dir and no run log — those were launchd's. What
    # DOES mean "an atom is being built right now" is an implementation subagent whose
    # transcript is still growing, so that is what this measures. A transcript touched
    # within IDLE_SECS is live; anything older is a finished agent.
    live: dict = {"running": False}
    IDLE_SECS = 300
    try:
        agents = sorted(
            SUBAGENT_DIR.glob("agent-*.jsonl"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        now = time.time()
        for f in agents:
            age = now - f.stat().st_mtime
            if age <= IDLE_SECS:
                live["running"] = True
                live["agent_idle_secs"] = int(age)
                live["agent_bytes"] = f.stat().st_size
                # started ≈ the transcript's creation; birthtime where the FS has it
                started = getattr(f.stat(), "st_birthtime", f.stat().st_ctime)
                live["elapsed_min"] = max(0, int((now - started) // 60))
                break
        if not live["running"] and agents:
            live["last_agent_min_ago"] = int((now - agents[0].stat().st_mtime) // 60)
    except OSError:
        pass

    # A worktree holding commits with no PR is also in-flight work, and it survives a
    # subagent exiting — so it is the second, slower signal.
    try:
        wts = [
            ln.split()[0]
            for ln in (sh("git worktree list 2>/dev/null") or "").splitlines()
            if ln.strip() and "/private/tmp/" in ln
        ]
        live["worktrees"] = len(wts)
    except Exception:
        pass

    live["branch"] = sh("git branch --show-current") or ""
    dirty = sh("git status --porcelain 2>/dev/null")
    dirty_lines = [ln for ln in dirty.splitlines() if ln.strip()]
    live["dirty_files"] = [ln[3:] for ln in dirty_lines][:6]
    live["dirty_count"] = len(dirty_lines)
    if ticks:
        live["last_tick"] = ticks[-1].get("ts", "")

    return {
        "launchd_loaded": launchd_loaded,
        "ticks": ticks,
        "stack": stack,
        "stack_is_chain": is_chain,
        "live": live,
    }


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
        # Startable by deps but held back by a gate the ordering graph cannot see. Each entry
        # carries a `gate` array — the difference between "the owner can act" and "this
        # environment cannot", i.e. rules 1 and 2 of `AtomClassifier`.
        "gated": d.get("gated_frontier", []),
        "topo": d.get("topo_order", []),
        "cycles": d.get("cycles", []),
        "dangling": d.get("dangling", []),
        "unresolved": d.get("unresolved", []),
        "edges": d.get("edge_count", 0),
    }


def atom_deps(atom: dict, atoms: dict[str, dict]) -> list[str]:
    """The atom's dependency ids that EXIST in the catalog — this file's one reading of an edge.

    `deps` is dag.json's edge key, and it carries two kinds of entry: real atom ids, and
    `EXT:<PLAN>:<prose>` pseudo-refs naming work outside the decomposition. Only the first is an
    edge this graph can traverse, so every traversal here goes through this accessor — the tier
    chart and `unblock_leverage` must not end up disagreeing about what blocks what. An unknown
    id is dropped rather than counted as unmet: a dangling edge is a data problem
    `_render_validation` reports, and it must neither make the graph unrenderable nor silently
    inflate a count.

    Display is the exception: `_render_tiers` shows the RAW `deps` list in its tooltip, because
    there the EXT prose is the useful part.
    """
    return [d for d in (atom.get("deps") or []) if d in atoms]


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
        deps = atom_deps(atoms[aid], atoms)
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


# ── DAG status vocabulary — ONE source of truth shared by every bar, chip, dot and legend ──
#
# done / in_progress come straight from the atom, and a `todo` atom on the authoritative
# ready-frontier is `ready`. The rest is the interesting part. Until 2026-09-07 EVERYTHING
# else was a single grey `blocked` — every stuck atom in one bar, and the owner could not see
# which of them were HIS. They are three unrelated situations — a sign-off, repo, release or
# money-spending live run only the owner can do; a machine, OS grant, live account or price
# row this environment lacks; or plain dependency ordering behind another atom — so they get
# three states. `unclassified` is the seventh, and deliberate: an atom the rules cannot place
# must be VISIBLE, because a guessed classification is worse than the grey bucket it replaced
# (the owner would act on it). See `AtomClassifier` for the precedence.


class DagState(NamedTuple):
    """One rendered state. Key, CSS class, colours and wording travel TOGETHER, by design."""

    key: str
    cls: str
    bg: str  #: the fill
    ink: str  #: text ON that fill — half a pair, never set anywhere else (see `_state_css`)
    label: str  #: legend chip + bar-segment wording; keep it chip-short
    hint: str  #: legend tooltip — what this state asks of the reader


DAG_STATES: tuple[DagState, ...] = (
    DagState("done", "st-done", "#3fb950", "#0d1117", "done", "Shipped, with evidence in-tree."),
    DagState(
        "in_progress",
        "st-inprog",
        "#d29922",
        "#0d1117",
        "in progress",
        "Being built right now.",
    ),
    DagState(
        "ready",
        "st-ready",
        "#58a6ff",
        "#0d1117",
        "startable",
        "Every dependency satisfied and ungated — an agent can pick this up now.",
    ),
    DagState(
        "owner",
        "st-owner",
        "#db61a2",
        "#0d1117",
        "owner action",
        "OWNER'S QUEUE: only the owner can move it — a sign-off, a repo or listing to "
        "create, a release to cut, provisioning, commissioning a review, a live run that "
        "spends real money.",
    ),
    DagState(
        "environment",
        "st-env",
        "#a371f7",
        "#0d1117",
        "environment",
        "Needs a machine, an OS grant held by another responsible process, a live external "
        "account or a price row that does not exist here. No decision unblocks it.",
    ),
    DagState(
        "waiting",
        "st-waiting",
        "#6e7681",
        "#c9d1d9",
        "waiting",
        "Blocked only on another unfinished atom — ordinary dependency ordering. Nothing "
        "for anyone to decide.",
    ),
    DagState(
        "unclassified",
        "st-unclass",
        "#f85149",
        "#0d1117",
        "unclassified",
        "The rules could not say WHY this is stuck — read its blocked_reason. Loud on "
        "purpose: a guess here is worse than a grey bucket, because it would be acted on.",
    ),
)
_DAG_CLASS = {s.key: s.cls for s in DAG_STATES}
_DAG_LABEL = {s.key: s.label for s in DAG_STATES}

#: The invariant every percentage on this page rests on: each atom lands in EXACTLY ONE
#: state, so the per-state counts must sum to the atom total. Spelled out as an explicit
#: roll-call instead of `sum(counts.values())` because `sum()` cannot tell a state that is
#: counted from one that was added to `DAG_STATES` and then silently dropped from the hero
#: bar — the second makes every percentage lie while still adding up. Same shape as the
#: per-plan column invariant `tests/test_roadmap_dag_derived.py` asserts over dag.json's
#: `plan_counts`: name the columns, then require them to account for the whole.
_STATE_ROLL_CALL = (
    "done",
    "in_progress",
    "ready",
    "owner",
    "environment",
    "waiting",
    "unclassified",
)


def assert_state_partition(counts: dict[str, int], total: int, what: str) -> None:
    """Raise unless `counts` partitions `total` across EVERY state in `_STATE_ROLL_CALL`.

    Raises (rather than `assert`) so `python -O` cannot strip the only thing standing
    between a new state and a page whose bars quietly stop summing to 100%.
    """
    known = {s.key for s in DAG_STATES}
    unlisted = sorted(known - set(_STATE_ROLL_CALL))
    if unlisted:
        raise AssertionError(
            f"DAG_STATES gained {unlisted} but _STATE_ROLL_CALL was not updated — the "
            f"{what} bar would silently stop summing to its total. Add the state there."
        )
    stale = sorted(set(_STATE_ROLL_CALL) - known)
    if stale:
        raise AssertionError(f"_STATE_ROLL_CALL names {stale}, which DAG_STATES dropped")
    tallied = sum(counts.get(key, 0) for key in _STATE_ROLL_CALL)
    if tallied != total:
        parts = " + ".join(f"{key} {counts.get(key, 0)}" for key in _STATE_ROLL_CALL)
        raise AssertionError(f"{what}: {parts} = {tallied}, but total is {total}")


# When the DAG is absent, fall back to plan-level status.
_STATUS_TO_STATE = {
    "done": "done",
    "in_progress": "in_progress",
    "proposed": "ready",
    # Without the DAG there is no gate array and no blocked_reason, so WHY a deferred /
    # unknown / missing plan is not moving is genuinely unknown here. Say that, rather than
    # colouring the tile `owner` or `waiting` on no evidence.
    "deferred": "unclassified",
    "unknown": "unclassified",
    "missing": "unclassified",
}

#: An OWNER marker in a `blocked_reason`. The first two mirror `OWNER_GATE_RE` in
#: `tools/regen_dag_derived.py` — the regex that puts `"owner"` in a `gated_frontier` gate
#: array — so the two agree by construction on the atoms both can see. `residual` is the
#: dashboard's own addition: it only ever appears on atoms whose status is already `blocked`,
#: which the deriver never considers startable and so never gates.
_OWNER_TAG_RE = re.compile(r"owner[-\s]?(?:gated|only|residual)", re.I)
#: The matching ENVIRONMENT marker. The deriver has no `"env"` gate token, so an
#: environment-gated atom is only legible here through this marker on a `blocked` atom.
_ENV_TAG_RE = re.compile(r"environment[-\s]?gated", re.I)
#: An atom id shaped token; only ids that exist in the catalog are believed (`AtomClassifier
#: ._unfinished_named`), which is what keeps `WORKFLOWS-V2` and `PR #81` out.
_ATOM_ID_RE = re.compile(r"\b[A-Z][A-Z0-9]{0,9}-\d{1,3}\b")
#: "…blocked on a core scanner decision … see issue #2526" — a core DECISION issue named as
#: the blocker. Not owner work (the atom's own scope is met) and not a dep edge the DAG can
#: see, but still just waiting on something else to land.
_DECISION_ISSUE_RE = re.compile(
    r"(?:decision|ruling)\b[^.]{0,140}?issue\s*#\d+"
    r"|issue\s*#\d+[^.]{0,140}?(?:decision|ruling)\b",
    re.I,
)
#: An owner act named as THE BLOCKER, for reasons carrying no explicit marker. Blocker
#: language must introduce it inside the same clause, because the bare phrase is usually
#: PROVENANCE, not a gate: "split from WF2UNI-12 per owner ruling 2026-08-27" records who
#: decided the split, and reading that as "the owner can act" would put an atom that is
#: purely waiting on its predecessor into the owner's queue.
_OWNER_AS_BLOCKER_RE = re.compile(
    r"(?:blocker|blocked|gate|gates|gated|unmet|unblocks?)\b[^.;]{0,80}?"
    r"owner\s+(?:ruling|approval|sign-?off|live\s+run)",
    re.I,
)


@dataclass(frozen=True)
class AtomClassifier:
    """Which `DAG_STATES` key an atom is in, and WHY — mechanically, in a fixed precedence.

    Built once per run by `classifier_for` from the dag block, because three of the rules
    need graph-wide facts (the two frontiers, and every other atom's status).

    The precedence, highest first:

    0. `status` is `done` / `in_progress` — the atom itself settles it; it IS moving.
    1. on `gated_frontier` with `"owner"` in its gate array → **owner**. Deps are all met;
       the only thing left is an owner act. This outranks the gate's other tokens: AR-1 is
       gated `["ext", "owner"]` and it is the owner's ruling that unsticks it.
    2. on `gated_frontier` with no `"owner"` gate (e.g. `["ext"]`) → **environment**.
    3. on `ready_frontier` → **ready**.
    4. `status` is `blocked` → read `blocked_reason`, case-insensitively:
       a. an ENVIRONMENT-GATED marker → **environment**. Checked BEFORE the owner markers
          because an environment reason often also cites the owner ruling that scoped it
          (PCS-9: "ENVIRONMENT-GATED (owner ruling 2026-08-28 split this from …)") and the
          explicit marker is the atom's own answer to this exact question.
       b. an OWNER-GATED / OWNER-ONLY / OWNER-RESIDUAL marker → **owner**.
       c. a core decision issue, or another unfinished atom, named as the blocker →
          **waiting**.
       d. an owner act named as the blocker in prose → **owner**.
       e. otherwise → **unclassified**, and the page says so out loud.
    5. `status` is `todo` and it is on neither frontier → **waiting**: something in its dep
       closure is unfinished, which is exactly why the deriver left it off both frontiers.
       A marker in such an atom's reason describes the gate it will hit LATER, not what is
       stopping it now (DL-10 is OWNER-ONLY, but what it waits on is DL-11).
    6. anything else → **unclassified**.
    """

    ready_ids: frozenset[str]
    gates: dict[str, tuple[str, ...]]  #: atom id → its `gated_frontier` gate tokens
    atoms: dict[str, dict]  #: the whole catalog, for "does this reason name a live blocker"

    def state(self, atom: dict) -> str:
        return self.explain(atom)[0]

    def explain(self, atom: dict) -> tuple[str, str]:
        """`(state, why)` — `why` is shown on the page for `unclassified` and in the log."""
        aid = str(atom.get("id") or "")
        st = str(atom.get("status") or "todo")
        if st in ("done", "in_progress"):
            return st, f"status {st}"
        gate = self.gates.get(aid)
        if gate is not None:
            if "owner" in gate:
                return "owner", "gated_frontier, gate includes owner"
            return "environment", f"gated_frontier, gate {','.join(gate) or '(empty)'}"
        if aid in self.ready_ids:
            return "ready", "ready_frontier"
        reason = str(atom.get("blocked_reason") or "")
        if st == "blocked":
            if _ENV_TAG_RE.search(reason):
                return "environment", "blocked_reason carries an ENVIRONMENT-GATED marker"
            if _OWNER_TAG_RE.search(reason):
                return "owner", "blocked_reason carries an OWNER-GATED/ONLY/RESIDUAL marker"
            if _DECISION_ISSUE_RE.search(reason):
                return "waiting", "blocked_reason names a core decision issue as the blocker"
            named = self._unfinished_named(aid, reason)
            if named:
                return "waiting", f"blocked_reason names unfinished {', '.join(named)}"
            if _OWNER_AS_BLOCKER_RE.search(reason):
                return "owner", "blocked_reason names an owner act as the blocker"
            return "unclassified", (
                "status blocked, but its blocked_reason carries no owner or environment "
                "marker and names neither an unfinished atom nor a decision issue as the "
                "blocker — nothing here can say who unsticks it"
            )
        if st == "todo":
            return "waiting", "todo, on neither frontier — something in its deps is unfinished"
        return "unclassified", f"status {st!r} matches no rule"

    def _unfinished_named(self, aid: str, reason: str) -> list[str]:
        """Atom ids the reason names that exist in the catalog and are not `done`."""
        found: list[str] = []
        for token in _ATOM_ID_RE.findall(reason):
            if token == aid or token in found:
                continue
            other = self.atoms.get(token)
            if other is not None and other.get("status") != "done":
                found.append(token)
        return sorted(found)


def classifier_for(dag: dict) -> AtomClassifier:
    """One classifier for the whole run, from `parse_atoms`' output (or an empty dag)."""
    return AtomClassifier(
        ready_ids=frozenset(str(r.get("id") or "") for r in (dag.get("ready") or [])),
        gates={
            str(g.get("id") or ""): tuple(str(t).lower() for t in (g.get("gate") or ()))
            for g in (dag.get("gated") or [])
        },
        atoms=dag.get("atoms") or {},
    )


def _natkey(aid: str):
    m = re.match(r"([A-Za-z]+)-?(\d+)", aid or "")
    return (m.group(1), int(m.group(2))) if m else (aid or "", 0)


# ── unblock leverage: which not-done atom, if it flipped, would free the most others ──
#
# The classifier above says WHY an atom is stuck. At 650/684 that is no longer the open
# question: nearly the whole remainder is `owner`, so what the page must answer is WHICH of
# those owner acts buys the most. That is a graph measurement, not a judgement, and it was
# being re-derived by hand every cycle instead of computed here.


def dep_blocked_ids(atoms: dict[str, dict]) -> frozenset[str]:
    """The BASELINE: not-done atoms held back by at least one unfinished dependency.

    This set is the entire difference between a measurement and a tautology, so it is computed
    FIRST and nothing outside it can ever be reported as freed. "Are its deps satisfied once X
    lands?" is **vacuously true** for an atom whose deps were never an obstacle — most obviously
    one with no deps at all — so asking only that question makes every atom look like it frees
    every dep-less atom. Run against the live catalog, that shape reported that all 34 not-done
    atoms each unblocked the other 33: absurd on its face, and confidently useless.

    An atom that starts here can be freed. An atom that does not, cannot be freed by anything —
    something other than the graph is holding it, which is itself worth printing.
    """
    done = {aid for aid, a in atoms.items() if a.get("status") == "done"}
    return frozenset(
        aid
        for aid, a in atoms.items()
        if aid not in done and any(d not in done for d in atom_deps(a, atoms))
    )


def unblock_leverage(atoms: dict[str, dict]) -> dict[str, tuple[str, ...]]:
    """Per not-done atom: the not-done atoms that cross from dependency-BLOCKED to
    dependency-SATISFIED if it flips to done — transitively, and nothing else.

    Transitively, because a freed atom's own dependents are part of what the flip buys: AR-1
    frees AR-2 and AR-3 directly, and through them the rest of the AR chain. The cascade is
    confined to `dep_blocked_ids`, so it models exactly one hypothesis — "this atom, and then
    whatever that alone makes startable" — and never credits a flip with atoms whose deps were
    already met.

    Counting, not judging: an atom appears in a freed list because its dep edges say so. Whether
    anyone can then actually START it (a gate, a machine, a live account) is the classifier's
    question, answered elsewhere on the page.

    WHICH GRAPH, stated because the two levels give different answers and other tooling reads
    the same one: this walks the RAW atom→atom graph only — `atom_deps`, so `EXT:<PLAN>:<prose>`
    plan-refs are not edges here. It is NOT the resolved graph the deriver reports `cycles` and
    `edge_count` over, which additionally turns each `EXT:` ref into an atom edge (measured at
    c5369c37a: 705 raw edges and no cycles; 705 + 143 resolved = 848, with one cycle). A leverage
    number therefore answers "what do this atom's own dep edges free", never "what does the
    cross-plan closure free".
    """
    done = {aid for aid, a in atoms.items() if a.get("status") == "done"}
    blocked = dep_blocked_ids(atoms)
    # Unmet-dependency counts + reverse edges, built once for the whole run: a landing atom then
    # only ever decrements, so each source is one linear sweep instead of a re-scan per round.
    unmet: dict[str, set[str]] = {
        aid: {d for d in atom_deps(atoms[aid], atoms) if d not in done} for aid in blocked
    }
    dependents: dict[str, list[str]] = {}
    for aid, missing in unmet.items():
        for dep in missing:
            dependents.setdefault(dep, []).append(aid)

    out: dict[str, tuple[str, ...]] = {}
    for src in (aid for aid in atoms if aid not in done):
        remaining = {aid: len(missing) for aid, missing in unmet.items()}
        freed: list[str] = []
        landing = [src]
        while landing:
            just_landed = landing.pop()
            for aid in dependents.get(just_landed, ()):
                remaining[aid] -= 1
                # Exactly 0 = its last unmet dep just landed. `!= src` keeps a source out of its
                # own freed list if the graph ever has a cycle back to it.
                if remaining[aid] == 0 and aid != src:
                    freed.append(aid)
                    landing.append(aid)
        out[src] = tuple(sorted(freed, key=_natkey))
    return out


# ── bar / caption helpers ──


def _bar(counts: dict[str, int], total: int, height: int, labels: bool = False) -> str:
    """A single stacked horizontal bar over every state (widths = share of total)."""
    if not total:
        return f'<div class="statbar" style="height:{height}px"></div>'
    segs = ""
    for s in DAG_STATES:
        n = counts.get(s.key, 0)
        if not n:
            continue
        pc = n / total * 100
        text = ""
        if labels:
            text = f"{n} {s.label}" if pc >= 12 else (str(n) if pc >= 5 else "")
        segs += f'<i class="{s.cls}" style="width:{pc:.3f}%" title="{n} {s.label}">{text}</i>'
    return f'<div class="statbar" style="height:{height}px">{segs}</div>'


def _caption(counts: dict[str, int]) -> str:
    """e.g. 'N done · N startable · N owner action · N waiting' — nonzero states only."""
    return " · ".join(
        f"{counts.get(s.key, 0)} {s.label}" for s in DAG_STATES if counts.get(s.key, 0)
    )


def _count_states(atom_list: list[dict], clf: AtomClassifier) -> dict[str, int]:
    c = {s.key: 0 for s in DAG_STATES}
    for a in atom_list:
        c[clf.state(a)] += 1
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
    # Nothing shipped and nothing startable: colour the tile by the most ACTIONABLE cause it
    # holds, so a plan the owner could unstick reads as his instead of as generic grey.
    # `unclassified` sorts last: it is reported in full by its own strip, and letting one
    # unplaceable atom repaint a plan whose other atoms are plainly waiting would hide the
    # thing this ordering exists to surface.
    for key in ("owner", "environment", "waiting", "unclassified"):
        if counts.get(key):
            return key
    return "unclassified"


# ── rendering ──


def render(
    plans: list[Plan],
    queue: QueueStats,
    git: dict,
    nxt: list[dict],
    dag: dict,
    exec_state: dict,
    driver: dict,
) -> str:
    def pct(a, b):
        return round(100 * a / b) if b else 0

    ready = dag.get("ready") or []
    clf = classifier_for(dag)
    atoms = dag.get("atoms") or {}

    # index atoms by their (long) plan name for the join with roadmap pillar rows
    atoms_by_plan: dict[str, list[dict]] = {}
    for a in atoms.values():
        atoms_by_plan.setdefault(a.get("plan") or a.get("plan_code") or "?", []).append(a)

    # ---- build per-plan records grouped by pillar ----
    pillars: dict[str, list[dict]] = {}
    seen: set[str] = set()

    def _rec(name, code, slug, num, status_line, atom_list):
        counts = _count_states(atom_list, clf)
        state = _plan_state(counts)
        if state == "empty":  # DAG absent → colour by plan-level status
            state = _STATUS_TO_STATE.get(status_kind_for.get(slug, "unknown"), "unclassified")
        # pair each atom with its authoritative state so the tile dots match the frontier
        atoms_sorted = sorted(atom_list, key=lambda x: _natkey(x.get("id", "")))
        return {
            "name": name,
            "code": code,
            "slug": slug,
            "num": num,
            "status_line": status_line,
            "atoms": [(a, clf.state(a)) for a in atoms_sorted],
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
        pillars["Pillar Z — Other (not in pillar tables)"] = sorted(other, key=lambda r: r["name"])

    all_recs = [r for recs in pillars.values() for r in recs]

    # ---- hero band: plans + atoms, each a stacked bar over every state ----
    plan_counts = {s.key: 0 for s in DAG_STATES}
    for r in all_recs:
        plan_counts[r["state"]] += 1
    plan_total = sum(plan_counts.values())
    assert_state_partition(plan_counts, len(all_recs), "plan hero bar")

    atom_counts = {s.key: 0 for s in DAG_STATES}
    unplaced: list[tuple[dict, str]] = []
    for a in atoms.values():
        state, why = clf.explain(a)
        atom_counts[state] += 1
        if state == "unclassified":
            unplaced.append((a, why))
    atom_total = sum(atom_counts.values())
    # The whole page's arithmetic in one line: every atom in the catalog lands in exactly one
    # state, so a state added to DAG_STATES and forgotten here reds instead of skewing the %.
    assert_state_partition(atom_counts, len(atoms), "atom hero bar")

    def herocard(title, counts, total):
        return (
            f'<div class="herocard"><div class="herohead">'
            f'<span class="herotitle">{esc(title)}</span>'
            f'<span class="herobig">{pct(counts["done"], total)}<small>%</small></span>'
            f'<span class="herosub">{counts["done"]}/{total} done</span></div>'
            f"{_bar(counts, total, 30, labels=True)}"
            f'<div class="cap">{esc(_caption(counts))}</div></div>'
        )

    hero = (
        '<section class="hero">'
        + herocard("Plans", plan_counts, plan_total)
        + (herocard("Atoms", atom_counts, atom_total) if atom_total else "")
        + "</section>"
    )

    # Each swatch carries its state's CLASS, not an inline colour: the fill and the ink that
    # goes on it live together in DAG_STATES and reach the page through `_state_css` only.
    legend = (
        '<div class="legend">'
        + "".join(
            f'<span title="{_tip(s.hint)}"><b class="{s.cls}"></b>{esc(s.label)}</span>'
            for s in DAG_STATES
        )
        + '<span class="hint">click any tile to drill into its atoms</span></div>'
    )

    # ---- working now (live stack + next-up), from .roadmap-exec-state.json ----
    working = _render_working(exec_state, git, ready)

    # ---- the owner's own queue, ranked by what each item unblocks ----
    # Sits directly under "Working now" on purpose: that panel is the agent's queue, this one is
    # the owner's, and the decision they inform ("what do I pick up next") is the same decision.
    leverage = _render_leverage(dag, clf)

    driver_html = _render_driver(driver)

    # ---- validation strip (cycles / unresolved / dangling), then the unplaceable atoms ----
    validation = _render_validation(dag) + _render_unclassified(unplaced)

    # ---- pillar grid (compact tiles; each is a closed <details>) ----
    grid_sections = ""
    for pillar, recs in pillars.items():
        letter = pillar.split("—")[0].replace("Pillar", "").strip()
        rest = pillar.split("—", 1)[1].strip() if "—" in pillar else pillar
        recs_sorted = sorted(recs, key=lambda r: (999 if r["num"] == "—" else int(r["num"])))
        pc = {s.key: 0 for s in DAG_STATES}
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
    extras = _render_extras(dag, nxt, queue, ready, clf)

    return _PAGE.format(
        css=_CSS + _state_css(),
        stamp=time.strftime("%Y-%m-%d %H:%M", time.localtime()),
        staleness=_render_staleness(),
        plan_pct=pct(plan_counts["done"], plan_total),
        atom_pct=pct(atom_counts["done"], atom_total) if atom_total else 0,
        atom_total=atom_total,
        hero=hero,
        legend=legend,
        working=working,
        leverage=leverage,
        driver=driver_html,
        validation=validation,
        grid=grid_sections,
        extras=extras,
    )


def _render_staleness() -> str:
    """Say so, on the page, when this was generated from a tree that is behind `main`.

    Every number here comes from CORE's WORKING TREE, so the page describes whatever
    branch the checkout happens to be on. That is invisible in the output: the "Generated"
    stamp refreshes on every run, so a page rebuilt hourly from a feature branch that is
    30 commits behind `main` looks current and reports stale counts. Measured 2026-08-14:
    the checkout sat on a feature branch 33 commits behind `main` and the page under-
    reported done atoms by 14 while its timestamp said it was minutes old.

    Best-effort and non-fatal: no network fetch (a stale `origin/main` ref just means a
    smaller number), and any git failure renders nothing rather than blocking the page.
    """
    branch = sh("git rev-parse --abbrev-ref HEAD")
    behind = sh("git rev-list --count HEAD..origin/main")
    dirty = sh("git status --porcelain -- docs/roadmap")
    if not behind.isdigit():
        return ""
    n = int(behind)
    bits = []
    if n:
        bits.append(
            f"generated from <code>{esc(branch)}</code>, which is <b>{n} commit(s) behind "
            f"<code>origin/main</code></b> — counts below may under-report shipped work"
        )
    if dirty:
        bits.append("roadmap files have uncommitted local edits")
    if not bits:
        return ""
    logger_warn("dashboard generated from a tree behind origin/main: " + "; ".join(bits))
    return (
        '<div class="sub" style="background:#3d2b12;border:1px solid #9e6a03;'
        'border-radius:6px;padding:8px 10px;margin:8px 0;color:#e3b341">'
        "⚠ " + " · ".join(bits) + "</div>"
    )


def _tile(r: dict, pct) -> str:
    state = r["state"]
    total = r["total"]
    p = pct(r["counts"]["done"], total)
    bar = (
        _bar(r["counts"], total, 8)
        if total
        else (
            f'<div class="statbar" style="height:8px"><i class="{_DAG_CLASS[state]}" '
            f'style="width:100%"></i></div>'
        )
    )
    atomrows = (
        "".join(
            f'<li><span class="atom {_DAG_CLASS[st]}" title="{esc(_DAG_LABEL[st])}"></span>'
            f'<code>{esc(a.get("id", ""))}</code> {esc(a.get("title", ""))}</li>'
            for a, st in r["atoms"]
        )
        or "<li class='muted'>No atoms decomposed for this plan yet.</li>"
    )
    status_html = f'<p class="tstatus">{esc(r["status_line"])}</p>' if r["status_line"] else ""
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
    # Drop PLACEHOLDER next_up rows (e.g. atom "(driver-selected)") — they are not
    # real queued atoms, and rendering them made this panel read as stale forever
    # while hiding the genuinely useful DAG ready-frontier fallback below.
    next_up = [
        e
        for e in (exec_state.get("next_up") or [])
        if (e.get("atom") or "").strip().strip("()").lower()
        not in {"", "driver-selected", "tbd", "n/a", "-"}
    ]

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
            st = esc(e.get("status", "in flight"))
            rows += (
                f'<li class="wn-live"><span class="wn-badge live">{st}</span>'
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
    rows = (
        "".join(
            f'<li><span class="wn-badge next">startable</span>'
            f'<code>{esc(r.get("id", ""))}</code> {esc(r.get("title", ""))} '
            f'<span class="wn-recon">{esc(r.get("plan", ""))}</span></li>'
            for r in ready[:5]
        )
        or "<li class='muted'>Nothing in flight and nothing startable.</li>"
    )
    return (
        f'<section class="box wn"><h2 class="section">Working now '
        f'<span class="wn-meta">no exec-state file — showing DAG ready frontier</span></h2>'
        f"<ul class='wn-list'>{rows}</ul></section>"
    )


def _render_leverage(dag: dict, clf: AtomClassifier) -> str:
    """The owner's queue, ordered by what each item measurably frees.

    The page already colours an atom as the OWNER'S. What it could not say is which owner act
    buys the most — the one question worth asking when the remainder is almost entirely his. So
    this is his queue, ranked by `unblock_leverage`, with the freed ids NAMED (a bare count is
    not checkable, and the ids are how he sees the chain he is unsticking).

    Zero is printed, never omitted. Most of the queue frees nothing further, and "this unblocks
    nothing else" is exactly the finding an owner deciding where to spend attention needs; hiding
    the zeroes would turn a ranked queue into a highlight reel of four atoms.

    Deliberately absent: any prose about WHY an atom is gated. That text already lives on the
    atom (`blocked_reason`, and the classifier's own hint in the legend); re-narrating it here
    would be this file inventing a second, drifting account of the same fact.
    """
    atoms = dag.get("atoms") or {}
    if not atoms:
        return ""
    leverage = unblock_leverage(atoms)
    not_done = [a for a in atoms.values() if a.get("status") != "done"]
    blocked = dep_blocked_ids(atoms)
    rows = sorted(
        (a for a in not_done if clf.state(a) == "owner"),
        key=lambda a: (-len(leverage.get(str(a.get("id") or ""), ())), _natkey(a.get("id", ""))),
    )
    if not rows:
        return ""

    items = ""
    for a in rows:
        aid = str(a.get("id") or "")
        frees = leverage.get(aid, ())
        if frees:
            named = ", ".join(f"<code>{esc(f)}</code>" for f in frees)
            body = f'<div class="lev-frees">unblocks {len(frees)}: {named}</div>'
        else:
            body = '<div class="lev-frees none">this unblocks nothing else</div>'
        items += (
            f'<li><span class="lev-n{"" if frees else " zero"}">{len(frees)}</span>'
            f'<div class="lev-body"><code>{esc(aid)}</code> {esc(a.get("title", ""))}'
            f'<span class="rf-plan">{esc(a.get("plan_code") or a.get("plan") or "")}</span>'
            f"{body}</div></li>"
        )

    # Measured at run time, like every other number on this page: how much of the remainder the
    # graph is actually holding. When most of it is already dependency-satisfied, ordering by
    # leverage is the honest answer AND the reason the ordering is nearly flat.
    satisfied = sum(1 for a in not_done if str(a.get("id") or "") not in blocked)
    moving = sum(1 for a in rows if leverage.get(str(a.get("id") or "")))
    meta = (
        f"{len(rows)} owner-action atoms · {moving} free another atom · "
        f"{satisfied} of {len(not_done)} not-done atoms are already dependency-satisfied"
    )
    return (
        f'<section class="box lev"><h2 class="section">Owner queue — by unblock leverage'
        f'<span class="wn-meta">{esc(meta)}</span></h2>'
        f'<p class="xhint">The number is how many not-done atoms cross from '
        f"dependency-blocked to dependency-satisfied when this one flips, following the chain. "
        f"Counted from the DAG's <code>deps</code> edges — it says what a flip frees, not "
        f"whether the freed atoms are themselves gated.</p>"
        f'<ol class="lev-list">{items}</ol></section>'
    )


def _render_driver(driver: dict) -> str:
    """The headless-operation window: is the launchd driver live, when did it last
    act and to what effect, and what open PR stack is it building."""
    launchd_loaded = driver.get("launchd_loaded")
    ticks = driver.get("ticks") or []
    stack = driver.get("stack") or []
    live = driver.get("live") or {}

    # The headline is the LAST COMPLETED TICK, because that is what the session records.
    last = ticks[-1] if ticks else None
    last_verb = last.get("verb") if last else ""
    # Health is about the LOOP, not about launchd: an in-session driver with a live
    # subagent or a fresh tick is healthy. launchd being loaded is now a WARNING —
    # two drivers would race for the same atom.
    if launchd_loaded:
        dot, hdr = "warn", "launchd job is loaded TOO — two drivers would race"
    elif live.get("running"):
        dot, hdr = "ok", "Session driver — building an atom now"
    elif ticks:
        dot, hdr = "ok", "Session driver — idle between ticks"
    else:
        dot, hdr = "off", "No ticks recorded yet"

    last_html = ""
    if last:
        last_html = (
            f'<span class="drv-last"><b>{esc(last_verb)}</b> '
            f'<span class="drv-ts">{esc(last.get("ts", ""))}</span> '
            f'{esc((last.get("msg") or "")[:140])}</span>'
        )

    verb_class = {
        "DONE": "done",
        "FIX": "fire",
        "PARTIAL": "warn",
        "BLOCKED": "stall",
        "TICK": "noop",
    }
    # collapse consecutive identical (verb,msg) ticks into one ×N row (kills NOOP walls)
    decisions = list(ticks)
    runs: list[dict] = []
    for t in decisions:
        key = (t.get("verb"), t.get("msg", ""))
        if runs and runs[-1]["key"] == key:
            runs[-1]["count"] += 1
            runs[-1]["last_ts"] = t.get("ts", "")
        else:
            runs.append(
                {
                    "key": key,
                    "verb": t.get("verb"),
                    "msg": t.get("msg", ""),
                    "count": 1,
                    "first_ts": t.get("ts", ""),
                    "last_ts": t.get("ts", ""),
                }
            )

    def _run_row(r: dict) -> str:
        cnt = r["count"]
        vc = verb_class.get(r["verb"], "noop")
        span = f' <span class="drv-ct">×{cnt}</span>' if cnt > 1 else ""
        since = (
            f' <span class="drv-since">since {esc(r["first_ts"][11:19])}</span>' if cnt > 1 else ""
        )
        ts = r["last_ts"]
        # A current key renders as its clock; a legacy day-only key renders as the day.
        shown = ts[11:19] if (len(ts) >= 19 and ts[10:11] == "T") else ts[:10]
        return (
            f'<li><span class="drv-verb {vc}">{esc(r["verb"])}</span>'
            f'{span}<span class="drv-ts">{esc(shown)}</span>'
            f'<span class="drv-msg">{esc((r["msg"] or "")[:110])}</span>{since}</li>'
        )

    tick_rows = "".join(_run_row(r) for r in reversed(runs[-10:])) or (
        '<li class="muted">No ticks recorded in exec-state yet.</li>'
    )

    def pr_row(p: dict, i: int) -> str:
        n = p.get("number")
        ms = (p.get("mergeStateStatus") or "").upper()
        badge = {
            "CLEAN": ("ok", "mergeable"),
            "UNSTABLE": ("warn", "CI running"),
            "BLOCKED": ("warn", "blocked"),
            "DIRTY": ("stall", "CONFLICT"),
            "BEHIND": ("warn", "behind"),
            "UNKNOWN": ("noop", "checking"),
        }.get(ms, ("noop", ms.lower() or "—"))
        draft = " · draft" if p.get("isDraft") else ""
        return (
            f'<li class="drv-pr"><span class="drv-idx">{i}</span>'
            f'<a class="prlink" href="{REPO_URL}/pull/{n}" target="_blank">#{n}</a>'
            f'<span class="drv-prtitle">{esc((p.get("title") or "")[:64])}</span>'
            f'<code class="br">{esc(p.get("headRefName", ""))}</code>'
            f'<span class="drv-verb {badge[0]}">{esc(badge[1])}{draft}</span>'
            f'<span class="drv-base">on {esc(p.get("baseRefName", ""))}</span></li>'
        )

    stack_html = "".join(pr_row(p, i + 1) for i, p in enumerate(stack)) or (
        '<li class="muted">No open PRs of ours — stack empty '
        "(all merged or nothing in flight).</li>"
    )
    is_chain = driver.get("stack_is_chain")
    if not stack:
        stack_label = f"Open PR stack ({len(stack)})"
    elif is_chain:
        stack_label = f"Open PR stack ({len(stack)}) — merge bottom-up"
    else:
        stack_label = f"Open PRs ({len(stack)}) — independent, any merge order"

    # ── "Processing now" strip: LIVE truth, not exec-state's after-the-fact text ──
    if live.get("running"):
        bits = [f'<b>implementation agent live {live.get("elapsed_min", 0)}m</b>']
        idle = live.get("agent_idle_secs")
        if idle is not None:
            bits.append(f"last wrote {idle}s ago")
        if live.get("agent_bytes"):
            bits.append(f'transcript {live["agent_bytes"] // 1024}KB')
        if live.get("worktrees"):
            bits.append(f'{live["worktrees"]} worktree(s) in flight')
        now_html = (
            '<div class="drv-now live"><span class="drv-pulse"></span>'
            '<span class="drv-nowlbl">Processing now</span>'
            f'<span class="drv-nowbody">{" · ".join(bits)}</span></div>'
        )
    else:
        idle_bits = []
        if live.get("last_tick"):
            idle_bits.append(f'last tick completed {esc(live["last_tick"][11:19])}Z')
        last_ag = live.get("last_agent_min_ago")
        if last_ag is not None:
            idle_bits.append(f"no agent for {last_ag}m")
        if live.get("worktrees"):
            idle_bits.append(f'{live["worktrees"]} worktree(s) still on disk')
        idle_bits.append("next tick on the session's schedule")
        now_html = (
            '<div class="drv-now idle"><span class="drv-pulse idle"></span>'
            '<span class="drv-nowlbl">Between ticks</span>'
            f'<span class="drv-nowbody">{" · ".join(idle_bits)}</span></div>'
        )

    return (
        f'<section class="box drv"><h2 class="section">'
        f'<span class="valdot {dot}"></span>Autonomous execution '
        f'<span class="wn-meta">{esc(hdr)} · in-session scheduled ticks · one atom/tick '
        f'· PRs stack on the prior open one{(" · " if last_html else "")}</span>{last_html}</h2>'
        f"{now_html}"
        f'<div class="drv-cols">'
        f'<div class="drv-col"><h3 class="drv-h3">Recent ticks</h3>'
        f'<ul class="drv-ticks">{tick_rows}</ul></div>'
        f'<div class="drv-col"><h3 class="drv-h3">{esc(stack_label)}</h3>'
        f'<ul class="drv-stack">{stack_html}</ul></div>'
        f"</div></section>"
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


def _render_unclassified(rows: list[tuple[dict, str]]) -> str:
    """Name every atom the classifier REFUSED to place, with the reason text it read.

    This strip is the honest half of the why-is-it-stuck split. The alternative — quietly
    defaulting an unmatched atom into `waiting` (invisible) or `owner` (worse: the owner
    would go do something) — buys a tidy page by lying on it. An entry here is a real
    finding about dag.json: either the `blocked_reason` names a blocker the DAG does not
    model as a dep, or the marker vocabulary needs a deliberate extension. Empty renders
    nothing, so a clean run costs no screen space.
    """
    if not rows:
        return ""
    items = ""
    for atom, why in sorted(rows, key=lambda pair: _natkey(pair[0].get("id", ""))):
        reason = (atom.get("blocked_reason") or "").strip()
        quoted = f"“{reason[:220]}…”" if len(reason) > 220 else (f"“{reason}”" if reason else "")
        items += (
            f'<li><code>{esc(atom.get("id", ""))}</code> {esc(atom.get("title", ""))}'
            f'<span class="rf-plan">{esc(atom.get("plan_code") or atom.get("plan") or "")}'
            f'</span><div class="unc-why">{esc(why)}</div>'
            f'<div class="unc-reason">{esc(quoted) or "no blocked_reason recorded"}</div></li>'
        )
    return (
        '<section class="box val warn"><span class="valdot st-unclass"></span>'
        f'<div class="valbody"><b class="valtitle">Unclassified — why these are stuck '
        f"cannot be read from the DAG ({len(rows)})</b>"
        f'<p class="xhint">Not a bucket: each of these is a gap in dag.json or in the '
        f"classifier, and is left uncoloured rather than guessed. Fix the atom's "
        f"<code>blocked_reason</code> (or its deps) upstream — not here.</p>"
        f'<ul class="unc-list">{items}</ul></div></section>'
    )


def _render_extras(
    dag: dict, nxt: list[dict], queue: QueueStats, ready: list, clf: AtomClassifier
) -> str:
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
        CAP = 40
        shown = ready[:CAP]
        items = "".join(
            f'<li><code>{esc(r.get("id", ""))}</code> {esc(r.get("title", ""))}'
            f'<span class="rf-plan">{esc(r.get("plan", ""))}</span></li>'
            for r in shown
        )
        more = ""
        if len(ready) > CAP:
            from collections import Counter

            tail = Counter(r.get("plan", "?") for r in ready[CAP:])
            top = ", ".join(f"{esc(pl)} ×{n}" for pl, n in tail.most_common(6))
            more = (
                f'<li class="rf-more">+{len(ready) - CAP} more across '
                f"{len(tail)} plans — {top}…</li>"
            )
        out += (
            f'<details class="xd"><summary>Startable now — frontier ({len(ready)}, '
            f"showing {len(shown)})</summary>"
            f'<p class="xhint">Every dependency satisfied; begin any of these without putting '
            f'another plan in flight.</p><ol class="rf-list">{items}{more}</ol></details>'
        )

    # dependency tiers
    if dag:
        out += _render_tiers(dag, clf)

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


def _render_tiers(dag: dict, clf: AtomClassifier) -> str:
    atoms = dag["atoms"]
    state = {aid: clf.state(a) for aid, a in atoms.items()}
    layers = dag_layers(atoms)
    tiers = ""
    for i, layer in enumerate(layers):
        tcounts = {s.key: 0 for s in DAG_STATES}
        chips = ""
        for a in sorted(layer, key=lambda x: _natkey(x.get("id", ""))):
            s = state.get(a.get("id", ""), "unclassified")
            tcounts[s] += 1
            deps = ", ".join(a.get("deps") or []) or "no deps"
            chips += (
                f'<span class="atomchip {_DAG_CLASS[s]}" '
                f'title="{_tip(a.get("id", "") + " — " + a.get("title", ""))} | '
                f'{_DAG_LABEL[s]} | deps: {_tip(deps)}">{esc(a.get("id", ""))}</span>'
            )
        barsegs = "".join(
            f'<i class="{st.cls}" style="width:{tcounts[st.key] / len(layer) * 100:.3f}%"></i>'
            for st in DAG_STATES
            if tcounts[st.key]
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
  /* Per-state fills and inks are GENERATED from DAG_STATES by `_state_css()` and appended
     to this sheet — edit the table, never a colour here. */
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
    font-size:10.5px; font-weight:700; min-width:0; overflow:hidden; white-space:nowrap; }
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
  /* owner queue — unblock leverage */
  .lev-list { list-style:none; margin:0; padding:0; }
  .lev-list li { display:flex; gap:10px; align-items:flex-start; padding:6px 0;
    border-top:1px solid var(--border); font-size:12.5px; }
  .lev-list li:first-child { border-top:none; }
  .lev-n { flex:0 0 26px; text-align:center; font-weight:800; font-size:13px;
    font-variant-numeric:tabular-nums; background:#21262d; border-radius:6px; padding:1px 0;
    color:var(--text); }
  .lev-n.zero { color:var(--muted); font-weight:600; }
  .lev-body { flex:1 1 auto; min-width:0; }
  .lev-frees { color:var(--muted); font-size:11.5px; margin-top:3px; }
  .lev-frees code { font-size:10.5px; }
  .lev-frees.none { font-style:italic; }
  /* validation */
  .val { display:flex; gap:11px; align-items:flex-start; font-size:12.5px; }
  .val.ok { color:#3fb950; align-items:center; }
  .valdot { width:10px; height:10px; border-radius:50%; flex:0 0 10px; margin-top:4px; }
  .valdot.ok { background:#3fb950; } .valdot.warn { background:#d29922; }
  .valdot.off { background:#8b949e; } .valdot.stall { background:#f85149; }
  .val.warn { border-color:#d29922; }
  /* autonomous driver panel */
  .drv-now { display:flex; align-items:center; gap:9px; margin:10px 0 2px; padding:7px 10px;
    border-radius:8px; font-size:12px; border:1px solid var(--border); flex-wrap:wrap; }
  .drv-now.live { background:rgba(63,185,80,.08); border-color:rgba(63,185,80,.45); }
  .drv-now.idle { background:#161b22; }
  .drv-pulse { width:8px; height:8px; border-radius:50%; background:#3fb950; flex:0 0 8px;
    box-shadow:0 0 0 0 rgba(63,185,80,.7); animation:drvpulse 2s infinite; }
  .drv-pulse.idle { background:#8b949e; animation:none; box-shadow:none; }
  @keyframes drvpulse { 70% { box-shadow:0 0 0 7px rgba(63,185,80,0); }
    100% { box-shadow:0 0 0 0 rgba(63,185,80,0); } }
  .drv-nowlbl { font-weight:700; font-size:11px; text-transform:uppercase;
    letter-spacing:.05em; flex:0 0 auto; }
  .drv-now.live .drv-nowlbl { color:#3fb950; }
  .drv-now.idle .drv-nowlbl { color:var(--muted); }
  .drv-nowbody { color:var(--muted); flex:1 1 auto; min-width:0; }
  .drv-nowbody b { color:var(--text); }
  .drv .section { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  .drv-last { font-size:11px; text-transform:none; letter-spacing:0; color:var(--muted); }
  .drv-last b { color:var(--text); }
  .drv-cols { display:grid; grid-template-columns:1fr 1.3fr; gap:18px; margin-top:10px; }
  @media (max-width:760px){ .drv-cols { grid-template-columns:1fr; } }
  .drv-h3 { font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted);
    margin:0 0 6px; font-weight:600; }
  .drv-ticks, .drv-stack { list-style:none; margin:0; padding:0; font-size:12px; }
  .drv-ticks li, .drv-stack li { display:flex; align-items:center; gap:8px; padding:2px 0;
    border-bottom:1px solid #21262d; }
  .drv-ticks li:last-child, .drv-stack li:last-child { border-bottom:none; }
  .drv-verb { font-size:9.5px; font-weight:700; text-transform:uppercase; letter-spacing:.04em;
    padding:1px 6px; border-radius:5px; color:#fff; flex:0 0 auto; }
  .drv-verb.fire { background:#3fb950; } .drv-verb.done { background:#238636; }
  .drv-verb.noop { background:#30363d; color:var(--muted); }
  .drv-verb.stall { background:#f85149; } .drv-verb.warn { background:#d29922; }
  .drv-verb.ok { background:#238636; }
  .drv-ts { color:var(--muted); font-variant-numeric:tabular-nums; flex:0 0 auto; font-size:11px; }
  .drv-msg { color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
    flex:1 1 auto; min-width:0; }
  .drv-ct { font-size:10px; font-weight:700; color:var(--text); background:#30363d;
    padding:0 5px; border-radius:5px; flex:0 0 auto; }
  .drv-since { color:var(--muted); font-size:10px; flex:0 0 auto; opacity:.8; }
  .drv-idx { width:18px; height:18px; border-radius:5px; background:#21262d; color:var(--muted);
    display:grid; place-items:center; font-size:10px; font-weight:700; flex:0 0 18px; }
  .drv-prtitle { flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis;
    white-space:nowrap; }
  .drv-base { color:var(--muted); font-size:10.5px; flex:0 0 auto; }
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
  .atomchip { font-weight:700; font-size:10.5px; padding:2px 6px; border-radius:5px;
    cursor:help; }
  /* unclassified strip */
  .unc-list { list-style:none; margin:0; padding:0; font-size:12.5px; }
  .unc-list li { padding:6px 0; border-top:1px solid var(--border); }
  .unc-list li:first-child { border-top:none; }
  .unc-why { color:var(--text); font-size:11.5px; margin-top:3px; }
  .unc-reason { color:var(--muted); font-size:11px; margin-top:2px; }
"""


def _state_css() -> str:
    """Emit every state's colours from `DAG_STATES`, fill and ink in the SAME rule.

    Theme safety, mechanically enforced: a fill and the ink that sits on it are one pair in
    the table and are written out together here, so no state can ship half-dressed. The
    hand-written block this replaced had exactly that bug latent — `.st-*` set four fills,
    while the ink for the grey one lived in two unrelated overrides (`.statbar i.st-blocked`,
    `.atomchip.st-blocked`) that any new state was guaranteed to miss, inheriting near-black
    text on whatever fill it chose.

    Selectors are scoped to the elements that actually wear a state (segment, chip, dot,
    swatch, tile edge) rather than a bare `.st-*`, because `.tile` and `.atom` carry the
    state class too: a bare `.st-owner { background; color }` would repaint a whole plan tile
    pink and set its text near-black on dark. Each is >= 2 classes, so it wins by specificity
    and this block can be appended without depending on where it lands in the cascade.
    """
    out = ["  /* GENERATED from DAG_STATES by _state_css() — do not hand-edit */"]
    for s in DAG_STATES:
        out.append(
            f"  .statbar i.{s.cls}, .atomchip.{s.cls} {{ background:{s.bg}; color:{s.ink}; }}"
        )
        out.append(f"  .atom.{s.cls}, .legend b.{s.cls}, .valdot.{s.cls} {{ background:{s.bg}; }}")
        out.append(f"  .tile.{s.cls} {{ border-left-color:{s.bg}; }}")
    return "\n" + "\n".join(out) + "\n"


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PersonalClaw Roadmap</title>
<style>{css}</style></head>
<body><div class="wrap">
  <h1>PersonalClaw Roadmap</h1>
  <div class="sub">Generated {stamp} · {plan_pct}% of plans done ·
    {atom_pct}% of {atom_total} atoms done · from dag.json + ROADMAP + live git/exec</div>
  {staleness}
  {hero}
  {legend}
  {working}
  {leverage}
  {driver}
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
    # Refuse BEFORE doing any work: writing a degraded page to a path nobody reads is worse
    # than not writing one, and it is indistinguishable from a good run in the exit status.
    detached = detached_workspace()
    if detached:
        if not os.environ.get(ALLOW_DETACHED_ENV):
            logger_warn(f"refusing to write: {detached}")
            logger_warn(
                f"re-run from the real checkout, or set {ALLOW_DETACHED_ENV}=1 to write "
                f"{OUT} anyway — that page has no ROADMAP §5 prose and no exec state"
            )
            return 2
        logger_warn(f"{ALLOW_DETACHED_ENV} set: writing a DEGRADED page — {detached}")

    plans = parse_pillars()
    for p in plans:
        enrich_plan(p)
    queue = parse_queue()
    git = git_state()
    nxt = parse_next()
    dag = parse_atoms()
    exec_state = parse_exec_state()
    driver = parse_driver_state()
    html_out = render(plans, queue, git, nxt, dag, exec_state, driver)
    OUT.write_text(html_out, encoding="utf-8")

    print(f"wrote {OUT}")
    if dag:
        atoms = dag["atoms"]
        clf = classifier_for(dag)
        counts = {s.key: 0 for s in DAG_STATES}
        unplaced: list[tuple[dict, str]] = []
        for a in atoms.values():
            state, why = clf.explain(a)
            counts[state] += 1
            if state == "unclassified":
                unplaced.append((a, why))
        assert_state_partition(counts, len(atoms), "atom census")
        by_state = " · ".join(f"{counts[s.key]} {s.label}" for s in DAG_STATES)
        print(f"  atoms: {len(atoms)} total · {by_state} (sum {sum(counts.values())})")
        # The ranking the page now carries, on the terminal too: the operator running this by
        # hand is usually asking exactly this question.
        lev = unblock_leverage(atoms)
        ranked = sorted(lev.items(), key=lambda kv: (-len(kv[1]), _natkey(kv[0])))
        frees_any = [(aid, ids) for aid, ids in ranked if ids]
        satisfied = len(lev) - len(dep_blocked_ids(atoms))
        top = " · ".join(f"{aid} frees {len(ids)}" for aid, ids in frees_any[:5])
        print(
            f"  unblock leverage: {len(frees_any)} of {len(lev)} not-done atoms free anything · "
            f"{satisfied} already dependency-satisfied" + (f" · {top}" if top else "")
        )
        print(
            f"  ready_frontier: {len(dag.get('ready') or [])} · gated_frontier "
            f"{len(dag.get('gated') or [])} · cycles {len(dag.get('cycles') or [])} · "
            f"dangling {len(dag.get('dangling') or [])} · "
            f"unresolved {len(dag.get('unresolved') or [])}"
        )
        # Loud, not silent: an unplaceable atom is a finding about dag.json, and the page
        # already names it — the operator running this by hand should not have to open it.
        for atom, why in sorted(unplaced, key=lambda pair: _natkey(pair[0].get("id", ""))):
            logger_warn(f"unclassified atom {atom.get('id', '?')}: {why}")
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
