import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ChevronDown, ChevronRight, FolderGit2, GitBranch, MessageSquarePlus, MessageSquareCode, Package, Pause, Pencil, Play, RotateCcw, ScanSearch, Scale, SkipForward, X, XCircle } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { Segmented } from '../../ui/Segmented'
import { Loading } from '../../ui/ListScaffold'
import { QuietButton } from '../../ui/QuietButton'
import { SidePanel } from '../../ui/SidePanel'
import { api, type UsageBudget, type WorkflowContinuation, type WorkflowRunDetailData } from '../../lib/api'
import { accentChip } from '../../design/accent'
import { notify } from '../../app/appSdk'
import { confirm, promptForm } from '../../ui/dialog'
import { PageTitle } from '../../ui/PageTitle'
import { fmtElapsed, isPrelaunch, isTerminal, itemProgress, nodeLabel, nodeLook, runLook, stepName } from './workflowMeta'
import { PolicyOverridesPanel, overlayOf } from './PolicyOverridesPanel'
import { byInstancePath } from './instancePathOrder'
import { buildTree, initialCollapsed, summarize, summaryLabel, visibleRows } from './nodeTree'
import { useWorkflowStream } from './useWorkflowStream'
import { DagView } from '../tasks/DagView'
import { layoutRunDag } from './runDag'
import { tokenForNode } from './surfacingMeta'
import { reentrySummary, revalidateNotice, revalidateSummary } from './revalidate'
import { confirmationPreview, rewindNode } from './reentry'
import { WorkflowAsk } from './WorkflowAsk'
import { RunToolApprovals } from './RunToolApprovals'
import { capsHaveRoom, readEscalations, retryWindow, spendCapStop, stoppedAtBudget } from './attentionMeta'
import { budgetLine, capsReached, raisedCapProblem } from './runBudgetMeta'
import { EscalationPanel } from './EscalationPanel'
import { NodeInspectorDrawer } from './NodeInspectorDrawer'
import { SteeringPanel } from './SteeringPanel'
import { WorkspacePanel } from './WorkspacePanel'
import { OutboxPanel } from './OutboxPanel'
import { IntrospectPanel } from './IntrospectPanel'
import { LedgerRailsPanel } from './LedgerRailsPanel'
import { DeliverablePanel } from './DeliverablePanel'
import { ReviewTriagePanel } from './ReviewTriagePanel'

/** One workflow run, live.
 *
 *  Snapshot-then-subscribe: the SSE endpoint writes the full status BEFORE the stream
 *  opens, so the first frame populates the view. A lifecycle event is a REFETCH CUE, not a
 *  patch source — the engine's own status projection stays the single truth, and applying
 *  partial patches here would let the view drift from the run it claims to show.
 *
 *  A terminal run does not subscribe at all: its stream would close immediately anyway, and
 *  the status it already has is final. */
