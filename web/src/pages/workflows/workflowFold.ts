/** Pure fold of a workflow run + its SSE events into ONE view-model (WF2-R11).
 *
 *  No React, no fetch: a pure function of (snapshot, events), so the "which events must be
 *  handled and what each does" contract is UNIT-TESTABLE rather than a hand-maintained
 *  comment spread across the run view and the chat card. Same treatment `runFold.ts` gets,
 *  for the same reason.
 *
 *  **The fold law:** folding a run's events over its snapshot reconstructs exactly the state
 *  the server would report. That is what makes the widget trustworthy — if the law holds, a
 *  reconnect that replays events converges on the same view as a fresh snapshot fetch.
 *
 *  Three guards make the law survive rewind and fork, which are what break naive live
 *  widgets (the K42/K44/K45 coalescer bugs in this codebase's chat stream):
 *
 *  1. **Dedup by event id.** Ids are deterministic at emit (`<run>-evt-<n>`), so a re-emit
 *     after a reconnect is an idempotent no-op instead of a duplicated row.
 *  2. **Epoch supersede-drop.** An event stamped with an epoch older than the one already
 *     folded is DROPPED — it was in flight when a rewind landed, and applying it would
 *     resurrect state the user just reset.
 *  3. **Node-keyed patches.** A node event patches ONE node by instance path; it never
 *     rebroadcasts the whole node list. Two nodes finishing concurrently therefore cannot
 *     clobber each other, and a 20-node fan-out is 20 patches, not 20 full rebuilds.
 */

import type { WorkflowNodeState, WorkflowRunDetailData } from '../../lib/api'
import { readAttention } from './attentionMeta'
import { byInstancePath } from './instancePathOrder'
import type { WorkflowLifecycleEvent } from './useWorkflowStream'

/** The envelope every published event carries (stamped at the controller's one publish
 *  seam, so a payload cannot be missing them). */
export interface WorkflowEventEnvelope {
  run_id?: string
  event_id?: string
  seq?: number
  /** The RUN's epoch at emit time — the supersede-drop key. */
  epoch?: number
  /** A node event's own epoch, which can lag the run's after a partial rewind. */
  node_epoch?: number
  node_id?: string
  instance_path?: string
  status?: string
  degraded_reason?: string
  cached?: boolean
  /** Per-item foreach context (WF2-R5), present only on an iterated node's events. */
  item_index?: number
  item_total?: number
  item_label?: string
  [key: string]: unknown
}

export interface WorkflowViewModel {
  runId: string
  workflow: string
  status: string
  specVersion: number
  error: string
  /** Nodes in instance-path order — the spec's own reading order. */
  nodes: WorkflowNodeState[]
  doneCount: number
  totalCount: number
  /** Nodes whose output was served from the resume cache (WF2-A1) rather than re-run.
   *
   *  A COUNT, not a per-row flag, because that is the shape of the question: "did my edit
   *  re-run anything?" is about the whole run, and the compact chat card renders only the
   *  currently-active node — which is by definition never a cache hit. */
  cachedCount: number
  /** Nodes that reached a terminal state, over the total — the progress fraction. */
  progress: number
  tokens: number
  elapsedSecs: number
  /** True while the run can still move on its own. */
  live: boolean
  /** True when a human is required — the only state a user can act on. */
  needsInput: boolean
  attention: Record<string, unknown> | null
  /** The highest epoch folded so far. An event below this is superseded. */
  epoch: number
  /** Event ids already applied, so a replay cannot double-apply. */
  seen: ReadonlySet<string>
  /** instance path -> the highest `seq` applied to that node. An event arriving out of
   *  order for a node that has already advanced is dropped rather than regressing it. */
  nodeSeq: ReadonlyMap<string, number>
  /** Events dropped as superseded or duplicate — surfaced so a test (and a debug view) can
   *  assert the guards actually fired rather than trusting they did. */
  dropped: number
}

