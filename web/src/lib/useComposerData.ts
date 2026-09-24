import { useEffect, useState } from 'react'
import { api, type AgentProvider, type DiscoveredAgent, type ModelItem, type AgentDef } from './api'
import { loadAcpDiscovered } from './agents'

/** Loads the real agent/model option lists the composer pickers need (native
 *  installed agents + ACP-discovered agents + providers + models). The ACP
 *  discovery loop is shared with the agent catalog via `loadAcpDiscovered`.
 *  Degrades gracefully (empty lists) if the backend isn't reachable.
 *
 *  🔑 "Degrades to an empty list" is only safe for a consumer that RENDERS
 *  nothing. It is NOT safe for one that renders a SENTENCE about the empty list.
 *  `Promise.allSettled` sets `ready` even when a leg rejected, so a failed
 *  `api.agents()` used to be indistinguishable from a genuinely empty roster —
 *  and the agent picker read that as "No agents available", asserting the user
 *  has no agents installed when in truth nothing had been read. `agentsErr`
 *  exists so the picker can tell the two apart; see `AgentPill` in
 *  `ui/composer/controls.tsx`. The models legs need no equivalent: neither model
 *  path states an emptiness (see that file's branch table). */
export function useComposerData() {
  const [agents, setAgents] = useState<AgentDef[]>([])
  const [providers, setProviders] = useState<AgentProvider[]>([])
  const [discovered, setDiscovered] = useState<Record<string, DiscoveredAgent[]>>({})
  const [models, setModels] = useState<ModelItem[]>([])
  const [ready, setReady] = useState(false)
  const [agentsErr, setAgentsErr] = useState<unknown>(null)
  const [reloads, setReloads] = useState(0)

  useEffect(() => {
    let alive = true
    ;(async () => {
      // A retry must return to the honest not-yet-known state, or the stale error
      // would keep showing while the new read is in flight.
      setReady(false)
      const [ag, pr, md] = await Promise.allSettled([api.agents(), api.agentProviders(), api.models()])
      if (!alive) return
      // Native agents the user can actually bind a chat to: every authored native
      // agent (default + custom) MINUS the reserved background workers
      // (personalclaw-lite/loop/coder/…), which aren't user-facing chat agents.
      // Sourced from /api/agents (same as the Agents page) — NOT /api/agents/installed,
      // which lists provider identities and collapses all native agents to one entry.
      if (ag.status === 'fulfilled') {
        setAgents(ag.value.agents.filter((a) => !a.reserved))
        setAgentsErr(null)   // a successful retry must clear the previous failure
      } else {
        setAgentsErr(ag.reason ?? new Error('agents unavailable'))
      }
      if (md.status === 'fulfilled') setModels(md.value)
      if (pr.status === 'fulfilled') {
        setProviders(pr.value)
        const map = await loadAcpDiscovered(pr.value)
        if (alive) setDiscovered(map)
      }
      setReady(true)
    })()
    return () => { alive = false }
  }, [reloads])

  return { agents, providers, discovered, models, ready, agentsErr, retry: () => setReloads((n) => n + 1) }
}
