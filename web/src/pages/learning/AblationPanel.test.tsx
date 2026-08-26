import { describe, expect, it, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { invalidateKeys } from '../../lib/data'
import { AblationPanel } from './AblationPanel'
import { LearningPage } from './LearningPage'
import { ApiError } from '../../lib/api'
import type { AblationArmAggregate, AblationView, LearningInbox, StagingWeek } from '../../lib/api'

/** ES-7's keep/remove/lighten report — and, first, the fact that anything reads it at all.
 *
 *  `GET /api/evals/ablation` shipped REGISTERED, TESTED and documented in `routes.md` with **no
 *  frontend consumer**. Its ES-7 execution log flagged that deliberately rather than
 *  half-shipping a panel. So the load-bearing test in this file is not any of the rendering
 *  cases below — it is `is rendered BY LearningPage`, because a suite that only mounts
 *  `AblationPanel` in isolation stays green with the render deleted from the page, which
 *  reproduces the exact defect one level up.
 *
 *  The rendering cases then cover the two things a report that has usually NEVER RUN gets
 *  wrong:
 *
 *  1. An unmeasured arm must not render as a zero. For an ablation this is sharper than for the
 *     judge bench: `mean_score: null` means no cell was ever scored, and drawing 0.000 for it
 *     turns "we never measured this" into "it scored nothing" — the strongest possible case for
 *     deleting the component, asserted from a measurement that did not happen.
 *  2. EMPTY must not look like BROKEN. The backend mints three distinct codes on purpose; a
 *     failed fetch that renders as "no ablation has run yet" is the recurring defect this
 *     section exists to refuse.
 */

// ── the page's reads. `ablation` is the one under test; the rest are mocked because a double
// that omits a fetch the page makes throws inside a passive effect, which surfaces as unrelated
// failures and hides which fetch is missing.
const learningProposals = vi.fn<() => Promise<LearningInbox>>()
const learningStagingWeek = vi.fn<() => Promise<StagingWeek>>()
const learningHealth = vi.fn<() => Promise<never>>()
const judgeBench = vi.fn<() => Promise<never>>()
const evalStudies = vi.fn<() => Promise<never>>()
const retrievalBench = vi.fn<() => Promise<never>>()
const identityReport = vi.fn<() => Promise<never>>()
const ablation = vi.fn<() => Promise<AblationView>>()
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
      judgeBench: () => judgeBench(),
      evalStudies: () => evalStudies(),
      retrievalBench: () => retrievalBench(),
      identityReport: () => identityReport(),
      ablation: () => ablation(),
      // Rejected in each suite's setup with its own ORDINARY 404 code: this page reads it,
      // so a double that omits it throws inside a passive effect and hides the real failure.
      learningBenchmark: () => learningBenchmark(),
      acceptLearningProposal: () => Promise.resolve({ ok: true }),
      rejectLearningProposal: () => Promise.resolve(undefined),
    },
  }
})

const EMPTY_INBOX: LearningInbox = {
  rows: [], total: 0, by_kind: {}, by_tier: {}, flagged: 0, unrenderable: [], bulk_acceptable: 0,
}
const WEEK: StagingWeek = {
  days: 7, buckets: [], silent_days: [], error_days: [], produced_total: 0, cost_usd: 0,
}

function agg(over: Partial<AblationArmAggregate> = {}): AblationArmAggregate {
  return {
    counts: { passed: 3, failed: 0, verifier_absent: 0 },
    total: 3,
    scored_count: 3,
    mean_score: 0.82,
    ...over,
  }
}

function view(over: Partial<AblationView> = {}): AblationView {
  return {
    report: {
      component_id: 'refiner-skill',
      kind: 'skill',
      target: 'summarize-first',
      subject: 'inbox-triage',
      verdict: 'keep',
      arms: { on: agg(), off: agg({ mean_score: 0.4 }) },
      delta: 0.42,
      cheap_delta: null,
      epsilon: 0.05,
      matrix_id: 'ablation-20260824T000000Z',
      trials: 3,
      created_at: '2026-08-24T00:00:00+00:00',
      live_state: { 'config.json': 'sha256:abc' },
    },
    verdict_vocabulary: ['keep', 'remove', 'lighten'],
    registry: [],
    history: [],
    last_run_ts: '2026-08-24T00:00:00+00:00',
    cadence_days: 30,
    due: false,
    ...over,
  }
}

