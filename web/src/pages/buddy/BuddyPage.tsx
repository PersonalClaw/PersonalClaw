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
 *  rings the page and says its connection, and the page holds no socket of its own. With nothing
 *  to show it asks for `hidden`, and the shell shows the window again for an item that may
 *  interrupt you. In a browser tab the page's own socket is the doorbell: `running_changed`,
 *  `waiting_changed` and `listening` each ring it, carrying nothing it reads, and the socket's
 *  state is the connection, with an announced restart said as restarting. */

import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { type RouteProps } from '../../app/useQueryState'
import { createBuddyHost, LIVE_DEPS, type BuddyHost, type HostSnapshot } from '../../lib/buddy/host'
import { viewOf, type BuddyState } from '../../lib/buddy/machine'
import { buddyBridge, type BuddyBridge, type BuddyPresentation } from '../../lib/desktopBridge'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { BuddyCard } from '../../ui/BuddyCard'
import { BuddyPresence, orbOf } from './BuddyPresence'
import { liveCardPropsOf } from './liveCard'

const ORB_ID = 'buddy-presence-orb'

/** The size the buddy's window takes for where the machine stands: `hidden` with nothing to show,
 *  which the shell's doorbell undoes when something may interrupt you. */
export function presentationOf(machine: BuddyState): BuddyPresentation {
  const view = viewOf(machine)
  if (!view.shows) return 'hidden'
  if (view.room === 'expanded' || machine.card === 'open') return 'expanded'
  if (view.room === 'compact' || view.room === 'floating' || view.room === 'sleeping') return 'compact'
  return 'peek'
}

/** The frames that say a list moved: a doorbell, carrying nothing the page reads. */
const RINGS = new Set(['running_changed', 'waiting_changed', 'listening'])

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

/** The buddy window's doorbell: the shell's one socket, heard through the window's bridge. */
function WindowDoorbell({ hostRef, bridge }: { hostRef: HostRef; bridge: BuddyBridge }) {
  useEffect(() => {
    const stopRing = bridge.onRing(() => hostRef.current?.ring())
    const stopConnection = bridge.onConnection((state) => hostRef.current?.send({ type: 'connection', state: state === 'up' ? 'back' : state }))
    return () => {
      stopRing()
      stopConnection()
    }
  }, [bridge, hostRef])
  return null
}

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


  // In the buddy's own window its size follows the machine, once the lists have been read: before
  // that the machine's hidden is only where it starts.
  const [bridge] = useState(buddyBridge)
  const presentation = snap?.read ? presentationOf(snap.machine) : null
  useEffect(() => {
    if (presentation) bridge?.setPresentation(presentation)
  }, [bridge, presentation])

  const host = hostRef.current
  const truth = snap ? viewOf(snap.machine).truth : null
  // In the buddy's own window a link to the app (the card's Open, a row's route) opens in the main
  // window: followed here, it would load the app inside the buddy.
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
    <div data-full-screen="buddy" onKeyDown={onKeyDown} onClickCapture={onClickCapture} className="flex h-full min-w-0 flex-col items-center p-s">
      <h1 className="sr-only">Buddy</h1>
      {bridge ? <WindowDoorbell hostRef={hostRef} bridge={bridge} /> : <SocketDoorbell hostRef={hostRef} />}
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
