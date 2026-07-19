import { useEffect, useState } from 'react'
import { api, type AgentProvider, type DiscoveredAgent, type ModelItem, type AgentDef } from './api'
import { loadAcpDiscovered } from './agents'

/** Loads the real agent/model option lists the composer pickers need (native
 *  installed agents + ACP-discovered agents + providers + models). The ACP
 *  discovery loop is shared with the agent catalog via `loadAcpDiscovered`.
 *  Degrades gracefully (empty lists) if the backend isn't reachable. */
export function useComposerData() {
  const [agents, setAgents] = useState<AgentDef[]>([])
  const [providers, setProviders] = useState<AgentProvider[]>([])
  const [discovered, setDiscovered] = useState<Record<string, DiscoveredAgent[]>>({})
  const [models, setModels] = useState<ModelItem[]>([])
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let alive = true
    ;(async () => {
      const [ag, pr, md] = await Promise.allSettled([api.agents(), api.agentProviders(), api.models()])
      if (!alive) return
      // Native agents the user can actually bind a chat to: every authored native
      // agent (default + custom) MINUS the reserved background workers
      // (personalclaw-lite/loop/coder/…), which aren't user-facing chat agents.
      // Sourced from /api/agents (same as the Agents page) — NOT /api/agents/installed,
      // which lists provider identities and collapses all native agents to one entry.
      if (ag.status === 'fulfilled') setAgents(ag.value.agents.filter((a) => !a.reserved))
      if (md.status === 'fulfilled') setModels(md.value)
      if (pr.status === 'fulfilled') {
        setProviders(pr.value)
        const map = await loadAcpDiscovered(pr.value)
        if (alive) setDiscovered(map)
      }
      setReady(true)
    })()
    return () => { alive = false }
  }, [])

  return { agents, providers, discovered, models, ready }
}
