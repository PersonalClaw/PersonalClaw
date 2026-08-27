import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RetrievalBenchPanel } from './RetrievalBenchPanel'
import { api, ApiError, type RetrievalArmContribution, type RetrievalBenchView, type RetrievalMaskRow, type RetrievalStoreReport } from '../../lib/api'

/** ES-3's per-arm retrieval table, on what a table of measurements gets wrong.
 *
 *  1. An UNMEASURED metric must not render as a zero. The control mask retrieves nothing by
 *     construction, so `p_at_k: null` is its correct value; drawing "0.0%" would report the
 *     retriever as scoring zero.
 *  2. An arm with NO EXECUTOR never ran, so its zero delta must not read as "worthless".
 *  3. Saving an EMPTY hand-label selection is a real judgement and must be SUBMITTED — the
 *     bug being pinned is a card that drops it and lets the mined weak label survive.
 *  4. A 404 is the ORDINARY state, and it is the state where the card is still useful. */

function maskRow(over: Partial<RetrievalMaskRow> = {}): RetrievalMaskRow {
  return {
    mask: 'keyword+graph+vector',
    k: 5,
    p_at_k: 0.8,
    r_at_k: 0.7,
    queries: 10,
    scored_queries: 10,
    no_candidate_queries: 0,
    undefined_recall_queries: 0,
    ...over,
  }
}

function contribution(over: Partial<RetrievalArmContribution> = {}): RetrievalArmContribution {
  return {
    arm: 'graph',
    full_p_at_k: 0.8,
    without_p_at_k: 0.5,
    contribution_p: 0.3,
    full_r_at_k: 0.7,
    without_r_at_k: 0.6,
    contribution_r: 0.1,
    solo_p_at_k: 0.6,
    scored_queries: 10,
    verdict: 'enable',
    reasons: ['P@k contribution +0.3000 >= 0.02'],
    ...over,
  }
}

function report(over: Partial<RetrievalStoreReport> = {}): RetrievalStoreReport {
  return {
    run: 'retrieval-knowledge-20260825T000000Z',
    table: {
      store: 'knowledge',
      columns: [],
      rows: [maskRow({ mask: 'none', p_at_k: null, r_at_k: 0, scored_queries: 0, no_candidate_queries: 10 }), maskRow()],
      corpus_snapshot_ref: 'knowledge:16:aaa',
      benchmark_corpus_snapshot_ref: 'knowledge:16:aaa',
      corpus_drifted: false,
      arm_executors: { keyword: true, graph: true, vector: true },
      floors: { min_arm_contribution: 0.02, min_scored_queries: 5 },
    },
    contributions: [contribution()],
    benchmark: null,
    ...over,
  }
}

function view(over: Partial<RetrievalBenchView> = {}): RetrievalBenchView {
  return {
    stores: { knowledge: report(), memory: report({ run: '', table: null, contributions: null }) },
    arms: ['keyword', 'graph', 'vector'],
    masks: ['none', 'keyword+graph+vector'],
    control_mask: 'none',
    arm_verdicts: ['enable', 'hold', 'unmeasured'],
    k: 5,
    floors: { min_arm_contribution: 0.02, min_scored_queries: 5 },
    ...over,
  }
}

