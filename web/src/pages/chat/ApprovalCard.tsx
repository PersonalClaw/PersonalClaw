import { motion } from 'framer-motion'
import { ShieldQuestion, Check, Ban, Clock, ShieldCheck, ShieldAlert, AlertTriangle } from 'lucide-react'
import { messageEnter } from '../../design/motion'
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
    <span className="inline-flex items-center gap-1 rounded-pill px-1.5 h-[18px] text-[0.65rem] shrink-0"
      title={`Risk: ${m.label}`} aria-label={`Risk level: ${m.label}`}
      style={{ background: `color-mix(in srgb, ${m.color} 16%, transparent)`, color: m.color, fontVariationSettings: '"wght" 600' }}>
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
 *  collapses to a quiet outcome line. */
export function ApprovalCard({ seg, onAct }: { seg: ApprovalSegment; onAct: (id: string, action: Action) => void }) {
  if (seg.resolved) {
    const ok = seg.resolved === 'approved'
    return (
      <div className="my-1 flex items-center gap-1.5 text-[0.75rem]" style={{ color: ok ? 'var(--color-ok)' : 'var(--color-on-surface-low)' }}>
        {ok ? <Check size={13} aria-hidden /> : <Ban size={13} aria-hidden />}
        <span>{seg.tool} — {ok ? 'approved' : 'denied'}</span>
      </div>
    )
  }
  return (
    // role=alert + aria-label so a screen reader is interrupted to announce that a
    // blocking permission decision is required (the agent halts until acted on);
    // role=group ties the scope buttons to this prompt.
    <motion.div variants={messageEnter} initial="initial" animate="animate"
      role="group" aria-label={`Permission needed to run ${seg.tool}`}
      className="my-1.5 overflow-hidden rounded-xl border" style={{ borderRadius: 'var(--radius-md)', borderColor: 'color-mix(in srgb, var(--color-warn) 40%, transparent)', background: 'color-mix(in srgb, var(--color-warn) 8%, transparent)' }}>
      <div className="flex items-start gap-2 px-3 pt-2.5">
        <ShieldQuestion size={15} className="mt-0.5 shrink-0" aria-hidden style={{ color: 'var(--color-warn)' }} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <div role="alert" className="text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 500' }}>Permission needed</div>
            {seg.risk && <RiskChip risk={seg.risk} />}
          </div>
          <div className="mt-0.5 truncate font-mono text-on-surface-var text-[0.75rem]">{seg.tool}{seg.input ? `(${seg.input.replace(/\s+/g, ' ').slice(0, 60)})` : ''}</div>
          {seg.purpose && <p className="mt-1 text-on-surface-low text-[0.7rem]">{seg.purpose}</p>}
        </div>
      </div>
      {/* Scope picker: allow-once is the primary (least-privilege default); the two
          broader grants carry a "how long" icon so the durability is legible; Deny is
          the destructive edge. One row, wraps on a narrow chat column. */}
      <div className="flex flex-wrap items-center gap-1.5 px-3 py-2.5">
        <ApprBtn icon={Check} label="Allow once" primary onClick={() => onAct(seg.id, 'approved')} />
        <ApprBtn icon={Clock} label="Allow for this chat" onClick={() => onAct(seg.id, 'trust')} />
        <ApprBtn icon={ShieldCheck} label="Always for this agent" onClick={() => onAct(seg.id, 'trust_agent')} />
        <ApprBtn icon={Ban} label="Deny" danger onClick={() => onAct(seg.id, 'rejected')} />
      </div>
    </motion.div>
  )
}

function ApprBtn({ icon: Icon, label, primary, danger, onClick }: { icon: typeof Check; label: string; primary?: boolean; danger?: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} title={label}
      className="inline-flex items-center gap-1 rounded-pill px-2.5 h-7 text-[0.75rem] transition-colors"
      style={primary
        ? { background: 'var(--color-primary)', color: 'var(--color-on-primary)' }
        : danger
          ? { background: 'color-mix(in srgb, var(--color-danger) 14%, transparent)', color: 'var(--color-danger)' }
          : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }}>
      <Icon size={12} aria-hidden /> {label}
    </button>
  )
}
