import { Compass, X } from 'lucide-react'
import { api, type DiscoverTip as Tip, type DiscoverTryIt } from '../../lib/api'
import { invalidateKeys, useQuery } from '../../lib/data'
import { IconButton } from '../../ui/IconButton'
import { TextLink } from '../../ui/TextLink'
import { reportingWrite } from '../../app/reportingWrite'

/** A 44 px target at the compact widths (320 to 768 px), the kit's own size above them, as the
 *  buddy card's controls are (`ui/BuddyCard.tsx`): the tip's link and its dismiss are a phone's to
 *  press, and the dismiss is drawn at 24 px. */
export const TIP_TARGET = 'max-lg:inline-flex max-lg:min-h-11 max-lg:min-w-11 max-lg:items-center'

/** A tip's link, as an address after `#/`. */
function tryItPath(t: DiscoverTryIt): string {
  const q = new URLSearchParams(t.query ?? {}).toString()
  return q ? `${t.route}?${q}` : t.route
}

/** The tip an empty state shows: the first one still showing (the catalogue's order, less what was
 *  dismissed and the areas already used) that points somewhere other than *here*, the route the
 *  empty state sits on, which already says how to fill itself. */
export function tipFor(tips: readonly Tip[], here: string): Tip | undefined {
  const page = here.split(/[/?]/)[0]
  return tips.find((t) => t.try_it.route.split(/[/?]/)[0] !== page)
}

/** One Discover tip, in an empty Library, Automations or Capabilities page and at the end of first
 *  run (#3818): Discover left the sidebar's reach for the places it helps. It reads the catalogue the
 *  Discover page reads, under the same key, and its dismiss is the page's, so a tip dismissed in one
 *  place is gone from both.
 *
 *  Off, not read yet, unreadable, or with nothing left to show, it draws nothing: a tip is never
 *  what an empty state is about, so a failed read of one is no reason to put an error in it. The
 *  Discover page says the read failed, with its Retry.
 *
 *  Its link is an address (`href`); in first run, which holds the address until setup is done, it
 *  leaves through the way out it is handed (`onGo`). */
export function DiscoverTip({ here, onGo }: {
  /** The route the empty state sits on. */
  here: string
  /** Opens the tip's address, in place of the link. */
  onGo?: (route: string) => void
}) {
  const { data } = useQuery('discover', () => api.discover(), { persist: false })
  const tip = data?.enabled ? tipFor(data.areas.flatMap((a) => a.tips), here) : undefined
  if (!tip) return null
  const to = tryItPath(tip.try_it)
  const dismiss = async () => {
    await reportingWrite('dismiss this tip', () => api.dismissDiscoverTip(tip.id))
    invalidateKeys('discover')
  }
  return (
    <aside aria-label="A tip from Discover"
      className="flex w-full max-w-[420px] items-start gap-s rounded-lg bg-surface-container px-m py-s text-left">
      <Compass size={16} aria-hidden className="mt-0.5 shrink-0 text-primary" />
      <div className="min-w-0 flex-1">
        <p data-type="label-l" className="text-on-surface">{tip.title}</p>
        <p data-type="body-s" className="mt-0.5 text-on-surface-low">{tip.lesson}</p>
        <div className="mt-xs">
          {onGo
            ? <TextLink size="sm" ink="emphasis" onClick={() => onGo(to)} className={TIP_TARGET}>{tip.try_it.label}</TextLink>
            : <TextLink size="sm" ink="emphasis" href={`#/${to}`} className={TIP_TARGET}>{tip.try_it.label}</TextLink>}
        </div>
      </div>
      <IconButton icon={X} label="Dismiss this tip" size={24} iconSize={14} onClick={dismiss} className={`shrink-0 ${TIP_TARGET}`} />
    </aside>
  )
}
