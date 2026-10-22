import { useState, type ReactNode } from 'react'
import { ListChecks } from 'lucide-react'
import { Button } from '../../ui/Button'
import { PaneHost } from '../../ui/PaneHost'

// ── A plan in review, beside the planning that made it ─────────────────────────────────────────
//
// A goal loop or code project whose plan has landed keeps its planning walkthrough as the work, and
// its Plan Review opens beside it in the shared pane host (#3821), where it used to replace the
// section. The pane's own controls only show and hide it: closing it leaves the walkthrough, whose
// header offers the review back. Launching, and everything else the review decides, stays inside
// the review.

/** Plan Review is a form with a step bar, so its pane starts wider than a diff's. */
const SIZE = { def: 640, min: 400, max: 1100 }

export function PlanBesideItsPlanning({ surface, review, planning }: {
  /** The pane host's surface, which keeps the pane's width and dock (`pane-host-<surface>-…`). */
  surface: string
  /** The review's body once the plan has landed, else null (still planning). */
  review: ReactNode | null
  /** The walkthrough, given whether the plan is in review and the control that reopens it. */
  planning: (planned: boolean, actions: ReactNode) => ReactNode
}) {
  const [open, setOpen] = useState(true)
  const reopen = review && !open
    ? <Button variant="tonal" size="sm" onClick={() => setOpen(true)}><ListChecks size={14} /> Review the plan</Button>
    : null
  // The host grows as a flex child (`flex-1`); a section is drawn in a plain box, so this one gives
  // it the page's height, which the walkthrough and the review each scroll inside.
  return (
    <div className="flex h-full min-h-0">
      <PaneHost surface={surface} size={SIZE}
        pane={review && open ? {
          id: 'plan', title: 'Review the plan', label: 'plan review', icon: ListChecks, onClose: () => setOpen(false), body: review,
        } : null}>
        {planning(!!review, reopen)}
      </PaneHost>
    </div>
  )
}
