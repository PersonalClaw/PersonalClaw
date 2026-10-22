import { useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { motion } from 'framer-motion'
import { ShieldQuestion, type LucideIcon } from 'lucide-react'
import { messageEnter } from '../design/motion'
import { fvs } from '../design/fontWeight'
import { clipWords } from '../lib/clipWords'
import { Button } from './Button'
import { TextLink } from './TextLink'
import { bindingLabel, matchBinding } from '../app/keymap'

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
 *   • `compact` — the chat column and the code cockpit. The argument line is one mono row cut
 *     at a word, with the whole input a click away; the card sits inline between turns.
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
  /** Send the answer, and return the request: it resolves `true` once the answer landed. Until it
   *  settles the prompt sends nothing more; once it landed the prompt stays decided while it is on
   *  the page (the row leaves when its resolved frame or the next read arrives), so a decision is
   *  one request. Anything else (a rejection, `false`, no promise at all) is an answer that did not
   *  land, and leaves the prompt answerable. */
  onClick: () => void | Promise<unknown>
  /** This choice approves the call: the one ⌘Enter presses, and the one a second confirmation
   *  holds back. At most one choice per prompt. */
  approves?: boolean
  /** An answer for THIS prompt is in flight.
   *
   *  Natively `disabled` on purpose (the one class `rawSoftOffContract` leaves native): a
   *  permission decision must not be re-submittable while its POST is outstanding, and
   *  `aria-busy` already says why the control is inert — there is no "reason" a user could act
   *  on, so soft-off with an explanation would be the wrong shape here. */
  busy?: boolean
}

/** The second confirmations the approval row names (`approval_confirms.CONFIRMS`), each said as
 *  what the call would do. */
export const CONFIRM_WORDS: Record<string, string> = {
  deletes: 'It deletes files.',
  outside_workspace: "It changes files outside this chat's folder.",
  sends: 'It sends something to other people.',
  spends: 'It spends money.',
  undeclared: "Its tool doesn't say what it does, so it could delete, change files outside this chat's folder, send to other people or spend money.",
}

/** The words for *confirms*, one sentence each; an unknown kind is said as the undeclared one. */
export function confirmSentences(confirms: readonly string[]): string[] {
  return [...new Set(confirms.map((k) => CONFIRM_WORDS[k] ?? CONFIRM_WORDS.undeclared))]
}

/** Whether a key pressed on *el* is typed into a text field: an input, a text area or an editor. */
function typesText(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  if (el.isContentEditable || el.closest('[contenteditable="true"],[role="textbox"]')) return true
  const tag = el.tagName
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (tag !== 'INPUT') return false
  const type = (el as HTMLInputElement).type
  return !['button', 'checkbox', 'radio', 'submit', 'reset'].includes(type)
}

/** Whether *el* is out of sight or out of reach: under a hidden, inert or aria-hidden ancestor, or
 *  behind a modal dialog. A modal covers the page whether or not it made the rest inert, and the
 *  browser can still report focus on a card behind it (a dialog that did not take focus, a key
 *  that raced its focus trap), so a card no open modal contains is out of reach. A dialog the card
 *  itself opened counts too: while it is open the card is under it. */
function unseen(el: HTMLElement): boolean {
  if (el.closest('[hidden],[inert],[aria-hidden="true"]') !== null) return true
  // Unquoted: the same selector, and this file declares no dialog (`dialogFocusContract` reads
  // the attribute as written).
  const modals = el.ownerDocument.querySelectorAll('[aria-modal=true]')
  return [...modals].some((m) => m.isConnected && !m.contains(el))
}

