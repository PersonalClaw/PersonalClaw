import { type ReactNode } from 'react'
import { motion } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import { cx } from '../../../ui/cx'
import { spring } from '../../../design/motion'
import { RowHitTarget } from '../../../ui/RowHitTarget'

/** Calm "all clear" dashboard-slot empty state — a compact, top-aligned strip
 *  (icon + line + optional inline action). Deliberately NOT the full-height
 *  centered `EmptyState` (ui/ListScaffold): dashboard sections sit side by side
 *  in grid rows, and a stretched empty widget next to a full sibling reads as a
 *  conspicuous void. The dashed hairline marks the slot as intentionally empty
 *  without adding card chrome. See docs/design/patterns.md — the two empty-state
 *  patterns are distinct on purpose (page-empty vs slot-empty). */
export function SlotEmptyState({ icon: Icon, children, action }: { icon: LucideIcon; children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex items-center gap-s self-start rounded-lg border border-dashed border-outline-variant/50 px-m py-s">
      <Icon size={15} className="shrink-0 text-on-surface-low opacity-70" />
      <p data-type="body-m" className="min-w-0 text-on-surface-low">{children}</p>
      {action && <div className="flex shrink-0 items-center" onClick={(e) => e.stopPropagation()}>{action}</div>}
    </div>
  )
}

/** The on-ramp inside a `SlotEmptyState` — "+ New task", "+ New trigger", "Open Settings".
 *
 *  ONE definition, because the second slot to need one is what turns a call-site idiom into
 *  drift. Tasks shipped this markup inline; Schedule needed the same affordance, and a
 *  hand-rolled copy would have put two differently-inked on-ramps side by side on the app's
 *  first screen.
 *
 *  Not only create actions: Discover's off-branch offers "Open Settings" — a remediation, not a
 *  "+ New X". The role is the SLOT's, not the verb's. Anything in a slot's `action` position is a
 *  slot action, because a user reading two slots side by side reads two controls that look alike
 *  as one kind of thing; that is the whole claim, and a label cannot opt out of it.
 *
 *  Deliberately NOT `RowAction` and not `Button`, and the reason is measured, not stylistic:
 *  `RowAction`'s default tone is `on-surface-var` on `surface-highest` hover and it carries
 *  `-my-px`, so adopting it would have re-inked and re-sized the shipped Tasks on-ramp
 *  (105×28 on `#/dashboard` at 1440×1000) to pay for a refactor. A slot action belongs to the
 *  slot's own sentence, so it takes the slot's ink (`on-surface-low`) — the same relationship
 *  `RowAction` has to a row. It clears SC 2.5.8 at 28px without `min-h-6`; `RowAction` needs
 *  that floor because a row action is 22px painted.
 *
 *  `SlotEmptyState` already stops propagation around `action`, so this never double-fires
 *  inside a clickable parent. */
export function SlotAction({ icon: Icon, children, onClick }: { icon: LucideIcon; children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-xs rounded-pill px-m py-xs text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface"
      data-type="label-m"
    >
      <Icon size={13} /> {children}
    </button>
  )
}

/** A row in a widget list — a tappable surface with spring press feedback + a
 *  hover lift, matching the app's pressable idiom. Optional trailing actions. */
export function WidgetRow({
  onClick, label, children, actions, className,
}: {
  children: ReactNode
  actions?: ReactNode
  className?: string
} & (
  // 🔑 A CLICKABLE ROW CANNOT EXIST WITHOUT A NAME, and the type is what guarantees it rather
  // than a convention a future widget forgets. `label` is what the row IS — the same subject its
  // own actions announce, so "Reply: X" and the row that opens X agree.
  | { onClick: () => void; label: string }
  | { onClick?: undefined; label?: never }
)) {
  return (
    <motion.div
      layout
      transition={spring.spatialDefault}
      whileHover={onClick ? { y: -1 } : undefined}
      // 🔴 OPENING A WIDGET ROW WAS POINTER-ONLY, on the app's FIRST screen. Measured on
      // `#/dashboard` at 1440×1000: **20** clickable rows across four widgets (Action Center,
      // Tasks, Schedule, Pinned Artifacts), `tabindex` **null** and `role` **null** on every one,
      // and **0 of 80** Tab presses ever landed on a row. The row's ACTION pills are reachable, so
      // a keyboard user could reply to or dismiss an item but never open it — and axe reported
      // **0 blocking findings**, because a div with an onclick and no role is invisible to every
      // rule. WCAG 2.1.1. Same shape and same fix as the tasks list (cycle 159) and the
      // notification rows (cycle 164), applied once here because four widgets share this row.
      tabIndex={onClick ? -1 : undefined}
      className={cx(
        'flex items-center gap-s rounded-lg bg-surface-low px-m py-s',
        onClick && 'relative cursor-pointer transition-colors hover:bg-surface-high',
        onClick && 'has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary',
        className,
      )}
      onClick={onClick}
    >
      {onClick && <RowHitTarget label={label} />}
      <div className="min-w-0 flex-1">{children}</div>
      {actions && <div className="flex shrink-0 items-center gap-xs" onClick={(e) => e.stopPropagation()}>{actions}</div>}
    </motion.div>
  )
}