const TERMINAL_NODE = new Set([
  'done', 'degraded', 'failed', 'skipped', 'no_change', 'scope_violation',
  'discarded', 'escalated', 'blocked', 'cancelled',
])
const TERMINAL_RUN = new Set(['complete', 'failed', 'cancelled', 'escalated'])

/** Fold a server snapshot into the view-model. Reruns whole on every snapshot — the
 *  snapshot is authoritative, so it RESETS the dedup and epoch state rather than merging
 *  into it. Merging would let a pre-snapshot epoch keep suppressing fresh events. */
export function foldSnapshot(snap: WorkflowRunDetailData): WorkflowViewModel {
  const nodes = [...(snap.nodes ?? [])].sort(byInstancePath)
  const done = nodes.filter((n) => TERMINAL_NODE.has(n.state)).length
  return {
    runId: snap.run_id,
    workflow: snap.workflow,
    status: snap.status,
    specVersion: snap.spec_version,
    error: snap.error ?? '',
    nodes,
    doneCount: done,
    totalCount: nodes.length,
    cachedCount: nodes.filter((n) => n.cached).length,
    progress: nodes.length ? done / nodes.length : 0,
    tokens: snap.tokens ?? 0,
    elapsedSecs: snap.elapsed_secs ?? 0,
    live: !TERMINAL_RUN.has(snap.status),
    needsInput: snap.status === 'needs_input',
    attention: snap.attention ?? null,
    epoch: 0,
    seen: new Set<string>(),
    nodeSeq: new Map<string, number>(),
    dropped: 0,
  }
}

/** Fold ONE event into the model. Pure: returns a new model, never mutates the input.
 *
 *  Returns the SAME object identity when an event is dropped, so a React caller can skip a
 *  re-render on a no-op fold rather than re-rendering on every duplicate. */
export function foldEvent(
  vm: WorkflowViewModel,
  event: WorkflowLifecycleEvent,
  data: unknown,
): WorkflowViewModel {
  const env = (data ?? {}) as WorkflowEventEnvelope

  // Guard 1 — an event for a DIFFERENT run is not ours. A per-run stream should never
  // deliver one, but a shared hub bug would be silent otherwise.
  if (env.run_id && vm.runId && env.run_id !== vm.runId) {
    return { ...vm, dropped: vm.dropped + 1 }
  }

  // Guard 2 — dedup by deterministic id. A reconnect replay re-delivers events; applying
  // one twice would double-count progress.
  if (env.event_id && vm.seen.has(env.event_id)) {
    return { ...vm, dropped: vm.dropped + 1 }
  }

  // Guard 3 — epoch supersede-drop. An event emitted before a rewind landed carries the
  // OLD epoch; applying it would resurrect state the user just reset.
  const epoch = typeof env.epoch === 'number' ? env.epoch : vm.epoch
  if (epoch < vm.epoch) {
    return { ...vm, dropped: vm.dropped + 1 }
  }

  const seen = env.event_id ? new Set(vm.seen).add(env.event_id) : vm.seen
  let next: WorkflowViewModel = { ...vm, seen, epoch: Math.max(vm.epoch, epoch) }

  switch (event) {
    case 'workflow_run_update':
      if (typeof env.status === 'string') next = applyRunStatus(next, env.status)
      if (typeof env.error === 'string') next.error = env.error
      break

    case 'workflow_node_started':
      next = patchNode(next, env, 'running')
      break

    case 'workflow_node_done':
      next = patchNode(next, env, typeof env.status === 'string' ? env.status : 'done')
      break

    case 'workflow_attention':
    case 'workflow_needs_input': {
      next.attention = (env.ask as Record<string, unknown>) ?? next.attention
      // Only an ASK means the run is waiting on input. `_escalate` publishes on this same
      // channel (#565), and an escalation is the engine giving up — the run is on its way to
      // FAILED, not waiting for an answer. Flipping to `needs_input` for one showed "Waiting on
      // you" on a run nobody could answer, until the terminal status arrived and corrected it.
      const read = readAttention(next.attention)
      if (read?.kind !== 'escalation') next = applyRunStatus(next, 'needs_input')
      break
    }

    case 'workflow_gate_resolved':
      // The gate is answered, so the run has work again. Clearing attention here (rather
      // than waiting for the next snapshot) is what stops the ask card lingering after the
      // user answered it.
      next.attention = null
      if (env.instance_path) next = patchNode(next, env, 'done')
      break

    case 'workflow_gate_revised':
      // A revise is NOT an approval: the ask is answered (clear it, same as above) but the
      // revised step goes back to WORK rather than being done, because the engine patched it
      // and re-runs it. Marking it 'done' here would show a finished step the engine is still
      // executing. `patchNode` keys on `instance_path` like every other arm here; the
      // payload's `step_ref` names the patched node for a reader, it is not the key.
      next.attention = null
      if (env.instance_path) next = patchNode(next, env, 'running')
      break

    case 'workflow_spec_updated':
      if (typeof env.spec_version === 'number') next.specVersion = env.spec_version
      break

    case 'workflow_progress':
      // A blocking-mode tick: a full node-state list, used to re-derive counts. Applied as
      // a whole because it IS the server's view, not a per-node delta.
      if (Array.isArray(env.nodes)) next = applyProgress(next, env.nodes as WorkflowNodeState[])
      if (typeof env.tokens === 'number') next.tokens = env.tokens
      break

    case 'workflow_forked':
    case 'workflow_mutation_rejected':
      // Neither changes THIS run's state: a fork creates a sibling, and a rejected mutation
      // by definition applied nothing. Folded as no-ops so the switch stays exhaustive —
      // an unhandled case would be indistinguishable from a missing listener.
      break
  }

  return recount(next)
}

