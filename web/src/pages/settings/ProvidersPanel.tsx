import { useMemo } from 'react'
import {
  Bot, Cpu, Hash, Inbox, Bell, Wrench, ListChecks, Webhook, Sparkles,
  BookOpen, Database, FileText, Workflow, Search, RefreshCw, type LucideIcon,
} from 'lucide-react'
import { api, type SettingsProvider, type AgentRuntime, type ChannelRuntime } from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { useVisiblePoll } from '../../lib/useVisiblePoll'
import { requestRunInTerminal } from '../terminal/terminalBridge'
import { reportingWrite } from '../../app/reportingWrite'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { Section, PanelHeader } from './settingsUI'
import { Skeleton, LoadingStatus, LoadError } from '../../ui/ListScaffold'
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
  model: { label: 'Model providers', icon: Cpu, hint: 'Contribute models to the pool you bind to use cases in Models. Native bundled models run in-process; every other provider is an instance you add, test, edit and remove here.' },
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
  // A `type: "sync"` app is a transport for Settings → Backups → Sync: its own settings
  // (repo, folder, host) are configured on its card here, and WHICH transport syncs is
  // chosen there. Named rather than left to the unknown-type fallback, which labelled a
  // real entity "sync providers" with a wrench.
  sync: { label: 'Sync transports', icon: RefreshCw, hint: 'Storage you own that more than one machine syncs through — a git repo, a synced folder, a bucket. Configure one here, then choose it under Backups → Sync.' },
}
const ENTITY_ORDER = ['agent', 'model', 'search', 'channel', 'inbox', 'notification', 'tool', 'task', 'action', 'skills', 'knowledge', 'memory', 'prompt', 'workflow', 'sync']

/** How often the panel re-reads a list that still holds a `checking` answer. The gateway serves
 *  those answers from memory, so this polls a cache — it never re-runs a check. */
