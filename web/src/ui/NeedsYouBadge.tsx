import { Hand } from 'lucide-react'
import { withWeight } from '../design/fontWeight'
import { useNeedsYou } from '../lib/useNeedsYou'

/** The toolbar's needs-you badge: how many things wait on you, on every page, opening Home.
 *
 *  It counts the rows of Home's Needs You from the same read (`useNeedsYou`), so the badge and the
 *  group can never disagree about how many there are. It shows nothing at 0: a counter reading 0
 *  is a claim nobody needs to read (`web/REDESIGN.md` §3, rule 3). And since its absence says
 *  nothing waits, a count not known is never absent: before the first read lands, and after a read
 *  that failed, the badge keeps a muted dash where the number would sit. */
export function NeedsYouBadge({ navigate, className }: {
  navigate: (path: string) => void
  /** Added to the badge, e.g. the compact widths' 44 px hit box (`TOUCH_TARGET`). */
  className?: string
}) {
  const { rows, data, error } = useNeedsYou()
  const unknown = !data
  const n = rows.length
  if (!unknown && n === 0) return null
  const label = !unknown ? `${n} waiting on you`
    : error ? 'What waits on you couldn’t be read' : 'What waits on you is not read yet'
  return (
    <button type="button" onClick={() => navigate('dashboard')}
      aria-label={`${label} — open Home`} title={label}
      className={`relative grid size-7 place-items-center rounded-pill transition-colors${className ? ` ${className}` : ''}`}
      style={{ color: 'var(--color-on-surface-low)' }}>
      <Hand size={16} aria-hidden />
      {unknown ? (
        <span data-type="caption" aria-hidden
          className="absolute -right-0.5 -top-0.5 grid min-w-[15px] h-[15px] place-items-center rounded-pill bg-surface-high px-1 leading-none text-on-surface-low">
          —
        </span>
      ) : (
        <span data-type="caption" aria-hidden
          className="absolute -right-0.5 -top-0.5 grid min-w-[15px] h-[15px] place-items-center rounded-pill px-1 leading-none"
          style={withWeight({ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }, 600)}>
          {n > 9 ? '9+' : n}
        </span>
      )}
    </button>
  )
}
