import { describe, it, expect } from 'vitest'
import { scheduleToTrigger, storeToTrigger, eventToTrigger, hookToTrigger } from './triggerMeta'
import { triggerStatusMeta } from '../schedule/scheduleMeta'
import type { ScheduleJob, Trigger as WireTrigger, HookItem } from '../../lib/api'

// ── Claiming a run outcome for a trigger that never ran ────────────────────────────────
//
// The right-hand cell of a trigger row pairs a status GLYPH with a last-run TIME. The adapter fell
// back from `last_run_status` — the actual run outcome — to `last_status`, which is job-level HEALTH.
// The backend reports `'ok'` there for a job that has never fired, so the row drew the ok-green
// CheckCircle beside the word "never".
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
// 🔴 THE GATE MOVED, BECAUSE THE LIST REACHED AROUND IT (issue 496). Gating inside this adapter
// protected `Trigger.runStatus` — and then `TriggersListPage` rendered its dot from the RAW wire pair
// (`t.schedule.last_run_status, t.schedule.last_status`) and never read the guarded field, so the
// green tick came back on **6 of 11** live schedule rows. Worse, the gate only ever existed for the
// schedule kind: a store row with `run_count: 0` and the same default `health: ok` was green too.
//
// So the adapters now keep the two vocabularies SEPARATE (`runStatus` = run outcomes, `health` = the
// `TriggerHealth` rollup, `hasRun` = did anything fire) and `triggerStatusMeta` — the one function
// every surface renders through — owns the gate for all four kinds. These cases therefore assert the
// RENDERED result, which is what the user sees and what the old shape could satisfy while still
// drawing a green tick.

const job = (over: Partial<ScheduleJob>): ScheduleJob => ({
  id: 'schedule:test', name: 'test', enabled: true, schedule: 'At 08:00 AM',
  ...(over as object),
} as ScheduleJob)
const NEVER = { label: 'never run', tone: 'var(--color-on-surface-low)' }

describe('a trigger that never ran claims no outcome', () => {
  it('does not inherit job HEALTH as a run outcome when nothing ran', () => {
    // The measured shape: healthy job, never fired.
    const t = scheduleToTrigger(job({ last_status: 'ok', last_run_ts: null, run_count: 0 } as never))
    expect(t.runStatus, 'job health is not a run outcome').toBeNull()
    expect(t.hasRun, 'and the adapter says so out loud').toBe(false)
    const m = triggerStatusMeta(t)
    expect(m.label).toBe(NEVER.label)
    expect(m.tone).toBe(NEVER.tone)
  })

  it('still uses job health once a run HAS happened', () => {
    // The fallback is not removed — it is gated on a run existing, because `last_status` is the only
    // signal for an older job whose per-run record has aged out.
    const t = scheduleToTrigger(job({ last_status: 'ok', last_run_ts: 1786000000, run_count: 3 } as never))
    expect(t.health).toBe('ok')
    expect(t.hasRun).toBe(true)
    expect(triggerStatusMeta(t).tone).toBe('var(--color-ok)')
  })

  it('counts a run recorded WITHOUT a timestamp as a run', () => {
    // `run_count` is the second half of the signal: a row whose per-run record aged out still fired.
    const t = scheduleToTrigger(job({ last_status: 'ok', last_run_ts: null, run_count: 9 } as never))
    expect(t.hasRun).toBe(true)
    expect(triggerStatusMeta(t).label).toBe('ok')
  })

  it('always prefers the per-run status over job health', () => {
    const t = scheduleToTrigger(job({ last_run_status: 'failed', last_status: 'ok', last_run_ts: 1786000000 } as never))
    expect(t.runStatus, 'the run record wins — that is the T7 rule this extends').toBe('failed')
    expect(triggerStatusMeta(t).tone).toBe('var(--color-danger)')
  })

  it('reports a per-run status even with no timestamp', () => {
    // A run record exists, so there IS an outcome to report; only the health fallback is gated.
    const t = scheduleToTrigger(job({ last_run_status: 'skipped_overlap', last_status: 'degraded', last_run_ts: null } as never))
    expect(t.runStatus).toBe('skipped_overlap')
    // …and the non-ok ROLLUP still outranks it: `degraded` is written by something happening, so it
    // is an observation regardless of `hasRun` (#685's precedence, unchanged).
    expect(triggerStatusMeta(t).label).toBe('degraded')
  })

  it('stays null when the backend offers nothing at all', () => {
    const t = scheduleToTrigger(job({ last_status: null, last_run_status: null, last_run_ts: null } as never))
    expect(t.runStatus).toBeNull()
    expect(triggerStatusMeta(t).label).toBe(NEVER.label)
  })

  it('an error health on a never-run job is NOT withheld', () => {
    // 🔴 CHANGED DELIBERATELY, and the old behaviour was the wrong direction. The adapter used to
    // withhold EVERY health value on a never-run row "because the gate is about whether there was a
    // run" — which also swallowed `error`/`degraded`/`failing`, the values that are only ever written
    // BY something going wrong. So a trigger the backend had marked failing rendered as "never run".
    // The gate now applies only to the value it was built for: the DEFAULT `ok`.
    const t = scheduleToTrigger(job({ last_status: 'error', last_run_ts: null } as never))
    expect(t.runStatus).toBeNull()
    expect(t.health).toBe('error')
    expect(triggerStatusMeta(t).tone).toBe('var(--color-danger)')
  })
})

describe('the same claim, for every other kind', () => {
  // 🔴 The gate used to be schedule-only, which is why this block exists: the identical
  // default-`ok`-with-no-runs shape reaches the list from three more converters.
  it('a store automation with no fires does not claim a successful run', () => {
    const wire = { kind: 'store', id: 'store:file:x', raw_id: 'file:x', name: 'Docs', enabled: true, health: 'ok', state: 'active', run_count: 0 } as WireTrigger
    const t = storeToTrigger(wire)
    expect(t.hasRun).toBe(false)
    expect(triggerStatusMeta(t).label).toBe(NEVER.label)
    expect(triggerStatusMeta(storeToTrigger({ ...wire, run_count: 4 })).label).toBe('ok')
  })

  it('an event trigger with no fires does not claim a successful run', () => {
    const wire = { kind: 'event', id: 'event:x', raw_id: 'x', name: 'x', enabled: true, pattern: 'AppEvent', health: 'ok', state: 'active', fire_count: 0 } as WireTrigger
    const t = eventToTrigger(wire)
    expect(t.hasRun).toBe(false)
    expect(triggerStatusMeta(t).label).toBe(NEVER.label)
    expect(triggerStatusMeta(eventToTrigger({ ...wire, fire_count: 7 })).label).toBe('ok')
  })

  it('a lifecycle hook reports its own run outcome, with no rollup to confuse it', () => {
    const hook = { id: 'h', name: 'h', event: 'PreToolUse', provider: 'notify', enabled: true, run_count: 0, used_by: [] } as unknown as HookItem
    expect(triggerStatusMeta(hookToTrigger(hook)).label).toBe(NEVER.label)
    const ran = hookToTrigger({ ...hook, run_count: 2, last_run: 1786000000, last_status: 'ok' } as HookItem)
    expect(ran.health, 'a hook has no health rollup — none is fabricated').toBeUndefined()
    expect(triggerStatusMeta(ran).label).toBe('ok')
  })
})
