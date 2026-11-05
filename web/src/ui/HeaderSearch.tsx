import { Search } from 'lucide-react'
import { cx } from './cx'
import { Popover } from './Popover'
import { SearchField } from './SearchField'
import { useHeaderChild, useHeaderProbe } from './HeaderActions'

/** The page's search, as a member of the header's action cluster (`HeaderActions`): every
 *  page-level control lives in the page header's action area, the search that filters the page
 *  among them, so no page draws a search bar under its header.
 *
 *  It sheds with the cluster: a field at the full tier, a narrower one at the text tier, and where
 *  the cluster sheds to icons, a Search button that opens the field in a popover beside it. It is
 *  never pushed into the `…` menu, because a search hidden in a menu is not found, and it keeps the
 *  primary rank so it is among the last controls the row gives up. While it holds a query and shows
 *  as a button, the button carries a dot and names the query, so a searched page never looks
 *  unsearched.
 *
 *  Inside the cluster's measuring probes it draws an empty box of the field's width, not a second
 *  input with the same id. */
export function HeaderSearch({ value, onChange, placeholder = 'Search', label, autoFocus, className, tour }: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  /** The field's accessible name; defaults to its placeholder. */
  label?: string
  autoFocus?: boolean
  /** Added to the shed form's Search button, e.g. the compact widths' 44 px hit box (`TOUCH_TARGET`). */
  className?: string
  /** A product-tour anchor (`data-tour`), on the live field or its button and never on a probe, so
   *  the tour rings what is on screen. */
  tour?: string
}) {
  const name = label ?? placeholder
  const { tier } = useHeaderChild({ priority: 'primary', canIcon: true, neverOverflow: true, menu: { label: name, icon: Search } })
  const probe = useHeaderProbe()
  const width = tier === 'full' ? 'w-64' : 'w-48'
  const fieldName = `search-${name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')}`

  if (tier === 'full' || tier === 'text') {
    if (probe) return <div aria-hidden className={cx('h-10 shrink-0', width)} />
    return (
      <div data-tour={tour} className={cx('shrink-0', width)}>
        {/* `lg`: the row's 40 px height, as every header control is. */}
        <SearchField size="lg" value={value} onChange={onChange} placeholder={placeholder} ariaLabel={name}
          name={fieldName} autoFocus={autoFocus} />
      </div>
    )
  }
  const searched = value.trim() !== ''
  const said = searched ? `${name}: “${value.trim()}”` : name
  if (probe) return <div aria-hidden className="size-10 shrink-0" />
  return (
    <Popover portal placement="bottom" align="right" width={288}
      trigger={(open, toggle) => (
        <button type="button" onClick={toggle} aria-haspopup="dialog" aria-expanded={open} aria-label={said} title={said}
          data-tour={tour}
          className={cx(
            'relative inline-flex size-10 shrink-0 items-center justify-center rounded-pill transition-colors duration-(--duration-short)',
            open ? 'bg-surface-high text-on-surface' : 'text-on-surface hover:bg-surface-high',
            className,
          )}>
          <Search size={16} />
          {searched && <span aria-hidden data-header-set className="absolute right-2 top-2 size-1.5 rounded-pill bg-primary" />}
        </button>
      )}>
      {() => (
        <SearchField size="md" value={value} onChange={onChange} placeholder={placeholder} ariaLabel={name}
          name={fieldName} autoFocus />
      )}
    </Popover>
  )
}
