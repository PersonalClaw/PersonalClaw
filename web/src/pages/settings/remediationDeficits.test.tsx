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
// fix them was a no-op — because run_remediation() stops the moment score >= target. Every fact
// needed to understand that was in the payload and none of it was on screen. A score without its
// breakdown cannot distinguish "nothing is wrong" from "nothing the engine will act on".
//
// `reachable` decides what Run now touches — an unreachable deficit (missing embeddings with no
// embedder bound, a failed Doctor check no job repairs) renders greyed and names its own next
// step. It is NOT left out of the score any more (settings B16): health_score() used to sum
// reachable deficits only, which is how "Health score 100" sat under two failed Doctor checks. So
// every row carries the penalty it subtracts, and the column adds up to 100 minus the score.

const PANEL = join(process.cwd(), 'src/pages/settings/DoctorPanel.tsx')

const BLOCKER = 'no embedding model is bound — pick one in Settings → Models'

const DEFICITS = [
  { key: 'knowledge_missing_embeddings', count: 26, penalty: 13.0, reachable: false, blocked_by: BLOCKER },
  { key: 'orphan_locks', count: 26, penalty: 10.0, reachable: true, blocked_by: '' },
  { key: 'skill_aging_due', count: 0, penalty: 0.0, reachable: true, blocked_by: '' },
]

const snapshot = (over: Record<string, unknown> = {}) => ({
  score: 90, target_score: 90, deficits: DEFICITS, plan: [], recent_runs: [], ...over,
})

