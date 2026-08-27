import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import { evidenceLabel } from './learningMeta'
import { LearningPage } from './LearningPage'
import { ApiError } from '../../lib/api'
import type { LearningInbox, LearningRow, StagingWeek } from '../../lib/api'

// ── ES-7 §3.1: the evidence TIER is read, not merely stamped ──────────────────
//
// `file_retirement_proposal` files a LEARN-R9 retirement whose evidence is a paired on/off ablation
// and stamps `evidence_strength: "ablation"` to say which KIND of claim it is. Measured before this
// suite: the tier had nine `enqueue` call sites across eight modules and ZERO readers anywhere — no
// gate, no inbox projection, no API payload, no frontend. So the row a human decides a RETIREMENT
// on read `1 evidence ref(s)` whether the null result was measured or merely co-occurred, and the
// deleted stamp would have reddened nothing but an assertion on the returned object.
//
// Two rails, because either alone is satisfiable by dead code: the LABEL distinguishes the tiers,
// and `LearningPage` is what CALLS it. A suite that only tested `evidenceLabel` would stay green
// with the call deleted from the page — which is exactly the state the field was already in.

const row = (over: Partial<LearningRow> = {}): LearningRow => ({
  id: 'ablation.correction-heuristic', kind: 'retirement',
  title: 'Retire correction-heuristic — ablation measured no delta',
  provenance: 'inferred', source_cadence: 'ablation', source_excerpt: '',
  evidence_refs: ['ablation:ablation-20260817T120000Z', 'matrix:ablation-20260817T120000Z'],
  evidence_strength: 'ablation', reinforcements: 3, confidence: 0.7,
  manifest_valid: true, manifest_issues: [], risk_tier: 'review',
  status: 'pending', renderable: true, bulk_acceptable: true,
  ...over,
})

const inboxOf = (rows: LearningRow[]): LearningInbox => ({
  rows, total: rows.length, by_kind: {}, by_tier: {},
  flagged: 0, unrenderable: [], bulk_acceptable: rows.length,
})

const WEEK: StagingWeek = {
  days: 7, buckets: [], silent_days: [], error_days: [], produced_total: 0, cost_usd: 0,
}

const learningProposals = vi.fn<() => Promise<LearningInbox>>()
const learningStagingWeek = vi.fn<() => Promise<StagingWeek>>()
const learningHealth = vi.fn<() => Promise<never>>()
const judgeBench = vi.fn<() => Promise<never>>()
const evalStudies = vi.fn<() => Promise<never>>()
const retrievalBench = vi.fn<() => Promise<never>>()
const ablation = vi.fn<() => Promise<never>>()

// 🪤 PARTIAL mock, via `importOriginal`: the REAL `ApiError`/`hasApiCode` are kept. The five eval
// panels branch on `hasApiCode(error, '<code>')`, so a factory that returned only `api` made the
// mocked module throw "No \"hasApiCode\" export is defined" from inside the render — and a fixture
// that rejected with a bare `Error` would carry no `.code`, so the branch under test would never
// fire and the test would pass by rendering the generic failure instead.
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      learningProposals: () => learningProposals(),
      learningStagingWeek: () => learningStagingWeek(),
      learningHealth: () => learningHealth(),
      acceptLearningProposal: () => Promise.resolve({ ok: true }),
      rejectLearningProposal: () => Promise.resolve(undefined),
      judgeBench: () => judgeBench(),
      evalStudies: () => evalStudies(),
      retrievalBench: () => retrievalBench(),
      ablation: () => ablation(),
      // LV-4's identity report is fetched by the same LearningPage this file renders. A partial
      // `api` mock does not fail on the missing key — it throws `api.identityReport is not a
      // function` from inside the render, so BOTH call-site tests below died before asserting
      // anything. Neither PR could see it alone: LV-4 added the call, this file mocks only its own
      // reads, and the break exists only in the union. Rejecting is the honest stub — the page must
      // paint the ablation grade whether or not the identity report resolves, and if it ever grows a
      // dependency on that payload these tests should say so rather than silently pass.
      identityReport: () => Promise.reject(new Error('not under test')),
      // LV-7's skill-impact benchmark, and the SECOND instance of the paragraph above — same
      // shape, one PR later: LV-7 added the read to `LearningPage`, this file mocks only its own,
      // and `api.learningBenchmark is not a function` killed both call-site tests in the union
      // alone. Rejecting with the real `ApiError` code rather than a bare `Error`, because
      // `BenchmarkPanel` branches on `hasApiCode` and a message-only double would quietly render
      // the generic failure instead of the never-run empty state.
      learningBenchmark: () => Promise.reject(
        new ApiError('No skill-impact benchmark has run yet.', 404, 'learning_benchmark_absent'),
      ),
    },
  }
})

