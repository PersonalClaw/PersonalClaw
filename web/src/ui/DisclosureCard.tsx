import { useId, useState, type ReactNode } from 'react'
import { ChevronRight, type LucideIcon } from 'lucide-react'
import { fvs } from '../design/fontWeight'
import { accentChip } from '../design/accent'

/** A collapsible settings card: an always-visible header summarising a binding, and a body that
 *  discloses the controls that change it.
 *
 *  This existed TWICE, byte-identical, as the `UseCaseRow` shell in `settings/ModelsPanel` and
 *  `settings/SearchPanel` — same wrapper, same disclosure button, same chevron rotation, same accent
 *  icon chip, same label/subtitle stack, same count pill, same bordered body. Only the *content* of
 *  the subtitle, the count and the body ever differed, and both call sites kept an `open` flag that
 *  nothing outside the shell read. So the state lives here now and neither caller declares it.
 *
 *  🔑 THE DUPLICATION WAS NOT COSMETIC — IT DUPLICATED A DEFECT. Both headers had a clipped focus
 *  ring (the header fills an `overflow-hidden rounded-lg` card, so the global rail's outward
 *  `outline-offset: 2px` drew entirely outside the clip and nothing painted), and both needed the
 *  same `focus-visible:-outline-offset-2` fix, applied twice in the same PR. One copy of a component
 *  is one place for a fix like that to land.
 *
 *  The header is a real `<button>` wired to the body with `aria-expanded` + `aria-controls`, which
 *  neither copy had: `aria-expanded` alone says "this thing expands" without saying WHAT, so a screen
 *  reader could not jump from the control to the region it governs.
 *
 *  Open state is deliberately INTERNAL and uncontrolled. Both call sites want exactly that, and a
 *  `open`/`onOpenChange` pair added on speculation is API nobody is asking for yet. */
export function DisclosureCard({ icon: Icon, label, subtitle, active, count, countLabel = 'available', children }: {
  icon: LucideIcon
  /** The binding's name — the header's primary line. */
  label: string
  /** The one-line summary of what is currently bound. A node, not a string, because both call sites
   *  render an italic "nothing is bound" fallback rather than empty text. */
  subtitle: ReactNode
  /** Whether something IS bound. Drives the icon chip's accent — `accentChip` when bound, a quiet
   *  surface fill when not, which is what makes a scan of the list show the gaps. */
  active: boolean
  /** How many options this card can offer. The pill is suppressed at 0 rather than reading "0
   *  available", because a card with nothing to offer says so in its body instead. */
  count?: number
  /** Noun for the count pill. `available` suits both current callers. */
  countLabel?: string
  /** The disclosed body. Rendered in a flex column with the card's own gap and padding, so a caller
   *  passes plain children and never re-states the layout. */
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const bodyId = `disclosure-card-${useId()}`

  return (
    <div className="mb-2 overflow-hidden rounded-lg bg-surface-container">
      {/* `focus-visible:-outline-offset-2`: this header fills the `overflow-hidden rounded-lg` card
          above, so an outward ring is clipped on ALL FOUR sides — 4px lost per side, the ring's whole
          reach, i.e. no visible focus indicator (WCAG 2.4.7). The negative offset draws it inside. */}
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls={bodyId}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-high focus-visible:-outline-offset-2">
        <ChevronRight size={14} className="shrink-0 text-on-surface-low transition-transform" style={{ transform: open ? 'rotate(90deg)' : 'none', color: open ? 'var(--color-primary)' : undefined }} />
        <span className="grid size-7 shrink-0 place-items-center rounded-md"
          style={active ? accentChip : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
          <Icon size={14} />
        </span>
        <div className="min-w-0 flex-1">
          <div data-type="label-s" className="text-on-surface" style={fvs(500)}>{label}</div>
          <div data-type="caption" className="mt-0.5 text-on-surface-low">{subtitle}</div>
        </div>
        {count !== undefined && count > 0 && (
          <span data-type="caption" className="shrink-0 rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low tabular-nums">{count} {countLabel}</span>
        )}
      </button>

      {open && (
        <div id={bodyId} className="flex flex-col gap-3 border-t border-outline-variant/30 px-4 pb-4 pt-3">
          {children}
        </div>
      )}
    </div>
  )
}
