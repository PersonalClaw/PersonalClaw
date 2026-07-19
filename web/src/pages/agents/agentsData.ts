import { api, type SavedAgent, type AgentProvider, type DiscoveredAgent } from '../../lib/api'
import { loadAcpDiscovered } from '../../lib/agents'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'

/** A provider group for the Agents list. Native agents are PClaw-owned and fully
 *  editable; discovered agents come from an ACP runtime (e.g. claude-code, codex),
 *  are owned + invoked by that runtime, and are READ-ONLY here — surfaced for
 *  visibility (the pain-point fix) and used directly via the chat picker. */
export interface NativeGroup {
  kind: 'native'
  agents: SavedAgent[]
  defaultAgent: string
}
export interface DiscoveredGroup {
  kind: 'discovered'
  providerId: string        // "acp:claude-code"
  ready: boolean
  detail: string
  agents: DiscoveredAgent[]
}
export type AgentGroup = NativeGroup | DiscoveredGroup

export interface AgentsData {
  groups: AgentGroup[]
  loading: boolean
  reload: () => void
}

/** Build the grouped agent catalog: native saved profiles + every ACP runtime's
 *  discovered agents. Degrades gracefully (a failed slice yields an empty group). */
async function fetchAgentGroups(): Promise<AgentGroup[]> {
  const [nat, provs] = await Promise.allSettled([api.agents(), api.agentProviders()])
  const out: AgentGroup[] = [{
    kind: 'native',
    agents: nat.status === 'fulfilled' ? nat.value.agents : [],
    defaultAgent: nat.status === 'fulfilled' ? nat.value.default_agent : '',
  }]
  if (provs.status === 'fulfilled') {
    const acp = provs.value.filter((p: AgentProvider) => p.type !== 'native')
    // discover agents for READY providers (unready ones still shown as a group)
    const discovered = await loadAcpDiscovered(acp.filter((p) => p.ready))
    for (const p of acp) {
      out.push({ kind: 'discovered', providerId: p.provider_id, ready: p.ready, detail: p.detail, agents: discovered[p.provider_id] ?? [] })
    }
  }
  return out
}

/** Loads ALL agent definitions, grouped by source. Cache-backed so the Agents
 *  list paints instantly on revisit (persist:true — the catalog changes slowly)
 *  and revalidates in the background; `reload()` invalidates + re-pulls. */
export function useAgentsData(): AgentsData {
  const { data, loading, refresh } = useCachedData('agents:groups', fetchAgentGroups, { persist: true })
  return { groups: data ?? [], loading, reload: () => { invalidateCache('agents:groups'); refresh() } }
}
