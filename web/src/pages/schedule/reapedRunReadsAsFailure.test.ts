import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { lastRunMeta, statusMeta } from './scheduleMeta'

// ── A reaped run reads as a failure, not "ok" above a red banner (#685) ──────────────────────────
//
// The reaper kills an overrunning turn and writes health_status=degraded + the reap reason —
// while the run-store row it launched still says 'success'. The detail badge read
// `statusMeta(last_run_status || last_status)`, so the ONE record where the fields disagree
// (the reaped run) rendered a green "ok · 1d ago" two lines above the red
// "Reaped after 1818s" banner. `lastRunMeta` is the single reconciler: a non-ok health rollup
// dominates the run row.
//
// The enum sweep is the issue's own ask: this codebase has hit the closed-enum-vs-default-branch
// shape before (#496 is the sibling), so every TriggerHealth member is enumerated against the
// renderer — a NEW member falling through to 'never run' fails here, not in front of a user.

/** Mirror of the backend enum — src/personalclaw/triggers/models.py::TriggerHealth.
 *  The mirror-drift check below reads the Python source, so a new member cannot be
 *  added there without this list (and lastRunMeta's handling) being revisited. */
const TRIGGER_HEALTH = ['ok', 'degraded', 'parked', 'failing'] as const

describe('lastRunMeta reconciles the reaper disagreement (#685)', () => {
  it('the measured record: health=degraded over a success run row reads degraded, warn-toned', () => {
    const m = lastRunMeta('success', 'degraded')
    expect(m.label).toBe('degraded')
    expect(m.tone).toBe('var(--color-warning)')
  })

  it('every non-ok TriggerHealth member dominates a success run row — none reads ok or never-run', () => {
    for (const h of TRIGGER_HEALTH) {
      if (h === 'ok') continue
      const m = lastRunMeta('success', h)
      expect(m.label, `health=${h} must not fall through`).not.toBe('never run')
      expect(m.label, `health=${h} must not read as success`).not.toBe('ok')
      expect(m.tone, `health=${h} must not be ok-green`).not.toBe('var(--color-ok)')
    }
  })

  it('the legacy error value (pre-TriggerHealth last_status) keeps its danger shape', () => {
    const m = lastRunMeta('success', 'error')
    expect(m.label).toBe('error')
    expect(m.tone).toBe('var(--color-danger)')
  })

  it('an ok health defers to the run row exactly as before', () => {
    expect(lastRunMeta('launched', 'ok').label).toBe('launched')
    expect(lastRunMeta('ran_late', 'ok').label).toBe('ran late')
    expect(lastRunMeta('skipped_quiet_hours', 'ok').label).toBe('quiet hours')
    // No health at all: pure run-row behavior, including the honest never-run state.
    expect(lastRunMeta(null, null).label).toBe('never run')
    expect(lastRunMeta(null, 'ok').label).toBe(statusMeta('ok').label)
  })

  it('mirror drift: the backend enum has exactly the members this rail enumerates', () => {
    // Read the Python source so a NEW TriggerHealth member fails this rail instead of
    // silently falling through the renderer (the #496/#685 defect shape).
    const py = readFileSync(
      join(import.meta.dirname, '..', '..', '..', '..', 'src', 'personalclaw', 'triggers', 'models.py'),
      'utf8',
    )
    const enumBody = py.split('class TriggerHealth')[1]?.split('class ')[0] ?? ''
    const members = [...enumBody.matchAll(/^\s+[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1])
    expect(members.sort()).toEqual([...TRIGGER_HEALTH].sort())
  })
})

describe('both renderers consume the reconciler, not the bare chain', () => {
  const read = (rel: string) =>
    readFileSync(join(import.meta.dirname, '..', rel), 'utf8')

  it('the detail badge', () => {
    const s = read('schedule/ScheduleDetail.tsx')
    expect(s).toContain('lastRunMeta(job.last_run_status, job.last_status)')
    expect(s).not.toContain('statusMeta(job.last_run_status || job.last_status)')
  })

  it("the list's schedule rows", () => {
    const s = read('triggers/TriggersListPage.tsx')
    expect(s).toContain('lastRunMeta(t.schedule.last_run_status, t.schedule.last_status)')
  })
})
