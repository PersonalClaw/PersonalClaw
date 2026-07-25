import { type ReactNode } from 'react'
import { Segmented, type SegOption } from './Segmented'
import { SearchField } from './SearchField'

/** The canonical on-PAGE controls bar for a list section — search + an optional
 *  filter segmented + optional extra controls (sort, chips), pinned just below the
 *  TopBar and centered to the content width. List controls belong here, on the
 *  page, NOT in the header (the header keeps only structural view-switches + the
 *  primary action). Mirrors the chat-history layout so every list page reads the
 *  same. Render via WorkbenchLayout's `controls` slot, or inline above a body. */
export function ListControls({
  search, filter, children,
}: {
  /** Optional search box config — omit for a filter-only bar. */
  search?: { value: string; onChange: (v: string) => void; placeholder?: string; label?: string; autoFocus?: boolean }
  /** Optional single-select filter strip (status / kind / scope — NOT a view switch). */
  filter?: { value: string; onChange: (v: string) => void; options: SegOption[]; ariaLabel?: string }
  /** Extra controls (sort dropdown, filter chips) rendered after search + filter. */
  children?: ReactNode
}) {
  if (!search && !filter && !children) return null
  return (
    <div className="shrink-0 border-b border-outline-variant/30">
      <div className="mx-auto flex w-full flex-wrap items-center gap-s px-l py-m" style={{ maxWidth: 'var(--content-width)' }}>
        {search && (
          <div className="min-w-[12rem] flex-1">
            <SearchField value={search.value} onChange={search.onChange}
              placeholder={search.placeholder ?? 'Search'} ariaLabel={search.label ?? search.placeholder ?? 'Search'}
              name={`search-${(search.label ?? search.placeholder ?? 'list').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')}`}
              autoFocus={search.autoFocus} />
          </div>
        )}
        {filter && (
          <Segmented ariaLabel={filter.ariaLabel ?? 'Filter'} value={filter.value} onChange={filter.onChange} options={filter.options} />
        )}
        {children}
      </div>
    </div>
  )
}
