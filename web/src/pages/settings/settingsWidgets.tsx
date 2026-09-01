import {
  User, Palette, MessageSquare, Plug, Cpu, FileText, Database, Bot, AudioLines,
  Inbox, Bell, Shield, ShieldAlert, ScrollText, Archive, FolderSync, DownloadCloud, CheckCircle2, Search, Blocks, Activity, Compass, Stethoscope, Scissors, ThumbsUp, HardDriveDownload, Coins, Route, Trophy,
  MonitorSmartphone, Plug2, FileType2, LayoutDashboard, Smartphone, Rss, Package, FlaskConical, KeyRound,
  MessageCircle,
} from 'lucide-react'
import { verifiedScope } from './AuditPanel'
import type { LucideIcon } from 'lucide-react'
import { notify } from '../../app/appSdk'
import {
  api, type SecurityStats, type SecretsVaultState, type MemoryStats, type AgentRuntime, type DashboardConfig,
  type SettingsProvider, type NotificationSettings, type UpdateCheck,
  type PromptBindings, type SelVerify, type SavedAgent,
  type SearchProviderInfo,
  type ToolsSavings, type DeviceRec, type InstalledPackRec, type ChannelTrust,
} from '../../lib/api'
// One spelling for a poll cadence: `#/knowledge/sources` renders every source row's cadence
// through THIS function (`SourcesPage.tsx:177`, `· every {fmtInterval(poll_interval_secs)}`), and
// the number this tile shows is the DEFAULT those rows fall back to. A second formatter here would
// print the same seconds two ways on two surfaces one click apart — the shape `lib/epoch.test.ts`
// already rules for relative time. Reused, not re-derived.
import { fmtInterval } from '../knowledge/sourceMeta'
import { useQuery, invalidateSpecs, type CacheKeySpec } from '../../lib/data'
import { useIdentity } from '../../app/identity'
import { useAppearance } from '../../app/appearance'
import { useMode } from '../../app/theme'
import {
  BentoCard, BigStat, KVList, StatusPill, ChipRow, Highlight,
  Switch, SegToggle, InlineSelect, type BentoSize,
} from './bento'
import { fvs } from '../../design/fontWeight'

/** A settings widget: surfaces its subpage's most essential info on a bento card,
 *  deep-links into the subpage on click, contributes its data to the search index
 *  (`searchText`), and highlights query matches in its rendered body. */
export interface SettingsWidget {
  id: string
  group: string
  label: string
  icon: LucideIcon
  description: string
  size: BentoSize
  /** A React hook returning the text blob this card surfaces (for search match +
   *  highlight). Returns '' while loading. Each widget owns its own cached fetch. */
  useSearchText: () => string
  /** Render the card. `query` drives highlight; `go` opens the subpage. */
  render: (query: string, go: (id: string) => void) => React.ReactNode
}

const shortModel = (ref: string) => { const i = ref.indexOf(':'); return i >= 0 ? ref.slice(i + 1) : ref }

// ─────────────────────────────────────────────────────────────────────────────
// Per-subpage data hooks (cache keys mirror each panel so paint is shared/instant)
// ─────────────────────────────────────────────────────────────────────────────
const useSecurity = () => useQuery('settings:security', () => api.securityStats().catch(() => null as SecurityStats | null), { persist: true })
// A DISTINCT cache key from the panel's `settings:secrets`, deliberately. This card swallows a
// failed read (a bento card that cannot load simply does not render its body), and the panel's read
// is BARE so it can tell "no secrets" from "the fetch failed". Sharing one key would let this
// card's `null` land in the panel, which would then shimmer forever with no error to show — the
// exact dishonesty `panelReadHonestyTail` exists to catch.
const useSecretsVault = () => useQuery('settings:secrets-card', () => api.secrets().catch(() => null as SecretsVaultState | null), { persist: true })
const useMemoryStats = () => useQuery('settings:memory-stats', () => api.memoryStats().catch(() => null as MemoryStats | null), { persist: true })
// Today's spend for the Usage bento tile (COST-AND-TOKEN-OBSERVABILITY). Midnight-UTC
// window matches the Usage panel's "Today"; a null means the ledger read failed.
const useUsageToday = () => useQuery('settings:usage-today', () => {
  const since = `${new Date().toISOString().slice(0, 10)}T00:00:00+00:00`
  return api.usageTotals({ since }).then((d) => d.totals).catch(() => null)
}, { persist: false })
const useModelsActive = () => useQuery('settings:models-active', () => api.modelsActive().catch(() => null as Record<string, string[]> | null), { persist: true })
// Routing efficiency for the default (chat, short_chat) bucket — the card's headline
// is how many models are on the Pareto frontier there; deep-links into the subpage,
// which lets the user pick any bucket. null on read failure (distinct from []=no data).
// 🔴 THIS CARD COULD NEVER FILL. It asked for `use_case: 'chat'`, and chat is the one axis routing
// telemetry is never recorded for: the fold lives in `ModelCallGuard._audit`, `provider_bridge` applies
// that guard only when `_guard_use_case` is set, and that happens for exactly
// ("reasoning", "background", "loops", "orchestration") — "The interactive chat/code_tools stream stays
// OUT OF SCOPE … both human-watched", in the bridge's own words. So the Settings home showed a
// permanently empty "Routing & Efficiency" card to every user, under copy promising the numbers would
// "land here as models handle work".
//
// Reading it as an oversight rather than a choice: the cache key and params mirror `RoutingPanel`'s
// DEFAULT tab, and this card's own description says "for each kind of request" — plural — while it
// queried exactly one kind that has no data. So it now asks for the axis the panel itself maps to
// (`reasoning` → `long_reasoning`), which is a measured one, and the empty copy says what is measured.
const useRoutingTelemetry = () => useQuery('settings:routing-telemetry:reasoning:long_reasoning',
  () => api.modelsTelemetry({ use_case: 'reasoning', query_class: 'long_reasoning' }).then((d) => d.rows).catch(() => null), { persist: false })
const useSearchEntity = () => useQuery('settings:search', async () => {
  const [providers, active] = await Promise.all([
    api.searchProviders().catch(() => [] as SearchProviderInfo[]),
    api.searchActive().catch(() => ({} as Record<string, string[]>)),
  ])
  return { providers, active }
}, { persist: true })
const useRuntimes = () => useQuery('settings:agent-runtimes', () => api.agentRuntimes().catch(() => null as AgentRuntime[] | null), { persist: true })
const useProviders = () => useQuery('settings:providers', () => api.settingsProviders().catch(() => [] as SettingsProvider[]), { persist: true })
const useDashCfg = () => useQuery('settings:dashboard-config', () => api.dashboardConfig().catch(() => null as DashboardConfig | null), { persist: true })
// The swallow here is what POISONED the shared `'settings:inbox'` key: it resolved with `null`, which the
// hook then persisted, so both inbox-settings panels seeded `null` from cache and read it as loaded.
const useInbox = () => useQuery('settings:inbox', () => api.inboxSettings(), { persist: true })
// 🔴 NO `.catch(() => [])` HERE EITHER, and the reason is subtler than one surface's empty state:
// `useQuery` caches by KEY, and this hook shares the `'apps'` key with `#/apps`. Swallowing the
// rejection made this call RESOLVE with `[]`, which the hook then persisted to sessionStorage — so
// `#/apps` read `[]` as a successful value and its `data === undefined && error` branch could never
// fire, even after that surface stopped swallowing. Measured: `{appsUndef: false, appsErr: ApiError,
// n: 0}` on the failing render. One swallowing caller defeats every other consumer of the same key.
const useApps = () => useQuery('apps', () => api.apps(), { persist: true })
const useNotif = () => useQuery('settings:notification-settings', () => api.notificationSettings().catch(() => null as NotificationSettings | null), { persist: true })
const useUpdates = () => useQuery('settings:update-check', () => api.updateCheck().catch(() => null as UpdateCheck | null), { persist: true })
const usePromptBindings = () => useQuery('settings:prompt-bindings', () => api.promptBindings().catch(() => null as PromptBindings | null), { persist: true })
const useDurability = () => useQuery('settings:durability-card', async () => {
  const [status, snaps] = await Promise.all([
    api.durabilityStatus().catch(() => null),
    api.durabilityArchive().catch(() => null),
  ])
  return { status, snaps }
}, { persist: true })
// 🔴 SAME KEY-POISONING SHAPE AS `'apps'` ABOVE, and it made a fix on another surface INERT.
// `#/settings/archive` now branches on the load error — but this tile shares its key, so while this
// caller swallowed, the hub primed `cache:settings:archives` with `[]` and the panel read a success.
// A cold-cache probe missed it entirely (navigating straight to the panel never runs this hook); the
// key-consumer rail in `ui/loadErrorState.test.tsx` is what caught it.
// Consequence, stated: on failure this tile now stays in its `loading` shimmer instead of claiming
// "0 archived sessions". Every hub tile turns a failure into a permanent shimmer — one idiom, ~30
// tiles, logged as its own family rather than fixed inside this change.
const useArchives = () => useQuery('settings:archives', () => api.sessionArchives(), { persist: true })
const useAudit = () => useQuery('settings:audit-verify', () => api.auditVerify().catch(() => null as SelVerify | null), { persist: false })
const useLogLevel = () => useQuery('settings:log-level', () => api.logLevel().catch(() => null as string | null), { persist: true }).data
const useVoice = () => useQuery('settings:voice', async () => {
  const [active, stt, tts] = await Promise.all([
    api.modelsActive().catch(() => ({} as Record<string, string[]>)),
    api.useCaseSettings('stt').catch(() => ({} as Record<string, unknown>)),
    api.useCaseSettings('tts').catch(() => ({} as Record<string, unknown>)),
  ])
  return { active, stt, tts }
}, { persist: true })
// 🔴 KEY POISONING, MEASURED ON THE JOURNEY. `#/settings/legibility` already refuses to show fabricated
// values — but this tile shares its key, so opening `#/settings` first primed `cache:settings:legibility`
// with `{}` and the panel read it as a success. Driven with `/api/config/personalclaw` at 500:
//
//   direct to the panel   cache=null   → "Couldn't load your settings" + Retry   ✅ its fix works
//   hub → the panel       cache="{}"   → **2 switches, no alert**                🔴 its fix was inert
//
// The tile can now fail, so it carries the failure line the other four tiles got in #1194.
const useLegibility = () => useQuery('settings:legibility', () =>
  api.personalclawConfig().then((c) => (c.legibility ?? {}) as Record<string, unknown>), { persist: true })
