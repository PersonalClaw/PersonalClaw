import { useCallback, useEffect, useRef, useState } from 'react'
import { Inbox, MessageSquare, PanelLeft, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import { SpotlightTour, type SpotlightStep } from '../../ui/SpotlightTour'
import { consumeProductTourRequest, onProductTourRequest } from './tourLaunch'

/** One stop: what to spotlight, and the surface it lives on. */
interface Stop extends SpotlightStep {
  /** The route this stop's anchor lives on, or null for shell chrome that is on every
   *  route. The tour navigates there; it never renders the surface itself. */
  route: string | null
}

/** The five stops (ONBOARDING-UX ruling b: rail → chat → inbox → approvals → settings).
 *
 *  Every route here is in `STARTER_NAV_IDS`, and that is load-bearing rather than a
 *  coincidence: the shell auto-pins a rail surface the moment it is REACHED (OU-5), so a
 *  tour that walked the user through a held-back surface would grow their rail on their
 *  behalf. Because all four are starter surfaces, `isDisclosed` is already true for each
 *  and the auto-pin effect returns before it writes — the tour leaves the disclosure
 *  record byte-identical. `productTour.test.tsx` asserts that as an outcome. */
const STOPS: Stop[] = [
  {
    id: 'rail', anchor: 'rail', route: null, icon: PanelLeft,
    title: 'The sidebar is the whole app',
    body: 'It starts with five essentials. Any other surface joins it the first time you open one — nothing is locked away.',
  },
  {
    id: 'chat', anchor: 'chat', route: 'chat', icon: MessageSquare,
    title: 'Chat is where you ask for things',
    body: 'Type what you want. Attach files, mention knowledge, pick an agent — or just talk, and it will pick the tools it needs.',
  },
  {
    id: 'inbox', anchor: 'inbox', route: 'inbox', icon: Inbox,
    title: 'Work comes back to you in the Inbox',
    body: 'Messages, reminders and finished runs queue up here instead of chasing you across the app.',
  },
  {
    id: 'approvals', anchor: 'approvals', route: 'dashboard', icon: ShieldCheck,
    title: 'Anything risky waits for you here',
    body: '"Needs you" on your home screen holds every run that is blocked on your permission. Approve or reject it in one click.',
  },
  {
    id: 'settings', anchor: 'settings', route: 'settings', icon: SlidersHorizontal,
    title: 'Everything is yours to change',
    body: 'Models, appearance, how much it may do unattended — all of it is in Settings, and this search covers every value inside every panel.',
  },
]

/** The replayable product tour (ONBOARDING-UX Session 5 / T5.1).
 *
 *  Mounted in the shell, once, and normally renders nothing. It runs when something asks
 *  for it — the onboarding done screen, or the Discover hub's "Replay the tour" card, both
 *  through `tourLaunch.ts` — and it is what does the walking: `SpotlightTour` draws a stop
 *  and reports Back/Next, this owns which stop that is and which surface it lives on.
 *
 *  **Guidance never gates**, which here means three specific things:
 *   • the tour has no step that blocks and no step that must be finished;
 *   • Escape (or the X, or a click outside the card) exits from any stop, leaving the app
 *     exactly as reachable as it was — the only thing that unmounts is the overlay;
 *   • exiting returns the user to whatever surface they were on when the tour started, so
 *     a tour taken from Discover ends back on Discover rather than stranding them in
 *     Settings.
 *
 *  **Nothing is reported and nothing is stored.** There is no progress field, no "seen the
 *  tour" flag and no request on any step — the stop index is React state and the launch
 *  request is module state (see `tourLaunch.ts`). The tour is replayable instead of
 *  resumable, which is why it needs no memory at all. */
export function ProductTour({ route, navigate }: {
  /** The shell's current route id, so exiting can avoid a redundant navigation. */
  route: string
  navigate: (path: string) => void
}) {
  /** The showing stop, or null when the tour is not running. */
  const [index, setIndex] = useState<number | null>(null)
  /** Where the tour was launched from, to hand back on exit. */
  const launchedFrom = useRef('')
  // `route` in a ref as well: `start` must read the CURRENT route without re-subscribing
  // the launch listener every time the user changes page.
  const routeRef = useRef(route)
  routeRef.current = route

  const start = useCallback(() => {
    launchedFrom.current = routeRef.current
    setIndex(0)
  }, [])

  // Both launch paths. The mount read is for a request left behind by the onboarding done
  // screen, whose `finish()` replaced the flow with this shell; the subscription is for a
  // request made while the shell is already up (the Discover card).
  useEffect(() => {
    if (consumeProductTourRequest()) start()
    return onProductTourRequest(() => { if (consumeProductTourRequest()) start() })
  }, [start])

  // Take the user to the stop's surface. The spotlight polls for its anchor, so it is fine
  // that the page mounts a few frames after this.
  useEffect(() => {
    if (index === null) return
    const target = STOPS[index].route
    if (target && target !== routeRef.current) navigate(target)
  }, [index, navigate])

  const exit = useCallback(() => {
    const back = launchedFrom.current
    setIndex(null)
    if (back && back !== routeRef.current) navigate(back)
  }, [navigate])

  if (index === null) return null
  return (
    <SpotlightTour steps={STOPS} index={index} label="PersonalClaw tour"
      onIndex={setIndex} onExit={exit} />
  )
}

/** The stops, for the rails that assert every anchor exists on the surface it names. */
export const PRODUCT_TOUR_STOPS: readonly Stop[] = STOPS
