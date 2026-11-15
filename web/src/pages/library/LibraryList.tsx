import { useMemo } from 'react'
import type { LibraryItem } from '../../lib/api'
import { WindowedList } from '../../ui/WindowedList'
import { LibraryRow } from '../../ui/LibraryRow'
import { LibraryCard } from '../../ui/LibraryCard'
import { TextLink } from '../../ui/TextLink'
import type { ContextMenuItem } from '../../ui/motion'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { groupRows, type LibraryContext, type LibraryGroup } from './libraryMeta'

/** One entry of the windowed list: a group's heading, a row, or a group's Show all. */
type Entry =
  | { type: 'heading'; key: string; title: string }
  | { type: 'row'; key: string; item: LibraryItem; group: string; pos: number }
  | { type: 'more'; key: string; group: LibraryGroup }

/** The library's list: its groups, each heading a list item holding an `h2`, and
 *  its rows, windowed, with `aria-posinset` and `aria-setsize` counted over the rows alone. A
 *  group cut at its first rows ends with its Show all. */
export function LibraryList({ rows, sort, searching, open, onShowAll, selected, ctx, onOpen, onFilterProject, menuOf, bulk, checked, onCheck, now }: {
  rows: readonly LibraryItem[]
  sort: string
  searching: boolean
  /** The groups shown whole after their Show all was pressed. */
  open: ReadonlySet<string>
  onShowAll: (title: string) => void
  /** The ref whose preview is open. */
  selected: string
  ctx: LibraryContext
  onOpen: (item: LibraryItem) => void
  onFilterProject: (id: string) => void
  menuOf: (item: LibraryItem) => ContextMenuItem[]
  bulk: boolean
  checked: ReadonlySet<string>
  onCheck: (ref: string, on: boolean) => void
  now?: number
}) {
  const groups = useMemo(() => groupRows(rows, sort, { searching, open, now }), [rows, sort, searching, open, now])
  const entries = useMemo(() => {
    const out: Entry[] = []
    let pos = 0
    for (const g of groups) {
      if (g.title) out.push({ type: 'heading', key: `h:${g.title}`, title: g.title })
      for (const item of g.rows) out.push({ type: 'row', key: item.ref, item, group: g.title, pos: ++pos })
      if (g.more) out.push({ type: 'more', key: `m:${g.title}`, group: g })
    }
    return out
  }, [groups])
  const setsize = entries.filter((e) => e.type === 'row').length
  return (
    <WindowedList
      items={entries}
      rowKey={(e) => e.key}
      rowHeights="variable"
      estimateRowHeight={68}
      noun="items"
      findHint="use the Search your library field above, which searches what each item says."
      revealKey={selected || undefined}
      ariaLabel="Library items"
      positionOf={(e) => (e.type === 'row' ? { posinset: e.pos, setsize } : null)}
      className="flex flex-col"
    >
      {(e) => {
        if (e.type === 'heading') {
          return <h2 data-type="label-m" className="px-m pb-xs pt-m text-on-surface-low">{e.title}</h2>
        }
        if (e.type === 'more') {
          return (
            <div className="px-m py-xs">
              <TextLink size="sm" onClick={() => onShowAll(e.group.title)} className={TOUCH_TARGET}>{e.group.more!.words}</TextLink>
            </div>
          )
        }
        return (
          <LibraryRow item={e.item} ctx={ctx} group={e.group} selected={e.item.ref === selected} onOpen={() => onOpen(e.item)}
            onFilterProject={onFilterProject} menu={menuOf(e.item)} phoneSheet now={now}
            {...(bulk ? { checked: checked.has(e.item.ref), onCheck: (on: boolean) => onCheck(e.item.ref, on) } : {})} />
        )
      }}
    </WindowedList>
  )
}

/** The library's Grid: a card per thing, the previewed one `selected`. */
export function LibraryGrid({ rows, selected, ctx, onOpen, now }: {
  rows: readonly LibraryItem[]
  selected: string
  ctx: LibraryContext
  onOpen: (item: LibraryItem) => void
  now?: number
}) {
  return (
    <ul aria-label="Library items" className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-m px-m pb-l">
      {rows.map((item) => (
        <li key={item.ref}>
          <LibraryCard item={item} ctx={ctx} selected={item.ref === selected} onOpen={() => onOpen(item)} now={now} />
        </li>
      ))}
    </ul>
  )
}
