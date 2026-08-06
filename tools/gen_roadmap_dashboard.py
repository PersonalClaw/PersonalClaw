#!/usr/bin/env python3
"""Generate roadmap-dashboard.html — one self-contained page for owner visibility.

Design goal (owner ask, 2026-08-05): low cognitive load. First screen answers three
questions with no scrolling — how much is done (progress), where the work lives (pillar
map), and what happens next (execution order). Everything else is one click away.

Derives ENTIRELY from files already maintained as the source of truth, so it never drifts:
  * workspace ROADMAP.md          — §5 execution order (what's next), current-state notes
  * docs/roadmap/roadmap.md       — the Plans-by-Pillar tables (number, name, sessions, wave)
  * docs/roadmap/plans/*.md       — each plan's **Status:** line + `## Execution log`
  * docs/roadmap/WF2-SESSION-QUEUE.md — per-session status (DONE / PENDING_PR / TODO / BLOCKED)
  * git + gh                      — unpushed commits, open PRs (best-effort; skipped if absent)

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
OUT = WORKSPACE / "roadmap-dashboard.html"


def sh(cmd: str, cwd: Path = CORE, timeout: int = 20) -> str:
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip()
    except Exception:
        return ""


# ── data model ──


@dataclass
class Plan:
    number: str
    name: str
    slug: str  # plan filename stem (link target), "" if unresolved
    sessions: str  # planned session count, e.g. "~10"
    wave: str
    pillar: str
    status_kind: str = "unknown"  # done | in_progress | proposed | deferred | unknown
    status_line: str = ""
    log_last: list[str] = field(default_factory=list)  # recent execution-log entries

    @property
    def path(self) -> Path | None:
        p = PLANS_DIR / f"{self.slug}.md"
        return p if self.slug and p.exists() else None


PILLAR_RE = re.compile(r"^### (Pillar [A-Z][^\n]*)", re.M)
# | 7 | One Automation Substrate … | [AUTOMATION-SUBSTRATE](plans/WORKFLOWS-V2-AUTOMATION-SUBSTRATE.md) | ~10 | 3 |
ROW_RE = re.compile(
    r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$", re.M
)
LINK_RE = re.compile(r"\[[^\]]+\]\(plans/([A-Za-z0-9._-]+)\.md\)")


def parse_pillars() -> list[Plan]:
    text = ROADMAP_MD.read_text(encoding="utf-8")
    plans: list[Plan] = []
    # Split the document by pillar header so each row is attributed to its pillar.
    parts = PILLAR_RE.split(text)
    # parts = [pre, pillar1, body1, pillar2, body2, …]
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
        plan.status_line = "plan file not found"
        return
    text = path.read_text(encoding="utf-8")
    sm = re.search(r"^\*\*Status:\*\*\s*(.+?)(?:\n\n|\n##|\n\*\*)", text, re.S | re.M)
    if sm:
        line = " ".join(sm.group(1).split())
        plan.status_line = line[:400]
        plan.status_kind = classify_status(line)
    # Pull the last few execution-log bullets (most recent activity).
    log_m = re.search(r"^##+\s*Execution log\s*\n(.+?)(?:\n##\s|\Z)", text, re.S | re.M | re.I)
    if log_m:
        bullets = re.findall(r"^[-*]\s+(.+?)(?=\n[-*]\s|\n##|\Z)", log_m.group(1), re.S | re.M)
        cleaned = [" ".join(re.sub(r"\*\*|`", "", b).split())[:260] for b in bullets]
        plan.log_last = cleaned[-6:][::-1]  # newest first


# ── WF2 session queue ──


@dataclass
class QueueStats:
    total: int = 0
    done: int = 0
    pending_pr: int = 0
    todo: int = 0
    blocked: int = 0
    recent: list[tuple[str, str, str]] = field(default_factory=list)  # (id, kind, subject)


def parse_queue() -> QueueStats:
    if not WF2_QUEUE.exists():
        return QueueStats()
    text = WF2_QUEUE.read_text(encoding="utf-8")
    q = QueueStats()
    rows = re.findall(r"^\|\s*(S?\d+)\s*\|(.+?)\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$", text, re.M)
    for sid, subj, _group, status in rows:
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


# ── git / PR state (best-effort) ──


def git_state() -> dict:
    ahead = sh("git log --oneline origin/main..HEAD 2>/dev/null")
    n_ahead = len([x for x in ahead.splitlines() if x.strip()])
    branch = sh("git branch --show-current")
    prs_raw = sh(
        "gh pr list --state open --limit 60 --json number,headRefName,baseRefName 2>/dev/null",
        timeout=25,
    )
    stack_prs = []
    try:
        for p in json.loads(prs_raw or "[]"):
            if p["headRefName"].startswith("feature-wf2-"):
                stack_prs.append(
                    (
                        p["number"],
                        p["headRefName"].replace("feature-wf2-", ""),
                        p["baseRefName"].replace("feature-wf2-", ""),
                    )
                )
    except Exception:
        pass
    return {
        "branch": branch,
        "ahead": n_ahead,
        "stack_prs": sorted(stack_prs, key=lambda x: x[1]),
    }


# ── what's next (from workspace ROADMAP §5) ──


def parse_next() -> list[dict]:
    """Extract the ordered execution list from ROADMAP §5. Returns section→items."""
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
                items.append(item[:220])
        if items:
            sections.append({"title": title, "items": items[:8]})
    return sections[:8]


# ── HTML rendering ──

KIND_COLOR = {
    "done": "#3fb950",
    "in_progress": "#d29922",
    "proposed": "#8b949e",
    "deferred": "#6e7681",
    "unknown": "#8b949e",
    "missing": "#f85149",
}
KIND_LABEL = {
    "done": "Done",
    "in_progress": "In progress",
    "proposed": "Not started",
    "deferred": "Deferred",
    "unknown": "Unknown",
    "missing": "No plan file",
}


def esc(s: str) -> str:
    return html.escape(s or "")


def render(plans: list[Plan], queue: QueueStats, git: dict, nxt: list[dict]) -> str:
    # Aggregate per pillar.
    pillars: dict[str, list[Plan]] = {}
    for p in plans:
        pillars.setdefault(p.pillar, []).append(p)

    total = len(plans)
    done = sum(1 for p in plans if p.status_kind == "done")
    inprog = sum(1 for p in plans if p.status_kind == "in_progress")
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime())

    def pct(a, b):
        return round(100 * a / b) if b else 0

    # ---- header stat tiles ----
    tiles = [
        ("Plans done", f"{done}/{total}", pct(done, total), KIND_COLOR["done"]),
        ("In progress", str(inprog), None, KIND_COLOR["in_progress"]),
        (
            "Engine sessions",
            f"{queue.done}/{queue.total}",
            pct(queue.done, queue.total),
            KIND_COLOR["done"],
        ),
        (
            "Awaiting PR merge",
            str(queue.pending_pr),
            None,
            "#d29922" if queue.pending_pr else "#3fb950",
        ),
    ]
    tile_html = ""
    for label, value, p, color in tiles:
        bar = (
            f'<div class="mini"><div class="mini-fill" style="width:{p}%;'
            f'background:{color}"></div></div>'
            if p is not None
            else ""
        )
        tile_html += (
            f'<div class="tile"><div class="tile-v" style="color:{color}">{esc(value)}'
            f'</div><div class="tile-l">{esc(label)}</div>{bar}</div>'
        )

    # ---- pillar map (grid of cells, colour = completion) ----
    pillar_cards = ""
    for pillar, ps in pillars.items():
        d = sum(1 for x in ps if x.status_kind == "done")
        p = pct(d, len(ps))
        cells = ""
        for x in sorted(ps, key=lambda z: int(z.number)):
            c = KIND_COLOR.get(x.status_kind, "#8b949e")
            cells += (
                f'<button class="cell" style="--c:{c}" '
                f'data-plan="{x.number}" title="{esc(x.number)}. {esc(x.name)} — '
                f'{esc(KIND_LABEL.get(x.status_kind, ""))}">{esc(x.number)}</button>'
            )
        letter = pillar.split("—")[0].replace("Pillar", "").strip()
        rest = pillar.split("—", 1)[1].strip() if "—" in pillar else ""
        pillar_cards += f"""
        <div class="pillar">
          <div class="pillar-h">
            <span class="pillar-badge">{esc(letter)}</span>
            <span class="pillar-name">{esc(rest)}</span>
            <span class="pillar-pct">{d}/{len(ps)}</span>
          </div>
          <div class="pillar-bar"><div style="width:{p}%"></div></div>
          <div class="cells">{cells}</div>
        </div>"""

    # ---- what's next ----
    next_html = ""
    for i, sec in enumerate(nxt):
        items = "".join(f"<li>{esc(it)}</li>" for it in sec["items"])
        open_attr = "open" if i < 2 else ""
        next_html += (
            f'<details {open_attr}><summary>{esc(sec["title"])}</summary>'
            f"<ol>{items}</ol></details>"
        )

    # ---- stacked-PR strip ----
    pr_html = ""
    if git["stack_prs"]:
        chips = "".join(
            f'<a class="pr-chip" href="https://github.com/PersonalClaw/PersonalClaw/pull/{n}" '
            f'target="_blank">#{n} <b>{esc(h)}</b>←{esc(b)}</a>'
            for n, h, b in git["stack_prs"]
        )
        pr_html = (
            f'<div class="prs"><h3>Stacked PRs open ({len(git["stack_prs"])})</h3>{chips}</div>'
        )
    elif git["ahead"]:
        pr_html = (
            f'<div class="prs warn"><h3>⚠ {git["ahead"]} commits ahead of origin/main '
            f'with no open PRs</h3><p>Branch <code>{esc(git["branch"])}</code> — '
            f"work committed but unpublished.</p></div>"
        )

    # ---- per-plan drill-down (hidden panels) ----
    panels = ""
    for p in sorted(plans, key=lambda z: int(z.number)):
        logs = (
            "".join(f"<li>{esc(entry)}</li>" for entry in p.log_last)
            or "<li>No execution log yet.</li>"
        )
        link = (
            f'<a href="PersonalClaw/docs/roadmap/plans/{esc(p.slug)}.md" '
            f'target="_blank">open plan file →</a>'
            if p.slug
            else ""
        )
        panels += f"""
        <div class="panel" id="plan-{p.number}">
          <button class="panel-close" data-close>✕</button>
          <div class="panel-tag" style="background:{KIND_COLOR.get(p.status_kind)}">
            {esc(KIND_LABEL.get(p.status_kind, "?"))}</div>
          <h2>{esc(p.number)}. {esc(p.name)}</h2>
          <div class="panel-meta">{esc(p.pillar)} · ~{esc(p.sessions)} sessions · wave {esc(p.wave)} · {link}</div>
          <p class="panel-status">{esc(p.status_line)}</p>
          <h4>Recent execution log</h4>
          <ul class="panel-log">{logs}</ul>
        </div>"""

    return TEMPLATE.format(
        stamp=stamp,
        tiles=tile_html,
        pillars=pillar_cards,
        next=next_html or "<p>No execution-order section found.</p>",
        prs=pr_html,
        panels=panels,
        overall_pct=pct(done, total),
    )


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PersonalClaw Roadmap</title>
<style>
  :root {{
    --bg:#0d1117; --panel:#161b22; --border:#30363d; --text:#e6edf3; --muted:#8b949e;
    --accent:#58a6ff;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
    font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:28px 22px 80px; }}
  h1 {{ font-size:20px; margin:0 0 2px; }}
  .sub {{ color:var(--muted); font-size:12px; margin-bottom:22px; }}
  .tiles {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:26px; }}
  .tile {{ background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:14px 16px; }}
  .tile-v {{ font-size:26px; font-weight:700; letter-spacing:-.5px; }}
  .tile-l {{ color:var(--muted); font-size:12px; margin-top:2px; }}
  .mini {{ height:4px; background:#21262d; border-radius:3px; margin-top:9px; overflow:hidden; }}
  .mini-fill {{ height:100%; }}
  .cols {{ display:grid; grid-template-columns:1fr 360px; gap:22px; align-items:start; }}
  @media (max-width:900px) {{ .cols {{ grid-template-columns:1fr; }} .tiles {{ grid-template-columns:repeat(2,1fr); }} }}
  h2.section {{ font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted);
    margin:0 0 12px; font-weight:600; }}
  .pillar {{ background:var(--panel); border:1px solid var(--border); border-radius:10px;
    padding:13px 15px; margin-bottom:12px; }}
  .pillar-h {{ display:flex; align-items:center; gap:9px; margin-bottom:9px; }}
  .pillar-badge {{ width:22px; height:22px; border-radius:6px; background:#21262d; color:var(--accent);
    display:grid; place-items:center; font-weight:700; font-size:12px; }}
  .pillar-name {{ flex:1; font-weight:600; font-size:13px; }}
  .pillar-pct {{ color:var(--muted); font-size:12px; font-variant-numeric:tabular-nums; }}
  .pillar-bar {{ height:5px; background:#21262d; border-radius:3px; overflow:hidden; margin-bottom:11px; }}
  .pillar-bar>div {{ height:100%; background:#3fb950; }}
  .cells {{ display:flex; flex-wrap:wrap; gap:5px; }}
  .cell {{ width:30px; height:26px; border:none; border-radius:6px; cursor:pointer; color:#0d1117;
    font-weight:700; font-size:11px; background:var(--c); opacity:.92;
    border-bottom:2px solid rgba(0,0,0,.28); transition:transform .08s; }}
  .cell:hover {{ transform:translateY(-2px); opacity:1; }}
  aside .box {{ background:var(--panel); border:1px solid var(--border); border-radius:10px;
    padding:15px 16px; margin-bottom:16px; }}
  details {{ border-bottom:1px solid var(--border); padding:6px 0; }}
  details:last-child {{ border-bottom:none; }}
  summary {{ cursor:pointer; font-weight:600; font-size:12.5px; padding:3px 0; }}
  details ol {{ margin:6px 0 8px; padding-left:20px; color:var(--muted); font-size:12px; }}
  details li {{ margin-bottom:5px; }}
  .prs h3 {{ font-size:12px; margin:0 0 9px; color:var(--muted); }}
  .prs.warn {{ border-color:#d29922; }}
  .pr-chip {{ display:inline-block; background:#21262d; border:1px solid var(--border); border-radius:6px;
    padding:2px 7px; margin:2px 3px 2px 0; font-size:11px; color:var(--text); text-decoration:none; }}
  .pr-chip:hover {{ border-color:var(--accent); }}
  .pr-chip b {{ color:var(--accent); }}
  .legend {{ display:flex; gap:14px; flex-wrap:wrap; margin:6px 0 20px; font-size:11.5px; color:var(--muted); }}
  .legend span {{ display:inline-flex; align-items:center; gap:5px; }}
  .dot {{ width:10px; height:10px; border-radius:3px; display:inline-block; }}
  /* drill-down overlay */
  .scrim {{ position:fixed; inset:0; background:rgba(1,4,9,.7); display:none; z-index:10; }}
  .scrim.on {{ display:block; }}
  .panel {{ display:none; position:fixed; top:50%; left:50%; transform:translate(-50%,-50%);
    width:min(680px,92vw); max-height:84vh; overflow:auto; background:var(--panel);
    border:1px solid var(--border); border-radius:12px; padding:24px 26px; z-index:11;
    box-shadow:0 16px 60px rgba(0,0,0,.6); }}
  .panel.on {{ display:block; }}
  .panel h2 {{ margin:6px 0 4px; font-size:18px; }}
  .panel-close {{ position:absolute; top:14px; right:16px; background:none; border:none;
    color:var(--muted); font-size:16px; cursor:pointer; }}
  .panel-tag {{ display:inline-block; color:#0d1117; font-weight:700; font-size:11px;
    padding:2px 8px; border-radius:5px; }}
  .panel-meta {{ color:var(--muted); font-size:12px; margin-bottom:14px; }}
  .panel-meta a {{ color:var(--accent); }}
  .panel-status {{ font-size:13px; background:#0d1117; border:1px solid var(--border);
    border-radius:8px; padding:11px 13px; }}
  .panel h4 {{ font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
    margin:16px 0 7px; }}
  .panel-log {{ margin:0; padding-left:18px; font-size:12px; color:var(--muted); }}
  .panel-log li {{ margin-bottom:8px; }}
  code {{ background:#21262d; padding:1px 5px; border-radius:4px; font-size:12px; }}
  /* execution DAG */
  .dag-cols {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:14px; }}
  @media (max-width:900px) {{ .dag-cols {{ grid-template-columns:1fr; }} }}
  .box h3 {{ font-size:12px; margin:0 0 8px; color:var(--muted); text-transform:uppercase;
    letter-spacing:.06em; }}
  .ready {{ border-color:#238636; }}
  .ready-hint {{ font-size:11.5px; color:var(--muted); margin:0 0 9px; }}
  .ready-list {{ margin:0; padding-left:20px; font-size:12.5px; }}
  .ready-list li {{ margin-bottom:6px; }}
  .ready-plan {{ color:var(--muted); font-size:11px; margin-left:6px; }}
  .prob {{ font-size:12px; margin-bottom:9px; }}
  .prob b {{ color:#f85149; }}
  .prob.ok {{ color:#3fb950; }}
  .prob ul {{ margin:5px 0 0; padding-left:18px; color:var(--muted); }}
  .tiers .tier {{ display:flex; gap:11px; align-items:flex-start; padding:7px 0;
    border-top:1px solid var(--border); }}
  .tiers .tier:first-of-type {{ border-top:none; }}
  .tier-n {{ flex:0 0 74px; font-size:11px; color:var(--muted);
    font-variant-numeric:tabular-nums; }}
  .tier-c {{ display:block; color:var(--text); font-size:14px; font-weight:700; }}
  .tier-atoms {{ display:flex; flex-wrap:wrap; gap:4px; }}
  .atom {{ background:var(--c); color:#0d1117; font-weight:700; font-size:10.5px;
    padding:2px 6px; border-radius:5px; cursor:help;
    border-bottom:2px solid rgba(0,0,0,.28); }}
</style></head>
<body><div class="wrap">
  <h1>PersonalClaw Roadmap</h1>
  <div class="sub">Generated {stamp} · derived from ROADMAP.md, plan files, the WF2 session queue, and live git/PR state · {overall_pct}% of plans done</div>

  <div class="tiles">{tiles}</div>

  <div class="legend">
    <span><i class="dot" style="background:#3fb950"></i>Done</span>
    <span><i class="dot" style="background:#d29922"></i>In progress</span>
    <span><i class="dot" style="background:#8b949e"></i>Not started</span>
    <span><i class="dot" style="background:#6e7681"></i>Deferred</span>
    <span><i class="dot" style="background:#f85149"></i>No plan file</span>
    <span style="margin-left:auto">click any cell to drill in</span>
  </div>

  <div class="cols">
    <main>
      <h2 class="section">Pillar map</h2>
      {pillars}
    </main>
    <aside>
      {prs}
      <div class="box"><h2 class="section" style="margin-bottom:10px">What's next</h2>{next}</div>
    </aside>
  </div>
</div>

<div class="scrim" id="scrim"></div>
{panels}

<script>
  const scrim = document.getElementById('scrim');
  function closeAll() {{
    scrim.classList.remove('on');
    document.querySelectorAll('.panel.on').forEach(p=>p.classList.remove('on'));
  }}
  document.querySelectorAll('.cell').forEach(c=>c.addEventListener('click',()=>{{
    const el = document.getElementById('plan-'+c.dataset.plan);
    if(!el) return;
    closeAll(); scrim.classList.add('on'); el.classList.add('on');
  }}));
  document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',closeAll));
  scrim.addEventListener('click',closeAll);
  document.addEventListener('keydown',e=>{{ if(e.key==='Escape') closeAll(); }});
</script>
</body></html>"""


