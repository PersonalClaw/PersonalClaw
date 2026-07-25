import type { ReactNode, MouseEvent } from 'react'
import type { LucideIcon } from 'lucide-react'
import { cx } from './cx'

type Size = 'xs' | 'sm' | 'inherit'
const SIZE: Record<Size, string> = {
  xs: 'text-[0.75rem]',      // dense chrome (queue chips, VoicePanel hints, FilterMenu "Clear")
  sm: 'text-[0.8125rem]',    // standalone links (Show more, View all loops, external "Open")
  inherit: '',               // inline inside a running sentence — take the paragraph's size
}

/** The inline text-link idiom — a coral `text-primary` label that underlines on
 *  hover, used for in-sentence navigations ("Browse the Store"), quiet inline
 *  actions ("Remove from queue", "View all loops"), and the occasional real
 *  `<a>` (an external task URL, a memory deep-link). ~16 sites across the app
 *  hand-rolled `text-primary hover:underline` with drifting size classes,
 *  element types, icon slots, and margins; this is the single source.
 *
 *  Renders a `<button type="button">` by default, or an `<a>` when `href` is set
 *  (`external` adds `target=_blank rel="noopener noreferrer"` for off-app URLs;
 *  omit it for in-app hash links). An `icon` opts the row into
 *  `inline-flex items-center gap-1` and sits before the label, or after it when
 *  `iconPosition="trailing"` — plain (icon-less) links stay bare inline so they
 *  flow inside running text without a baseline shift. `size` defaults to
 *  `inherit` (the in-sentence case); pass `xs`/`sm` for standalone links. Extra
 *  layout (`ml-auto`, `mt-1.5`, `normal-case`) rides through `className`. */
export function TextLink({
  children, href, external = false, onClick, icon: Icon, iconPosition = 'leading',
  iconSize = 13, size = 'inherit', disabled = false, title, className,
}: {
  children: ReactNode
  href?: string
  external?: boolean
  onClick?: (e: MouseEvent<HTMLElement>) => void
  icon?: LucideIcon
  iconPosition?: 'leading' | 'trailing'
  iconSize?: number
  size?: Size
  disabled?: boolean
  title?: string
  className?: string
}) {
  const cls = cx(
    'text-primary hover:underline disabled:opacity-50',
    Icon && 'inline-flex items-center gap-1',
    SIZE[size],
    className,
  )
  const body = Icon
    ? (iconPosition === 'trailing'
        ? <>{children} <Icon size={iconSize} /></>
        : <><Icon size={iconSize} /> {children}</>)
    : children

  if (href !== undefined) {
    return (
      <a href={href} onClick={onClick} title={title} className={cls}
        {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>
        {body}
      </a>
    )
  }
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title} className={cls}>
      {body}
    </button>
  )
}
