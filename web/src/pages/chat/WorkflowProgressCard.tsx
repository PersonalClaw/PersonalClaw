import { useCallback, useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowUpRight, Workflow } from 'lucide-react'
import { api, ApiError, type PendingApproval, type WorkflowBatchState } from '../../lib/api'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { messageEnter } from '../../design/motion'
import { accentChip } from '../../design/accent'
import { fvs } from '../../design/fontWeight'
import { Meter } from '../../ui/Meter'
import { Button } from '../../ui/Button'
import { workflowApprovalSession } from '../../app/approvalDestination'
import { attentionLine, readAttention, stoppedAtBudget } from '../workflows/attentionMeta'
import { foldEvent, foldSnapshot, type WorkflowViewModel } from '../workflows/workflowFold'
import { useWorkflowStream } from '../workflows/useWorkflowStream'
import { fmtElapsed, isTerminal, nodeLabel, nodeLook, runLook } from '../workflows/workflowMeta'
import { TextLink } from '../../ui/TextLink'
import type { ChatTurn, Segment, ToolSegment } from './chatTypes'

// Tools whose result means "a workflow run now exists worth watching". `workflow_start`
// creates one, and so does `subagent_run` given two or more tasks: they run as one batch run,
// each task a step whose card says how it ended. `workflow_status`/`workflow_observe` name one
// the agent inspected, and a user reading that turn wants the live thing, not the frozen text
// the tool returned.
const CREATING_TOOLS = new Set(['workflow_start', 'subagent_run'])
const WORKFLOW_TOOLS = new Set([...CREATING_TOOLS, 'workflow_status', 'workflow_observe'])

/** A run a chat's tool result names. `batch` is a `subagent_run` batch still waiting for its one
 *  ask, before it has a run (its card follows it to one); `workflow` is the definition a status
 *  read names, which is how a read of a batch's run is known for that batch's. */
export interface WorkflowRunRef { runId: string; created: boolean; batch?: string; workflow?: string }

/** The step states a finished run can carry that mean the step did not do its work. */
const FAILED_STEP_STATES = new Set(['failed', 'scope_violation', 'blocked', 'escalated'])
/** How many of them a chat card names before it counts the rest: a card is a glance. */
const FAILED_STEPS_SHOWN = 3

/** Recognize a workflow tool segment and pull the run id out of its output, or the batch it
 *  waits to start.
 *
 *  Matches the JSON the tools actually return (`"run_id": "<8 hex>"`, `"batch": "<name>"`) rather
 *  than a deep link, because these tools return structured results for a model to read — unlike
 *  the SDLC tools, which return a `/#/…` URL for a human. Anchoring on the real shape is what
 *  keeps this from silently stopping when a description is reworded. */
export function workflowRefFromTool(
  toolName: string | undefined,
  output: string | undefined,
): WorkflowRunRef | null {
  if (!toolName || !WORKFLOW_TOOLS.has(toolName) || !output) return null
  const created = CREATING_TOOLS.has(toolName)
  const workflow = output.match(/"workflow"\s*:\s*"([a-z0-9][a-z0-9-]*)"/)?.[1]
  const m = output.match(/"run_id"\s*:\s*"([0-9a-f]{6,})"/i)
  if (m) return { runId: m[1], created, ...(workflow ? { workflow } : {}) }
  const batch = toolName === 'subagent_run' ? output.match(/"batch"\s*:\s*"([a-z0-9][a-z0-9-]*)"/)?.[1] : undefined
  return batch ? { runId: '', created, batch } : null
}

/** The tool segments of a chat that show a run's live card: the first to name each run, one card
 *  per run however often the agent read it. A status read of a run already shown (by its id, or by
 *  the batch it is the run of) keeps its plain tool card, in the order the transcript reads. */
export function liveWorkflowCards(turns: ChatTurn[]): Set<Segment> {
  const shown = new Set<string>()
  const cards = new Set<Segment>()
  for (const turn of turns) {
    for (const seg of turn.segments) {
      if (seg.kind !== 'tool' || !(seg as ToolSegment).done) continue
      const ref = workflowRefFromTool((seg as ToolSegment).tool, (seg as ToolSegment).output)
      if (!ref) continue
      const keys = [ref.runId && `run:${ref.runId}`, ref.batch && `batch:${ref.batch}`].filter(Boolean) as string[]
      if (keys.some((k) => shown.has(k)) || (ref.workflow && shown.has(`batch:${ref.workflow}`))) continue
      keys.forEach((k) => shown.add(k))
      cards.add(seg)
    }
  }
  return cards
}

