import { useState } from 'react'
import { motion } from 'framer-motion'
import { Compass, X } from 'lucide-react'
import { spring } from '../../design/motion'
import { fvs } from '../../design/fontWeight'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { api } from '../../lib/api'
import { notify } from '../../app/appSdk'

/** Routing suggestion chip (AGENT-ROUTING S2) — a subtle, non-blocking pill above
 *  the composer proposing a better-fit specialist for the current default-agent chat.
 *  "Route" re-targets the session via the existing agent-switch path; ✕ dismisses
 *  (and suppresses future suggestions for that agent). Both actions double-write a
 *  feedback record (routing_pair producer) so routing-pair accuracy shows up in
 *  Settings → AI feedback with zero extra UI. The chip is a *proposal* — nothing
 *  about the session changes until the user clicks Route. */
export interface RoutingSuggestion {
  session: string
  agent: string
  specialty: string
  score: number
  method: string
}

export function RoutingChip({ suggestion, defaultAgent, onRoute, onDismiss }: {
  suggestion: RoutingSuggestion
  defaultAgent: string
  onRoute: () => void
  onDismiss: () => void
}) {
  const [busy, setBusy] = useState(false)
  const producerId = `${defaultAgent || 'default'}->${suggestion.agent}`
  const targetId = `${suggestion.session}:${suggestion.agent}`

  const route = async () => {
    setBusy(true)
    try {
      await api.setSessionAgent(suggestion.session, suggestion.agent)
      // Double-write: accepting a suggestion is positive feedback on the routing pair.
      api.recordFeedback({
        target_kind: 'routing_suggestion', target_id: targetId, verdict: 'up',
        producer_kind: 'routing_pair', producer_id: producerId,
        snapshot: { agent: suggestion.agent, method: suggestion.method, score: suggestion.score },
      }).catch(() => {})
      notify(`Routed to ${suggestion.agent}`, 'success')
      onRoute()
    } catch (e) {
      notify(`Couldn't route: ${String((e as Error)?.message || e)}`, 'error')
      setBusy(false)
    }
  }

  const dismiss = () => {
    api.routingDismiss(suggestion.agent).catch(() => {})
    // Dismissing is negative feedback on the routing pair.
    api.recordFeedback({
      target_kind: 'routing_suggestion', target_id: targetId, verdict: 'down',
      producer_kind: 'routing_pair', producer_id: producerId,
      snapshot: { agent: suggestion.agent, method: suggestion.method, score: suggestion.score },
    }).catch(() => {})
    onDismiss()
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }}
      transition={spring.spatialFast}
      className="inline-flex items-center gap-2 rounded-pill border border-outline-variant/50 bg-surface-container pl-3 pr-1.5 h-8 text-[0.8125rem]"
      role="status">
      <Compass size={14} style={{ color: 'var(--color-primary)' }} className="shrink-0" />
      <span className="text-on-surface-var">
        <span className="text-on-surface" style={fvs(600)}>{suggestion.agent}</span>
        {suggestion.specialty ? ` handles this` : ' may fit better'} — route this chat to it?
      </span>
      <Button variant="secondary" size="xs" onClick={route} loading={busy} className="h-6 px-3">Route</Button>
      <IconButton icon={X} label="Not now (won't ask again for a while)" onClick={dismiss} size={24} iconSize={13} />
    </motion.div>
  )
}
