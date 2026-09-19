/** WHERE AN APPROVAL IS ANSWERED — derived from its `session` key, once, for every surface.
 *
 *  A pending tool approval broadcasts the key of the session that owns it, and the
 *  out-of-context nudge used to spend that key verbatim: *"open <session> to respond"*. That
 *  sentence is true for a chat and a dead end for everything else. A workflow STAGE's approval
 *  carries a SYNTHETIC key — `workflow:<run_id>:<node_id>` — which is not a chat at all:
 *  `/api/chat/sessions/<key>` answers 404 and no `#/chat/<key>` route renders it (#258). The
 *  only notification the user got pointed at nothing, so a blocked run read as a hung one.
 *
 *  The fix is not nicer wording. It is that the destination must be DERIVED from the key's
 *  shape, and that the derivation live in ONE place, because the promise has two halves that
 *  have to agree:
 *
 *    the nudge  NAMES a place and links to it   (`useApprovalToasts` → `approvalDestination`)
 *    that place CARRIES the Approve/Reject pair (`WorkflowRunDetail` → `workflowApprovalOf`)
 *
 *  Two parsers would let a toast link somewhere that does not claim the approval, which is the
 *  same false assurance in a new location. So the grammar of the key is stated here and both
 *  halves read it.
 */

/** The synthetic session key a workflow stage's approval carries.
 *
 *  `[^:]+` for the run id and a greedy tail for the node: a run id is a hex slug and cannot
 *  contain a colon, while an instance path can (and the node is the part a deep link needs
 *  whole). Anchored at both ends so a chat session that merely starts with the word is not
 *  mistaken for one.
 */
const WORKFLOW_SESSION = /^workflow:([^:]+):(.+)$/

export interface WorkflowApprovalSession {
  runId: string
  /** The node the approval belongs to — an instance path or a bare node id, as broadcast. */
  nodeId: string
}

/** Decode a workflow stage's approval session, or `null` for anything else (a chat, a
 *  subagent of a chat, an empty key). */
export function workflowApprovalSession(session: string): WorkflowApprovalSession | null {
  const m = WORKFLOW_SESSION.exec(session)
  return m ? { runId: m[1], nodeId: m[2] } : null
}

/** Is this approval one that *runId*'s run view owns? Used by the run view to claim only its
 *  own approvals out of the global `/api/approvals` list — the same parse the nudge links
 *  with, so the surface the sentence names is the surface that answers. */
export function workflowApprovalOf(session: string, runId: string): boolean {
  const parsed = workflowApprovalSession(session)
  return parsed !== null && parsed.runId === runId
}

export interface ApprovalDestination {
  /** The in-app hash route that ANSWERS this approval. */
  href: string
  /** How a sentence names it ("open <label> to respond") — prose, not a key. */
  label: string
  /** The link's own text, which must say where it goes rather than "click here". */
  linkLabel: string
}

/** The surface that answers *session*'s approval.
 *
 *  `#/workflows/runs/<run>?node=<node>` is not invented here: it is the run view's own deep
 *  link, already parsed by `WorkflowsSection` and already pinned end-to-end by
 *  `workflowCardNodeDeepLink.test.tsx`. Reusing it is the point — a second route spelling for
 *  one surface is the drift this module exists to prevent.
 */
export function approvalDestination(session: string): ApprovalDestination {
  const wf = workflowApprovalSession(session)
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
  // A chat session (including a subagent escalating to its parent). Encoded the way every
  // other chat deep link in the app is — `dashboard:<key>` sessions carry a colon.
  return {
    href: `#/chat/${encodeURIComponent(session)}`,
    label: session,
    linkLabel: 'Open the chat',
  }
}