/** Fold a whole event list. The fold law's subject: `foldEvents(snapshot, events)` must
 *  equal what the server would report after those events. */
export function foldEvents(
  vm: WorkflowViewModel,
  events: Array<{ event: WorkflowLifecycleEvent; data: unknown }>,
): WorkflowViewModel {
  return events.reduce((acc, e) => foldEvent(acc, e.event, e.data), vm)
}

/** Apply a run-level status. **`attention` is deliberately untouched** (#565).
 *
 *  This used to null the record on any terminal status, to stop a dead ask card rendering on a
 *  finished run. That threw away the ESCALATION — written at exactly the moment the run goes
 *  terminal, and the only account a retries-exhausted run has, since `_finish(status)` passes no
 *  `error` on that path.
 *
 *  Two facts had been sharing one field: "the run is holding something" and "the user can act on
 *  it". Deleting the first to express the second is what lost the evidence. The second is
 *  `live`/`needsInput`, which every consumer already gates its ask affordances on, and
 *  `readAttention` says which KIND of thing is held — so a diagnosis can render where an ask
 *  must not.
 *
 *  It also restores the fold law: `service.status` reads `run.attention` unconditionally, so
 *  nulling it here made the event path disagree with the snapshot path for the same run. */
function applyRunStatus(vm: WorkflowViewModel, status: string): WorkflowViewModel {
  return {
    ...vm,
    status,
    live: !TERMINAL_RUN.has(status),
    needsInput: status === 'needs_input',
  }
}

/** Patch ONE node by instance path. Node-keyed, never a whole-list rebroadcast — that is
 *  what lets two concurrent completions land without clobbering each other.
 *
 *  Per-node `seq` ordering is enforced here, not globally: SSE delivery order is not
 *  guaranteed across a reconnect, and a late `node_started` arriving after that node's
 *  `node_done` would otherwise regress the widget from Done back to Running. Scoped per node
 *  because two DIFFERENT nodes' events are genuinely independent — a global seq floor would
 *  drop a legitimate sibling event that merely arrived second. */
