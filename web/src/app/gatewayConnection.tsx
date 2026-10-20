import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api } from '../lib/api'
import { expiryStamp } from '../lib/epoch'
import { useChatSocket } from '../lib/useChatSocket'
import { useVisiblePoll } from '../lib/useVisiblePoll'

/** The gateway connection, as the shell reads it: one reading for the status line, its banner and
 *  the System status card (#3823).
 *
 *  - `connecting` until the first `/api/system` read answers;
 *  - `connected` while reads answer;
 *  - `disconnected` once one fails, until one answers again.
 *
 *  It reads the gateway once on mount, then on a heartbeat: every HEARTBEAT_MS while connected,
 *  every RETRY_MS while not (both through `useVisiblePoll`, so a hidden tab reads nothing and an
 *  idle one reads less). A heartbeat alone notices a stopped gateway late, so the tab's shared
 *  socket is watched too: its drop reads the gateway at once, and so does its reopen, so a restart
 *  that is back in a second is read as back in a second. */
export type ConnectionState = 'connecting' | 'connected' | 'disconnected'

export interface GatewayConnection {
  state: ConnectionState
  /** While `disconnected`: when the gateway last answered (epoch ms), or `null` when it has not
   *  answered since this page opened. */
  lastReachedAt: number | null
  /** While `disconnected`: when it was lost (epoch ms). One lost connection, one value. */
  lostAt: number | null
  /** Read the gateway now, rather than at the next heartbeat. */
  check: () => void
}

/** How often a connected tab reads the gateway. */
export const HEARTBEAT_MS = 30_000
/** How often a tab that has lost the gateway, or not reached it yet, reads it again. */
export const RETRY_MS = 5_000

type Reading = Omit<GatewayConnection, 'check'>

const CONNECTING: Reading = { state: 'connecting', lastReachedAt: null, lostAt: null }

const GatewayConnectionContext = createContext<GatewayConnection>({ ...CONNECTING, check: () => {} })

/** Reads the gateway connection for everything under it. The shell mounts one. */
export function GatewayConnectionProvider({ children }: { children: ReactNode }) {
  const connection = useGatewayConnectionReading()
  return <GatewayConnectionContext.Provider value={connection}>{children}</GatewayConnectionContext.Provider>
}

/** The shell's reading of the gateway connection. */
export function useGatewayConnection(): GatewayConnection {
  return useContext(GatewayConnectionContext)
}

function useGatewayConnectionReading(): GatewayConnection {
  const [reading, setReading] = useState<Reading>(CONNECTING)
  // The last answer, kept out of state: a heartbeat that answers changes nothing on screen, so it
  // re-renders nothing. It is copied into the reading when the connection is lost.
  const reachedAt = useRef<number | null>(null)
  // Only the latest read decides: one that answers after a later one is stale.
  const latest = useRef(0)

  const check = useCallback(() => {
    const n = ++latest.current
    Promise.resolve().then(() => api.system()).then(
      () => {
        if (n !== latest.current) return
        reachedAt.current = Date.now()
        setReading((r) => (r.state === 'connected' ? r : { state: 'connected', lastReachedAt: null, lostAt: null }))
      },
      () => {
        if (n !== latest.current) return
        setReading((r) => (r.state === 'disconnected' ? r : { state: 'disconnected', lastReachedAt: reachedAt.current, lostAt: Date.now() }))
      },
    )
  }, [])

  useEffect(check, [check])
  // Not `immediate`: a change of cadence is a change of state, which the read that caused it has
  // just reported, so the next read waits its interval.
  useVisiblePoll(check, reading.state === 'connected' ? HEARTBEAT_MS : RETRY_MS, { immediate: false })
  useChatSocket(() => {}, check, (open) => { if (!open) check() })

  return useMemo(() => ({ ...reading, check }), [reading, check])
}

/** The connection in words: what the status line shows while not connected, and what the System
 *  status control is called. */
export function connectionWords(c: Pick<GatewayConnection, 'state' | 'lastReachedAt'>): string {
  if (c.state === 'connected') return 'Gateway connected'
  if (c.state === 'connecting') return 'Connecting to gateway…'
  return `Gateway disconnected · ${lastReached(c.lastReachedAt)}`
}

/** "last reached 11:24", with the day in front when that was not today, or that it was not
 *  reached since this page opened. */
export function lastReached(at: number | null): string {
  const stamp = at === null ? '' : expiryStamp(at / 1000)
  return stamp ? `last reached ${stamp}` : 'not reached since this page opened'
}
