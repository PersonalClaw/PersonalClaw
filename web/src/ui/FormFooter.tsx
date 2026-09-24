import type { ReactNode } from 'react'
import { cx } from './cx'

/** Sticky edit-mode action bar — the right-aligned Cancel/Save row pinned to the
 *  bottom of a detail pane's edit form. Bleeds to the pane edges (`-mx-l`), sits on
 *  a translucent surface with a hairline top border, and stays visible while the form
 *  scrolls. Every *Detail edit form (Task, Schedule, Lifecycle, Workflow, Agent,
 *  Prompt, Snippet) rendered this exact wrapper inline; this is the single source.
 *  Pure chrome — the buttons and their handlers are the caller's `children`. */
export function FormFooter({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx('sticky bottom-0 -mx-l px-l py-3 bg-surface/95 border-t border-outline-variant/40 flex justify-end gap-s', className)}>
      {children}
    </div>
  )
}
