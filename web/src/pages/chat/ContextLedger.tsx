import { useEffect, useRef, useState, type ReactNode, type Ref } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Brain, ChevronRight, Gauge, Sparkles, type LucideIcon } from 'lucide-react'
import { spring } from '../../design/motion'
import { TextLink } from '../../ui/TextLink'
import { learnedSurface } from './chatTypes'

/** Holistic per-turn context-transparency footer. Consolidates the three
 *  provenance signals — what context FED the turn (memory/lessons/knowledge/
 *  skills/workflows), what the turn LEARNED & saved (after-turn review), and the
 *  turn TELEMETRY — into one quiet, collapsed-by-default affordance. The
 *  high-signal "learned" flag stays visible even collapsed (so the user always
 *  sees, and can open to undo, what was persisted). On demand, never intrusive.
 *
 *  Lives in its own module rather than inside `ChatPage.tsx` because its ONE-ACTION
 *  reach (below) is a behavioural contract, and a component defined inside a ~4k-line
 *  page that owns a socket and a composer cannot be mounted to prove one. See
 *  `contextLedgerReach.test.tsx`, which renders exactly this component with the real
 *  handler wired and taps it the way a user does. */
export function ContextLedger({ fed, learned, learnedOrigin, stats }: { fed?: string; learned?: string; learnedOrigin?: string; stats?: string }) {
  const [open, setOpen] = useState(false)
  const learnedRowRef = useRef<HTMLDivElement>(null)
  const fedChars = fed?.match(/([\d,]+)\s*chars/)?.[1] ?? ''
  // "Learned: <text>" → just the text for the expanded row.
  const learnedText = learned?.replace(/^Learned:\s*/i, '').trim() ?? ''
  // Where a tap on this chip lands, decided by the EMITTER (T2.2) rather than by the one
  // hardcoded Memory link this row used to carry for all three origins — which was right for
  // a facet and wrong for a skill proposal. `null` for an absent/unknown origin: the row
  // still renders its text, it just isn't a link, because we don't know which surface owns it.
  const surface = learnedSurface(learnedOrigin)
  const learnedHref = surface?.href ?? null

  // ── ONE action, not two (LV-2 owner ruling, 2026-08-26) ────────────────────────────────
  //
  // A learning the user has to go looking for is not visible, and visibility is this
  // plan's whole subject. Before this, opening the ledger only REVEALED the approve/edit
  // link somewhere below; finding and reaching it was a second action, and for a keyboard
  // user a second action plus an unknown number of Tab presses. So the tap that opens the
  // disclosure also brings the target into view and puts the caret on it: one tap, and the
  // surface that can approve, edit or undo the captured learning is the focused element.
  //
  // Scroll first, then `focus({ preventScroll: true })`: `focus()` scrolls on its own, which
  // would make "brought into view" an untestable side effect of the focus call and leave the
  // ledger's height animation racing a browser-chosen scroll. Explicit `block: 'nearest'` is
  // the idiom this app already uses in a dozen places, and the two halves stay separable —
  // each can be removed on its own and a test notices.
  //
  // Keyed on `learnedHref` rather than the `surface` object: `learnedSurface` returns a fresh
  // literal every render, which would re-fire the effect (and re-steal focus) on every keystroke
  // elsewhere in the page. A row with no known surface has no target, so nothing moves — the
  // ledger just opens, which is the pre-ruling behaviour and the correct degrade.
  useEffect(() => {
    if (!open || !learnedHref) return
    const link = learnedRowRef.current?.querySelector('a')
    if (!link) return
    link.scrollIntoView({ block: 'nearest' })
    link.focus({ preventScroll: true })
  }, [open, learnedHref])

  const summary = open
    ? 'Context & learning'
    : [fed && 'recalled context', learned && 'learned 1', stats && 'telemetry'].filter(Boolean).join(' · ') || 'Turn details'
  // Hover says what the tap DOES when there is somewhere to land, so the focus jump reads as
  // the affordance it is rather than as the page moving on its own. `title` is a hover
  // affordance only — the button's accessible name is its visible text, which already carries
  // "learned 1" — so nothing here is name-bearing and no `sr-only` is added.
  const collapsedTitle = surface
    ? 'What fed this turn · what was learned — opens and jumps to where you can review it'
    : 'What fed this turn · what was learned'
  return (
    <div className="mt-2 mb-1">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="flex items-center gap-1.5 rounded-pill text-on-surface-low/80 text-[0.75rem] transition-colors hover:text-on-surface-low"
        title={open ? 'Hide what fed this turn and what was learned' : collapsedTitle}>
        <motion.span animate={{ rotate: open ? 90 : 0 }} transition={spring.spatialFast} className="shrink-0 opacity-60">
          <ChevronRight size={11} />
        </motion.span>
        <Brain size={11} className="shrink-0 opacity-70" />
        <span>{summary}</span>
        {!open && learned && <Sparkles size={11} className="shrink-0 text-primary/80" />}
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
            transition={spring.spatialFast} className="overflow-hidden">
            <div className="mt-1.5 ml-1.5 flex flex-col gap-1.5 border-l border-outline-variant/40 pl-3 text-[0.75rem] text-on-surface-low">
              {fed && (
                <LedgerRow icon={Brain} label="Fed this turn">
                  Recalled relevant context{fedChars ? ` · ${fedChars} chars` : ''} — saved memories, learned lessons, earlier conversation, and episodic history, assembled and prepended to the prompt.
                </LedgerRow>
              )}
              {learned && (
                <LedgerRow icon={Sparkles} label="Learned & saved" contentRef={learnedRowRef}>
                  <span className="text-on-surface-var">{learnedText || 'A preference was captured.'}</span>
                  {surface && <>{' '}<TextLink href={surface.href}>{surface.label}</TextLink></>}
                </LedgerRow>
              )}
              {stats && (
                <LedgerRow icon={Gauge} label="Telemetry">
                  <span className="whitespace-pre-wrap break-words">{stats}</span>
                </LedgerRow>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/** One labeled row inside the {@link ContextLedger}. `contentRef` addresses the row's
 *  content box (not a new wrapper element, so the layout is unchanged) for the one caller
 *  that has to reach the link inside it — `TextLink` takes no ref, and widening a shared
 *  design-system primitive is not this atom's business. */
function LedgerRow({ icon: Icon, label, children, contentRef }: { icon: LucideIcon; label: string; children: ReactNode; contentRef?: Ref<HTMLDivElement> }) {
  return (
    <div className="flex items-start gap-1.5">
      <Icon size={11} className="mt-[0.15rem] shrink-0 opacity-70" />
      <div ref={contentRef} className="min-w-0">
        <span className="font-medium text-on-surface-low/90">{label}:</span>{' '}
        {children}
      </div>
    </div>
  )
}
