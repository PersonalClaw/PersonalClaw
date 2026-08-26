import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowLeft, Play, Sparkles, RotateCcw } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { Loading } from '../../ui/ListScaffold'
import { QuietButton } from '../../ui/QuietButton'
import { Button } from '../../ui/Button'
import { HeaderActions } from '../../ui/HeaderActions'
import { Segmented } from '../../ui/Segmented'
import { Field, TextInput } from '../../ui/forms'
import { Toggle } from '../../ui/Toggle'
import { PageTitle } from '../../ui/PageTitle'
import {
  api,
  type WorkflowDef,
  type WorkflowNode,
  type WorkflowVersionRow,
  type WorkflowVersionOp,
  type WorkflowMaturity,
  type WorkflowLedgerRow,
} from '../../lib/api'
import { notify } from '../../app/appSdk'

interface FlatNode { depth: number; kind: string; id: string; label: string; summary: string }

/** Flatten a spec tree for display, one row per node.
 *
 *  Rendered as an indented list rather than a graph: a workflow's shape IS a tree (the
 *  engine's own model), and a force-directed graph of six nodes communicates less than six
 *  indented lines. The label carries what a reader needs to identify the node; the summary
 *  carries the one config field that says what it DOES. */
function flatten(node: WorkflowNode, depth = 0, label = ''): FlatNode[] {
  const cfg = node.config ?? {}
  const summary =
    typeof cfg.prompt === 'string' ? cfg.prompt
      : typeof cfg.expr === 'string' ? cfg.expr
      : typeof cfg.provider === 'string' ? `provider: ${cfg.provider}`
      : typeof cfg.kind === 'string' ? `${cfg.kind} gate`
      : ''
  const out: FlatNode[] = [{
    depth,
    kind: node.kind,
    id: node.id ?? '',
    label,
    summary: typeof summary === 'string' ? summary.replace(/\s+/g, ' ').slice(0, 140) : '',
  }]
  for (const child of node.children ?? []) out.push(...flatten(child, depth + 1))
  if (node.body) out.push(...flatten(node.body, depth + 1, 'body'))
  for (const [caseLabel, caseNode] of Object.entries(node.cases ?? {})) {
    out.push(...flatten(caseNode, depth + 1, `case ${caseLabel}`))
  }
  if (node.default) out.push(...flatten(node.default, depth + 1, 'default'))
  return out
}

/** The maturity badge (R11). Level and label come from the backend; the tone rises with it so a
 *  glance says "proven" vs "draft" — a check that has never rejected a bad run is not yet proven,
 *  and the badge is honest about that. */
function MaturityBadge({ maturity }: { maturity: WorkflowMaturity }) {
  const strong = maturity.level >= 2
  const tone = strong ? 'var(--color-primary)' : 'var(--color-on-surface-low)'
  return (
    <span
      className="inline-flex shrink-0 items-center rounded-pill px-2 py-0.5 text-[0.75rem]"
      style={{ background: `color-mix(in srgb, ${tone} 14%, transparent)`, color: tone }}
      title={`Maturity L${maturity.level}: ${maturity.clean_runs} clean run(s)`
        + (maturity.evaluator_rejected ? ', gate has rejected a bad run' : ', gate not yet proven')}
    >
      {maturity.label} · L{maturity.level}
    </span>
  )
}

/** One workflow definition — its tree, its declared inputs, its version history and run ledger,
 *  and a way to run or refine it.
 *
 *  Read-mostly by design: authoring a spec by hand is what `workflow_author` and the chat
 *  planner are for, and a half-built visual editor here would be worse than either. What
 *  this page owes the user is an honest view of what the workflow DOES plus the ability to
 *  supply inputs and start it. */
