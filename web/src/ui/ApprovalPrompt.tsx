import type { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { ShieldQuestion, type LucideIcon } from 'lucide-react'
import { messageEnter } from '../design/motion'
import { fvs } from '../design/fontWeight'

/** ONE renderer for "the agent is blocked waiting on your permission".
 *
 *  There are two surfaces that ask this question and they must not drift apart: the in-chat
 *  card (`pages/chat/ApprovalCard`, driven by a transcript segment and the chat-scoped
 *  trust vocabulary) and the phone companion (`pages/companion/CompanionPage`, driven by the
 *  `GET /api/approvals` queue with approve/reject only). They differ in their DATA and their
 *  ACTION VOCABULARY, not in what a permission prompt looks like or how it is announced —
 *  so the shell lives here once and each surface supplies an adapter.
 *
 *  Two densities, because the difference is real and not cosmetic:
 *   • `compact` — the chat column. The argument line is a single truncated mono row; the
 *     card sits inline between turns.
 *   • `roomy` — the companion. FULL arguments (a phone approval is the whole decision, so
 *     truncating the thing being approved would hide what is being consented to), a metadata
 *     block, and ≥44px action targets for a thumb.
 *
 *  Announcement contract (identical on both): `role="group"` named for the tool so a screen
 *  reader user knows which prompt they are in when several pend, plus an inner `role="alert"`
 *  so arrival interrupts — the agent is halted until this is answered.
 */
export type ApprovalDensity = 'compact' | 'roomy'

export interface ApprovalChoice {
  key: string
  /** Visible verb — kept short so the row wraps on a narrow column. */
  label: string
  icon: LucideIcon
  /** primary = the least-privilege default; danger = the deny edge; neutral = broader grants. */
  tone?: 'primary' | 'neutral' | 'danger'
  /** The ACCESSIBLE name, when `label` alone would be ambiguous.
   *
   *  A queue renders one card per pending approval, so a bare "Allow" appears N times and
   *  announces identically every time. Pass the composed name ("Allow Bash") and it becomes
   *  the button's name via `aria-label` — NOT via an `sr-only` span, which would be
   *  concatenated into the name alongside the visible verb instead of replacing it. */
  name?: string
  onClick: () => void
  /** An answer for THIS prompt is in flight.
   *
   *  Natively `disabled` on purpose (the one class `rawSoftOffContract` leaves native): a
   *  permission decision must not be re-submittable while its POST is outstanding, and
   *  `aria-busy` already says why the control is inert — there is no "reason" a user could act
   *  on, so soft-off with an explanation would be the wrong shape here. */
  busy?: boolean
}

export function ApprovalPrompt({
  tool, args, purpose, badge, meta, scope, choices, density = 'compact', className,
}: {
  tool: string
  /** The tool's arguments, raw. `compact` truncates; `roomy` shows all of it. */
  args?: string
  purpose?: string
  /** Optional chip beside the heading (the chat's risk indicator). */
  badge?: ReactNode
  /** Context block under the arguments — the chat's blast-radius chips, the
   *  companion's session/source/age. */
  meta?: ReactNode
  /** Optional block between the brief and the action row: how far the answer should
   *  reach (the chat's remember-scope picker).
   *
   *  A SLOT rather than a shared control, because the two surfaces do not have the same
   *  answer to give. The chat card can persist a standing grant (`trust`/`trust_agent`),
   *  so it offers a scope; the companion's queue posts approve/reject only, so it passes
   *  nothing and renders nothing. Nothing here is defaulted or auto-selected — the
   *  caller owns the wording of every promise it makes about what gets remembered. */
  scope?: ReactNode
  choices: ApprovalChoice[]
  density?: ApprovalDensity
  className?: string
}) {
  const roomy = density === 'roomy'
  return (
    <motion.div variants={messageEnter} initial="initial" animate="animate"
      role="group" aria-label={`Permission needed to run ${tool}`}
      className={`${roomy ? '' : 'my-1.5 '}overflow-hidden rounded-xl border${className ? ` ${className}` : ''}`}
      style={{ borderRadius: 'var(--radius-md)', borderColor: 'color-mix(in srgb, var(--color-warn) 40%, transparent)', background: 'color-mix(in srgb, var(--color-warn) 8%, transparent)' }}>
      <div className={roomy ? 'flex items-start gap-3 px-l pt-l' : 'flex items-start gap-2 px-3 pt-2.5'}>
        <ShieldQuestion size={roomy ? 18 : 15} className="mt-0.5 shrink-0" aria-hidden style={{ color: 'var(--color-warn)' }} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <div role="alert" className={`text-on-surface ${roomy ? 'text-[0.9375rem]' : 'text-[0.8125rem]'}`} style={fvs(500)}>Permission needed</div>
            {badge}
          </div>
          {roomy ? (
            <>
              <div className="mt-1 break-all font-mono text-on-surface text-[0.875rem]">{tool}</div>
              {args && (
                // Wrapped and scrollable rather than truncated: on the phone this text IS
                // the decision. A capped height keeps one enormous payload from burying the
                // Allow/Deny row below the fold.
                <pre className="mt-s max-h-[14rem] overflow-auto whitespace-pre-wrap break-all rounded-md bg-surface-high p-s font-mono text-on-surface-var text-[0.75rem]">{args}</pre>
              )}
            </>
          ) : (
            <div className="mt-0.5 truncate font-mono text-on-surface-var text-[0.75rem]">{tool}{args ? `(${args.replace(/\s+/g, ' ').slice(0, 60)})` : ''}</div>
          )}
          {purpose && <p className={`mt-1 text-on-surface-low ${roomy ? 'text-[0.8125rem]' : 'text-[0.75rem]'}`}>{purpose}</p>}
          {meta}
          {/* How far the answer reaches, read BEFORE the verbs — the scope has to be
              settled while the reader is still weighing the call, not after they have
              already reached for a button. */}
          {scope}
        </div>
      </div>
      <div className={roomy ? 'flex flex-wrap items-center gap-s px-l py-l' : 'flex flex-wrap items-center gap-1.5 px-3 py-2.5'}>
        {choices.map((c) => <ApprovalChoiceButton key={c.key} choice={c} roomy={roomy} />)}
      </div>
    </motion.div>
  )
}

/** The prompt's action pill.
 *
 *  Deliberately NOT the `Button` primitive: the deny edge here is a TINTED danger chip, and
 *  `Button`'s only danger tier is solid `bg-danger`. Repainting a security prompt's Deny
 *  button solid red is a visual change to the surface a user reads under time pressure, and
 *  it is not this change's job. It lives in `ui/` — the sanctioned home for shared chrome —
 *  so it is one implementation rather than a per-page hand-roll.
 */
function ApprovalChoiceButton({ choice, roomy }: { choice: ApprovalChoice; roomy: boolean }) {
  const { icon: Icon, label, tone = 'neutral', name, onClick, busy } = choice
  const palette = tone === 'primary'
    ? { background: 'var(--color-primary)', color: 'var(--color-on-primary)' }
    : tone === 'danger'
      ? { background: 'color-mix(in srgb, var(--color-danger) 14%, transparent)', color: 'var(--color-danger)' }
      : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }
  return (
    <button type="button" onClick={onClick} disabled={busy} aria-busy={busy || undefined}
      title={name || label} aria-label={name || undefined}
      className={`inline-flex items-center justify-center gap-1 rounded-pill transition-colors disabled:opacity-50 ${roomy ? 'h-11 min-w-[6.5rem] gap-2 px-l text-[0.9375rem]' : 'h-7 px-2.5 text-[0.75rem]'}`}
      style={palette}>
      <Icon size={roomy ? 16 : 12} aria-hidden /> {label}
    </button>
  )
}
