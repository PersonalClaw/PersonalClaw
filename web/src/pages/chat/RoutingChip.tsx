import { useState } from 'react'
import { reportingWrite } from '../../app/reportingWrite'
import { motion } from 'framer-motion'
import { Compass, X } from 'lucide-react'
import { spring } from '../../design/motion'
import { fvs } from '../../design/fontWeight'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { api, type RoutingSuggestion } from '../../lib/api'
import { notify } from '../../app/appSdk'

// The wire type lives with the transport that produces it (lib/api.ts) — the server
// builds one payload per send and ships it over BOTH the WS broadcast and the send
// response, so a copy declared here could drift from what actually arrives. Re-exported
// so the chip stays the one import site for everything routing-chip-shaped.
export type { RoutingSuggestion }

/** The one sentence a user gets when a dismissal crosses the mute threshold, and where to undo it.
 *
 *  🔴 THE THIRD DISMISSAL IS A DIFFERENT EVENT and it used to look identical to the first two.
 *  Crossing the threshold mutes the agent for good — there is no expiry, and `is_suppressed` returns
 *  on the mute BEFORE it reads `cooldown_hours`, so the "Dismiss cooldown" setting cannot walk it
 *  back (measured: PATCHing it to 0 leaves the mute in place, and so does toggling the section's
 *  master switch off and on). The dismiss response already says which dismissal this was
 *  (`{count, muted}`) and the old code discarded it, so the agent went quiet forever with nothing
 *  said anywhere. Naming the surface that clears it is the other half of issue 414. */
const announceMuted = (agent: string) =>
  notify(`${agent} won't be suggested again — undo it under Settings › Chat › Agent routing › Muted agents.`, 'info')

/** Routing suggestion chip (AGENT-ROUTING S2) — a subtle, non-blocking pill above
 *  the composer proposing a better-fit specialist for the current default-agent chat.
 *  "Route" re-targets the session via the existing agent-switch path; ✕ dismisses
 *  (and suppresses future suggestions for that agent). Both actions double-write a
 *  feedback record (routing_pair producer) so routing-pair accuracy shows up in
 *  Settings → AI feedback with zero extra UI. The chip is a *proposal* — nothing
 *  about the session changes until the user clicks Route. */
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

  // 🪤 Same deferred shape as the organize chip: this dismissal BUMPS A COUNTER that mutes the agent
  // at a threshold, so a swallowed rejection means the suggestion keeps coming and never mutes — the
  // user's repeated dismissals quietly amount to nothing. The chip still hides (a dismissal is a
  // request to get something out of the way); the report is what makes the recurrence explicable.
  // The threshold crossing itself is announced by `announceMuted` — see its note.
  const dismiss = () => {
    void reportingWrite(`dismiss the ${suggestion.agent} suggestion`, async () => {
      if ((await api.routingDismiss(suggestion.agent))?.muted) announceMuted(suggestion.agent)
    })
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
      data-type="label-s"
      className="inline-flex items-center gap-2 rounded-pill border border-outline-variant/50 bg-surface-container pl-3 pr-1.5 h-8"
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