/** A small pill button for inline row actions (approve/dismiss/complete). Tone
 *  drives the accent; ghost by default. */
export function RowAction({
  onClick, children, tone = 'default', title, ariaLabel,
}: {
  onClick: () => void
  children: ReactNode
  tone?: 'default' | 'primary' | 'ok' | 'danger'
  title?: string
  /** The accessible name, composed with the ROW'S SUBJECT — "Reply: Skill: refine-a-skill".
   *
   *  Required for any action rendered inside a per-row `.map`, because `title` alone names the verb
   *  and every row repeats it. Measured on `#/dashboard`: **8× "Reply", 8× "Dismiss", 6× "Mark
   *  complete"**, each acting on a different item, so a screen-reader user listing the controls hears
   *  the same three words over and over with nothing to choose between them (WCAG 4.1.2). Note the
   *  visible text stays the verb — on screen the subject is the row you are looking at.
   *
   *  The kit already composes names this way elsewhere: `ui/Toaster` (`Dismiss: ${message}`),
   *  `ui/forms` (`Remove ${value}`), `ui/WidthPill` (`Content width: ${label}`), and FileTree /
   *  AppsSection (`Actions for ${name}`). Omit it only for a SINGLETON action (one per widget, no
   *  row to disambiguate). */
  ariaLabel?: string
}) {
  const toneCls = {
    default: 'text-on-surface-var hover:bg-surface-highest hover:text-on-surface',
    // 🔴 THE `primary` TONE WAS THE ONE THAT COULD NOT CLEAR AA. A row action is 15px, and these rows
    // paint `bg-surface-low` (#f4f6f9 in light) — not white. Measured on the rendered row: `text-primary`
    // is **4.46:1**, and computed across the curated set on that ground it fails in **6 of 12** schemes
    // (4.46-4.49) while `primary-emphasis` clears every one (worst 4.92, dark worst 8.38). Every sibling
    // tone here already passes on this ground (5.59-10.11) — `--color-primary` is the token tuned for
    // brand presence, which is exactly why the emphasis shade exists. Fourth ground for the pairing
    // cycles 146/147/155 established.
    primary: 'text-primary-emphasis hover:bg-primary-container/40',
    ok: 'text-ok hover:bg-ok/15',
    danger: 'text-danger hover:bg-danger/15',
  }[tone]
  return (
    <motion.button
      type="button"
      title={title}
      aria-label={ariaLabel}
      whileTap={{ scale: 0.92 }}
      transition={spring.spatialFast}
      onClick={onClick}
      // 24px HIT BOX, 22px painted. Measured on `#/dashboard`: `px-m py-xs` rendered **38×22**, and
      // sibling actions sit `gap-xs` (4px) apart — so SC 2.5.8's undersized-target exception cannot
      // rescue it either, the 24px circles overlap. `min-h-6` raises the box and `-my-px` returns the
      // 2px to the layout, so the row keeps its height: the fix is the hit box, not the design. Same
      // resolution as the `sm` Toggle's 36×20 → 36×24.
      className={cx('inline-flex min-h-6 -my-px items-center gap-xs rounded-pill px-m py-xs transition-colors', toneCls)}
      data-type="label-m"
    >
      {children}
    </motion.button>
  )
}

/** A tiny status dot, colored by a CSS var. */
export function StatusDot({ color, pulse }: { color: string; pulse?: boolean }) {
  return (
    <span className="relative inline-flex shrink-0" style={{ width: 8, height: 8 }}>
      {pulse && <span className="status-pulse absolute inset-0 rounded-pill" style={{ background: color }} />}
      <span className="relative inline-block rounded-pill" style={{ width: 8, height: 8, background: color }} />
    </span>
  )
}
