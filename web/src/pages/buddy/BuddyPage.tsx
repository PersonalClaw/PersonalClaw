/** The buddy's live presence, `#/buddy` (#3832): what the buddy window shows, and a browser tab
 *  can open too.
 *
 *  The host (`lib/buddy/host.ts`) feeds the state machine the gateway's two lists and whether the
 *  microphone is live, read again on each `running_changed`, `waiting_changed` and `listening`
 *  frame (a doorbell: no field is read off it), and the page draws what the machine says:
 *  nothing when nothing is happening, the compact line while work runs, and one alert at a time,
 *  expanded with its brief. The socket's own state is the connection, and an announced restart
 *  says restarting rather than offline. Answers go only through the card's answer controls, on
 *  each item's own route. A desktop window adds the shell's inputs (absence, the top-edge zone,
 *  drag) when it hosts this page; in a tab there is no shell, and the page shows what the lists say. */

import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { type RouteProps } from '../../app/useQueryState'
import { createBuddyHost, LIVE_DEPS, type BuddyHost, type HostSnapshot } from '../../lib/buddy/host'
import { viewOf, type BuddyState } from '../../lib/buddy/machine'
import { buddyBridge, type BuddyPresentation } from '../../lib/desktopBridge'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { BuddyCard } from '../../ui/BuddyCard'
import { BuddyPresence, orbOf } from './BuddyPresence'
import { liveCardPropsOf } from './liveCard'

const ORB_ID = 'buddy-presence-orb'

/** The size the buddy's window takes for where the machine stands. Hidden keeps the smallest, not
 *  `hidden`: nothing in the shell shows the window again yet (the main process's doorbell will),
 *  and a window with nothing drawn on it passes every click through. */
export function presentationOf(machine: BuddyState): BuddyPresentation {
  const room = viewOf(machine).room
  if (room === 'expanded' || machine.card === 'open') return 'expanded'
  if (room === 'compact' || room === 'floating' || room === 'sleeping') return 'compact'
  return 'peek'
}

/** The frames that say a list moved: a doorbell, carrying nothing the page reads. */
const RINGS = new Set(['running_changed', 'waiting_changed', 'listening'])

export function BuddyPage(_: RouteProps) {
  const [snap, setSnap] = useState<HostSnapshot | null>(null)
  const hostRef = useRef<BuddyHost | null>(null)

  useEffect(() => {
    const host = createBuddyHost({
      ...LIVE_DEPS,
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

  // In the buddy's own window, its size follows the machine.
  const presentation = snap ? presentationOf(snap.machine) : null
  useEffect(() => {
    if (presentation) buddyBridge()?.setPresentation(presentation)
  }, [presentation])

  const host = hostRef.current
  const truth = snap ? viewOf(snap.machine).truth : null
  // In the buddy's own window a link to the app (the card's Open, a row's route) opens in the main
  // window: followed here, it would load the app inside the buddy.
  const onClickCapture = (e: MouseEvent<HTMLDivElement>) => {
    const bridge = buddyBridge()
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
    <div data-full-screen="buddy" onKeyDown={onKeyDown} onClickCapture={onClickCapture} className="flex h-full min-w-0 flex-col items-center p-s">
      <h1 className="sr-only">Buddy</h1>
      {snap && host ? (
        <BuddyPresence
          machine={snap.machine}
          orbId={ORB_ID}
          onClick={() => host.send({ type: 'click' })}
          onPointer={(at) => host.send({ type: 'pointer', at })}
          card={truth ? <BuddyCard {...liveCardPropsOf(snap, host, ORB_ID)} state={orbOf(truth, snap.machine)} /> : null}
        />
      ) : null}
    </div>
  )
}
