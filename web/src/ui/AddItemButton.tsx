import type { ReactNode } from 'react'
import { cx } from './cx'

/** The quiet "add another row" affordance beneath an editable list (workflow
 *  steps, prompt/snippet variables). A surface-container fill, medium radius, 36px
 *  tall, ink-var label with a leading glyph; hover lifts to surface-high. Deliberately
 *  rectangular + understated (not the pill CTA {@link Button}) so it aligns with the
 *  `rounded-md bg-surface-container` list rows it sits under. Five editors (WorkflowForm
 *  ×2, PromptForm, SnippetForm, PromptEditFields) rendered this exact markup inline;
 *  this is the single source. Children are the leading icon + label. Pass
 *  `className="self-start"` where the button must not stretch in its flex column.
 *
 *  A press shows by colour, at once (the `active:` fill is the hover's), with no
 *  scale. This button APPENDS A ROW, so the press acknowledgement is the only
 *  feedback between the click and the new row arriving, and the tint gives it on a
 *  touch screen too, where no hover precedes the tap. */
export function AddItemButton({ children, onClick, className }: {
  children: ReactNode
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-type="body-s"
      className={cx(
        'inline-flex items-center gap-xs rounded-md bg-surface-container px-m h-9',
        'text-on-surface-var hover:bg-surface-high active:bg-surface-high transition-colors',
        className,
      )}
    >
      {children}
    </button>
  )
}
