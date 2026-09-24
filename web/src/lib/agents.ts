import { useEffect, useState } from 'react'
import { api, type AgentProvider, type DiscoveredAgent, type ModelItem } from './api'

/** Single source of truth for "what agents can the system run". The genuinely
 *  shared piece is the ACP discovery loop (filter ready ACP runtimes → fetch
 *  each runtime's discovered agents → map by provider_id). Both the chat
 *  composer (useComposerData) and the agent-catalog hook below build on it, so
 *  the discovery logic lives in exactly one place. */

/** The key an agent name is stored under in the routing-suppression store.
 *
 *  🔑 MIRRORS `agents/routing.py`'s `canonical_agent`, and it exists because the mismatch was a
 *  live defect, not a hypothetical. `GET /api/agents/routing/status` returns CANONICAL keys, and
 *  the agent detail page compared them to the raw route name with `.includes()`. Measured on a live
 *  gateway: three dismissals of the shipped default agent `PersonalClaw` wrote
 *  `muted: ["personalclaw"]`, `is_suppressed("PersonalClaw")` returned True, and the panel said
 *  "Active — eligible for auto-routing suggestions" with no Unmute control. Any comparison against
 *  a name in `routing_status().muted` goes through here; `routingMuteReachable` is the rail. */
export const canonicalAgentKey = (name: string): string => String(name ?? '').trim().toLowerCase()

/** ACP discovery: for every ready non-native provider, fetch its agents. */
export async function loadAcpDiscovered(providers: AgentProvider[]): Promise<Record<string, DiscoveredAgent[]>> {
  const acp = providers.filter((p) => p.type !== 'native' && p.ready)
  const results = await Promise.allSettled(acp.map((p) => api.agentProviderAgents(p.provider_id)))
  const map: Record<string, DiscoveredAgent[]> = {}
  acp.forEach((p, i) => { const r = results[i]; if (r.status === 'fulfilled') map[p.provider_id] = r.value.agents })
  return map
}

/** A flat, grouped agent option — native agents + ACP-discovered agents. */
export interface AgentOption {
  value: string        // binding id — native agent name, or "acp:<cli>/<modeId>"
  label: string
  group: string        // "Native" | provider id (e.g. "acp:claude-code")
  description?: string
}

export function flattenAgentOptions(nativeNames: string[], discovered: Record<string, DiscoveredAgent[]>): AgentOption[] {
  const out: AgentOption[] = nativeNames.map((n) => ({ value: n, label: n, group: 'Native' }))
  for (const [providerId, agents] of Object.entries(discovered)) {
    for (const d of agents) out.push({ value: d.id, label: d.name, group: providerId, description: d.description })
  }
  return out
}

/** Hook: the full agent catalog (native + ACP), grouped. The native source is
 *  parameterized because consumers differ — chat picks the installed runtime
 *  agent, while binding (workflows/hooks/agent-scope) targets saved agent
 *  PROFILES. Defaults to saved profiles. */
export function useAgentCatalog(opts: { native?: 'saved' | 'installed' } = {}): { options: AgentOption[]; loading: boolean; discovered: Record<string, DiscoveredAgent[]> } {
  const nativeSource = opts.native ?? 'saved'
  const [options, setOptions] = useState<AgentOption[]>([])
  const [discovered, setDiscovered] = useState<Record<string, DiscoveredAgent[]>>({})
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    ;(async () => {
      const [nat, providers] = await Promise.allSettled([
        nativeSource === 'installed' ? api.agentsInstalled() : api.savedAgents(),
        api.agentProviders(),
      ])
      if (!alive) return
      const nativeNames = nat.status === 'fulfilled' ? nat.value.map((a) => a.name) : []
      const disc = providers.status === 'fulfilled' ? await loadAcpDiscovered(providers.value) : {}
      if (!alive) return
      setDiscovered(disc)
      setOptions(flattenAgentOptions(nativeNames, disc))
      setLoading(false)
    })()
    return () => { alive = false }
  }, [nativeSource])

  return { options, loading, discovered }
}

