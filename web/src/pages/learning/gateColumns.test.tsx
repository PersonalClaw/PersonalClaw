import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import { gateLabel, gateRegressed, gateScore } from './learningMeta'
import { LearningPage } from './LearningPage'
import { ApiError } from '../../lib/api'
import type { LearningGate, LearningInbox, LearningRow, StagingWeek } from '../../lib/api'

// ── ES-6 / amendment E2: the Loop-2 gate's before/after columns on the proposal card ──────────
//
// The atom's own proof is a planted regression: a candidate skill edit that makes things worse must
// show a SCORE DROP on its own card, before the user accepts. That is a backend measurement, and
// this file is the half that decides whether a reviewer ever sees it.
//
// Three states have to stay distinguishable on screen, and none may collapse into another:
//
//   1. GATED with a drop    → the numbers, plus a chip, because that is the decision-changing case;
//   2. GATED but unmeasured → "not measured", the string every eval panel already uses for a null
//      mean. Never 0.000 — an unmeasured arm drawn as zero reads as a total failure;
//   3. UNGATED              → "ungated" plus the backend's own reason. Never blank, never a chip,
//      and never a reason to withhold Accept: a gate that blocked on its own absence would stop a
//      user shipping a change because the GATE broke.
//
// Two rails per case, because either alone is satisfiable by dead code: the LABEL distinguishes the
// states, and `LearningPage` is what CALLS it. A suite that only tested `gateLabel` would stay green
// with the call deleted from the page.

const GATE_UNGATED: LearningGate = {
  state: 'ungated',
  reason: 'no gate run yet — accept on the evidence above, or run the gate first',
  before: null, after: null, delta: null, regressed: false, scenarios: 0,
  halted: false, dollars_est: 0, spend_observed: false, pin: {}, ran_at: '',
}

const gated = (over: Partial<LearningGate> = {}): LearningGate => ({
  state: 'gated', reason: '',
  before: 0.9, after: 0.4, delta: -0.5, regressed: true, scenarios: 12,
  halted: false, dollars_est: 0.031, spend_observed: true,
  pin: { model_fp: 'abc123def456', scenario_sha256: 'f00dcafe' },
  ran_at: '2026-08-27T10:00:00+00:00',
  ...over,
})

const row = (over: Partial<LearningRow> = {}): LearningRow => ({
  id: 'skill-deadbeef0001', kind: 'skill',
  title: 'Promote the release checklist we re-derived three times',
  provenance: 'inferred', source_cadence: 'skill_promotion', source_excerpt: '',
  evidence_refs: ['run-1'], evidence_strength: 'correlated', reinforcements: 1, confidence: 0.4,
  manifest_valid: true, manifest_issues: [], risk_tier: 'review',
  status: 'pending', renderable: true, bulk_acceptable: true,
  gate: GATE_UNGATED,
  replay: {
    state: 'unreplayed', reason: 'no replay run yet', verdict: 'unmeasured',
    candidate_mean: null, baseline_mean: null, cases: 0, scored: 0, rejected: 0, tool_free: 0,
    deferred: false, provenance: [], ran_at: '',
  },
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
const acceptLearningProposal = vi.fn((_id: string) => Promise.resolve({ ok: true }))

// PARTIAL mock via `importOriginal`, for the reason `evidenceGrade.test.tsx` records: the five side
// panels branch on the REAL `hasApiCode`, so a factory returning only `api` throws from inside the
// render and every assertion below would die before it ran.
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      learningProposals: () => learningProposals(),
      learningStagingWeek: () => learningStagingWeek(),
      learningHealth: () => learningHealth(),
      acceptLearningProposal: (id: string) => acceptLearningProposal(id),
      rejectLearningProposal: () => Promise.resolve(undefined),
      judgeBench: () => judgeBench(),
      evalStudies: () => evalStudies(),
      retrievalBench: () => retrievalBench(),
      ablation: () => ablation(),
      identityReport: () => Promise.reject(new Error('not under test')),
      learningBenchmark: () => Promise.reject(
        new ApiError('No skill-impact benchmark has run yet.', 404, 'learning_benchmark_absent'),
      ),
    },
  }
})

describe('gateScore keeps an unmeasured number unmeasured', () => {
  it('renders null as the house string, not as zero', () => {
    expect(gateScore(null)).toBe('not measured')
    // The whole point: `0` is a real score and must not be spelled the same way as its absence.
    expect(gateScore(0)).toBe('0.000')
    expect(gateScore(0)).not.toBe(gateScore(null))
  })
})