export function WorkflowRunDetail({ runId, onBack, onOpenRun, deepLinkNodeId = null }: {
  runId: string
  onBack: () => void
  /** Open another run's page — where Fork and Retry take the user, because the run they create
   *  is a different run with its own Start, controls and outcome. */
  onOpenRun: (runId: string) => void
  /** The node named by `?node=<id>` — the chat card's active-node deep link. Seeds and
   *  then follows the inspector's open node, so arriving from that link lands ON the node rather
   *  than on the run with nothing open. Absent on every other entry into this page. */
  deepLinkNodeId?: string | null
}) {
  const [run, setRun] = useState<WorkflowRunDetailData | null>(null)
  const [conts, setConts] = useState<WorkflowContinuation[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  // The day's spend caps, read for a run a spend cap stopped (`spendCapStop`).
  const [capBudget, setCapBudget] = useState<UsageBudget | null>(null)
  // The node whose inspector drawer is open. Null = closed. Holds the node_id — the
  // drawer fetches on open, so nothing is loaded until a row's Inspect is actually clicked.
  const [inspectNodeId, setInspectNodeId] = useState<string | null>(deepLinkNodeId)
  // The `?node=<id>` deep link, FOLLOWED rather than merely seeded: a second link to the same run
  // (a different node in a later chat card) does not remount this page, so a one-shot `useState`
  // seed would silently keep showing the first node. Local state stays the render source — a row's
  // own Inspect click must not depend on a URL round-trip — and this only pushes into it.
  useEffect(() => { if (deepLinkNodeId) setInspectNodeId(deepLinkNodeId) }, [deepLinkNodeId])
  // The mid-run steering + judge-triage panel. Docked to the right like
  // the inspector; toggled from the header, live runs only.
  const [steerOpen, setSteerOpen] = useState(false)
  // The code-run workspace review: changed files + the two reintegration verbs. Closed by
  // default and fetched on open — answering costs a `git status` plus a conflict probe, and most
  // runs are never reviewed. Available on a TERMINAL run too, which is exactly when a user wants
  // to decide what to do with the work.
  const [workspaceOpen, setWorkspaceOpen] = useState(false)
  const [outboxOpen, setOutboxOpen] = useState(false)
  // The reviewer-comment triage drawer. Closed by default
  // and fetched on open — the read costs a live `git diff` plus a ledger scan, and it re-anchors
  // every finding on each open rather than caching a verdict that goes stale as the worker works.
  // On BOTH sides of the terminal split: mid-run an accepted finding is steered into the next
  // iteration, and after, it is still the surface that records what the reviewer got wrong.
  const [reviewOpen, setReviewOpen] = useState(false)
  // The introspection drawer: the nine questions, the cost/latency strip, the template
  // p50/p95 card, the said-no badges and the Proof section. Closed by default and fetched on
  // open — answering costs a cross-run ledger read, and most runs are never audited.
  const [introspectOpen, setIntrospectOpen] = useState(false)
  // The two ledger rails: the findings rail and the verdict/ROI rail, which the
  // loop cockpit has had and a run detail had not. Closed by default and fetched on open like its
  // siblings, though this read is the cheap one — a single run's own ledger, no cross-run scan.
  // On BOTH sides of the terminal split: mid-run the findings rail is what a step produced so far,
  // and after, it is the per-step cost and judge trail behind a finished result.
  const [railsOpen, setRailsOpen] = useState(false)
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
  const { connected } = useWorkflowStream(runId, live, {
    onSnapshot: (snap) => { setRun(snap); setLoading(false) },
    onLifecycle: () => scheduleRefetch(),
  })

  const act = useCallback(async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
      await refetch()
    } catch (e) {
      notify(e instanceof Error ? e.message : `${label} failed`, 'error')
    } finally {
      setBusy(false)
    }
  }, [refetch])

  // Resume. A run paused at a cap it reached goes on only with that cap raised, which its owner
  // sets here (the server refuses a resume that would only pause it again, and anyone but her);
  // one whose dollar budget could not count a step that had no price goes on past that step.
  const resume = useCallback(async () => {
    if (!run) return
    const hit = capsReached(run)
    if (!hit.tokens && !hit.dollars) {
      await act('Resume', () => api.resumeWorkflowRun(runId, {}))
      return
    }
    const spent = run.spend ?? { tokens: 0, dollars: 0, unpriced_steps: 0 }
    const caps = run.budget ?? { max_tokens: 0, max_cost: 0 }
    const answers = await promptForm({
      title: 'Raise its budget to resume',
      body: run.error ? `${run.error} Set more than it has spent, or 0 for no budget.` : undefined,
      fields: [
        ...(hit.dollars ? [{
          name: 'max_cost', label: 'Dollar budget', initial: String(caps.max_cost), required: true,
          validate: (v: string) => raisedCapProblem(v, spent.dollars, 'dollars'),
        }] : []),
        ...(hit.tokens ? [{
          name: 'max_tokens', label: 'Token budget', initial: String(caps.max_tokens), required: true,
          validate: (v: string) => raisedCapProblem(v, spent.tokens, 'tokens'),
        }] : []),
      ],
      confirmLabel: 'Resume',
    })
    if (answers === null) return
    const budget: { max_tokens?: number; max_cost?: number } = {}
    if (hit.dollars) budget.max_cost = Number(String(answers.max_cost).trim().replace(/^\$/, ''))
    if (hit.tokens) budget.max_tokens = Number(String(answers.max_tokens).trim())
    await act('Resume', () => api.resumeWorkflowRun(runId, { budget }))
  }, [act, run, runId])

  // What each step is called, by its id, for the surfaces that are handed only an id — the
  // dialogs, the inspector, the escalation panel, an expired ask. Every one names a step the way
  // the run's own ending and failure lines do: by its label (`stepName`).
  const nameOf = useMemo(() => {
    const names = new Map<string, string>()
    for (const n of run?.nodes ?? []) if (n.node_id && !names.has(n.node_id)) names.set(n.node_id, stepName(n))
    return (nodeId: string) => names.get(nodeId) || nodeId
  }, [run])

  const answer = useCallback(async (cont: WorkflowContinuation, value: unknown, alwaysAllow: boolean) => {
    await act('Answer', () => api.resumeWorkflowRun(runId, {
      answer: value, resume_token: cont.resume_token, always_allow: alwaysAllow,
    }))
  }, [act, runId])

  const rewind = useCallback(async (nodeId: string) => {
    await act('Rewind', () => rewindNode(runId, nodeId))
  }, [act, runId])

  const runFrom = useCallback(async (nodeId: string) => {
    await act('Run from', async () => {
      try {
        await api.workflowRunFrom(runId, { node_id: nodeId })
        return
      } catch (error) {
        const preview = confirmationPreview(error)
        if (preview === null) throw error
        const ok = await confirm({
          title: `Run from “${nameOf(nodeId)}”?`,
          body: reentrySummary('run-from', preview),
          confirmLabel: 'Run from',
        })
        if (!ok) return
        await api.workflowRunFrom(runId, { node_id: nodeId, confirm_cascade: true })
      }
    })
  }, [act, nameOf, runId])

  // Mid-flight edit of a node's prompt. The user pauses a running
  // workflow, edits a stage's instruction, and resumes. The edit is a real spec mutation
  // (`update_node`), but the calibration point is the WARNING: a bundled template carries a
  // typed doc block whose judge calibration is tuned to the prompt it shipped with, so editing
  // that prompt can silently invalidate it. We surface that BEFORE applying — the user
  // confirms the trade rather than discovering later that the judge is grading against a
  // rubric the run no longer matches.
  const editNodePrompt = useCallback(async (nodeId: string) => {
    const answers = await promptForm({
      title: `Edit “${nameOf(nodeId)}”`,
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
        notify(res.issues?.[0]?.message ?? 'The edit was rejected.', 'error')
        return
      }
      notify(revalidateSummary(res.preview))
    })
  }, [act, nameOf, runId])

  const cancel = useCallback(async () => {
    const ok = await confirm({
      title: 'Cancel this run?',
      body: 'In-flight steps are stopped. Completed work is kept.',
      confirmLabel: 'Cancel run',
      danger: true,
    })
    if (ok) await act('Cancel', () => api.cancelWorkflowRun(runId))
  }, [act, runId])

  // Fork lands ON the child: it is a draft, and its own page is where Start and the prelaunch
  // policy editor live. Left on the parent, the only trace of the new run was a toast naming its id.
  // Not routed through `act`, whose refetch reads THIS run and could land after the navigation,
  // painting the parent over the child.
  const fork = useCallback(async () => {
    setBusy(true)
    try {
      const res = await api.forkWorkflowRun(runId, { note: 'branched from the run view' })
      notify(`Forked to ${res.child_run_id}. Not isolated: ${res.shared_axes.length} shared axes.`)
      onOpenRun(res.child_run_id)
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Fork failed', 'error')
    } finally {
      setBusy(false)
    }
  }, [runId, onOpenRun])

  // Retry = a NEW attempt. A finished run never runs again (`models.RESUMABLE_ENDED_RUN_STATUSES` is
  // empty by design) and rewind needs a live controller, so the verbs that apply are fork + start:
  // the child keeps every step the parent finished and re-runs what did not. Offered only for
  // failures the engine itself calls retryable (`retryWindow`), where the same work can succeed
  // once its cause clears. If the start is refused the user still lands on the child's draft,
  // whose Start button is the same call, with the refusal in the notice.
  //
  // The run is read again first. A provider's circuit breaker can open after this page loaded,
  // because every call to that provider counts toward it, and a Retry inside the window started a
  // run that failed in microseconds without a call. The fresh read shows when it can run instead,
  // so a read that FAILS stops the Retry and says so: treating it as "no window" started the very
  // run the read exists to hold back.
  //
  // A run a spend cap stopped reads the caps the same way: a Retry while they are still full would
  // start a run the same cap refuses before its first call.
  const retry = useCallback(async () => {
    setBusy(true)
    let child = ''
    try {
      const fresh = await api.workflowRun(runId)
      setRun(fresh)
      if ((retryWindow(fresh)?.retryAt ?? 0) > Date.now() / 1000) return
      if (spendCapStop(fresh)) {
        const caps = await api.usageBudget()
        setCapBudget(caps)
        if (!capsHaveRoom(caps)) return
      }
      child = (await api.forkWorkflowRun(runId, { note: 'retry after a transient failure' })).child_run_id
      await api.startDraftWorkflowRun(child)
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Retry failed', 'error')
    } finally {
      setBusy(false)
    }
    if (child) onOpenRun(child)
  }, [runId, onOpenRun])

  // Launch a run that has not executed yet (#372). Unconfirmed, unlike Cancel: starting is the
  // affirmative action the draft exists for, and a confirm on the primary verb of a surface reads
  // as a warning about something that is simply what the user came here to do. No refetch race —
  // `act` refetches, and the SSE stream is already open for a non-terminal run, so the first tick
  // arrives on the stream rather than waiting for a poll.
  //
  // The header branch this feeds has THREE phases, not two. `isTerminal` is a binary split and
  // `draft` is neither side of it: a run that has not started is not terminal, so it fell into the
  // running branch and rendered Pause + Cancel — controls for work in flight, on a run with no
  // work in flight and no way to start any. The only outcome a forked run offered its author was
  // cancelling something that never ran. The branch is ordered prelaunch → active → ended so it
  // reads as the lifecycle it mirrors (`models.RUN_PHASES`), and is gated on `isPrelaunch` rather
  // than `=== 'draft'` so a future prelaunch status inherits it.
  //
  // Kept OUT of the JSX as a `//` comment on purpose: `token-lint` skips lines opening `//`, `*`
  // or `/*` but not a `{/*` JSX comment, so a three-digit `#372` inside one reads as a raw CSS
  // hex. Keeping the prose here also keeps the header's right slot short, which matters more than
  // it looks — see the scanner note in `headerActionsAdoption.test.ts`.
  const start = useCallback(async () => {
    await act('Start', () => api.startDraftWorkflowRun(runId))
  }, [act, runId])

  // A loop that stopped at the budget it was given ends the run `escalated`, and it is not a step
  // that gave up: the page says it stopped at its budget, as the bell does, on every part of it.
  const atBudget = run ? stoppedAtBudget(run.status, run.attention) : false
  // The caps a run paused at its budget has reached, which its Resume raises (`capsReached`).
  const reached = run ? capsReached(run) : { tokens: false, dollars: false }
  const look = run ? runLook(run.status, run.held, atBudget) : null
  const StatusIcon = look?.icon
  // What you declined, less what the line a loop waiting on your Deny shows already says.
  const declinedListed = (run?.declined ?? []).filter(
    (line) => !(run?.declined_wait && run.error?.startsWith(line)),
  )

  // Sorted by instance path so the list reads in the spec's own order, and indented to its
  // tree shape — a flat list of twenty node ids is unreadable on a real workflow. Numerically,
  // matching the engine: a string sort puts item 10 ahead of item 2 (issue #568).
  const nodes = useMemo(() => [...(run?.nodes ?? [])].sort(byInstancePath), [run])

  // What the run last showed of the inspected node: its latest instance (the one the inspector
  // reads) and that instance's state. The drawer reads again whenever it changes, so a drawer opened
  // on a running node (a chat card's deep link) follows it to its final state.
  const inspectedVersion = useMemo(() => {
    const latest = nodes.filter((n) => n.node_id === inspectNodeId).at(-1)
    return latest ? `${latest.instance_path}:${latest.state}` : ''
  }, [nodes, inspectNodeId])

  // Collapsible containers. The `deep-research` template expands to 21 rows and 18
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

  // What the engine knew when it gave up (#565): EVERY escalation, oldest first, from the
  // ledger-backed `run.escalations`. `run.attention` is one slot that each escalation overwrote,
  // so a run whose two steps both gave up explained only the second. Not status-gated: a run
  // whose failed `foreach` items were skipped finishes with them escalated, and says so.
  const escalations = useMemo(() => readEscalations(run?.escalations), [run])
  // The instances that stopped at their budget, so their rows read as the run they ended does.
  const budgetStops = useMemo(
    () => new Set(escalations.filter((e) => e.cause === 'budget' && e.instancePath).map((e) => e.instancePath)),
    [escalations],
  )

  // Whether Retry is offered, and from when (`retryWindow`: every escalated step's own
  // `failure.retryable`, and `retry_at` while a provider's breaker refuses calls).
  const retryable = useMemo(() => retryWindow(run), [run])

  // A run a spend cap stopped is retried once the cap has room: raised or removed in Settings →
  // Guardrails (read again when the page is shown again), or reset at the end of the day (read
  // again then).
  const capStop = useMemo(() => spendCapStop(run), [run])
  const readCaps = useCallback(() => {
    api.usageBudget().then(setCapBudget, () => setCapBudget(null))
  }, [])
  useEffect(() => {
    if (!capStop) return
    readCaps()
    const onShow = () => { if (document.visibilityState === 'visible') readCaps() }
    window.addEventListener('focus', onShow)
    document.addEventListener('visibilitychange', onShow)
    return () => {
      window.removeEventListener('focus', onShow)
      document.removeEventListener('visibilitychange', onShow)
    }
  }, [capStop, readCaps])
  const capResetsAt = capBudget?.resets_at ?? 0
  useEffect(() => {
    if (!capStop || capResetsAt <= 0) return
    const timer = window.setTimeout(readCaps, Math.max(0, capResetsAt * 1000 - Date.now()) + 1000)
    return () => window.clearTimeout(timer)
  }, [capStop, capResetsAt, readCaps])

  // The countdown to `retryAt`, ticking once a second from the moment it is known and stopping
  // when it lapses.
  const [clock, setClock] = useState(() => Date.now() / 1000)
  const retryAt = retryable?.retryAt ?? 0
  useEffect(() => {
    if (retryAt <= 0) return
    const tick = () => {
      const now = Date.now() / 1000
      setClock(now)
      if (now >= retryAt) window.clearInterval(timer)
    }
    const timer = window.setInterval(tick, 1000)
    tick()
    return () => window.clearInterval(timer)
  }, [retryAt])
  const retryWaitSecs = Math.max(0, Math.ceil(retryAt - clock))

  // The graph's Approve/Deny reads the continuations this view ALREADY fetches on every refetch —
  // no second request. A `waiting` node is only ANSWERABLE when a live resume token exists for it:
  // a `wait` node is parked on the clock, and offering approval on one would ask the user to answer
  // something nobody asked them.
  const dag = useMemo(
    () => layoutRunDag(nodes, {
      continuations: conts,
      label: (n) => (n.item_label ? `${stepName(n)} · ${n.item_label}` : stepName(n)),
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
        if (res.ok === false) notify(res.message ?? 'Could not resolve the gate.', 'error')
        else notify(`Gate ${res.verb}d.`)
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
          {/* 🔴 THIS ROUTE HAD NO h1 AT ALL. `#/workflows` renders `h1 "Workflows"`, and opening
              a run navigates to `#/workflows/runs/<id>` where the h1 count drops to ZERO — axe
              `page-has-heading-one`, and a screen-reader user skimming by heading lands on nothing.
              The rule this settles: when the URL's PATH identifies the entity,
              that entity IS the destination and takes the h1; when the entity is a query param on a list
              route (`?item=`, `?open=` — the peek), the list keeps its h1 and the panel gets none. All
              five surfaces measured agree, and it is the row lesson one level up: a
              destination is named by its identity, not by its category. */}
          {/* A run started as a loop is named by the loop (`title`), not by its template — the page
              read "general-project" for a loop the user had just named. */}
          {run && <PageTitle className="truncate">{run.title || run.workflow}</PageTitle>}
        </div>}
        // The run's status and its feed's liveness sit UNDER the row, not in it (the TopBar's
        // `below`): in the row they were `shrink-0` beside a truncating title, so a long title left
        // them no room and "Needs you" painted over the Workspace button. Below, they can only wrap.
        below={run && (live || (look && StatusIcon)) ? (
          <>
            {look && StatusIcon && (
              <span data-type="caption" data-run-status className={`inline-flex shrink-0 items-center gap-1 ${look.tone}`}>
                <StatusIcon size={13} className={look.spin ? 'animate-spin' : ''} /> {look.label}
              </span>
            )}
            {/* FEED liveness, distinct from the RUN's status beside it. A live run whose stream has
                dropped keeps showing "Running" while nothing arrives — indistinguishable from a run
                that is simply quiet. Same dot-plus-WORD form `settings/DiagnosticsPanel` already ships
                for its own SSE feed, so the vocabulary is not re-invented; the colour only confirms the
                word, which is what keeps it clear of 1.4.1. Shown only while `live` — a terminal run
                has no stream to be connected to. */}
            {live && (
              <span data-type="caption" className="inline-flex shrink-0 items-center gap-1 text-on-surface-low">
                <span className="inline-block size-1.5 rounded-pill"
                  style={{ background: connected ? 'var(--color-ok)' : 'var(--color-on-surface-low)' }} />
                {connected ? 'Streaming' : 'Connecting…'}
              </span>
            )}
          </>
        ) : undefined}
        // The run's controls are the header's responsive cluster (`HeaderActions`): they shed
        // their labels, then fall into a `…` menu, as the row narrows, so the title keeps room and
        // nothing paints over anything. Eight labelled buttons in a fixed row took the whole band
        // at 1280px on a live run: the title read one letter and the controls ran over it.
        // The five panel toggles go into the menu first; the lifecycle actions stay.
        right={run ? (
          <HeaderActions>
            {/* Workspace on BOTH sides of the terminal split, unlike Steer/Pause/Fork: reviewing
                what a run changed is the one thing a user wants equally mid-run (is it touching
                what I expected) and after (do I take this work). */}
            <HeaderControl icon={FolderGit2} label="Workspace" priority="low" ariaExpanded={workspaceOpen}
              onClick={() => setWorkspaceOpen((v) => !v)}
              title="Workspace — changed files and how to take this work" hint="Changed files and how to take this work" />
            {/* Artifacts, likewise on both sides of the terminal split: mid-run it answers "what has
                it produced so far", and after, it is where the deliverable and its version diff
                live. It is also the only surface that can hand a live run a file. */}
            <HeaderControl icon={Package} label="Artifacts" priority="low" ariaExpanded={outboxOpen}
              onClick={() => setOutboxOpen((v) => !v)}
              title="Artifacts — what this run published, version diffs, and handing it files" hint="What this run published, version diffs, and handing it files" />
            {/* Introspect, on both sides of the terminal split for the strongest reason of the
                three: mid-run it answers "what will you do next if I say nothing", and after, it
                is the Proof section that lets a user review unattended work without reading the
                transcript. */}
            <HeaderControl icon={ScanSearch} label="Introspect" priority="low" ariaExpanded={introspectOpen}
              onClick={() => setIntrospectOpen((v) => !v)}
              title="Introspect — cost, latency, gates, timeline and proof" hint="Cost, latency, gates, timeline and proof" />
            {/* Rails, on both sides of the terminal split like Introspect and for the neighbouring
                reason: mid-run the findings rail is what each step has produced so far, and after,
                it is the per-step cost and the judge trail behind the result. Separate from
                Introspect because it answers about THIS run only — no cross-run scan. */}
            <HeaderControl icon={Scale} label="Rails" priority="low" ariaExpanded={railsOpen}
              onClick={() => setRailsOpen((v) => !v)}
              title="Rails — the per-step findings rail and the judge verdict/ROI rail from this run's ledger" hint="Per-step findings and the judge verdict/ROI rail" />
            {/* Review, likewise on both sides: mid-run an accepted finding is steered into the next
                iteration, and on a finished run it is still where a reviewer's misses get recorded.
                Nothing here writes to the code without an explicit accept. */}
            <HeaderControl icon={MessageSquareCode} label="Review" priority="low" ariaExpanded={reviewOpen}
              onClick={() => setReviewOpen((v) => !v)}
              title="Review — accept or reject this run's line-anchored findings" hint="Accept or reject this run's line-anchored findings" />
            {/* Three lifecycle phases, not two — see the note beside `start` above. */}
            {isPrelaunch(run.status) ? (
              <>
                <HeaderControl icon={Play} label="Start" priority="primary" onClick={start}
                  title="Start this run — it has not executed yet" />
                <HeaderControl icon={X} label="Cancel" onClick={cancel} title="Cancel this run before it starts" />
              </>
            ) : !isTerminal(run.status) ? (
              <>
                <HeaderControl icon={MessageSquarePlus} label="Steer" ariaExpanded={steerOpen}
                  onClick={() => setSteerOpen((v) => !v)}
                  title="Steer this run — queue an instruction or accept a judge comment" />
                {/* A pause STOPS the step in flight and re-queues it — the old "in-flight steps
                    finish" promise was a pause that paused nothing. It lands on the controller's
                    next step, so the gap between the click and that step says so instead of
                    looking ignored. A paused run is resumed here: it had no way forward on this
                    page. */}
                {run.status === 'paused' ? (
                  <HeaderControl icon={Play} label="Resume" priority="primary"
                    onClick={resume}
                    title={run.at_budget
                      ? (reached.tokens || reached.dollars
                        ? 'Resume — raise the budget it reached, and it goes on'
                        : 'Resume — it goes on without counting the step that had no price')
                      : run.declined_wait
                        ? 'Resume — its next cycle runs, with anything you told it'
                        : 'Resume — the step the pause stopped runs again'} />
                ) : run.pause_requested ? (
                  <HeaderControl icon={Pause} label="Pausing…" priority="primary" title="Pausing"
                    disabled disabledReason="the step in flight is being stopped" />
                ) : (
                  <HeaderControl icon={Pause} label="Pause" priority="primary"
                    onClick={() => act('Pause', () => api.pauseWorkflowRun(runId))}
                    title="Pause — stops the step in flight; Resume runs it again" />
                )}
                <HeaderControl icon={X} label="Cancel" onClick={cancel} title="Cancel this run" />
              </>
            ) : (
              <HeaderControl icon={GitBranch} label="Fork" priority="primary" onClick={fork}
                title="Branch a new run from this one; the original is untouched" />
            )}
          </HeaderActions>
        ) : undefined}
      />

      <div className="flex min-h-0 flex-1">
      <div className="min-h-0 flex-1 overflow-y-auto p-l">
        {loading && !run ? <Loading what="this run" /> : !run ? (
          <p data-type="body-s" className="text-on-surface-low">This run could not be loaded.</p>
        ) : (
          <div className="mx-auto flex max-w-[var(--content-width)] flex-col gap-l">
            {/* Pending asks come FIRST: they are the only thing here a user can act on. */}
            {conts.map((c) => (
              <WorkflowAsk key={c.resume_token} continuation={c} runId={runId} busy={busy} onAnswer={answer}
                stepName={nameOf(c.node_id)} />
            ))}

            {/* …and beside them, the run's pending TOOL approvals (issue 258). A stage that
                spawns a subagent blocks on the global approvals queue rather than on an engine
                gate, so it rendered nothing here while a gate rendered the card above — two shapes
                of "the run is waiting on you", one of them invisible on the surface the user is
                watching. Spelled "issue 258" rather than with a hash: `token-lint` skips lines
                opening `//`, `*` or `/*` but not a `{/*` JSX comment, so a three-digit ref there
                reads as a raw CSS hex. */}
            <RunToolApprovals runId={runId} />

            {/* A declined run's line is why it ended, not a fault: it names the approval and who
                said no, in the informational tone its status takes (`runLook('declined')`). A run
                that stopped at its budget is not a fault either, nor one whose loop's stop ended it
                ("Stopped because its loop … was stopped."), nor one whose loop waits for you after
                your Deny. */}
            {run.error && (
              <p data-type="body-s" className={run.status === 'declined' || run.status === 'cancelled' || atBudget || run.at_budget || run.declined_wait ? 'text-on-surface-var' : 'text-danger'}>{run.error}</p>
            )}

            {/* What you declined in the run, in one list: each loop cycle that ended at your Deny
                (it ran nothing more of itself and asked you nothing again) and each step you
                declined part of. The cycle a loop waits on is said once, by the line above; it is
                listed here once the wait is over. */}
            {declinedListed.length > 0 && (
              <section aria-labelledby="declined-heading" className="flex flex-col gap-xs rounded-lg bg-surface-container px-m py-s">
                <h2 id="declined-heading" data-type="label-s" className="flex items-center gap-xs text-on-surface">
                  <XCircle size={14} className="shrink-0 text-on-surface-low" /> Declined by you
                </h2>
                {declinedListed.map((line, i) => (
                  <p key={i} data-type="body-s" className="text-on-surface-var">{line}</p>
                ))}
              </section>
            )}

            {/* Incident mode holds a running run: its status stays `running`, so without this the
                page read as working while nothing ran. */}
            {run.held && (
              <p role="status" data-type="body-s" className="text-on-surface-var">{run.held}</p>
            )}

            {/* Beneath the error line, because it explains the same failure in more depth: the
                line names the step that failed and its cause, and this adds the attempts and the
                engine's own next move. A run recorded before that line existed has it EMPTY, and
                then this is its only account. */}
            {escalations.length > 0 && (
              <EscalationPanel
                reads={escalations}
                runStatus={run.status}
                runError={run.error ?? ''}
                retry={retryable || capStop ? { onRetry: retry, busy, waitSecs: retryWaitSecs } : undefined}
                editHref={run.workflow ? `#/workflows/defs/${encodeURIComponent(run.workflow)}/edit` : undefined}
                nameOf={nameOf}
                atBudget={atBudget}
                spendCap={capStop ? {
                  fix: capStop.fix,
                  room: capBudget ? capsHaveRoom(capBudget) : null,
                  resetsAt: capResetsAt,
                } : undefined}
              />
            )}

            <div data-type="caption" className="flex flex-wrap items-center gap-l text-on-surface-low">
              <span>run <span className="font-mono">{run.run_id}</span></span>
              <span>spec v{run.spec_version}</span>
              {/* What it has spent beside what it may (`budgetLine`): its tokens and its dollars,
                  each against its cap when it has one, and what the dollar figure leaves out. */}
              {budgetLine(run).map((piece) => <span key={piece} className="tabular-nums">{piece}</span>)}
              {run.elapsed_secs ? <span className="tabular-nums">{fmtElapsed(run.elapsed_secs)}</span> : null}
            </div>

            {/* The prelaunch policy editor. PRELAUNCH ONLY, mirroring the
                backend's phase gate — once launched, the engine's whole-row saves would
                silently revert a live overlay edit and the route 409s, so offering the
                editor on a running run would teach the user the UI lies. Keyed on the run
                id so navigating between runs re-seeds from that run's own overlay — prefixed,
                because `DeliverablePanel` below is a sibling keyed on the same run: two siblings
                with one key left React re-rendering one while the other's stale copies piled up
                on the page (3–6 "Policy overrides" editors, the top ones dead, and a save from
                one of those refused with no notice anywhere). */}
            {isPrelaunch(run.status) && (
              <PolicyOverridesPanel
                key={`policy:${run.run_id}`}
                runId={runId}
                initial={overlayOf(run)}
                onSaved={() => refetch()}
              />
            )}

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
                  label={`Run graph — ${dag.nodes.length} ${dag.nodes.length === 1 ? 'step' : 'steps'}`}
                  onNodeClick={(id) => toggle(id)}
                  // The declared-but-unwired seam, finally bound. Passed only
                  // when the run can still be answered: a terminal run's gate cannot be resolved,
                  // and an Approve button that always fails teaches the user the UI lies.
                  onApprove={isTerminal(run.status) ? undefined : (id) => resolveGate(id, true)}
                  onDeny={isTerminal(run.status) ? undefined : (id) => resolveGate(id, false)}
                />
              </div>
            ) : null}

            <div className={`flex flex-col gap-xs${view === 'graph' ? ' hidden' : ''}`}>
              {shownRows.map(({ node: n, depth, descendants, collapsible }) => {
                const nl = nodeLook(n.state, budgetStops.has(n.instance_path))
                const NIcon = nl.icon
                // Re-entry (edit / rewind / run-from) is a mutation the LIVE controller applies at its
                // drain point, so it exists only while the run is active. A draft has no controller
                // yet and a finished run has none any more: offered on a fork's draft, Rewind
                // answered 409 "start the run before rewind" on every click. A draft's verb is
                // Start; a finished run's are Fork and, for a retryable failure, Retry.
                const canReenter = !isTerminal(run.status) && !isPrelaunch(run.status) && !!n.node_id
                const isCollapsed = collapsed.has(n.instance_path)
                const summary = collapsible ? summarize(descendants, nodes) : null
                return (
                  <div
                    key={n.instance_path}
                    className="group flex items-center gap-m rounded-lg px-s py-xs hover:bg-surface-high"
                    style={{ paddingLeft: `calc(var(--spacing-s) + ${depth} * 1rem)` }}
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
                        <span data-type="body-s" className="truncate text-on-surface"
                          title={n.label ? `${nodeLabel(n)} (${n.node_id})` : undefined}>{nodeLabel(n)}</span>
                        {/* The per-item label: what makes one row of a twelve-item
                            fan-out identifiable. Dimmed — it is which, not what. */}
                        {itemProgress(n) && (
                          <span data-type="caption" className="min-w-0 shrink truncate text-on-surface-low tabular-nums">
                            {itemProgress(n)}
                          </span>
                        )}
                        {/* What a collapsed subtree DID, counted by state rather than reduced to a
                            percentage: "18 skipped" says the branch was not taken and "17 done · 1
                            failed" says exactly where to look. A progress bar says neither. */}
                        {isCollapsed && summary && (
                          <span data-type="caption" className="min-w-0 shrink truncate text-on-surface-low">
                            {summaryLabel(summary)}
                          </span>
                        )}
                      </div>
                      {(n.degraded_reason || n.failure?.cause_plain) && (
                        <div data-type="caption" className="truncate text-on-surface-low">
                          {n.degraded_reason || n.failure?.cause_plain}
                        </div>
                      )}
                      {/* What you declined while it worked, on the step it stopped. */}
                      {n.declined && (
                        <div data-type="caption" className="truncate text-on-surface-low" title={n.declined}>
                          {n.declined}
                        </div>
                      )}
                      {/* The remediation is a DIFFERENT fact from the cause — it is the next
                          action, and dropping it leaves the user with a diagnosis only. */}
                      {n.failure?.remediation && (
                        <div data-type="caption" className="truncate text-on-surface-low">{n.failure.remediation}</div>
                      )}
                      {/* A fallback in the user's model chain served this step because the model
                          it asked for could not. The row's `done` and the model it names are both
                          true, which is why this line exists: without it the step read as the model
                          it asked for. `text-warning`, not the dimmed `on-surface-low` the lines
                          above use: dimming the one line that qualifies the status badge would
                          hide it. */}
                      {(n.model_substituted ?? []).map((line) => (
                        <div
                          key={line}
                          data-type="caption"
                          data-testid="node-model-substituted"
                          className="truncate text-warning"
                          title={line}
                        >
                          {line}
                        </div>
                      ))}
                    </div>
                    {/* Cache-origin, at a glance on the ROW rather than only inside the
                        per-node drawer. "Did my edit actually re-run anything?" is a question about
                        the whole run, and answering it by opening twenty drawers in turn is the
                        per-node version of a run-level question.

                        🔑 ONE VOCABULARY, three places. Same word and same title text as
                        `NodeInspectorDrawer`'s badge, and the same `accentChip` it paints the
                        cached state with — a different tint here would make "cached" mean two
                        things on two surfaces of the same run.

                        🪤 Only the CACHED state is marked. The drawer renders `fresh` too, which is
                        right for a single node under inspection (a badge that failed to render is
                        otherwise indistinguishable from an absent one) and wrong for a list, where
                        it would put a chip on every row of every normal run. The word carries the
                        state, so the tint only confirms it. */}
                    {n.cached && (
                      <span
                        data-testid="node-cached-badge"
                        data-type="caption"
                        className="inline-flex shrink-0 items-center rounded-pill px-2 py-0.5"
                        style={accentChip}
                        title="Output served from the resume cache"
                      >
                        cached
                      </span>
                    )}
                    <span data-type="caption" className={`shrink-0 ${nl.tone}`}>{nl.label}</span>
                    {(canReenter || !!n.node_id) && (
                      <span className="flex shrink-0 items-center gap-xs opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                        {/* Inspect: the reconstructability drawer, on every step. A step still at
                            work answers with its live state (what it was given, no output yet), so
                            the step the run is working on can be looked at while it does; a
                            finished run is exactly when a user wants to reconstruct what one did. */}
                        {!!n.node_id && (
                          <QuietButton onClick={() => setInspectNodeId(n.node_id)} title="Inspect this node — resolved prompt, inputs, output, attempts and ledger">
                            <ScanSearch size={12} />
                          </QuietButton>
                        )}
                        {canReenter && (
                          <>
                            {/* Mid-flight edit: change this stage's instruction on a
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

            {/* The run's DOCUMENT deliverable + working log, the run-side answer to
                `GET /api/loops/{id}/report`. In the BODY rather than a drawer, for two reasons: the
                loop cockpit puts its Deliverable tab in the body too (so the loop and run sides read
                alike), and the COMMON case here is that there is no document yet — which a drawer
                would hide behind a click and a body section states outright. Keyed on the run id so
                navigating between runs refetches rather than showing the previous run's document,
                which on a reading surface would be the worst kind of wrong: plausible prose about
                someone else's work. Below the steps because it is what they produced. */}
            <DeliverablePanel key={`deliverable:${run.run_id}`} runId={runId} />
          </div>
        )}
      </div>

      {/* The node-inspector drawer, docked to the right and pushing the run body narrower.
          Keyed on the node id so switching nodes remounts and refetches rather than showing the
          previous node's data, and handed what the run shows of that node so it follows it. A
          terminal node's row exposes the Inspect trigger; a chat card's deep link opens any node.
          Mounted once the run has loaded: before it, the view knows nothing of the node, and a
          drawer that read then would read again the moment the run arrived. */}
      {run && inspectNodeId && (
        <NodeInspectorDrawer key={inspectNodeId} runId={runId} nodeId={inspectNodeId}
          name={nameOf(inspectNodeId)} nodeVersion={inspectedVersion}
          onClose={() => setInspectNodeId(null)} />
      )}

      {/* The workspace review drawer, docked right. Keyed on the run id so
          navigating between runs refetches rather than showing the previous run's diff. */}
      {workspaceOpen && (
        <WorkspacePanel runId={runId} onClose={() => setWorkspaceOpen(false)} />
      )}

      {/* The outbox / artifact drawer, docked right. Keyed on the run id for the same
          reason as the workspace drawer: navigating between runs must refetch, not show the previous
          run's artifacts. */}
      {outboxOpen && (
        <OutboxPanel runId={runId} onClose={() => setOutboxOpen(false)} />
      )}

      {/* The introspection drawer, docked right. Keyed on the run id like
          its siblings, so navigating between runs refetches rather than showing the previous run's
          economics — which on this surface would be a wrong number a user would act on. */}
      {introspectOpen && (
        <IntrospectPanel runId={runId} onClose={() => setIntrospectOpen(false)} />
      )}

      {/* The two ledger rails, docked right. Keyed on the run id like its siblings:
          these are per-step costs and judge scores, and showing the previous run's would be a wrong
          number on exactly the surface a user consults to find out what a run cost. */}
      {railsOpen && (
        <SidePanel title="Ledger rails" icon={<Scale size={18} />} onClose={() => setRailsOpen(false)} fillHeight>
          <LedgerRailsPanel runId={runId} />
        </SidePanel>
      )}

      {/* The reviewer-comment triage drawer, docked right. Keyed on the run id
          like its siblings. The panel re-anchors on mount, so opening it is what produces a fresh
          anchor verdict — there is no cached one to go stale. */}
      {reviewOpen && (
        <SidePanel title="Review findings" icon={<MessageSquareCode size={18} />} onClose={() => setReviewOpen(false)} fillHeight>
          <ReviewTriagePanel runId={runId} onDispatched={refetch} />
        </SidePanel>
      )}

      {/* The steering + judge-triage drawer, docked right. Mounted only for
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
