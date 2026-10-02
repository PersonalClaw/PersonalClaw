import type { PendingApproval } from '../../lib/api'
import { approvalRiskOf, blastRadiusOf } from './approvalMeta'
import type { ApprovalSegment } from './chatTypes'

/** An approval's input as text: the registry sends a string, a native call's dict as JSON. */
export function approvalInputText(raw: unknown): string {
  if (raw === undefined || raw === null) return ''
  return typeof raw === 'string' ? raw : JSON.stringify(raw)
}

/** What the approval card reads off a registry row: the call, how risky it is, who asked. */
export type ApprovalCardInput = Pick<
  PendingApproval,
  'id' | 'request_id' | 'tool' | 'tool_input' | 'tool_purpose' | 'risk' | 'blast_radius' | 'grant_agent'
  | 'session' | 'source_label' | 'reach' | 'deny_effect'
>

/** A registry row (`GET /api/approvals`, the `approval` frame) as the card's segment, so every
 *  surface that lists approvals hands `ApprovalCard` the same fields the chat's transcript does.
 *  `id` is the waiter's own id for the call, which a chat's approve route takes. */
export function approvalSegmentOf(a: ApprovalCardInput): ApprovalSegment {
  return {
    kind: 'approval',
    id: a.request_id,
    tool: a.tool || 'a tool',
    input: approvalInputText(a.tool_input),
    purpose: a.tool_purpose || '',
    risk: approvalRiskOf(a.risk),
    blastRadius: blastRadiusOf(a.blast_radius),
    grantAgent: a.grant_agent || '',
    reach: a.reach || '',
    ...(a.deny_effect ? { denyEffect: a.deny_effect } : {}),
  }
}
