import type { ReactNode } from 'react'
import { buttonLook, type ButtonSize, type ButtonVariant } from './Button'
import { cx } from './cx'
import { fvs } from '../design/fontWeight'

/** A link drawn as a button: a navigation that is a row's one action ("Answer in Attention"), where
 *  a text link would be too quiet and a button would be the wrong element. It is a real `<a href>`,
 *  so it opens in a new tab, shows where it goes, and reads as a link to assistive tech; a button
 *  that sets the hash in its click handler does none of that. It wears `Button`'s own variant, size
 *  and shape (`buttonLook`), so the two never drift.
 *
 *  A link has no disabled or loading state, so this has none: a link that can't go anywhere isn't
 *  drawn. `external` adds `target=_blank rel="noopener noreferrer"` for off-app URLs; omit it for
 *  in-app hash links. No hardcoded colours or px: all through the button's tokens. */
export function ButtonLink({
  children, href, external = false, variant = 'primary', size = 'md', shape = 'pill', title, ariaLabel, className,
}: {
  children: ReactNode
  href: string
  external?: boolean
  variant?: ButtonVariant
  size?: ButtonSize
  shape?: 'pill' | 'squircle'
  title?: string
  /** The accessible name, when the visible label alone doesn't say where the link goes. */
  ariaLabel?: string
  className?: string
}) {
  const look = buttonLook(variant, size, shape)
  return (
    <a href={href} title={title} aria-label={ariaLabel} data-type={look.role} style={fvs(470)}
      className={cx(look.className, className)}
      {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>
      {children}
    </a>
  )
}
