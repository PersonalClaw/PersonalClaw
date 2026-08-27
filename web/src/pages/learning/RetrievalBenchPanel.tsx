import { useState } from 'react'
import { Search, ShieldAlert } from 'lucide-react'
import { LoadError } from '../../ui/ListScaffold'
import { Button } from '../../ui/Button'
import { Checkbox } from '../../ui/forms'
import { InlineError } from '../../ui/InlineError'
import { fvs } from '../../design/fontWeight'
import { api, hasApiCode, type RetrievalArmContribution, type RetrievalBenchView, type RetrievalLabelCard, type RetrievalMaskRow, type RetrievalStoreReport } from '../../lib/api'
import { EvalsOff } from './EvalsOff'

/** Per-arm P@k/R@k for both retrieval stores (EVALUATION-SUBSTRATE §5 / ES-3).
 *
 *  The question this answers with numbers: does the graph arm earn its complexity, does
 *  vector beat keyword on THIS user's corpus, and what would a reranker have to beat. The
 *  two stores are rendered as two tables because §5.1 runs them separately and never shares
 *  a corpus — a merged table would be the one shape that boundary forbids.
 *
 *  **Nothing is re-decided here.** Each arm's marginal contribution, its verdict and the
 *  floors it was compared against arrive computed. A UI that re-derived "is this arm worth
 *  enabling" would eventually disagree with the runner.
 *
 *  **`null` renders as "not measured", never as a zero.** A mask that retrieved NOTHING has
 *  no precision (0/0); drawing 0.00 for it would report a measured failure where the truth
 *  is "there was nothing to be precise about". The `no candidates` column is how many
 *  queries that was, so the absence is legible rather than merely missing. */
export function RetrievalBenchPanel({ bench, error, onRetry }: {
  bench: RetrievalBenchView | undefined
  error: unknown
  onRetry: () => void
}) {
  // A 404 is the ordinary state — the substrate is off, or no benchmark has run — so both render
  // as guidance rather than as a failure. Only the second offers the labelling card: hand labels
  // are read BY a run, so collecting them while the substrate is off would bank work for a
  // machine that has been told not to start.
  if (bench === undefined && error) {
    if (hasApiCode(error, 'evals_disabled')) {
      return (
        <section className="flex flex-col gap-s" aria-labelledby="retrieval-bench-heading">
          <Heading />
          <EvalsOff what="retrieval benchmark" />
        </section>
      )
    }
    if (hasApiCode(error, 'retrieval_absent')) {
      return (
        <section className="flex flex-col gap-s" aria-labelledby="retrieval-bench-heading">
          <Heading />
          <p className="text-on-surface-low text-[0.8125rem]">
            No retrieval benchmark has run yet. Run{' '}
            <code className="text-on-surface-var">personalclaw retrieval-eval</code> to score
            both stores. It reads knowledge.db and memory.db and writes to neither — a run
            that touched either one refuses to report.
          </p>
          <LabelCards stores={['knowledge', 'memory']} />
        </section>
      )
    }
    return <LoadError what="retrieval benchmark" error={error} onRetry={onRetry} />
  }
  if (!bench) return null

  const stores = Object.keys(bench.stores)

  return (
    <section className="flex flex-col gap-m" aria-labelledby="retrieval-bench-heading">
      <Heading />

      <p className="text-on-surface-low text-[0.75rem]">
        An arm is worth enabling when dropping it costs at least{' '}
        {fmt(bench.floors.min_arm_contribution)} P@{bench.k}, measured over at least{' '}
        {bench.floors.min_scored_queries} scored queries. Below that population the verdict is{' '}
        <em>unmeasured</em> rather than a number — a delta computed from four queries is a
        number, not evidence. The <code className="text-on-surface-var">{bench.control_mask}</code>{' '}
        row is the control: every arm off, so it must retrieve nothing. If it ever reports a
        score, the mask stopped reaching the retriever and every delta above it is noise.
      </p>

      {stores.map((store) => (
        <StoreReport key={store} store={store} report={bench.stores[store]} k={bench.k} />
      ))}

      <LabelCards stores={stores} />
    </section>
  )
}

