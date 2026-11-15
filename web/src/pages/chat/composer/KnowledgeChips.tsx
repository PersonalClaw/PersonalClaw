import type { ReactNode } from 'react'
import { BookText, Boxes, X, type LucideIcon } from 'lucide-react'
import { IconButton } from '../../../ui/IconButton'

/** Removable chips for the library items the next message carries, shown ABOVE the composer:
 *  knowledge items (an @-mention, the knowledge picker, a link's `attach`) and artifacts (the
 *  artifact picker, a link's `attach`). An @-mention also leaves an inline `@name` token in the
 *  prompt; the backend inlines each item's content at send. ✕ removes the reference. */
export function KnowledgeChips({ items, onRemove, artifacts = [], onRemoveArtifact }: {
  items: { id: string; name: string }[]
  onRemove: (id: string) => void
  artifacts?: { slug: string; name: string }[]
  onRemoveArtifact?: (slug: string) => void
}) {
  if (!items.length && !artifacts.length) return null
  // Each remove control names its kind in its own literal label, as the danger-tint rail reads it
  // (`ui/destructiveIconTone`).
  return (
    <div className="mb-2 flex flex-wrap gap-2">
      {items.map((k) => (
        <Chip key={`knowledge:${k.id}`} name={k.name} Icon={BookText}>
          <IconButton icon={X} label="Remove knowledge reference" onClick={() => onRemove(k.id)} size={20} iconSize={13}
            tone="danger" className="shrink-0" />
        </Chip>
      ))}
      {artifacts.map((a) => (
        <Chip key={`artifact:${a.slug}`} name={a.name} Icon={Boxes}>
          <IconButton icon={X} label="Remove artifact reference" onClick={() => onRemoveArtifact?.(a.slug)} size={20} iconSize={13}
            tone="danger" className="shrink-0" />
        </Chip>
      ))}
    </div>
  )
}

/** One chip: the item's kind as an icon, its name, and its remove control. */
function Chip({ name, Icon, children }: { name: string; Icon: LucideIcon; children: ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 rounded-lg border border-primary/40 px-2.5 py-1.5 text-[0.8125rem]"
      style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>
      <Icon size={13} className="shrink-0 text-primary" />
      <span className="min-w-0 truncate text-on-surface" title={name}>{name}</span>
      {children}
    </div>
  )
}