const CHECKING_POLL_MS = 2500

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
  // 🔴 THIS READ MUST REJECT — IT IS THE ONE THE ZERO-CARD RENDER MAKES A CLAIM ABOUT.
  // It used to end `.catch(() => [] as SettingsProvider[])`, and `[]` is TRUTHY, so the
  // `if (!providers) return <ProvidersSkeleton />` gate below could never fire on a failure: the
  // page rendered its header over zero cards, which reads as a confident "you have no providers
  // configured" produced by a request that never landed.
  //
  // The three reads below KEEP their `.catch` deliberately — the same split
  // `pages/agents/agentsData.ts` makes, and its rail records the reasoning: the read an empty
  // state makes a claim about has to propagate, while enrichment reads (runtime readiness, model
  // catalogs, live channel health) stay tolerant so one dead subsystem renders as its own unready
  // card instead of taking the whole page down.
  const { data: providers, status: providersStatus, error: providersError, refresh: refreshProviders } = useQuery(
    'settings:providers', () => api.settingsProviders(), { persist: true },
  )
  const { data: runtimesData, refresh: refreshRuntimes } = useQuery(
    'settings:agent-runtimes', () => api.agentRuntimes().catch(() => [] as AgentRuntime[]), { persist: true },
  )
  const runtimes = runtimesData ?? []
  // Available models per provider — feeds each local-model provider's download card
  // (catalog + downloaded state + `searchable`). One fetch, revalidated on mutation.
  const { data: availableData, refresh: refreshAvailable } = useQuery(
    'settings:models-available', () => api.modelsAvailable().catch(() => [] as ProviderModels[]), { persist: true },
  )
  const availableByProvider = useMemo(() => {
    const m = new Map<string, ProviderModels>()
    for (const r of availableData ?? []) m.set(r.name, r)
    return m
  }, [availableData])
  // Live channel runtime (connection health) — folded onto the matching channel
  // provider card so the enable/config surface also shows whether it's connected now.
  const { data: channelsData, refresh: refreshChannels } = useQuery(
    'settings:channels', () => api.channels().catch(() => [] as ChannelRuntime[]), { persist: true },
  )
  // The channel *provider* is the app (`slack-channel`) and the channel *runtime* its transport
  // (`slack`): the gateway names the app each channel came from, so the card finds its channel by
  // that rather than by guessing from the app's name.
  const channelByApp = useMemo(() => {
    const m = new Map<string, ChannelRuntime>()
    for (const c of channelsData ?? []) if (c.app) m.set(c.app, c)
    return m
  }, [channelsData])
  // A mutation (enable/disable/config) invalidates the cached catalog so the next
  // read revalidates against the changed state instead of a stale snapshot. The channel
  // runtime too: enabling, disabling or saving a channel starts or stops its receiver, and
  // the row kept showing the status from before the click until the page was reloaded.
  const reload = () => {
    // Prefix mode: `settings:channels-owners` (the chat's "Continue on" list and the Configure
    // page's owner section) reads the same channels without this page's catch.
    invalidateKeys('settings:providers'); invalidateKeys('settings:models-available'); invalidateKeys('settings:channels', true)
    refreshProviders(); refreshRuntimes(); refreshAvailable(); refreshChannels()
  }

  // The card's Test — the ONE thing on this page that starts an agent CLI, and only the one
  // whose card was pressed. Reading the runtimes starts nothing: each answers from whether its
  // CLI is installed and from its last Test. After a Test every reader of the list re-reads it.
  const testRuntime = async (who: string, runtime: string) => {
    if (await reportingWrite(`test ${who}`, () => api.testAgentRuntime(runtime))) {
      invalidateKeys('settings:agent-runtimes')
    }
  }

  // A card the gateway has not measured yet reads `checking` (availability is measured in a
  // child process). Re-read until each has its answer; nothing polls once no card is left
  // checking. The reads start nothing.
  const providersChecking = (providers ?? []).some((p) => p.availability?.state === 'checking')
  useVisiblePoll(() => { if (providersChecking) refreshProviders() }, providersChecking ? CHECKING_POLL_MS : null)
  // Same for a channel whose receiver the gateway is starting: re-read until it says how that went.
  const channelsStarting = (channelsData ?? []).some((c) => c.health?.state === 'starting')
  useVisiblePoll(() => { if (channelsStarting) refreshChannels() }, channelsStarting ? CHECKING_POLL_MS : null)

  // 🔑 THE FAILURE BRANCH COMES FIRST, and it has to: `providers` is `undefined` both while
  // loading and after a rejection, so the skeleton below would otherwise claim "still loading"
  // forever on a dead read — the same never-resolving state the `useQuery` docstring warns about.
  if (providersStatus === 'error') {
    return <LoadError what="provider settings" error={providersError} onRetry={refreshProviders} />
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

  // Sign-in runs the CLI's own login in the terminal, which the user drives. Whether it worked is
  // what the card's Test then says: nothing re-starts the CLI behind the sign-in to find out.
  const onSignIn = (rt: AgentRuntime) => {
    if (rt.login_command?.length) requestRunInTerminal(rt.login_command.join(' '))
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
              <ProviderCard key={ext.name} ext={ext} runtime={runtimeByExt.get(ext.name)} open={openProvider === ext.name} onOpenChange={openCfg(ext.name)} onChanged={reload} onSignIn={onSignIn}
                onTest={(rt) => testRuntime(ext.displayName || ext.name, rt.provider_id || rt.name)} />
            ))}

            {type === 'model' && <ModelEntitySection exts={exts} availableByProvider={availableByProvider} openProvider={openProvider} openCfg={openCfg} onChanged={reload} />}

            {type === 'action' && <ActionGroups exts={exts} openProvider={openProvider} openCfg={openCfg} onChanged={reload} />}

            {/* every other entity: a multiInstance provider is an instance frame
                (MCP/OpenAI tools, …); a singleton is a plain toggle+config card. */}
            {type !== 'agent' && type !== 'model' && type !== 'action' && exts.map((ext) => (
              ext.provider?.multiInstance
                ? <MultiInstanceCard key={ext.name} ext={ext} onChanged={reload} />
                : <ProviderCard key={ext.name} ext={ext} channel={type === 'channel' ? channelByApp.get(ext.name) : undefined}
                    open={openProvider === ext.name} onOpenChange={openCfg(ext.name)} onChanged={reload} onChannelChanged={() => invalidateKeys('settings:channels', true)} />
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
        <section key={s} className="mb-2xl" role="status" aria-busy="true" >
        <LoadingStatus what="providers" />
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
  // 🔴 This hand-rolled what `Section` provides, and two rails caught it in sequence. First
  // `#/settings/providers` reported `h1 → h3` (axe heading-order, both themes and phone): the row
  // rendered an `h3` under `PanelHeader`'s `h1`. Changing the tag alone then tripped
  // `sectionHeadingScale`, whose recorded standard is that settings panels do NOT write their own
  // section titles — its detector had simply never matched this one, because it keys on `h2` and this
  // outlier used `h3`. A level fix made the hand-roll visible.
  //
  // 🔑 So this adopts the primitive, which is what that rail's own conclusion prescribes: "a primitive
  // that the majority uses and the outliers cannot is missing a slot, not being ignored" — and `Section`
  // already grew the three slots this header needs (`icon`, a ReactNode `hint`, and `right` for the
  // count). The count moves from beside the label to the row's right edge and the glyph takes the
  // primitive's `text-primary` tint; that is what adopting it looks like, and the diff shows it.
  return (
    <Section iconTone="muted" icon={Icon} hint={hint}
      title={<>{label}<span data-type="caption" className="ml-2 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low tabular-nums">{count}</span></>}>
      <div className="flex flex-col gap-2">{children}</div>
    </Section>
  )
}

// Threaded from ProvidersPanel so nested ProviderCards share the one URL-backed
// single-open accordion (?open=<provider>).
type OpenCfg = { openProvider: string; openCfg: (name: string) => (v: boolean) => void }

/** Model entity: the BUNDLED local downloadable providers (each with a uniform
 *  download-management card) + every configured instance. "Local" is the `local: true` flag
 *  on the /api/models/available card — the one signal from the core local-model registry (no
 *  hardcoded names). Every local provider renders the SAME LocalModelManager card, so the
 *  download UX is uniform.
 *
 *  A local provider that is a configured INSTANCE (an Ollama endpoint — no bundled ext of its
 *  own) is not rendered here: it is an instance like any other, so it renders in the instance
 *  list with its Test, Edit and Remove, and its download card inside it. Rendering it here, as
 *  this used to, left it with none of the three. */
function ModelEntitySection({ exts, availableByProvider, openProvider, openCfg, onChanged }: {
  exts: SettingsProvider[]; availableByProvider: Map<string, ProviderModels>; onChanged: () => void
} & OpenCfg) {
  const extByName = new Map(exts.map((e) => [e.name, e]))
  // A bundled local provider is a registry card flagged local whose name is one of these exts;
  // a local card with no ext of its own is a configured instance (see above).
  const localCards = [...availableByProvider.values()].flatMap((av) => {
    const ext = av.local ? extByName.get(av.name) : undefined
    return ext ? [{ av, ext }] : []
  })
  const otherNative = exts.filter((e) => !availableByProvider.get(e.name)?.local && !e.provider?.multiInstance)
  return (
    <div className="flex flex-col gap-4">
      {(localCards.length > 0 || otherNative.length > 0) && (
        <div>
          <div data-type="caption" className="mb-2 text-on-surface-low uppercase tracking-wide">Native (bundled)</div>
          <div className="flex flex-col gap-2">
            {localCards.map(({ av, ext }) => (
              <div key={av.name}>
                <ProviderCard ext={ext} open={openProvider === ext.name} onOpenChange={openCfg(ext.name)} onChanged={onChanged} />
                {ext.enabled && (
                  <div className="mt-2 pl-4">
                    <LocalModelManager provider={av.name} models={av.models ?? []} searchable={av.searchable} error={av.error} onChanged={onChanged} />
                  </div>
                )}
              </div>
            ))}
            {otherNative.map((ext) => <ProviderCard key={ext.name} ext={ext} open={openProvider === ext.name} onOpenChange={openCfg(ext.name)} onChanged={onChanged} />)}
          </div>
        </div>
      )}
      <div>
        <div data-type="caption" className="mb-2 text-on-surface-low uppercase tracking-wide">Instances</div>
        <RemoteModelProviders onChanged={onChanged} />
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
          <div data-type="caption" className="mb-2 text-on-surface-low uppercase tracking-wide">{ACTION_ENTITY_LABELS[entity] ?? 'Other actions'}</div>
          <div className="flex flex-col gap-2">
            {(byEntity.get(entity) ?? []).map((ext) => <ProviderCard key={ext.name} ext={ext} open={openProvider === ext.name} onOpenChange={openCfg(ext.name)} onChanged={onChanged} />)}
          </div>
        </div>
      ))}
    </div>
  )
}
