import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import { replayLabel, replayRegressed, replayScore } from './learningMeta'
import { LearningPage } from './LearningPage'
import { ApiError } from '../../lib/api'
import type { LearningGate, LearningInbox, LearningReplay, LearningRow, StagingWeek } from '../../lib/api'

// ── EA-6: the local A/B replay harness's clause on the proposal card ──────────────────────────
//
// The harness replays real turns mined from the user's OWN captured sessions twice — once without
// the candidate skill/template, once with it — so the card answers what the Loop-2 gate cannot:
// would this have helped on my work?
//
// Three states have to stay distinguishable on screen, and none may collapse into another:
//
//   1. REPLAYED with a drop     → the two means, plus a chip, because that is the decision-changing
//      case. And Accept stays ENABLED, because this is evidence and not a veto.
//   2. REPLAYED but unscored    → "not measured". Never 0.000 — a candidate that genuinely scored
//      zero and a candidate nobody scored lead a reviewer to OPPOSITE decisions, so the two must
//      not be spelled the same way.
//   3. NOT REPLAYED             → "not replayed" plus the backend's own reason. Never blank, never a
//      chip, and never a reason to withhold Accept.
//
// Two rails per case, because either alone is satisfiable by dead code: the LABEL distinguishes the
// states, and `LearningPage` is what CALLS it. A suite that only tested `replayLabel` would stay
// green with the call deleted from the page — the same trap `gateColumns.test.tsx` records.
//
// The gate's clause lives beside this one and is deliberately NOT merged with it: a candidate can
// regress on the shipped scenario library and improve on the user's captured turns, and that
// disagreement is exactly what a reviewer most needs to see.

const GATE_UNGATED: LearningGate = {
  state: 'ungated',
  reason: 'no gate run yet — accept on the evidence above, or run the gate first',
  before: null, after: null, delta: null, regressed: false, scenarios: 0,
  halted: false, dollars_est: 0, spend_observed: false, pin: {}, ran_at: '',
}

const NOT_REPLAYED: LearningReplay = {
  state: 'unreplayed',
  reason: 'no learning replay budget is set, so a replay would have had no ceiling at all',
  verdict: 'unmeasured',
  candidate_mean: null, baseline_mean: null,
  cases: 0, scored: 0, rejected: 0, tool_free: 0,
  deferred: false, provenance: [], ran_at: '',
}

const replayed = (over: Partial<LearningReplay> = {}): LearningReplay => ({
  state: 'replayed', reason: '', verdict: 'regressed',
  baseline_mean: 4.2, candidate_mean: 1.5,
  cases: 3, scored: 3, rejected: 0, tool_free: 3,
  deferred: false,
  provenance: ['capture:sess-a#h1', 'capture:sess-a#h2', 'capture:sess-b#h3'],
  ran_at: '2026-08-27T10:00:00+00:00',
  ...over,
})