/** The card of a batch waiting for its one ask, until it has a run, which its card becomes.
 *
 *  Read off the batch's own record (`GET /api/workflows/batches/<name>`): it is what survives a
 *  reload and a restart, and what says why a batch never started. The ask is answered on the
 *  approvals queue, so the card reads again when one ends there, with a slow poll behind it. */
export function BatchStartCard({ name }: { name: string }) {
  const [state, setState] = useState<WorkflowBatchState | null>(null)
  const [gone, setGone] = useState(false)
  const load = useCallback(async () => {
    try {
      setState(await api.workflowBatch(name))
    } catch (e) {
      // Only a 404 means there is no such batch; any other miss keeps what the card has.
      if (e instanceof ApiError && e.status === 404) setGone(true)
    }
  }, [name])
  useEffect(() => { load() }, [load])
  useChatSocket((m: WsMessage) => {
    if (m.type === 'approval_resolved' || m.type === 'approval') load()
  }, load)
  const settled = state?.status === 'started' || state?.status === 'not_started'
  useEffect(() => {
    if (settled) return
    const t = window.setInterval(load, 5_000)
    return () => window.clearInterval(t)
  }, [settled, load])

  if (gone) return null
  if (state?.status === 'started' && state.run_id) {
    return <WorkflowProgressCard refObj={{ runId: state.run_id, created: true }} />
  }
  const count = state?.tasks ? `${state.tasks} tasks` : 'its tasks'
  const line = !state
    ? 'Loading…'
    : state.status === 'not_started'
      ? (state.error ? `${state.error[0].toUpperCase()}${state.error.slice(1)}.` : 'It never started.')
      : state.status === 'starting'
        ? `Allowed. Starting ${count}…`
        : `Waits for your Allow before any of ${count} start.`
  return (
    <motion.div {...messageEnter} className="my-s flex flex-col gap-s rounded-xl border border-outline-variant p-m">
      <div className="flex min-w-0 items-center gap-s">
        <Workflow size={15} className="shrink-0 text-on-surface-low" />
        <span data-type="label-s" className="min-w-0 flex-1 truncate text-on-surface" style={fvs(500)}>
          Batch of subagents
        </span>
        {/* The Inbox lists the ask for as long as it waits, a reload and a restart included. */}
        {state?.status === 'asking' && (
          <TextLink href="#/inbox" size="xs" icon={ArrowUpRight} iconPosition="trailing" iconSize={12}
            className="shrink-0 transition-colors" title="Answer its ask in your Inbox">
            Answer in Inbox
          </TextLink>
        )}
      </div>
      <p data-type="caption" className={state?.status === 'asking' ? 'text-warning' : 'text-on-surface-var'}>{line}</p>
    </motion.div>
  )
}

/** The asks *runId*'s steps are waiting on her answer for, from the approvals queue: each one a
 *  step's start or one of its agent's calls, listed under the step's own key. Re-read when an ask
 *  is raised or ends, on the socket every surface hears them on, while the run is live. */
function useWaitingSteps(runId: string, live: boolean): PendingApproval[] {
  const [waiting, setWaiting] = useState<PendingApproval[]>([])
  const load = useCallback(async () => {
    if (!live) { setWaiting([]); return }
    try {
      const all = await api.approvals()
      setWaiting(all.filter((a) => workflowApprovalSession(a.session)?.runId === runId))
    } catch {
      /* A failed read keeps what the card has: the next frame or poll reads again. */
    }
  }, [runId, live])
  useEffect(() => { load() }, [load])
  useChatSocket((m: WsMessage) => {
    if (m.type === 'approval' || m.type === 'approval_resolved') load()
  }, load)
  return waiting
}

/** What one step waiting on her answer is waiting for, in words: its start, or a call by name. */
function waitingFor(ask: PendingApproval): string {
  return ask.id.startsWith('spawn:') ? 'your Allow to start' : `your answer on ${ask.tool}`
}

