import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ChevronDown, ChevronRight, FolderGit2, GitBranch, MessageSquarePlus, Package, Pause, Pencil, RotateCcw, ScanSearch, SkipForward, X } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { Segmented } from '../../ui/Segmented'
import { Loading } from '../../ui/ListScaffold'
import { QuietButton } from '../../ui/QuietButton'
import { SidePanel } from '../../ui/SidePanel'
import { api, type WorkflowContinuation, type WorkflowRunDetailData } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { confirm, promptForm } from '../../ui/dialog'
import { fmtElapsed, isNodeTerminal, isTerminal, itemProgress, nodeLabel, nodeLook, runLook } from './workflowMeta'
import { byInstancePath } from './instancePathOrder'
import { buildTree, initialCollapsed, summarize, summaryLabel, visibleRows } from './nodeTree'
import { useWorkflowStream } from './useWorkflowStream'
import { DagView } from '../tasks/DagView'
import { layoutRunDag } from './runDag'
import { tokenForNode } from './surfacingMeta'
import { revalidateNotice, revalidateSummary } from './revalidate'
import { WorkflowAsk } from './WorkflowAsk'
import { NodeInspectorDrawer } from './NodeInspectorDrawer'
import { SteeringPanel } from './SteeringPanel'
import { WorkspacePanel } from './WorkspacePanel'
import { OutboxPanel } from './OutboxPanel'
import { IntrospectPanel } from './IntrospectPanel'

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
  // The node whose inspector drawer is open (WV-10). Null = closed. Holds the node_id — the
  // drawer fetches on open, so nothing is loaded until a row's Inspect is actually clicked.
  const [inspectNodeId, setInspectNodeId] = useState<string | null>(null)
  // The mid-run steering + judge-triage panel (R14 / criterion 8). Docked to the right like
  // the inspector; toggled from the header, live runs only.
  const [steerOpen, setSteerOpen] = useState(false)
  // The code-run workspace review (§4.1): changed files + the two reintegration verbs. Closed by
  // default and fetched on open — answering costs a `git status` plus a conflict probe, and most
  // runs are never reviewed. Available on a TERMINAL run too, which is exactly when a user wants
  // to decide what to do with the work.
  const [workspaceOpen, setWorkspaceOpen] = useState(false)
  const [outboxOpen, setOutboxOpen] = useState(false)
  // The §6.4 introspection drawer: the nine questions, the cost/latency strip, the template
  // p50/p95 card, the said-no badges and the Proof section. Closed by default and fetched on
  // open — answering costs a cross-run ledger read, and most runs are never audited.
  const [introspectOpen, setIntrospectOpen] = useState(false)
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

  // Mid-flight edit of a node's prompt (WF2-R10 / criterion 9). The user pauses a running
  // workflow, edits a stage's instruction, and resumes. The edit is a real spec mutation
  // (`update_node`), but the calibration point is the WARNING: a bundled template carries a
  // typed doc block whose judge calibration is tuned to the prompt it shipped with, so editing
  // that prompt can silently invalidate it (R10b). We surface that BEFORE applying — the user
  // confirms the trade rather than discovering later that the judge is grading against a
  // rubric the run no longer matches.
  const editNodePrompt = useCallback(async (nodeId: string) => {
    const answers = await promptForm({
      title: `Edit "${nodeId}"`,
      body: revalidateNotice,
      fields: [{
        name: 'prompt',
        label: 'Instruction',
        type: 'textarea',
        placeholder: 'The new instruction for this stage.',
        required: true,
      }],
      confirmLabel: 'Apply edit',
    })
    if (answers === null) return  // cancelled — the warning did its job
    await act('Edit', async () => {
      const res = await api.editWorkflowRun(runId, {
        ops: [{ kind: 'update_node', node_id: nodeId, fields: { prompt: answers.prompt } }],
      })
      // A rejected batch reports typed issues; surface the first rather than a silent no-op so
      // the user knows the edit did not land (and why).
      if (res.ok === false || (res.issues?.length ?? 0) > 0) {
        notify(res.issues?.[0]?.message ?? 'The edit was rejected.')
        return
      }
      notify(revalidateSummary(res.preview))
    })
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
        right={run ? (
          <div className="flex items-center gap-xs">
            {/* Workspace on BOTH sides of the terminal split, unlike Steer/Pause/Fork: reviewing
                what a run changed is the one thing a user wants equally mid-run (is it touching
                what I expected) and after (do I take this work). */}
            <QuietButton onClick={() => setWorkspaceOpen((v) => !v)} title="Workspace — changed files and how to take this work">
              <FolderGit2 size={13} /> Workspace
            </QuietButton>
            {/* Artifacts, likewise on both sides of the terminal split: mid-run it answers "what has
                it produced so far", and after, it is where the deliverable and its version diff
                live. It is also the only surface that can hand a live run a file. */}
            <QuietButton onClick={() => setOutboxOpen((v) => !v)} title="Artifacts — what this run published, version diffs, and handing it files">
              <Package size={13} /> Artifacts
            </QuietButton>
            {/* Introspect, on both sides of the terminal split for the strongest reason of the
                three: mid-run it answers "what will you do next if I say nothing", and after, it
                is the Proof section that lets a user review unattended work without reading the
                transcript (criteria 6 & 8). */}
            <QuietButton onClick={() => setIntrospectOpen((v) => !v)} title="Introspect — cost, latency, gates, timeline and proof">
              <ScanSearch size={13} /> Introspect
            </QuietButton>
            {!isTerminal(run.status) ? (
              <>
                <QuietButton onClick={() => setSteerOpen((v) => !v)} title="Steer this run — queue an instruction or accept a judge comment">
                  <MessageSquarePlus size={13} /> Steer
                </QuietButton>
                <QuietButton onClick={() => act('Pause', () => api.pauseWorkflowRun(runId))} title="Pause — in-flight steps finish">
                  <Pause size={13} /> Pause
                </QuietButton>
                <QuietButton onClick={cancel} title="Cancel this run"><X size={13} /> Cancel</QuietButton>
              </>
            ) : (
              <QuietButton onClick={fork} title="Branch a new run from this one; the original is untouched">
                <GitBranch size={13} /> Fork
              </QuietButton>
            )}
          </div>
        ) : undefined}
      />

      <div className="flex min-h-0 flex-1">
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
                    {(canReenter || (isNodeTerminal(n.state) && !!n.node_id)) && (
                      <span className="flex shrink-0 items-center gap-2xs opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                        {/* Inspect: the §5 reconstructability drawer (WV-10). Offered ONLY for a
                            terminal node — the endpoint 409s otherwise, so a button on a running
                            node would teach the user the UI lies. Available on a terminal run too,
                            which is exactly when a user wants to reconstruct what a node did. */}
                        {isNodeTerminal(n.state) && !!n.node_id && (
                          <QuietButton onClick={() => setInspectNodeId(n.node_id)} title="Inspect this node — resolved prompt, inputs, output, attempts and ledger">
                            <ScanSearch size={12} />
                          </QuietButton>
                        )}
                        {canReenter && (
                          <>
                            {/* Mid-flight edit (criterion 9): change this stage's instruction on a
                                live run. Surfaces the re-validate warning before applying, since a
                                bundled template's judge calibration is tuned to the shipped prompt. */}
                            <QuietButton onClick={() => editNodePrompt(n.node_id)} title="Edit this stage's instruction — re-validates the template's judge calibration">
                              <Pencil size={12} />
                            </QuietButton>
                            <QuietButton onClick={() => rewind(n.node_id)} title="Re-run this node and everything reading its output">
                              <RotateCcw size={12} />
                            </QuietButton>
                            <QuietButton onClick={() => runFrom(n.node_id)} title="Re-run only what comes after, keeping this output">
                              <SkipForward size={12} />
                            </QuietButton>
                          </>
                        )}
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>

      {/* The node-inspector drawer (WV-10), docked to the right and pushing the run body narrower.
          Keyed on the node id so switching nodes remounts and refetches rather than showing the
          previous node's data. Only a terminal node's row exposes the Inspect trigger. */}
      {inspectNodeId && (
        <NodeInspectorDrawer runId={runId} nodeId={inspectNodeId} onClose={() => setInspectNodeId(null)} />
      )}

      {/* The workspace review drawer (§4.1 / criterion 7), docked right. Keyed on the run id so
          navigating between runs refetches rather than showing the previous run's diff. */}
      {workspaceOpen && (
        <WorkspacePanel runId={runId} onClose={() => setWorkspaceOpen(false)} />
      )}

      {/* The outbox / artifact drawer (§2.2d + §2.5), docked right. Keyed on the run id for the same
          reason as the workspace drawer: navigating between runs must refetch, not show the previous
          run's artifacts. */}
      {outboxOpen && (
        <OutboxPanel runId={runId} onClose={() => setOutboxOpen(false)} />
      )}

      {/* The introspection drawer (§6.4 / criteria 6 & 8), docked right. Keyed on the run id like
          its siblings, so navigating between runs refetches rather than showing the previous run's
          economics — which on this surface would be a wrong number a user would act on. */}
      {introspectOpen && (
        <IntrospectPanel runId={runId} onClose={() => setIntrospectOpen(false)} />
      )}

      {/* The steering + judge-triage drawer (R14 / criterion 8), docked right. Mounted only for
          a live run — a terminal run cannot act on a steer, and the backend refuses one anyway.
          Auto-closes if the run reaches a terminal state while open. */}
      {run && steerOpen && !isTerminal(run.status) && (
        <SidePanel title="Steer run" icon={<MessageSquarePlus size={18} />} onClose={() => setSteerOpen(false)} fillHeight>
          <SteeringPanel runId={runId} projectId={run.project_id} nodes={run.nodes} onSteered={refetch} />
        </SidePanel>
      )}
      </div>
    </div>
  )
}
