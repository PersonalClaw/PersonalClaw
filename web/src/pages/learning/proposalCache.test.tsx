import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, renderHook, act, waitFor } from '@testing-library/react'
import { useQuery, invalidateKeys, peekQuery } from '../../lib/data'
import { PROPOSALS_KEY_PREFIX, WEEK_KEY, proposalsKey, refreshAfterDecision, refreshEverything } from './proposalCache'
import { LearningPage } from './LearningPage'
import { ApiError } from '../../lib/api'
import type { LearningInbox, LearningRow, StagingWeek } from '../../lib/api'

// ── #676: a decided proposal must leave the screen ───────────────────────────
//
// Measured before the fix: DELETE /api/learning/proposals/{id} returned 200, the server's own list
// went to `rows: 0`, and the row was still on screen at 7.5s with no second request. `decide()`
// dropped the cache entry and stopped there — that arms the next MOUNT, it does not re-render the
// live one. The row was not merely cosmetic either: a second Reject on the ghost escalates the
// rejection cooldown (learning/proposals.py:298-302), so the user's own retry compounds the damage.
//
// The button read "Dismiss" when #676 was written and reads "Reject" now: the app reserves Dismiss
// for triaging an item off a list (InboxDetail writes `status: 'dismissed'`), and this declines a
// PROPOSAL. Same handler, same endpoint — only the label moved, so these assertions are unchanged
// in substance.
//
// The first describe drives the REAL page, because that is the only thing that proves the row is
// gone from the DOM — a helper test can only prove the helper. The second pins the cache reasoning
// the helper carries (facet sweep, week left alone), which the page cannot show.

const row = (over: Partial<LearningRow> = {}): LearningRow => ({
  id: 'skill-f6fab94955e7', kind: 'skill', title: 'summarize before filing', provenance: 'refiner',
  source_cadence: 'run_end', source_excerpt: '', evidence_refs: ['r1'],
  evidence_strength: 'correlated',
  reinforcements: 2, confidence: 0.7, manifest_valid: true, manifest_issues: [],
  risk_tier: 'low', status: 'pending', renderable: true, bulk_acceptable: true,
  gate: {
    state: 'ungated', reason: 'no gate run yet', before: null, after: null, delta: null,
    regressed: false, scenarios: 0, halted: false, dollars_est: 0, spend_observed: false,
    pin: {}, ran_at: '',
  },
  ...over,
})

const inboxOf = (rows: LearningRow[]): LearningInbox => ({
  rows, total: rows.length,
  by_kind: rows.reduce<Record<string, number>>((a, r) => ({ ...a, [r.kind]: (a[r.kind] ?? 0) + 1 }), {}),
  by_tier: {}, flagged: 0, unrenderable: [], bulk_acceptable: rows.length,
})

const WEEK: StagingWeek = {
  days: 7, buckets: [], silent_days: [], error_days: [], produced_total: 3, cost_usd: 0,
}

// Only the four calls this page makes. Mocked at the api module rather than at fetch: the page's
// contract is with these four functions, and a fetch-level fake would re-implement api.ts's URL
// building just to assert on it.
const learningProposals = vi.fn<() => Promise<LearningInbox>>()
const learningStagingWeek = vi.fn<() => Promise<StagingWeek>>()
const acceptLearningProposal = vi.fn<() => Promise<{ ok: boolean }>>()
const rejectLearningProposal = vi.fn<() => Promise<void>>()
// The page gained a third read (the flywheel health panel, LEARN-R14b). Mocked here because a
// double that omits a fetch the page makes throws inside a passive effect — which surfaces as
// five unrelated failures about rows and cache keys, and hides which fetch is missing.
const learningHealth = vi.fn<() => Promise<never>>()
const judgeBench = vi.fn<() => Promise<never>>()
const evalStudies = vi.fn<() => Promise<never>>()
const retrievalBench = vi.fn<() => Promise<never>>()
const ablation = vi.fn<() => Promise<never>>()
const identityReport = vi.fn<() => Promise<never>>()
const learningBenchmark = vi.fn<() => Promise<never>>()

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
      acceptLearningProposal: () => acceptLearningProposal(),
      rejectLearningProposal: () => rejectLearningProposal(),
      judgeBench: () => judgeBench(),
      evalStudies: () => evalStudies(),
      retrievalBench: () => retrievalBench(),
      ablation: () => ablation(),
      identityReport: () => identityReport(),
      // Rejected in each suite's setup with its own ORDINARY 404 code: this page reads it,
      // so a double that omits it throws inside a passive effect and hides the real failure.
      learningBenchmark: () => learningBenchmark(),
    },
  }
})

