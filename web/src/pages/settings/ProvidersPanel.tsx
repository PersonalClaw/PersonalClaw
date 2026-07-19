import { useState, useMemo } from 'react'
import {
  Bot, Cpu, Hash, Inbox, Bell, Wrench, ListChecks, Webhook, Sparkles,
  BookOpen, Database, FileText, Workflow, Search, type LucideIcon,
} from 'lucide-react'
import { api, type SettingsProvider, type AgentRuntime, type ChannelRuntime } from '../../lib/api'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { requestRunInTerminal } from '../terminal/terminalBridge'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { PanelHeader } from './settingsUI'
import { Skeleton } from '../../ui/ListScaffold'
import { ProviderCard } from './ProviderCard'
import { MultiInstanceCard } from './MultiInstanceCard'
import { RemoteModelProviders } from './ModelBackends'
import { LocalModelManager } from './LocalModelManager'
import type { ProviderModels } from '../../lib/api'

// One section per provider ENTITY (VISION §"The entities"). Order is intentional:
// the entities a user touches most (what backs a chat, what models are available)
// come first. Each section's `hint` says what plugging into it means.
const ENTITY_META: Record<string, { label: string; icon: LucideIcon; hint: string }> = {
  agent: { label: 'Agent providers', icon: Bot, hint: 'Runtimes that drive a chat — the in-process native agent and external agent CLIs (Claude Code, Codex). Enable one, then sign in to any CLI that needs it.' },
  model: { label: 'Model providers', icon: Cpu, hint: 'Contribute models to the pool you bind to use cases in Models. Native bundled models run in-process; remote providers are multi-instance connections.' },
  search: { label: 'Search providers', icon: Search, hint: 'Web-search backends you bind to use cases in Search. Configure a provider (endpoint / API key) here, then assign it per use case.' },
  channel: { label: 'Channel providers', icon: Hash, hint: 'Interaction surfaces you reach the system through — initiate sessions and talk to agents from each channel.' },
  inbox: { label: 'Inbox providers', icon: Inbox, hint: 'Each contributes its own items into the unified inbox you pull into chats.' },
  notification: { label: 'Notification providers', icon: Bell, hint: 'Channels notifications can be routed to.' },
  tool: { label: 'Tool providers', icon: Wrench, hint: 'Contribute tools the native agent can call. MCP and OpenAI-compatible servers are multi-instance.' },
  task: { label: 'Task providers', icon: ListChecks, hint: 'Contribute tasks into one combined pool the agent and you manage.' },
  action: { label: 'Action providers', icon: Webhook, hint: 'Actions a trigger can fire when it runs, grouped by what they act on.' },
  skills: { label: 'Skill providers', icon: Sparkles, hint: 'Marketplaces you install skills from; installed skills live on the filesystem for agents to reference.' },
  knowledge: { label: 'Knowledge providers', icon: BookOpen, hint: 'Contribute knowledge entities into one shared pool the system draws on.' },
  memory: { label: 'Memory providers', icon: Database, hint: 'Ordered fallbacks providing memory CRUD — the primary serves unless it is unavailable.' },
  prompt: { label: 'Prompt providers', icon: FileText, hint: 'Contribute prompts into the system.' },
  workflow: { label: 'Workflow providers', icon: Workflow, hint: 'Contribute workflows into the system.' },
}
const ENTITY_ORDER = ['agent', 'model', 'search', 'channel', 'inbox', 'notification', 'tool', 'task', 'action', 'skills', 'knowledge', 'memory', 'prompt', 'workflow']

// Within Actions, sub-group cards by the entity each action acts on (manifest entity).
const ACTION_ENTITY_LABELS: Record<string, string> = {
  task: 'Task actions', agent: 'Agent actions', comms: 'Messaging actions',
  notification: 'Notification actions', shell: 'Shell actions', script: 'Script actions', webhook: 'Webhook actions',
}
const ACTION_ENTITY_ORDER = ['task', 'agent', 'comms', 'notification', 'shell', 'script', 'webhook']

/** Providers → one section per provider ENTITY (VISION). Each provider card
 *  carries its own enable toggle + schema-driven config under it. Agent merges
 *  runtime readiness/sign-in; Model splits native-bundled (download-managed)
 *  from remote multi-instance connections. A bundle that serves two entities
 *  appears under each entity's section. */
