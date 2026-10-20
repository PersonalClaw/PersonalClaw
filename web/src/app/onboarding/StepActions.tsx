import { createContext, useContext } from 'react'
import { createPortal } from 'react-dom'
import { ArrowRight } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Button } from '../../ui/Button'

/** One of a step's own actions: what it says, what it does, and — while it cannot run — why. */
export interface StepAction {
  label: string
  onClick: () => void
  disabled?: boolean
  /** Said while `disabled`, and it keeps the button reachable by keyboard (see `ui/Button`). */
  disabledReason?: string
  loading?: boolean
  /** A leading icon, for a secondary action that is an alternative rather than a skip. */
  icon?: LucideIcon
}

/** Where the active step's actions render. Three values, three places:
 *
 *  - an element — the onboarding shell's navigation bar, for the step on screen;
 *  - `null` — inside the shell but NOT the step on screen (a body still animating shut, or the
 *    bar not mounted yet): nothing, so two steps never offer their buttons at once;
 *  - `undefined`, i.e. no provider — a step rendered on its own, as its own tests render it: in
 *    place, where the step used to draw them. */
export const StepActionsSlot = createContext<HTMLElement | null | undefined>(undefined)

/** A step's forward actions — Continue, or its own equivalent ("Import selected", "Start using"),
 *  and at most one quieter alternative ("Set up later", "Skip this").
 *
 *  🔑 THEY RENDER IN THE SHELL'S BAR, NOT AT THE END OF THE STEP. Each step used to draw its own row
 *  at the bottom of its body, under content that can run a screen and a half, while the flow's Back
 *  and skip sat below THAT as centred links — the owner: "not tucked away at the bottom in an oddly
 *  aligned manner". The bar is one sticky row at the foot of the onboarding shell, the same on
 *  every step; the shell draws Back and the skip-everything door, and the step on screen contributes
 *  these through a portal. A portal rather than lifted state, so each button keeps the step's own
 *  live handlers and state — nothing is copied up and nothing can go stale. */
export function StepActions({ primary, secondary }: { primary?: StepAction; secondary?: StepAction }) {
  const slot = useContext(StepActionsSlot)
  if (slot === null) return null
  const buttons = (
    <>
      {secondary && (
        <Button variant="secondary" size="md" onClick={secondary.onClick} disabled={secondary.disabled}
          disabledReason={secondary.disabledReason} loading={secondary.loading}>
          {secondary.icon && <secondary.icon size={16} aria-hidden="true" />} {secondary.label}
        </Button>
      )}
      {primary && (
        <Button variant="primary" size="md" onClick={primary.onClick} disabled={primary.disabled}
          disabledReason={primary.disabledReason} loading={primary.loading}>
          {primary.label} <ArrowRight size={16} aria-hidden="true" />
        </Button>
      )}
    </>
  )
  if (slot) return createPortal(buttons, slot)
  return <div className="flex flex-wrap items-center gap-m">{buttons}</div>
}
