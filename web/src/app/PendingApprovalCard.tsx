import { rowSubject } from '../lib/rowSubject'
import { ApprovalCard } from '../pages/chat/ApprovalCard'
import { approvalInputText, approvalSegmentOf, type ApprovalCardInput } from '../pages/chat/approvalSegment'
import { approvalDestination } from './approvalDestination'

/** One pending approval from the queue (`GET /api/approvals`), answered here with Allow or Deny.
 *
 *  The approval card every surface outside the asking chat renders: the workflow run page, Mission
 *  Control, Home's To triage, and the Inbox row and its notification. It is the chat's own card
 *  (`ApprovalCard`), so a call reads the same everywhere it can be answered: the tool and its risk,
 *  what it can touch, the whole input a click away, and where it came from. The run page asked
 *  "This step needs your approval to run bash" and Mission Control "Loop · write_file", with no
 *  command and no path, while the Inbox showed both: she approved there blind.
 *
 *  The answer is the queue's (`POST /api/approvals/{id}/{action}`), which remembers nothing, so the
 *  card offers this call alone. *opensSource* links the source to where that work is answered in
 *  full, for a surface that lists approvals from everywhere.
 */
export function PendingApprovalCard({ approval, onDecide, busy, opensSource = false }: {
  approval: ApprovalCardInput
  onDecide: (action: 'approve' | 'reject') => void
  busy?: boolean
  opensSource?: boolean
}) {
  const source = approval.source_label || undefined
  // A queue holds one card per call, so the verbs' names carry who asked and what the call does.
  const subject = rowSubject([
    approval.tool, source, approval.tool_purpose || approvalInputText(approval.tool_input),
  ])
  return (
    <ApprovalCard
      seg={approvalSegmentOf(approval)}
      answers="once"
      source={source}
      sourceHref={opensSource && approval.session ? approvalDestination(approval.session).href : undefined}
      subject={subject}
      busy={busy}
      onAct={(_id, action) => onDecide(action === 'rejected' ? 'reject' : 'approve')}
    />
  )
}
