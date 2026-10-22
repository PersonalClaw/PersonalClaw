/** The buddy's live presence, `#/buddy` (#3832): what the buddy window shows, and a browser tab
 *  can open too.
 *
 *  The host (`lib/buddy/host.ts`) feeds the state machine the gateway's two lists and whether the
 *  microphone is live, read again whenever the doorbell rings, and the page draws what the machine
 *  says: nothing when nothing is happening, the compact line while work runs, and one alert at a
 *  time, expanded with its brief. Answers go only through the card's answer controls, on each
 *  item's own route.
 *
 *  In the buddy's own window the doorbell is the shell's (`desktop/doorbell.js`): its one socket
 *  rings the page and says its connection, and the page holds no socket of its own. The shell
 *  also says where the window is (a drag docks or floats the machine with it) and whether one of
 *  the app's own windows is full screen (over which only alerts show, as a quiet peek). Docked with
 *  nothing to show, the window stays as the top-edge zone; otherwise it asks for `hidden`, and the
 *  shell shows the window again for an item that may interrupt you. In a browser tab the page's own socket is the doorbell: `running_changed`,
 *  `waiting_changed` and `listening` each ring it, carrying nothing it reads, and the socket's
 *  state is the connection, with an announced restart said as restarting. */

import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { type RouteProps } from '../../app/useQueryState'
import { BUDDY_POLICY_CHANGED } from '../../lib/api'
import { createBuddyHost, BUDDY_DEPS, type BuddyHost, type HostSnapshot } from '../../lib/buddy/host'
import { viewOf, type BuddyState } from '../../lib/buddy/machine'
import { buddyBridge, type BuddyBridge, type BuddyLayout, type BuddyPresentation, type BuddyRect } from '../../lib/desktopBridge'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { BuddyCard } from '../../ui/BuddyCard'
import { BuddyPresence, orbOf } from './BuddyPresence'
import { CARD_BUDGET, CARD_GAP, ORB_BOX, layoutAt } from '../../lib/buddy/cardPlacement'
import { liveCardPropsOf } from './liveCard'
import { lineOf } from '../../lib/buddy/lines'


/** The size the buddy's window takes for where the machine stands. With nothing to show, a docked
 *  buddy keeps the peek's strip as its top-edge zone, which draws nothing and takes no click; one
 *  hidden by the owner, switched off or floating is `hidden`, which the shell's doorbell undoes for
 *  something that may interrupt you. */
export function presentationOf(machine: BuddyState): BuddyPresentation {
  const view = viewOf(machine)
  if (!view.shows) return machine.enabled && machine.docked && !machine.muted ? 'peek' : 'hidden'
  if (view.room === 'expanded' || machine.card === 'open') return 'expanded'
  if (view.room === 'compact' || view.room === 'floating' || view.room === 'sleeping') return 'compact'
  return 'peek'
}

/** The frames that say a list or the buddy's policy moved: a doorbell, carrying nothing the page reads. */
const RINGS = new Set(['running_changed', 'waiting_changed', 'listening', BUDDY_POLICY_CHANGED])

type HostRef = { current: BuddyHost | null }

/** A browser tab's doorbell: the page's own socket. */
function SocketDoorbell({ hostRef }: { hostRef: HostRef }) {
  useChatSocket(
    (m: WsMessage) => {
      const host = hostRef.current
      if (!host) return
      if (RINGS.has(m.type)) host.ring()
      // A restart the gateway announced, read as the update overlay reads it.
      else if (m.type === 'update_progress' && m.data?.step === 'restarting') host.send({ type: 'connection', state: 'restarting' })
    },
    () => hostRef.current?.ring(),
    (connected) => hostRef.current?.send({ type: 'connection', state: connected ? 'back' : 'lost' }),
  )
  return null
}

/** The buddy window's shell: its doorbell (the shell's one socket, heard through the window's
 *  bridge), where the window is, whether one of the app's own windows is full screen, and the
 *  top-edge zone. */
