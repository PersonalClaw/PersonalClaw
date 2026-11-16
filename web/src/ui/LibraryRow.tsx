import { Fragment, useId, type ReactNode } from 'react'
import { LoaderCircle, MoreHorizontal, Pin } from 'lucide-react'
import type { LibraryItem } from '../lib/api'
import { useIsMobile } from '../app/useIsMobile'
import { formatIcon, metaParts, phoneMetaParts, rowName, stateMarkOf, titleOf, type LibraryContext, type MetaPart } from '../pages/library/libraryMeta'
import { RowHitTarget, SELECTED_ROW_OUTLINE } from './RowHitTarget'
import { MarkdownLine } from './MarkdownLine'
import { ProjectChip } from './ProjectChip'
import { ProjectMark } from './ProjectMark'
import { ContextMenu, type ContextMenuItem } from './motion'
import { MenuRow, Popover } from './Popover'
import { SquareIconButton } from './SquareIconButton'
import { TOUCH_TARGET } from './Toggle'
import { SignedImg } from './SignedMedia'

/** One thing in the library, as a row: one neutral icon or a thumbnail, the title in
 *  one line, the meta line he saw ("PDF · You · Homelab · 38 pages · Still being read") and, on a
 *  desktop, a line of its text (a search's passage, with the match marked). The row's hit target is
 *  `RowHitTarget`, so the project chip and the ⋯ are not nested in the row's button; its name starts
 *  with the title, then the meta. The state mark is never cut: the size gives way first, then the
 *  age.
 *
 *  From 320 to 768 px the meta is the phone's short form (`phoneMetaParts`), the project is plain
 *  text, the row is at least 56 px, and its ⋯ is always drawn; a long press opens the same menu,
 *  both as a sheet with `phoneSheet`. On a desktop the menu is the row's right click or Shift+F10,
 *  and the preview's More.
 *
 *  **Compact** (the Projects page's About › Knowledge): the icon, the title and "Added by <name>",
 *  or "From <project>" for an item shared from another project, alone (an item of the page's own
 *  project says neither), in a box of at least 44 px from 320 to 768 px. */
export function LibraryRow({ item, ctx, group = '', selected = false, onOpen, onFilterProject, menu = [], checked, onCheck, compact = false, phoneSheet = false, now }: {
  /** The row, as `GET /api/library` answers it. */
  item: LibraryItem
  /** What the page knows beside the rows (`libraryMeta.LibraryContext`): an intent's findings, a
   *  report's schedule, the project the list is filtered to. */
  ctx?: LibraryContext
  /** The group it is listed under, whose words a phone row's age leaves out ("Yesterday"). */
  group?: string
  /** The row whose preview is open: `aria-current`, and the forced-colours outline. */
  selected?: boolean
  /** The row's own press: the page previews it, or opens it on a phone. */
  onOpen: () => void
  /** The project chip's filter form: narrows the list to the project. Without it the chip opens
   *  the project. */
  onFilterProject?: (id: string) => void
  /** The row's menu: its ⋯ on a phone, its long press, its right click and Shift+F10. */
  menu?: ContextMenuItem[]
  /** Bulk mode's check box: checked or not; without it the row draws none. */
  checked?: boolean
  onCheck?: (checked: boolean) => void
  /** The Projects page's form: icon, title, and who it is from (above). */
  compact?: boolean
  /** Its ⋯ and long press open as a phone sheet from 320 to 768 px. */
  phoneSheet?: boolean
  /** The clock it reads, for tests. */
  now?: number
}) {
  const checkId = useId()
  const phone = useIsMobile()
  const title = titleOf(item)
  const parts: MetaPart[] = compact
    ? compactParts(item, ctx)
    : phone ? phoneMetaParts(item, ctx, group, now) : metaParts(item, ctx, now)
  const name = [rowName(item, parts), item.pinned && !compact ? 'Pinned' : ''].filter(Boolean).join(', ')
  const mark = compact ? null: stateMarkOf(item, ctx)
  const Icon = formatIcon(item)
  const bulk = checked !== undefined
  const showText = !compact && !phone
  const text = item.match ? `${item.match.meaning_only ? 'Similar meaning: ' : ''}${item.match.passage}` : item.excerpt

  const row = (
    <div
      tabIndex={-1}
      onClick={() => (bulk ? onCheck?.(!checked) : onOpen())}
      data-library-row={item.ref}
      className={[
        'group relative flex items-start gap-m rounded-lg px-m text-left',
        compact ? 'py-xs max-lg:min-h-11' : 'py-s max-md:min-h-14',
        selected ? 'bg-surface-highest shadow-sm' : 'hover:bg-surface-high',
        'has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary',
        SELECTED_ROW_OUTLINE,
      ].join(' ')}
    >
      <RowHitTarget label={name} selected={selected} />
      {bulk && (
        <input type="checkbox" id={checkId} checked={checked} aria-label={`Select ${title}`} className="mt-3 size-4 shrink-0 accent-primary"
          onChange={(e) => onCheck?.(e.target.checked)} onClick={(e) => e.stopPropagation()} />
      )}
      <Tile item={item} Icon={Icon} small={compact} />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex min-w-0 items-center gap-xs">
          <MarkdownLine text={title} className="min-w-0 truncate text-on-surface" />
          {item.pinned && !compact && !phone && <Pin aria-hidden size={13} className="ml-auto shrink-0 text-on-surface-low" />}
        </div>
        {parts.length > 0 && (
          <div data-type="caption" className="flex min-w-0 items-center overflow-hidden whitespace-nowrap text-on-surface-low">
            {item.pinned && phone && !compact && <Pin aria-hidden size={11} className="mr-1 shrink-0" />}
            {parts.map((p, i) => (
              <Fragment key={`${p.part}-${i}`}>
                {i > 0 && <span aria-hidden className={`px-1 ${GIVES_WAY[p.part] ?? 'shrink-0'}`}>·</span>}
                <Part part={p} item={item} ctx={ctx} phone={phone} mark={p.part === 'state' ? mark: null} onFilterProject={onFilterProject} />
              </Fragment>
            ))}
          </div>
        )}
        {showText && text && (
          <MarkdownLine text={text} hits={!!item.match} className="min-w-0 truncate text-on-surface-var" tooltip={false} />
        )}
      </div>
      {phone && !compact && menu.length > 0 && (
        <RowMenu title={title} items={menu} phoneSheet={phoneSheet} />
      )}
    </div>
  )
  if (!menu.length || compact) return row
  return <ContextMenu items={menu} phoneSheet={phoneSheet} name={title}>{row}</ContextMenu>
}