const row = (over: Partial<LearningRow> = {}): LearningRow => ({
  id: 'skill-deadbeef0001', kind: 'skill',
  title: 'Promote the retry-helper checklist',
  provenance: 'inferred', source_cadence: 'skill_promotion', source_excerpt: '',
  evidence_refs: ['run-1'], evidence_strength: 'correlated', reinforcements: 1, confidence: 0.4,
  manifest_valid: true, manifest_issues: [], risk_tier: 'review',
  status: 'pending', renderable: true, bulk_acceptable: true,
  gate: GATE_UNGATED,
  replay: NOT_REPLAYED,
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

describe('replayScore keeps an unmeasured number unmeasured', () => {
  it('renders null as the house string, not as zero', () => {
    expect(replayScore(null)).toBe('not measured')
    // The whole point, and the reason this is a separate assertion from the one above: `0` is a
    // real score — the strongest possible evidence a candidate made things worse — and must not be
    // spelled the same way as its absence.
    expect(replayScore(0)).toBe('0.000')
    expect(replayScore(0)).not.toBe(replayScore(null))
  })
})

describe('the replay clause names which of the three states a row is in', () => {
  it('reads the two means and what they mean, for a scored replay', () => {
    const label = replayLabel(row({ replay: replayed() }))
    expect(label).toContain('4.200 → 1.500')
    expect(label).toContain('made things worse on your captured turns')
    expect(label).toContain('3 replayed cases')
  })

  it('says "not measured" for a replay whose cases never scored', () => {
    const label = replayLabel(row({
      replay: replayed({
        verdict: 'unmeasured', baseline_mean: null, candidate_mean: null, scored: 0, rejected: 3,
      }),
    }))
    expect(label).toContain('not measured')
    expect(label).not.toContain('0.000')
    // The rejections get their own clause: they cost money and produced nothing, so folding them
    // into the headline count would overstate the evidence.
    expect(label).toContain('3 cases rejected')
  })

  it('says "not replayed" AND why, for a proposal nothing replayed', () => {
    const label = replayLabel(row())
    expect(label).toContain('not replayed')
    expect(label).toContain('no learning replay budget is set')
    expect(label).not.toContain('→')
  })

  it('names the deferral rather than going quiet on an exhausted budget', () => {
    expect(replayLabel(row({ replay: replayed({ deferred: true }) })))
      .toContain('deferred on the replay budget')
    expect(replayLabel(row({ replay: replayed() })))
      .not.toContain('deferred on the replay budget')
  })

  it('distinguishes improved from regressed from neutral', () => {
    const up = replayLabel(row({ replay: replayed({ verdict: 'improved', candidate_mean: 4.8, baseline_mean: 2.0 }) }))
    const flat = replayLabel(row({ replay: replayed({ verdict: 'neutral' }) }))
    const down = replayLabel(row({ replay: replayed() }))
    expect(up).toContain('improved on your captured turns')
    expect(flat).toContain('no measurable difference')
    expect(down).toContain('made things worse')
    expect(new Set([up, flat, down]).size).toBe(3)
  })

  it('falls back to the unmeasured wording for a verdict this build does not know', () => {
    // A card that renders a word this build cannot interpret is a card the reader fills in with a
    // guess. The numbers still show; the CLAUSE does not invent a meaning for them.
    const label = replayLabel(row({ replay: replayed({ verdict: 'wonderful' as never }) }))
    expect(label).toContain('4.200 → 1.500')
    expect(label).not.toContain('made things worse')
    expect(label).not.toContain('wonderful')
  })

  it('treats a MISSING replay object as not-replayed rather than crashing', () => {
    // An older cached row has no `replay` key at all. An absent measurement and an absent FIELD
    // mean the same thing to a reviewer, and neither is evidence that the candidate helped.
    const stale = row()
    delete (stale as Partial<LearningRow>).replay
    expect(replayLabel(stale as LearningRow)).toContain('not replayed')
    expect(replayRegressed(stale as LearningRow)).toBe(false)
  })
})

describe('replayRegressed fires on a MEASURED drop only', () => {
  it('is true for a regressed verdict', () => {
    expect(replayRegressed(row({ replay: replayed() }))).toBe(true)
  })

  it('is false for improved, neutral, unmeasured, and not-replayed', () => {
    expect(replayRegressed(row({ replay: replayed({ verdict: 'improved' }) }))).toBe(false)
    expect(replayRegressed(row({ replay: replayed({ verdict: 'neutral' }) }))).toBe(false)
    // The unmeasured one matters most: flagging it would be the same dishonesty as drawing an
    // unmeasured mean as 0, just pointing the other way.
    expect(replayRegressed(row({ replay: replayed({ verdict: 'unmeasured' }) }))).toBe(false)
    expect(replayRegressed(row())).toBe(false)
  })

  it('ignores a regressed verdict on an UNREPLAYED report', () => {
    // A contradictory record (nothing ran, yet the verdict says regressed) must not shout. The
    // state is the authority on whether a measurement exists at all.
    expect(replayRegressed(row({
      replay: { ...NOT_REPLAYED, verdict: 'regressed' },
    }))).toBe(false)
  })
})

describe('LearningPage RENDERS the replay clause (the call site)', () => {
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

  /** 🔑 THE RAIL THAT KEEPS THE CLAUSE ON SCREEN.
   *
   *  Deleting `replayLabel(row)` from `LearningPage` must turn this red. Every case above calls the
   *  helper directly and would survive that deletion untouched. */
  it('paints the A/B on the card a reviewer accepts from', async () => {
    // Vacuity floor: unsatisfiable before the page mounts, or it would pass with the call deleted.
    const control = render(<div />)
    expect(screen.queryByText(/4\.200 → 1\.500/)).toBeNull()
    control.unmount()

    learningProposals.mockResolvedValue(inboxOf([row({ replay: replayed() })]))
    render(<LearningPage />)

    expect(await screen.findByText(/4\.200 → 1\.500/)).toBeTruthy()
    expect(screen.getByText(/replay drop/)).toBeTruthy()
    // And it is on the row being decided, not a detached chip.
    expect(screen.getByText(/Promote the retry-helper checklist/)).toBeTruthy()
  })

  it('paints "not replayed" and NO chip for a proposal nothing replayed', async () => {
    learningProposals.mockResolvedValue(inboxOf([row()]))
    render(<LearningPage />)

    expect(await screen.findByText(/not replayed/)).toBeTruthy()
    // The absence must not be dressed as a warning: doing that trains reviewers to ignore the chip
    // that means something.
    expect(screen.queryByText(/replay drop/)).toBeNull()
  })

  /** 🔑 THE "NOT A GATE" RAIL, on the surface where it is decided.
   *
   *  The backend half is `test_a_regressed_verdict_still_accepts`. This is the other half: a card
   *  that greyed out Accept would enforce a veto the backend refuses to, and no Python test could
   *  see it. */
  it('leaves Accept enabled on a REGRESSED replay — it is evidence, not a lock', async () => {
    learningProposals.mockResolvedValue(inboxOf([row({ replay: replayed() })]))
    render(<LearningPage />)

    await screen.findByText(/replay drop/)
    const accept = screen.getByRole('button', { name: /Accept/ })
    expect(accept).toBeTruthy()
    expect(accept.hasAttribute('disabled')).toBe(false)
  })

  it('leaves Accept enabled on a not-replayed proposal too', async () => {
    learningProposals.mockResolvedValue(inboxOf([row()]))
    render(<LearningPage />)

    await screen.findByText(/not replayed/)
    expect(screen.getByRole('button', { name: /Accept/ }).hasAttribute('disabled')).toBe(false)
  })

  it('shows the gate and the replay as SEPARATE clauses when they disagree', async () => {
    // The case that justifies two clauses instead of one merged number: the shipped scenario
    // library says this improved, the user's own captured turns say it got worse. Collapsing them
    // would hide whichever one the merge happened to lose.
    learningProposals.mockResolvedValue(inboxOf([row({
      gate: {
        ...GATE_UNGATED, state: 'gated', reason: '',
        before: 0.4, after: 0.9, delta: 0.5, scenarios: 12,
      },
      replay: replayed(),
    })]))
    render(<LearningPage />)

    expect(await screen.findByText(/0\.400 → 0\.900/)).toBeTruthy()
    expect(screen.getByText(/4\.200 → 1\.500/)).toBeTruthy()
    // Only the replay regressed, so only the replay chip appears.
    expect(screen.getByText(/replay drop/)).toBeTruthy()
    expect(screen.queryByText(/score drop/)).toBeNull()
  })
})
