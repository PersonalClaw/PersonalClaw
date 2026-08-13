import { useState } from 'react'
import { withWeight } from '../../design/fontWeight'
import { Check, Ban, ShieldCheck, ShieldAlert, AlertTriangle } from 'lucide-react'
import { ApprovalPrompt } from '../../ui/ApprovalPrompt'
import { Segmented } from '../../ui/Segmented'
import { approvalOutcome } from './approvalOutcome'
import { deriveBlastRadius, establishedFacets } from './approvalMeta'
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

/** Zone 3 of the brief — WHAT THIS CAN TOUCH.
 *
 *  Renders the ESTABLISHED facets only (`approvalMeta.establishedFacets`), and renders
 *  nothing at all when the inputs established nothing. Four "no" chips from an all-false
 *  radius would be a confident all-clear derived from zero evidence — the one failure mode
 *  the derivation returns `undefined` to avoid, so the renderer must not undo it by
 *  enumerating the facets with on/off states. The list is NAMED, and its name says these
 *  are the facts established rather than a full audit, because the absence of a chip is
 *  not a guarantee about the tool.
 */
function BlastRadiusChips({ tool, risk }: { tool: string; risk?: ApprovalSegment['risk'] }) {
  const facets = establishedFacets(deriveBlastRadius({ tool, risk: risk ?? undefined }))
  if (facets.length === 0) return null
  return (
    <ul aria-label="What this can touch, as far as we can establish"
      className="mt-1.5 flex list-none flex-wrap items-center gap-1 p-0">
      {facets.map((f) => (
        <li key={f.key} title={f.detail}
          className="inline-flex items-center rounded-pill bg-surface-high px-1.5 h-[18px] text-on-surface-var text-[0.75rem]">
          {f.label}
        </li>
      ))}
    </ul>
  )
}

// One vocabulary — a per-approval SCOPE choice (resolved with the user), not a mode
// toggle. `approved` = allow once; `trust` = allow all tools for THIS chat (session
// trust); `trust_agent` = always allow all tools for this agent (persists
// AgentProfile.approval_mode="auto") + this chat; `rejected` = deny.
type Action = 'approved' | 'rejected' | 'trust' | 'trust_agent'

/** Zone 4 of the brief — HOW FAR THE ANSWER REACHES.
 *
 *  `promise` is the load-bearing string: it is what the user is told will be remembered,
 *  so it must describe the backend action EXACTLY and claim nothing more. Each row cites
 *  the action it posts (`chat_handlers.api_chat_session_approve`).
 *
 *  Contract C2 names three scopes — `session`, `tool_always`, `no`. Two of them ship here.
 *  `tool_always` does NOT, because nothing in this codebase remembers a decision per TOOL:
 *  `trust`/`trust_agent`/`yolo` are all "every tool" at a widening blast radius, and
 *  `config.hooks.auto_approve_tools` (the one per-tool matcher, hooks.py:394) is pinned into
 *  a `HookManager` at gateway construction (gateway.py:662) with no write path, so writing
 *  it would keep asking until a restart. Labelling any of those "always allow this tool"
 *  would be a security-relevant lie about what a click did, so the option is absent and
 *  recorded as a remainder in the plan instead. Widening it into `trust` was the
 *  alternative, and it is the worse of the two by a distance.
 */
const REMEMBER_SCOPES = [
  {
    key: 'once',
    label: 'Just this once',
    action: 'approved' as const,
    promise: 'Nothing is remembered. The next tool call asks again.',
  },
  {
    key: 'chat',
    label: 'This chat',
    action: 'trust' as const,
    // `trust` sets session._trust AND set_approval_policy(dashboard:<key>, "auto") — it is
    // not scoped to this tool, and saying "this tool" here would be the lie above.
    promise: 'Every tool in this chat runs without asking, until you change it back.',
  },
  {
    key: 'agent',
    label: 'This agent',
    action: 'trust_agent' as const,
    // `trust_agent` additionally persists AgentProfile.approval_mode="auto".
    promise: 'Saved on this agent: every tool runs without asking, in this chat and future ones.',
  },
] as const

type RememberScope = (typeof REMEMBER_SCOPES)[number]['key']