function patchNode(
  vm: WorkflowViewModel,
  env: WorkflowEventEnvelope,
  state: string,
): WorkflowViewModel {
  const path = env.instance_path
  if (!path) return vm

  const seq = typeof env.seq === 'number' ? env.seq : null
  const applied = vm.nodeSeq.get(path)
  if (seq !== null && applied !== undefined && seq < applied) {
    return { ...vm, dropped: vm.dropped + 1 }
  }
  const nodeSeq = seq === null ? vm.nodeSeq : new Map(vm.nodeSeq).set(path, seq)

  // A node event carries its OWN epoch, which can lag the run's after a partial rewind.
  // An event for a node whose epoch has moved on is stale for that node specifically.
  const existing = vm.nodes.find((n) => n.instance_path === path)
  const patched: WorkflowNodeState = {
    instance_path: path,
    node_id: (env.node_id as string) || existing?.node_id || '',
    state,
    attempt: existing?.attempt,
    degraded_reason: (env.degraded_reason as string) || '',
    // Cache-origin (WF2-A1). Read from THIS event, deliberately not carried forward like
    // `item_*` below: only a cache hit publishes `cached`, so a re-run after a mid-flight edit
    // emits `node_done` without it — and inheriting `existing.cached` would keep the row
    // claiming a cache hit the edit just invalidated, which is the exact question the flag
    // exists to answer.
    cached: env.cached === true,
    failure: existing?.failure ?? null,
    // Per-item context arrives on `node_started` and is NOT re-sent on `node_done` — so it is
    // carried forward rather than overwritten, or a finished item would lose the label that
    // identified it the moment it succeeded.
    item_index: env.item_index ?? existing?.item_index,
    item_total: env.item_total ?? existing?.item_total,
    item_label: env.item_label ?? existing?.item_label,
  }

  const nodes = existing
    ? vm.nodes.map((n) => (n.instance_path === path ? patched : n))
    // A node the snapshot did not contain (a foreach expanded after the snapshot) is
    // APPENDED and re-sorted, so a fan-out that grows mid-run still reads in spec order.
    : [...vm.nodes, patched].sort(byInstancePath)

  return { ...vm, nodes, nodeSeq }
}

function applyProgress(vm: WorkflowViewModel, incoming: WorkflowNodeState[]): WorkflowViewModel {
  const byPath = new Map(vm.nodes.map((n) => [n.instance_path, n]))
  for (const n of incoming) {
    const prior = byPath.get(n.instance_path)
    // The tick carries only path+state; keep whatever richer detail the snapshot had, or a
    // progress tick would erase a node's failure reason.
    byPath.set(n.instance_path, prior ? { ...prior, state: n.state } : { ...n, node_id: n.node_id ?? '' })
  }
  return {
    ...vm,
    nodes: [...byPath.values()].sort(byInstancePath),
  }
}

function recount(vm: WorkflowViewModel): WorkflowViewModel {
  const done = vm.nodes.filter((n) => TERMINAL_NODE.has(n.state)).length
  return {
    ...vm,
    doneCount: done,
    totalCount: vm.nodes.length,
    // Recounted from the nodes on every fold rather than incremented on a `cached` event: a
    // rewind clears the flag on the node it re-runs, and a counter that only ever went up
    // would keep reporting a cache hit the re-run superseded.
    cachedCount: vm.nodes.filter((n) => n.cached).length,
    progress: vm.nodes.length ? done / vm.nodes.length : 0,
  }
}

/** The explicit dedup key from the plan: `run_id|node_id|epoch|seq|state`.
 *
 *  Exported for the replay harness (Slice 11) and for a debug view. The fold itself dedups
 *  on `event_id`, which is stronger — but this key is what a RECORDED trace can be grouped
 *  by when event ids were not captured. */
export function dedupKey(env: WorkflowEventEnvelope): string {
  return [
    env.run_id ?? '',
    env.node_id ?? env.instance_path ?? '',
    env.node_epoch ?? env.epoch ?? 0,
    env.seq ?? 0,
    env.status ?? '',
  ].join('|')
}