describe('the per-arm retrieval ablation table', () => {
  it('renders the control row as an undefined precision but a real zero recall', () => {
    // The asymmetry, read off the DOM row rather than off the whole panel: P@k of no
    // candidates is 0/0 and must say "not measured"; R@k of no candidates is a genuine 0%
    // (the retriever found none of the known answers) and must NOT say "not measured".
    render(<RetrievalBenchPanel bench={view()} error={undefined} onRetry={() => {}} />)
    const control = screen.getByRole('cell', { name: 'none' }).closest('tr')!
    const cells = [...control.querySelectorAll('td')].map((td) => td.textContent)
    expect(cells[1]).toBe('not measured')
    expect(cells[2]).toBe('0.0%')
  })

  it('shows BOTH stores, and says so when one has never been benchmarked', () => {
    render(<RetrievalBenchPanel bench={view()} error={undefined} onRetry={() => {}} />)
    expect(screen.getByText('knowledge')).toBeTruthy()
    expect(screen.getByText('memory')).toBeTruthy()
    expect(screen.getByText(/Not measured yet/)).toBeTruthy()
  })

  it('warns that an arm with no executor never ran', () => {
    render(<RetrievalBenchPanel
      bench={view({
        stores: {
          knowledge: report({
            table: { ...report().table!, arm_executors: { keyword: true, graph: true, vector: false } },
            contributions: [contribution({
              arm: 'vector', contribution_p: 0, verdict: 'unmeasured',
              reasons: ['no executor: the arm could not run in this process (no embedder?)'],
            })],
          }),
        },
      })}
      error={undefined} onRetry={() => {}} />)
    expect(screen.getByText(/No executor for vector/)).toBeTruthy()
    expect(screen.getByText(/no executor: the arm could not run/)).toBeTruthy()
  })

  it('renders an absent delta as "no delta", distinct from a delta of zero', () => {
    render(<RetrievalBenchPanel
      bench={view({
        stores: {
          knowledge: report({
            contributions: [
              contribution({ arm: 'graph', contribution_p: null, verdict: 'unmeasured', reasons: ['no delta'] }),
              contribution({ arm: 'keyword', contribution_p: 0, verdict: 'hold', reasons: ['+0.0000 < 0.02'] }),
            ],
          }),
        },
      })}
      error={undefined} onRetry={() => {}} />)
    const graph = screen.getByRole('cell', { name: 'graph' }).closest('tr')!
    expect([...graph.querySelectorAll('td')][1].textContent).toBe('no delta')
    const keyword = screen.getByRole('cell', { name: 'keyword' }).closest('tr')!
    expect([...keyword.querySelectorAll('td')][1].textContent).toBe('+0.0pp')
  })

  it('names the ground truth that produced the numbers, and unlabelled queries too', () => {
    // §5.2 names three sources and this harness mines a SUBSTITUTE for one of them, so the
    // mix is part of reading the score. Counted server-side; rendered verbatim here.
    render(<RetrievalBenchPanel
      bench={view({
        stores: {
          knowledge: report({
            table: {
              ...report().table!,
              qrels_sources: { '': 1, hand_label: 3, 'mined:intent_outcomes': 10 },
            },
          }),
        },
      })}
      error={undefined} onRetry={() => {}} />)
    const line = screen.getByText(/Ground truth:/)
    expect(line.textContent).toContain('10')
    expect(line.textContent).toContain('mined:intent_outcomes')
    expect(line.textContent).toContain('hand_label')
    expect(line.textContent).toContain('unlabelled')
  })

  it('says the provenance is unstated when a run recorded none — never "0 from every source"', () => {
    const table = { ...report().table! }
    delete table.qrels_sources
    render(<RetrievalBenchPanel
      bench={view({ stores: { knowledge: report({ table }) } })}
      error={undefined} onRetry={() => {}} />)
    expect(screen.getByText(/Ground truth: not stated by this run/)).toBeTruthy()
  })

  it('warns when the corpus drifted, because R@k changed denominators', () => {
    render(<RetrievalBenchPanel
      bench={view({
        stores: { knowledge: report({ table: { ...report().table!, corpus_drifted: true } }) },
      })}
      error={undefined} onRetry={() => {}} />)
    expect(screen.getByText(/corpus changed since these labels/)).toBeTruthy()
  })

  it('renders "no benchmark yet" as guidance AND still offers the label card', () => {
    render(<RetrievalBenchPanel bench={undefined} error={new ApiError('No retrieval benchmark has run yet. Run `personalclaw retrieval-eval` to score both stores.', 404, 'retrieval_absent')} onRetry={() => {}} />)
    expect(screen.getByText(/personalclaw retrieval-eval/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Label knowledge/ })).toBeTruthy()
    expect(screen.queryByText(/Retry/)).toBeNull()
  })

  it('surfaces a REAL failure instead of swallowing it', () => {
    render(<RetrievalBenchPanel bench={undefined} error={new Error('boom')} onRetry={() => {}} />)
    expect(screen.getByText(/retrieval benchmark/)).toBeTruthy()
    expect(screen.queryByText(/personalclaw retrieval-eval/)).toBeNull()
  })
})

describe('the hand-label card', () => {
  const card = {
    store: 'knowledge',
    benchmark: 'retrieval-knowledge',
    candidates_per_query: 8,
    labelled: 0,
    mined: 2,
    queries: [
      { query: 'q one', source: 'mined:intent_outcomes', already_relevant: ['id-a'], candidates: ['id-a', 'id-b'] },
      { query: 'q two', source: 'mined:intent_outcomes', already_relevant: [], candidates: [] },
    ],
  }

  it('submits an EMPTY selection, so unticking everything overrules the mined label', async () => {
    const saved = vi.fn().mockResolvedValue({ ok: true, store: 'knowledge', queries: 2, hand_labelled: 2 })
    vi.spyOn(api, 'retrievalLabelCard').mockResolvedValue(card)
    vi.spyOn(api, 'saveRetrievalLabels').mockImplementation(saved)
    render(<RetrievalBenchPanel bench={view()} error={undefined} onRetry={() => {}} />)

    await userEvent.click(screen.getByRole('button', { name: /Label knowledge/ }))
    // The card seeds from what is already labelled, so an untouched card round-trips.
    const seeded = await screen.findByRole('checkbox', { name: /id-a/ })
    expect((seeded as HTMLInputElement).checked).toBe(true)
    await userEvent.click(seeded)

    await userEvent.click(screen.getByRole('button', { name: /Save labels/ }))
    await waitFor(() => expect(saved).toHaveBeenCalled())
    // EVERY query on the card is submitted, including the two now-empty ones. Omitting them
    // is what lets a mined weak label the human overruled quietly survive.
    expect(saved).toHaveBeenCalledWith('knowledge', { 'q one': [], 'q two': [] })
    vi.restoreAllMocks()
  })

  it('says so when the retriever returned nothing to mark', async () => {
    vi.spyOn(api, 'retrievalLabelCard').mockResolvedValue(card)
    render(<RetrievalBenchPanel bench={view()} error={undefined} onRetry={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: /Label knowledge/ }))
    expect(await screen.findByText(/returned nothing for this query/)).toBeTruthy()
    vi.restoreAllMocks()
  })

  it('surfaces a card-read failure rather than showing an empty card', async () => {
    vi.spyOn(api, 'retrievalLabelCard').mockRejectedValue(new Error('card_unavailable'))
    render(<RetrievalBenchPanel bench={view()} error={undefined} onRetry={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: /Label knowledge/ }))
    expect(await screen.findByText(/card_unavailable/)).toBeTruthy()
    vi.restoreAllMocks()
  })
})
