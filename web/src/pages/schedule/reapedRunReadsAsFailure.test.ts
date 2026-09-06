import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { triggerStatusMeta, statusMeta } from './scheduleMeta'
import { pyEnumMembers } from '../../design/pySource'

// ── A reaped run reads as a failure, not "ok" above a red banner (#685) ──────────────────────────
//
// The reaper kills an overrunning turn and writes health_status=degraded + the reap reason —
// while the run-store row it launched still says 'success'. The detail badge read
// `statusMeta(last_run_status || last_status)`, so the ONE record where the fields disagree
// (the reaped run) rendered a green "ok · 1d ago" two lines above the red
// "Reaped after 1818s" banner. `triggerStatusMeta` is the single reconciler: a non-ok health rollup
// dominates the run row.
//
// The enum sweep is the issue's own ask: this codebase has hit the closed-enum-vs-default-branch
// shape before (#496 is the sibling), so every TriggerHealth member is enumerated against the
// renderer — a NEW member falling through to 'never run' fails here, not in front of a user.
//
// 🔴 THE RECONCILER WAS RENAMED AND GAINED `hasRun` (issue 496). `lastRunMeta(runStatus, health)`
// fixed the reaper direction and left the opposite one open: with no run row at all it fell back to
// `statusMeta(runStatus || health)`, so a healthy rollup — which DEFAULTS to `ok` on a trigger that
// has never fired — rendered an ok-green tick beside the word "never". Measured on a live gateway:
// 6 of 11 schedule rows, five of them the SYSTEM triggers every fresh home registers. The reconciler
// now takes NAMED facts including `hasRun`, so it cannot be called without answering "has this ever
// run"; the full derived sweep lives in `triggers/triggerStatusVocabulary.test.ts` and this file
// keeps the #685 case it was written for.

/** Read from the Python source rather than mirrored by hand: a hand-copied list is what let the
 *  vocabulary outgrow the renderer in the first place. */
const MODELS = readFileSync(
  join(import.meta.dirname, '..', '..', '..', '..', 'src', 'personalclaw', 'triggers', 'models.py'),
  'utf8',
)
const TRIGGER_HEALTH = pyEnumMembers(MODELS, 'TriggerHealth')

describe('triggerStatusMeta reconciles the reaper disagreement (#685)', () => {
  it('the backend enum is readable — this rail is not vacuous', () => {
    expect(TRIGGER_HEALTH).toContain('degraded')
    expect(TRIGGER_HEALTH.length).toBeGreaterThanOrEqual(4)
  })

  it('the measured record: health=degraded over a success run row reads degraded, warn-toned', () => {
    const m = triggerStatusMeta({ runStatus: 'success', health: 'degraded', hasRun: true })
    expect(m.label).toBe('degraded')
    expect(m.tone).toBe('var(--color-warning)')
  })

  it('every non-ok TriggerHealth member dominates a success run row — none reads ok or never-run', () => {
    for (const h of TRIGGER_HEALTH) {
      if (h === 'ok') continue
      const m = triggerStatusMeta({ runStatus: 'success', health: h, hasRun: true })
      expect(m.label, `health=${h} must not fall through`).not.toBe('never run')
      expect(m.label, `health=${h} must not read as success`).not.toBe('ok')
      expect(m.tone, `health=${h} must not be ok-green`).not.toBe('var(--color-ok)')
    }
  })

  it('the legacy error value (pre-TriggerHealth last_status) keeps its danger shape', () => {
    const m = triggerStatusMeta({ runStatus: 'success', health: 'error', hasRun: true })
    expect(m.label).toBe('error')
    expect(m.tone).toBe('var(--color-danger)')
  })

  it('an ok health defers to the run row exactly as before', () => {
    const ran = { health: 'ok', hasRun: true }
    expect(triggerStatusMeta({ ...ran, runStatus: 'launched' }).label).toBe('launched')
    expect(triggerStatusMeta({ ...ran, runStatus: 'ran_late' }).label).toBe('ran late')
    expect(triggerStatusMeta({ ...ran, runStatus: 'skipped_quiet_hours' }).label).toBe('quiet hours')
    // No health at all: pure run-row behavior, including the honest never-run state.
    expect(triggerStatusMeta({}).label).toBe('never run')
    // 🔴 CHANGED DELIBERATELY — this is the issue-496 half. The case used to assert
    // `lastRunMeta(null, 'ok') === statusMeta('ok')`: A HEALTHY ROLLUP READ AS A SUCCESSFUL RUN.
    // With nothing run, `ok` is the dataclass default, not an observation.
    expect(triggerStatusMeta({ health: 'ok', hasRun: false }).label).toBe('never run')
    expect(triggerStatusMeta({ health: 'ok', hasRun: true }).label).toBe(statusMeta('ok').label)
  })
})

describe('every renderer consumes the reconciler, not the bare chain', () => {
  const read = (rel: string) => readFileSync(join(import.meta.dirname, '..', rel), 'utf8')

  it('the detail badge', () => {
    const s = read('schedule/ScheduleDetail.tsx')
    expect(s).toContain('triggerStatusMeta({')
    expect(s).not.toContain('statusMeta(job.last_run_status || job.last_status)')
  })

  it("the list's rows — ONE call, no per-kind branch", () => {
    // 🔴 The per-kind ternary WAS the second copy. Each branch chose a mapper, so a new kind chose a
    // vocabulary again — and the event branch chose the run-outcome mapper for a row that has no run
    // outcome, rendering a parked event trigger as "never run".
    const s = read('triggers/TriggersListPage.tsx')
    expect(s).toContain('triggerStatusMeta(t)')
    expect(s, 'no kind may pick its own mapper').not.toMatch(/kind === 'store'\s*\n?\s*\?\s*triggerHealthMeta/)
    expect(s, 'the raw wire pair must not reach a renderer').not.toContain('t.schedule.last_status')
  })

  it('the reconciler itself is the only owner of the precedence', () => {
    const s = read('schedule/scheduleMeta.ts')
    expect(s, 'the superseded reconciler is deleted, not kept alongside').not.toContain(
      'export function lastRunMeta',
    )
    expect(s).toContain('export function triggerStatusMeta')
  })
})