async function mount(over: Record<string, unknown> = {}) {
  vi.resetModules()
  vi.doMock('../../lib/api', async () => ({
    // The real module under the stubbed `api`: the section imports `isSwitchedOff` from it too.
    ...(await vi.importActual<typeof import('../../lib/api')>('../../lib/api')),
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
  it('shows the penalty every row subtracts, reachable or not', async () => {
    const text = (await mount()).container.textContent ?? ''
    expect(text).toContain('−10.0')          // reachable → subtracted, and Run now can win it back
    expect(text).toContain('−13.0')          // unreachable → subtracted too: the home is no healthier
  })

  it('labels a failed Doctor check by its title and names its next step', async () => {
    // A failed check reaches the score as a `check:<probe id>` deficit carrying the probe's title
    // and its remedy — the same sentence its Doctor row shows.
    const remedy = 'No automatic fix — copy the paths listed in this row\'s details somewhere safe.'
    const text = (await mount({
      score: 85,
      deficits: [{
        key: 'check:durability.inventory', title: 'Every state path is claimed by the manifest',
        count: 1, penalty: 15, reachable: false, blocked_by: remedy,
      }],
    })).container.textContent ?? ''
    expect(text).toContain('Every state path is claimed by the manifest')
    expect(text).not.toContain('Check:durability')
    expect(text).toContain(remedy)
    expect(text).toContain('−15.0')
    expect(text).toContain('nothing measured above is fixable by maintenance')
  })

  it('names WHY an unreachable deficit will not clear, not just that it will not', async () => {
    // 🔴 THIS ROW USED TO READ `· not fixable yet`, AND THAT WAS THE WHOLE DEFECT'S SECOND HALF.
    // Measured live on a seeded home: `GET /api/doctor/remediation` returned score 100.0 with
    // `knowledge_missing_embeddings ×25 penalty 12.5 reachable:false`, and the panel rendered
    //
    //     Health score 100 / target 90
    //     Knowledge missing embeddings ×25 · not fixable yet   —
    //     Nothing to do — no fixable deficits.
    //
    // The arithmetic is correct (an unreachable penalty is excluded — no run can improve it), but
    // "yet" promises a later pass, and no pass will ever reach this: what is missing is an
    // embedding model. The reason was computed one line from where `reachable` was and thrown
    // away. `blocked_by` carries it, produced once in core so the CLI prints the same sentence.
    const text = (await mount()).container.textContent ?? ''
    expect(text).toContain(BLOCKER)
    expect(text, 'the reason replaces the dead end, it does not sit beside it').not.toContain('not fixable yet')
  })

  it('does not mark a reachable one', async () => {
    const { container } = await mount({
      deficits: [{ key: 'orphan_locks', count: 3, penalty: 6, reachable: true, blocked_by: '' }],
    })
    expect(container.textContent).not.toContain('·  ')
    expect(container.textContent).toContain('−6.0')
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

  it('does not promise a job that is cooling down', async () => {
    // The dry-run plan lists a job inside its cooldown as `skipped_cooldown`: it will not run.
    const { container } = await mount({
      score: 60,
      plan: [{ id: 'memory.rebuild-faiss-index', status: 'skipped_cooldown', cost: 0 }],
    })
    expect(container.textContent).toContain('Memory.rebuild faiss index (cooling down — not yet)')
  })

  it('says WHY an empty plan is empty when fixable deficits remain', async () => {
    // The contradiction this cycle found: a nonzero fixable deficit list next to a no-op button.
    // Silence here reads as a bug; the reason is the whole point.
    const text = (await mount()).container.textContent ?? ''
    expect(text).toContain('already meets its target')
  })

  it('distinguishes "nothing fixable" from "nothing measured"', async () => {
    // 🔴 ONE LINE SERVED TWO OPPOSITE STATES. `Nothing to do — no fixable deficits.` rendered
    // directly beneath `Knowledge missing embeddings ×25` reads as "no deficits" to anyone who
    // does not stop on the word "fixable" — which is exactly the reading the green 100 above it
    // already invites. An empty deficit list and a list of unfixable ones are different facts.
    const blocked = await mount({
      score: 85,
      deficits: [{ key: 'knowledge_missing_embeddings', count: 30, penalty: 15, reachable: false, blocked_by: BLOCKER }],
    })
    expect(blocked.container.textContent).toContain('nothing measured above is fixable by maintenance')

    const clean = await mount({ deficits: [] })
    expect(clean.container.textContent).toContain('no deficits measured')
  })
})

describe('the run ledger says when, and whether it worked', () => {
  // It read `score 88→100 · 1 job · target_score reached`. `ts` and every `jobs[].status` were in
  // the payload and unread, so a pass whose every job THREW rendered identically to one that did
  // the work — and a silently failing maintenance job is the one thing this list exists to catch.
  const RUN = (over: Record<string, unknown> = {}) => ({
    ts: Math.floor(Date.now() / 1000) - 7200,
    score_before: 76, score_after: 88, stopped_reason: 'plan exhausted',
    jobs: [{ id: 'serving-fs.prune-orphans', status: 'ok', cost: 0, detail: 'Removed 4 stale locks' }],
    ...over,
  })

  it('stamps each run with when it happened', async () => {
    const text = (await mount({ recent_runs: [RUN()] })).container.textContent ?? ''
    expect(text).toContain('2h ago')
  })

  it('counts the jobs that FAILED — and a cooldown skip is not one', async () => {
    // Measured live: a second Run now inside the 6h window returned
    // `jobs: [{id: 'serving-fs.prune-orphans', status: 'skipped_cooldown'}]`. The storm guard
    // doing its job must not read as a failure, so the count is `=== 'error'`, not `!== 'ok'`.
    const skipped = (await mount({
      recent_runs: [RUN({ jobs: [{ id: 'serving-fs.prune-orphans', status: 'skipped_cooldown', cost: 0 }] })],
    })).container.textContent ?? ''
    expect(skipped).not.toContain('failed')
    expect(skipped).toContain('Serving fs.prune orphans — skipped_cooldown')
  })

  it('counts the jobs that did not succeed', async () => {
    const text = (await mount({
      recent_runs: [RUN({
        jobs: [
          { id: 'serving-fs.prune-orphans', status: 'ok', cost: 0, detail: 'Removed 4 stale locks' },
          { id: 'memory.rebuild-fts', status: 'error', cost: 0, error: 'database is locked' },
        ],
      })],
    })).container.textContent ?? ''
    expect(text).toContain('2 jobs')
    expect(text).toContain('1 failed')
  })

  it('names each outcome of the newest pass, so a failure carries its reason', async () => {
    const text = (await mount({
      recent_runs: [RUN({
        jobs: [{ id: 'memory.rebuild-fts', status: 'error', cost: 0, error: 'database is locked' }],
      })],
    })).container.textContent ?? ''
    expect(text).toContain('Memory.rebuild fts — error: database is locked')
  })

  it('expands only the newest pass, so five rows do not bury the score', async () => {
    const text = (await mount({
      recent_runs: [
        RUN({ jobs: [{ id: 'sel.prune', status: 'ok', cost: 0, detail: 'newest detail' }] }),
        RUN({ jobs: [{ id: 'skills.age', status: 'ok', cost: 0, detail: 'older detail' }] }),
      ],
    })).container.textContent ?? ''
    expect(text).toContain('newest detail')
    expect(text).not.toContain('older detail')
  })
})

describe('the Run-now toast level is derived from the result', () => {
  // 🔴 PROVEN LIVE, not read off the code path: `POST /api/doctor/remediation/run` on the seeded
  // home returned `{score_before: 100, score_after: 100, jobs: [], stopped_reason: "target_score
  // already met"}`, and the panel raised a GREEN success toast reading "score 100→100
  // (target_score already met)". Nothing ran, the 25-item backlog was untouched, and the only
  // feedback was the colour that means "done". A run whose jobs all threw took the same green.
  async function runAndCapture(result: Record<string, unknown>) {
    vi.resetModules()
    const calls: Array<[string, string | undefined]> = []
    vi.doMock('../../app/appSdk', () => ({
      notify: (m: string, l?: string) => { calls.push([m, l]) },
    }))
    vi.doMock('../../ui/dialog', () => ({ confirm: () => Promise.resolve(true) }))
    vi.doMock('../../lib/api', async () => ({
      ...(await vi.importActual<typeof import('../../lib/api')>('../../lib/api')),
      api: {
        doctorRemediation: () => Promise.resolve(snapshot()),
        doctorRemediationRun: () => Promise.resolve(result),
      },
    }))
    const { RemediationSection } = await import('./DoctorPanel')
    let r!: ReturnType<typeof render>
    await act(async () => { r = render(<RemediationSection />); await new Promise((res) => setTimeout(res, 0)) })
    const btn = [...r.container.querySelectorAll('button')].find((b) => /Run now/.test(b.textContent ?? ''))
    await act(async () => { btn?.click(); await new Promise((res) => setTimeout(res, 0)) })
    return calls
  }

  it('a run that did nothing is information, not success', async () => {
    const calls = await runAndCapture({ score_before: 100, score_after: 100, jobs: [], stopped_reason: 'target_score already met' })
    expect(calls).toHaveLength(1)
    expect(calls[0][1]).toBe('info')
    expect(calls[0][0]).toContain('changed nothing')
    expect(calls[0][0]).toContain('target_score already met')
  })

  it('an all-skipped run is information too, not a failure', async () => {
    // The live second press: the cooldown guard skipped the only planned job.
    const calls = await runAndCapture({
      score_before: 88, score_after: 88, stopped_reason: 'plan exhausted',
      jobs: [{ id: 'serving-fs.prune-orphans', status: 'skipped_cooldown', cost: 0 }],
    })
    expect(calls[0][1]).toBe('info')
    expect(calls[0][0]).toContain('changed nothing')
  })

  it('a run whose job failed is an error, and says so', async () => {
    const calls = await runAndCapture({
      score_before: 76, score_after: 76, stopped_reason: 'plan exhausted',
      jobs: [{ id: 'memory.rebuild-fts', status: 'error', cost: 0, error: 'database is locked' }],
    })
    expect(calls[0][1]).toBe('error')
    expect(calls[0][0]).toContain('1 failed')
  })

  it('a run that actually did the work is the only success', async () => {
    const calls = await runAndCapture({
      score_before: 76, score_after: 100, stopped_reason: 'target_score reached',
      jobs: [{ id: 'serving-fs.prune-orphans', status: 'ok', cost: 0, detail: 'Removed 4 stale locks' }],
    })
    expect(calls[0][1]).toBe('success')
    expect(calls[0][0]).toContain('1 ok')
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
