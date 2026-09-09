import { describe, expect, it, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import { BenchmarkPanel } from './BenchmarkPanel'
import { LearningPage } from './LearningPage'
import { ApiError } from '../../lib/api'
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
const identityReport = vi.fn<() => Promise<never>>()
const learningBenchmark = vi.fn<() => Promise<BenchmarkView>>()

// 🪟 PARTIAL mock, via `importOriginal`: the REAL `ApiError`/`hasApiCode` are kept. This
// panel branches on `hasApiCode(error, '<code>')`, so a factory that returned only `api` makes the
// mocked module throw "No \"hasApiCode\" export is defined" from inside the render — and a fixture
// that rejected with a bare `Error` would carry no `.code`, so the branch under test would never
// fire and the test would pass by rendering the generic failure instead.
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      // ES-16: LearningPage now also reads attention accounting; empty is its ordinary
      // state, and an omitted stub would throw inside a passive effect (see note above).
      workflowAttention: () => Promise.resolve({ scopes: [] }),
      evalFieldMetrics: () => Promise.resolve({ subjects: [] }),
      learningProposals: () => learningProposals(),
      learningStagingWeek: () => learningStagingWeek(),
      learningHealth: () => learningHealth(),
      judgeBench: () => judgeBench(),
      evalStudies: () => evalStudies(),
      retrievalBench: () => retrievalBench(),
      ablation: () => ablation(),
      identityReport: () => identityReport(),
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
const DOC = 'docs/research/learning-benchmark-protocol.md'

/** The binding a real run records — the exact shape `cell_provider.CellProviderBinding.to_dict()`
 *  emits, verified against a measured `report.json` from a run against a local Ollama. It carries
 *  the NAME of a key variable, never a value; `api_key_env: ''` is the unauthenticated-endpoint
 *  case, which is what a local runtime is. */
const BINDING = {
  use_case: 'chat',
  provider_name: 'LocalOllama',
  model: 'gemma4:12b',
  protocol: 'openai',
  base_url: 'http://127.0.0.1:11434/v1',
  api_key_env: '',
  max_tokens: null,
}

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
    tokens_recorded: true,
    unrecorded_spend_cells: 0,
    notes: [],
    ...over,
  }
}

