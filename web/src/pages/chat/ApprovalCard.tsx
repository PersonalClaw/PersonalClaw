import { withWeight } from '../../design/fontWeight'
import { Check, Ban, Clock, ShieldCheck, ShieldAlert, AlertTriangle } from 'lucide-react'
import { ApprovalPrompt } from '../../ui/ApprovalPrompt'
import { approvalOutcome } from './approvalOutcome'
import type { ApprovalSegment } from './chatTypes'

// Risk indicator (tool risk taxonomy): a purely INFORMATIONAL chip so the human
// can weigh the decision — it does not gate (an explicit trust/YOLO still
// auto-approves everything). safe = read-only/no side effects; caution = bounded
// write / unclassified external; destructive = arbitrary exec or host side-effects.
const RISK_META = {
  safe: { label: 'Safe', icon: ShieldCheck, color: 'var(--color-ok)' },
  caution: { label: 'Caution', icon: AlertTriangle, color: 'var(--color-warn)' },
  destructive: { label: 'Destructive', icon: ShieldAlert, color: 'var(--color-danger)' },
} as const

function RiskChip({ risk }: { risk: NonNullable<ApprovalSegment['risk']> }) {
  const m = RISK_META[risk]
  if (!m) return null
  const Icon = m.icon
  return (
    // No aria-label: on a role-less span it is a PROHIBITED attribute (discarded, and axe
    // reports aria-prohibited-attr), and it was redundant anyway — the chip renders
    // `m.label` as visible text below, so the risk level is already in the a11y tree. The
    // `title` stays as the hover affordance that spells out "Risk: …".
    <span className="inline-flex items-center gap-1 rounded-pill px-1.5 h-[18px] text-[0.75rem] shrink-0"
      title={`Risk: ${m.label}`}
      style={withWeight({ background: `color-mix(in srgb, ${m.color} 16%, transparent)`, color: m.color }, 600)}>
      <Icon size={11} aria-hidden /> {m.label}
    </span>
  )
}

// One vocabulary — a per-approval SCOPE choice (resolved with the user), not a mode
// toggle. `approved` = allow once; `trust` = allow all tools for THIS chat (session
// trust); `trust_agent` = always allow all tools for this agent (persists
// AgentProfile.approval_mode="auto") + this chat; `rejected` = deny.
type Action = 'approved' | 'rejected' | 'trust' | 'trust_agent'

/** Inline approval prompt — appears when the agent needs permission to run a tool.
 *  Offers a SCOPE picker (how long the permission lasts) rather than a set of trust
 *  modes: Allow once · Allow for this chat · Always for this agent · Deny. Wires to
 *  POST /api/chat/sessions/{s}/approve {action, request_id}. Once resolved it
 *  collapses to a quiet outcome line.
 *
 *  The card CHROME (warn-tinted shell, the role=group/role=alert announcement, the tool +
 *  argument line, the action row) is `ui/ApprovalPrompt` — shared with the phone companion's
 *  approvals queue so the two surfaces that ask for permission cannot drift. What stays here
 *  is what is genuinely chat's: the transcript segment shape, the settled-outcome collapse,
 *  the risk chip, and the chat-scoped trust vocabulary. */
export function ApprovalCard({ seg, onAct }: { seg: ApprovalSegment; onAct: (id: string, action: Action) => void }) {
  if (seg.resolved) {
    // Every outcome the backend persists is mapped EXPLICITLY (approvalOutcome), not
    // inferred from `!== 'approved'`: the trust/YOLO grants are approvals, and testing
    // inequality against one value collapsed all three into "denied" — the transcript
    // misreporting a security decision it is the permanent record of.
    const { label, icon: Icon, tone } = approvalOutcome(seg.resolved)
    return (
      <div className="my-1 flex items-center gap-1.5 text-[0.75rem]" style={{ color: tone }}>
        <Icon size={13} aria-hidden />
        <span>{seg.tool} — {label}</span>
      </div>
    )
  }
  return (
    // Scope picker: allow-once is the primary (least-privilege default); the two
    // broader grants carry a "how long" icon so the durability is legible; Deny is
    // the destructive edge. One row, wraps on a narrow chat column.
    <ApprovalPrompt
      tool={seg.tool}
      args={seg.input}
      purpose={seg.purpose}
      badge={seg.risk ? <RiskChip risk={seg.risk} /> : undefined}
      choices={[
        { key: 'approved', icon: Check, label: 'Allow once', tone: 'primary', onClick: () => onAct(seg.id, 'approved') },
        { key: 'trust', icon: Clock, label: 'Allow for this chat', onClick: () => onAct(seg.id, 'trust') },
        { key: 'trust_agent', icon: ShieldCheck, label: 'Always for this agent', onClick: () => onAct(seg.id, 'trust_agent') },
        { key: 'rejected', icon: Ban, label: 'Deny', tone: 'danger', onClick: () => onAct(seg.id, 'rejected') },
      ]}
    />
  )
}