/** Live in-chat progress widget for a workflow run the agent started or inspected.
 *
 *  Snapshot-then-subscribe, then folded: the REST snapshot lands first so the card never
 *  renders an empty run that looks stalled, and SSE events fold into it through the SAME
 *  pure `workflowFold` the run page uses. One fold, two surfaces — the alternative is two
 *  inline switches that drift (the exact problem `runFold.ts` was extracted to solve).
 *
 *  A terminal run does not subscribe: its stream closes immediately anyway, and its status
 *  is final. A batch still waiting for its one ask has no run yet: its card follows the batch
 *  until it has one (:func:`BatchStartCard`). */
export function WorkflowProgressCard({ refObj }: { refObj: WorkflowRunRef }) {
  return refObj.batch && !refObj.runId
    ? <BatchStartCard name={refObj.batch} />
    : <RunProgressCard refObj={refObj} />
}

function RunProgressCard({ refObj }: { refObj: WorkflowRunRef }) {
  const [vm, setVm] = useState<WorkflowViewModel | null>(null)
  const [gone, setGone] = useState(false)
  // A fetch miss that is NOT a 404: the run still exists, we just could not read it.
  const [loadFailed, setLoadFailed] = useState(false)
  // Guard against a late fetch landing after a newer snapshot: the poll and the stream can
  // both deliver, and applying the older one would flicker the card backwards.
  const latest = useRef(0)

  const load = useCallback(async () => {
    const stamp = ++latest.current
    try {
      const snap = await api.workflowRun(refObj.runId)
      if (stamp === latest.current) { setVm(foldSnapshot(snap)); setLoadFailed(false) }
    } catch (e) {
      if (stamp !== latest.current) return
      // Only a 404 collapses the card — the run is genuinely gone (deleted, never
      // readable). Any OTHER failure (5xx, network blip) used to erase the card too,
      // which read as the workflow vanishing; keep what we have and mark the miss so
      // a later poll/stream event can recover it.
      if (e instanceof ApiError && e.status === 404) setGone(true)
      else setLoadFailed(true)
    }
  }, [refObj.runId])

  useEffect(() => { load() }, [load])

  const live = !!vm && vm.live
  useWorkflowStream(refObj.runId, live, {
    onSnapshot: (snap) => { latest.current++; setVm(foldSnapshot(snap)) },
    // Folded, not refetched: the fold is the whole point of the stream, and a refetch per
    // event would make a 20-node fan-out 20 round-trips.
    onLifecycle: (event, data) => setVm((prev) => (prev ? foldEvent(prev, event, data) : prev)),
  })

  // A slow poll backs the stream up. Not the primary path — it exists because an
  // EventSource can drop silently behind a proxy, and a card that stops updating with no
  // error is worse than one that updates late.
  useEffect(() => {
    if (!live) return
    const t = window.setInterval(load, 15_000)
    return () => window.clearInterval(t)
  }, [live, load])

  // A step waiting on her answer is the one thing a Running card would otherwise hide: its
  // agent asked, on the approvals queue, under the step's own key, and nothing here said so.
  const waiting = useWaitingSteps(refObj.runId, live)

  if (gone) return null

  // Never loaded AND the read failed: say so instead of an eternal skeleton (the
  // pre-fix behaviour was worse — the card erased itself entirely on any failure).
  if (!vm && loadFailed) {
    return (
      <motion.div {...messageEnter} className="my-s flex items-center gap-s rounded-xl border border-outline-variant p-m">
        <Workflow size={15} className="shrink-0 text-on-surface-low" />
        <span data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface-var">Couldn't load this workflow run</span>
        <Button variant="ghost-accent" size="xs" onClick={() => load()}>Try again</Button>
      </motion.div>
    )
  }

  // A loop that stopped at the budget it was given did not give up: its status says so, and its
  // ending line is the budget sentence, not an alert.
  const atBudget = !!vm && stoppedAtBudget(vm.status, vm.attention)
  const look = vm ? runLook(vm.status, vm.held, atBudget) : null
  // The escalation, if the run gave up. Reachable here only because the fold now KEEPS the
  // record through a terminal status (#565) — it used to be nulled on the very event that
  // carries the failure.
  const attention = readAttention(vm?.attention)
  const escalation = attention?.kind === 'escalation' ? attention : null
  const StatusIcon = look?.icon
  const pct = vm ? Math.round(vm.progress * 100) : 0

  return (
    <motion.div
      {...messageEnter}
      className="my-s flex flex-col gap-s rounded-xl border border-outline-variant p-m"
    >
      <div className="flex min-w-0 items-center gap-s">
        <Workflow size={15} className="shrink-0 text-on-surface-low" />
        <span data-type="label-s" className="min-w-0 flex-1 truncate text-on-surface" style={fvs(500)}>
          {vm?.workflow || 'Workflow'}
        </span>
        {look && StatusIcon && (
          <span data-type="caption" className={`inline-flex shrink-0 items-center gap-xs ${look.tone}`}>
            <StatusIcon size={12} className={look.spin ? 'animate-spin' : ''} /> {look.label}
          </span>
        )}
        <TextLink href={`#/workflows/runs/${refObj.runId}`} size="xs" icon={ArrowUpRight} iconPosition="trailing" iconSize={12}
          className="shrink-0 transition-colors" title="Open the run">
          Open
        </TextLink>
      </div>

      {vm && vm.totalCount > 0 && (
        <div className="flex items-center gap-s">
          <Meter size="thin" className="flex-1" pct={pct}
            label={`${vm.workflow || 'Workflow'} progress: ${vm.doneCount} of ${vm.totalCount} steps finished`} />
          <span data-type="caption" className="shrink-0 text-on-surface-low tabular-nums">
            {vm.doneCount}/{vm.totalCount}
          </span>
          {/* Cache-origin as a COUNT, which is the shape this surface can carry. The card
              renders one node — the active one — and an active node is by definition never a cache
              hit, so a per-row chip here would be dead code. The count answers the question the
              flag exists for ("did my edit re-run anything?") at the run level, which is the level
              it was asked at. Same word as the run view's row chip and the inspector's badge. */}
          {vm.cachedCount > 0 && (
            <span
              data-testid="run-cached-count"
              data-type="caption"
              className="shrink-0 rounded-pill px-s py-xs tabular-nums"
              style={accentChip}
              title={`${vm.cachedCount} step${vm.cachedCount === 1 ? '' : 's'} served from the resume cache rather than re-run`}
            >
              {vm.cachedCount} cached
            </span>
          )}
        </div>
      )}

      {/* The ask, inline: a run waiting on a human is the whole reason to look at this card,
          and making the user open the run page to discover WHY defeats it.

          Read through `attentionLine` rather than reaching for `attention.prompt` (issue 565): that
          field belongs to a gate ask, and the record is polymorphic — an escalation carries a
          `reason` instead, so guessing `prompt` printed the generic fallback for it. */}
      {vm?.needsInput && (
        <p data-type="caption" className="text-warning">{attentionLine(vm.attention)}</p>
      )}

      {/* A run that gave up says SO, in one line, right where the user is reading (issue 565). The
          engine writes its escalation as the run goes terminal, and on the retries-exhausted
          path `run.error` is empty — so without this the card showed "Failed" and nothing else.
          The depth (per-attempt evidence, the suggested fix) is on the run page behind Open. */}
      {escalation && !atBudget && (
        <p data-type="caption" className="text-danger">Stopped: {escalation.headline}</p>
      )}

      {/* An ending nobody has to fix reads in the run's informational tone, as the run page reads
          it: a step you denied ("“Audit the notes” was denied by you."), a run its loop's stop
          ended, a loop that stopped at its budget. Only a fault is an alert. */}
      {vm?.error && (atBudget || vm.status === 'declined' || vm.status === 'cancelled'
        ? <p data-type="caption" className="text-on-surface-var">{vm.error}</p>
        : <p role="alert" data-type="caption" className="text-danger">{vm.error}</p>)}

      {/* A run that finished with steps that did not. A batch completes when any of its tasks
          does, so without this a task whose every call was refused read as done: the card said
          Completed and nothing else. A run whose ending already names them (`error`) says it there. */}
      {vm && isTerminal(vm.status) && !vm.error && (() => {
        const failed = vm.nodes.filter((n) => FAILED_STEP_STATES.has(n.state))
        if (!failed.length) return null
        return (
          <div className="flex flex-col gap-xs">
            {failed.slice(0, FAILED_STEPS_SHOWN).map((n) => {
              const line = `${nodeLabel(n)} failed${n.failure?.cause_plain ? `: ${n.failure.cause_plain}` : ''}`
              return <p key={n.instance_path} data-type="caption" className="truncate text-danger" title={line}>{line}</p>
            })}
            {failed.length > FAILED_STEPS_SHOWN && (
              <p data-type="caption" className="text-on-surface-low">and {failed.length - FAILED_STEPS_SHOWN} more</p>
            )}
          </div>
        )
      })()}

      {/* The currently-interesting node, not the whole list — a chat card is a glance, and
          twenty rows in a message stream is a wall. */}
      {vm && !isTerminal(vm.status) && (() => {
        const active = vm.nodes.find((n) => n.state === 'running')
          ?? vm.nodes.find((n) => n.state === 'waiting')
        if (!active) return null
        const nl = nodeLook(active.state)
        const NIcon = nl.icon
        // Named as the run page names it: the step's label, else its id (`nodeLabel`).
        const label = nodeLabel(active)
        return (
          <div data-type="caption" className="flex min-w-0 items-center gap-s text-on-surface-low">
            <NIcon size={12} className={`shrink-0 ${nl.tone}${nl.spin ? ' animate-spin' : ''}`} />
            {/* ROUTES to the one inspector; it does not host a second. `NodeInspectorDrawer`
                is owned by the run view, and the hash grammar already reserves `?query` for "which
                detail panel is open" — so the node rides the SAME destination the `Open` link above
                uses, with `?node=<id>` appended, and the drawer opens ON it instead of the user
                landing on the run with nothing open. Mounting a second drawer here would make two
                inspectors for one job, which is the coherence defect this routing avoids.
                Plain text when the node carries no id: there would be nothing to deep-link to. */}
            {active.node_id ? (
              <TextLink
                href={`#/workflows/runs/${refObj.runId}?node=${encodeURIComponent(active.node_id)}`}
                className="min-w-0 flex-1 truncate"
                title="Inspect this step in the run — resolved prompt, inputs, output"
                aria-label={`Inspect the current step: ${label}`}
              >
                {label}
              </TextLink>
            ) : (
              <span className="min-w-0 flex-1 truncate">{label}</span>
            )}
          </div>
        )
      })()}

      {/* Each step that waits on her answer says so, and for what, where its ask is answered: the
          run's page lists the step's asks with Allow and Deny (`RunToolApprovals`). */}
      {vm && !isTerminal(vm.status) && waiting.length > 0 && (
        <div className="flex flex-col gap-xs">
          {waiting.slice(0, FAILED_STEPS_SHOWN).map((ask) => {
            const nodeId = workflowApprovalSession(ask.session)?.nodeId ?? ''
            const node = vm.nodes.find((n) => n.node_id === nodeId)
            const line = `${node ? nodeLabel(node) : nodeId || 'A step'} waits for ${waitingFor(ask)}`
            return (
              <p key={ask.id} data-type="caption" className="flex min-w-0 items-center gap-s text-warning">
                <span className="min-w-0 flex-1 truncate" title={line}>{line}</span>
                <TextLink href={`#/workflows/runs/${refObj.runId}?node=${encodeURIComponent(nodeId)}`}
                  size="xs" className="shrink-0" aria-label={`Answer it: ${line}`}>
                  Answer it
                </TextLink>
              </p>
            )
          })}
          {waiting.length > FAILED_STEPS_SHOWN && (
            <p data-type="caption" className="text-on-surface-low">and {waiting.length - FAILED_STEPS_SHOWN} more</p>
          )}
        </div>
      )}

      {vm && (vm.tokens > 0 || vm.elapsedSecs > 0) && (
        <div data-type="caption" className="flex items-center gap-m text-on-surface-low">
          {vm.elapsedSecs > 0 && <span className="tabular-nums">{fmtElapsed(vm.elapsedSecs)}</span>}
          {vm.tokens > 0 && <span className="tabular-nums">{vm.tokens.toLocaleString()} tokens</span>}
        </div>
      )}
    </motion.div>
  )
}