describe('the ablation report is CONSUMED, not merely served', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
    vi.clearAllMocks()
    learningProposals.mockResolvedValue(EMPTY_INBOX)
    learningStagingWeek.mockResolvedValue(WEEK)
    // Each of these rejects with its own ORDINARY 404 code: their panels own their own
    // rendering, and this suite's subject must not depend on any of them loading.
    learningHealth.mockRejectedValue(new Error('not under test'))
    judgeBench.mockRejectedValue(new ApiError('No judge benchmark has run yet. Run `personalclaw judge-bench` to produce one.', 404, 'judge_bench_absent'))
    evalStudies.mockRejectedValue(new ApiError('No study is registered under that id.', 404, 'study_absent'))
    retrievalBench.mockRejectedValue(new ApiError('No retrieval benchmark has run yet. Run `personalclaw retrieval-eval` to score both stores.', 404, 'retrieval_absent'))
    // LV-4's identity report: the page reads it, so a double that omits it throws inside a
    // passive effect — exactly the failure mode the note above the declarations describes.
    identityReport.mockRejectedValue(new Error('not under test'))
    // LV-7's skill-impact benchmark, in its ORDINARY never-run state. An `ApiError` carrying the
    // real code, not a bare `Error`: `BenchmarkPanel` branches on `hasApiCode`, so a message-only
    // double would fall through to the generic failure and assert nothing about the empty state.
    learningBenchmark.mockRejectedValue(new ApiError('No skill-impact benchmark has run yet. Run `python scripts/learning_benchmark.py --preflight` and then `--run`.', 404, 'learning_benchmark_absent'))
  })

  /** 🔑 THE RAIL THAT CLOSES THE INERT ROUTE.
   *
   *  Deleting `<AblationPanel …>` from `LearningPage` must turn this red. Every other case in
   *  this file renders the component directly and would survive that deletion untouched —
   *  which is precisely the state the route was already in. */
  it('is rendered BY LearningPage, and the api client is actually called', async () => {
    // Vacuity floor. If the heading query were satisfiable by anything on screen — or by
    // nothing at all — this rail would pass with the render deleted, and would be measuring
    // its own matcher instead of the call site.
    const control = render(<div />)
    expect(screen.queryByText('Component ablation')).toBeNull()
    expect(ablation).not.toHaveBeenCalled()
    control.unmount()

    ablation.mockResolvedValue(view())
    render(<LearningPage />)

    // The route is REQUESTED: the client is not merely exported next to `judgeBench`.
    expect(ablation).toHaveBeenCalled()
    // And its payload is PAINTED, on the page a user can actually reach (`#/learning`,
    // `app/App.tsx:148`) — not in a component nothing routes to.
    expect(await screen.findByText('Component ablation')).toBeTruthy()
    expect(screen.getByText('ablation-20260824T000000Z')).toBeTruthy()
    expect(screen.getByText(/Keep summarize-first/)).toBeTruthy()
  })

  it('names the panel for assistive tech', () => {
    render(<AblationPanel view={view()} error={undefined} onRetry={() => {}} />)
    // Asked of the accessibility tree, not of a class name: the section is only a landmark
    // because `aria-labelledby` resolves, and a heading that renders while the id mismatches
    // looks identical on screen.
    expect(screen.getByRole('region', { name: 'Component ablation' })).toBeTruthy()
  })

  it('renders an unmeasured arm as unmeasured, never as a zero', () => {
    render(<AblationPanel
      view={view({
        report: {
          ...view().report,
          verdict: 'inconclusive',
          arms: { on: agg(), off: agg({ counts: { verifier_absent: 3 }, scored_count: 0, mean_score: null }) },
          delta: null,
        },
      })}
      error={undefined}
      onRetry={() => {}}
    />)
    // The off arm's mean AND the delta it feeds. Neither may become a number.
    expect(screen.getAllByText('not measured').length).toBe(2)
    expect(screen.queryByText('0.000')).toBeNull()
    expect(screen.queryByText('+0.000')).toBeNull()
  })

  it('refuses to read an inconclusive report as a retirement', () => {
    render(<AblationPanel
      view={view({ report: { ...view().report, verdict: 'inconclusive', delta: null } })}
      error={undefined} onRetry={() => {}} />)
    expect(screen.getByText('inconclusive')).toBeTruthy()
    expect(screen.getByText(/No verdict for summarize-first/)).toBeTruthy()
    // The whole point: an absent verifier is never a zero, so it is never a `remove` either.
    expect(screen.queryByText(/Retire/)).toBeNull()
  })

  it('reads the verdict as a verdict, not as a raw enum', () => {
    render(<AblationPanel
      view={view({ report: { ...view().report, verdict: 'remove', delta: 0.01 } })}
      error={undefined} onRetry={() => {}} />)
    expect(screen.getByText(/Retire summarize-first/)).toBeTruthy()
    // A `remove` is actionable somewhere else — the panel says where instead of leaving the
    // reader with a word.
    expect(screen.getByRole('link', { name: 'Inbox' })).toBeTruthy()
    // And the bare enum value appears nowhere: `remove` is the machine's word for it.
    expect(screen.queryByText('remove')).toBeNull()
  })

  it('says a lighten verdict is unreachable for a component with no cheap form', () => {
    render(<AblationPanel
      view={view({
        registry: [{
          component_id: 'refiner-skill', kind: 'skill', target: 'summarize-first',
          subject: 'inbox-triage', off_value: false, cheap_value: null, live_refs: [],
          description: 'the refiner summarizes before filing',
        }],
      })}
      error={undefined} onRetry={() => {}} />)
    expect(screen.getByText(/No cheap form declared/)).toBeTruthy()
  })

  it('shows a remove verdict that filed NOTHING as unfiled', () => {
    render(<AblationPanel
      view={view({
        history: [
          { ts: '2026-07-24T00:00:00+00:00', component_id: 'refiner-skill', verdict: 'remove', matrix_id: 'm1', delta: 0.001, proposal: '' },
          { ts: '2026-06-24T00:00:00+00:00', component_id: 'other', verdict: 'remove', matrix_id: 'm0', delta: 0.002, proposal: 'not_filed:cooldown' },
        ],
      })}
      error={undefined} onRetry={() => {}} />)
    // A dropped recommendation looks identical to a completed one unless the panel says so.
    expect(screen.getByText('not filed')).toBeTruthy()
    expect(screen.getByText('not filed (cooldown)')).toBeTruthy()
  })

  // ── EMPTY vs BROKEN vs OFF: three codes, three answers ─────────────────────────────

  it('renders "no ablation yet" as guidance rather than as a load failure', () => {
    render(<AblationPanel view={undefined} error={new ApiError('No ablation has run yet. Register a component in `evals/ablation_registry.json` and run `personalclaw ablation --force`.', 404, 'ablation_absent')} onRetry={() => {}} />)
    expect(screen.getByText(/personalclaw ablation --force/)).toBeTruthy()
    expect(screen.queryByText(/Retry/)).toBeNull()
  })

  it('surfaces a REAL failure instead of rendering it as "nothing has run"', () => {
    render(<AblationPanel view={undefined} error={new Error('boom')} onRetry={() => {}} />)
    expect(screen.getByText(/ablation report/)).toBeTruthy()
    // The exact confusion this panel refuses: a failed fetch reading as an empty report.
    expect(screen.queryByText(/personalclaw ablation --force/)).toBeNull()
    expect(screen.queryByText(/No ablation has run yet/)).toBeNull()
  })

  it('points at the SWITCH when the substrate is off, not at the registry', () => {
    render(<AblationPanel view={undefined} error={new ApiError('The eval substrate is off. Turn on `evals.enabled` to publish benchmark results.', 404, 'evals_disabled')} onRetry={() => {}} />)
    expect(screen.getByText(/evals.enabled/)).toBeTruthy()
    // Turning on a setting and registering a component send a user to two different places.
    expect(screen.queryByText(/ablation_registry.json/)).toBeNull()
  })

  /** The client the page calls must target the route that was inert.
   *
   *  Read from source because the api module is mocked for the rest of this file — and because
   *  the failure this guards is a typo'd path, which a mock can never catch. */
  it('the api client targets GET /api/evals/ablation', () => {
    const src = readFileSync(join(process.cwd(), 'src', 'lib', 'api.ts'), 'utf8')
    expect(src).toContain("ablation: () => get<AblationView>('/api/evals/ablation')")
    // Vacuity floor for the scan itself: `toContain` over a large file passes for the wrong
    // reasons easily, so prove it discriminates.
    expect(src).not.toContain("'/api/evals/ablations'")
  })
})
