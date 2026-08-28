import { useEffect, useMemo, useState } from 'react'
import { CircleCheck, CircleHelp, Clock, DollarSign, ScanSearch, ShieldQuestion, Split, TriangleAlert } from 'lucide-react'
import { SidePanel } from '../../ui/SidePanel'
import { Segmented } from '../../ui/Segmented'
import { Skeleton } from '../../ui/ListScaffold'
import { InlineError } from '../../ui/InlineError'
import { api, type WorkflowIntrospection, type WorkflowTimelineRow } from '../../lib/api'
import { fmtElapsed } from './workflowMeta'
import { runCostText } from '../../lib/runCost'

/** The cockpit's introspection panel: the nine questions §6.4 promotes to Success Criteria
 *  (WORK-CONTAINERS R6 — criteria 6 & 8).
 *
 *  The backend module behind this (`workflows/introspection.py`) was fully written and fully
 *  tested but consumed by NOTHING — no route, no surface — so it answered none of the nine
 *  questions it exists to answer. This panel is the consumer.
 *
 *  **One fetch, not nine.** `checklist_gaps` is a property of the whole payload: the backend can
 *  only name a question its own response cannot answer if it assembles the response in full.
 *  Nine requests would let this panel render eight answers and never learn the ninth was missing
 *  — the precise failure the checklist exists to prevent.
 *
 *  **A gap is SHOWN, never hidden.** A non-empty `checklist_gaps` is a backend hole this panel
 *  cannot close by rendering harder, so it renders as a warning rather than as blank space.
 *  Silence would make a broken surface look complete, which is the whole thing R6 is against.
 *
 *  **An empty answer is an answer.** "Nothing is blocked" is information; rendering it as an
 *  absence would make a healthy idle run look broken. So each section states its empty case in
 *  words rather than collapsing. */