function Heading() {
  return (
    <div className="flex flex-wrap items-center gap-s">
      <Search size={16} className="text-on-surface-var" />
      <h2 id="retrieval-bench-heading" data-type="title-m" className="text-on-surface">
        Retrieval arms
      </h2>
    </div>
  )
}

function StoreReport({ store, report, k }: { store: string; report: RetrievalStoreReport; k: number }) {
  // A store present in the payload but never benchmarked is a real state, and saying so beats
  // omitting the section: an absent heading reads as "this store has no arms to measure".
  if (!report.run || !report.table) {
    return (
      <div className="flex flex-col gap-xs">
        <span data-type="title-s" className="text-on-surface">{store}</span>
        <p className="text-on-surface-low text-[0.8125rem]">
          Not measured yet. <code className="text-on-surface-var">personalclaw retrieval-eval
          --store {store}</code>
        </p>
      </div>
    )
  }
  const dead = Object.entries(report.table.arm_executors ?? {})
    .filter(([, live]) => !live)
    .map(([arm]) => arm)
  return (
    <div className="flex flex-col gap-xs">
      <div className="flex flex-wrap items-baseline gap-s">
        <span data-type="title-s" className="text-on-surface">{store}</span>
        <span className="text-on-surface-low text-[0.75rem]">{report.run}</span>
      </div>

      {report.table.corpus_drifted && (
        <Warn>
          The corpus changed since these labels were made, so R@{k}'s denominator is no longer
          the one that was judged. Re-label, or read the recall column as indicative only.
        </Warn>
      )}
      {dead.length > 0 && (
        <Warn>
          No executor for {dead.join(', ')} — that arm never ran, so its zero delta says nothing
          about the arm. Bind an embedding model on{' '}
          <a className="underline" href="#/settings/models">Settings → Models</a> to measure the
          vector arm.
        </Warn>
      )}

      <Provenance sources={report.table.qrels_sources} />

      <div className="overflow-x-auto rounded-lg bg-surface-container">
        <table className="w-full text-[0.75rem]">
          <caption className="sr-only">
            Precision and recall at {k} for the {store} store, per arm mask
          </caption>
          <thead>
            <tr className="text-on-surface-low">
              <th scope="col" className="px-m py-s text-left">Arms</th>
              <th scope="col" className="px-m py-s text-right">P@{k}</th>
              <th scope="col" className="px-m py-s text-right">R@{k}</th>
              <th scope="col" className="px-m py-s text-right">Scored</th>
              <th scope="col" className="px-m py-s text-right">No candidates</th>
            </tr>
          </thead>
          <tbody>
            {report.table.rows.map((row) => <MaskRow key={row.mask} row={row} />)}
          </tbody>
        </table>
      </div>

      {report.contributions && report.contributions.length > 0 && (
        <div className="overflow-x-auto rounded-lg bg-surface-container">
          <table className="w-full text-[0.75rem]">
            <caption className="sr-only">
              Per-arm marginal contribution for the {store} store
            </caption>
            <thead>
              <tr className="text-on-surface-low">
                <th scope="col" className="px-m py-s text-left">Arm</th>
                <th scope="col" className="px-m py-s text-right">ΔP@{k}</th>
                <th scope="col" className="px-m py-s text-right">ΔR@{k}</th>
                <th scope="col" className="px-m py-s text-right">Alone</th>
                <th scope="col" className="px-m py-s text-left">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {report.contributions.map((c) => <ContributionRow key={c.arm} contribution={c} />)}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/** Which labels produced these numbers. A P@5 with no visible ground-truth provenance is a
 *  number without a claim attached — and this harness mines a SUBSTITUTE for one of §5.2's
 *  named sources, so the mix is part of reading the score. An absent census (a run written
 *  before it existed) says so, and never renders as "0 queries from every source". */
function Provenance({ sources }: { sources?: Record<string, number> }) {
  if (!sources) {
    return (
      <p className="text-on-surface-low text-[0.75rem]">
        Ground truth: not stated by this run — re-run to record which labels it scored.
      </p>
    )
  }
  const entries = Object.entries(sources).filter(([, count]) => count > 0)
  if (entries.length === 0) return null
  return (
    <p className="text-on-surface-low text-[0.75rem]">
      Ground truth:{' '}
      {entries.map(([source, count], index) => (
        <span key={source}>
          {index > 0 ? ' · ' : ''}
          {count} <code className="text-on-surface-var">{source || 'unlabelled'}</code>
        </span>
      ))}
    </p>
  )
}

function MaskRow({ row }: { row: RetrievalMaskRow }) {
  return (
    <tr className="border-t border-outline-variant/30">
      <td className="px-m py-s text-on-surface">{row.mask}</td>
      <td className="px-m py-s text-right text-on-surface-var">{fmtRate(row.p_at_k)}</td>
      <td className="px-m py-s text-right text-on-surface-var">{fmtRate(row.r_at_k)}</td>
      <td className="px-m py-s text-right text-on-surface-var">
        {row.scored_queries} of {row.queries}
      </td>
      <td className="px-m py-s text-right text-on-surface-var">{row.no_candidate_queries}</td>
    </tr>
  )
}

function ContributionRow({ contribution }: { contribution: RetrievalArmContribution }) {
  const enabled = contribution.verdict === 'enable'
  return (
    <tr className="border-t border-outline-variant/30 align-top">
      <td className="px-m py-s text-on-surface">{contribution.arm}</td>
      <td className="px-m py-s text-right text-on-surface-var">
        {fmtDelta(contribution.contribution_p)}
      </td>
      <td className="px-m py-s text-right text-on-surface-var">
        {fmtDelta(contribution.contribution_r)}
      </td>
      <td className="px-m py-s text-right text-on-surface-var">
        {fmtRate(contribution.solo_p_at_k)}
      </td>
      <td className="px-m py-s">
        {enabled ? (
          <span className="text-on-surface" style={fvs(600)}>enable</span>
        ) : (
          <span
            className="inline-flex w-fit items-center gap-1.5 rounded-pill px-m h-6"
            style={{
              background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)',
              color: 'var(--color-warn)',
            }}
          >
            <ShieldAlert size={12} /> {contribution.verdict}
          </span>
        )}
        {/* Every reason, not the first: "unmeasured" covers no delta, low power AND a dead
            arm, and they send the reader three different places. */}
        <ul className="mt-1 flex flex-col gap-0.5 text-on-surface-low">
          {contribution.reasons.map((reason) => <li key={reason}>{reason}</li>)}
        </ul>
      </td>
    </tr>
  )
}

/** §5.2's hand-labeling pass, as the 10-minute card it was designed to be.
 *
 *  Mined weak labels cover the tail; the head queries need a human to say which results
 *  actually answer them. Saving an EMPTY selection is a real judgement ("none of these
 *  answer it") and is submitted as such — dropping it would let the mined label the user
 *  just overruled quietly survive, which is the whole thing this pass exists to fix. */
function LabelCards({ stores }: { stores: string[] }) {
  const [store, setStore] = useState('')
  const [card, setCard] = useState<RetrievalLabelCard | null>(null)
  const [picked, setPicked] = useState<Record<string, string[]>>({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [saved, setSaved] = useState('')

  async function open(next: string) {
    setBusy(true); setErr(''); setSaved(''); setStore(next); setCard(null)
    try {
      const loaded = await api.retrievalLabelCard(next)
      setCard(loaded)
      // Seeded from what is already labelled, so an unchanged card round-trips to the same
      // qrels instead of silently clearing every existing positive.
      setPicked(Object.fromEntries(loaded.queries.map((q) => [q.query, [...q.already_relevant]])))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    if (!card) return
    setBusy(true); setErr(''); setSaved('')
    try {
      const result = await api.saveRetrievalLabels(
        card.store,
        // EVERY query on the card, including the ones with an empty array.
        Object.fromEntries(card.queries.map((q) => [q.query, picked[q.query] ?? []])),
      )
      setSaved(`Saved ${result.hand_labelled} hand-labelled of ${result.queries} queries.`)
      setCard(null)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function toggle(query: string, id: string) {
    setPicked((prev) => {
      const current = prev[query] ?? []
      return {
        ...prev,
        [query]: current.includes(id) ? current.filter((x) => x !== id) : [...current, id],
      }
    })
  }

  return (
    <div className="flex flex-col gap-s rounded-lg bg-surface-container px-l py-m">
      <span data-type="title-s" className="text-on-surface">Label a few queries</span>
      <p className="text-on-surface-low text-[0.8125rem]">
        The benchmark's ground truth is mined from what you actually used. Ten minutes of
        hand-labelling on the head queries is what makes the numbers above worth reading.
      </p>
      <div className="flex flex-wrap items-center gap-s">
        {stores.map((s) => (
          <Button key={s} variant="ghost" disabled={busy} onClick={() => open(s)}>
            Label {s}
          </Button>
        ))}
      </div>
      {err && <InlineError icon onDismiss={() => setErr('')}>{err}</InlineError>}
      {saved && <p className="text-on-surface-low text-[0.8125rem]">{saved}</p>}
      {card && (
        <div className="flex flex-col gap-m">
          <p className="text-on-surface-low text-[0.75rem]">
            {card.store}: {card.labelled} hand-labelled, {card.mined} mined. Tick every result
            that answers the query. Ticking none is a real answer — save it.
          </p>
          {card.queries.map((entry) => (
            <fieldset key={entry.query} className="flex flex-col gap-xs">
              <legend className="text-on-surface text-[0.8125rem]" style={fvs(600)}>
                {entry.query}
              </legend>
              {entry.candidates.length === 0 ? (
                <p className="text-on-surface-low text-[0.75rem]">
                  The retriever returned nothing for this query — there is nothing to mark, and
                  that itself is the measurement.
                </p>
              ) : entry.candidates.map((id) => (
                <label key={id} className="flex items-center gap-s text-on-surface-var text-[0.75rem]">
                  <Checkbox
                    checked={(picked[entry.query] ?? []).includes(id)}
                    onChange={() => toggle(entry.query, id)}
                    ariaLabel={id}
                  />
                  <span className="break-all">{id}</span>
                </label>
              ))}
            </fieldset>
          ))}
          <div className="flex flex-wrap items-center gap-s">
            <Button disabled={busy} onClick={save}>Save labels</Button>
            <Button variant="ghost" disabled={busy} onClick={() => { setCard(null); setStore('') }}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {busy && !card && <p className="text-on-surface-low text-[0.75rem]">Reading {store}…</p>}
    </div>
  )
}

function Warn({ children }: { children: React.ReactNode }) {
  return (
    <p
      className="flex items-start gap-1.5 rounded-lg px-m py-s text-[0.75rem]"
      style={{
        background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)',
        color: 'var(--color-warn)',
      }}
    >
      <ShieldAlert size={12} className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </p>
  )
}

function fmt(value: number | undefined): string {
  return value === undefined ? '—' : String(value)
}

/** `null` is UNMEASURED — the one value that must never render as 0%. A mask that retrieved
 *  nothing has no precision to report, and 0% would say the opposite of what happened. */
function fmtRate(value: number | null): string {
  return value === null ? 'not measured' : `${(value * 100).toFixed(1)}%`
}

/** A signed delta. `null` means one of the two differenced masks scored nothing, so no delta
 *  exists — distinct from a delta that happens to be zero. */
function fmtDelta(value: number | null): string {
  if (value === null) return 'no delta'
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}pp`
}
