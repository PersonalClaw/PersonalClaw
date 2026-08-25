import { Database, Plus, Tag } from 'lucide-react'
import { Button } from '../../ui/Button'
import { FilterRow } from '../../ui/FilterRow'

/** One selectable rail entry. `count` is the number of cards the Store grid will
 *  show when it is pressed — computed from the SAME universe the grid renders, so a
 *  rail entry can never advertise apps the grid cannot produce. */
export interface RailOption {
  key: string
  label: string
  count: number
}

/** The Store's persistent category/source rail (PEP-3).
 *
 *  Two single-select blocks — CATEGORIES (canonical tags) and SOURCES (Built-in +
 *  each registered git/local source) — driving the SAME URL-backed filter state the
 *  narrow-screen `FilterMenu` dropdown drives (`?stag=` / `?ssrc=`). There is one
 *  filter model with two presentations, not two filters: on a wide viewport this rail
 *  owns the two dimensions and the dropdown drops them; below the shell's
 *  `useIsMobile` threshold the rail is gone and the dropdown carries them again. The
 *  rows are literally `ui/FilterRow`, the same component the dropdown renders, so
 *  "one control at two widths" is structural rather than a resemblance to maintain.
 *
 *  🔑 Selection state is `aria-pressed` on a real button element, not a class. A tinted
 *  background is invisible to every non-visual reader, and single-select-by-tint is
 *  what a "make it look selected" fix reaches for first. The label is the button's
 *  VISIBLE text plus its count — no visually-hidden span inside the button, which
 *  would pollute its accessible name with text the sighted user cannot see.
 *
 *  Keyboard: every entry is a native button, so each is its own tab stop and Enter or
 *  Space activates it. There is deliberately NO arrow-key cursor. An arrow-driven list
 *  is a different APG pattern — it implies a roving tabindex, i.e. ONE tab stop for the
 *  whole group and `aria-selected` on options inside a `role="listbox"`. Bolting arrows
 *  onto independently-tabbable toggle buttons produces neither pattern: assistive tech is
 *  told "seven buttons" and then a cursor moves focus for reasons nothing announced.
 *  `ui/popupItemRoles.test.tsx` catches exactly that shape (a cursor over a mapped list
 *  with no container role), and it caught the first version of this file.
 */
export function StoreSideRail({
  categories, category, onCategory, categoryTotal,
  sources, source, onSource, sourceTotal,
  onAddSource,
}: {
  categories: RailOption[]
  category: string
  onCategory: (key: string) => void
  categoryTotal: number
  sources: RailOption[]
  source: string
  onSource: (key: string) => void
  sourceTotal: number
  /** Opens the existing Manage Sources panel — the rail adds an entrance to that
   *  flow, it does not grow a second source editor. */
  onAddSource: () => void
}) {
  return (
    <nav aria-label="Categories and sources" className="flex w-[13rem] shrink-0 flex-col gap-l">
      <StoreRailBlock title="Categories" allLabel="All apps" allCount={categoryTotal} icon={Tag}
        options={categories} value={category} onChange={onCategory} />
      <StoreRailBlock title="Sources" allLabel="All sources" allCount={sourceTotal} icon={Database}
        options={sources} value={source} onChange={onSource} />
      <div className="px-1">
        <Button variant="ghost" size="sm" onClick={onAddSource}>
          <Plus size={14} /> Add source
        </Button>
      </div>
    </nav>
  )
}

/** One titled block of pressable entries. Named `StoreRailBlock` rather than
 *  `RailBlock` so it cannot be confused with — or shadow — the shell's `ui/NavRail`
 *  vocabulary (`design/primitiveShadowing.test.ts`'s rule). */
function StoreRailBlock({ title, allLabel, allCount, icon, options, value, onChange }: {
  title: string
  allLabel: string
  allCount: number
  icon: typeof Tag
  options: RailOption[]
  value: string
  onChange: (key: string) => void
}) {
  // The reset entry leads each block, exactly as "All …" leads each dropdown section.
  const rows: RailOption[] = [{ key: 'all', label: allLabel, count: allCount }, ...options]
  return (
    <div className="flex flex-col gap-0.5">
      {/* h2: the page's h1 is "Apps" (PageTitle), so a block heading sits one level under
          it — an h3 here would skip a level. */}
      <h2 className="px-2 pb-0.5 text-on-surface-low text-[0.75rem] uppercase tracking-wide">{title}</h2>
      {rows.map((o) => (
        <FilterRow key={o.key} label={o.label} count={o.count} icon={icon}
          selected={value === o.key} pressed={value === o.key}
          indicatorId={`store-rail-${title}`} onClick={() => onChange(o.key)} />
      ))}
    </div>
  )
}
