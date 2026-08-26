import { describe, expect, it, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import { BenchmarkPanel } from './BenchmarkPanel'
import { LearningPage } from './LearningPage'
import type {
  BenchmarkReport, BenchmarkTaskRow, BenchmarkView, LearningInbox, StagingWeek,
} from '../../lib/api'

/** LV-7's results page — and, first, the fact that the page actually renders it.
 *
 *  The load-bearing test here is `is rendered BY LearningPage`. Every other case mounts
 *  `BenchmarkPanel` directly and would survive the render being deleted from the page, which is
 *  precisely the inert-route state this repo keeps finding: a registered route, a tested
 *  component, and nothing on screen.
 *
 *  The rendering cases cover the three things this surface most easily gets wrong:
 *
 *  1. **An unmeasured result must not render as a zero.** `verdict: null` / `delta_points: null`
 *     means the arms could not be assembled. Drawing `0.000` would turn "we never measured this"
 *     into "the skills you approved scored nothing" — for a benchmark whose whole question is
 *     whether skills help, a fabricated zero IS the negative answer.
 *  2. **A failed fetch must not render as an empty state.** "No benchmark has run yet" is this
 *     panel's ORDINARY state, permanently so for most users (the paired design is 100 real model
 *     calls), which makes it exactly the state that must be distinguishable from "we could not
 *     ask".
 *  3. **"Within stated variance" requires the variance to be STATED.** The reproduction block
 *     prints the conditions and cites where the protocol states them, so a tolerance invented by
 *     the code would be visibly missing its citation.
 */

const learningProposals = vi.fn<() => Promise<LearningInbox>>()
const learningStagingWeek = vi.fn<() => Promise<StagingWeek>>()
const learningHealth = vi.fn<() => Promise<never>>()
const judgeBench = vi.fn<() => Promise<never>>()
const evalStudies = vi.fn<() => Promise<never>>()
const retrievalBench = vi.fn<() => Promise<never>>()
const ablation = vi.fn<() => Promise<never>>()
const learningBenchmark = vi.fn<() => Promise<BenchmarkView>>()

vi.mock('../../lib/api', () => ({
  api: {
    learningProposals: () => learningProposals(),
    learningStagingWeek: () => learningStagingWeek(),
    learningHealth: () => learningHealth(),
    judgeBench: () => judgeBench(),
    evalStudies: () => evalStudies(),
    retrievalBench: () => retrievalBench(),
    ablation: () => ablation(),
    learningBenchmark: () => learningBenchmark(),
    acceptLearningProposal: () => Promise.resolve({ ok: true }),
    rejectLearningProposal: () => Promise.resolve(undefined),
  },
}))

const EMPTY_INBOX: LearningInbox = {
  rows: [], total: 0, by_kind: {}, by_tier: {}, flagged: 0, unrenderable: [], bulk_acceptable: 0,
}
const WEEK: StagingWeek = {
  days: 7, buckets: [], silent_days: [], error_days: [], produced_total: 0, cost_usd: 0,
}
const DOC = 'docs/roadmap/research/learning-benchmark-protocol.md'

function arm(mean: number, spread = 1.5) {
  return { trials: 5, mean_score: mean, spread, tokens: 41000, tokens_per_point: 512.5 }
}

function task(over: Partial<BenchmarkTaskRow> = {}): BenchmarkTaskRow {
  return {
    task_id: 'sk_grill',
    skill: 'grill',
    verdict: 'skills_on_wins',
    verdict_class: 'skills_on_wins',
    reason: '',
    delta_points: 12.5,
    token_ratio: 1.002,
    arms: { skills_on: arm(78), skills_off: arm(65.5) },
    absent_cells: 0,
    tool_calls: { skills_on: 6, skills_off: 2 },
    spend_observed: true,
    spend_estimated: false,
    notes: [],
    ...over,
  }
}

function report(over: Partial<BenchmarkReport> = {}): BenchmarkReport {
  return {
    run_id: 'learnbench-20260826T000000Z',
    created_at: '2026-08-26T00:00:00+00:00',
    protocol_doc: DOC,
    task_set_version: 1,
    task_set_fingerprint: { sk_grill: 'ab'.repeat(32) },
    trials_per_arm: 5,
    arms: ['skills_on', 'skills_off'],
    thresholds: {
      inconclusive_band_points: 5,
      token_match_tolerance: 0.05,
      min_trials_per_arm: 3,
      source: 'harness/fanout_measure.py',
    },
    tasks: [task()],
    skipped: [],
    measured_tasks: 1,
    absent_cells: 0,
    ...over,
  }
}

function view(over: Partial<BenchmarkView> = {}): BenchmarkView {
  return {
    report: report(),
    register: [{ task_id: 'sk_grill', skill: 'grill', observable: 'no bare acceptance' }],
    task_set_version: 1,
    protocol_doc: DOC,
    stated_variance: ['same task_set_version'],
    ...over,
  }
}

describe('the skill-impact benchmark is CONSUMED, not merely served', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
    vi.clearAllMocks()
    learningProposals.mockResolvedValue(EMPTY_INBOX)
    learningStagingWeek.mockResolvedValue(WEEK)
    learningHealth.mockRejectedValue(new Error('not under test'))
    judgeBench.mockRejectedValue(new Error('judge_bench_absent'))
    evalStudies.mockRejectedValue(new Error('study_absent'))
    retrievalBench.mockRejectedValue(new Error('retrieval_absent'))
    ablation.mockRejectedValue(new Error('ablation_absent'))
  })

  /** 🔑 THE CALL-SITE RAIL. Deleting `<BenchmarkPanel …>` from `LearningPage` must turn this
   *  red. Every other case in this file renders the component directly and would survive that
   *  deletion untouched. */
  it('is rendered BY LearningPage, and the api client is actually called', async () => {
    // Vacuity floor: if the heading query were satisfiable by nothing on screen, this rail
    // would pass with the render deleted and would be measuring its own matcher.
    const control = render(<div />)
    expect(screen.queryByText('Skill impact benchmark')).toBeNull()
    expect(learningBenchmark).not.toHaveBeenCalled()
    control.unmount()

    learningBenchmark.mockResolvedValue(view())
    render(<LearningPage />)

    expect(learningBenchmark).toHaveBeenCalled()
    expect(await screen.findByText('Skill impact benchmark')).toBeTruthy()
    expect(screen.getByText('learnbench-20260826T000000Z')).toBeTruthy()
    expect(screen.getByText('+12.50')).toBeTruthy()
  })

  it('names the panel for assistive tech', () => {
    render(<BenchmarkPanel view={view()} error={undefined} onRetry={() => {}} />)
    // Asked of the accessibility tree, not of a class name: the section is a labelled region
    // only because `aria-labelledby` RESOLVES, and a mismatched id looks identical on screen.
    expect(screen.getByRole('region', { name: 'Skill impact benchmark' })).toBeTruthy()
  })

  // ── 1. an unmeasured result is never a zero ────────────────────────────────

  it('renders an unmeasured task as "not measured", never as 0.000', () => {
    render(
      <BenchmarkPanel
        view={view({
          report: report({
            tasks: [task({
              verdict: null,
              verdict_class: null,
              delta_points: null,
              token_ratio: null,
              arms: {},
              absent_cells: 10,
              reason: 'arm(s) skills_off produced no scored cell',
            })],
            measured_tasks: 0,
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('Nothing was measured')).toBeTruthy()
    expect(screen.getAllByText('not measured').length).toBeGreaterThan(0)
    // The vacuity assertion for the claim above: prove the forbidden strings are ABSENT, not
    // merely that a good string is present. A panel that drew both would pass a presence-only
    // check while publishing the fabricated zero.
    expect(screen.queryByText('+0.00')).toBeNull()
    expect(screen.queryByText('0.000')).toBeNull()
    expect(screen.queryByText('0.00 ±0.00')).toBeNull()
  })

  it('draws a real measured delta, so the case above is not vacuous', () => {
    render(<BenchmarkPanel view={view()} error={undefined} onRetry={() => {}} />)
    expect(screen.getByText('+12.50')).toBeTruthy()
    expect(screen.getByText('78.00 ±1.50')).toBeTruthy()
    expect(screen.getByText('1 of 1 task measured')).toBeTruthy()
    expect(screen.queryByText('not measured')).toBeNull()
  })

  it('publishes a skills-off win with the same prominence as a win', () => {
    render(
      <BenchmarkPanel
        view={view({
          report: report({
            tasks: [task({
              verdict: 'skills_off_wins',
              verdict_class: 'skills_off_wins',
              delta_points: -9.25,
            })],
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('skills_off_wins')).toBeTruthy()
    expect(screen.getByText('-9.25')).toBeTruthy()
  })

  it('says so when spend was not observed, rather than printing a bare token ratio', () => {
    render(
      <BenchmarkPanel
        view={view({ report: report({ tasks: [task({ spend_observed: false })] }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText(/spend not observed/)).toBeTruthy()
  })

  it('says "estimated" about tokens, because §4 requires the word', () => {
    render(
      <BenchmarkPanel
        view={view({ report: report({ tasks: [task({ spend_estimated: true })] }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText(/estimated, not provider-reported/)).toBeTruthy()
  })

  it('reports tasks the runner refused to run instead of shortening the table', () => {
    render(
      <BenchmarkPanel
        view={view({
          report: report({
            tasks: [],
            measured_tasks: 0,
            skipped: [{
              task_id: 'sk_grill',
              skill: 'grill',
              blockers: ['incomplete RunPin (missing: model_fingerprint)'],
            }],
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('Not run (1)')).toBeTruthy()
    expect(screen.getByText(/incomplete RunPin/)).toBeTruthy()
  })

  // ── 2. empty must not look like broken ─────────────────────────────────────

  it('distinguishes "no benchmark yet" from "we could not ask"', () => {
    const absent = render(
      <BenchmarkPanel
        view={undefined}
        error={new Error('learning_benchmark_absent')}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText(/No skill-impact benchmark has run yet/)).toBeTruthy()
    // The ordinary state still offers the methodology, because a reader in it has nothing else.
    expect(screen.getByRole('link', { name: /Methodology/ })).toBeTruthy()
    absent.unmount()

    render(
      <BenchmarkPanel view={undefined} error={new Error('boom')} onRetry={() => {}} />,
    )
    // A real failure reaches the shared LoadError, NOT the empty copy. Asserted as an absence
    // of the empty sentence: the whole defect is the two states looking identical.
    expect(screen.queryByText(/No skill-impact benchmark has run yet/)).toBeNull()
    expect(screen.getByText(/skill-impact benchmark/)).toBeTruthy()
  })

  it('tells a user the substrate is off rather than showing an empty benchmark', () => {
    render(
      <BenchmarkPanel view={undefined} error={new Error('evals_disabled')} onRetry={() => {}} />,
    )
    expect(screen.getByText(/The eval substrate is off/)).toBeTruthy()
    expect(screen.queryByText(/No skill-impact benchmark has run yet/)).toBeNull()
  })

  // ── 3. the methodology link, and a STATED variance ─────────────────────────

  it('links the methodology at the path the REPORT cited, not a hardcoded one', () => {
    render(
      <BenchmarkPanel
        view={view({ report: report({ protocol_doc: 'docs/other/protocol-v2.md' }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    const link = screen.getByRole('link', { name: /Methodology/ }) as HTMLAnchorElement
    // The path travels with the report so the link cannot drift from the document the runner
    // actually measured against.
    expect(link.href).toContain('docs/other/protocol-v2.md')
    expect(link.href).not.toContain('learning-benchmark-protocol.md')
  })

  it('prints the reproduction conditions and cites where the variance is stated', () => {
    render(
      <BenchmarkPanel
        view={view({
          report: report({
            reproduction: {
              baseline_run_id: 'learnbench-A',
              rerun_run_id: 'learnbench-B',
              reproduces: false,
              stated_variance: ['same task_set_version', 'same verdict class per task'],
              stated_variance_source: `${DOC} §8 (Reproduction (V4))`,
              conditions: {
                'same task_set_version': true,
                'same verdict class per task': false,
              },
              verdict_changes: [
                { task_id: 'sk_grill', baseline: 'inconclusive', rerun: 'skills_on_wins' },
              ],
              notes: [],
            },
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('Did NOT reproduce within the stated variance')).toBeTruthy()
    // The CITATION is the point: "within stated variance" is only checkable if the reader can
    // see where the variance is stated.
    expect(screen.getByText(`${DOC} §8 (Reproduction (V4))`)).toBeTruthy()
    expect(screen.getByText(/same verdict class per task/)).toBeTruthy()
    expect(screen.getByText('not met')).toBeTruthy()
    expect(screen.getByText('met')).toBeTruthy()
  })

  it('omits the reproduction block entirely when no re-run was judged', () => {
    render(<BenchmarkPanel view={view()} error={undefined} onRetry={() => {}} />)
    expect(screen.queryByText(/reproduce within the stated variance/)).toBeNull()
  })
})
