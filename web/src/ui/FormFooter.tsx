import { useEffect, useRef, type ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'
import { cx } from './cx'

/** Sticky edit-mode action bar — the right-aligned Cancel/Save row pinned to the
 *  bottom of a detail pane's edit form. Bleeds to the pane edges (`-mx-l`), sits on
 *  a translucent surface with a hairline top border, and stays visible while the form
 *  scrolls. Every *Detail edit form (Task, Schedule, Lifecycle, Workflow, Agent,
 *  Prompt, Snippet) rendered this exact wrapper inline; this is the single source.
 *  The buttons and their handlers are the caller's `children`.
 *
 *  🔴 A REFUSED SAVE LOOKED LIKE NOTHING HAPPENED, and `error` is why this bar owns the
 *  message now. Every adopter rendered its save error as a `FieldError` just ABOVE this bar —
 *  i.e. at the END of the scrolling form. Measured on the task panel (1440×900): Save at y=828,
 *  the refusal "cannot complete: unfinished exit criteria — Copy reviewed by Sam" at y=1664,
 *  803px below the fold, and focus dropped to `<body>` (the Save button is natively disabled
 *  while it saves). `getByRole('alert')` found it; no user could. The message now renders IN
 *  the sticky bar, directly above the button that produced it — so it is on screen whatever the
 *  form's scroll position — and takes focus, so a keyboard user lands on it instead of on
 *  nothing. It is still an `alert`, so it is announced. */
export function FormFooter({ children, className, error }: {
  children: ReactNode
  className?: string
  /** The last action's failure, shown beside the actions. Omit or `''` for none. A STRING,
   *  not a node: focus moves when the message changes, and a node is a new object on every
   *  render — it would pull focus back to the message on every keystroke in the form. */
  error?: string
}) {
  const errRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => {
    if (!error) return
    // `preventScroll`: the bar is sticky, so the message is already on screen — scrolling the
    // form to "reveal" it would only move the field the user was looking at.
    errRef.current?.focus({ preventScroll: true })
  }, [error])
  return (
    <div data-form-footer className={cx('sticky bottom-0 -mx-l px-l py-3 bg-surface/95 border-t border-outline-variant/40 flex flex-wrap items-center justify-end gap-s', className)}>
      {error ? (
        <p ref={errRef} role="alert" tabIndex={-1} data-type="body-s"
          className="flex basis-full items-start gap-1.5 text-danger outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-danger rounded-sm">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
          <span className="min-w-0">{error}</span>
        </p>
      ) : null}
      {children}
    </div>
  )
}
