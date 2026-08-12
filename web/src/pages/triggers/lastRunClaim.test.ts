import { describe, it, expect } from 'vitest'
import { scheduleToTrigger } from './triggerMeta'
import { statusMeta } from '../schedule/scheduleMeta'
import type { ScheduleJob } from '../../lib/api'

// ── Claiming a run outcome for a trigger that never ran ────────────────────────────────
//
// The right-hand cell of a trigger row pairs a status GLYPH (from `lastStatus`) with a last-run TIME
// (from `lastRunTs`). The adapter fell back from `last_run_status` — the actual run outcome — to
// `last_status`, which is job-level HEALTH. The backend reports `'ok'` there for a job that has never
// fired, so the row drew the ok-green CheckCircle beside the word "never".
//
// Measured live on #/triggers, before: **2 of 7 rows** rendered `rgb(14,188,95)` (`--color-ok`) next
// to "never" — `schedule:clock:photographer-nudge…` and `schedule:5fe8293b`, both with
// `last_status: 'ok'`, `last_run_ts: null`, `run_count: 0`. After: both neutral
// `rgb(154,155,156)`, and the five other rows unchanged — including the two lifecycle hooks that
// keep their green for genuine runs.
//
// `scheduleMeta` already names this the one pair a user must never confuse ("a genuinely FAILED
// automation rendered identically to one that had never run"). The same confusion in the other
// direction was being manufactured in this adapter, by mixing a health field into a run claim.
//
// ⚠️ THE REMAINING CONTRADICTION IS A BACKEND GAP, NOT FIXED HERE: two rows show a real time
// ("8d ago") with the neutral never-run glyph, because `last_run_ts` is set while `last_run_status`
// is null. The UI has no outcome to report there, so it must not invent one — recorded in the ledger.

const job = (over: Partial<ScheduleJob>): ScheduleJob => ({
  id: 'schedule:test', name: 'test', enabled: true, schedule: 'At 08:00 AM',
  ...(over as object),
} as ScheduleJob)

describe('a trigger that never ran claims no outcome', () => {
  it('does not inherit job HEALTH as a run outcome when nothing ran', () => {
    // The measured shape: healthy job, never fired.
    const t = scheduleToTrigger(job({ last_status: 'ok', last_run_ts: null, run_count: 0 } as never))
    expect(t.lastStatus, "job health is not a run outcome").toBeNull()
    expect(statusMeta(t.lastStatus).label).toBe('never run')
    expect(statusMeta(t.lastStatus).tone).toBe('var(--color-on-surface-low)')
  })

  it('still uses job health once a run HAS happened', () => {
    // The fallback is not removed — it is gated on a run existing, because `last_status` is the only
    // signal for an older job whose per-run record has aged out.
    const t = scheduleToTrigger(job({ last_status: 'ok', last_run_ts: 1786000000, run_count: 3 } as never))
    expect(t.lastStatus).toBe('ok')
    expect(statusMeta(t.lastStatus).tone).toBe('var(--color-ok)')
  })

  it('always prefers the per-run status over job health', () => {
    const t = scheduleToTrigger(job({ last_run_status: 'failed', last_status: 'ok', last_run_ts: 1786000000 } as never))
    expect(t.lastStatus, 'the run record wins — that is the T7 rule this extends').toBe('failed')
    expect(statusMeta(t.lastStatus).tone).toBe('var(--color-danger)')
  })

  it('reports a per-run status even with no timestamp', () => {
    // A run record exists, so there IS an outcome to report; only the health fallback is gated.
    const t = scheduleToTrigger(job({ last_run_status: 'skipped_overlap', last_status: 'degraded', last_run_ts: null } as never))
    expect(t.lastStatus).toBe('skipped_overlap')
  })

  it('stays null when the backend offers nothing at all', () => {
    const t = scheduleToTrigger(job({ last_status: null, last_run_status: null, last_run_ts: null } as never))
    expect(t.lastStatus).toBeNull()
    expect(statusMeta(t.lastStatus).label).toBe('never run')
  })

  it('an error health on a never-run job is also withheld', () => {
    // Symmetry: the gate is about "was there a run", not about which health value is convenient.
    const t = scheduleToTrigger(job({ last_status: 'error', last_run_ts: null } as never))
    expect(t.lastStatus).toBeNull()
  })
})
