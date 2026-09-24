import { api, type SavedAgent, type AgentProvider, type DiscoveredAgent } from '../../lib/api'
import { loadAcpDiscovered } from '../../lib/agents'
import { useQuery, invalidateKeys } from '../../lib/data'

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
  /** The read's rejection, so a consumer can tell a failure from an empty catalog. */
  error: unknown
  /** True once the read has produced a value — `groups: []` alone cannot say that. */
  loaded: boolean
  loading: boolean
  reload: () => void
}

/** Build the grouped agent catalog: native saved profiles + every ACP runtime's
 *  discovered agents. A failed PROVIDER slice degrades to an empty group; a failed NATIVE
 *  read rejects, because the list's empty state is a claim about exactly that read. */
async function fetchAgentGroups(): Promise<AgentGroup[]> {
  const [nat, provs] = await Promise.allSettled([api.agents(), api.agentProviders()])
  // 🔑 `allSettled` never rejects, so exposing the hook's `error` above would have been an INERT
  // control — the fetcher always resolved, mapping a failed native read to `agents: []`. The NATIVE
  // slice is the collection this page's empty state makes a claim about ("No native agents"), so its
  // rejection has to reach the caller. The ACP provider slices stay tolerant on purpose: an unready
  // or unreachable runtime is still rendered as its own group, which is this surface's whole point.
  if (nat.status === 'rejected') throw nat.reason
  const out: AgentGroup[] = [{
    kind: 'native',
    agents: nat.value.agents,
    defaultAgent: nat.value.default_agent,
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
  // 🪤 `error` used to be DROPPED here, and `data ?? []` erased the difference between "not loaded"
  // and "none" before any consumer could see it — so `#/agents` answered a failed read with its
  // newcomer empty state ("No native agents · Create an agent to define its model…"). The adapter
  // was the swallow: `useQuery` had the error all along. Re-exposed, matching `useAutonomyLadder`,
  // the one adapter in the tree that already did this.
  const { data, error, loading, refresh } = useQuery('agents:groups', fetchAgentGroups, { persist: true })
  return { groups: data ?? [], error, loaded: data !== undefined, loading,
    reload: () => { invalidateKeys('agents:groups'); refresh() } }
}