def parse_atoms() -> dict:
    """The atomic-plan catalog + DAG, when it exists.

    Returns ``{}`` when the decomposition has not landed yet, so the dashboard renders
    exactly as before rather than failing — the atoms are additive, not a precondition.
    """
    dag_path = CORE / "docs/roadmap/atomic/dag.json"
    if not dag_path.exists():
        return {}
    try:
        data = json.loads(dag_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger_warn(f"could not read {dag_path}")
        return {}
    atoms: dict[str, dict] = {}
    for plan in data.get("plans", []):
        for atom in plan.get("atoms", []):
            atom = dict(atom)
            atom["plan"] = plan.get("plan", "")
            atom["plan_code"] = plan.get("code", "")
            atoms[atom.get("id", "")] = atom
    atoms.pop("", None)
    # The workflow returns {plans, dag:{...}}; write_atomic_plans.py persists that verbatim.
    # Read the DAG fields from the nested `dag` object, falling back to the top level so a
    # hand-flattened dag.json also works.
    d = data.get("dag") if isinstance(data.get("dag"), dict) else data
    return {
        "atoms": atoms,
        "ready": d.get("ready_frontier", []),
        "topo": d.get("topo_order", []),
        "cycles": d.get("cycles", []),
        "dangling": d.get("dangling", []),
        "unresolved": d.get("unresolved", []),
        "counts": d.get("plan_counts", []),
        "edges": d.get("edge_count", 0),
    }


def logger_warn(msg: str) -> None:
    print(f"  warning: {msg}", file=sys.stderr)


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


def render_dag(dag: dict) -> str:
    """The DAG section: ready frontier, tiered graph, validation problems."""
    if not dag:
        return ""
    atoms = dag["atoms"]
    layers = dag_layers(atoms)
    total = len(atoms)
    done = sum(1 for a in atoms.values() if a.get("status") == "done")

    ready = dag.get("ready") or []
    ready_html = (
        "".join(
            f'<li><code>{esc(r.get("id", ""))}</code> {esc(r.get("title", ""))}'
            f'<span class="ready-plan">{esc(r.get("plan", ""))}</span></li>'
            for r in ready[:14]
        )
        or "<li>Nothing startable — every remaining atom is blocked.</li>"
    )

    tiers = ""
    for i, layer in enumerate(layers):
        chips = ""
        for a in layer:
            st = a.get("status", "todo")
            color = {"done": "#3fb950", "in_progress": "#d29922"}.get(st, "#8b949e")
            deps = ", ".join(a.get("deps") or []) or "no deps"
            chips += (
                f'<span class="atom" style="--c:{color}" '
                f'title="{esc(a.get("title", ""))}\n\nplan: {esc(a.get("plan", ""))}'
                f'\nstatus: {esc(st)}\ndeps: {esc(deps)}\n\n{esc((a.get("scope") or "")[:260])}">'
                f'{esc(a.get("id", ""))}</span>'
            )
        tiers += (
            f'<div class="tier"><div class="tier-n">tier {i}'
            f'<span class="tier-c">{len(layer)}</span></div>'
            f'<div class="tier-atoms">{chips}</div></div>'
        )

    problems = ""
    for label, rows, fmt in (
        ("Cycles", dag.get("cycles") or [], lambda c: " → ".join(c)),
        (
            "Dangling deps",
            dag.get("dangling") or [],
            lambda d: f'{d.get("atom", "")} → {d.get("dep", "")}',
        ),
        (
            "Unresolved cross-plan refs",
            dag.get("unresolved") or [],
            lambda u: f'{u.get("atom", "")} → {u.get("ext_ref", "")}',
        ),
    ):
        if rows:
            items = "".join(f"<li>{esc(fmt(r))}</li>" for r in rows[:12])
            problems += f"<div class='prob'><b>{esc(label)} ({len(rows)})</b><ul>{items}</ul></div>"
    if not problems:
        problems = "<div class='prob ok'>No cycles, no dangling edges — the DAG is clean.</div>"

    return f"""
      <h2 class="section" style="margin-top:26px">Execution DAG — {total} atoms
        ({done} done, {dag.get('edges', 0)} edges)</h2>
      <div class="dag-cols">
        <div class="box ready">
          <h3>Startable now ({len(ready)})</h3>
          <p class="ready-hint">Every dependency satisfied — begin any of these without
             putting another plan in flight.</p>
          <ol class="ready-list">{ready_html}</ol>
        </div>
        <div class="box">
          <h3>Validation</h3>
          {problems}
        </div>
      </div>
      <div class="box tiers"><h3>Dependency tiers</h3>
        <p class="ready-hint">Tier 0 depends on nothing outstanding. Hover an atom for its
           scope and dependencies.</p>{tiers}</div>"""


def main() -> int:
    plans = parse_pillars()
    for p in plans:
        enrich_plan(p)
    queue = parse_queue()
    git = git_state()
    nxt = parse_next()
    dag = parse_atoms()
    html_out = render(plans, queue, git, nxt)
    if dag:
        # Inject the DAG section just before the closing wrapper so it lands under the map.
        html_out = html_out.replace(
            "</main>\n    <aside>", f"{render_dag(dag)}</main>\n    <aside>", 1
        )
    OUT.write_text(html_out, encoding="utf-8")
    done = sum(1 for p in plans if p.status_kind == "done")
    print(f"wrote {OUT}")
    print(
        f"  {len(plans)} plans ({done} done), {queue.done}/{queue.total} engine sessions, "
        f"{queue.pending_pr} PENDING_PR, {len(git['stack_prs'])} stack PRs, "
        f"{git['ahead']} commits ahead"
    )
    if dag:
        atoms = dag["atoms"]
        a_done = sum(1 for a in atoms.values() if a.get("status") == "done")
        print(
            f"  DAG: {len(atoms)} atoms ({a_done} done), {dag.get('edges', 0)} edges, "
            f"{len(dag.get('ready') or [])} startable, "
            f"{len(dag.get('cycles') or [])} cycles, "
            f"{len(dag.get('dangling') or [])} dangling"
        )
    else:
        print("  DAG: docs/roadmap/atomic/dag.json not present yet (section omitted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