export function ProvidersPanel({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  // Which provider's config accordion is expanded rides the URL (?open=<name>,
  // push → Back collapses it; deep-link/refresh restores it). Single-open across
  // the whole panel: opening one closes any other. Per-instance edit/add forms
  // stay local (form draft, not view-state).
  const [openProvider, setOpenProvider] = useQueryParam(query, setQuery, 'open', '')
  const openCfg = (name: string) => (v: boolean) => setOpenProvider(v ? name : '')

  // Stale-while-revalidate + sessionStorage persistence: the provider catalog and
  // its schemas barely change, so on revisit (and after a full reload) the page
  // paints instantly from cache and revalidates in the background — no long
  // "Loading…". The two fetches are independent: the provider list renders as soon
  // as IT lands, without waiting on the slower agent-runtime readiness probe.
  const { data: providers, refresh: refreshProviders } = useCachedData(
    'settings:providers', () => api.settingsProviders().catch(() => [] as SettingsProvider[]), { persist: true },
  )
  const { data: runtimesData, refresh: refreshRuntimes } = useCachedData(
    'settings:agent-runtimes', () => api.agentRuntimes().catch(() => [] as AgentRuntime[]), { persist: true },
  )
  // Available models per provider — feeds each local-model provider's download card
  // (catalog + downloaded state + `searchable`). One fetch, revalidated on mutation.
  const { data: availableData, refresh: refreshAvailable } = useCachedData(
    'settings:models-available', () => api.modelsAvailable().catch(() => [] as ProviderModels[]), { persist: true },
  )
  const availableByProvider = useMemo(() => {
    const m = new Map<string, ProviderModels>()
    for (const r of availableData ?? []) m.set(r.name, r)
    return m
  }, [availableData])
  // Live channel runtime (connection health) — folded onto the matching channel
  // provider card so the enable/config surface also shows whether it's connected now.
  const { data: channelsData, refresh: refreshChannels } = useCachedData(
    'settings:channels', () => api.channels().catch(() => [] as ChannelRuntime[]), { persist: true },
  )
  const channelByName = useMemo(() => {
    const m = new Map<string, ChannelRuntime>()
    for (const c of channelsData ?? []) m.set(c.name, c)
    return m
  }, [channelsData])
  // The channel *provider* is named e.g. `slack-channel` while the channel *runtime*
  // is `slack` — match exactly, else on the name with a trailing `-channel` stripped.
  const matchChannel = (providerName: string, map: Map<string, ChannelRuntime>): ChannelRuntime | undefined =>
    map.get(providerName) ?? map.get(providerName.replace(/-channel$/, ''))
  // A forced readiness recheck (manual / post-sign-in) takes precedence over the
  // cached snapshot until the next revalidate folds it back in.
  const [runtimeOverride, setRuntimeOverride] = useState<AgentRuntime[] | null>(null)
  const runtimes = runtimeOverride ?? runtimesData ?? []

  // A mutation (enable/disable/config) invalidates the cached catalog so the next
  // read revalidates against the changed state instead of a stale snapshot.
  const reload = () => { invalidateCache('settings:providers'); invalidateCache('settings:models-available'); refreshProviders(); refreshRuntimes(); refreshAvailable() }

  // Re-probe agent-runtime readiness, forcing a fresh probe (bypassing the 5-min
  // readiness cache). Used by the manual "Check availability" action + post-sign-in.
  const recheckRuntimes = async () => {
    try { setRuntimeOverride(await api.agentRuntimes(true)) } catch { /* keep current */ }
  }

  // After kicking off a sign-in, the CLI auth (often a browser OAuth flow) takes a
  // few seconds — a single fixed delay misses it. Poll a fresh probe a handful of
  // times until the runtime stops reporting needs_login (or we give up).
  const pollAfterSignIn = async (id: string) => {
    for (let i = 0; i < 12; i++) {
      await new Promise((r) => setTimeout(r, 2500))
      let rts: AgentRuntime[] = []
      try { rts = await api.agentRuntimes(true) } catch { continue }
      setRuntimeOverride(rts)
      const rt = rts.find((r) => r.provider_id === id || r.name === id)
      if (rt && rt.state !== 'needs_login') return  // signed in (ready) or a new state
    }
  }

  // First load with nothing cached: render the section SHAPE (skeleton) so the
  // panel appears instantly instead of a bare "Loading…". On every revisit the
  // cache seeds `providers` synchronously, so this branch is skipped.
  if (!providers) return <ProvidersSkeleton />

  // group by entity (= provider.type)
  const byType = new Map<string, SettingsProvider[]>()
  for (const p of providers) {
    const t = p.provider?.type || 'other'
    if (!byType.has(t)) byType.set(t, [])
    byType.get(t)!.push(p)
  }
  // runtime readiness keyed by the extension name it belongs to (native + acp)
  const runtimeByExt = new Map<string, AgentRuntime>()
  for (const r of runtimes) if (r.extension) runtimeByExt.set(r.extension, r)

  const onSignIn = (rt: AgentRuntime) => {
    if (rt.login_command?.length) requestRunInTerminal(rt.login_command.join(' '))
    // Sign-in completes asynchronously (terminal/browser auth) — poll a fresh
    // readiness probe until the runtime is no longer needs_login.
    void pollAfterSignIn(rt.provider_id || rt.name)
  }

  const orderedTypes = [...ENTITY_ORDER.filter((t) => byType.has(t)), ...[...byType.keys()].filter((t) => !ENTITY_ORDER.includes(t))]

  return (
    <div>
      <PanelHeader title="Providers" hint="Everything pluggable in the system, organized by the entity each provider plugs into. Enable a provider and configure it inline; a provider that serves two entities appears under each." />
      {orderedTypes.map((type) => {
        const meta = ENTITY_META[type] ?? { label: `${type} providers`, icon: Wrench, hint: '' }
        const exts = byType.get(type) ?? []
        return (
          <EntitySection key={type} icon={meta.icon} label={meta.label} hint={meta.hint} count={exts.length}>
            {type === 'agent' && exts.map((ext) => (
              <ProviderCard key={ext.name} ext={ext} runtime={runtimeByExt.get(ext.name)} open={openProvider === ext.name} onOpenChange={openCfg(ext.name)} onChanged={reload} onSignIn={onSignIn} onRecheck={recheckRuntimes} />
            ))}

            {type === 'model' && <ModelEntitySection exts={exts} availableByProvider={availableByProvider} openProvider={openProvider} openCfg={openCfg} onChanged={reload} />}

            {type === 'action' && <ActionGroups exts={exts} openProvider={openProvider} openCfg={openCfg} onChanged={reload} />}

            {/* every other entity: a multiInstance provider is an instance frame
                (MCP/OpenAI tools, …); a singleton is a plain toggle+config card. */}
            {type !== 'agent' && type !== 'model' && type !== 'action' && exts.map((ext) => (
              ext.provider?.multiInstance
                ? <MultiInstanceCard key={ext.name} ext={ext} onChanged={reload} />
                : <ProviderCard key={ext.name} ext={ext} channel={type === 'channel' ? matchChannel(ext.name, channelByName) : undefined}
                    open={openProvider === ext.name} onOpenChange={openCfg(ext.name)} onChanged={reload} onChannelChanged={refreshChannels} />
            ))}
          </EntitySection>
        )
      })}
    </div>
  )
}

/** First-load placeholder: the panel header + a few entity sections rendered as
 *  shimmering shapes, so Providers paints instantly on a cold (uncached) open
 *  instead of stalling on a bare "Loading…". Calm, content-shaped, matches the
 *  real section rhythm (heading + hint + a couple of cards). */
function ProvidersSkeleton() {
  return (
    <div>
      <PanelHeader title="Providers" hint="Everything pluggable in the system, organized by the entity each provider plugs into. Enable a provider and configure it inline; a provider that serves two entities appears under each." />
      {Array.from({ length: 4 }).map((_, s) => (
        <section key={s} className="mb-2xl" aria-busy="true" aria-label="Loading providers">
          <div className="mb-1 flex items-center gap-2">
            <Skeleton className="size-4 rounded" />
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-4 w-6 rounded-pill" />
          </div>
          <Skeleton className="mb-m h-3 w-2/3" />
          <div className="flex flex-col gap-2">
            {Array.from({ length: 2 }).map((_, c) => (
              <div key={c} className="flex items-center gap-3 rounded-lg bg-surface-container px-l py-l">
                <Skeleton className="size-8 shrink-0 rounded-lg" />
                <div className="flex-1 min-w-0 space-y-2">
                  <Skeleton className="h-3.5 w-1/4" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
                <Skeleton className="h-6 w-10 shrink-0 rounded-pill" />
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

function EntitySection({ icon: Icon, label, hint, count, children }: {
  icon: LucideIcon; label: string; hint: string; count: number; children: React.ReactNode
}) {
  return (
    <section className="mb-2xl">
      <div className="mb-1 flex items-center gap-2">
        <Icon size={16} className="text-on-surface-low" />
        <h3 className="text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 600' }}>{label}</h3>
        <span className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.65rem] tabular-nums">{count}</span>
      </div>
      {hint && <p className="mb-m text-on-surface-low text-[0.8125rem]">{hint}</p>}
      <div className="flex flex-col gap-2">{children}</div>
    </section>
  )
}

// Threaded from ProvidersPanel so nested ProviderCards share the one URL-backed
// single-open accordion (?open=<provider>).
type OpenCfg = { openProvider: string; openCfg: (name: string) => (v: boolean) => void }

/** Model entity: LOCAL downloadable providers (each with a uniform download-management
 *  card) + remote multi-instance connections. "Local" is the `local: true` flag on the
 *  /api/models/available card — the one signal from the core local-model registry (no
 *  hardcoded names). Every local provider renders the SAME LocalModelManager card,
 *  including ollama (searchable → gets a library search box), so the download UX is
 *  uniform. A local provider backed by a bundled ext also shows its enable/config card;
 *  a config-backed local provider (ollama) shows just the manager. */
function ModelEntitySection({ exts, availableByProvider, openProvider, openCfg, onChanged }: {
  exts: SettingsProvider[]; availableByProvider: Map<string, ProviderModels>; onChanged: () => void
} & OpenCfg) {
  const extByName = new Map(exts.map((e) => [e.name, e]))
  // Local providers come from the registry (available cards flagged local), NOT from
  // exts — so a config-backed searchable provider (ollama) appears here too.
  const localCards = [...availableByProvider.values()].filter((a) => a.local)
  const otherNative = exts.filter((e) => !availableByProvider.get(e.name)?.local && !e.provider?.multiInstance)
  return (
    <div className="flex flex-col gap-4">
      {(localCards.length > 0 || otherNative.length > 0) && (
        <div>
          <div className="mb-2 text-on-surface-low text-[0.7rem] uppercase tracking-wide">Native (bundled)</div>
          <div className="flex flex-col gap-2">
            {localCards.map((av) => {
              const ext = extByName.get(av.name)
              const enabled = ext ? ext.enabled : true  // config-backed (ollama) → always show
              return (
                <div key={av.name}>
                  {ext
                    ? <ProviderCard ext={ext} open={openProvider === ext.name} onOpenChange={openCfg(ext.name)} onChanged={onChanged} />
                    : <div className="text-on-surface text-[0.875rem]" style={{ fontVariationSettings: '"wght" 600' }}>{av.displayName || av.name}</div>}
                  {enabled && (
                    <div className={ext ? 'mt-2 pl-4' : 'mt-2'}>
                      <LocalModelManager provider={av.name} models={av.models ?? []} searchable={av.searchable} onChanged={onChanged} />
                    </div>
                  )}
                </div>
              )
            })}
            {otherNative.map((ext) => <ProviderCard key={ext.name} ext={ext} open={openProvider === ext.name} onOpenChange={openCfg(ext.name)} onChanged={onChanged} />)}
          </div>
        </div>
      )}
      <div>
        <div className="mb-2 text-on-surface-low text-[0.7rem] uppercase tracking-wide">Remote (multi-instance)</div>
        <RemoteModelProviders />
      </div>
    </div>
  )
}

/** Actions sub-grouped by the entity each acts on. */
function ActionGroups({ exts, openProvider, openCfg, onChanged }: { exts: SettingsProvider[]; onChanged: () => void } & OpenCfg) {
  const byEntity = new Map<string, SettingsProvider[]>()
  for (const e of exts) {
    const k = e.provider?.entity || 'other'
    if (!byEntity.has(k)) byEntity.set(k, [])
    byEntity.get(k)!.push(e)
  }
  const ordered = [...ACTION_ENTITY_ORDER.filter((e) => byEntity.has(e)), ...[...byEntity.keys()].filter((e) => !ACTION_ENTITY_ORDER.includes(e))]
  return (
    <div className="flex flex-col gap-4">
      {ordered.map((entity) => (
        <div key={entity}>
          <div className="mb-2 text-on-surface-low text-[0.7rem] uppercase tracking-wide">{ACTION_ENTITY_LABELS[entity] ?? 'Other actions'}</div>
          <div className="flex flex-col gap-2">
            {(byEntity.get(entity) ?? []).map((ext) => <ProviderCard key={ext.name} ext={ext} open={openProvider === ext.name} onOpenChange={openCfg(ext.name)} onChanged={onChanged} />)}
          </div>
        </div>
      ))}
    </div>
  )
}