function WindowShell({ hostRef, bridge }: { hostRef: HostRef; bridge: BuddyBridge }) {
  useEffect(() => {
    const machine = () => hostRef.current?.snapshot().machine
    const stops = [
      bridge.onRing?.(() => hostRef.current?.ring()),
      bridge.onConnection?.((state) => hostRef.current?.send({ type: 'connection', state: state === 'up' ? 'back' : state })),
      // A drag the shell is moving, then where it settled; on each load, where the window is.
      bridge.onPlacement?.((where) => {
        const m = machine()
        if (!m) return
        if (where === 'moving') {
          if (!m.dragging) hostRef.current?.send({ type: 'drag', phase: 'start' })
        } else if (m.dragging || m.docked !== (where === 'docked')) {
          hostRef.current?.send({ type: 'drag', phase: 'end', docked: where === 'docked' })
        }
      }),
      bridge.onFullScreen?.((on) => hostRef.current?.fullScreen(on)),
      // Away and back, as the shell reads the system's idle time; and the owner's hide.
      bridge.onAway?.((away) => {
        const m = machine()
        if (m && m.away !== away) hostRef.current?.send({ type: away ? 'away' : 'return' })
      }),
      bridge.onHidden?.((hidden) => {
        const m = machine()
        if (m && m.muted !== hidden) hostRef.current?.send({ type: hidden ? 'hide' : 'show' })
      }),
    ]
    // The top-edge zone: docked with nothing to show, the window stays up as a strip that takes no
    // click, and the pointer resting on it is the machine's `zone` (a peek). On macOS the shell
    // forwards the pointer's moves to it; nothing samples the pointer on a timer.
    let inZone = false
    const onMove = () => {
      const m = machine()
      if (inZone || !m || !m.docked || m.presence !== 'hidden') return
      inZone = true
      hostRef.current?.send({ type: 'pointer', at: 'zone' })
    }
    const onLeave = () => {
      if (!inZone) return
      inZone = false
      hostRef.current?.send({ type: 'pointer', at: 'away' })
    }
    document.addEventListener('mousemove', onMove, { passive: true })
    document.documentElement.addEventListener('mouseleave', onLeave)
    return () => {
      for (const stop of stops) stop?.()
      document.removeEventListener('mousemove', onMove)
      document.documentElement.removeEventListener('mouseleave', onLeave)
    }
  }, [bridge, hostRef])
  return null
}

/** What a screen reader hears of the buddy (#3902): the page's one polite live region. A screen
 *  reader follows the focused window, and the buddy never takes focus, so its words have to reach a
 *  listener without it. An alert is said as "Needs you: <its line>", once, when it comes to the
 *  front, and not again while it waits, however often the lists are read again or its line moves;
 *  otherwise it says the buddy's one line as it stands, in full. */
function Announcer({ machine }: { machine: BuddyState }) {
  const view = viewOf(machine)
  const truth = view.shows ? view.truth : null
  const alertId = truth?.kind === 'alert' ? truth.alert.id : null
  const alertLine = truth?.kind === 'alert' ? `Needs you: ${lineOf(truth, 'expandedCard')}` : ''
  const [said, setSaid] = useState<{ id: string; text: string } | null>(null)
  useEffect(() => {
    if (alertId && said?.id !== alertId) setSaid({ id: alertId, text: alertLine })
  }, [alertId, alertLine, said?.id])
  // The card's full line, never the island's shortened one: a listener gets every word.
  const text = alertId
    ? (said?.id === alertId ? said.text : alertLine)
    : truth ? lineOf(truth, 'expandedCard') : ''
  return <p data-buddy-announce="" role="status" aria-live="polite" className="sr-only">{text}</p>
}