export function WorkflowDefDetail({ name, onBack, onStarted }: {
  name: string
  onBack: () => void
  onStarted: (runId: string) => void
}) {
  const [def, setDef] = useState<WorkflowDef | null>(null)
  const [loading, setLoading] = useState(true)
  const [inputs, setInputs] = useState<Record<string, string>>({})
  const [starting, setStarting] = useState(false)
  const [tab, setTab] = useState<'steps' | 'versions' | 'ledger'>('steps')
  const [versions, setVersions] = useState<WorkflowVersionRow[]>([])
  const [pinned, setPinned] = useState<number | null>(null)
  const [maturity, setMaturity] = useState<WorkflowMaturity | null>(null)
  const [diffOps, setDiffOps] = useState<WorkflowVersionOp[] | null>(null)
  const [ledger, setLedger] = useState<WorkflowLedgerRow[] | null>(null)
  const [refining, setRefining] = useState(false)
  // Named for the census's in-flight vocabulary (`disabledReasonCensus`'s BUSY list): the
  // switch is only ever unavailable while its OWN write is in flight, which is the one class
  // that stays natively disabled rather than carrying a `disabledReason`.
  const [publishSaving, setPublishSaving] = useState(false)

  const loadVersions = useCallback(() => {
    api.workflowVersions(name)
      .then((v) => {
        setVersions(v.versions)
        setPinned(v.pinned)
        setMaturity(v.maturity)
        // A typed-op diff of the two most recent versions — the shape the refiner proposes and
        // the user rolls back. Only meaningful once a second version exists.
        if (v.versions.length >= 2) {
          const [a, b] = [v.versions[v.versions.length - 2].version, v.versions[v.versions.length - 1].version]
          api.workflowVersionDiff(name, a, b).then((d) => setDiffOps(d.ops)).catch(() => setDiffOps(null))
        } else {
          setDiffOps(null)
        }
      })
      .catch(() => { setVersions([]); setPinned(null); setMaturity(null) })
  }, [name])

  useEffect(() => {
    let alive = true
    setLoading(true)
    api.workflowDef(name)
      .then((d) => { if (alive) setDef(d.definition) })
      .catch(() => { if (alive) setDef(null) })
      .finally(() => { if (alive) setLoading(false) })
    loadVersions()
    return () => { alive = false }
  }, [name, loadVersions])

  // The Run Ledger tab is a per-run history read; load it lazily the first time it is opened.
  useEffect(() => {
    if (tab === 'ledger' && ledger === null) {
      api.workflowLedger(name).then((l) => setLedger(l.runs)).catch(() => setLedger([]))
    }
  }, [tab, ledger, name])

  const rows = useMemo(() => (def ? flatten(def.root) : []), [def])
  const declared = useMemo(() => Object.entries(def?.inputs ?? {}), [def])
  // Read with `=== true`, matching the backend's `is True`: an absent key is UNPUBLISHED, which
  // is what every template authored before A2A existed looks like.
  const published = def?.metadata?.a2a_published === true
  const handoffs = useMemo(
    () => (def?.metadata?.hands_off_to ?? []).filter((h) => (h?.target_def ?? '').trim()),
    [def],
  )

  const start = useCallback(async () => {
    setStarting(true)
    try {
      const payload: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(inputs)) if (value !== '') payload[key] = value
      const res = await api.startWorkflowRun({ name, inputs: payload })
      onStarted(res.run_id)
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Could not start the workflow')
    } finally {
      setStarting(false)
    }
  }, [inputs, name, onStarted])

  // "Refine now": fire the propose-only refiner over this template. It launches a run that
  // proposes a diff for review — it never edits the template — so we navigate to that run.
  const refine = useCallback(async () => {
    if (refining) return
    setRefining(true)
    try {
      const res = await api.refineWorkflow(name)
      if (res.run_id) onStarted(res.run_id)
      else notify('Refiner started')
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Could not start the refiner')
    } finally {
      setRefining(false)
    }
  }, [name, onStarted, refining])

  // EXTERNAL-ACCESS §5 — publish/unpublish this template as an A2A skill.
  //
  // The optimistic flip is reverted on failure rather than left standing: this switch is the
  // user's only view of whether an external agent can reach this workflow, and a control that
  // shows "on" after the write failed is worse than one that lags, because it claims an exposure
  // decision that did not happen (or hides one that did).
  const togglePublish = useCallback(async (next: boolean) => {
    const previous = def?.metadata?.a2a_published === true
    setPublishSaving(true)
    setDef((d) => (d ? { ...d, metadata: { ...(d.metadata ?? {}), a2a_published: next } } : d))
    try {
      const res = await api.publishWorkflowToA2A(name, next)
      setDef((d) => (d ? { ...d, metadata: { ...(d.metadata ?? {}), a2a_published: res.a2a_published } } : d))
    } catch (e) {
      setDef((d) => (d ? { ...d, metadata: { ...(d.metadata ?? {}), a2a_published: previous } } : d))
      notify(e instanceof Error ? e.message : 'Could not change A2A publication')
    } finally {
      setPublishSaving(false)
    }
  }, [def, name])

  const rollback = useCallback(async (version: number) => {
    try {
      await api.repinWorkflowVersion(name, version)
      loadVersions()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Could not roll back')
    }
  }, [name, loadVersions])

  return (
    <div className="flex h-full flex-col">
      <TopBar
        keepCornerPadding
        left={<div className="flex min-w-0 items-center gap-m">
          <QuietButton onClick={onBack} title="Back to workflows"><ArrowLeft size={13} /> Workflows</QuietButton>
          <PageTitle className="truncate">{name}</PageTitle>
          {def?.source === 'bundled' && <span className="shrink-0 text-on-surface-low text-[0.75rem]">bundled</span>}
          {maturity && <MaturityBadge maturity={maturity} />}
        </div>}
        right={def ? (
          <HeaderActions>
            <QuietButton onClick={refine} title="Propose an improvement to this template from its run history">
              <Sparkles size={13} /> {refining ? 'Refining…' : 'Refine now'}
            </QuietButton>
            <Button onClick={start} loading={starting} disabled={starting}>
              <Play size={14} /> Run
            </Button>
          </HeaderActions>
        ) : undefined}
      />
      <div className="min-h-0 flex-1 overflow-y-auto p-l">
        {loading ? <Loading what="this workflow" /> : !def ? (
          <p className="text-on-surface-low text-[0.8125rem]">This definition could not be loaded.</p>
        ) : (
          <div className="mx-auto flex max-w-[var(--content-width)] flex-col gap-l">
            {def.description && <p className="text-on-surface text-[0.8125rem]">{def.description}</p>}

            <Segmented
              ariaLabel="Definition view"
              value={tab}
              onChange={(k) => setTab(k as 'steps' | 'versions' | 'ledger')}
              options={[
                { key: 'steps', label: 'Steps' },
                { key: 'versions', label: 'Versions' },
                { key: 'ledger', label: 'Run Ledger' },
              ]}
            />

            {tab === 'steps' && (
              <>
                {declared.length > 0 && (
                  <div className="flex flex-col gap-s">
                    <span data-type="title-m" className="text-on-surface">Inputs</span>
                    {declared.map(([key, meta]) => (
                      <Field
                        key={key}
                        label={`${key}${meta.required ? ' *' : ''}`}
                        hint={meta.help || (meta.default !== undefined && meta.default !== null ? `Default: ${String(meta.default)}` : undefined)}
                      >
                        <TextInput
                          value={inputs[key] ?? ''}
                          onChange={(v) => setInputs((p) => ({ ...p, [key]: v }))}
                          placeholder={meta.default !== undefined && meta.default !== null ? String(meta.default) : ''}
                          ariaLabel={key}
                        />
                      </Field>
                    ))}
                  </div>
                )}

                <div className="flex items-start justify-between gap-m">
                  <div className="min-w-0">
                    <span data-type="title-m" className="text-on-surface">Publish to A2A</span>
                    <p className="text-on-surface-low text-[0.75rem]">
                      {published
                        ? 'External agents holding an A2A token can see this template on the agent card and start it.'
                        : 'Off. This template is not on the A2A agent card and cannot be started by an external agent.'}
                    </p>
                  </div>
                  {/* The accessible name states the TEMPLATE, not just the action: the switch's
                      name is read out of context in a screen-reader's forms list, where four
                      "Publish to A2A" switches would be indistinguishable. */}
                  <Toggle
                    on={published}
                    onChange={togglePublish}
                    disabled={publishSaving}
                    label={`Publish ${name} to A2A`}
                  />
                </div>

                <div className="flex flex-col gap-2xs">
                  <span data-type="title-m" className="text-on-surface">Steps</span>
                  {rows.map((r, i) => (
                    <div
                      key={`${r.depth}-${r.id || r.kind}-${i}`}
                      className="flex items-baseline gap-m py-2xs"
                      style={{ paddingLeft: `calc(${r.depth} * 1rem)` }}
                    >
                      <span className="shrink-0 font-mono text-on-surface-low text-[0.75rem]">{r.kind}</span>
                      <div className="min-w-0 flex-1">
                        {r.id && <span className="text-on-surface text-[0.8125rem]">{r.id}</span>}
                        {r.label && <span className="ml-s text-on-surface-low text-[0.75rem]">{r.label}</span>}
                        {r.summary && <div className="truncate text-on-surface-low text-[0.75rem]">{r.summary}</div>}
                      </div>
                    </div>
                  ))}
                </div>

                {handoffs.length > 0 && (
                  <div className="flex flex-col gap-2xs">
                    <span data-type="title-m" className="text-on-surface">Hands off to</span>
                    {handoffs.map((h) => (
                      <div key={h.target_def} className="flex items-baseline gap-m py-2xs">
                        <span className="shrink-0 text-on-surface text-[0.8125rem]">{h.target_def}</span>
                        <div className="min-w-0 flex-1">
                          {h.condition && <span className="text-on-surface-low text-[0.75rem]">{h.condition}</span>}
                          {!!h.context_fields?.length && (
                            <div className="text-on-surface-low text-[0.75rem]">
                              carries {h.context_fields.join(', ')}
                            </div>
                          )}
                        </div>
                        {h.requires_user_request && (
                          <span className="shrink-0 text-on-surface-low text-[0.75rem]">only on request</span>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {def.metadata?.requirements && Object.keys(def.metadata.requirements).length > 0 && (
                  <div className="flex flex-col gap-2xs">
                    <span data-type="title-m" className="text-on-surface">Requires</span>
                    {Object.entries(def.metadata.requirements).map(([group, items]) => (
                      <span key={group} className="text-on-surface-low text-[0.75rem]">
                        {group}: {items.join(', ')}
                      </span>
                    ))}
                  </div>
                )}
              </>
            )}

            {tab === 'versions' && (
              <div className="flex flex-col gap-l">
                <div className="flex flex-col gap-2xs">
                  <span data-type="title-m" className="text-on-surface">Versions</span>
                  {versions.length === 0 ? (
                    <p className="text-on-surface-low text-[0.75rem]">No version history yet.</p>
                  ) : (
                    [...versions].reverse().map((v) => (
                      <div key={v.version} className="flex items-baseline gap-m py-2xs">
                        <span className="shrink-0 font-mono text-on-surface text-[0.8125rem]">v{v.version}</span>
                        <div className="min-w-0 flex-1">
                          <span className="text-on-surface-low text-[0.75rem]">{v.source}</span>
                          {v.created_at && <span className="ml-s text-on-surface-low text-[0.75rem]">{v.created_at}</span>}
                        </div>
                        {v.version === pinned ? (
                          <span className="shrink-0 text-on-surface-low text-[0.75rem]">pinned</span>
                        ) : (
                          <QuietButton onClick={() => rollback(v.version)} title={`Roll back to v${v.version}`}>
                            <RotateCcw size={12} /> Roll back
                          </QuietButton>
                        )}
                      </div>
                    ))
                  )}
                </div>

                {diffOps && diffOps.length > 0 && (
                  <div className="flex flex-col gap-2xs">
                    <span data-type="title-m" className="text-on-surface">Latest change</span>
                    {diffOps.map((op, i) => (
                      <div key={`${op.op}-${op.node_id ?? ''}-${i}`} className="flex items-baseline gap-m py-2xs">
                        <span className="shrink-0 font-mono text-on-surface-low text-[0.75rem]">{op.op}</span>
                        <div className="min-w-0 flex-1 text-on-surface text-[0.8125rem]">
                          {op.node_id}
                          {op.fields?.length ? <span className="text-on-surface-low"> — {op.fields.join(', ')}</span> : null}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {tab === 'ledger' && (
              <div className="flex flex-col gap-2xs">
                <span data-type="title-m" className="text-on-surface">Run Ledger</span>
                {ledger === null ? (
                  <Loading what="the run ledger" />
                ) : ledger.length === 0 ? (
                  <p className="text-on-surface-low text-[0.75rem]">This template has no recorded runs yet.</p>
                ) : (
                  ledger.map((run) => (
                    <div key={run.run_id} className="flex items-baseline gap-m py-2xs">
                      <span className="shrink-0 font-mono text-on-surface-low text-[0.75rem]">{run.run_id}</span>
                      <div className="min-w-0 flex-1">
                        <span className="text-on-surface text-[0.8125rem]">{run.status}</span>
                        <span className="ml-s text-on-surface-low text-[0.75rem]">v{run.spec_version}</span>
                      </div>
                      <span className="shrink-0 text-on-surface-low text-[0.75rem]">
                        {run.totals?.steps_completed ?? 0} done
                        {run.totals?.steps_failed ? `, ${run.totals.steps_failed} failed` : ''}
                      </span>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
