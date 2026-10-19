import { Loader2, CircleDot, CheckCircle2, Circle } from 'lucide-react'
import { type CodeProject } from '../../../../lib/api'
import { stageKey } from '../cockpitShared'

// ── The cockpit's stage trail ──────────────────────────────────────────────────────────────────
//
// The plan's stages in a row, each done, current or still to come, and on a narrow screen the
// current stage as "stage N/M" with its title.

export function StageTrail({ project }: { project: CodeProject }) {
  const plan = project.stage_plan ?? []
  if (!plan.length) return null
  // While the project is RUNNING, the active stage's icon spins — an at-a-glance
  // "the worker is alive + cycling" cue (the header otherwise looks static).
  const running = project.status === 'running'
  // Pre-launch (ready/review/intake/planning), NO stage is "active" yet — work
  // hasn't begun, so highlighting stage 1 as active falsely implies the design
  // stage is underway before the user clicks Start. Treat the whole trail as
  // upcoming until the project has actually started.
  const started = !['ready', 'review', 'intake', 'planning'].includes(project.status)
  // The active stage = first not-done (matches the backend's active_stage_index),
  // so the header is right even if stage_status briefly lags a transition. -1 when
  // not started → every stage renders 'pending'.
  const activeIdx = started
    ? plan.findIndex((s) => (project.stage_status?.[stageKey(s)] ?? 'pending') !== 'done')
    : -1
  const allDone = started && activeIdx < 0
  const cur = allDone || !started ? null : plan[activeIdx]
  // The active stage's color must not imply healthy progress when the run is actually
  // parked. Only a genuinely RUNNING project's active stage gets the primary (in-flight)
  // color; an attention/halted state (blocked / needs_input / stagnant / failed / stopped)
  // renders it WARN so the trail agrees with the status pill + outcome banner instead of
  // showing a hopeful blue "active" while the worker waits on the user or has stopped.
  const ATTENTION = ['blocked', 'needs_input', 'stagnant', 'failed', 'stopped']
  const halted = ATTENTION.includes(project.status)
  return (
    <>
      {/* full trail on wide screens — min-w-0 + overflow-hidden so a long multi-stage
          plan can't push the project title out or overflow the header; each stage
          label truncates individually. */}
      <div className="ml-1 hidden min-w-0 items-center gap-1 overflow-hidden md:flex">
        {plan.map((s, i) => {
          const st = i < activeIdx || allDone ? 'done' : i === activeIdx ? 'active' : 'pending'
          // A halted (failed/stopped) project's active stage renders muted (not the
          // primary in-progress color) — it stopped here, it isn't working here.
          const color = st === 'done' ? 'var(--color-ok)'
            : st === 'active' ? (halted ? 'var(--color-warn)' : 'var(--color-primary)')
            : 'var(--color-on-surface-low)'
          const activeRunning = st === 'active' && running
          const Icon = st === 'done' ? CheckCircle2 : activeRunning ? Loader2 : st === 'active' ? CircleDot : Circle
          return (
            <span key={i} className="inline-flex min-w-0 items-center gap-1" title={`${s.title || s.stage} — ${st}`}>
              {i > 0 && <span className="shrink-0 text-on-surface-low/40">›</span>}
              <Icon size={12} className={`shrink-0 ${activeRunning ? 'animate-spin' : ''}`} style={{ color }} />
              <span data-type="caption" className="max-w-[7rem] truncate" style={{ color }}>{s.title || s.stage}</span>
            </span>
          )
        })}
      </div>
      {/* compact "stage N/M · title" — always visible (incl. narrow screens where
          the full trail is hidden), so stage context is never lost. Pre-launch it
          shows the plan size as upcoming rather than a misleading "stage 1 active". */}
      <span data-type="caption" className="ml-1 inline-flex items-center gap-1 md:hidden"
        style={{ color: allDone ? 'var(--color-ok)' : halted ? 'var(--color-warn)' : started ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }}>
        {!started
          ? <><Circle size={12} /> {plan.length} stage{plan.length === 1 ? '' : 's'} planned</>
          : allDone
          ? <><CheckCircle2 size={12} /> all stages done</>
          : <>{running ? <Loader2 size={12} className="animate-spin" /> : <CircleDot size={12} />} stage {activeIdx + 1}/{plan.length} · {cur?.title || cur?.stage}</>}
      </span>
    </>
  )
}