// Shares its key with `EvalsPanel`, so the hub tile and the panel are one read. NO `.catch` here
// either, for the reason the legibility key records below: this tile carries a live SWITCH, and a
// substituted `{}` would render its "off" position as saved state on a config that never loaded.
const useEvals = () => useQuery('settings:evals', () =>
  api.personalclawConfig().then((c) => (c.evals ?? {}) as Record<string, unknown>), { persist: true })
// The `.catch(() => null)` here resolved the fetcher, so `loading` (`d === undefined`) went false
// and the card rendered `{d && …}` = NOTHING: a blank health card, which on a health surface reads
// as "nothing to report". Let the rejection through and say we could not check.
const useDoctor = () => useQuery('settings:doctor', () => api.doctor(), { persist: false })
// Same shape as the doctor tile above, on a SAFETY control: the swallowed rejection resolved to
// `null`, so the card stopped "loading" and rendered nothing at all — no "Normal operation", no
// incident pill. On the one card that says whether unattended work is suspended, blank is not an
// answer. (`toolsSavings` below keeps its catch: a missing SAVINGS number is genuinely "no data".)
const useIncident = () => useQuery('settings:incident', () => api.incident(), { persist: true })
// `persist: false` — this reports which network surfaces are reachable right now. A
// cached "no inbound access" surviving a restart is the one wrong answer that matters.
const useExternalAccess = () =>
  useQuery('settings:external-access', () => api.externalAccess(), { persist: false })
// A SEPARATE key from the panel's `settings:devices` (like `settings:durability-card`): this one
// swallows a read failure into `null` so the card can say "couldn't check", while the panel needs
// the raw error to render `LoadError`. One key with two fetchers would make which behaviour you get
// depend on which surface mounted first.
const useDevices = () => useQuery('settings:devices-card',
  () => api.devices().catch(() => null as DeviceRec[] | null), { persist: true })
// Same split as `devices` above: a SEPARATE key from the panel's `settings:sender-trust`,
// because this one swallows a read failure into `null` so the card can say "couldn't check"
// while the panel needs the raw error for `LoadError`.
const useSenderTrust = () => useQuery('settings:sender-trust-card',
  () => api.channelTrust().catch(() => null as ChannelTrust | null), { persist: true })
// Same story: `#/settings/tool-output` reads the error now, and this tile shares its key.
const useProjectionRules = () => useQuery('settings:projection-rules', () => api.projectionRules(), { persist: true })
const useToolsSavings = () => useQuery('settings:tools-savings', () => api.toolsSavings().catch(() => null as ToolsSavings | null), { persist: true })
const useFeedbackProducers = () => useQuery('settings:feedback-producers', () => api.feedbackProducers().catch(() => null), { persist: false })
const useAgentDefaults = () => useQuery('settings:agent-defaults', async () => {
  const [cfg, agents] = await Promise.all([
    api.personalclawConfig().then((c) => (c.agent ?? {}) as Record<string, unknown>).catch(() => ({} as Record<string, unknown>)),
    api.agents().then((a) => a.default_agent).catch(() => ''),
  ])
  return { cfg, defaultAgent: agents }
}, { persist: true })
// ── The four subpages that had no card at all (see the block comment at their widgets) ───────
//
// FIVE reads for four tiles, and the rule per read is "match the PANEL exactly, or take a key of
// your own" — never a third thing. Where a tile shares the panel's key its fetcher is BYTE-IDENTICAL,
// for the two reasons this file already records: the hub tile and the panel become one read, and —
// the sharper one — a DIFFERENT swallow here would prime the shared key with a substitute the panel
// then reads as a success, making its `LoadError` branch unreachable
// (`configReadNotFabricated.test.ts`, the legibility tile: `direct → the alert` but
// `hub → the panel → 2 switches, no alert`). Measured against the four panels, the split is:
//
//   settings:ambient             shared, no `.catch`   AmbientPanel's fetcher, verbatim
//   settings:packs               shared, no `.catch`   PacksPanel's config read, verbatim
//   settings:packs:installed     shared, WITH `.catch` PacksPanel's ledger read, verbatim — see below
//   settings:companion:discovery shared, no `.catch`   CompanionPanel's advertiser read, verbatim
//   settings:sources-card        SEPARATE key          the panel's is a composite — see below
//
// So the honesty claim is per-key, not a blanket "no `.catch`": three of the four shared keys carry
// no substitute and their tiles render a failure line instead, and the fourth copies the panel's own
// swallow rather than diverging from it.
//
// ✅ DRIVEN, not just read. With `/api/config/personalclaw` held at 500 and the service worker
// blocked, on the hub→panel journey (the one that defeated legibility's own fix): all four tiles say
// "Couldn't load your … settings" — none shimmers forever, none shows a fabricated value — and
// `#/settings/ambient` and `#/settings/packs` then render `LoadError` with **0 editable controls**,
// so the shared keys are not poisoned. The companion tile is the one that still reports state at
// 500, correctly: it reads no config at all.
const useAmbient = () => useQuery('settings:ambient', () =>
  api.personalclawConfig().then((c) => (c.ambient ?? {}) as Record<string, unknown>), { persist: true })
// 🪤 A SEPARATE KEY FROM THE PANEL'S, deliberately — the `settings:devices-card` precedent, for the
// reason stated there: one key with two fetchers makes which SHAPE you get depend on which surface
// mounted first. `SourcesPanel`'s `settings:sources` is a COMPOSITE (`{ sources, scratchpadPath,
// knowledge }`, three config sections in one read, because the panel edits all three), and this tile
// needs only `sources.*`. Sharing the key would have this tile read `data.enabled` off an object
// that has `data.sources.enabled` — undefined, i.e. "Parked", on every hub visit that followed a
// panel visit. Same namespace, so the freshness policy is the one `lib/data/keys.ts` declares.
const useSourcesCfg = () => useQuery('settings:sources-card', () =>
  api.personalclawConfig().then((c) => (c.sources ?? {}) as Record<string, unknown>), { persist: true })
const usePacksCfg = () => useQuery('settings:packs', () =>
  api.personalclawConfig().then((c) => (c.packs ?? {}) as Record<string, unknown>), { persist: true })
// The installed ledger, byte-identical to `PacksPanel`'s read — including its `.catch`, which is
// what makes the key safe to share. Keeping the swallow means a failed ledger read shows `0` here
// exactly as the panel shows "No packs installed yet"; de-swallowing it is the panel's fix to make,
// not something to do from the hub in half.
const usePacksInstalled = () => useQuery('settings:packs:installed', () =>
  api.packsInstalled().catch(() => [] as InstalledPackRec[]), { persist: true })
// The LIVE advertiser, not the flag that requests it — `CompanionPanel`'s own key and fetcher. The
// two legitimately disagree (a loopback-only gateway advertises nothing by design), and a tile that
// showed only `discovery_enabled` would render that disagreement as success. This read is why the
// companion tile needs no config read at all: the record carries the instance name too.
const useCompanionDiscovery = () => useQuery('settings:companion:discovery', () => api.companionDiscovery())

/** Run an async mutation, then invalidate the widget's cache key(s) so its data re-reads the new
 *  value — so a REJECTED write reconciles: the control snaps back to the server's answer.
 *
 *  That reconcile was already right, and it was also the whole story, which is the defect this
 *  replaces: the toggle flipped back on its own with nothing said, which reads as a glitchy UI
 *  rather than as a write the server refused. Reconciling is not the same as reporting. The
 *  server's own message carries the reason (it usually names the field), so no per-tile copy is
 *  invented here — the same funnel every other failed action in the app uses. */
async function mutate(fn: () => Promise<unknown>, ...affects: CacheKeySpec[]) {
  try {
    await fn()
  } catch (e) {
    notify(`Couldn't save that change: ${String((e as Error)?.message || e)}`, 'error')
  }
  // Runs on both paths on purpose: after a failure the re-read is what makes the control honest.
  //
  // `invalidateSpecs` is the one data layer's entry point, so the busted keys reach every
  // MOUNTED reader — not just the widget that saved. Before the layer existed each tile called
  // its own `refresh()` beside its bust, so a second surface reading the same config kept
  // painting the pre-save value until its own next mount.
  invalidateSpecs(affects)
}

