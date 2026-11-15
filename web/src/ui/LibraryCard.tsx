import type { Artifact, LibraryItem } from '../lib/api'
import { ArtifactFace } from '../pages/artifacts/ArtifactCard'
import { dayAge, formatIcon, metaParts, refId, refKind, rowName, titleOf, type LibraryContext } from '../pages/library/libraryMeta'
import { MarkdownLine } from './MarkdownLine'
import { SignedImg } from './SignedMedia'
import { TileButton } from './TileButton'

/** One thing in the library as a card: its face (an artifact's live page, at
 *  most `IFRAME_CAP` live at once, or its image; else its kind's neutral tile with a line of its
 *  text), its title, and its meta line. Picking it opens the same preview a row does. The previewed
 *  card is `selected`: `TileButton`'s `current` (`aria-current`, and the outline forced colours keep).
 *
 *  **The project form** (`form="project"`, the Projects page's Results): the kind's icon, the title and
 *  "From <work> · <when>" (what it came from and the day of its last activity), or the day alone when
 *  it came from nothing still named; no chip, and never a live page. */
export function LibraryCard({ item, ctx, selected = false, onOpen, form = 'library', now }: {
  /** The thing, as `GET /api/library` answers it. */
  item: LibraryItem
  /** What the page knows beside the rows (`libraryMeta.LibraryContext`). */
  ctx?: LibraryContext
  /** The card whose preview is open. */
  selected?: boolean
  /** Picking it: the page previews it. */
  onOpen: () => void
  /** `project`: the Projects page's Results tile (above). */
  form?: 'library' | 'project'
  /** The clock it reads, for tests. */
  now?: number
}) {
  const title = titleOf(item)
  const Icon = formatIcon(item)
  if (form === 'project') {
    const when = dayAge(item.activity_at || item.created_at, now)
    const from = item.from?.title ? `From ${item.from.title}` : ''
    const line = [from, when].filter(Boolean).join(' · ')
    return (
      <TileButton onClick={onOpen} current={selected} ariaLabel={[title, line].filter(Boolean).join(', ')} title={title} className="h-full w-full">
        <div className="flex min-w-0 flex-col gap-xs p-m text-left">
          <span aria-hidden className="inline-flex size-8 items-center justify-center rounded-md bg-surface-high text-on-surface-var"><Icon size={15} /></span>
          <MarkdownLine text={title} className="min-w-0 truncate text-on-surface" />
          {line && <span data-type="caption" className="truncate text-on-surface-low">{line}</span>}
        </div>
      </TileButton>
    )
  }
  const parts = metaParts(item, ctx, now).filter((p) => p.part !== 'project')
  const kind = refKind(item.ref)
  const face = kind === 'artifact'
    ? <ArtifactFace art={{ slug: refId(item.ref), kind: item.format as Artifact['kind'], version: item.version ?? 1, name: title }} tone="var(--color-on-surface-var)" />
    : item.thumb
      ? <SignedImg src={item.thumb} alt="" loading="lazy" className="h-full w-full object-cover" />
      : (
        <div className="flex h-full w-full flex-col gap-s p-m">
          <span aria-hidden className="inline-flex size-8 items-center justify-center rounded-md bg-surface-high text-on-surface-var"><Icon size={15} /></span>
          {item.excerpt && <p data-type="caption" className="line-clamp-4 text-on-surface-var">{item.excerpt}</p>}
        </div>
      )
  return (
    <TileButton onClick={onOpen} current={selected} ariaLabel={rowName(item, parts)} title={title} className="h-full w-full">
      <div className="h-36 w-full shrink-0 overflow-hidden border-b border-outline-variant/30 bg-surface">{face}</div>
      <div className="flex min-w-0 flex-col gap-0.5 px-3 py-2 text-left">
        <MarkdownLine text={title} className="min-w-0 truncate text-on-surface" />
        {parts.length > 0 && (
          <span data-type="caption" className="truncate text-on-surface-low">{parts.map((p) => p.text).join(' · ')}</span>
        )}
      </div>
    </TileButton>
  )
}