/** Ensure a SAVED agent profile exists for a discovered ACP agent, returning
 *  its profile name. Persistent bindings (default_agent / pool_agent) resolve a
 *  saved profile name — an ephemeral discovered agent has none — so we
 *  materialize a tiny profile (provider + provider_agent) that resolve_agent_
 *  bindings can bind exactly like a chat session does. Idempotent: reuses an
 *  existing profile with the same provider+provider_agent. `value` is the
 *  AgentOption value: a native name, or a discovered id "acp:<cli>/<modeId>". */
export async function ensureBindableAgentName(value: string, discovered: Record<string, DiscoveredAgent[]>): Promise<string> {
  // Native agent (not an acp: id) → the value IS the profile name.
  if (!value.startsWith('acp:')) return value
  let found: { providerId: string; agent: DiscoveredAgent } | null = null
  for (const [providerId, list] of Object.entries(discovered)) {
    const a = list.find((d) => d.id === value)
    if (a) { found = { providerId, agent: a }; break }
  }
  if (!found) return value  // unknown — leave as-is (backend will warn/fallback)
  const { providerId, agent } = found
  // Deterministic profile name so re-selecting the same agent reuses the profile.
  const profileName = `${providerId}-${agent.provider_agent || agent.name}`
    .replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64)
  const existing = await api.agents().then((d) => d.agents.find((p) => p.name === profileName)).catch(() => undefined)
  if (!existing) {
    await api.createAgent({
      name: profileName, provider: providerId, provider_agent: agent.provider_agent,
      description: `${agent.name} (${providerId})`,
    }).catch(() => {})
  }
  return profileName
}

/** A model option shaped for the Combobox, grouped by provider. Mirrors the
 *  chat composer's ModelPill (value=model.name, label=model_name||name,
 *  group=provider) so a model dropdown reads identically everywhere. */
export interface ModelOption { value: string; label: string; group: string; description?: string }

export function flattenModelOptions(models: ModelItem[]): ModelOption[] {
  return models.map((m) => ({ value: m.name, label: m.model_name || m.name, group: m.provider || 'Models', description: m.description }))
}

/** Hook: the model catalog (the same /api/models the composer uses), grouped by provider.
 *
 *  🔴 IT USED TO "DEGRADE TO AN EMPTY LIST IF THE BACKEND ISN'T REACHABLE", and that sentence was
 *  the defect written down as a feature. `.catch(() => setLoading(false))` left `options` at `[]`
 *  with `loading` false, so a failed `/api/models` rendered a model picker that said the user has
 *  no models — identical on screen to an account with none, with nothing announced and no retry.
 *  The rejection is CAPTURED now and every consumer renders it beside the field: an empty picker
 *  and an unreachable one are different facts and the form has to say which it is. */
export function useModelCatalog(): { options: ModelOption[]; loading: boolean; error: unknown } {
  const [options, setOptions] = useState<ModelOption[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    let alive = true
    api.models()
      .then((m) => { if (alive) { setOptions(flattenModelOptions(m)); setLoading(false) } })
      .catch((e) => { if (alive) { setError(e); setLoading(false) } })
    return () => { alive = false }
  }, [])
  return { options, loading, error }
}

/** Hook: the ACTIVE chat models (Settings → Models bindings), shaped for the
 *  Combobox. Used for agent model selection so a user can only pin a model
 *  that's actually active/bound — preventing the staleness that arises when an
 *  agent points at a model later removed from the active set.
 *
 *  Same capture as `useModelCatalog` above, and the stakes are higher here: this list is the set a
 *  user may PIN an agent to, so an unreadable one that renders empty reads as "no model is active"
 *  on a box where three are bound. */
export function useActiveChatModelOptions(): { options: ModelOption[]; loading: boolean; error: unknown } {
  const [options, setOptions] = useState<ModelOption[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    let alive = true
    api.chatModels().then((rows) => {
      if (!alive) return
      setOptions(rows.map((r) => ({ value: r.name, label: r.model_id || r.name, group: r.provider || 'Models', description: r.description })))
      setLoading(false)
    }).catch((e) => { if (alive) { setError(e); setLoading(false) } })
    return () => { alive = false }
  }, [])
  return { options, loading, error }
}
