/** WHERE AN APPROVAL IS ANSWERED — read from the place it carries, once, for every surface.
 *
 *  A pending tool approval broadcasts the key of the session that owns it, and the
 *  out-of-context nudge used to spend that key verbatim: *"open <session> to respond"*. That
 *  sentence is true for a chat and a dead end for everything else. A workflow STAGE's approval
 *  carries a SYNTHETIC key — `workflow:<run_id>:<node_id>` — which is not a chat at all:
 *  `/api/chat/sessions/<key>` answers 404 and no `#/chat/<key>` route renders it (#258). The
 *  only notification the user got pointed at nothing, so a blocked run read as a hung one.
 *
 *  The gateway reads the key once (`approval_place.place_of`) and the approval carries what it
 *  read: `work` (`chat:<key>`, `loop:<id>`, `workflow:<run>`, or "" for work no page answers),
 *  `step` (the run's step) and `room`. So does its Inbox row, and the note it leaves when nobody
 *  answered it (`placeOfRefs`). No surface reads a key's shape (#3889). The promise still has two halves that
 *  have to agree, and both read the place here:
 *
 *    the nudge  NAMES a place and links to it   (`useApprovalToasts` → `approvalDestination`)
 *    that place CARRIES the Approve/Reject pair (`RunToolApprovals` → `workflowApprovalOf`)
 */

import type { PendingApproval } from '../lib/api'

/** Where an approval is answered, as it carries it: a registry row, its frame, or the `refs` of
 *  its Inbox row (`placeOfRefs`). */
export type ApprovalPlace = Partial<Pick<PendingApproval, 'session' | 'work' | 'step' | 'room'>>

/** The place an Inbox row's `refs` keep, under keys of their own (`approval_place.place_refs`: a
 *  room's pause row is closed on its `room`): an approval's row, the note an unanswered one left,
 *  or an agent's question. */
export function placeOfRefs(refs: Record<string, unknown>): ApprovalPlace {
  const text = (v: unknown) => (typeof v === 'string' ? v : '')
  return {
    session: text(refs.session), work: text(refs.place_work), step: text(refs.place_step), room: text(refs.place_room),
  }
}

/** The id of *place*'s work when it is work of *kind*, else "". */
function idOf(place: ApprovalPlace, kind: 'chat' | 'loop' | 'workflow'): string {
  const work = place.work ?? ''
  return work.startsWith(`${kind}:`) ? work.slice(kind.length + 1) : ''
}

export interface WorkflowApprovalStep {
  runId: string
  /** The step the approval belongs to — an instance path or a bare node id, as broadcast. */
  nodeId: string
}

/** The run and step a workflow step's approval is answered at, or `null` for anything else. */
export function workflowApprovalStep(place: ApprovalPlace): WorkflowApprovalStep | null {
  const runId = idOf(place, 'workflow')
  return runId ? { runId, nodeId: place.step ?? '' } : null
}

/** Is this approval one that *runId*'s run view owns? The run view claims only its own approvals
 *  out of the global `/api/approvals` list by the place the nudge links with, so the surface the
 *  sentence names is the surface that answers. */
export function workflowApprovalOf(place: ApprovalPlace, runId: string): boolean {
  return runId !== '' && idOf(place, 'workflow') === runId
}

/** The loop whose worker or planner asked, or "" for anything else. */
export function loopApprovalId(place: ApprovalPlace): string {
  return idOf(place, 'loop')
}

/** Is this approval one of *loopId*'s workers or its planner asked? The loop's page claims its own
 *  approvals out of `/api/approvals` by the place the nudge links with. */
export function loopApprovalOf(place: ApprovalPlace, loopId: string): boolean {
  return loopId !== '' && loopApprovalId(place) === loopId
}

/** Is this approval one *roomId*'s members asked? The room's own view claims its members' asks out
 *  of `/api/approvals` by the place the nudge links with (`RoomApprovals`). */
export function roomApprovalOf(place: ApprovalPlace, roomId: string): boolean {
  return roomId !== '' && (place.room ?? '') === roomId
}

export interface ApprovalDestination {
  /** The in-app hash route that ANSWERS this approval. */
  href: string
  /** How a sentence names it ("open <label> to respond") — prose, not a key. */
  label: string
  /** The link's own text, which must say where it goes rather than "click here". */
  linkLabel: string
}

/** The surface that answers *place*'s approval.
 *
 *  `#/workflows/runs/<run>?node=<node>` is not invented here: it is the run view's own deep
 *  link, already parsed by `WorkflowsSection` and already pinned end-to-end by
 *  `workflowCardNodeDeepLink.test.tsx`. Reusing it is the point — a second route spelling for
 *  one surface is the drift this module exists to prevent.
 */
export function approvalDestination(place: ApprovalPlace): ApprovalDestination {
  const wf = workflowApprovalStep(place)
  if (wf) {
    return {
      href: `#/workflows/runs/${wf.runId}?node=${encodeURIComponent(wf.nodeId)}`,
      // Prose, deliberately: `workflow:11b9a34c:synthesize` told the reader to open a string,
      // and the string was not openable. This says which step of which run, which is what they
      // are looking at when the run is blocked.
      label: `the ${wf.nodeId} step of workflow run ${wf.runId}`,
      linkLabel: 'Open the workflow run',
    }
  }
  // A loop's worker or planner asks on the loop's own page (`LoopApprovals`): its key is no chat
  // anyone opened, and `#/loops/<id>` lands on whichever cockpit or planning walkthrough the loop
  // is at (`LoopsSection`).
  const loopId = loopApprovalId(place)
  if (loopId) {
    return { href: `#/loops/${loopId}`, label: `loop ${loopId}`, linkLabel: 'Open the loop' }
  }
  // A room member asks in its room (`RoomApprovals`): its key is no chat anyone opened. The room is
  // named by the sentence's own "from" (`source_label`, the room's title), so the place is "the room".
  if (place.room) {
    return {
      href: `#/chat/room/${encodeURIComponent(place.room)}`,
      label: 'the room',
      linkLabel: 'Open the room',
    }
  }
  // A chat (including a subagent escalating to its parent), named by its own key; work no page
  // answers keeps the session it was asked under. Encoded the way every other chat deep link in
  // the app is.
  const chat = idOf(place, 'chat') || (place.session ?? '')
  return {
    href: `#/chat/${encodeURIComponent(chat)}`,
    label: chat,
    linkLabel: 'Open the chat',
  }
}
