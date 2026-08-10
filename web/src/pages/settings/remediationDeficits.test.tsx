import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The measured breakdown behind the health score ─────────────────────────────
//
// `/api/doctor/remediation` returns {score, target_score, deficits, plan, recent_runs}.
// RemediationSection read score, target_score and recent_runs — and dropped BOTH `deficits` (the
// engine's own measured input) and `plan` (the dry-run preview of what "Run now" would do).
//
// Why that mattered, measured on the validation home:
//
//   score 90 / target 90        rendered in SUCCESS GREEN
//   deficits                    knowledge_missing_embeddings ×26  penalty 13.0  reachable:false
//                               orphan_locks                 ×26  penalty 10.0  reachable:TRUE
//                               skill_aging_due              ×0   penalty  0.0
//   plan                        []   ("target_score already met")
//
// So the panel said "healthy" while 26 orphan locks sat there fixable, and the button that would
// fix them was a no-op — because health_score() sums penalties over REACHABLE deficits only, and
// run_remediation() stops the moment score >= target. Every fact needed to understand that was in
// the payload and none of it was on screen. A score without its breakdown cannot distinguish
// "nothing is wrong" from "nothing the engine will act on".
//
// `reachable` is the load-bearing field: an unreachable deficit is at its floor (missing
// embeddings with no embedder bound), is NOT subtracted from the score, and pressing Run now will
// not clear it — so it renders greyed, marked, and with no penalty number, because showing one
// would misattribute the score above it.

const PANEL = join(process.cwd(), 'src/pages/settings/DoctorPanel.tsx')

const DEFICITS = [
  { key: 'knowledge_missing_embeddings', count: 26, penalty: 13.0, reachable: false },
  { key: 'orphan_locks', count: 26, penalty: 10.0, reachable: true },
  { key: 'skill_aging_due', count: 0, penalty: 0.0, reachable: true },
]

const snapshot = (over: Record<string, unknown> = {}) => ({
  score: 90, target_score: 90, deficits: DEFICITS, plan: [], recent_runs: [], ...over,
})

async function mount(over: Record<string, unknown> = {}) {
  vi.resetModules()
  vi.doMock('../../lib/api', () => ({
    api: {
      doctorRemediation: () => Promise.resolve(snapshot(over)),
      doctorRemediationRun: () => Promise.resolve({}),
      doctor: () => Promise.resolve(null),
    },
  }))
  const { RemediationSection } = await import('./DoctorPanel')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<RemediationSection />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

beforeEach(() => { vi.resetModules() })

describe('deficits reach the panel', () => {
  it('lists each measured deficit with its count', async () => {
    const text = (await mount()).container.textContent ?? ''
    expect(text).toContain('Knowledge missing embeddings')
    expect(text).toContain('Orphan locks')
    expect(text).toContain('×26')
  })

  it('hides a deficit measured at zero', async () => {
    // measure_deficits() reports every readable source, including ones at 0. Those are
    // measurements, not problems — listing them buries the real ones.
    const text = (await mount()).container.textContent ?? ''
    expect(text).not.toContain('Skill aging due')
  })

  it('orders reachable deficits first, then by penalty', async () => {
    const text = (await mount()).container.textContent ?? ''
    // orphan_locks (reachable, −10) must precede the unreachable 13-point one: the actionable
    // row is the one the user can do something about, regardless of which scores worse.
    expect(text.indexOf('Orphan locks')).toBeLessThan(text.indexOf('Knowledge missing embeddings'))
  })
})

describe('reachable vs unreachable is not flattened', () => {
  it('shows a penalty only for a deficit that actually counts against the score', async () => {
    const text = (await mount()).container.textContent ?? ''
    expect(text).toContain('−10.0')          // reachable → subtracted
    expect(text).not.toContain('−13.0')      // unreachable → at its floor, NOT subtracted
  })

  it('marks an unreachable deficit so Run now is not expected to clear it', async () => {
    expect((await mount()).container.textContent).toContain('not fixable yet')
  })

  it('does not mark a reachable one', async () => {
    const { container } = await mount({
      deficits: [{ key: 'orphan_locks', count: 3, penalty: 6, reachable: true }],
    })
    expect(container.textContent).not.toContain('not fixable yet')
  })
})

describe('the plan explains what Run now would do', () => {
  it('names the jobs when the engine would act', async () => {
    const { container } = await mount({
      score: 60,
      plan: [{ id: 'serving-fs.prune-orphans', status: 'planned', cost: 0 }],
    })
    expect(container.textContent).toContain('Run now would')
    expect(container.textContent).toContain('Serving fs.prune orphans')
  })

  it('says WHY an empty plan is empty when fixable deficits remain', async () => {
    // The contradiction this cycle found: a nonzero fixable deficit list next to a no-op button.
    // Silence here reads as a bug; the reason is the whole point.
    const text = (await mount()).container.textContent ?? ''
    expect(text).toContain('already meets its target')
  })

  it('says nothing-to-do when there is genuinely nothing fixable', async () => {
    const { container } = await mount({
      deficits: [{ key: 'knowledge_missing_embeddings', count: 4, penalty: 2, reachable: false }],
    })
    expect(container.textContent).toContain('Nothing to do')
  })
})

describe('the label helper is shared, not duplicated', () => {
  it('capLabel handles snake_case as well as kebab/slash', () => {
    // Deficit keys are snake_case; capability keys are kebab/slash. A second near-identical
    // helper beside the first is exactly the drift this session converges.
    const src = readFileSync(PANEL, 'utf8')
    expect(src).toMatch(/key\.replace\(\/\[-\/_\]\/g, ' '\)/)
    expect((src.match(/function capLabel/g) ?? []).length, 'only one capLabel').toBe(1)
  })
})
