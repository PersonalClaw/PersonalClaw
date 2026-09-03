import { useCallback, useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowUpRight, Workflow } from 'lucide-react'
import { api, ApiError } from '../../lib/api'
import { messageEnter } from '../../design/motion'
import { fvs } from '../../design/fontWeight'
import { Meter } from '../../ui/Meter'
import { Button } from '../../ui/Button'
import { foldEvent, foldSnapshot, type WorkflowViewModel } from '../workflows/workflowFold'
import { useWorkflowStream } from '../workflows/useWorkflowStream'
import { fmtElapsed, isTerminal, nodeLook, runLook } from '../workflows/workflowMeta'
import { TextLink } from '../../ui/TextLink'

// Tools whose result means "a workflow run now exists worth watching". `workflow_start`
// creates one; `workflow_status`/`workflow_observe` name one the agent inspected, and a
// user reading that turn wants the live thing, not the frozen text the tool returned.
const WORKFLOW_TOOLS = new Set(['workflow_start', 'workflow_status', 'workflow_observe'])

export interface WorkflowRunRef { runId: string; created: boolean }

/** Recognize a workflow tool segment and pull the run id out of its output.
 *
 *  Matches the JSON the tools actually return (`"run_id": "<8 hex>"`) rather than a deep
 *  link, because these tools return structured results for a model to read — unlike the
 *  SDLC tools, which return a `/#/…` URL for a human. Anchoring on the real shape is what
 *  keeps this from silently stopping when a description is reworded. */
export function workflowRefFromTool(
  toolName: string | undefined,
  output: string | undefined,
): WorkflowRunRef | null {
  if (!toolName || !WORKFLOW_TOOLS.has(toolName) || !output) return null
  const m = output.match(/"run_id"\s*:\s*"([0-9a-f]{6,})"/i)
  if (!m) return null
  return { runId: m[1], created: toolName === 'workflow_start' }
}

/** Live in-chat progress widget for a workflow run the agent started or inspected.
 *
 *  Snapshot-then-subscribe, then folded: the REST snapshot lands first so the card never
 *  renders an empty run that looks stalled, and SSE events fold into it through the SAME
 *  pure `workflowFold` the run page uses. One fold, two surfaces — the alternative is two
 *  inline switches that drift (the exact problem `runFold.ts` was extracted to solve).
 *
 *  A terminal run does not subscribe: its stream closes immediately anyway, and its status
 *  is final. */
export function WorkflowProgressCard({ refObj }: { refObj: WorkflowRunRef }) {
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
    // Folded, not refetched: the fold is the whole point of Slice 8a, and a refetch per
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

  if (gone) return null

  // Never loaded AND the read failed: say so instead of an eternal skeleton (the
  // pre-fix behaviour was worse — the card erased itself entirely on any failure).
  if (!vm && loadFailed) {
    return (
      <motion.div {...messageEnter} className="my-s flex items-center gap-s rounded-xl border border-outline-variant p-m">
        <Workflow size={15} className="shrink-0 text-on-surface-low" />
        <span className="min-w-0 flex-1 truncate text-on-surface-var text-[0.8125rem]">Couldn't load this workflow run</span>
        <Button variant="ghost-accent" size="xs" onClick={() => load()}>Try again</Button>
      </motion.div>
    )
  }

  const look = vm ? runLook(vm.status) : null
  const StatusIcon = look?.icon
  const pct = vm ? Math.round(vm.progress * 100) : 0

  return (
    <motion.div
      {...messageEnter}
      className="my-s flex flex-col gap-s rounded-xl border border-outline-variant p-m"
    >
      <div className="flex min-w-0 items-center gap-s">
        <Workflow size={15} className="shrink-0 text-on-surface-low" />
        <span className="min-w-0 flex-1 truncate text-on-surface text-[0.8125rem]" style={fvs(500)}>
          {vm?.workflow || 'Workflow'}
        </span>
        {look && StatusIcon && (
          <span className={`inline-flex shrink-0 items-center gap-1 text-[0.75rem] ${look.tone}`}>
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
            label={`${vm.workflow || 'Workflow'} progress: ${vm.doneCount} of ${vm.totalCount} steps done`} />
          <span className="shrink-0 text-on-surface-low text-[0.75rem] tabular-nums">
            {vm.doneCount}/{vm.totalCount}
          </span>
        </div>
      )}

      {/* The ask, inline: a run waiting on a human is the whole reason to look at this card,
          and making the user open the run page to discover WHY defeats it. */}
      {vm?.needsInput && (
        <p className="text-warning text-[0.75rem]">
          {typeof vm.attention?.prompt === 'string' ? String(vm.attention.prompt) : 'Waiting on you'}
        </p>
      )}

      {vm?.error && <p role="alert" className="text-danger text-[0.75rem]">{vm.error}</p>}

      {/* The currently-interesting node, not the whole list — a chat card is a glance, and
          twenty rows in a message stream is a wall. */}
      {vm && !isTerminal(vm.status) && (() => {
        const active = vm.nodes.find((n) => n.state === 'running')
          ?? vm.nodes.find((n) => n.state === 'waiting')
        if (!active) return null
        const nl = nodeLook(active.state)
        const NIcon = nl.icon
        return (
          <div className="flex min-w-0 items-center gap-s text-on-surface-low text-[0.75rem]">
            <NIcon size={12} className={`shrink-0 ${nl.tone}${nl.spin ? ' animate-spin' : ''}`} />
            <span className="min-w-0 flex-1 truncate">{active.node_id || active.instance_path}</span>
          </div>
        )
      })()}

      {vm && (vm.tokens > 0 || vm.elapsedSecs > 0) && (
        <div className="flex items-center gap-m text-on-surface-low text-[0.75rem]">
          {vm.elapsedSecs > 0 && <span className="tabular-nums">{fmtElapsed(vm.elapsedSecs)}</span>}
          {vm.tokens > 0 && <span className="tabular-nums">{vm.tokens.toLocaleString()} tokens</span>}
        </div>
      )}
    </motion.div>
  )
}
