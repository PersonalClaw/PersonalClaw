import { useEffect, useState } from 'react'
import { WifiOff } from 'lucide-react'
import { Button } from '../ui/Button'
import { lastReached, useGatewayConnection } from './gatewayConnection'

/** How long a lost connection lasts before its banner shows. A restart or an update is usually back
 *  inside it, and the status line already says the connection is lost meanwhile. */
export const BANNER_WAIT_MS = 2_000

/** The lost connection's banner (#3823): the status line's escalation, shown only while the gateway
 *  has stayed unreachable for BANNER_WAIT_MS, and gone the moment it answers again. It floats over
 *  the page, below the shell corners, so nothing moves under the owner when it comes and goes.
 *
 *  Not `IncidentBanner`, which keeps its own meaning: incident mode has suspended unattended work. */
export function DisconnectedBanner({ className }: { className?: string }) {
  const { state, lastReachedAt, lostAt, check } = useGatewayConnection()
  // The lost connection whose wait is over. A later one waits again.
  const [waited, setWaited] = useState<number | null>(null)
  useEffect(() => {
    if (state !== 'disconnected' || lostAt === null) return
    const timer = window.setTimeout(() => setWaited(lostAt), Math.max(0, lostAt + BANNER_WAIT_MS - Date.now()))
    return () => window.clearTimeout(timer)
  }, [state, lostAt])

  if (state !== 'disconnected' || lostAt === null || waited !== lostAt) return null
  const reached = lastReached(lastReachedAt)
  const when = `${reached.charAt(0).toUpperCase()}${reached.slice(1)}`
  return (
    <div role="alert" className={['glass pointer-events-auto flex items-start gap-s rounded-lg px-m py-s', className].filter(Boolean).join(' ')}>
      <WifiOff size={16} aria-hidden className="mt-0.5 shrink-0 text-danger" />
      <p data-type="body-s" className="min-w-0 flex-1 text-on-surface">
        <strong>The gateway isn’t answering.</strong>{' '}
        <span className="text-on-surface-var">{/[.!?]$/.test(when) ? when : `${when}.`} It keeps trying on its own.</span>
      </p>
      <Button variant="secondary" size="xs" className="shrink-0" onClick={check}>Retry now</Button>
    </div>
  )
}