describe('the gate clause names which of the three states a row is in', () => {
  it('shows before → after and the signed delta for a measured pair', () => {
    const label = gateLabel(row({ gate: gated() }))
    expect(label).toContain('0.900 → 0.400')
    expect(label).toContain('-0.500')
    // 🔁 Was `12 gate scenario(s)`.
    expect(label).toContain('12 gate scenarios')
  })

  it('signs an IMPROVEMENT so a rise cannot be misread as a drop', () => {
    const up = gateLabel(row({ gate: gated({ before: 0.4, after: 0.9, delta: 0.5, regressed: false }) }))
    expect(up).toContain('+0.500')
    // VACUITY FLOOR: the two directions must not render identically, which is what a bare
    // magnitude would do.
    expect(up).not.toBe(gateLabel(row({ gate: gated() })))
  })

  it('says "not measured" for a gated run whose arms never scored', () => {
    const label = gateLabel(row({ gate: gated({ before: null, after: null, delta: null, regressed: false }) }))
    expect(label).toContain('not measured')
    // Never a zero: an unmeasured arm drawn as 0.000 reads as "it failed everything".
    expect(label).not.toContain('0.000')
  })

  it('says "ungated" AND why, for a proposal no gate ran on', () => {
    const label = gateLabel(row())
    expect(label).toContain('ungated')
    expect(label).toContain('accept on the evidence above')
    // The absence is not a score, so it must not carry one.
    expect(label).not.toContain('0.000')
    expect(label).not.toContain('→')
  })

  it('treats a MISSING gate object as ungated rather than crashing', () => {
    // An older cached row has no `gate` key at all. An absent measurement and an absent FIELD mean
    // the same thing to a reviewer, and neither is evidence of a passing gate.
    const stale = { ...row() } as Partial<LearningRow>
    delete stale.gate
    expect(gateLabel(stale as LearningRow)).toContain('ungated')
    expect(gateRegressed(stale as LearningRow)).toBe(false)
  })

  it('reports the budget halt, because a partial sweep is a weaker claim', () => {
    expect(gateLabel(row({ gate: gated({ halted: true }) }))).toContain('stopped early on the eval budget')
    expect(gateLabel(row({ gate: gated() }))).not.toContain('stopped early')
  })
})

describe('gateRegressed fires on a MEASURED drop only', () => {
  it('is true for a measured drop', () => {
    expect(gateRegressed(row({ gate: gated() }))).toBe(true)
  })

  it('is false for a rise, a tie, an unmeasured pair, and an ungated row', () => {
    expect(gateRegressed(row({ gate: gated({ delta: 0.5 }) }))).toBe(false)
    expect(gateRegressed(row({ gate: gated({ delta: 0 }) }))).toBe(false)
    // Unmeasured is the one that matters: flagging it would be the same dishonesty as drawing an
    // unmeasured mean as 0, just pointing the other way.
    expect(gateRegressed(row({ gate: gated({ delta: null }) }))).toBe(false)
    expect(gateRegressed(row())).toBe(false)
  })
})

describe('LearningPage RENDERS the columns (the call site)', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
    vi.clearAllMocks()
    learningStagingWeek.mockResolvedValue(WEEK)
    learningHealth.mockRejectedValue(new Error('not under test'))
    judgeBench.mockRejectedValue(new ApiError('No judge benchmark has run yet.', 404, 'judge_bench_absent'))
    evalStudies.mockRejectedValue(new ApiError('No study is registered under that id.', 404, 'study_absent'))
    retrievalBench.mockRejectedValue(new ApiError('No retrieval benchmark has run yet.', 404, 'retrieval_absent'))
    ablation.mockRejectedValue(new ApiError('No ablation has run yet.', 404, 'ablation_absent'))
  })

  /** 🔑 THE RAIL THAT KEEPS THE COLUMNS ON SCREEN.
   *
   *  Deleting `gateLabel(row)` from `LearningPage` must turn this red. Every case above calls the
   *  helper directly and would survive that deletion untouched. */
  it('paints the planted regression on the card a reviewer accepts from', async () => {
    // Vacuity floor: unsatisfiable before the page mounts, or it would pass with the call deleted.
    const control = render(<div />)
    expect(screen.queryByText(/0\.900 → 0\.400/)).toBeNull()
    control.unmount()

    learningProposals.mockResolvedValue(inboxOf([row({ gate: gated() })]))
    render(<LearningPage />)

    expect(await screen.findByText(/0\.900 → 0\.400/)).toBeTruthy()
    // A measured drop earns a chip — the one gate outcome that changes the decision.
    expect(screen.getByText(/score drop/)).toBeTruthy()
    // And it is on the row being decided, not a detached chip.
    expect(screen.getByText(/Promote the release checklist/)).toBeTruthy()
  })

  it('paints "ungated" and NO chip for a proposal no gate ran on', async () => {
    learningProposals.mockResolvedValue(inboxOf([row()]))
    render(<LearningPage />)

    expect(await screen.findByText(/ungated/)).toBeTruthy()
    // The absence must not be dressed as a warning: doing that trains reviewers to ignore the chip
    // that means something.
    expect(screen.queryByText(/score drop/)).toBeNull()
    expect(screen.queryByText(/→/)).toBeNull()
  })

  it('leaves Accept enabled on an ungated proposal — the gate never blocks', async () => {
    learningProposals.mockResolvedValue(inboxOf([row()]))
    render(<LearningPage />)

    await screen.findByText(/ungated/)
    const accept = screen.getByRole('button', { name: /Accept/ })
    expect(accept).toBeTruthy()
    expect(accept.hasAttribute('disabled')).toBe(false)
  })

  it('leaves Accept enabled on a REGRESSED proposal too — it is evidence, not a lock', async () => {
    // The user may know something the twelve scenarios do not. The columns inform the decision;
    // they do not take it.
    learningProposals.mockResolvedValue(inboxOf([row({ gate: gated() })]))
    render(<LearningPage />)

    await screen.findByText(/score drop/)
    const accept = screen.getByRole('button', { name: /Accept/ })
    expect(accept.hasAttribute('disabled')).toBe(false)
  })
})