/** Which part gives way first when the line is too long: the size, then the age; the state mark
 *  never. */
const GIVES_WAY: Partial<Record<MetaPart['part'], string>> = { size: 'min-w-0 shrink-[3] truncate', age: 'min-w-0 shrink-[2] truncate', fact: 'min-w-0 shrink-[2] truncate' }

function Part({ part, item, phone, mark, onFilterProject }: {
  part: MetaPart; item: LibraryItem; ctx?: LibraryContext; phone: boolean
  mark: ReturnType<typeof stateMarkOf>; onFilterProject?: (id: string) => void
}): ReactNode {
  if (part.part === 'project' && item.project) {
    if (phone) {
      return (
        <span className="inline-flex min-w-0 shrink items-center gap-1">
          <ProjectMark name={item.project.name} size="sm" />
          <span className="min-w-0 truncate">{item.project.name}</span>
        </span>
      )
    }
    return <ProjectChip id={item.project.id} name={item.project.name} inControl onFilter={onFilterProject} />
  }
  if (part.part === 'state' && mark) {
    return (
      <span className={`inline-flex shrink-0 items-center gap-1 ${mark.tone === 'warn' ? 'text-warn' : 'text-on-surface-var'}`} style={{ fontWeight: 540 }}>
        {mark.tone === 'progress' && <LoaderCircle aria-hidden size={11} className="shrink-0" />}
        {part.text}
      </span>
    )
  }
  return <span className={GIVES_WAY[part.part] ?? 'shrink-0'}>{part.text}</span>
}

/** The kind's neutral tile, or the item's thumbnail. */
function Tile({ item, Icon, small }: { item: LibraryItem; Icon: ReturnType<typeof formatIcon>; small: boolean }) {
  const size = small ? 'size-7' : 'size-9'
  if (item.thumb) {
    return <SignedImg src={item.thumb} alt="" className={`${size} shrink-0 rounded-md object-cover`} />
  }
  return (
    <span aria-hidden className={`${size} inline-flex shrink-0 items-center justify-center rounded-md bg-surface-high text-on-surface-var`}>
      <Icon size={small ? 14: 16} />
    </span>
  )
}

/** The compact form's one line: who it is from, as the Projects page names it. A teammate's says
 *  who added it, an item shared from another project names that project, and the page's own
 *  project's (`ctx.project`) says nothing. */
function compactParts(item: LibraryItem, ctx?: LibraryContext): MetaPart[] {
  if (item.contributor) return [{ part: 'maker', text: `Added by ${item.contributor}` }]
  if (item.project && item.project.id !== ctx?.project) return [{ part: 'maker', text: `From ${item.project.name}` }]
  return []
}

/** A phone row's ⋯: the same items as its long press, always drawn, a sheet with `phoneSheet`. */
function RowMenu({ title, items, phoneSheet }: { title: string; items: ContextMenuItem[]; phoneSheet: boolean }) {
  return (
    <Popover portal placement="bottom" align="right" width={260} phoneSheet={phoneSheet}
      trigger={(open, toggle) => (
        <SquareIconButton icon={MoreHorizontal} label={`More for ${title}`} title="More" ariaExpanded={open}
          onClick={(e) => { e.stopPropagation(); toggle() }} className={`shrink-0 ${TOUCH_TARGET}`} />
      )}>
      {(close) => (
        <div className="flex flex-col gap-0.5">
          {items.map((it) => (
            <div key={it.label} className={it.danger && !it.disabled ? '[&_span]:!text-danger' : undefined}>
              <MenuRow icon={it.icon} label={it.label} hint={it.hint} name={it.name} disabled={it.disabled}
                onClick={() => { if (it.disabled) return; it.onSelect(); close() }} />
            </div>
          ))}
        </div>
      )}
    </Popover>
  )
}