export function ApprovalPrompt({
  tool, args, purpose, source, sourceHref, badge, meta, scope, choices, density = 'compact', className,
  confirms = [],
}: {
  tool: string
  /** The tool's arguments, raw. `compact` cuts them at a word and shows all of them on demand;
   *  `roomy` shows all of them. */
  args?: string
  purpose?: string
  /** Where the call came from, in the registry's words (`PendingApproval.source_label`):
   *  `loop “Fix the README”`, `workflow “deep-research” · step “sweep”`. Shown as "From …" on every
   *  surface that is not the place it came from, so the same call is never approved there blind
   *  to who asked. */
  source?: string
  /** Where that work can be opened, when the surface is not already it. The source becomes a link. */
  sourceHref?: string
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
  /** The second confirmations the approval row names (`PendingApproval.confirms`). Non-empty: the
   *  approving choice first asks once more, saying what the call would do, and sends nothing
   *  before that confirm. */
  confirms?: readonly string[]
}) {
  const roomy = density === 'roomy'
  const rootRef = useRef<HTMLDivElement>(null)
  const yesRef = useRef<HTMLButtonElement>(null)
  // One request per decision, whichever choice it is: set when an answer is sent, and kept once it
  // landed, so a card still on the page (its row leaves on the resolved frame or the next read)
  // sends no second request. Cleared only by an answer that did not land.
  const decided = useRef(false)
  const [armed, setArmed] = useState(false)
  const approving = choices.find((c) => c.approves)
  const asks = confirms.length > 0

  function send(choice: ApprovalChoice) {
    if (decided.current || choice.busy) return
    decided.current = true
    const reopen = () => { decided.current = false }
    try {
      Promise.resolve(choice.onClick()).then((landed) => { if (landed !== true) reopen() }, reopen)
    } catch {
      reopen()
    }
  }

  /** A press of the approving choice. When the row names a second confirmation every press of it,
   *  the second of a double-click or a held Enter's repeat included, only asks: the confirm alone
   *  sends. */
  function approve(choice: ApprovalChoice) {
    if (asks) { setArmed(true); return }
    send(choice)
  }

  // ⌘Enter (Ctrl+Enter elsewhere) approves THIS request, only while focus is inside it: never from
  // a text field, never on a hidden or detached card, never while its answer is on its way or once
  // it landed, and a held key sends nothing more. Armed, the key sends only from the confirm
  // itself, and anywhere else in the card it does nothing. Escape steps back from the confirm and
  // decides nothing. Enter alone, and every other key, decide nothing here; Enter or Space on a
  // focused button presses that button, as a click does.
  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const root = rootRef.current
    if (!root) return
    // A dialog portalled from inside the card bubbles its keys here through React, from outside
    // the card on the page: only a key pressed on the card itself, while nothing covers it, counts.
    // So Escape in that dialog closes the dialog's detail and leaves the card's confirm as it was.
    const onTheCard = e.target instanceof Node && root.contains(e.target) && !unseen(root)
    if (e.key === 'Escape' && armed) {
      if (!onTheCard) return
      e.stopPropagation()
      setArmed(false)
      return
    }
    if (!approving || !matchBinding(e, 'approve-focused')) return
    e.preventDefault()
    e.stopPropagation()
    if (!onTheCard || e.repeat || typesText(e.target)) return
    if (armed) {
      if (e.target === yesRef.current) send(approving)
      return
    }
    approve(approving)
  }

  return (
    <motion.div ref={rootRef} variants={messageEnter} initial="initial" animate="animate"
      role="group" aria-label={`Permission needed to run ${tool}`} tabIndex={-1} onKeyDown={onKeyDown}
      className={`${roomy ? '' : 'my-1.5 '}overflow-hidden rounded-xl border${className ? ` ${className}` : ''}`}
      style={{ borderRadius: 'var(--radius-md)', borderColor: 'color-mix(in srgb, var(--color-warn) 40%, transparent)', background: 'color-mix(in srgb, var(--color-warn) 8%, transparent)' }}>
      <div className={roomy ? 'flex items-start gap-3 px-l pt-l' : 'flex items-start gap-2 px-3 pt-2.5'}>
        <ShieldQuestion size={roomy ? 18 : 15} className="mt-0.5 shrink-0" aria-hidden style={{ color: 'var(--color-warn)' }} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <div role="alert" data-type={roomy ? 'title-m' : 'label-s'} className="text-on-surface" style={fvs(500)}>Permission needed</div>
            {badge}
          </div>
          {roomy ? (
            <>
              <div data-type="body-m" className="mt-1 break-all font-mono text-on-surface">{tool}</div>
              {args && (
                // Wrapped and scrollable rather than truncated: on the phone this text IS
                // the decision. A capped height keeps one enormous payload from burying the
                // Allow/Deny row below the fold.
                <pre tabIndex={0} role="group" aria-label="Tool arguments" data-type="caption"
                  className="mt-s max-h-[14rem] overflow-auto whitespace-pre-wrap break-all rounded-md bg-surface-high p-s font-mono text-on-surface-var">{args}</pre>
              )}
            </>
          ) : (
            <CompactArgs tool={tool} args={args} />
          )}
          {purpose && <p data-type={roomy ? 'body-s' : 'caption'} className="mt-1 text-on-surface-low">{purpose}</p>}
          {source && (
            <p data-type={roomy ? 'body-s' : 'caption'} className="mt-xs break-words text-on-surface-low">
              From {sourceHref
                ? <TextLink href={sourceHref} ink="emphasis" aria-label={`Open ${source}`}>{source}</TextLink>
                : <span className="text-on-surface-var">{source}</span>}
            </p>
          )}
          {meta}
          {/* How far the answer reaches, read BEFORE the verbs — the scope has to be
              settled while the reader is still weighing the call, not after they have
              already reached for a button. */}
          {scope}
        </div>
      </div>
      <div className={roomy ? 'flex flex-wrap items-center gap-s px-l py-l' : 'flex flex-wrap items-center gap-1.5 px-3 py-2.5'}>
        {choices.map((c) => (
          // Every choice sends through `send`, so a Deny on its way holds the key too.
          <ApprovalChoiceButton key={c.key} roomy={roomy}
            choice={{ ...c, onClick: () => (c.approves ? approve(c) : send(c)) }} />
        ))}
      </div>
      {armed && approving && (
        // The second confirmation: what the call would do, then the confirm and a way back. Nothing
        // moves focus here, so a held key cannot fall through onto the confirm.
        <div className={roomy ? 'flex flex-col gap-s px-l pb-l' : 'flex flex-col gap-1.5 px-3 pb-2.5'}>
          <p role="status" data-type={roomy ? 'body-s' : 'caption'} className="text-on-surface">
            {confirmSentences(confirms).join(' ')} Run it?
          </p>
          <div className="flex flex-wrap items-center gap-1.5">
            <ApprovalChoiceButton roomy={roomy} buttonRef={yesRef}
              choice={{ ...approving, key: 'confirm', label: 'Yes, run it', name: `Yes, run it: ${approving.name || approving.label}`,
                onClick: () => send(approving) }} />
            <ApprovalChoiceButton roomy={roomy}
              choice={{ key: 'back', label: 'Back', icon: approving.icon, name: 'Back, decide nothing yet (Esc)', onClick: () => setArmed(false) }} />
          </div>
        </div>
      )}
    </motion.div>
  )
}