/** The page's own size: the buddy's window, or the tab it is open in. */
function useViewport(): { width: number; height: number } {
  const read = () => ({ width: window.innerWidth, height: window.innerHeight })
  const [size, setSize] = useState(read)
  useEffect(() => {
    const onResize = () => setSize(read())
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  return size
}

/** Kept clear inside a tab's edges. */
const PAGE_EDGE = 8

/** Until the shell says where, as the window opens by default: the orb at its corner and the card's
 *  area right of it, opening down. */
export const DEFAULT_LAYOUT: BuddyLayout = {
  side: 'right', opens: 'down',
  orb: { x: 0, y: 0, width: ORB_BOX, height: ORB_BOX },
  card: { x: ORB_BOX + CARD_GAP, y: 0, width: CARD_BUDGET.width, height: CARD_BUDGET.maxHeight },
}

const scaled = (r: BuddyRect, by: number): BuddyRect => ({ x: r.x / by, y: r.y / by, width: r.width / by, height: r.height / by })

/** Where the page draws the orb and its card. In the buddy's window, the shape its shell pushes
 *  (`onLayout`), in the window's points: the work area and the choice of display live in main, so
 *  the page follows them and never guesses its own. Zoomed in, the points are divided by the zoom,
 *  read off the window's own width against the shape's. In a tab, or a window from a shell that
 *  sizes it to the room, the same rule over the page itself, the orb at its top centre. */
function useLayout(bridge: BuddyBridge | null): BuddyLayout {
  const view = useViewport()
  const [pushed, setPushed] = useState<BuddyLayout | null>(null)
  const follows = Boolean(bridge?.onLayout)
  useEffect(() => bridge?.onLayout?.(setPushed), [bridge])
  if (follows) {
    const l = pushed ?? DEFAULT_LAYOUT
    const width = Math.max(l.orb.x + l.orb.width, l.card.x + l.card.width) - Math.min(l.orb.x, l.card.x)
    const zoom = view.width > 0 ? Math.max(1, width / view.width) : 1
    return zoom === 1 ? l : { ...l, orb: scaled(l.orb, zoom), card: scaled(l.card, zoom) }
  }
  const area = { x: PAGE_EDGE, y: PAGE_EDGE, width: Math.max(0, view.width - 2 * PAGE_EDGE), height: Math.max(0, view.height - 2 * PAGE_EDGE) }
  return layoutAt(area, { x: Math.round(view.width / 2 - ORB_BOX / 2), y: PAGE_EDGE })
}

export function BuddyPage(_: RouteProps) {
  const [snap, setSnap] = useState<HostSnapshot | null>(null)
  const hostRef = useRef<BuddyHost | null>(null)

  useEffect(() => {
    const host = createBuddyHost({
      ...BUDDY_DEPS,
      now: () => Date.now(),
      setTimer: (fire, ms) => window.setTimeout(fire, ms),
      clearTimer: (handle) => window.clearTimeout(handle as number),
    }, setSnap)
    hostRef.current = host
    host.ring()
    return () => {
      host.stop()
      hostRef.current = null
    }
  }, [])


  // In the buddy's own window its size follows the machine, once the lists have been read: before
  // that the machine's hidden is only where it starts.
  const [bridge] = useState(buddyBridge)
  const layout = useLayout(bridge)
  const presentation = snap?.read ? presentationOf(snap.machine) : null

  // A tap on an alert's notification opens the buddy (#3897): the shell shows its window and rings
  // it, and the page opens as a click on the orb opens it, on the alert the one order shows first.
  // Here, after the host's own effect, not in the doorbell: a child's effects run first, and a ring
  // held for the page is handed over the moment it listens.
  useEffect(() => bridge?.onOpenAlert?.(() => hostRef.current?.send({ type: 'click' })), [bridge])
  useEffect(() => {
    if (presentation) bridge?.setPresentation(presentation)
  }, [bridge, presentation])

  const host = hostRef.current
  const truth = snap ? viewOf(snap.machine).truth : null
  // In the buddy's own window a link to the app (the card's Open, a row's route) opens in the main
  // window: followed here, it would load the app inside the buddy.
  // In the buddy's own window a right-click on the face or the card is its context menu.
  const onContextMenu = (e: MouseEvent<HTMLDivElement>) => {
    if (!bridge?.openMenu || !(e.target instanceof Element) || !e.target.closest('[data-buddy-shape]')) return
    e.preventDefault()
    bridge.openMenu?.()
  }
  const onClickCapture = (e: MouseEvent<HTMLDivElement>) => {
    const link = e.target instanceof Element ? e.target.closest<HTMLAnchorElement>('a[href^="#/"]') : null
    if (!bridge || !link) return
    e.preventDefault()
    bridge.openInApp(link.getAttribute('href')!)
  }
  // Escape closes the card and decides nothing; a key pressed in another window never arrives.
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Escape' || !host) return
    const onAlert = e.target instanceof Element && Boolean(e.target.closest('[role="group"][aria-label^="Permission needed"]'))
    host.send({ type: 'key', key: 'Escape', windowFocused: document.hasFocus(), onAlert })
  }

  return (
    <div data-full-screen="buddy" onKeyDown={onKeyDown} onClickCapture={onClickCapture} onContextMenu={onContextMenu} className="relative h-full min-w-0">
      <h1 className="sr-only">Buddy</h1>
      {snap ? <Announcer machine={snap.machine} /> : null}
      {/* A shell built before its doorbell has no ring to hear: the page keeps its own socket. */}
      {bridge?.onRing ? <WindowShell hostRef={hostRef} bridge={bridge} /> : <SocketDoorbell hostRef={hostRef} />}
      {snap && host ? (
        <BuddyPresence
          machine={snap.machine}
          layout={layout}
          onClick={() => host.send({ type: 'click' })}
          onPointer={(at) => host.send({ type: 'pointer', at })}
          card={truth ? <BuddyCard {...liveCardPropsOf(snap, host)} state={orbOf(truth, snap.machine)} /> : null}
        />
      ) : null}
    </div>
  )
}
