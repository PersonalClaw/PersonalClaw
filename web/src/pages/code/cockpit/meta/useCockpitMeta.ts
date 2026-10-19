import { useEffect, useState } from 'react'
import { api, type CodeProject } from '../../../../lib/api'
import { useVisiblePoll } from '../../../../lib/useVisiblePoll'
import { effectiveLoopStatus } from '../../../../lib/loopStatus'

// ── The cockpit meta strip's controller ───────────────────────────────────────────────────────
//
// What the strip under the cockpit's title knows: the run's status in the registry's words, its
// elapsed time ticking while it runs, its cycles against the budget, its workspace, and the name of
// the Project that contains it. `CockpitMeta.tsx` draws it.

export function useCockpitMeta({ project: p }: { project: CodeProject }) {
  // The Folder chip represents the BOUND workspace only — NOT the engine files dir.
  // A no-workspace project's files_dir is internal bookkeeping (~/.personalclaw/loop/
  // <id>), so falling back to it rendered a folder chip labeled with the loop-id hash
  // (e.g. "da50a558") — a meaningless path that reads as a bug. No workspace → no chip.
  const ws = p.workspace_dir || ''
  const cycles = p.total_cycles || 0
  // Resolve the containing Project's name (the redacted code project carries only its
  // id) so the meta strip can show + link to it — the Code→Project direction, mirroring
  // the Projects page's →linked-work. Fetched once when the binding id is present.
  // tasks_project_id (the provisioned/containing project — set at launch, takes
  // precedence) OR project_id (an explicit pre-launch user scope, before provision runs).
  const projId = p.tasks_project_id || p.project_id || ''
  const [projName, setProjName] = useState('')
  useEffect(() => {
    if (!projId) { setProjName(''); return }
    let alive = true
    api.project(projId).then((pr) => { if (alive) setProjName(pr.name) }).catch(() => {})
    return () => { alive = false }
  }, [projId])
  // Tick every 30s WHILE RUNNING so the live elapsed clock actually advances. Without
  // this, liveStretch is recomputed only when `project` changes (an SSE event), which
  // can be minutes apart during one long worker cycle — the minute-granularity clock
  // would sit frozen then jump in chunks. Visible-only (pauses when the tab is hidden).
  const [, tick] = useState(0)
  useVisiblePoll(() => tick((n) => n + 1), p.status === 'running' ? 30000 : null)
  // elapsed = time banked from prior run stretches (elapsed_seconds, excludes
  // pauses) + the CURRENT stretch while running (server only banks it on
  // pause/stop, so add now − started_at live so a running build's clock ticks).
  const banked = p.elapsed_seconds || 0
  const liveStretch = p.status === 'running' && p.started_at ? Math.max(0, Date.now() / 1000 - p.started_at) : 0
  const elapsed = banked + liveStretch
  // Elapsed leads at the far LEFT; workspace · project · cycles all float to the far
  // RIGHT edge (a single ml-auto group), leaving the StageTrail alone in the middle.
  const wsBase = ws ? (ws.replace(/\/$/, '').split('/').pop() || ws) : ''
  // Show progress toward the cycle BUDGET when capped ("5 / 60 cycles") so the user
  // can gauge how close the run is to its ceiling; uncapped (max_cycles 0) stays a
  // bare count. Also render the cap pre-run (cycles 0) so the configured budget is
  // visible before launch, not only once cycles accrue.
  const cap = p.max_cycles || 0
  const cyclesText = (cycles > 0 || cap > 0)
    ? (cap > 0 ? `${cycles} / ${cap} cycles` : `${cycles} cycle${cycles === 1 ? '' : 's'}`)
    : ''
  const elapsedText = elapsed > 0 ? fmtDuration(elapsed) : ''
  // The project chip is interactive (deep-links to the Project), so it's rendered
  // separately from the plain info items. Show it whenever the work is bound.
  const showProj = !!(projId && projName)
  // The LIFECYCLE STATUS, in the project's own words from the one registry (#671). This strip
  // calls itself "the single place to read where the run is" and named everything except that:
  // elapsed, stage, workspace, project, cycles. On a loop the API reported as `blocked`, the word
  // "blocked" appeared NOWHERE on the page — the Code list shows a status pill (`CodeSection`'s
  // `statusPill`), so the state was legible until you opened the thing it described.
  //
  // Always rendered, including while running: the stage trail says which stage, the streaming dot
  // says whether the feed is live, and neither says whether the run is running, paused, blocked or
  // finished. It also means this strip now always has content, which is why it no longer returns
  // null — a bare pre-run draft reading "Ready" is exactly what the guard's "nothing worth a bar"
  // was mis-deciding.
  //
  // `effectiveLoopStatus` for the same reason every other surface uses it: a `complete` project
  // whose `stop_reason` names a ceiling rather than `done` finished non-genuinely, and must not
  // read as a green "Completed".
  const dispStatus = effectiveLoopStatus(p.status, p.stop_reason, p.held)
  return {
    ws, cycles, projId, projName, wsBase, cap, cyclesText, elapsedText, showProj, dispStatus,
  }
}

/** Humanize a duration in seconds → "3m", "1h 12m", "2d 4h". */
function fmtDuration(secs: number): string {
  const s = Math.floor(secs)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ${m % 60}m`
  const d = Math.floor(h / 24)
  return `${d}d ${h % 24}h`
}