/** Inline approval prompt — appears when the agent needs permission to run a tool.
 *
 *  A four-zone decision brief (Design "Approval brief", Contract C2): WHAT (tool +
 *  arguments) · WHY (the runner's one-line purpose, when it supplied one) · WHAT IT CAN
 *  TOUCH (established blast-radius facets) · HOW FAR THE ANSWER REACHES (the
 *  remember-scope picker). Then one Allow and one Deny. Wires to
 *  POST /api/chat/sessions/{s}/approve {action, request_id}; once resolved it collapses to
 *  a quiet outcome line.
 *
 *  **The brief describes; it never advocates.** No zone says a call looks fine, nothing is
 *  recommended, no approve control is the visual primary, and nothing is focused or
 *  pre-submitted on arrival — the only thing preselected is the NARROWEST scope, which
 *  remembers nothing. A prompt that nudges is worse than no prompt: it trains the reflex it
 *  exists to interrupt. `ApprovalCard.test.tsx` holds that line mechanically.
 *
 *  The card CHROME (warn-tinted shell, the role=group/role=alert announcement, the tool +
 *  argument line, the action row) is `ui/ApprovalPrompt` — shared with the phone companion's
 *  approvals queue so the two surfaces that ask for permission cannot drift. What stays here
 *  is what is genuinely chat's: the transcript segment shape, the settled-outcome collapse,
 *  the risk chip, the blast-radius chips, and the chat-scoped trust vocabulary.
 */
export function ApprovalCard({ seg, onAct }: { seg: ApprovalSegment; onAct: (id: string, action: Action) => void }) {
  // The narrowest scope is the initial one: a click on Allow with nothing else touched
  // grants once and remembers nothing. Broadening is always a deliberate act.
  const [scope, setScope] = useState<RememberScope>('once')
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
  // `find` cannot miss (state is typed to the option keys), but a fallback keeps the card
  // renderable rather than crashing the transcript if that ever stops being true.
  const chosen = REMEMBER_SCOPES.find((s) => s.key === scope) ?? REMEMBER_SCOPES[0]
  return (
    <ApprovalPrompt
      tool={seg.tool}
      args={seg.input}
      purpose={seg.purpose}
      badge={seg.risk ? <RiskChip risk={seg.risk} /> : undefined}
      meta={<BlastRadiusChips tool={seg.tool} risk={seg.risk} />}
      scope={
        <div className="mt-2 flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-on-surface-low text-[0.75rem]">Remember this choice</span>
            <Segmented size="sm" ariaLabel="Remember this choice"
              options={REMEMBER_SCOPES.map((s) => ({ key: s.key, label: s.label, title: s.promise }))}
              value={scope} onChange={(k) => setScope(k as RememberScope)} />
          </div>
          {/* The promise, in plain sight rather than only in a tooltip: a scope the user
              cannot read is a scope they cannot consent to. Announced politely (not
              assertively) because the user caused the change by choosing it. */}
          <p aria-live="polite" className="text-on-surface-low text-[0.75rem]">{chosen.promise}</p>
        </div>
      }
      choices={[
        // Neither verb is the primary tier. `tone: 'primary'` would paint Allow as the
        // recommended action — the advocacy this surface must not do — so Allow is neutral
        // and Deny keeps the tinted danger edge it has always had. The accessible name
        // carries the scope, because "Allow" alone does not say how far the answer reaches.
        {
          key: 'allow', icon: Check, label: 'Allow',
          name: `Allow ${seg.tool} — ${chosen.label.toLowerCase()}: ${chosen.promise}`,
          onClick: () => onAct(seg.id, chosen.action),
        },
        {
          key: 'rejected', icon: Ban, label: 'Deny', tone: 'danger',
          // Deny is single-shot whatever the scope says: no backend action persists a
          // refusal, so the name states that rather than letting the picker imply it.
          name: `Deny ${seg.tool} — nothing is remembered`,
          onClick: () => onAct(seg.id, 'rejected'),
        },
      ]}
    />
  )
}

// Exported for the test that enumerates the scope vocabulary as a CLOSED set: every option
// must map to a distinct action `api_chat_session_approve` already implements, and no
// option may promise a persistence the backend does not perform.
export { REMEMBER_SCOPES, type RememberScope }
