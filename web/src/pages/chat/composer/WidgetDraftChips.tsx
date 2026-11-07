/** Where a widget's message stands in the composer, said where the owner types: the draft a
 *  widget put there ("from “X”", with an × that takes it out), and a widget's message waiting
 *  for the owner while they use the composer ("“X” suggests a message · Add / Dismiss"). The
 *  region is always mounted and polite, so a screen reader hears each as it comes. */
import { X } from 'lucide-react'
import { Button } from '../../../ui/Button'
import { IconButton } from '../../../ui/IconButton'
import type { WidgetDraft } from './useChatComposer'

/** The frame by its title, else "a widget" ("A widget" to start a sentence). */
function named(w: WidgetDraft, start = false): string {
  return w.frame ? `“${w.frame}”` : start ? 'A widget' : 'a widget'
}

export function WidgetDraftChips({ draft, suggestion, onClear, onAdd, onDismiss }: {
  /** The widget's message in the draft, if the draft is one. */
  draft: WidgetDraft | null
  /** A widget's message waiting to be added. */
  suggestion: WidgetDraft | null
  onClear: () => void
  onAdd: () => void
  onDismiss: () => void
}) {
  return (
    <div aria-live="polite" data-testid="widget-draft-chips" className="flex w-full flex-wrap items-center gap-xs">
      {draft && (
        <span data-testid="widget-draft-from" data-type="caption"
          className="inline-flex items-center gap-xs rounded-pill bg-surface-high pl-s text-on-surface-var">
          {draft.mixed && !draft.text
            ? 'A widget\u2019s message was taken out. What is left can\u2019t be told apart from it, so none of its links count as yours.'
            : draft.quoted || draft.mixed
            ? 'Holds a widget\u2019s words: none of its links count as yours.'
            : draft.replaced
              ? `${named(draft, true)} replaced its earlier message. It is sent only if you send it.`
              : `From ${named(draft)}. It is sent only if you send it.`}
          {/* A mixed draft has no part the × could take out: deleting all of it is the way out. */}
          {!draft.mixed && (
            <IconButton icon={X} label={draft.quoted ? 'Take the widget\u2019s words out' : `Take out the message ${named(draft)} put here`} size={28} iconSize={14} onClick={onClear} />
          )}
        </span>
      )}
      {suggestion && (
        <span data-testid="widget-suggestion" data-type="caption"
          className="inline-flex items-center gap-xs rounded-pill bg-surface-high pl-s text-on-surface-var">
          {named(suggestion, true)} suggests a message
          <Button variant="ghost" size="xs" onClick={onAdd}>Add</Button>
          <Button variant="ghost" size="xs" onClick={onDismiss}>Dismiss</Button>
        </span>
      )}
    </div>
  )
}
