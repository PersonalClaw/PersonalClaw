import type { CSSProperties } from 'react'
import type { Loop } from '../../lib/api'
import { ACTIVE_LOOP_STATUSES } from '../../lib/loopStatus'
import type { RunSnapshotViewModel } from './runFold'

// ── A loop's phase matrix (#3824's inline card and detail tile) ───────────────────────────────────
//
// One cell for each part of the loop, each with its state in words as well as in its colour, so a
// screen reader and a reader who cannot tell the colours apart hear the same as a glance sees. A
// loop whose stages someone writes (a code or design loop's plan) gets a cell per stage; every other
// kind makes no claim about its sub-goals (`runFold`'s `untracked`), so it gets a cell per cycle,
// which the loop does count: done, the one under way, and the ones its cap leaves.

export type CellState = 'done' | 'running' | 'paused' | 'stalled' | 'failed' | 'pending'
export interface MatrixCell { label: string; state: CellState }
export interface LoopMatrixModel { unit: 'stages' | 'cycles'; cells: MatrixCell[]; earlier: number }

/** The most cycles the matrix draws; a longer loop shows its latest, and says how many came before. */
export const MATRIX_CYCLES = 12

const WORDS: Record<CellState, string> = {
  done: 'done', running: 'running', paused: 'paused', stalled: 'stalled', failed: 'failed', pending: 'to go',
}

/** What the part under way is doing, from the loop's (effective) status. */
function underWay(status: string): CellState {
  if (status === 'failed') return 'failed'
  if (status === 'paused') return 'paused'
  if (status === 'running') return 'running'
  // Parked on something: a question, a block, no progress, a cap that ended it early.
  if (ACTIVE_LOOP_STATUSES.has(status) || status === 'ended_early' || status === 'held') return 'stalled'
  return 'pending'
}

/** The matrix for *loop* at its effective *status*, read off the shared run view-model. */
export function loopMatrix(loop: Pick<Loop, 'total_cycles' | 'max_cycles'>, status: string, vm: Pick<RunSnapshotViewModel, 'steps'> | null): LoopMatrixModel {
  const tracked = (vm?.steps ?? []).filter((s) => s.state !== 'untracked')
  if (tracked.length) {
    return {
      unit: 'stages',
      earlier: 0,
      cells: tracked.map((s) => ({
        label: s.label,
        state: s.state === 'done' ? 'done' : s.state === 'active' ? underWay(status) : 'pending',
      })),
    }
  }
  const done = Math.max(0, loop.total_cycles || 0)
  const cap = loop.max_cycles || 0
  const notStarted = ['ready', 'intake', 'planning', 'review'].includes(status)
  const ended = ['complete', 'stopped'].includes(status)
  // The cycle under way: the next one while the loop is live, the one that failed when it failed,
  // and none once it ended (a stopped loop's unrun cycles never run).
  const current = ended || notStarted ? 0 : (cap ? Math.min(done + 1, cap) : done + 1)
  const last = notStarted ? Math.max(cap, 1) : ended ? done : Math.max(cap, current)
  const cells: MatrixCell[] = []
  for (let n = 1; n <= last; n++) {
    const state: CellState = n === current ? underWay(status) : n <= done ? 'done' : 'pending'
    cells.push({ label: `Cycle ${n}`, state })
  }
  const earlier = Math.max(0, cells.length - MATRIX_CYCLES)
  return { unit: 'cycles', cells: cells.slice(earlier), earlier }
}

/** "3 done · 1 running · 6 to go": every state the matrix holds, in words. */
export function matrixSummary(m: LoopMatrixModel): string {
  const order: CellState[] = ['done', 'running', 'paused', 'stalled', 'failed', 'pending']
  const counts = order.map((s) => [s, m.cells.filter((c) => c.state === s).length + (s === 'done' ? m.earlier : 0)] as const)
  return counts.filter(([, n]) => n > 0).map(([s, n]) => `${n} ${WORDS[s]}`).join(' · ')
}

const CELL_LOOK: Record<CellState, CSSProperties> = {
  done: { background: 'var(--color-ok)' },
  running: { background: 'var(--color-primary)' },
  paused: { boxShadow: 'inset 0 0 0 1.5px var(--color-primary)' },
  stalled: { background: 'var(--color-warn)' },
  failed: { background: 'var(--color-danger)' },
  pending: { boxShadow: 'inset 0 0 0 1px var(--color-outline-variant)' },
}

/** The matrix drawn: a row of cells, each naming its part and its state. */
export function LoopMatrix({ model }: { model: LoopMatrixModel }) {
  if (!model.cells.length) return null
  return (
    <ul aria-label={model.unit === 'stages' ? 'Stages' : 'Cycles'} className="flex flex-wrap items-center gap-1">
      {model.earlier > 0 && (
        <li data-type="caption" className="text-on-surface-low">{`+${model.earlier} earlier`}</li>
      )}
      {model.cells.map((c) => (
        <li key={c.label} title={`${c.label}: ${WORDS[c.state]}`} data-state={c.state}
          className={`size-2.5 shrink-0 ${c.state === 'running' ? 'motion-safe:animate-pulse' : ''}`} style={{ borderRadius: 'var(--radius-xs)', ...CELL_LOOK[c.state] }}>
          <span className="sr-only">{`${c.label}: ${WORDS[c.state]}`}</span>
        </li>
      ))}
    </ul>
  )
}
