import { type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Search, X } from 'lucide-react'
import { Segmented, type SegOption } from './Segmented'
import { bounce } from '../design/motion'

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
          <div className="group relative min-w-[12rem] flex-1">
            {/* magnifier brightens + nudges toward the text on focus (focus-within
                works regardless of DOM order) so the field visibly "wakes" when the
                user starts a search */}
            <Search size={15} className="pointer-events-none absolute left-3 top-1/2 z-10 -translate-y-1/2 text-on-surface-low transition-all duration-200 group-focus-within:translate-x-0.5 group-focus-within:text-primary" />
            <input value={search.value} onChange={(e) => search.onChange(e.target.value)}
              placeholder={search.placeholder ?? 'Search'} aria-label={search.label ?? search.placeholder ?? 'Search'}
              name={`search-${(search.label ?? search.placeholder ?? 'list').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')}`}
              id={`search-${(search.label ?? search.placeholder ?? 'list').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')}`}
              type="search" autoFocus={search.autoFocus}
              onKeyDown={(e) => { if (e.key === 'Escape' && search.value) { e.preventDefault(); e.stopPropagation(); search.onChange('') } }}
              className="h-10 w-full rounded-pill bg-surface-high pl-9 pr-9 text-[0.9375rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
            {/* clear-X springs in/out (a small pop) instead of appearing/vanishing
                on the first/last keystroke — a light touch on a restrained control */}
            <AnimatePresence>
              {search.value && (
                <motion.button type="button" onClick={() => search.onChange('')} aria-label="Clear search"
                  initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0, opacity: 0 }}
                  transition={bounce.subtle} whileTap={{ scale: 0.88 }}
                  className="absolute right-2.5 top-1/2 inline-flex size-6 -translate-y-1/2 items-center justify-center rounded-full text-on-surface-low hover:bg-surface-highest hover:text-on-surface">
                  <X size={14} />
                </motion.button>
              )}
            </AnimatePresence>
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
