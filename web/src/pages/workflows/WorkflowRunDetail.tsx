import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ChevronDown, ChevronRight, GitBranch, Pause, RotateCcw, SkipForward, X } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { Segmented } from '../../ui/Segmented'
import { Loading } from '../../ui/ListScaffold'
import { QuietButton } from '../../ui/QuietButton'
import { api, type WorkflowContinuation, type WorkflowRunDetailData } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { confirm } from '../../ui/dialog'
import { fmtElapsed, isTerminal, itemProgress, nodeLabel, nodeLook, runLook } from './workflowMeta'
import { byInstancePath } from './instancePathOrder'
import { buildTree, initialCollapsed, summarize, summaryLabel, visibleRows } from './nodeTree'
import { useWorkflowStream } from './useWorkflowStream'
import { DagView } from '../tasks/DagView'
import { layoutRunDag } from './runDag'
import { tokenForNode } from './surfacingMeta'
import { WorkflowAsk } from './WorkflowAsk'

/** One workflow run, live (WORKFLOWS-V2 Slice 7b).
 *
 *  Snapshot-then-subscribe: the SSE endpoint writes the full status BEFORE the stream
 *  opens, so the first frame populates the view. A lifecycle event is a REFETCH CUE, not a
 *  patch source — the engine's own status projection stays the single truth, and applying
 *  partial patches here would let the view drift from the run it claims to show.
 *
 *  A terminal run does not subscribe at all: its stream would close immediately anyway, and
 *  the status it already has is final. */