export function IntrospectPanel({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [data, setData] = useState<WorkflowIntrospection | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'summary' | 'timeline' | 'proof'>('summary')

  useEffect(() => {
    let live = true
    setLoading(true)
    setError(null)
    api
      .workflowRunIntrospect(runId)
      .then((body) => { if (live) setData(body) })
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : 'could not read this run') })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [runId])

  // Every gate that earned a said-no warning, across the template's runs. Derived here rather
  // than in the render so the badge and the risk section cannot disagree about how many there are.
  const fakeChecks = useMemo(
    () => Object.values(data?.gates ?? {}).filter((g) => !!g.fake_check_warning),
    [data],
  )

  return (
    <SidePanel title="Introspection" icon={<ScanSearch size={18} />} onClose={onClose} fillHeight>
      {loading ? (
        <Skeleton />
      ) : error ? (
        <InlineError>{error}</InlineError>
      ) : !data ? (
        <p className="text-on-surface-low text-[0.8125rem]">This run could not be introspected.</p>
      ) : (
        <div className="flex flex-col gap-l">
          {/* A named gap comes FIRST. It says this surface is incomplete, which changes how a
              reader should weigh everything below it. */}
          {data.checklist_gaps.length > 0 && (
            <div className="flex items-start gap-xs rounded-lg bg-surface-high p-s text-[0.75rem]">
              <TriangleAlert size={14} className="text-warning shrink-0" aria-hidden />
              <div>
                <p className="text-on-surface">
                  {data.checklist_gaps.length} of 9 questions cannot be answered from this run's state:
                </p>
                <ul className="mt-2xs flex flex-col gap-2xs text-on-surface-low">
                  {data.checklist_gaps.map((gap) => <li key={gap}>{gap}</li>)}
                </ul>
              </div>
            </div>
          )}

          <Segmented
            ariaLabel="Introspection view"
            value={tab}
            onChange={(v) => setTab(v as 'summary' | 'timeline' | 'proof')}
            options={[
              { key: 'summary', label: 'Summary' },
              { key: 'timeline', label: 'Timeline' },
              { key: 'proof', label: 'Proof' },
            ]}
          />

          {tab === 'summary' && (
            <>
              {/* Q6 "what is costing money" — the RunStats cost/latency strip. first_byte_ms is
                  shown beside total duration, never merged into it: one is what a watching user
                  feels, the other is what a scheduler budgets. */}
              <section className="flex flex-col gap-xs">
                <h3 className="flex items-center gap-2xs text-on-surface text-[0.8125rem] font-medium">
                  <DollarSign size={13} aria-hidden /> Cost and latency
                </h3>
                <dl className="grid grid-cols-2 gap-xs text-[0.75rem] sm:grid-cols-4">
                  {/* `~` for the same reason `runCostText` carries it: this run's cost is derived
                      from a price table, not billed. One panel rendering the SAME number as
                      "~$0.0342" in one place and "$0.0342" in another would read as two different
                      claims about the same dollar. The tilde is the marker everywhere; the
                      "what is costing money" answer below is where it is spelled out once. */}
                  <Stat label="Cost (est.)" value={`~$${data.stats.cost_usd.toFixed(4)}`} />
                  <Stat label="Tokens" value={data.stats.tokens.toLocaleString()} />
                  <Stat label="Duration" value={fmtElapsed(data.stats.duration_secs)} />
                  <Stat label="To first output" value={`${Math.round(data.stats.first_byte_ms)} ms`} />
                  <Stat label="Steps done" value={String(data.stats.steps_completed)} />
                  <Stat label="Steps failed" value={String(data.stats.steps_failed)} />
                  <Stat label="Cache hits" value={`${Math.round(data.stats.cache_hit_rate * 100)}%`} />
                  <Stat label="Models" value={data.stats.models.join(', ') || 'none recorded'} />
                </dl>
              </section>

              {/* Q6 continued — the template p50/p95 card. p50 answers "what does this usually
                  cost", p95 "what is the bad case". Never a mean: one runaway run moves it and
                  nothing tells you whether the typical run is cheap. */}
              <section className="flex flex-col gap-xs">
                <h3 className="flex items-center gap-2xs text-on-surface text-[0.8125rem] font-medium">
                  <Clock size={13} aria-hidden /> Template: {data.template_card.template || 'unnamed'}
                </h3>
                <p className="text-on-surface-low text-[0.75rem]">
                  Across {data.template_card.runs} run{data.template_card.runs === 1 ? '' : 's'}
                  {data.template_card.runs === 1 ? ' — p50 and p95 are that one run' : ''}
                </p>
                <dl className="grid grid-cols-2 gap-xs text-[0.75rem] sm:grid-cols-4">
                  <Stat label="Cost p50" value={`$${data.template_card.cost_p50.toFixed(4)}`} />
                  <Stat label="Cost p95" value={`$${data.template_card.cost_p95.toFixed(4)}`} />
                  <Stat label="Duration p50" value={fmtElapsed(data.template_card.duration_p50)} />
                  <Stat label="Duration p95" value={fmtElapsed(data.template_card.duration_p95)} />
                </dl>
                <p className="text-on-surface-low text-[0.75rem]">
                  {Math.round(data.template_card.failure_rate * 100)}% of these runs had a failed step
                </p>
              </section>

              {/* Q9 "were the checks that passed real checks" — the said-no fake-check badge. It
                  renders the backend's warning string verbatim, because the sample rule that earns
                  the warning lives there and a second phrasing here would drift from it. */}
              <section className="flex flex-col gap-xs">
                <h3 className="flex items-center gap-2xs text-on-surface text-[0.8125rem] font-medium">
                  <ShieldQuestion size={13} aria-hidden /> Gates
                </h3>
                {Object.keys(data.gates).length === 0 ? (
                  <p className="text-on-surface-low text-[0.75rem]">
                    This run resolved no gates, so there is nothing to judge.
                  </p>
                ) : (
                  <ul className="flex flex-col gap-2xs text-[0.75rem]">
                    {Object.values(data.gates).map((g) => (
                      <li key={g.node_id} className="flex flex-col gap-2xs rounded-lg bg-surface-high p-s">
                        <span className="flex flex-wrap items-center gap-xs">
                          <span className="font-mono text-on-surface">{g.node_id}</span>
                          <span className="text-on-surface-low tabular-nums">
                            {g.passes} passed · {g.rejects} rejected
                            {g.retries_consumed ? ` · ${g.retries_consumed} retries` : ''}
                          </span>
                          {g.fake_check_warning ? (
                            <span className="text-warning inline-flex items-center gap-2xs">
                              <TriangleAlert size={12} aria-hidden /> never said no
                            </span>
                          ) : null}
                        </span>
                        {g.fake_check_warning ? (
                          <span className="text-on-surface-low">{g.fake_check_warning}</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {/* PP-8 — per-`branch` case and per-judge verdict distributions across the template,
                  beside the said-no table because a routing decision and a gate decision are the
                  same kind of edge. Each warning renders the backend's SAMPLE-GATED string verbatim:
                  the rule that earns it ("dead across a real sample", "one verdict over many calls")
                  lives there, and a second phrasing here would drift from it. */}
              <section className="flex flex-col gap-xs">
                <h3 className="flex items-center gap-2xs text-on-surface text-[0.8125rem] font-medium">
                  <Split size={13} aria-hidden /> Edges
                </h3>
                {Object.keys(data.edges.branches).length === 0 &&
                Object.keys(data.edges.judges).length === 0 ? (
                  <p className="text-on-surface-low text-[0.75rem]">
                    This template has no branch or judge edges, so there is no routing to distribute.
                  </p>
                ) : (
                  <ul className="flex flex-col gap-2xs text-[0.75rem]">
                    {Object.values(data.edges.branches).map((b) => (
                      <li key={`b:${b.path}`} className="flex flex-col gap-2xs rounded-lg bg-surface-high p-s">
                        <span className="flex flex-wrap items-center gap-xs">
                          <span className="font-mono text-on-surface">{b.path}</span>
                          <span className="text-on-surface-low tabular-nums">
                            {Object.entries(b.cases)
                              .map(([label, n]) => `${label}: ${n}`)
                              .join(' · ') || 'no cases seen'}
                            {` · ${b.routed_runs} routed`}
                          </span>
                          {b.degenerate_warning || b.never_taken.length > 0 ? (
                            <span className="text-warning inline-flex items-center gap-2xs">
                              <TriangleAlert size={12} aria-hidden />{' '}
                              {b.degenerate_warning ? 'does no work' : 'dead case'}
                            </span>
                          ) : null}
                        </span>
                        {b.degenerate_warning ? (
                          <span className="text-on-surface-low">{b.degenerate_warning}</span>
                        ) : null}
                        {b.never_taken.length > 0 ? (
                          <span className="text-on-surface-low">
                            Never taken: {b.never_taken.join(', ')}
                          </span>
                        ) : null}
                      </li>
                    ))}
                    {Object.values(data.edges.judges).map((j) => (
                      <li key={`j:${j.node_id}`} className="flex flex-col gap-2xs rounded-lg bg-surface-high p-s">
                        <span className="flex flex-wrap items-center gap-xs">
                          <span className="font-mono text-on-surface">{j.node_id}</span>
                          <span className="text-on-surface-low tabular-nums">
                            {Object.entries(j.verdicts)
                              .map(([v, n]) => `${v || '(none)'}: ${n}`)
                              .join(' · ')}
                          </span>
                          {j.degenerate_warning ? (
                            <span className="text-warning inline-flex items-center gap-2xs">
                              <TriangleAlert size={12} aria-hidden /> one verdict
                            </span>
                          ) : null}
                        </span>
                        {j.degenerate_warning ? (
                          <span className="text-on-surface-low">{j.degenerate_warning}</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {/* Q1-Q5, Q7, Q8. Each states its empty case in words: "nothing is blocked" is an
                  answer, and blank space would read as a surface that failed to load. */}
              <section className="flex flex-col gap-xs">
                <h3 className="text-on-surface text-[0.8125rem] font-medium">The nine questions</h3>
                <dl className="flex flex-col gap-xs text-[0.75rem]">
                  <Answer
                    q="What is running now, and why"
                    a={`${data.answers.running.status} — ${data.answers.running.workflow || 'unnamed template'}, ${data.answers.running.nodes.length} node${data.answers.running.nodes.length === 1 ? '' : 's'} active`}
                  />
                  <Answer q="What changed" a={`${data.timeline.length} journal event${data.timeline.length === 1 ? '' : 's'} — see Timeline`} />
                  <Answer
                    q="What is blocked"
                    a={data.answers.blocked.length
                      ? `${data.answers.blocked.length} node${data.answers.blocked.length === 1 ? '' : 's'} waiting on something external`
                      : 'Nothing is blocked'}
                  />
                  <Answer
                    q="What needs my approval"
                    a={data.answers.approval.length
                      ? data.answers.approval.map((c) => c.node_id).join(', ')
                      : 'Nothing is waiting on you'}
                  />
                  <Answer
                    q="What failed"
                    a={data.answers.failed.length
                      ? `${data.answers.failed.length} node${data.answers.failed.length === 1 ? '' : 's'} failed`
                      : 'Nothing failed'}
                  />
                  <Answer q="What is costing money" a={runCostText(data.stats.cost_usd)} />
                  <Answer
                    q="What is risky"
                    a={riskyText(data.answers.risky.degraded.length, fakeChecks.length, data.stats.verification_debt)}
                  />
                  {/* The question no other surface answers, and the one that decides whether a
                      user can walk away. */}
                  <Answer q="What happens next if I say nothing" a={data.answers.next.detail} />
                  <Answer
                    q="Were the checks that passed real checks"
                    a={fakeChecks.length
                      ? `${fakeChecks.length} gate${fakeChecks.length === 1 ? '' : 's'} have never rejected over a real sample`
                      : 'No gate shows the fake-check pattern'}
                  />
                </dl>
              </section>
            </>
          )}

          {/* Q2 "what changed" + the attempt ledger. Oldest-first, because this reads as a
              narrative: newest-first makes a reader reconstruct causality backwards. */}
          {/* The live touched-items feed (§6.5): what the run PUBLISHED and what was handed to
              it. Above the step timeline because "what did it change in my world" is the
              question a user asks first — the step history is how it got there. */}
          {tab === 'timeline' && data.touched.length > 0 && (
            <section className="flex flex-col gap-xs">
              <h3 className="text-on-surface text-[0.8125rem] font-medium">Touched</h3>
              <ul className="flex flex-col gap-2xs text-[0.75rem]">
                {data.touched.map((t) => (
                  <li key={`${t.kind}-${t.ref}-${t.ts}`} className="flex flex-wrap items-baseline gap-xs rounded-lg bg-surface-high p-s">
                    <span className="text-on-surface-low">{t.kind === 'file' ? 'file in' : 'artifact'}</span>
                    <span className="font-mono text-on-surface">{t.label || t.ref}</span>
                    {/* The verb is preserved: a converged republish is not a new version, and
                        collapsing them would make an unchanged artifact look freshly written. */}
                    {t.action ? <span className="text-on-surface-low">{t.action}</span> : null}
                    {t.detail ? <span className="text-on-surface-low">{t.detail}</span> : null}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {tab === 'timeline' && (
            data.timeline.length === 0 ? (
              <p className="text-on-surface-low text-[0.75rem]">
                This run has written no journal events yet.
              </p>
            ) : (
              <ol className="flex flex-col gap-2xs text-[0.75rem]">
                {data.timeline.map((row, i) => (
                  <li key={`${row.ts}-${row.kind}-${i}`} className="flex flex-col gap-2xs rounded-lg bg-surface-high p-s">
                    <span className="flex flex-wrap items-baseline gap-xs">
                      <span className="text-on-surface font-medium">{row.kind}</span>
                      {row.node_id ? <span className="font-mono text-on-surface-low">{row.node_id}</span> : null}
                      {/* The attempt ledger: attempt 2+ is a retry, which is the number that
                          explains a cost the step count alone does not. */}
                      {typeof row.attempt === 'number' && row.attempt > 1 ? (
                        <span className="text-warning">attempt {row.attempt}</span>
                      ) : null}
                      {row.ts ? <span className="text-on-surface-low tabular-nums">{row.ts}</span> : null}
                    </span>
                    <span className="flex flex-wrap gap-xs text-on-surface-low tabular-nums">
                      {row.model ? <span>{row.model}</span> : null}
                      {typeof row.tokens === 'number' && row.tokens ? <span>{row.tokens.toLocaleString()} tokens</span> : null}
                      {/* Same tilde, same reason: a per-step dollar is the same estimate the run
                          total sums. Rendered only when non-zero, so "~$0.0000" never appears. */}
                      {typeof row.cost_usd === 'number' && row.cost_usd ? <span>~${row.cost_usd.toFixed(4)}</span> : null}
                      {typeof row.duration_secs === 'number' && row.duration_secs ? <span>{fmtElapsed(row.duration_secs)}</span> : null}
                      {typeof row.approved === 'boolean' ? <span>{row.approved ? 'approved' : 'rejected'}</span> : null}
                    </span>
                    {row.detail ? <span className="text-on-surface-low">{String(row.detail)}</span> : null}
                  </li>
                ))}
              </ol>
            )
          )}

          {/* Criterion 8: the Proof section, sufficient to review an unattended run without
              opening the transcript. Its own caveats render as prominently as its numbers — a
              Proof section with no evidence and no warning is the worst surface, because it
              looks like proof. */}
          {tab === 'proof' && (
            <section className="flex flex-col gap-xs">
              <p className="text-on-surface text-[0.8125rem]">{data.proof.summary}</p>
              <dl className="grid grid-cols-2 gap-xs text-[0.75rem]">
                <Stat label="Verified steps" value={`${data.proof.verified_steps} of ${data.proof.total_steps}`} />
                <Stat label="Coverage" value={`${Math.round(data.proof.coverage * 100)}%`} />
              </dl>
              <h4 className="text-on-surface text-[0.8125rem] font-medium">Evidence</h4>
              {data.proof.evidence_files.length === 0 ? (
                <p className="text-on-surface-low text-[0.75rem]">
                  No evidence files were captured.
                </p>
              ) : (
                <ul className="flex flex-col gap-2xs text-[0.75rem]">
                  {data.proof.evidence_files.map((f) => (
                    <li key={f} className="flex items-center gap-2xs text-on-surface-low">
                      <CircleCheck size={12} className="text-success shrink-0" aria-hidden />
                      <span className="font-mono">{f}</span>
                    </li>
                  ))}
                </ul>
              )}
              {data.proof.warnings.length > 0 && (
                <>
                  <h4 className="text-on-surface text-[0.8125rem] font-medium">Caveats</h4>
                  <ul className="flex flex-col gap-2xs text-[0.75rem]">
                    {data.proof.warnings.map((w) => (
                      <li key={w} className="flex items-start gap-2xs text-on-surface-low">
                        <TriangleAlert size={12} className="text-warning mt-2xs shrink-0" aria-hidden />
                        <span>{w}</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {/* `honest` false would mean neither evidence nor a caveat reached this section —
                  a claim dressed as proof. The backend guarantees one or the other; saying so
                  here means a regression is visible rather than invisible. */}
              {!data.proof.honest && (
                <p className="text-warning flex items-center gap-2xs text-[0.75rem]">
                  <CircleHelp size={12} aria-hidden />
                  This section has neither evidence nor a stated caveat, so it proves nothing.
                </p>
              )}
            </section>
          )}
        </div>
      )}
    </SidePanel>
  )
}

/** One labelled figure. `tabular-nums` so a column of costs stays aligned as digits change. */
function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col rounded-lg bg-surface-high p-s">
      <dt className="text-on-surface-low text-[0.6875rem]">{label}</dt>
      <dd className="text-on-surface tabular-nums">{value}</dd>
    </div>
  )
}

/** One checklist question and its answer, as a definition pair — the question IS the label. */
function Answer({ q, a }: { q: string; a: string }) {
  return (
    <div className="flex flex-col gap-2xs border-outline-variant border-b pb-xs last:border-b-0">
      <dt className="text-on-surface-low">{q}</dt>
      <dd className="text-on-surface">{a}</dd>
    </div>
  )
}

/** The risk answer, assembled from its three real sources.
 *
 *  Exported for the test: "what is risky" answered as an empty string would satisfy a
 *  render-without-crashing check and tell a reader nothing, so the property worth pinning is that
 *  every combination — including no risk at all — produces a sentence. */
export function riskyText(degraded: number, fakeChecks: number, debt: number): string {
  const parts: string[] = []
  // 🔑 Every count here is in hand one token to its left, and n === 1 is the ORDINARY case on this
  // panel: one degraded node or one never-rejecting gate is exactly the state a reader opens
  // Introspect to understand. Hedging it made the answer look generated rather than measured — on a
  // surface whose entire job is to answer questions precisely.
  if (degraded) parts.push(`${degraded} node${degraded === 1 ? '' : 's'} ran degraded`)
  if (fakeChecks) parts.push(`${fakeChecks} gate${fakeChecks === 1 ? '' : 's'} may not be checking`)
  // Rendered as a percentage because that is how the threshold is expressed. Deliberately not
  // gated on the warn threshold here: the number is informative below it too, and only the
  // WARNING is threshold-bound.
  if (debt > 0) parts.push(`${Math.round(debt * 100)}% of completed steps are unverified`)
  return parts.length ? parts.join('; ') : 'Nothing flagged: no degraded nodes, no unverified steps'
}

/** The timeline row's most explanatory field, for the collapsed one-line form.
 *
 *  Exported and tested because "the detail or the state or nothing" is exactly the kind of
 *  fallback chain that silently renders "undefined" once a backend field is renamed. */
export function rowSummary(row: WorkflowTimelineRow): string {
  return row.detail || row.state || row.kind
}