describe('the evidence clause names the tier, not only the count', () => {
  it('distinguishes a measured ablation from a co-occurrence', () => {
    const measured = evidenceLabel(row())
    const correlated = evidenceLabel(row({ evidence_strength: 'correlated' }))
    expect(measured).toContain('measured on/off')
    // VACUITY FLOOR: both rows carry the SAME two refs, so a label built from the count alone
    // would make these two strings identical — which is the defect this closes.
    expect(measured).not.toBe(correlated)
    expect(correlated).toContain('correlated')
    expect(measured).toContain('2 evidence ref(s)')
  })

  it('names a controlled study and an anecdote as themselves', () => {
    expect(evidenceLabel(row({ evidence_strength: 'causal' }))).toContain('controlled study')
    expect(evidenceLabel(row({ evidence_strength: 'anecdotal' }))).toContain('anecdotal')
  })

  it('reads an UNGRADED tier as ungraded, never as a grade', () => {
    // "" is a record filed before the tier existed. Falling back to `correlated` would turn
    // "nobody said" into a claim — the same failure as drawing an unmeasured mean as 0.000.
    expect(evidenceLabel(row({ evidence_strength: '' }))).toContain('ungraded')
    expect(evidenceLabel(row({ evidence_strength: 'invented_tier' }))).toContain('ungraded')
    expect(evidenceLabel(row({ evidence_strength: '' }))).not.toContain('correlated')
  })

  it('says so when there is nothing to check', () => {
    // No refs is a stronger statement than a weak grade, so it wins the clause outright.
    expect(evidenceLabel(row({ evidence_refs: [], evidence_strength: 'ablation' })))
      .toBe('no evidence')
  })
})

describe('LearningPage RENDERS the grade (the call site)', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
    vi.clearAllMocks()
    learningStagingWeek.mockResolvedValue(WEEK)
    // Each of the side panels rejects with its own ORDINARY absent code: they own their own
    // rendering, and this suite's subject is the proposal row.
    learningHealth.mockRejectedValue(new Error('not under test'))
    judgeBench.mockRejectedValue(new ApiError('No judge benchmark has run yet. Run `personalclaw judge-bench` to produce one.', 404, 'judge_bench_absent'))
    evalStudies.mockRejectedValue(new ApiError('No study is registered under that id.', 404, 'study_absent'))
    retrievalBench.mockRejectedValue(new ApiError('No retrieval benchmark has run yet. Run `personalclaw retrieval-eval` to score both stores.', 404, 'retrieval_absent'))
    ablation.mockRejectedValue(new ApiError('No ablation has run yet. Register a component in `evals/ablation_registry.json` and run `personalclaw ablation --force`.', 404, 'ablation_absent'))
  })

  /** 🔑 THE RAIL THAT KEEPS THE TIER READ.
   *
   *  Deleting `evidenceLabel(row)` from `LearningPage` must turn this red. Every case above
   *  calls the helper directly and would survive that deletion untouched. */
  it('paints the ablation grade on the row a reviewer decides on', async () => {
    // Vacuity floor: the matcher must be unsatisfiable before the page mounts, or it would
    // pass with the call deleted and be measuring itself.
    const control = render(<div />)
    expect(screen.queryByText(/measured on\/off/)).toBeNull()
    control.unmount()

    learningProposals.mockResolvedValue(inboxOf([row()]))
    render(<LearningPage />)

    expect(await screen.findByText(/measured on\/off/)).toBeTruthy()
    // And the retirement it grades is the row on screen, not a detached chip.
    expect(screen.getByText(/Retire correction-heuristic/)).toBeTruthy()
  })

  it('does not upgrade an ungraded row on the page either', async () => {
    learningProposals.mockResolvedValue(inboxOf([row({ evidence_strength: '' })]))
    render(<LearningPage />)

    expect(await screen.findByText(/ungraded/)).toBeTruthy()
    expect(screen.queryByText(/measured on\/off/)).toBeNull()
  })
})
