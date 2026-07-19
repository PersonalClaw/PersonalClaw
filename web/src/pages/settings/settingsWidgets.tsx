import {
  User, Palette, MessageSquare, Plug, Cpu, FileText, Database, Bot, AudioLines,
  Inbox, Bell, Shield, ScrollText, Archive, FolderSync, DownloadCloud, CheckCircle2, Search, Blocks, Activity,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import {
  api, type SecurityStats, type MemoryStats, type AgentRuntime, type DashboardConfig,
  type SettingsProvider, type InboxSettings, type NotificationSettings, type UpdateCheck,
  type PromptBindings, type SessionArchive, type SelVerify, type SavedAgent,
  type SearchProviderInfo, type AppSummary,
} from '../../lib/api'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { useIdentity } from '../../app/identity'
import { useAppearance } from '../../app/appearance'
import { useMode } from '../../app/theme'
import {
  BentoCard, BigStat, KVList, StatusPill, ChipRow, Highlight,
  Switch, SegToggle, InlineSelect, type BentoSize,
} from './bento'

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
const useSecurity = () => useCachedData('settings:security', () => api.securityStats().catch(() => null as SecurityStats | null), { persist: true })
const useMemoryStats = () => useCachedData('settings:memory-stats', () => api.memoryStats().catch(() => null as MemoryStats | null), { persist: true })
const useModelsActive = () => useCachedData('settings:models-active', () => api.modelsActive().catch(() => null as Record<string, string[]> | null), { persist: true })
const useSearchEntity = () => useCachedData('settings:search', async () => {
  const [providers, active] = await Promise.all([
    api.searchProviders().catch(() => [] as SearchProviderInfo[]),
    api.searchActive().catch(() => ({} as Record<string, string[]>)),
  ])
  return { providers, active }
}, { persist: true })
const useRuntimes = () => useCachedData('settings:agent-runtimes', () => api.agentRuntimes().catch(() => null as AgentRuntime[] | null), { persist: true })
const useProviders = () => useCachedData('settings:providers', () => api.settingsProviders().catch(() => [] as SettingsProvider[]), { persist: true })
const useDashCfg = () => useCachedData('settings:dashboard-config', () => api.dashboardConfig().catch(() => null as DashboardConfig | null), { persist: true })
const useInbox = () => useCachedData('settings:inbox', () => api.inboxSettings().catch(() => null as InboxSettings | null), { persist: true })
const useApps = () => useCachedData('apps', () => api.apps().catch(() => [] as AppSummary[]), { persist: true })
const useNotif = () => useCachedData('settings:notification-settings', () => api.notificationSettings().catch(() => null as NotificationSettings | null), { persist: true })
const useUpdates = () => useCachedData('settings:update-check', () => api.updateCheck().catch(() => null as UpdateCheck | null), { persist: true })
const usePromptBindings = () => useCachedData('settings:prompt-bindings', () => api.promptBindings().catch(() => null as PromptBindings | null), { persist: true })
const useArchives = () => useCachedData('settings:archives', () => api.sessionArchives().catch(() => [] as SessionArchive[]), { persist: true })
const useAudit = () => useCachedData('settings:audit-verify', () => api.selVerify().catch(() => null as SelVerify | null), { persist: false })
const useLogLevel = () => useCachedData('settings:log-level', () => api.logLevel().catch(() => null as string | null), { persist: true }).data
const useVoice = () => useCachedData('settings:voice', async () => {
  const [active, stt, tts] = await Promise.all([
    api.modelsActive().catch(() => ({} as Record<string, string[]>)),
    api.useCaseSettings('stt').catch(() => ({} as Record<string, unknown>)),
    api.useCaseSettings('tts').catch(() => ({} as Record<string, unknown>)),
  ])
  return { active, stt, tts }
}, { persist: true })
const useAgentDefaults = () => useCachedData('settings:agent-defaults', async () => {
  const [cfg, agents] = await Promise.all([
    api.personalclawConfig().then((c) => (c.agent ?? {}) as Record<string, unknown>).catch(() => ({} as Record<string, unknown>)),
    api.agents().then((a) => a.default_agent).catch(() => ''),
  ])
  return { cfg, defaultAgent: agents }
}, { persist: true })

/** Run an async mutation, then invalidate the widget's cache key(s) so its data
 *  re-reads the new value. Errors are swallowed (the control resets visually). */
async function mutate(fn: () => Promise<unknown>, ...invalidateKeys: string[]) {
  try { await fn() } catch { /* leave the control to reflect the unchanged cache */ }
  for (const k of invalidateKeys) invalidateCache(k)
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
          <div className="truncate text-on-surface text-[1.05rem]" style={{ fontVariationSettings: '"wght" 550' }}>{name || 'PersonalClaw'}</div>
          <div className="text-on-surface-low text-[0.78rem]">Display name &amp; onboarding</div>
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
            <span className="truncate text-on-surface text-[0.85rem]">{query ? <Highlight text={label} query={query} /> : label}</span>
          </div>
          {/* Mode is an inline choice; full theme/token editing lives in the subpage. */}
          <div className="mt-2.5 flex items-center justify-between gap-2">
            <span className="text-on-surface-low text-[0.75rem]">Mode</span>
            <SegToggle value={preference} onPick={(p) => setPreference(p)}
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
      const { data: c, refresh } = useDashCfg()
      const save = (patch: Record<string, unknown>) => mutate(
        () => api.saveDashboardConfig(patch).then(refresh), 'settings:dashboard-config',
      )
      return (
        <BentoCard icon={MessageSquare} title="Chat" query={query} onClick={() => go('chat')} loading={c === undefined} rows={4}>
          {c && <KVList query={query} rows={[
            { k: 'Restore sessions', control: true, v: <Switch on={c.restore_sessions} label="Restore sessions" onToggle={(v) => save({ restore_sessions: v })} /> },
            { k: 'Send on Enter', control: true, v: <Switch on={c.send_on_enter} label="Send on Enter" onToggle={(v) => save({ send_on_enter: v })} /> },
            { k: 'Timestamps', control: true, v: <Switch on={c.show_timestamps} label="Timestamps" onToggle={(v) => save({ show_timestamps: v })} /> },
            { k: 'Density', control: true, v: <SegToggle value={c.widget_density} onPick={(v) => save({ widget_density: v })}
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
      const { data: provs } = useProviders(); const { data: rt } = useRuntimes()
      const enabled = (provs ?? []).filter((p) => p.enabled)
      const ready = (rt ?? []).filter((r) => r.ready).length
      return (
        <BentoCard icon={Plug} title="Providers" query={query} onClick={() => go('providers')} loading={provs === undefined}>
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
      const { data: active } = useModelsActive()
      const CORE = [['chat', 'Chat'], ['embedding', 'Embed'], ['stt', 'STT'], ['tts', 'TTS']] as const
      return (
        <BentoCard icon={Cpu} title="Models" query={query} onClick={() => go('models')} loading={active === undefined}>
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
    id: 'search', group: 'AI & Models', label: 'Search', icon: Search, size: 'md',
    description: 'Which search provider serves each web search use case.',
    useSearchText() {
      const { data } = useSearchEntity()
      const provs = (data?.providers ?? []).map((p) => p.name).join(' ')
      const binds = Object.entries(data?.active ?? {}).map(([uc, names]) => `${uc} ${(names ?? []).join(' ')}`).join(' ')
      return `search web provider use case duckduckgo tavily searxng exa perplexity brave ${provs} ${binds}`
    },
    render(query, go) {
      const { data } = useSearchEntity()
      const USE_CASES = [['search-general', 'General'], ['search-news', 'News'], ['fetch-article', 'Fetch']] as const
      const active = data?.active
      return (
        <BentoCard icon={Search} title="Search" query={query} onClick={() => go('search')} loading={data === undefined}>
          {data && (data.providers.length === 0
            ? <div className="text-on-surface-low text-[0.8rem]">DuckDuckGo (keyless) is the default; add a provider in Providers to upgrade.</div>
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
      const { data: b } = usePromptBindings()
      const rows = (b?.bindings ?? []).slice(0, 4).map((x) => {
        const name = (x.ref || x.effective_ref || 'Default').replace(/\.md$/, '')
        return { k: x.use_case.replace(/_/g, ' '), v: name, vText: name, mono: false }
      })
      return (
        <BentoCard icon={FileText} title="Prompts" query={query} onClick={() => go('prompts')} loading={b === undefined}>
          {b && (rows.length ? <KVList query={query} rows={rows} /> : <div className="text-on-surface-low text-[0.8rem]">All contexts use the default prompt.</div>)}
        </BentoCard>
      )
    },
  },
  {
    id: 'memory', group: 'AI & Models', label: 'Memory', icon: Database, size: 'md',
    description: 'Semantic + episodic memory, consolidation, and retention.',
    useSearchText() { const { data: m } = useMemoryStats(); return `memory semantic episodic events embedded retention ${m ? `${m.semantic_active} semantic ${m.episodic_active} episodic ${m.embedding_provider ?? ''}` : ''}` },
    render(query, go) {
      const { data: m } = useMemoryStats()
      return (
        <BentoCard icon={Database} title="Memory" query={query} onClick={() => go('memory')} loading={m === undefined}
          footer={m?.embedding_provider ? <>Embedder: <span className="font-mono text-on-surface-var">{m.embedding_provider}</span></> : undefined}>
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
      const { data, refresh } = useAgentDefaults()
      const c = (data?.cfg ?? {}) as Record<string, unknown>
      const approval = String(c.approval_mode ?? 'interactive')
      const setCfg = (key: string, value: unknown) => mutate(
        () => api.patchConfig(`agent.${key}`, value).then(refresh), 'settings:agent-defaults',
      )
      return (
        <BentoCard icon={Bot} title="Agent defaults" query={query} onClick={() => go('agent')} loading={data === undefined} rows={3}>
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
      const { data, refresh } = useVoice()
      // Enabling needs a bound model (same gate as the subpage). Without one, the
      // toggle is disabled and the card nudges the user into Speech & Transcription → Models.
      const toggle = (uc: 'stt' | 'tts', settings: Record<string, unknown>, next: boolean) => mutate(
        () => api.saveUseCaseSettings(uc, { ...settings, enabled: next }).then(refresh), 'settings:voice',
      )
      const sttBound = !!(data?.active?.['stt'] ?? [])[0]
      const ttsBound = !!(data?.active?.['tts'] ?? [])[0]
      return (
        <BentoCard icon={AudioLines} title="Speech & Transcription" query={query} onClick={() => go('voice')} loading={data === undefined} rows={2}>
          {data && <KVList rows={[
            { k: 'Speech-to-text', control: true, v: <Switch on={!!data.stt?.enabled} disabled={!sttBound} label="Speech-to-text" onToggle={(v) => toggle('stt', data.stt ?? {}, v)} /> },
            { k: 'Text-to-speech', control: true, v: <Switch on={!!data.tts?.enabled} disabled={!ttsBound} label="Text-to-speech" onToggle={(v) => toggle('tts', data.tts ?? {}, v)} /> },
          ]} />}
        </BentoCard>
      )
    },
  },
  // ── Workspace ──────────────────────────────────────────────────────────────
  {
    id: 'inbox', group: 'Workspace', label: 'Inbox', icon: Inbox, size: 'md',
    description: 'Alert keywords, name-mention alerts, and retention policy.',
    useSearchText() { const { data: s } = useInbox(); return `inbox alert keywords name mention retention ${(s?.alert_keywords ?? []).join(' ')}` },
    render(query, go) {
      const { data: s, refresh } = useInbox()
      return (
        <BentoCard icon={Inbox} title="Inbox" query={query} onClick={() => go('inbox')} loading={s === undefined} rows={2}>
          {s && <>
            <div className="flex items-baseline gap-1.5">
              <BigStat value={s.alert_keywords.length} caption={s.alert_keywords.length === 1 ? 'alert keyword' : 'alert keywords'} />
            </div>
            {s.alert_keywords.length > 0 && <div className="mt-2"><ChipRow query={query} chips={s.alert_keywords.slice(0, 6).map((k) => ({ label: k, tone: 'muted' as const }))} /></div>}
            <div className="mt-2 flex items-center justify-between gap-2">
              <span className="text-on-surface-low text-[0.75rem]">Name-mention alerts</span>
              <Switch on={s.alert_on_name_mention} label="Name-mention alerts"
                onToggle={(v) => mutate(() => api.saveInboxSettings({ alert_on_name_mention: v }).then(refresh), 'settings:inbox')} />
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
      const { data: s, refresh } = useNotif()
      const save = (patch: Record<string, unknown>) => mutate(
        () => api.saveNotificationSettings(patch).then(refresh), 'settings:notification-settings',
      )
      return (
        <BentoCard icon={Bell} title="Notifications" query={query} onClick={() => go('notifications')} loading={s === undefined} rows={3}>
          {s && <KVList query={query} rows={[
            { k: 'Delivery', control: true, v: <Switch on={!s.mute_all} label="Deliver notifications" onToggle={(v) => save({ mute_all: !v })} /> },
            { k: 'Min severity', control: true, v: <SegToggle value={s.min_severity} onPick={(v) => save({ min_severity: v })}
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
      const { data } = useApps()
      const nonProvider = (data ?? []).filter((a) => !a.isProvider)
      const configurable = nonProvider.filter((a) => a.hasConfig).length
      return (
        <BentoCard icon={Blocks} title="Apps" query={query} onClick={() => go('apps')} loading={data === undefined}>
          {data && <>
            <BigStat value={nonProvider.length} caption={nonProvider.length === 1 ? 'installed app' : 'installed apps'} />
            <div className="mt-1.5 text-on-surface-low text-[0.78rem]">
              {configurable > 0 ? `${configurable} configurable` : 'No configurable settings'}
            </div>
          </>}
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
      const { data: s } = useSecurity()
      return (
        <BentoCard icon={Shield} title="Security" query={query} onClick={() => go('security')} loading={s === undefined}>
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
    id: 'audit', group: 'System', label: 'Audit log', icon: ScrollText, size: 'sm',
    description: 'The live security-event log stream.',
    useSearchText() { return 'audit log security event chain tamper evident verify' },
    render(query, go) {
      const { data: v } = useAudit()
      return (
        <BentoCard icon={ScrollText} title="Audit log" query={query} onClick={() => go('audit')} loading={v === undefined}>
          {v && (v.valid
            ? <><StatusPill label="Chain intact" tone="ok" />{typeof v.count === 'number' && <div className="mt-1.5 text-on-surface-low text-[0.78rem]">{v.count} events verified</div>}</>
            : <><StatusPill label="Chain broken" tone="warn" />{v.error && <div className="mt-1.5 text-on-surface-low text-[0.75rem]">{v.error}</div>}</>)}
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
          <div className="text-on-surface text-[0.9rem]" style={{ fontVariationSettings: '"wght" 550' }}>Live log stream</div>
          <div className="mt-1 text-on-surface-low text-[0.78rem]">Level: <Highlight text={level ?? '—'} query={query} /></div>
        </BentoCard>
      )
    },
  },
  {
    id: 'archive', group: 'System', label: 'Archive', icon: Archive, size: 'sm',
    description: 'Browse and inspect archived chat sessions.',
    useSearchText() { return 'archive archived chat sessions transcripts browse' },
    render(query, go) {
      const { data: a } = useArchives()
      return (
        <BentoCard icon={Archive} title="Archive" query={query} onClick={() => go('archive')} loading={a === undefined}>
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
          <div className="text-on-surface-var text-[0.85rem]">Back up or migrate this instance.</div>
          <div className="mt-1.5 text-on-surface-low text-[0.78rem]">Export a portable archive · import from another instance</div>
        </BentoCard>
      )
    },
  },
  {
    id: 'updates', group: 'System', label: 'Updates', icon: DownloadCloud, size: 'sm',
    description: 'Version, changelog, and update controls.',
    useSearchText() { const { data: u } = useUpdates(); return `updates version changelog upgrade ${u ? `${u.version ?? ''} ${u.available ? `update available ${u.latest ?? ''}` : 'up to date'} ${u.auto_update ? 'auto-update' : ''}` : ''}` },
    render(query, go) {
      const { data: u, refresh } = useUpdates()
      return (
        <BentoCard icon={DownloadCloud} title="Updates" query={query} onClick={() => go('updates')} loading={u === undefined} rows={2}>
          {u && <>
            <div className="text-on-surface text-[0.95rem] font-mono">{u.version || '—'}</div>
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
