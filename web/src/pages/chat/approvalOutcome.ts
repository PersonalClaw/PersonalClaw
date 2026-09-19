import { Check, Ban, HelpCircle } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

// How a SETTLED permission row renders in the transcript. Kept pure (no React) so
// every backend outcome is unit-pinned without mounting the card — the transcript is
// the permanent record of a security decision, so a wrong mapping here misreports
// one rather than merely looking off.
//
// The full set the backend persists into a permission row's `resolved`, and who
// writes it (dashboard/):
//   approved     — chat_handlers api_chat_session_approve ("Allow once" and one-shot YOLO
//                  grants); state resolve_approval
//   rejected     — the same writers, on a deny or an unknown action
//   trust        — api_chat_session_approve ("Allow for this chat"), and api_chat_mode
//                  bulk-resolving what was pending when the chat's Trust rung is set
//   trust_agent  — api_chat_session_approve ("Always for this agent"), when the grant was
//                  PERSISTED onto the bound agent's profile (approval_mode="auto")
//   trust_agent_session
//                — the same click, when the grant could NOT persist: a reserved system
//                  agent (fixed config), a name with no profile, or an ACP-bound chat.
//                  It trusted this chat and nothing more.
//   trust_reads  — api_chat_session_approve, read-only-tool grant for this chat
//   yolo         — api_chat_mode bulk-resolving pending calls under process-global YOLO
//
// The two `trust_agent*` rows are #683 + #541. The handler used to overwrite the action with
// `"approved"` before the record was written, so "Always for this agent" and "Allow once"
// were indistinguishable in the transcript — the standing grant that explains every later
// silent approval left no trace at the point it was made. `Session.mark_permission_resolved`
// is gone from this list too: it was a duplicate writer with a `decision="approved"` default
// and no production callers, deleted in the same change.
export type ApprovalResolution =
  | 'approved' | 'rejected' | 'trust' | 'trust_agent' | 'trust_agent_session'
  | 'trust_reads' | 'yolo'

export interface ApprovalOutcome {
  label: string
  icon: LucideIcon
  tone: string
}

// trust / trust_reads / yolo are APPROVALS — the tool ran — so they all take the
// approved treatment (check, ok tone). Only the wording differs, and it names the
// SCOPE: an auditor reading a transcript has to be able to tell a call the user
// individually confirmed from one a standing grant auto-approved. "auto-approved"
// is the same word ToolSegment.auto already uses for that distinction.
const OUTCOMES: Record<ApprovalResolution, ApprovalOutcome> = {
  approved: { label: 'approved', icon: Check, tone: 'var(--color-ok)' },
  trust: { label: 'auto-approved (trusted for this chat)', icon: Check, tone: 'var(--color-ok)' },
  // The two outcomes of one click, and the wording is the difference that matters to an
  // auditor: the first keeps auto-approving chats that have not happened yet, the second
  // expired with this session. "saved" vs "this chat only" is the whole distinction.
  trust_agent: { label: 'auto-approved (saved for this agent)', icon: Check, tone: 'var(--color-ok)' },
  trust_agent_session: { label: 'auto-approved (this agent, this chat only — not saved)', icon: Check, tone: 'var(--color-ok)' },
  trust_reads: { label: 'auto-approved (reads trusted for this chat)', icon: Check, tone: 'var(--color-ok)' },
  yolo: { label: 'auto-approved (YOLO — everywhere)', icon: Check, tone: 'var(--color-ok)' },
  rejected: { label: 'denied', icon: Ban, tone: 'var(--color-on-surface-low)' },
}

/** Render treatment for a settled permission row. Takes the wire `string` (persisted
 *  sessions outlive the build that wrote them), never the narrowed union, so an
 *  outcome this build doesn't know still gets an honest render.
 *
 *  An unrecognized value must NOT fall through to "denied": claiming a tool was
 *  blocked when we don't know is the same misreport as the bug this replaces, only
 *  inverted. It also must not claim "approved". So it gets a third, visually
 *  distinct treatment that asserts nothing and shows the literal value an auditor
 *  can look up. Truncated like the card's other untrusted-length field so a
 *  pathological persisted value can't stretch the chat column. */
export function approvalOutcome(resolved: string): ApprovalOutcome {
  return OUTCOMES[resolved as ApprovalResolution] ?? {
    label: `resolved: ${resolved.slice(0, 24)}`,
    icon: HelpCircle,
    tone: 'var(--color-on-surface-low)',
  }
}
