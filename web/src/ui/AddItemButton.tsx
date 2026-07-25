import type { ReactNode } from 'react'
import { cx } from './cx'

/** The quiet "add another row" affordance beneath an editable list (workflow
 *  steps, prompt/snippet variables). A surface-container fill, medium radius, 36px
 *  tall, ink-var label with a leading glyph; hover lifts to surface-high. Deliberately
 *  rectangular + understated (not the pill CTA {@link Button}) so it aligns with the
 *  `rounded-md bg-surface-container` list rows it sits under. Five editors (WorkflowForm
 *  ×2, PromptForm, SnippetForm, PromptEditFields) rendered this exact markup inline;
 *  this is the single source. Children are the leading icon + label. Pass
 *  `className="self-start"` where the button must not stretch in its flex column. */
export function AddItemButton({ children, onClick, className }: {
  children: ReactNode
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx(
        'inline-flex items-center gap-1.5 rounded-md bg-surface-container px-m h-9',
        'text-on-surface-var text-[0.8125rem] hover:bg-surface-high transition-colors',
        className,
      )}
    >
      {children}
    </button>
  )
}