function report(over: Partial<BenchmarkReport> = {}): BenchmarkReport {
  return {
    run_id: 'learnbench-20260826T000000Z',
    // Every report a run writes STATES its schema, and at 2 or above that is the panel's answer to
    // "was provenance recorded?" (#2562). A fixture WITHOUT it is a pre-provenance report — a
    // distinct case with its own test below, so the two are never conflated by a default here.
    report_schema: 2,
    tokens_recorded: true,
    unrecorded_spend_cells: 0,
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
    judgeBench.mockRejectedValue(new ApiError('no judge bench', 404, 'judge_bench_absent'))
    evalStudies.mockRejectedValue(new ApiError('no study', 404, 'study_absent'))
    retrievalBench.mockRejectedValue(new ApiError('no retrieval bench', 404, 'retrieval_absent'))
    ablation.mockRejectedValue(new ApiError('no ablation', 404, 'ablation_absent'))
    // LV-4's identity report: the page reads it, so a double that omits it throws inside a
    // passive effect and surfaces as failures about this panel instead.
    identityReport.mockRejectedValue(new Error('not under test'))
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
              // 🔁 Was `arm(s) skills_off produced no scored cell` — a sentence NO producer emits.
              // `harness/learning_verdict.py` composes this string and the count is the number of
              // arms with no scored cell, which is ONE in the ordinary case (a paired run where a
              // single arm produced nothing). A fixture inventing copy is the mirror image of the
              // usual defect, and it is worse here than elsewhere because the panel renders
              // `row.reason` VERBATIM, so this fixture is the only local record of what a reader
              // actually sees. Two-arm case asserted below.
              reason: 'arm skills_off produced no scored cell',
            })],
            measured_tasks: 0,
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('Nothing was measured')).toBeTruthy()
    // …and the reason reaches the reader unchanged, which is what makes the fixture above a
    // CONTRACT rather than decoration. Before this, no assertion read it at all.
    expect(screen.getByText('arm skills_off produced no scored cell')).toBeTruthy()
    expect(screen.getAllByText('not measured').length).toBeGreaterThan(0)
    // The vacuity assertion for the claim above: prove the forbidden strings are ABSENT, not
    // merely that a good string is present. A panel that drew both would pass a presence-only
    // check while publishing the fabricated zero.
    expect(screen.queryByText('+0.00')).toBeNull()
    expect(screen.queryByText('0.000')).toBeNull()
  })

  it('names BOTH arms, pluralised, when neither produced a scored cell', () => {
    // 🔑 THE BOUNDARY A ONE-ARM FIXTURE CANNOT CROSS. `arm(s)` was replaced by a conditional on
    // the arm count, so the singular and the plural are two different code paths in the producer
    // and a fixture fixed at one arm certifies only half of it. Both strings are what
    // `harness/learning_verdict.py` actually emits — verified by calling it at both boundaries.
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
              reason: 'arms skills_on, skills_off produced no scored cell',
            })],
            measured_tasks: 0,
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('arms skills_on, skills_off produced no scored cell')).toBeTruthy()
    // The hedge must not come back in either direction.
    expect(screen.queryByText(/arm\(s\)/)).toBeNull()
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
        error={new ApiError('no benchmark yet', 404, 'learning_benchmark_absent')}
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
      <BenchmarkPanel view={undefined} error={new ApiError('evals are off', 404, 'evals_disabled')} onRetry={() => {}} />,
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

  // ── 4. WHICH MODEL produced the table ──────────────────────────────────────
  //
  // The two run kinds — cells bound to a real `Provider:model`, and cells that resolve the
  // offline `scripted` replay — produce identically-shaped score tables. `provider_binding` is
  // the only field that tells them apart, and it went unread by every surface after ES-17 added
  // it. Publishing a table without its provenance is protocol §8's overclaim.

  it('names the model the cells actually called', () => {
    render(
      <BenchmarkPanel
        view={view({ report: report({ provider_binding: BINDING }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('Cells called LocalOllama:gemma4:12b')).toBeTruthy()
    expect(screen.getByText('http://127.0.0.1:11434/v1')).toBeTruthy()
    // The refusal copy of the OTHER two states must be absent, or this case would pass on a
    // panel that printed every state at once.
    expect(screen.queryByText(/not a model measurement/)).toBeNull()
    expect(screen.queryByText(/Provenance was not recorded/)).toBeNull()
  })

  it('says an unbound run measured no model, instead of publishing a bare table', () => {
    render(
      <BenchmarkPanel
        view={view({ report: report({ provider_binding: null }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('No model was bound — these cells called no model')).toBeTruthy()
    expect(screen.getByText('not a model measurement')).toBeTruthy()
    expect(screen.getByText(/resolved the offline/)).toBeTruthy()
    expect(screen.queryByText(/Cells called/)).toBeNull()
  })

  /** 🔑 THE TRAP THIS BLOCK EXISTS FOR. A report written before runs recorded provenance at all is
   *  a different claim from one that recorded "nothing was bound". Rendering the first as the second
   *  turns "we never recorded it" into "we recorded that no model ran" — this project's recurring
   *  absent-versus-declared-false failure at the one surface that publishes.
   *
   *  The DISCRIMINATOR changed with #2562 and this test changed with it. It used to be
   *  `'provider_binding' in report`, which worked and made the panel a second owner of the fact; it
   *  is now the schema the report STATES, which is what `REPORT_SCHEMA` was always for. So the
   *  legacy fixture is a report that states schema 1 — the value ES-17 left it at — rather than one
   *  with a key surgically removed. */
  it('distinguishes an UNRECORDED provenance from a recorded absence of one', () => {
    const legacy = report({ report_schema: 1 })
    // The fixture must genuinely lack the key too: a pre-ES-17 report had no `provider_binding` at
    // all, and the panel must reach the same conclusion from the SCHEMA rather than from that.
    expect('provider_binding' in legacy).toBe(false)
    const unrecorded = render(
      <BenchmarkPanel view={view({ report: legacy })} error={undefined} onRetry={() => {}} />,
    )
    expect(screen.getByText('Provenance was not recorded')).toBeTruthy()
    expect(screen.getByText('provenance unrecorded')).toBeTruthy()
    // …and it must NOT claim the run bound nothing, which is the collapse under test.
    expect(screen.queryByText('No model was bound — these cells called no model')).toBeNull()
    expect(screen.queryByText('not a model measurement')).toBeNull()
    unrecorded.unmount()

    // The other direction, so the case above is not merely asserting one string: a report that
    // DID record the absence renders the declared-false copy and not the unrecorded copy.
    render(
      <BenchmarkPanel
        view={view({ report: report({ provider_binding: null }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.queryByText('Provenance was not recorded')).toBeNull()
    expect(screen.getByText('No model was bound — these cells called no model')).toBeTruthy()
  })

  /** #2562's ruling, at the surface that carried the workaround: the schema is what decides, so a
   *  report that DOES state schema 2 and DOES carry `provider_binding: null` reads as recorded even
   *  though its value is null — and a report at schema 1 reads as unrecorded even if a stray key
   *  were present. The panel must not fall back to key presence for either. */
  it('reads provenance from the STATED schema, not from whether the key is present', () => {
    // Schema 1 with the key PRESENT: pre-provenance version, so still unrecorded. This is the case
    // a key-presence check gets wrong in the opposite direction.
    const stale = render(
      <BenchmarkPanel
        view={view({ report: report({ report_schema: 1, provider_binding: null }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('Provenance was not recorded')).toBeTruthy()
    expect(screen.queryByText('No model was bound — these cells called no model')).toBeNull()
    stale.unmount()

    // A report that states NO schema cannot certify what it recorded, and must not default to
    // "recorded" — that would be the same collapse with an extra step.
    render(
      <BenchmarkPanel
        view={view({ report: report({ report_schema: undefined, provider_binding: null }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('Provenance was not recorded')).toBeTruthy()
  })

  /** #2561 at the page: the pin now records BOTH facts, so the panel shows both under labels that
   *  say whose each is. `null` (unrecorded) must not render as `{}` (recorded: no model). */
  it('renders what the CELLS could reach beside what the HOME was bound to', () => {
    const bound = render(
      <BenchmarkPanel
        view={view({
          report: report({
            provider_binding: BINDING,
            pin: {
              model_fp: '5970c589da34',
              model_fingerprint: { chat: 'LocalOllama:gemma4:12b', reasoning: 'LocalOllama:gemma4:12b' },
              cell_model_fp: '709d87c51b62',
              cell_model_fingerprint: { chat: 'LocalOllama:gemma4:12b' },
            },
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    // Two lines, two labels. `could reach` is the cells' line; `not what the cells reached` is the
    // home's — and both are present, which is the point: two facts, two fields, two sentences.
    expect(screen.getByText(/could reach/)).toBeTruthy()
    expect(screen.getByText(/not what the cells reached/)).toBeTruthy()
    bound.unmount()

    // Recorded-and-no-model says so in words; unrecorded says "not recorded". Two states, two
    // strings, and neither is the other.
    const none = render(
      <BenchmarkPanel
        view={view({ report: report({ pin: { cell_model_fingerprint: {} } }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText(/no model at all/)).toBeTruthy()
    expect(screen.queryByText(/not recorded\./)).toBeNull()
    none.unmount()

    render(
      <BenchmarkPanel
        view={view({ report: report({ pin: { cell_model_fingerprint: null } }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText(/not recorded/)).toBeTruthy()
    expect(screen.queryByText(/no model at all/)).toBeNull()
  })

  /** #2540 at the page. `token_ratio: null` now has TWO causes and only one of them is "not
   *  measured": a run whose provider omitted its usage produced real scores and no spend match. */
  it('renders an unrecorded token ratio as "not recorded", not as "not measured" or 0.0000', () => {
    const unrecorded = render(
      <BenchmarkPanel
        view={view({
          report: report({
            tokens_recorded: false,
            unrecorded_spend_cells: 3,
            tasks: [
              task({
                verdict: 'tokens_unrecorded',
                verdict_class: 'tokens_unrecorded',
                token_ratio: null,
                tokens_recorded: false,
                unrecorded_spend_cells: 3,
                reason: '3 contributing cell(s) reported no token usage',
              }),
            ],
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    // The verdict is a STRING, not "not measured": the scores were measured.
    expect(screen.getByText('tokens_unrecorded')).toBeTruthy()
    expect(screen.queryByText('not measured')).toBeNull()
    // The ratio cell says which absence it is, and never 0.0000. Two matches: the run-level
    // sentence and the cell itself — the run-level one is what a reader sees before any row.
    expect(screen.getAllByText('not recorded').length).toBe(2)
    expect(screen.queryByText('0.0000')).toBeNull()
    // The delta survives, because it was measured.
    expect(screen.getByText('+12.50')).toBeTruthy()
    // And the run-level sentence appears once, before any row.
    expect(screen.getByText(/every token ratio below is/)).toBeTruthy()
    unrecorded.unmount()

    // VACUITY FLOOR, in both directions. A RECORDED zero ratio still prints 0.0000 — a real and
    // disqualifying observation that must not hide behind an absence…
    const zero = render(
      <BenchmarkPanel
        view={view({ report: report({ tasks: [task({ token_ratio: 0 })] }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('0.0000')).toBeTruthy()
    expect(screen.queryByText('not recorded')).toBeNull()
    zero.unmount()

    // …and an unassembled pair still says "not measured", which is the third state.
    render(
      <BenchmarkPanel
        view={view({
          report: report({ tasks: [task({ verdict: null, token_ratio: null, delta_points: null })] }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getAllByText('not measured').length).toBeGreaterThan(0)
    expect(screen.queryByText('not recorded')).toBeNull()
  })

  /** The pin is NOT provenance, and the panel must not let it read as provenance. `model_fp` /
   *  `model_fingerprint` come from the INVOKING home's `active_models.json`, so they name what the
   *  operator configured whether or not any of it crossed into a cell. MEASURED against the code
   *  on main: a bound run and an unbound run launched from the SAME bound home carry the identical
   *  `pin.model_fp` (`5970c589da34`), so a page that showed only the pin would present the unbound
   *  run as a real-model run. */
  it('labels the pin as the HOME’s binding, not as what the cells reached', () => {
    render(
      <BenchmarkPanel
        view={view({
          report: report({
            provider_binding: null,
            pin: { model_fp: '5970c589da34', model_fingerprint: { chat: 'LocalOllama:gemma4:12b' } },
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    // The home's ref is shown — it is real information — but under a label that says whose it is,
    // and beside the sentence saying the cells reached no model.
    expect(screen.getByText(/chat=LocalOllama:gemma4:12b/)).toBeTruthy()
    expect(screen.getByText(/not what the cells reached/)).toBeTruthy()
    expect(screen.getByText('No model was bound — these cells called no model')).toBeTruthy()
    expect(screen.queryByText('Cells called LocalOllama:gemma4:12b')).toBeNull()
  })

  // ── 5. §8: the verdict is published WITH its notes and its token ratio ─────
  //
  // "The verdict is published with its `notes`, its within-arm spread and its token ratio — never
  // the verdict alone." The spread rode the two arm columns; the notes and the ratio were dropped.
  // A `not_token_matched` row with neither is exactly "the verdict alone", and it is the row a
  // real run produces most often.

  it('publishes the note that says WHY a direction was withheld', () => {
    // The sentence `harness/learning_verdict.py` actually emits for this verdict, taken from the
    // measured run `learnbench-20260907T003211Z` (k=5, local Ollama, ratio 1.1365).
    const note = 'token spend differs by 13.7% (skills_on 42517 vs skills_off 37409), over the 5% '
      + 'match tolerance — the arms are not spend-matched, so no direction is offered.'
    render(
      <BenchmarkPanel
        view={view({
          report: report({
            tasks: [task({
              verdict: 'not_token_matched',
              verdict_class: 'not_token_matched',
              delta_points: 0,
              token_ratio: 1.1365,
              notes: [note],
            })],
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText(note)).toBeTruthy()
    expect(screen.getByText('1.1365')).toBeTruthy()
  })

  it('does not print the estimated/unobserved notes twice', () => {
    // The producer appends these in the same branch that sets the booleans, so the flag-driven
    // lines and the notes would both carry them. The flag lines stay (a producer that set the flag
    // and forgot the note must still warn a reader); the duplicate note is filtered.
    render(
      <BenchmarkPanel
        view={view({
          report: report({
            tasks: [task({
              spend_estimated: true,
              notes: ['tokens and dollars are ESTIMATED, not provider-reported'],
            })],
          }),
        })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getAllByText(/estimated, not provider-reported/i)).toHaveLength(1)
  })

  it('shows a zero token ratio as a zero, not as "not measured"', () => {
    // `Comparison.token_ratio` is 0.0 when the skills_off arm spent nothing. That is a real
    // observation that disqualifies the comparison, not an absent one, and the two must not
    // collapse — the whole point of the null/zero split everywhere else on this panel.
    const zero = render(
      <BenchmarkPanel
        view={view({ report: report({ tasks: [task({ token_ratio: 0 })] }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('0.0000')).toBeTruthy()
    zero.unmount()

    render(
      <BenchmarkPanel
        view={view({ report: report({ tasks: [task({ token_ratio: null })] }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.queryByText('0.0000')).toBeNull()
    expect(screen.getAllByText('not measured').length).toBeGreaterThan(0)
  })

  it('renders provenance for a report with no pin at all, without inventing one', () => {
    render(
      <BenchmarkPanel
        view={view({ report: report({ provider_binding: BINDING }) })}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('Cells called LocalOllama:gemma4:12b')).toBeTruthy()
    expect(screen.queryByText(/the pin records this/i)).toBeNull()
  })
})
