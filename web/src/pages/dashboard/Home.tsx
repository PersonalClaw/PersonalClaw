import { Plus } from 'lucide-react'
import { useIdentity, firstNameOf } from '../../app/identity'
import { useIsMobile } from '../../app/useIsMobile'
import type { RouteProps } from '../../app/useQueryState'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { TopBar } from '../../ui/TopBar'
import { HomeOverview } from './DashboardPage'

// ── Home: the Overview ──────────────────────────────────────────────────────────────────────────
//
// Home is the page a person comes to look at: your agents' day, with every ask that waits on you
// counted on its band and every live run on it, the tiles you keep on Home, and what else the
// Overview holds. What waits on your answer is answered on Needs you (`#/mission-control`,
// `NeedsYouPage`), which the badge, the band's "2 need you" and its Answer, ⌘⌥A and the palette
// open. Home has no View switch: an old `#/dashboard?view=attention` lands on Needs you
// (`App.tsx`).

/** Home: the greeting, New chat, and the Overview. */
export function Home(route: RouteProps) {
  const { navigate } = route
  const { name } = useIdentity()
  // A phone's New chat keeps a 44 px target beside the shell's corner controls.
  const phone = useIsMobile()
  // The column does not clip: its scroller is bounded by `min-h-0`, so the column's own
  // `overflow-hidden` only cut, at the fold, the focus ring of whatever control lay on it (the UI
  // matrix's clipped-ring check, on Schedule's "New trigger" at its viewport).
  return (
    <div className="flex h-full flex-col">
      <TopBar
        contentAligned
        left={<h1 data-type="headline-s" className="min-w-0 truncate text-on-surface">{greetingFor(name)}</h1>}
        right={(
          <HeaderActions>
            <HeaderControl icon={Plus} label="New chat" variant="secondary" priority="primary"
              className={phone ? 'min-h-11 min-w-11' : undefined} onClick={() => navigate('chat/new')} />
          </HeaderActions>
        )}
      />
      <div className="min-h-0 flex-1 overflow-y-auto"><HomeOverview {...route} /></div>
    </div>
  )
}

/** Time-of-day greeting, personalised when we know the owner's name: the page's title. */
function greetingFor(name: string | undefined): string {
  const h = new Date().getHours()
  const part = h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening'
  return name ? `Good ${part}, ${firstNameOf(name)}` : `Good ${part}`
}
