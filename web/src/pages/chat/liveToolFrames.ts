/** The live half of a turn's steps: how the chat page applies the gateway's tool and approval
 *  frames to the segments of the turn on screen. `hydrateTurns` is the other half, rebuilding the
 *  same segments from the rows the gateway persisted, and the two must agree: what she saw while
 *  the turn ran is what a reload shows (`liveAndReloadAgree.test.ts` holds them to that).
 *
 *  Each function is pure over the segment list it is given, so the page's state updater can apply
 *  it whenever React renders. */
import { approvalRiskOf, blastRadiusOf } from './approvalMeta'
import { ungatedOf, type ApprovalSegment, type Segment, type ToolSegment } from './chatTypes'

/** A gateway frame's payload, as the socket delivers it. */
export type Frame = Record<string, unknown>

/** `tool_call`: opens the call's card — or, for a card already shown, refines it in place. Agents
 *  stream the real arguments after an opening frame that carried none, and the gateway marks a
 *  call that ran without asking her once its result lands; neither opens a second card. */
export function applyToolCallFrame(segs: Segment[], d: Frame): Segment[] {
  const id = String(d.tool_call_id ?? '')
  const ungated = ungatedOf(d)
  const inputObj = d.input !== undefined && d.input !== null ? d.input : undefined
  const existing = segs.find((sg): sg is ToolSegment => sg.kind === 'tool' && sg.id === id)
  if (existing) {
    // Keep the STABLE tool name; an update carries the refined summary (command/file) as `detail`.
    const refined: ToolSegment = { ...existing }
    if (d.input_preview) refined.input = String(d.input_preview)
    if (inputObj !== undefined) refined.inputObj = inputObj
    if (d.detail) refined.detail = String(d.detail)
    if (d.tool && !d.update) refined.tool = String(d.tool)
    if (ungated) refined.ungated = ungated
    return segs.map((sg) => (sg === existing ? refined : sg))
  }
  return [...segs, {
    kind: 'tool', id, tool: String(d.tool ?? 'tool'), detail: d.detail ? String(d.detail) : undefined,
    toolKind: String(d.kind ?? ''), input: String(d.input_preview ?? ''), inputObj,
    purpose: String(d.purpose ?? ''), auto: !!d.auto, done: false, ...(ungated ? { ungated } : {}),
  }]
}

/** `tool_result`: the call finished — its output, and whether it failed. */
export function applyToolResultFrame(segs: Segment[], d: Frame): Segment[] {
  const id = String(d.tool_call_id ?? '')
  return segs.map((sg) => sg.kind === 'tool' && sg.id === id
    ? { ...sg, output: String(d.output ?? ''), done: true,
        contentType: d.content_type ? String(d.content_type) : sg.contentType,
        rawRef: d.raw_ref ? String(d.raw_ref) : sg.rawRef,
        truncated: d.truncated != null ? !!d.truncated : sg.truncated,
        originalLength: d.original_length != null ? Number(d.original_length) : sg.originalLength,
        recoveryHints: Array.isArray(d.recovery_hints) && d.recovery_hints.length
          ? (d.recovery_hints as string[]) : sg.recoveryHints,
        agentError: d.agent_error ? (d.agent_error as ToolSegment['agentError']) : sg.agentError,
        ok: d.ok === false ? false : sg.ok }
    : sg)
}

/** `approval`: the call waits on her answer. The card addresses the call by the CHAT's own id —
 *  what the transcript rehydrates as `approval_id` and what the approve route takes; `d.id` is the
 *  registry id every other surface uses, unique across chats. An ask with a `source` was raised by
 *  work this chat started (a subagent, a batch), and its `request_id` IS the registry id: only the
 *  approvals queue holds it, so its card answers there (`queued`). The risk and the radius are
 *  decoded, never cast: a frame another build sent can carry a level or a shape this one cannot
 *  read, and that must be no claim at all. */
export function applyApprovalFrame(segs: Segment[], d: Frame): Segment[] {
  const id = String(d.request_id ?? '')
  if (segs.some((sg) => sg.kind === 'approval' && sg.id === id)) return segs
  return [...segs, {
    kind: 'approval', id, tool: String(d.tool ?? 'tool'), input: String(d.tool_input ?? ''),
    purpose: String(d.tool_purpose ?? ''), risk: approvalRiskOf(d.risk),
    blastRadius: blastRadiusOf(d.blast_radius), grantAgent: d.grant_agent ? String(d.grant_agent) : '',
    reach: d.reach ? String(d.reach) : '',
    ...(d.source ? { queued: true } : {}),
  }]
}

/** `approval_resolved`: how the approval ENDED (approved / rejected / expired / cancelled) — a
 *  stopped turn is not a Deny, and the card says which, in the words the transcript row uses. */
export function applyApprovalResolved(segs: Segment[], d: Frame): Segment[] {
  const id = String(d.request_id ?? '')
  return segs.map((sg) => sg.kind === 'approval' && sg.id === id
    ? { ...sg, resolved: String(d.outcome ?? '') } as ApprovalSegment
    : sg)
}