/** How much of the arguments the compact line shows before it offers the rest. */
const COMPACT_ARGS = 60

/** The compact argument line: `tool(args)`, the arguments cut at a word, and all of them on demand.
 *
 *  It was `args.slice(0, 60)` under a CSS truncate, so the cockpit asked to allow
 *  `bash({"command": "find /home/user/src/feedsmith -name \"C)` and nothing on the card showed the
 *  rest: she allowed a command she could not read. The line is cut at a word (`clipWords`, the
 *  server's `textfmt.clip_words` rule) and wraps rather than truncating, and when it is cut the
 *  whole input opens in the same scrollable block the roomy card shows. */
function CompactArgs({ tool, args }: { tool: string; args?: string }) {
  const [open, setOpen] = useState(false)
  const whole = (args ?? '').split(/\s+/).filter(Boolean).join(' ')
  const line = clipWords(whole, COMPACT_ARGS)
  const cut = line !== whole
  return (
    <>
      <div className="mt-0.5 flex flex-wrap items-baseline gap-x-s">
        <div data-type="caption" className="min-w-0 break-all font-mono text-on-surface-var">{tool}{line ? `(${line})` : ''}</div>
        {cut && (
          <Button variant="ghost" size="xs" ariaExpanded={open}
            ariaLabel={open ? `Hide what ${tool} would run` : `Show all of what ${tool} would run`}
            onClick={() => setOpen((o) => !o)}>
            {open ? 'Hide' : 'Show all'}
          </Button>
        )}
      </div>
      {cut && open && (
        <pre tabIndex={0} role="group" aria-label="Tool arguments" data-type="caption"
          className="mt-xs max-h-[14rem] overflow-auto whitespace-pre-wrap break-all rounded-md bg-surface-high p-s font-mono text-on-surface-var">{args}</pre>
      )}
    </>
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
function ApprovalChoiceButton({ choice, roomy, buttonRef }: {
  choice: ApprovalChoice; roomy: boolean; buttonRef?: React.Ref<HTMLButtonElement>
}) {
  const { icon: Icon, label, tone = 'neutral', name, onClick, busy, approves } = choice
  // The key is shown where the command is: on the approving choice's tooltip, and only there.
  const tip = `${name || label}${approves ? ` (${bindingLabel('approve-focused')})` : ''}`
  const palette = tone === 'primary'
    ? { background: 'var(--color-primary)', color: 'var(--color-on-primary)' }
    : tone === 'danger'
      ? { background: 'color-mix(in srgb, var(--color-danger) 14%, transparent)', color: 'var(--color-danger)' }
      : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }
  return (
    <button ref={buttonRef} type="button" onClick={() => { void onClick() }} disabled={busy} aria-busy={busy || undefined}
      title={tip} aria-label={name || undefined}
      data-type={roomy ? 'body-m' : 'caption'}
      className={`inline-flex items-center justify-center gap-1 rounded-pill transition-colors disabled:opacity-50 ${roomy ? 'h-11 min-w-[6.5rem] gap-2 px-l' : 'h-7 px-2.5'}`}
      style={palette}>
      <Icon size={roomy ? 16 : 12} aria-hidden /> {label}
    </button>
  )
}
