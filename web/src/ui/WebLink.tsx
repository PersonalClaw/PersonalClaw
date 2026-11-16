import type { ReactNode } from 'react'
import { Tooltip } from './Tooltip'

/** The schemes a link out of the app may open. */
const WEB_LINK_PROTOCOLS = new Set(['http:', 'https:', 'mailto:'])

/** A link's destination if it is a web or email address, else undefined.
 *
 *  Parsed with the browser's own URL parser rather than matched as text, so this reads the
 *  scheme exactly where navigation will: the parser drops every tab and newline and trims
 *  control characters first, which is how `java<TAB>script:` is the script scheme to the page
 *  while a regex sees no scheme at all. A relative reference (a path on this gateway, a `#/`
 *  route, `//host`, a bare `host/path`) is refused too: it resolves against the dashboard's
 *  own origin, and nothing in a stored body gets to choose where inside this app a click
 *  lands. The returned value is the parser's serialisation — what the browser will follow. */
export function webHref(href: string): string | undefined {
  let url: URL
  try { url = new URL(href.trim()) } catch { return undefined }
  return WEB_LINK_PROTOCOLS.has(url.protocol) ? url.href : undefined
}

/** Why a link that is not a web or email address went nowhere. */
export const REFUSED = 'Not opened: only web and email links open from here'

/** A link out of the app: the one way rendered text (`Markdown`'s links) and the rows drawn from
 *  it (a turn's sources) open a page. Only a web or email address opens (`webHref`); it opens in
 *  a new tab with no opener and no referrer, which the desktop app's window-open handler hands to
 *  the system browser (`desktop/systemBrowser.js`). Any other address (`javascript:`, `data:`, a
 *  path on this gateway) keeps its words, styled as the link it was written as, and goes nowhere;
 *  the themed `Tooltip` says why (`REFUSED`). */
export function WebLink({ href, children, className, title, 'aria-label': ariaLabel }: {
  href: string | undefined
  children: ReactNode
  className?: string
  title?: string
  'aria-label'?: string
}) {
  const to = href ? webHref(href) : undefined
  if (!to) {
    return (
      // Its words are no control, so no hidden copy sits in the sentence (a copy of the text would
      // carry it): the bubble alone says it, on hover.
      <Tooltip text={REFUSED} describes={false}>
        {(trigger) => <span {...trigger} className={className}>{children}</span>}
      </Tooltip>
    )
  }
  return <a href={to} target="_blank" rel="noopener noreferrer" className={className} title={title} aria-label={ariaLabel}>{children}</a>
}