export function WorkflowRunDetail({ runId, onBack }: { runId: string; onBack: () => void }) {
  const [run, setRun] = useState<WorkflowRunDetailData | null>(null)
  const [conts, setConts] = useState<WorkflowContinuation[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  // Coalesce refetches: a fan-out completing fires many node_done events at once, and one
  // request per event would hammer the gateway for the same answer.
  const pending = useRef<number | null>(null)

  const refetch = useCallback(async () => {
    try {
      const [status, continuations] = await Promise.all([
        api.workflowRun(runId),
        api.workflowContinuations(runId).catch(() => ({ continuations: [] })),
      ])
      setRun(status)
      setConts(continuations.continuations)
    } catch {
      /* a transient read failure keeps the last good view rather than blanking it */
    } finally {
      setLoading(false)
    }
  }, [runId])

  useEffect(() => { refetch() }, [refetch])

  const scheduleRefetch = useCallback(() => {
    if (pending.current !== null) return
    pending.current = window.setTimeout(() => { pending.current = null; refetch() }, 250)
  }, [refetch])

  useEffect(() => () => { if (pending.current !== null) window.clearTimeout(pending.current) }, [])

  const live = !!run && !isTerminal(run.status)
  useWorkflowStream(runId, live, {
    onSnapshot: (snap) => { setRun(snap); setLoading(false) },
    onLifecycle: () => scheduleRefetch(),
  })

  const act = useCallback(async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
      await refetch()
    } catch (e) {
      notify(e instanceof Error ? e.message : `${label} failed`)
    } finally {
      setBusy(false)
    }
  }, [refetch])

  const answer = useCallback(async (cont: WorkflowContinuation, value: unknown, alwaysAllow: boolean) => {
    await act('Answer', () => api.resumeWorkflowRun(runId, {
      answer: value, resume_token: cont.resume_token, always_allow: alwaysAllow,
    }))
  }, [act, runId])

  const rewind = useCallback(async (nodeId: string) => {
    const ok = await confirm({
      title: `Re-run "${nodeId}"?`,
      body: 'This node and everything that reads its output will run again. Previous outputs are archived, not lost.',
      confirmLabel: 'Re-run',
    })
    if (ok) await act('Rewind', () => api.rewindWorkflowRun(runId, { node_id: nodeId }))
  }, [act, runId])

  const runFrom = useCallback(async (nodeId: string) => {
    await act('Run from', () => api.workflowRunFrom(runId, { node_id: nodeId }))
  }, [act, runId])

  const cancel = useCallback(async () => {
    const ok = await confirm({
      title: 'Cancel this run?',
      body: 'In-flight steps are stopped. Completed work is kept.',
      confirmLabel: 'Cancel run',
      danger: true,
    })
    if (ok) await act('Cancel', () => api.cancelWorkflowRun(runId))
  }, [act, runId])

  const fork = useCallback(async () => {
    await act('Fork', async () => {
      const res = await api.forkWorkflowRun(runId, { note: 'branched from the run view' })
      notify(`Forked to ${res.child_run_id}. Not isolated: ${res.shared_axes.length} shared axes.`)
    })
  }, [act, runId])

  const look = run ? runLook(run.status) : null
  const StatusIcon = look?.icon

  // Sorted by instance path so the list reads in the spec's own order, and indented to its
  // tree shape — a flat list of twenty node ids is unreadable on a real workflow. Numerically,
  // matching the engine: a string sort puts item 10 ahead of item 2 (issue #568).
  const nodes = useMemo(() => [...(run?.nodes ?? [])].sort(byInstancePath), [run])

  // Collapsible containers (WF2 Slice 10b). The `deep-research` template expands to 21 rows and 18
  // of them are one untaken subgraph — the three that matter are buried in the ones that did not
  // run.
  const rows = useMemo(() => buildTree(nodes), [nodes])

  // List | Graph. The LIST is the default: it carries failure text, remediation and per-item labels
  // that a 168px node box cannot, and it is what a user reads when something broke. The graph
  // answers a different question — where in the shape am I — so it is a mode, not a replacement.
  //
  // Local state, not URL-backed: this component takes `runId`/`onBack` and no route props, and
  // threading them through only to make a view toggle shareable would change the caller's contract
  // for a preference nobody links to.
  const [view, setView] = useState<'list' | 'graph'>('list')

  // The graph's Approve/Deny reads the continuations this view ALREADY fetches on every refetch —
  // no second request. A `waiting` node is only ANSWERABLE when a live resume token exists for it:
  // a `wait` node is parked on the clock, and offering approval on one would ask the user to answer
  // something nobody asked them.
  const dag = useMemo(
    () => layoutRunDag(nodes, {
      continuations: conts,
      label: (n) => (n.item_label ? `${n.node_id} · ${n.item_label}` : n.node_id),
    }),
    [nodes, conts],
  )

  const resolveGate = useCallback(
    async (instancePath: string, approved: boolean) => {
      // `tokenForNode` matches on `node_id`, so the continuation's INSTANCE PATH is passed as that
      // key: a DAG node is identified by its instance path (two iterations of one node share a
      // node_id and would collide), and the layout uses the same id.
      const token = tokenForNode(
        conts.map((c) => ({ node_id: c.instance_path, resume_token: c.resume_token, expired: c.expired })),
        instancePath,
      )
      // No token means nothing to answer. Guarded rather than sent: the backend reads an ABSENT
      // token as "the newest pending gate", which is right for a chat user saying "approve it" and
      // wrong for a click on a specific node.
      if (!token) { notify('That gate has no pending question.'); return }
      await act(approved ? 'Approve' : 'Deny', async () => {
        const res = await api.confirmWorkflowRun(runId, {
          verb: approved ? 'approve' : 'reject',
          resume_token: token,
        })
        notify(res.ok === false ? (res.message ?? 'Could not resolve the gate.') : `Gate ${res.verb}d.`)
      })
    },
    [act, conts, runId],
  )
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  // Seeded ONCE per run, not on every poll: re-deriving would slam a subtree shut the moment it
  // finished, right as the user was reading it. `touched` is what makes the seeding one-shot while
  // still re-seeding when the user navigates to a different run.
  const seeded = useRef<string>('')
  useEffect(() => {
    if (!run || seeded.current === run.run_id) return
    seeded.current = run.run_id
    setCollapsed(initialCollapsed(buildTree(run.nodes ?? []), run.nodes ?? []))
  }, [run])
  const toggle = useCallback((path: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }, [])
  const shownRows = useMemo(() => visibleRows(rows, collapsed), [rows, collapsed])

  return (
    <div className="flex h-full flex-col">
      <TopBar
        keepCornerPadding
        left={<div className="flex min-w-0 items-center gap-m">
          <QuietButton onClick={onBack} title="Back to workflows"><ArrowLeft size={13} /> Workflows</QuietButton>
          {run && <span data-type="title-l" className="truncate text-on-surface">{run.workflow}</span>}
          {look && StatusIcon && (
            <span className={`inline-flex shrink-0 items-center gap-1 text-[0.75rem] ${look.tone}`}>
              <StatusIcon size={13} className={look.spin ? 'animate-spin' : ''} /> {look.label}
            </span>
          )}
        </div>}
        right={run && !isTerminal(run.status) ? (
          <div className="flex items-center gap-xs">
            <QuietButton onClick={() => act('Pause', () => api.pauseWorkflowRun(runId))} title="Pause — in-flight steps finish">
              <Pause size={13} /> Pause
            </QuietButton>
            <QuietButton onClick={cancel} title="Cancel this run"><X size={13} /> Cancel</QuietButton>
          </div>
        ) : run ? (
          <QuietButton onClick={fork} title="Branch a new run from this one; the original is untouched">
            <GitBranch size={13} /> Fork
          </QuietButton>
        ) : undefined}
      />

      <div className="min-h-0 flex-1 overflow-y-auto p-l">
        {loading && !run ? <Loading /> : !run ? (
          <p className="text-on-surface-low text-[0.8125rem]">This run could not be loaded.</p>
        ) : (
          <div className="mx-auto flex max-w-[var(--content-width)] flex-col gap-l">
            {/* Pending asks come FIRST: they are the only thing here a user can act on. */}
            {conts.map((c) => (
              <WorkflowAsk key={c.resume_token} continuation={c} busy={busy} onAnswer={answer} />
            ))}

            {run.error && (
              <p className="text-danger text-[0.8125rem]">{run.error}</p>
            )}

            <div className="flex flex-wrap items-center gap-l text-on-surface-low text-[0.75rem]">
              <span>run <span className="font-mono">{run.run_id}</span></span>
              <span>spec v{run.spec_version}</span>
              {run.tokens ? <span className="tabular-nums">{run.tokens.toLocaleString()} tokens</span> : null}
              {run.elapsed_secs ? <span className="tabular-nums">{fmtElapsed(run.elapsed_secs)}</span> : null}
            </div>

            {/* The mode toggle sits ABOVE the nodes and is hidden when there is nothing to show —
                a List/Graph switch over an empty run offers two ways to look at nothing. */}
            {nodes.length > 0 && (
              <div className="flex items-center gap-xs">
                <Segmented
                  ariaLabel="Run view"
                  value={view}
                  onChange={(v) => setView(v as 'list' | 'graph')}
                  options={[
                    { key: 'list', label: 'List' },
                    { key: 'graph', label: 'Graph' },
                  ]}
                />
              </div>
            )}

            {view === 'graph' && dag.nodes.length > 0 ? (
              <div className="overflow-auto rounded-lg bg-surface-high p-s">
                <DagView
                  nodes={dag.nodes}
                  edges={dag.edges}
                  width={dag.width}
                  height={dag.height}
                  onNodeClick={(id) => toggle(id)}
                  // The declared-but-unwired seam, finally bound (TASKS-SOPS §7 R6). Passed only
                  // when the run can still be answered: a terminal run's gate cannot be resolved,
                  // and an Approve button that always fails teaches the user the UI lies.
                  onApprove={isTerminal(run.status) ? undefined : (id) => resolveGate(id, true)}
                  onDeny={isTerminal(run.status) ? undefined : (id) => resolveGate(id, false)}
                />
              </div>
            ) : null}

            <div className={`flex flex-col gap-2xs${view === 'graph' ? ' hidden' : ''}`}>
              {shownRows.map(({ node: n, depth, descendants, collapsible }) => {
                const nl = nodeLook(n.state)
                const NIcon = nl.icon
                const canReenter = !isTerminal(run.status) && !!n.node_id
                const isCollapsed = collapsed.has(n.instance_path)
                const summary = collapsible ? summarize(descendants, nodes) : null
                return (
                  <div
                    key={n.instance_path}
                    className="group flex items-center gap-m rounded-lg px-s py-xs hover:bg-surface-high"
                    style={{ paddingLeft: `calc(var(--space-s) + ${depth} * 1rem)` }}
                  >
                    {/* The disclosure control, only where it earns its place: a container with one
                        child costs a click and saves a row. */}
                    {collapsible ? (
                      <button
                        type="button"
                        onClick={() => toggle(n.instance_path)}
                        className="shrink-0 text-on-surface-low transition-colors hover:text-on-surface"
                        title={isCollapsed ? `Show ${descendants.length} nested steps` : 'Collapse'}
                        aria-expanded={!isCollapsed}
                      >
                        {isCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                      </button>
                    ) : (
                      <span className="w-[14px] shrink-0" />
                    )}
                    <NIcon size={14} className={`shrink-0 ${nl.tone}${nl.spin ? ' animate-spin' : ''}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex min-w-0 items-center gap-s">
                        <span className="truncate text-on-surface text-[0.8125rem]">{nodeLabel(n)}</span>
                        {/* The per-item label (WF2-R5): what makes one row of a twelve-item
                            fan-out identifiable. Dimmed — it is which, not what. */}
                        {itemProgress(n) && (
                          <span className="min-w-0 shrink truncate text-on-surface-low text-[0.75rem] tabular-nums">
                            {itemProgress(n)}
                          </span>
                        )}
                        {/* What a collapsed subtree DID, counted by state rather than reduced to a
                            percentage: "18 skipped" says the branch was not taken and "17 done · 1
                            failed" says exactly where to look. A progress bar says neither. */}
                        {isCollapsed && summary && (
                          <span className="min-w-0 shrink truncate text-on-surface-low text-[0.75rem]">
                            {summaryLabel(summary)}
                          </span>
                        )}
                      </div>
                      {(n.degraded_reason || n.failure?.cause_plain) && (
                        <div className="truncate text-on-surface-low text-[0.75rem]">
                          {n.degraded_reason || n.failure?.cause_plain}
                        </div>
                      )}
                      {/* The remediation is a DIFFERENT fact from the cause — it is the next
                          action, and dropping it leaves the user with a diagnosis only. */}
                      {n.failure?.remediation && (
                        <div className="truncate text-on-surface-low text-[0.75rem]">{n.failure.remediation}</div>
                      )}
                    </div>
                    <span className={`shrink-0 text-[0.75rem] ${nl.tone}`}>{nl.label}</span>
                    {canReenter && (
                      <span className="flex shrink-0 items-center gap-2xs opacity-0 transition-opacity group-hover:opacity-100">
                        <QuietButton onClick={() => rewind(n.node_id)} title="Re-run this node and everything reading its output">
                          <RotateCcw size={12} />
                        </QuietButton>
                        <QuietButton onClick={() => runFrom(n.node_id)} title="Re-run only what comes after, keeping this output">
                          <SkipForward size={12} />
                        </QuietButton>
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