// ─────────────────────────────────────────────────────────────────────────────
// The widgets (working backward from each subpage's most critical info)
// ─────────────────────────────────────────────────────────────────────────────
export const SETTINGS_WIDGETS: SettingsWidget[] = [
  // ── General ──────────────────────────────────────────────────────────────
  {
    id: 'account', group: 'General', label: 'Account', icon: User, size: 'sm',
    description: 'Your name and onboarding.',
    useSearchText() { const { name } = useIdentity(); return `account name ${name ?? ''}` },
    render(query, go) {
      const { name } = useIdentity()
      return (
        <BentoCard icon={User} title="Account" query={query} onClick={() => go('account')}>
          <div className="truncate text-on-surface text-[1.0625rem]" style={fvs(550)}>{name || 'PersonalClaw'}</div>
          <div className="text-on-surface-low text-[0.75rem]">Display name &amp; onboarding</div>
        </BentoCard>
      )
    },
  },
  {
    id: 'design', group: 'General', label: 'Design', icon: Palette, size: 'sm',
    description: 'Theme, accent, typography, and surface tokens.',
    useSearchText() { const { activeScheme, allSchemes } = useAppearance(); const { preference } = useMode(); const sc = allSchemes.find((s) => s.id === activeScheme); return `design theme appearance color accent typography scheme ${sc?.label ?? activeScheme} ${preference} mode` },
    render(query, go) {
      const { activeScheme, allSchemes } = useAppearance()
      const { preference, mode, setPreference } = useMode()
      const dark = mode === 'dark'
      const scheme = allSchemes.find((s) => s.id === activeScheme)
      // A few representative token colors from the active scheme for the swatch.
      const tokens = ['--color-primary', '--color-secondary', '--color-surface-high']
      const dots = scheme ? tokens.map((t) => scheme.colors[t]?.[dark ? 'dark' : 'light']).filter(Boolean) as string[] : []
      const label = scheme?.label || activeScheme
      return (
        <BentoCard icon={Palette} title="Design" query={query} onClick={() => go('design')}>
          <div className="flex items-center gap-2">
            <div className="flex -space-x-1">
              {(dots.length ? dots : [scheme?.swatch[dark ? 'dark' : 'light'] || '#ff6b5b']).map((c, i) => (
                <span key={i} className="size-4 rounded-full border border-outline-variant/40" style={{ background: c }} />
              ))}
            </div>
            <span className="truncate text-on-surface text-[0.8125rem]">{query ? <Highlight text={label} query={query} /> : label}</span>
          </div>
          {/* Mode is an inline choice; full theme/token editing lives in the subpage. */}
          <div className="mt-2.5 flex items-center justify-between gap-2">
            <span className="text-on-surface-low text-[0.75rem]">Mode</span>
            <SegToggle value={preference} onPick={(p) => setPreference(p)} ariaLabel="Mode"
              options={[{ key: 'light', label: 'Light' }, { key: 'dark', label: 'Dark' }, { key: 'auto', label: 'Auto' }]} />
          </div>
        </BentoCard>
      )
    },
  },
  {
    id: 'chat', group: 'General', label: 'Chat', icon: MessageSquare, size: 'md',
    description: 'Message behavior, history, and session preferences.',
    useSearchText() { const { data } = useDashCfg(); const c = data; return `chat message session restore history send enter timestamps ${c ? `restore ${c.restore_sessions} send-on-enter ${c.send_on_enter} timestamps ${c.show_timestamps} density ${c.widget_density}` : ''}` },
    render(query, go) {
      const { data: c, refresh, stale: cStale } = useDashCfg()
      const save = (patch: Record<string, unknown>) => mutate(
        () => api.saveDashboardConfig(patch).then(refresh), 'settings:dashboard-config',
      )
      return (
        <BentoCard icon={MessageSquare} title="Chat" query={query} onClick={() => go('chat')} loading={c === undefined} rows={4} stale={cStale}>
          {c && <KVList query={query} rows={[
            { k: 'Restore sessions', control: true, v: <Switch on={c.restore_sessions} label="Restore sessions" onToggle={(v) => save({ restore_sessions: v })} /> },
            { k: 'Send on Enter', control: true, v: <Switch on={c.send_on_enter} label="Send on Enter" onToggle={(v) => save({ send_on_enter: v })} /> },
            { k: 'Timestamps', control: true, v: <Switch on={c.show_timestamps} label="Timestamps" onToggle={(v) => save({ show_timestamps: v })} /> },
            { k: 'Density', control: true, v: <SegToggle value={c.widget_density} onPick={(v) => save({ widget_density: v })} ariaLabel="Density"
              options={[{ key: 'more', label: 'Comfortable' }, { key: 'less', label: 'Compact' }]} /> },
          ]} />}
        </BentoCard>
      )
    },
  },
  // ── AI & Models ──────────────────────────────────────────────────────────
  {
    id: 'providers', group: 'AI & Models', label: 'Providers', icon: Plug, size: 'lg',
    description: 'Model backends and capability providers; credentials and runtimes.',
    useSearchText() {
      const { data: provs } = useProviders(); const { data: rt } = useRuntimes()
      const enabled = (provs ?? []).filter((p) => p.enabled).map((p) => p.name).join(' ')
      const runtimes = (rt ?? []).map((r) => r.name.replace(/^acp:/, '')).join(' ')
      return `providers backends credentials runtimes enabled ${enabled} ${runtimes}`
    },
    render(query, go) {
      const { data: provs, stale: provsStale } = useProviders(); const { data: rt } = useRuntimes()
      const enabled = (provs ?? []).filter((p) => p.enabled)
      const ready = (rt ?? []).filter((r) => r.ready).length
      return (
        <BentoCard icon={Plug} title="Providers" query={query} onClick={() => go('providers')} loading={provs === undefined} stale={provsStale}>
          <div className="flex items-start justify-between gap-3">
            <BigStat value={enabled.length} caption="enabled" />
            {rt && <BigStat value={`${ready}/${rt.length}`} caption="runtimes ready" tone={ready ? 'var(--color-ok)' : undefined} />}
          </div>
          {rt && rt.length > 0 && (
            <div className="mt-2.5">
              <ChipRow query={query} chips={rt.map((r) => ({ label: r.name.replace(/^acp:/, ''), tone: r.ready ? 'ok' : 'warn' }))} />
            </div>
          )}
        </BentoCard>
      )
    },
  },
  {
    id: 'models', group: 'AI & Models', label: 'Models', icon: Cpu, size: 'md',
    description: 'Which model serves each use case (chat, embeddings, voice).',
    useSearchText() {
      const { data: a } = useModelsActive()
      const parts = ['chat', 'embedding', 'stt', 'tts'].map((uc) => `${uc} ${(a?.[uc] ?? []).map(shortModel).join(' ')}`)
      return `models bindings use case ${parts.join(' ')}`
    },
    render(query, go) {
      const { data: active, stale: activeStale } = useModelsActive()
      const CORE = [['chat', 'Chat'], ['embedding', 'Embed'], ['stt', 'STT'], ['tts', 'TTS']] as const
      return (
        <BentoCard icon={Cpu} title="Models" query={query} onClick={() => go('models')} loading={active === undefined} stale={activeStale}>
          {active && <KVList query={query} rows={CORE.map(([uc, label]) => {
            const bound = (active[uc] ?? [])[0]
            return { k: label, mono: true, vText: bound ? shortModel(bound) : '—', v: bound
              ? <span className="inline-flex items-center gap-1"><CheckCircle2 size={11} className="shrink-0 text-ok" /> <span className="truncate">{shortModel(bound)}</span></span>
              : <span className="text-on-surface-low">—</span> }
          })} />}
        </BentoCard>
      )
    },
  },
  {
    id: 'routing', group: 'AI & Models', label: 'Routing & Efficiency', icon: Route, size: 'sm',
    description: 'Per-model efficiency for each kind of request — success, latency, cost — and which models are on the Pareto frontier. Observation only.',
    useSearchText() {
      const { data } = useRoutingTelemetry()
      const frontier = (data ?? []).filter((r) => r.on_frontier).length
      return `routing efficiency telemetry pareto frontier model latency cost success p50 p95 ${data ? `${data.length} models ${frontier} frontier` : ''}`
    },
    render(query, go) {
      const { data, stale: isStalePaint } = useRoutingTelemetry()
      const frontier = (data ?? []).filter((r) => r.on_frontier).length
      return (
        <BentoCard icon={Route} title="Routing & Efficiency" query={query} onClick={() => go('routing')} loading={data === undefined} stale={isStalePaint}>
          {data === null || (data && data.length === 0)
            ? <div className="text-on-surface-low text-[0.8125rem]">Per-model success, latency, and cost land here as unattended work runs — reasoning, background, loops and orchestration — showing which is most efficient.</div>
            : data && <><BigStat value={data.length} caption={data.length === 1 ? 'model measured' : 'models measured'} />
                <div className="mt-1 inline-flex items-center gap-1 text-on-surface-low text-[0.8125rem]">
                  <Trophy size={11} className="text-ok" /> {frontier} on the frontier
                </div></>}
        </BentoCard>
      )
    },
  },
  {
    id: 'search', group: 'AI & Models', label: 'Search', icon: Search, size: 'md',
    description: 'Which search provider serves each web search use case.',
    useSearchText() {
      const { data } = useSearchEntity()
      const provs = (data?.providers ?? []).map((p) => p.name).join(' ')
      const binds = Object.entries(data?.active ?? {}).map(([uc, names]) => `${uc} ${(names ?? []).join(' ')}`).join(' ')
      return `search web provider use case duckduckgo tavily searxng exa perplexity brave ${provs} ${binds}`
    },
    render(query, go) {
      const { data, stale: isStalePaint } = useSearchEntity()
      const USE_CASES = [['search-general', 'General'], ['search-news', 'News'], ['fetch-article', 'Fetch']] as const
      const active = data?.active
      return (
        <BentoCard icon={Search} title="Search" query={query} onClick={() => go('search')} loading={data === undefined} stale={isStalePaint}>
          {data && (data.providers.length === 0
            ? <div className="text-on-surface-low text-[0.8125rem]">DuckDuckGo (keyless) is the default; add a provider in Providers to upgrade.</div>
            : <KVList query={query} rows={USE_CASES.map(([uc, label]) => {
                const bound = (active?.[uc] ?? [])[0]
                return { k: label, mono: false, vText: bound ?? 'General', v: bound
                  ? <span className="inline-flex items-center gap-1"><CheckCircle2 size={11} className="shrink-0 text-ok" /> <span className="truncate">{bound}</span></span>
                  : <span className="text-on-surface-low">— falls back</span> }
              })} />)}
        </BentoCard>
      )
    },
  },
  {
    id: 'prompts', group: 'AI & Models', label: 'Prompts', icon: FileText, size: 'md',
    description: 'Which system prompt serves each context.',
    useSearchText() {
      const { data: b } = usePromptBindings()
      const names = (b?.bindings ?? []).map((x) => `${x.use_case} ${x.ref || x.effective_ref || 'default'}`).join(' ')
      return `prompts system prompt context binding ${names}`
    },
    render(query, go) {
      const { data: b, stale: bStale } = usePromptBindings()
      const rows = (b?.bindings ?? []).slice(0, 4).map((x) => {
        const name = (x.ref || x.effective_ref || 'Default').replace(/\.md$/, '')
        return { k: x.use_case.replace(/_/g, ' '), v: name, vText: name, mono: false }
      })
      return (
        <BentoCard icon={FileText} title="Prompts" query={query} onClick={() => go('prompts')} loading={b === undefined} stale={bStale}>
          {b && (rows.length ? <KVList query={query} rows={rows} /> : <div className="text-on-surface-low text-[0.8125rem]">All contexts use the default prompt.</div>)}
        </BentoCard>
      )
    },
  },
  {
    id: 'evals', group: 'AI & Models', label: 'Evaluations', icon: FlaskConical, size: 'sm',
    description: 'Paired A/B studies over prompt templates, retrieval and judge benchmarks, and monthly ablations — the substrate that says whether a change actually helped.',
    // 🔑 THE HUB IS THE ONLY NAVIGATION. `#/settings` renders `SETTINGS_WIDGETS` and nothing else
    // (`SettingsHome` has no second list), so a subpage without a widget here is reachable only by
    // typing its URL — and invisible to the settings search, which reads `label`/`description`/
    // `useSearchText`. That is why "Evaluations" is spelled out in the search text alongside the
    // "evals" the config key uses: a user searching for either word must find this.
    useSearchText() {
      const { data: e } = useEvals()
      const on = e ? `${e.enabled ? 'on enabled' : 'off disabled'} k ${e.study_default_k} budget ${e.default_budget_usd} agreement ${e.judge_agreement_floor} ablation every ${e.ablation_cadence_days} days` : ''
      return `evals evaluations eval substrate study studies a/b ab test template judge benchmark retrieval benchmark ablation bake-off budget agreement floor ${on}`
    },
    render(query, go) {
      const { data: e, error: evalErr, refresh, stale: eStale } = useEvals()
      const save = (value: boolean) => mutate(
        () => api.patchConfig('evals.enabled', value).then(refresh), 'settings:evals',
      )
      return (
        <BentoCard icon={FlaskConical} title="Evaluations" query={query} onClick={() => go('evals')} loading={e === undefined && !evalErr} stale={eStale}>
          {!e && Boolean(evalErr) && <div className="text-on-surface-low text-[0.75rem]">Couldn&rsquo;t load your evaluation settings.</div>}
          {e && <><KVList query={query} rows={[
            { k: 'Evals enabled', control: true, v: <Switch on={!!e.enabled} label="Evals enabled" onToggle={save} /> },
          ]} />
            {/* Off is the default and the interesting state: it is why `#/learning`'s four eval
                panels are empty, so the tile says where the results would appear rather than
                repeating the switch's own label back at the reader. */}
            <div className="mt-1.5 text-on-surface-low text-[0.75rem]">
              {e.enabled
                ? `k=${Number(e.study_default_k) || 5} per arm · ablation every ${Number(e.ablation_cadence_days) || 30} days`
                : 'Off — the four eval panels on Learning stay empty'}
            </div></>}
        </BentoCard>
      )
    },
  },
  {
    id: 'memory', group: 'AI & Models', label: 'Memory', icon: Database, size: 'md',
    description: 'Semantic + episodic memory, consolidation, and retention.',
    useSearchText() { const { data: m } = useMemoryStats(); return `memory semantic episodic events embedded retention ${m ? `${m.semantic_active} semantic ${m.episodic_active} episodic ${m.embedding_provider ?? ''}` : ''}` },
    render(query, go) {
      const { data: m, stale: mStale } = useMemoryStats()
      return (
        <BentoCard icon={Database} title="Memory" query={query} onClick={() => go('memory')} loading={m === undefined}
          footer={m?.embedding_provider ? <>Embedder: <span className="font-mono text-on-surface-var">{m.embedding_provider}</span></> : undefined} stale={mStale}>
          {m && <div className="flex flex-wrap items-end gap-x-5 gap-y-2">
            <BigStat value={m.semantic_active} caption="semantic" />
            <BigStat value={m.episodic_active} caption="episodic" />
            <BigStat value={m.events_count} caption="events" />
          </div>}
        </BentoCard>
      )
    },
  },
  {
    id: 'agent', group: 'AI & Models', label: 'Agent defaults', icon: Bot, size: 'md',
    description: 'Default agent, approval mode, and execution settings.',
    useSearchText() { const { data } = useAgentDefaults(); const c = data?.cfg ?? {}; return `agent defaults default agent approval sandbox subagents ${data?.defaultAgent ?? ''} ${String(c.approval_mode ?? '')} ${c.yolo ? 'yolo' : ''}` },
    render(query, go) {
      const { data, refresh, stale: isStalePaint } = useAgentDefaults()
      const c = (data?.cfg ?? {}) as Record<string, unknown>
      const approval = String(c.approval_mode ?? 'interactive')
      const setCfg = (key: string, value: unknown) => mutate(
        () => api.patchConfig(`agent.${key}`, value).then(refresh), 'settings:agent-defaults',
      )
      return (
        <BentoCard icon={Bot} title="Agent defaults" query={query} onClick={() => go('agent')} loading={data === undefined} rows={3} stale={isStalePaint}>
          {data && <KVList query={query} rows={[
            { k: 'Default agent', v: data.defaultAgent || '—', vText: data.defaultAgent || '—' },
            { k: 'Approval', control: true, v: <InlineSelect value={approval} ariaLabel="Approval mode" onPick={(v) => setCfg('approval_mode', v)}
              options={[{ value: 'auto', label: 'Auto' }, { value: 'interactive', label: 'Ask each time' }, { value: 'trust_reads', label: 'Trust reads' }]} /> },
            { k: 'YOLO', control: true, v: <Switch on={!!c.yolo} label="YOLO auto-approve all" onToggle={(v) => setCfg('yolo', v)} /> },
          ]} />}
        </BentoCard>
      )
    },
  },
  {
    id: 'voice', group: 'AI & Models', label: 'Speech & Transcription', icon: AudioLines, size: 'sm',
    description: 'Speech-to-text, text-to-speech, and the vocabulary that biases all transcription.',
    useSearchText() { const { data } = useVoice(); const stt = !!data?.stt?.enabled; const tts = !!data?.tts?.enabled; return `voice speech text stt tts streaming speaking speed transcription vocabulary lexicon corrections terms ${stt ? 'stt on' : 'stt off'} ${tts ? 'tts on' : 'tts off'}` },
    render(query, go) {
      const { data, refresh, stale: isStalePaint } = useVoice()
      // Enabling needs a bound model (same gate as the subpage). Without one, the
      // toggle is disabled and the card nudges the user into Speech & Transcription → Models.
      const toggle = (uc: 'stt' | 'tts', settings: Record<string, unknown>, next: boolean) => mutate(
        () => api.saveUseCaseSettings(uc, { ...settings, enabled: next }).then(refresh), 'settings:voice',
      )
      const sttBound = !!(data?.active?.['stt'] ?? [])[0]
      const ttsBound = !!(data?.active?.['tts'] ?? [])[0]
      return (
        <BentoCard icon={AudioLines} title="Speech & Transcription" query={query} onClick={() => go('voice')} loading={data === undefined} rows={2} stale={isStalePaint}>
          {/* 🔴 THE COMMENT ABOVE PROMISED A NUDGE THE MARKUP NEVER RENDERED. With no bound model these
              two rows showed a DISABLED switch and nothing else — `Switch` takes no reason prop, so a
              user (and a screen reader) got "Speech-to-text, dimmed" with no way to learn that a model
              has to be bound first. A dead control is worse than no control, so where nothing is bound
              the row says so instead; the card itself already navigates to Speech & Transcription, which
              is the nudge that was described. */}
          {data && <KVList rows={[
            { k: 'Speech-to-text', control: true, vText: sttBound ? undefined : 'No model bound',
              v: sttBound
                ? <Switch on={!!data.stt?.enabled} label="Speech-to-text" onToggle={(v) => toggle('stt', data.stt ?? {}, v)} />
                : <span className="text-on-surface-low">No model bound</span> },
            { k: 'Text-to-speech', control: true, vText: ttsBound ? undefined : 'No model bound',
              v: ttsBound
                ? <Switch on={!!data.tts?.enabled} label="Text-to-speech" onToggle={(v) => toggle('tts', data.tts ?? {}, v)} />
                : <span className="text-on-surface-low">No model bound</span> },
          ]} />}
        </BentoCard>
      )
    },
  },
  // ── Workspace ──────────────────────────────────────────────────────────────
  {
    id: 'inbox', group: 'Workspace', label: 'Inbox', icon: Inbox, size: 'md',
    description: 'Retention policy and automatic cleanup.',
    useSearchText() { return 'inbox retention auto cleanup' },
    render(query, go) {
      // Alert keywords moved to the notification rules matrix (plan 42 S3), so this card
      // now surfaces what the inbox itself still owns: how long items are kept.
      const { data: s, error: inboxErr, stale: inboxStale, refresh } = useInbox()
      return (
        <BentoCard icon={Inbox} title="Inbox" query={query} onClick={() => go('inbox')} loading={s === undefined && !inboxErr} stale={inboxStale} rows={2}>
          {/* A tile that shimmers forever is the same lie in miniature — say it failed instead. */}
          {!s && Boolean(inboxErr) && <div className="text-on-surface-low text-[0.75rem]">Couldn&rsquo;t load inbox settings.</div>}
          {s && <>
            <div className="flex items-baseline gap-1.5">
              <BigStat value={s.retention_days} caption="day retention" />
            </div>
            <div className="mt-2 flex items-center justify-between gap-2">
              <span className="text-on-surface-low text-[0.75rem]">Auto-cleanup</span>
              <Switch on={s.auto_cleanup_enabled} label="Auto-cleanup"
                onToggle={(v) => mutate(() => api.saveInboxSettings({ auto_cleanup_enabled: v }).then(refresh), 'settings:inbox')} />
            </div>
          </>}
        </BentoCard>
      )
    },
  },
  {
    id: 'notifications', group: 'Workspace', label: 'Notifications', icon: Bell, size: 'md',
    description: 'Mute, quiet hours, and severity filtering.',
    useSearchText() { const { data: s } = useNotif(); return `notifications quiet hours severity mute ${s ? `${s.min_severity} ${s.mute_all ? 'muted' : ''} ${s.quiet_hours_enabled ? 'quiet hours' : ''}` : ''}` },
    render(query, go) {
      const { data: s, refresh, stale: sStale } = useNotif()
      const save = (patch: Record<string, unknown>) => mutate(
        () => api.saveNotificationSettings(patch).then(refresh), 'settings:notification-settings',
      )
      return (
        <BentoCard icon={Bell} title="Notifications" query={query} onClick={() => go('notifications')} loading={s === undefined} rows={3} stale={sStale}>
          {s && <KVList query={query} rows={[
            { k: 'Delivery', control: true, v: <Switch on={!s.mute_all} label="Deliver notifications" onToggle={(v) => save({ mute_all: !v })} /> },
            { k: 'Min severity', control: true, v: <SegToggle value={s.min_severity} onPick={(v) => save({ min_severity: v })} ariaLabel="Min severity"
              options={[{ key: 'info', label: 'All' }, { key: 'warning', label: 'Warn+' }, { key: 'error', label: 'Errors' }]} /> },
            ...(s.quiet_hours_enabled ? [{ k: 'Quiet hours', v: `${s.quiet_hours_start}–${s.quiet_hours_end}`, vText: `${s.quiet_hours_start}-${s.quiet_hours_end}` }] : []),
          ]} />}
        </BentoCard>
      )
    },
  },
  {
    id: 'apps', group: 'Workspace', label: 'Apps', icon: Blocks, size: 'sm',
    description: 'Settings contributed by installed (non-provider) apps.',
    useSearchText() {
      const { data } = useApps()
      const nonProvider = (data ?? []).filter((a) => !a.isProvider)
      return `apps installed extensions settings configure ${nonProvider.map((a) => a.displayName).join(' ')}`
    },
    render(query, go) {
      const { data, error: appsErr, stale: isStalePaint } = useApps()
      const nonProvider = (data ?? []).filter((a) => !a.isProvider)
      const configurable = nonProvider.filter((a) => a.hasConfig).length
      return (
        <BentoCard icon={Blocks} title="Apps" query={query} onClick={() => go('apps')} loading={data === undefined && !appsErr} stale={isStalePaint}>
          {/* Same shape as the Inbox tile: a tile that shimmers forever is the same lie in miniature. */}
          {!data && Boolean(appsErr) && <div className="text-on-surface-low text-[0.75rem]">Couldn&rsquo;t load your apps.</div>}
          {data && <>
            <BigStat value={nonProvider.length} caption={nonProvider.length === 1 ? 'installed app' : 'installed apps'} />
            <div className="mt-1.5 text-on-surface-low text-[0.75rem]">
              {configurable > 0 ? `${configurable} configurable` : 'No configurable settings'}
            </div>
          </>}
        </BentoCard>
      )
    },
  },
  // ── The four subpages the hub never had a card for ────────────────────────────────────────
  //
  // 🔑 THE HUB IS THE ONLY NAVIGATION. `SettingsHome` renders `SETTINGS_WIDGETS` and holds no
  // second list, so a `SUBPAGES` entry with no widget here is reachable only by TYPING ITS URL —
  // and invisible to the settings search, whose haystack is `${label} ${description} ${text}`
  // (`SettingsHome.tsx`'s `Cell`), all three of which live on the widget. Measured on this tree:
  // `ambient`, `companion`, `sources` and `packs` were four such entries, and a code grep for
  // `'settings/(ambient|companion|sources|packs)'` across all of `web` returned ZERO hits — every
  // textual match was prose inside a comment or a test header. So no other surface deep-linked
  // them either. `settingsHubCoverage.test.ts` is the rail that keeps the count at zero.
  //
  // 🔑 EVERY TITLE AND DESCRIPTION BELOW IS THE PANEL'S OWN `PanelHeader`, VERBATIM — its
  // `title`, and its `hint` up to the first sentence break. Checked byte-for-byte against all
  // four panels. Writing fresh copy for a card would mean two answers to "what is this page", and
  // the one on the card is the one a user reads FIRST.
  //
  // The BODY lines are borrowed too, but not all of them are verbatim, and the difference is
  // worth stating rather than glossing:
  //   · sources, off   — verbatim from the panel's own toggle hint ("Off parks the loop; sources
  //                      you add are not fetched until you turn it back on"), including its word
  //                      "Parked" for the pill, which says more than "Off": the loop is stopped,
  //                      the sources you added are still there.
  //   · ambient        — the three row labels are the panel's `ToggleRow label`s verbatim.
  //   · companion      — `discovery.detail` is the BACKEND's sentence, the same string the panel's
  //                      Status row shows as its hint. Not our prose at all.
  //   · packs          — RECOMBINED from the panel's two sentences ("Let the zero-LLM scanner
  //                      propose matching packs for a project" + "it never installs anything on
  //                      its own"), because neither alone fits one line. Recombined, not invented:
  //                      it asserts nothing the panel does not.
  // The rule the whole block obeys is the same either way — the hub may not promise a capability
  // the panel does not have.
  //
  // 🪤 AND NONE OF THE FOUR CARRIES A LIVE SWITCH, unlike Inbox/Notifications/Legibility. Not an
  // omission: each of these master toggles spends something the card cannot explain — LAN
  // discovery ANNOUNCES this gateway on your network, watched sources starts FETCHING third-party
  // URLs on a schedule, fingerprinting SCANS your project directories, and `surfaces_max_layer`
  // is the panel's own "safe-mode knob". A one-click flip on a hub tile, with the consent
  // sentence one navigation away, is the wrong trade for all four. They report state and open the
  // page that explains it — the `Documents`/`Diagnostics` shape, which is also a shipped one.
  {
    id: 'packs', group: 'Workspace', label: 'Packs', icon: Package, size: 'sm',
    description: 'Importable capability bundles — skills, templates, agents and connector declarations one user can hand to another.',
    // Beside Apps because they are the two INSTALL surfaces: an app is a running extension, a pack
    // is a bundle you import. A user looking for either looks in one place.
    useSearchText() {
      const { data: p } = usePacksCfg()
      const { data: installed } = usePacksInstalled()
      const names = (installed ?? []).map((x) => x.name).join(' ')
      return `packs pack capability bundles skills templates agents connectors connector declarations import export fingerprint fingerprinting project scan propose catalog installed setup interview ${p ? (p.fingerprint_enabled ? 'fingerprinting on' : 'fingerprinting off') : ''} ${names}`
    },
    render(query, go) {
      const { data: p, error: packsErr, stale: pStale } = usePacksCfg()
      const { data: installed } = usePacksInstalled()
      const n = installed?.length ?? 0
      return (
        <BentoCard icon={Package} title="Packs" query={query} onClick={() => go('packs')}
          loading={(p === undefined || installed === undefined) && !packsErr} stale={pStale}>
          {!p && Boolean(packsErr) && <div className="text-on-surface-low text-[0.75rem]">Couldn&rsquo;t load your pack settings.</div>}
          {p && installed && <>
            <BigStat value={n} caption={n === 1 ? 'installed pack' : 'installed packs'} />
            <div className="mt-1.5 text-on-surface-low text-[0.75rem]">
              {p.fingerprint_enabled
                ? 'Matching packs are proposed for a project — never installed on their own'
                : 'Fingerprinting off — no packs are proposed for a project'}
            </div></>}
        </BentoCard>
      )
    },
  },
  {
    id: 'documents', group: 'Workspace', label: 'Documents', icon: FileType2, size: 'sm',
    description: 'Whether generated Word documents can be edited in place.',
    useSearchText() { return 'documents word docx office editing edit in place download only fidelity lossy re-render' },
    render(query, go) {
      // Reads the SAME flag the panel writes, and says which way it is set — a card that
      // only described the feature would leave "is it on?" unanswered, which is the one
      // thing a settings overview exists to answer. Shares `useDashCfg`'s key with the
      // other dashboard-config tiles rather than opening a third namespace over one
      // collection (see `splitCollectionBusts.test.ts`).
      const { data, stale } = useDashCfg()
      return (
        <BentoCard icon={FileType2} title="Documents" query={query} onClick={() => go('documents')} loading={data === undefined} stale={stale}>
          {data === null && <div className="text-on-surface-low text-[0.75rem]">Couldn&rsquo;t load your document settings.</div>}
          {data && <>
            <div className="text-on-surface-var text-[0.8125rem]">
              {data.document_editing ? 'Editing generated documents in place' : 'Generated documents are download-only'}
            </div>
            <div className="mt-1.5 text-on-surface-low text-[0.75rem]">
              {data.document_editing ? 'A save re-renders the file — the editor names what it cannot keep' : 'Turn on editing to change one in place'}
            </div>
          </>}
        </BentoCard>
      )
    },
  },
  {
    id: 'sources', group: 'Workspace', label: 'Watched sources', icon: Rss, size: 'sm',
    description: 'Poll feeds, pages and local directories into your knowledge library on a schedule.',
    // Workspace, not AI & Models: the panel's other two sections govern `knowledge.*` (the artifact
    // mirror) and `planning.scratchpad_path` (a notes file), so everything on it is about content
    // arriving in the workspace — not about the model stack Memory/Prompts/Models configure.
    useSearchText() {
      const { data: s } = useSourcesCfg()
      const live = s
        ? `${s.enabled ? 'on enabled polling' : 'off disabled parked'} interval ${s.poll_interval_default_secs} floor ${s.network_floor_secs} max ${s.max_sources} sources ${s.max_items_per_poll} items budget ${s.daily_request_budget}`
        : ''
      return `watched sources poll polling feeds rss pages directories folders ingest knowledge library schedule interval network floor rate limit budget artifacts scratchpad ${live}`
    },
    render(query, go) {
      const { data: s, error: srcErr, stale: sStale } = useSourcesCfg()
      const on = !!s?.enabled
      return (
        <BentoCard icon={Rss} title="Watched sources" query={query} onClick={() => go('sources')} loading={s === undefined && !srcErr} stale={sStale}>
          {!s && Boolean(srcErr) && <div className="text-on-surface-low text-[0.75rem]">Couldn&rsquo;t load your source settings.</div>}
          {/* "Parked" rather than "Off" because that is the panel's own word for this state, and it
              says more: the loop is stopped, the sources you added are still there. */}
          {s && <><StatusPill query={query} label={on ? 'Polling' : 'Parked'} tone={on ? 'ok' : 'muted'} />
            <div className="mt-1.5 text-on-surface-low text-[0.75rem]">
              {on
                ? `Every ${fmtInterval(Number(s.poll_interval_default_secs) || 0)} by default, never faster than ${fmtInterval(Number(s.network_floor_secs) || 0)}`
                : 'Sources you add are not fetched until you turn it back on'}
            </div></>}
        </BentoCard>
      )
    },
  },
  {
    id: 'ambient', group: 'Workspace', label: 'Ambient surfaces', icon: LayoutDashboard, size: 'sm',
    description: 'Your composable home, agent-authored widgets, and the menu-bar companion.',
    useSearchText() {
      const { data: a } = useAmbient()
      const live = a
        ? `tiles ${a.tiles_enabled ? 'on' : 'off'} max ${a.max_tiles} refresh ${a.default_refresh_ttl_secs} genui ${a.genui_enabled ? 'on' : 'off'} layers ${a.surfaces_max_layer} tray ${a.tray_enabled ? 'on' : 'off'}`
        : ''
      return `ambient surfaces composable home dashboard tiles pinned artifacts refresh generative ui genui agent-authored widgets surface layers safe mode menu-bar menubar companion tray macos ${live}`
    },
    render(query, go) {
      const { data: a, error: ambErr, stale: aStale } = useAmbient()
      // Three independent switches and no headline among them, so the tile lists all three by the
      // labels the panel gives them. On/Off in WORDS, not by tone: three coral-vs-grey pills would
      // carry the whole state in hue (WCAG 1.4.1) on the one card whose content IS three booleans.
      const onOff = (v: unknown) => (v ? 'On' : 'Off')
      return (
        <BentoCard icon={LayoutDashboard} title="Ambient surfaces" query={query} onClick={() => go('ambient')} loading={a === undefined && !ambErr} rows={3} stale={aStale}>
          {!a && Boolean(ambErr) && <div className="text-on-surface-low text-[0.75rem]">Couldn&rsquo;t load your ambient settings.</div>}
          {a && <KVList query={query} rows={[
            { k: 'Composable home', v: onOff(a.tiles_enabled), vText: onOff(a.tiles_enabled) },
            { k: 'Generative UI', v: onOff(a.genui_enabled), vText: onOff(a.genui_enabled) },
            { k: 'Menu-bar companion', v: onOff(a.tray_enabled), vText: onOff(a.tray_enabled) },
          ]} />}
        </BentoCard>
      )
    },
  },
  // ── System ──────────────────────────────────────────────────────────────
  {
    id: 'security', group: 'System', label: 'Security', icon: Shield, size: 'md',
    description: 'Enforcement posture and defense layers.',
    useSearchText() { const { data: s } = useSecurity(); return `security enforcement denied commands suspicious patterns redaction tool schemas ${s ? `${s.denied_commands} denied ${s.suspicious_patterns} suspicious` : ''}` },
    render(query, go) {
      const { data: s, stale: sStale } = useSecurity()
      return (
        <BentoCard icon={Shield} title="Security" query={query} onClick={() => go('security')} loading={s === undefined} stale={sStale}>
          {s && <>
            <BigStat value={s.denied_commands} caption="denied-command rules" />
            <div className="mt-2"><KVList query={query} rows={[
              { k: 'Suspicious patterns', v: s.suspicious_patterns, vText: String(s.suspicious_patterns) },
              { k: 'Redaction paths', v: s.redaction_paths, vText: String(s.redaction_paths) },
              { k: 'Tool schemas', v: s.tool_schemas, vText: String(s.tool_schemas) },
            ]} /></div>
          </>}
        </BentoCard>
      )
    },
  },
  {
    id: 'secrets', group: 'System', label: 'Secrets', icon: KeyRound, size: 'md',
    description: 'Stored credentials, their scope, and what uses them.',
    useSearchText() {
      const { data: v } = useSecretsVault()
      return `secrets vault credentials tokens api keys presence global project inherited host ${v ? `${v.counts.total} secrets ${v.secrets.map((s) => s.name).join(' ')}` : ''}`
    },
    render(query, go) {
      const { data: v, stale } = useSecretsVault()
      return (
        <BentoCard icon={KeyRound} title="Secrets" query={query} onClick={() => go('secrets')} loading={v === undefined} stale={stale}>
          {v && <>
            <BigStat value={v.counts.total} caption="secrets known" />
            <div className="mt-2"><KVList query={query} rows={[
              { k: 'Global', v: v.counts.global, vText: String(v.counts.global) },
              { k: 'Per-project', v: v.counts.project, vText: String(v.counts.project) },
              { k: 'From host env', v: v.counts.host, vText: String(v.counts.host) },
            ]} /></div>
          </>}
        </BentoCard>
      )
    },
  },
  {
    id: 'audit', group: 'System', label: 'Audit log', icon: ScrollText, size: 'sm',
    description: 'The live security-event log stream.',
    useSearchText() { return 'audit log security event chain tamper evident verify' },
    render(query, go) {
      const { data: v, stale: vStale } = useAudit()
      return (
        <BentoCard icon={ScrollText} title="Audit log" query={query} onClick={() => go('audit')} loading={v === undefined} stale={vStale}>
          {v && (v.ok
            ? <><StatusPill label="Chain intact" tone="ok" />{typeof v.checked === 'number' && <div className="mt-1.5 text-on-surface-low text-[0.75rem]">{verifiedScope(v)} verified</div>}</>
            : <><StatusPill label="Chain broken" tone="warn" />{(v.error || v.tampered) && <div className="mt-1.5 text-on-surface-low text-[0.75rem]">{v.error || `${v.tampered} altered`}</div>}</>)}
        </BentoCard>
      )
    },
  },
  {
    id: 'diagnostics', group: 'System', label: 'Diagnostics', icon: Activity, size: 'sm',
    description: 'Live backend log stream and runtime log level.',
    useSearchText() { const l = useLogLevel(); return `diagnostics logs live log stream tail level debug info warning error verbosity troubleshoot ${l ?? ''}` },
    render(query, go) {
      const level = useLogLevel()
      return (
        <BentoCard icon={Activity} title="Diagnostics" query={query} onClick={() => go('diagnostics')}>
          <div className="text-on-surface text-[0.9375rem]" style={fvs(550)}>Live log stream</div>
          <div className="mt-1 text-on-surface-low text-[0.75rem]">Level: <Highlight text={level ?? '—'} query={query} /></div>
        </BentoCard>
      )
    },
  },
  {
    id: 'doctor', group: 'System', label: 'Doctor', icon: Stethoscope, size: 'sm',
    description: 'Read-only health probes across every subsystem — memory, channels, models, apps, the SPA symlink.',
    useSearchText() {
      const { data: d } = useDoctor()
      const failed = d ? Object.entries(d.capabilities).filter(([, c]) => !c.ok).map(([k]) => k).join(' ') : ''
      return `doctor health probes diagnostics memory channels local models apps serving symlink breakers ${d ? (d.ok ? 'healthy ok' : `degraded ${failed}`) : ''}`
    },
    render(query, go) {
      const { data: d, error: dErr, stale: dStale } = useDoctor()
      return (
        <BentoCard icon={Stethoscope} title="Doctor" query={query} onClick={() => go('doctor')} loading={d === undefined && !dErr} stale={dStale}>
          {!d && dErr
            ? <StatusPill label="Couldn't check" tone="warn" />
            : d && (d.ok
            ? <StatusPill label="All systems healthy" tone="ok" />
            : !d.core_ok
              ? <StatusPill label="Gateway core failing" tone="warn" />
              : <><StatusPill query={query} label={`${d.worst} degraded`} tone="warn" />
                  <div className="mt-1.5 text-on-surface-low text-[0.75rem]">Core healthy · one capability needs attention</div></>)}
        </BentoCard>
      )
    },
  },
  {
    // Devices sits beside Security because "what is paired to my gateway" is a security
    // question. Without a card here the panel would be reachable only by typing the URL —
    // this home grid IS the navigation, and it renders SETTINGS_WIDGETS with no fallback.
    id: 'devices', group: 'System', label: 'Devices', icon: MonitorSmartphone, size: 'sm',
    description: 'Paired phones, tablets and browsers — and the switch that locks one out.',
    useSearchText() {
      const { data: d } = useDevices()
      return `devices paired device phone tablet browser desktop pairing code qr revoke lock out last seen session ${d ? `${d.length} paired ${d.map((x) => x.name).join(' ')}` : ''}`
    },
    render(query, go) {
      const { data: d, error: dErr, stale: dStale } = useDevices()
      return (
        <BentoCard icon={MonitorSmartphone} title="Devices" query={query} onClick={() => go('devices')} loading={d === undefined && !dErr} stale={dStale}>
          {!d
            ? <StatusPill label="Couldn't check" tone="warn" />
            : d.length === 0
              ? <><StatusPill label="No devices paired" tone="muted" />
                  <div className="mt-1.5 text-on-surface-low text-[0.75rem]">Pair a phone or another browser</div></>
              : <><BigStat value={d.length} caption={d.length === 1 ? 'paired device' : 'paired devices'} />
                  <div className="mt-1.5 truncate text-on-surface-low text-[0.75rem]">
                    <Highlight text={d.map((x) => x.name || 'Unnamed device').join(' · ')} query={query} />
                  </div></>}
        </BentoCard>
      )
    },
  },
  {
    // Beside Devices, and drawing the same line its comment draws: Devices is what holds a
    // SESSION on this gateway; this is who may talk to the agent through a messaging channel.
    // Without a card here the panel is reachable only by typing the URL — this home grid IS
    // the navigation and renders SETTINGS_WIDGETS with no fallback.
    id: 'sender-trust', group: 'System', label: 'Sender trust', icon: MessageCircle, size: 'sm',
    description: 'Who may talk to your agent on a messaging channel — and the switch that cuts one off.',
    useSearchText() {
      const { data: t } = useSenderTrust()
      const senders = t ? t.providers.flatMap((p) => p.allowed_senders.map((s) => s.name || s.sender_id)) : []
      return `sender trust channel allowlist allowed senders pairing code revoke telegram discord slack email stranger dm policy ${t ? `${senders.length} trusted ${senders.join(' ')} ${t.providers.map((p) => p.provider).join(' ')}` : ''}`
    },
    render(query, go) {
      const { data: t, error: tErr, stale: tStale } = useSenderTrust()
      const count = t ? t.providers.reduce((n, p) => n + p.allowed_senders.length, 0) : 0
      const names = t ? t.providers.flatMap((p) => p.allowed_senders.map((s) => s.name || s.sender_id)) : []
      return (
        <BentoCard icon={MessageCircle} title="Sender trust" query={query} onClick={() => go('sender-trust')} loading={t === undefined && !tErr} stale={tStale}>
          {!t
            ? <StatusPill label="Couldn't check" tone="warn" />
            : count === 0
              ? <><StatusPill label="No trusted senders" tone="muted" />
                  <div className="mt-1.5 text-on-surface-low text-[0.75rem]">Strangers must pair before they can talk</div></>
              : <><BigStat value={count} caption={count === 1 ? 'trusted sender' : 'trusted senders'} />
                  <div className="mt-1.5 truncate text-on-surface-low text-[0.75rem]">
                    <Highlight text={names.join(' · ')} query={query} />
                  </div></>}
        </BentoCard>
      )
    },
  },
  {
    id: 'companion', group: 'System', label: 'Companion apps', icon: Smartphone, size: 'sm',
    description: 'Native clients — phone or desktop — that connect to this gateway.',
    // Beside Devices, and the `SUBPAGES` comment draws the line this card must not blur: Devices is
    // the ONE device registry, this is the LAN/PWA transport. So the tile states the TRANSPORT's
    // state — is this gateway announcing itself — and never a device count.
    useSearchText() {
      const { data: d } = useCompanionDiscovery()
      const live = d ? `${d.advertising ? 'advertising' : 'not advertising'} ${d.reason} ${d.detail} ${d.instance_name}` : ''
      return `companion apps native clients phone desktop mobile lan local network discovery advertise announce bonjour mdns zeroconf instance name install offline pwa app shell ${live}`
    },
    render(query, go) {
      const { data: d, error: discErr, stale: dStale } = useCompanionDiscovery()
      return (
        <BentoCard icon={Smartphone} title="Companion apps" query={query} onClick={() => go('companion')} loading={d === undefined && !discErr} stale={dStale}>
          {!d && Boolean(discErr) && <div className="text-on-surface-low text-[0.75rem]">Couldn&rsquo;t check LAN discovery.</div>}
          {/* The advertiser's LIVE state plus the backend's own sentence for the reason code — the
              same two things the panel's Status row shows, in the same vocabulary, so "on but
              inert" can never read here as "on". State in words as well as tone (1.4.1). */}
          {d && <><StatusPill query={query} label={d.advertising ? 'Advertising' : 'Not advertising'} tone={d.advertising ? 'ok' : 'muted'} />
            <div className="mt-1.5 text-on-surface-low text-[0.75rem]"><Highlight text={d.detail} query={query} /></div></>}
        </BentoCard>
      )
    },
  },
  {
    id: 'guardrails', group: 'System', label: 'Guardrails', icon: ShieldAlert, size: 'sm',
    description: 'Autonomy safety floor — incident kill switch, spend budgets, and outbound scanning.',
    useSearchText() { const { data: i } = useIncident(); return `guardrails autonomy safety incident kill switch budgets spend scan denylist ${i ? (i.active ? 'incident active suspended' : 'normal') : ''}` },
    render(query, go) {
      const { data: i, error: iErr, stale: iStale } = useIncident()
      return (
        <BentoCard icon={ShieldAlert} title="Guardrails" query={query} onClick={() => go('guardrails')} loading={i === undefined && !iErr} stale={iStale}>
          {!i && iErr
            ? <StatusPill label="Couldn't check" tone="warn" />
            : i && (i.active
            ? <><StatusPill label="Incident mode — unattended work paused" tone="warn" />
                {i.reason && <div className="mt-1.5 truncate text-on-surface-low text-[0.75rem]">{i.reason}</div>}</>
            : <><StatusPill label="Normal operation" tone="ok" />
                <div className="mt-1.5 text-on-surface-low text-[0.75rem]">Kill switch · budgets · outbound scan</div></>)}
        </BentoCard>
      )
    },
  },
  {
    id: 'external-access', group: 'System', label: 'External access', icon: Plug2, size: 'sm',
    description: 'Ways in from outside — inbound surfaces, their tokens, and per-client limits.',
    useSearchText() {
      const { data: e } = useExternalAccess()
      const on = e?.surfaces.filter((s) => s.enabled && s.token_configured).length ?? 0
      return `external access inbound mcp openai a2a capture bridge tokens clients rate limit kill switch ${e ? (e.enabled ? `on ${on} serving` : 'off disabled') : ''}`
    },
    render(query, go) {
      const { data: e, error: eErr, stale: eStale } = useExternalAccess()
      // "Serving" is enabled AND holding a usable token — the two conditions that must
      // BOTH hold for a surface to answer. Counting only `enabled` would report a
      // tokenless surface as live, which is the exact confusion the panel's per-row
      // "not serving" pill exists to clear up.
      const serving = e?.surfaces.filter((s) => s.enabled && s.token_configured) ?? []
      return (
        <BentoCard icon={Plug2} title="External access" query={query} onClick={() => go('external-access')} loading={e === undefined && !eErr} stale={eStale}>
          {!e && eErr
            ? <StatusPill label="Couldn't check" tone="warn" />
            : e && (!e.enabled
            ? <><StatusPill label="No inbound access" tone="ok" />
                <div className="mt-1.5 text-on-surface-low text-[0.75rem]">Nothing outside can reach in</div></>
            : serving.length === 0
              ? <><StatusPill label="On, nothing serving" tone="warn" />
                  <div className="mt-1.5 text-on-surface-low text-[0.75rem]">Each surface still needs its own token</div></>
              : <><StatusPill label={`${serving.length} surface${serving.length === 1 ? '' : 's'} reachable`} tone="warn" />
                  <div className="mt-1.5 truncate text-on-surface-low text-[0.75rem]">
                    {serving.map((s) => s.surface).join(' · ')}
                    {e.clients.length > 0 && ` · ${e.clients.length} client${e.clients.length === 1 ? '' : 's'}`}
                  </div></>)}
        </BentoCard>
      )
    },
  },
  {
    id: 'legibility', group: 'System', label: 'Legibility', icon: Compass, size: 'md',
    description: 'How PersonalClaw describes its capabilities — always-on conventions, dashboard tips, project context files.',
    // "always-on conventions" is searchable here because the viewer lives INSIDE this subpage
    // rather than as its own tile; a surface nobody can search for is one nobody finds.
    useSearchText() { const { data: c } = useLegibility(); return `legibility always-on conventions always on rules injected every session project instructions overview provenance discover tips tour features context adapters claude.md agents.md cursorrules ${c ? `tips ${!!c.discover_tips} context ${!!c.context_adapters}` : ''}` },
    render(query, go) {
      const { data: c, error: legErr, refresh, stale: cStale } = useLegibility()
      const save = (key: string, value: boolean) => mutate(
        () => api.patchConfig(`legibility.${key}`, value).then(refresh), 'settings:legibility',
      )
      return (
        <BentoCard icon={Compass} title="Legibility" query={query} onClick={() => go('legibility')} loading={c === undefined && !legErr} rows={2} stale={cStale}>
          {/* This tile carries live SWITCHES, so a fabricated `{}` did more than mis-state a count — it
              offered two toggles whose "off" position was invented. #1194's line, fifth adopter. */}
          {!c && Boolean(legErr) && <div className="text-on-surface-low text-[0.75rem]">Couldn&rsquo;t load your legibility settings.</div>}
          {c && <KVList query={query} rows={[
            { k: 'Discover tips', control: true, v: <Switch on={!!c.discover_tips} label="Discover tips" onToggle={(v) => save('discover_tips', v)} /> },
            { k: 'Context files', control: true, v: <Switch on={!!c.context_adapters} label="Context files" onToggle={(v) => save('context_adapters', v)} /> },
          ]} />}
        </BentoCard>
      )
    },
  },
  {
    id: 'tool-output', group: 'System', label: 'Tool output', icon: Scissors, size: 'sm',
    description: 'TokenJuice shrinks large tool output before it reaches the model — with custom projection rules and a savings meter.',
    useSearchText() {
      const { data: r } = useProjectionRules()
      const { data: s } = useToolsSavings()
      const saved = s && s.saved_tokens_estimated > 0 ? `saved ${s.saved_tokens_estimated} tokens top ${s.top_compressor ?? ''}` : ''
      return `tool output projection rules trim shrink token juice tokenjuice savings saved tokens compressor regex marker strategy ${saved} ${(r ?? []).map((x) => `${x.name} ${x.strategy}`).join(' ')}`
    },
    render(query, go) {
      const { data: rules, error: rulesErr, stale: rulesStale } = useProjectionRules()
      const { data: savings } = useToolsSavings()
      const list = rules ?? []
      const savedTokens = savings?.saved_tokens_estimated ?? 0
      return (
        <BentoCard icon={Scissors} title="Tool output" query={query} onClick={() => go('tool-output')} loading={rules === undefined && !rulesErr} stale={rulesStale}>
          {/* The savings meter is a SEPARATE read that keeps its own fallback, so it can still headline
              here while the rules read has failed — the failure line only speaks for the rules. */}
          {!rules && Boolean(rulesErr) && savedTokens === 0 && <div className="text-on-surface-low text-[0.75rem]">Couldn&rsquo;t load your projection rules.</div>}
          {/* Headline the savings meter once there's data (the feature's whole point);
              fall back to the rule count / builtin-projectors hint otherwise so the card
              is never empty and the feature is always discoverable from the grid. */}
          {savedTokens > 0
            ? <><BigStat value={`~${savedTokens.toLocaleString()}`} caption="tokens saved by projection" />
                <div className="mt-1 text-on-surface-low text-[0.8125rem]">
                  {list.length ? `${list.length} custom rule${list.length === 1 ? '' : 's'} · ` : ''}
                  top compressor: {savings?.top_compressor ?? '—'}
                </div></>
            : rules && (list.length
              ? <><BigStat value={list.length} caption={list.length === 1 ? 'custom rule' : 'custom rules'} />
                  <div className="mt-2"><ChipRow query={query} chips={list.slice(0, 6).map((r) => ({ label: r.name, tone: 'muted' as const }))} /></div></>
              : <div className="text-on-surface-low text-[0.8125rem]">Builtin projectors shrink logs, diffs, JSON, tests, CSV, and code; the full raw stays recoverable. A savings meter appears here once projection kicks in.</div>)}
        </BentoCard>
      )
    },
  },
  {
    id: 'feedback', group: 'System', label: 'AI feedback', icon: ThumbsUp, size: 'sm',
    description: 'Per-source accuracy from your 👍/👎 on AI judgments — a source that keeps missing stops surfacing.',
    useSearchText() {
      const { data } = useFeedbackProducers()
      const rows = data?.producers ?? []
      return `feedback thumbs accuracy judgment verdict up down retire suppress ${rows.map((r) => r.producer_id).join(' ')}`
    },
    render(query, go) {
      const { data, stale: isStalePaint } = useFeedbackProducers()
      const rows = data?.producers ?? []
      const rated = rows.filter((r) => !r.collecting)
      const suppressed = rows.filter((r) => r.suppressed).length
      return (
        <BentoCard icon={ThumbsUp} title="AI feedback" query={query} onClick={() => go('feedback')} loading={data === undefined} stale={isStalePaint}>
          {rows.length === 0
            ? <div className="text-on-surface-low text-[0.8125rem]">👍/👎 on inbox triage, drafts, digests, and loop findings collect here per judgment source. A source that keeps missing stops surfacing.</div>
            : <><BigStat value={rows.length} caption={rows.length === 1 ? 'judgment source' : 'judgment sources'} />
                <div className="mt-1 text-on-surface-low text-[0.8125rem]">
                  {rated.length ? `${rated.length} rated` : 'collecting verdicts'}
                  {suppressed ? ` · ${suppressed} suppressed` : ''}
                </div></>}
        </BentoCard>
      )
    },
  },
  {
    id: 'usage', group: 'System', label: 'Usage', icon: Coins, size: 'sm',
    description: "Real cost + tokens spent across every turn — chat, subagents, loops, automations.",
    useSearchText() {
      const { data } = useUsageToday()
      return `usage cost tokens spend dollars price budget model source ${data ? `${data.cost_usd} ${data.turns} turns` : ''}`
    },
    render(query, go) {
      const { data, stale: isStalePaint } = useUsageToday()
      const tokens = data ? (data.input_tokens || 0) + (data.output_tokens || 0) : 0
      return (
        <BentoCard icon={Coins} title="Usage" query={query} onClick={() => go('usage')} loading={data === undefined} stale={isStalePaint}>
          {!data || data.turns === 0
            ? <div className="text-on-surface-low text-[0.8125rem]">Real cost + tokens for every turn — chat, subagents, loops, automations — land here once usage is recorded.</div>
            : <><BigStat value={data.priced ? (data.cost_usd >= 1 ? `$${data.cost_usd.toFixed(2)}` : `$${data.cost_usd.toFixed(4)}`) : 'unpriced'} caption="today" />
                <div className="mt-1 text-on-surface-low text-[0.8125rem]">
                  {tokens >= 1000 ? `${Math.round(tokens / 1000)}k` : tokens} tokens · {data.turns} {data.turns === 1 ? 'turn' : 'turns'}
                </div></>}
        </BentoCard>
      )
    },
  },
  {
    id: 'archive', group: 'System', label: 'Archive', icon: Archive, size: 'sm',
    description: 'Browse and inspect archived chat sessions.',
    useSearchText() { return 'archive archived chat sessions transcripts browse' },
    render(query, go) {
      const { data: a, error: archErr, stale: aStale } = useArchives()
      return (
        <BentoCard icon={Archive} title="Archive" query={query} onClick={() => go('archive')} loading={a === undefined && !archErr} stale={aStale}>
          {!a && Boolean(archErr) && <div className="text-on-surface-low text-[0.75rem]">Couldn&rsquo;t load your archives.</div>}
          {a && <BigStat value={a.length} caption={a.length === 1 ? 'archived session' : 'archived sessions'} />}
        </BentoCard>
      )
    },
  },
  {
    id: 'portability', group: 'System', label: 'Import / Export', icon: FolderSync, size: 'sm',
    description: 'Export a portable archive, or import from another instance.',
    useSearchText() { return 'import export portability backup migrate archive transfer instance' },
    render(query, go) {
      return (
        <BentoCard icon={FolderSync} title="Import / Export" query={query} onClick={() => go('portability')}>
          <div className="text-on-surface-var text-[0.8125rem]">Back up or migrate this instance.</div>
          <div className="mt-1.5 text-on-surface-low text-[0.75rem]">Export a portable archive · import from another instance</div>
        </BentoCard>
      )
    },
  },
  {
    id: 'durability', group: 'System', label: 'Backups', icon: HardDriveDownload, size: 'sm',
    description: 'Automatic snapshots, how long they are kept, and restore drills.',
    useSearchText() {
      const { data: s } = useDurability()
      return `backups backup durability snapshot snapshots retention restore drill schedule automatic ${
        s ? (s.status?.enabled ? 'on enabled' : 'off disabled') : ''
      } ${s?.snaps ? `${s.snaps.archives.length} snapshots` : ''}`
    },
    render(query, go) {
      const { data: s, stale: sStale } = useDurability()
      const count = s?.snaps?.archives.length
      return (
        <BentoCard icon={HardDriveDownload} title="Backups" query={query} onClick={() => go('durability')} loading={s === undefined} stale={sStale}>
          {s && (count === undefined
            ? <div className="text-on-surface-var text-[0.8125rem]">Snapshot schedule and retention.</div>
            : <>
                <BigStat value={count} caption={count === 1 ? 'snapshot kept' : 'snapshots kept'} />
                <div className="mt-1.5 text-on-surface-low text-[0.75rem]">
                  {s.status?.enabled ? 'Nightly + hourly, automatic' : 'Automatic backups are off'}
                </div>
              </>)}
        </BentoCard>
      )
    },
  },
  {
    id: 'updates', group: 'System', label: 'Updates', icon: DownloadCloud, size: 'sm',
    description: 'Version, changelog, and update controls.',
    useSearchText() { const { data: u } = useUpdates(); return `updates version changelog upgrade ${u ? `${u.version ?? ''} ${u.available ? `update available ${u.latest ?? ''}` : 'up to date'} ${u.auto_update ? 'auto-update' : ''}` : ''}` },
    render(query, go) {
      const { data: u, refresh, stale: uStale } = useUpdates()
      return (
        <BentoCard icon={DownloadCloud} title="Updates" query={query} onClick={() => go('updates')} loading={u === undefined} rows={2} stale={uStale}>
          {u && <>
            <div className="text-on-surface text-[0.9375rem] font-mono">{u.version || '—'}</div>
            <div className="mt-1.5">
              {u.available
                ? <StatusPill query={query} label={`Update available${u.latest ? ` — ${u.latest}` : ''}`} tone="primary" />
                : <StatusPill label="Up to date" tone="ok" />}
            </div>
            <div className="mt-2.5 flex items-center justify-between gap-2">
              <span className="text-on-surface-low text-[0.75rem]">Auto-update</span>
              <Switch on={u.auto_update} label="Auto-update"
                onToggle={(v) => mutate(() => api.setAutoUpdate(v).then(refresh), 'settings:update-check')} />
            </div>
          </>}
        </BentoCard>
      )
    },
  },
]

// Avoid an unused-import lint for SavedAgent (kept for the agents typing surface).
export type { SavedAgent }
