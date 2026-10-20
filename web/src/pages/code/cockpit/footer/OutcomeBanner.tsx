import { withWeight } from '../../../../design/fontWeight'
import { CheckCircle2, XCircle } from 'lucide-react'
import { type CodeProject, type CodeFinding } from '../../../../lib/api'
import { PROJECT_DIR_HIDDEN, PROJECT_DIR_HIDDEN_PREFIXES } from '../files/WorkspaceTree'
import { resolveTouchedPath } from '../cockpitShared'
import { FilesTouched } from '../FilesTouched'

// ── A finished run's outcome ───────────────────────────────────────────────────────────────────
//
// The closing banner of a complete, failed or stopped run: how it ended, and a tally of the stages
// done, the cycles run and the distinct files touched.

/** A closing banner for a terminal project (complete / failed / stopped): the
 *  outcome + a one-line tally (stages done · cycles · distinct files touched).
 *  Nothing for an active/pre-run project — those have their own affordances. */
export function OutcomeBanner({ project: p, findings }: { project: CodeProject; findings: CodeFinding[] }) {
  const TERMINAL: Record<string, { label: string; tone: string; ok: boolean }> = {
    complete: { label: 'Project complete', tone: 'var(--color-ok)', ok: true },
    // failed is NOT a frozen terminal state (the status machine allows failed →
    // running) — frame it as recoverable so the banner agrees with the Resume
    // button + steer box rather than reading like a dead end.
    failed: { label: 'Project failed — fix the issue + Resume to retry', tone: 'var(--color-danger)', ok: false },
    stopped: { label: 'Project stopped', tone: 'var(--color-on-surface-low)', ok: false },
  }
  let meta = TERMINAL[p.status]
  if (!meta) return null
  // A COMPLETE project carrying an error_message finished NON-genuinely (the cycle
  // budget ran out / the loop exhausted before all stages passed their gates). Re-
  // frame it so the user isn't misled by a green "complete" check: a warn tone +
  // honest label, with the persisted reason shown below (the !meta.ok branch).
  const incompleteFinish = p.status === 'complete' && !!p.error_message
  if (incompleteFinish) {
    meta = { label: 'Project ended before finishing', tone: 'var(--color-warn)', ok: false }
  }
  const stages = p.stage_plan?.length ?? 0
  const stagesDone = Object.values(p.stage_status ?? {}).filter((s) => s === 'done').length
  const cycles = p.total_cycles || findings.length
  // distinct files the worker touched across all cycles, scoped to the file root
  // (workspace, or the project files dir for no-workspace doc projects)
  const ws = (p.workspace_dir || p.files_dir || '').replace(/\/$/, '')
  // For a no-workspace project the root is the engine dir, so drop its bookkeeping
  // (findings/, brief.md, …) from "what was built" — only the real deliverables.
  const isProjectDir = !p.workspace_dir
  const root = (ws || '').replace(/\/$/, '')
  // Distinct files the worker actually built — resolve via the shared helper (worktree
  // remap + bare-relative + outside-root rejection), Set-dedup, and in project-dir mode
  // drop engine bookkeeping (incl. the dynamic guidance_<id>.txt prefix this tally
  // previously missed) so the count reflects real deliverables.
  const files = new Set<string>()
  for (const f of findings) for (const raw of (f.files_touched ?? [])) {
    const r = resolveTouchedPath(raw, root)
    if (!r) continue
    if (isProjectDir) {
      const top = r.rel.split('/')[0]
      if (PROJECT_DIR_HIDDEN.has(top) || [...PROJECT_DIR_HIDDEN_PREFIXES].some((pre) => top.startsWith(pre))) continue
    }
    files.add(r.abs)
  }
  const bits = [
    stages > 0 ? `${stagesDone}/${stages} stages` : '',
    cycles > 0 ? `${cycles} cycle${cycles === 1 ? '' : 's'}` : '',
    files.size > 0 ? `${files.size} file${files.size === 1 ? '' : 's'}` : '',
  ].filter(Boolean)
  const Icon = meta.ok ? CheckCircle2 : XCircle
  return (
    <div data-type="body-s" className="mb-3 rounded-lg p-2.5"
      style={{ background: `color-mix(in srgb, ${meta.tone} 12%, transparent)` }}>
      <div className="inline-flex items-center gap-1.5" style={withWeight({ color: meta.tone }, 550)}>
        <Icon size={14} /> {meta.label}
      </div>
      {bits.length > 0 && <p data-type="caption" className="mt-1 text-on-surface-var">{bits.join(' · ')}</p>}
      {!meta.ok && p.error_message && <p data-type="caption" className="mt-1 text-on-surface-low">{p.error_message}</p>}
      {/* Surface the files the worker produced as clickable chips — the payoff of the
          run is "open + read what was built". Shown for ANY terminal outcome that
          produced files: a genuine complete, an incomplete (budget-exhausted) finish,
          AND a stopped/failed run that still wrote real work (reviewing partial output
          is exactly why you'd open a stopped run). Only suppressed when nothing was
          built. */}
      {files.size > 0 && (
        <div className="mt-1.5">
          {/* FilesTouched now owns the cap + "+N more" reveal (max=12 here), so no
              external slice / static "showing 12" note — the toggle is self-describing. */}
          <p data-type="caption" className="text-on-surface-low">What was built — click to open:</p>
          <FilesTouched files={[...files]} ws={ws} max={12} />
        </div>
      )}
    </div>
  )
}
