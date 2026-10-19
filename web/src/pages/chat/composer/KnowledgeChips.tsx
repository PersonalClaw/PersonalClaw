import { BookText, X } from 'lucide-react'
import { IconButton } from '../../../ui/IconButton'

/** Removable chips for @-mentioned knowledge-library items, shown ABOVE the
 *  composer. Each pairs with an inline `@name` token in the prompt; the item's
 *  content is inlined by the backend at send. ✕ removes the reference. */
export function KnowledgeChips({ items, onRemove }: { items: { id: string; name: string }[]; onRemove: (id: string) => void }) {
  if (!items.length) return null
  return (
    <div className="mb-2 flex flex-wrap gap-2">
      {items.map((k) => (
        <div key={k.id} className="flex items-center gap-1.5 rounded-lg border border-primary/40 px-2.5 py-1.5 text-[0.8125rem]"
          style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>
          <BookText size={13} className="shrink-0 text-primary" />
          <span className="min-w-0 truncate text-on-surface" title={k.name}>{k.name}</span>
          <IconButton icon={X} label="Remove knowledge reference" onClick={() => onRemove(k.id)} size={20} iconSize={13}
            tone="danger" className="shrink-0" />
        </div>
      ))}
    </div>
  )
}