describe('LearningPage drops a decided row from the screen (#676)', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
    vi.clearAllMocks()
    learningStagingWeek.mockResolvedValue(WEEK)
    // Rejected on purpose: this suite is about the proposal list, and a health panel that
    // fails to load must not change what the list does. `HealthPanel.test.tsx` owns the
    // panel's own error rendering.
    learningHealth.mockRejectedValue(new Error('not under test'))
    // Same posture as the health panel, for the same reason: the judge-tier panel's own
    // rendering is not this suite's subject, and a 404 is its ORDINARY state (no benchmark
    // has run), so the list must be unaffected by it.
    judgeBench.mockRejectedValue(new ApiError('No judge benchmark has run yet. Run `personalclaw judge-bench` to produce one.', 404, 'judge_bench_absent'))
    // And the study panel, for the third time and the same reason: `StudiesPanel.test.tsx` owns
    // its rendering, and "no study registered" is its ordinary state.
    evalStudies.mockRejectedValue(new ApiError('No study is registered under that id.', 404, 'study_absent'))
    // And the retrieval-arms panel, for the fourth time and the same reason:
    // `RetrievalBenchPanel.test.tsx` owns its rendering, and "no retrieval benchmark yet"
    // is its ordinary state.
    retrievalBench.mockRejectedValue(new ApiError('No retrieval benchmark has run yet. Run `personalclaw retrieval-eval` to score both stores.', 404, 'retrieval_absent'))
    // And the ablation report, for the fifth time and the same reason: `AblationPanel.test.tsx`
    // owns its rendering, and "no ablation has run yet" is its ordinary state — for months,
    // since the cadence is monthly and the registry starts empty.
    ablation.mockRejectedValue(new ApiError('No ablation has run yet. Register a component in `evals/ablation_registry.json` and run `personalclaw ablation --force`.', 404, 'ablation_absent'))
    // And LV-4's identity report, for the sixth time and the same reason:
    // `IdentityReportPanel.test.tsx` owns its rendering. Omitting it threw inside a passive
    // effect and surfaced as five failures about rows and cache keys — the exact symptom the
    // note above this mock block describes, reproduced by the sixth read.
    identityReport.mockRejectedValue(new Error('not under test'))
    // And LV-7's benchmark, for the seventh. An `ApiError` carrying the real code, matching the
    // convention the rest of this block moved to: `BenchmarkPanel` branches on `hasApiCode`.
    learningBenchmark.mockRejectedValue(new ApiError('No skill-impact benchmark has run yet. Run `python scripts/learning_benchmark.py --preflight` and then `--run`.', 404, 'learning_benchmark_absent'))
    acceptLearningProposal.mockResolvedValue({ ok: true })
    rejectLearningProposal.mockResolvedValue(undefined)
  })

  /** Mount the page with one row, click `label`, and report what the list shows afterwards. */
  async function decideOnly(label: 'Accept' | 'Reject') {
    // The server truth from the bug report: one row, then zero.
    learningProposals
      .mockResolvedValueOnce(inboxOf([row()]))
      .mockResolvedValue(inboxOf([]))

    const { findByText, getByText, queryByText } = render(<LearningPage />)
    const title = await findByText('summarize before filing')
    expect(title).toBeInTheDocument()

    await act(async () => { getByText(label).click() })
    await waitFor(() => expect(queryByText('summarize before filing')).not.toBeInTheDocument())
    return { getByText, queryByText }
  }

  it('removes the row after Reject and shows the empty state', async () => {
    const { getByText } = await decideOnly('Reject')
    // `rows: 0` means the page must now say so. Before the fix this assertion failed at 7.5s.
    expect(getByText('Nothing to review')).toBeInTheDocument()
    // The refetch is the mechanism: two list reads, the second one AFTER the DELETE.
    expect(rejectLearningProposal).toHaveBeenCalledTimes(1)
    expect(learningProposals.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('removes the row after Accept too', async () => {
    await decideOnly('Accept')
    expect(acceptLearningProposal).toHaveBeenCalledTimes(1)
    expect(learningProposals.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('keeps the row when the decision FAILS, and says why', async () => {
    // The 403 human-installs gate. A page that optimistically removed the row would tell the user
    // the proposal was dismissed when the server refused — the reason `decide` refetches instead of
    // splicing local state.
    learningProposals.mockResolvedValue(inboxOf([row()]))
    rejectLearningProposal.mockRejectedValue(new Error('only a human reviewer may reject proposals'))

    const { findByText, getByText } = render(<LearningPage />)
    await findByText('summarize before filing')
    await act(async () => { getByText('Reject').click() })

    await waitFor(() => expect(getByText('only a human reviewer may reject proposals')).toBeInTheDocument())
    expect(getByText('summarize before filing')).toBeInTheDocument()
  })

  it('does not re-read the capture week on a decision', async () => {
    // The week's numbers come from the staging store's flush/staging tables — what a capture PASS
    // did. Neither accept nor reject writes either, so a decision-time refetch would be a request
    // that provably cannot return anything new.
    await decideOnly('Reject')
    expect(learningStagingWeek).toHaveBeenCalledTimes(1)
  })

  it('DOES re-read the capture week on an explicit Refresh', async () => {
    // A Refresh makes no claim about what changed — the user is asking for current server state,
    // and a capture pass may well have run since the page mounted. That is the whole difference.
    learningProposals.mockResolvedValue(inboxOf([row()]))
    const { findByText, getByText } = render(<LearningPage />)
    await findByText('summarize before filing')
    expect(learningStagingWeek).toHaveBeenCalledTimes(1)

    await act(async () => { getByText('Refresh').click() })
    await waitFor(() => expect(learningStagingWeek).toHaveBeenCalledTimes(2))
  })
})

describe('refreshAfterDecision sweeps every facet, not just the active one', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
  })

  it('refetches the live view instead of only arming the next mount', async () => {
    // The bug, isolated: `invalidateKeys` alone leaves `data` exactly as it was.
    const fetcher = vi.fn()
      .mockResolvedValueOnce(inboxOf([row()]))
      .mockResolvedValue(inboxOf([]))
    const { result } = renderHook(() => useQuery<LearningInbox>(proposalsKey('skill'), fetcher))
    await waitFor(() => expect(result.current.data?.total).toBe(1))

    act(() => { refreshAfterDecision(result.current.refresh) })
    await waitFor(() => expect(result.current.data?.total).toBe(0))
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('drops the OTHER facets too, so selecting a tab cannot paint a decided row', async () => {
    // Facet keys are per-tab (`learning:proposals:` for All, `learning:proposals:skill`, …). A row
    // dismissed from the Skill tab is also gone from All, and the hook seeds a key change straight
    // from cache — so a single-key drop would repaint the ghost the instant All is selected.
    const skillFetch = vi.fn().mockResolvedValue(inboxOf([row()]))
    const allFetch = vi.fn().mockResolvedValue(inboxOf([row()]))
    // Both facets mounted AND settled inside one act(): the All tab is the entry that must not
    // survive the sweep, so it has to be genuinely warm first. Settling inside act also keeps each
    // initial fetch from resolving into the tree after the assertions have run.
    const skill = await act(async () => {
      const active = renderHook(() => useQuery<LearningInbox>(proposalsKey('skill'), skillFetch))
      renderHook(() => useQuery<LearningInbox>(proposalsKey(''), allFetch))
      return active
    })
    expect(peekQuery(proposalsKey('skill'))).toBeTruthy()
    expect(peekQuery(proposalsKey(''))).toBeTruthy()

    expect(allFetch, 'the All facet fetched once on mount').toHaveBeenCalledTimes(1)
    await act(async () => { refreshAfterDecision(skill.result.current.refresh) })
    // DSC-14 STRENGTHENED THIS. The old assertion was `peekCache(proposalsKey('')) ===
    // undefined` — "the entry is gone, so its NEXT MOUNT refetches rather than seeding the
    // ghost". Under one data layer the sweep reaches every MOUNTED reader of a swept key, so the
    // inactive facet does not wait for a next mount: it re-reads now. Which is the stronger
    // property, and the one the user feels — the ghost row cannot be painted on tab select
    // because it is already gone from the tab that is not on screen.
    expect(allFetch, 'the swept facet re-read itself, unprompted').toHaveBeenCalledTimes(2)
    expect(peekQuery(proposalsKey('')), 'and holds a FRESH value, not a stale one').toBeTruthy()
    expect(peekQuery(proposalsKey('skill'))).toBeTruthy()
  })

  it('leaves the capture week cached, and refreshEverything does not', async () => {
    const weekFetch = vi.fn().mockResolvedValue(WEEK)
    const listFetch = vi.fn().mockResolvedValue(inboxOf([]))
    const { result } = renderHook(() => useQuery(WEEK_KEY, weekFetch))
    const list = renderHook(() => useQuery(proposalsKey(''), listFetch))
    await waitFor(() => expect(peekQuery(WEEK_KEY)).toBeTruthy())

    act(() => { refreshAfterDecision(list.result.current.refresh) })
    expect(peekQuery(WEEK_KEY)).toBeTruthy()

    act(() => { refreshEverything(list.result.current.refresh, result.current.refresh) })
    await waitFor(() => expect(weekFetch).toHaveBeenCalledTimes(2))
  })

  it('keys every facet under one prefix, so the sweep can find them', () => {
    // The sweep is a prefix match; a facet key that did not start with it would survive silently.
    for (const kind of ['', 'skill', 'lesson_batch', 'template_diff']) {
      expect(proposalsKey(kind).startsWith(PROPOSALS_KEY_PREFIX)).toBe(true)
    }
    expect(WEEK_KEY.startsWith(PROPOSALS_KEY_PREFIX)).toBe(false)
  })
})
