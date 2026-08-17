import { useMemo, useState } from 'react'
import { withWeight } from '../../design/fontWeight'
import { Wrench, ShieldAlert, Server, Cpu, Plug, Circle, RefreshCw, Loader2, Plus, Trash2, Download, ChevronRight } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { ListControls } from '../../ui/ListControls'
import { EmptyState, ListSkeleton } from '../../ui/ListScaffold'
import { SidePanel } from '../../ui/SidePanel'
import { Modal } from '../../ui/Modal'
import { Button } from '../../ui/Button'
import { Segmented } from '../../ui/Segmented'
import { Field, TextArea, TextInput } from '../../ui/forms'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { Toggle as SharedToggle } from '../../ui/Toggle'
import { confirm } from '../../ui/dialog'
import { notify } from '../../app/appSdk'
import { useQueryParam, useQueryFlag, type RouteProps } from '../../app/useQueryState'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { api, type ToolItem, type McpServer, type ImportableMcpServer, type ToolLoadFailure, type McpPoolStats, type ToolGroupsData } from '../../lib/api'
import { schemaProps } from './schema'
import { ToolInspector } from './ToolInspector'
import { ToolGroupsTile } from './ToolGroupsTile'
import { PageTitle } from '../../ui/PageTitle'

/** Tools = the capability catalog agents invoke. Grouped by provider — native
 *  built-in providers plus connected MCP servers (shown with health + inline
 *  enable/disable, even when erroring or contributing zero tools). Click a tool
 *  to inspect its full signature and run it. */

// Native providers the platform can't run without — no provider-level toggle,
// no delete (mirrors the backend LOCKED_PROVIDERS guard). Everything else (other
// native app-providers, MCP servers, OpenAI servers) is toggleable + removable.
const LOCKED_NATIVE_PROVIDER = 'personalclaw-filesystem'

interface Group {
  key: string
  label: string
  kind: 'native' | 'mcp'
  tools: ToolItem[]
  server?: McpServer        // present for mcp groups
  providerDisabled?: boolean  // whole native provider turned off
  providerLocked?: boolean    // platform provider — not toggleable/removable
  group?: string              // activation group (Context Economy §5); unset when grouping is off
}

function serverHealth(s: McpServer): { state: string; tone: string; detail?: string } {
  if (!s.enabled) return { state: 'disabled', tone: 'var(--color-on-surface-low)' }
  if (s.status === 'ready' || s.status === 'ok' || s.status === 'connected') return { state: 'ready', tone: 'var(--color-ok)' }
  if (s.status === 'error') return { state: 'error', tone: 'var(--color-danger)', detail: s.error }
  return { state: s.status || 'unknown', tone: 'var(--color-warn)', detail: s.error }
}

interface ToolsIndexData {
  tools: ToolItem[]
  loadFailures: ToolLoadFailure[]
  servers: McpServer[]
  importable: ImportableMcpServer[]
  poolStats: McpPoolStats
  groups: ToolGroupsData | null
}

export function ToolsPage({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  const { data, refresh } = useCachedData<ToolsIndexData>('tools:index', async () => {
    const [idx, servers, importable, poolStats, groups] = await Promise.all([
      api.toolsIndex().catch(() => ({ tools: [], load_failures: [] as ToolLoadFailure[] })),
      api.mcpServers().catch(() => [] as McpServer[]),
      api.importableMcp().catch(() => [] as ImportableMcpServer[]),
      api.mcpPoolStats().catch(() => ({ available: false } as McpPoolStats)),
      api.toolGroups().catch(() => null),
    ])
    return { tools: idx.tools, loadFailures: idx.load_failures ?? [], servers, importable, poolStats, groups }
  }, { persist: true })
  const tools = data?.tools ?? null
  const loadFailures = data?.loadFailures ?? []
  const servers = data?.servers ?? []
  const importable = data?.importable ?? []
  const poolStats = data?.poolStats ?? null
  const groupsInfo = data?.groups ?? null
  const groupsEnabled = !!groupsInfo?.enabled
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  // Risk filter (tool risk taxonomy): let a security-conscious user narrow to
  // "what can do caution/destructive things". URL-param so it's shareable, like q.
  const [risk, setRisk] = useQueryParam(query, setQuery, 'risk', 'all', { replace: true })
  const [openNameRaw, setOpenName] = useQueryParam(query, setQuery, 'open', '')
  const openName = openNameRaw || null

  const [probing, setProbing] = useState(false)
  const [addOpen, setAddOpen] = useQueryFlag(query, setQuery, 'add')
  const load = () => { invalidateCache('tools:index'); refresh() }

  async function reprobe() {
    setProbing(true)
    try { await api.probeMcp().catch(() => {}); load() } finally { setProbing(false) }
  }

  async function toggleServer(s: McpServer) {
    await api.toggleMcpServer(s.name, !s.enabled).catch(() => {})
    setTimeout(load, 400)
  }

  // Reconnect ONE server (re-probe just it) — recover a timed-out/errored provider
  // without re-probing the whole fleet.
  const [reconnecting, setReconnecting] = useState<string | null>(null)
  async function reconnectServer(s: McpServer) {
    setReconnecting(s.name)
    try { await api.reconnectMcp(s.name) } catch { /* status surfaces on reload */ }
    finally { setReconnecting(null); load() }
  }

  async function removeServer(s: McpServer) {
    if (!(await confirm({ title: `Remove MCP server "${s.name}"?`, body: 'Its tools will no longer be available.', danger: true, confirmLabel: 'Remove' }))) return
    try {
      await api.removeMcpServer(s.name)
    } catch (e) {
      // An app-contributed server (409 ownedByApp) can't be removed here — it's
      // owned by its app. Surface the backend's message instead of silently
      // "refreshing" (the bug), so the user knows to uninstall the app.
      let msg = e instanceof Error ? e.message : 'Failed to remove server'
      try { const p = JSON.parse(msg); msg = p.error || msg } catch { /* raw text */ }
      notify(msg, 'error')
    }
    setTimeout(load, 400)
  }

  // Per-tool enable/disable. MCP tools write mcp.json (disabledTools); native
  // tools write tool_prefs.json. Locked tools never reach here (switch disabled).
  async function toggleTool(g: Group, t: ToolItem) {
    const enabled = t.disabled === true  // flipping → if currently disabled, enable
    if (g.kind === 'mcp' && g.server) {
      await api.toggleMcpTool(g.server.name, t.name, enabled).catch(() => {})
    } else {
      await api.toggleTool(t.provider, t.name, enabled).catch(() => {})
    }
    setTimeout(load, 300)
  }

  // Whole-provider enable/disable. A native provider writes tool_prefs.json
  // (disabledProviders); an MCP server reuses the server toggle. One write path
  // per kind — the runtime + all surfaces read it back.
  async function toggleProvider(g: Group) {
    if (g.kind === 'mcp' && g.server) { await toggleServer(g.server); return }
    await api.toggleToolProvider(g.key, !!g.providerDisabled).catch(() => {})
    setTimeout(load, 300)
  }

  const groups = useMemo<Group[] | null>(() => {
    if (!tools) return null
    // The activation group a provider's tools belong to — shown as a badge only
    // when grouping is ON (off, every group is loaded, so naming one is noise).
    // Providers are group-grain by construction, so the provider's tools agree;
    // a core-locked tool can differ (it's always `core`), so take the majority
    // rather than the first, and show nothing if it's genuinely mixed.
    const groupOf = (list: ToolItem[]): string | undefined => {
      if (!groupsEnabled) return undefined
      const counts = new Map<string, number>()
      for (const t of list) {
        if (!t.group) continue
        counts.set(t.group, (counts.get(t.group) ?? 0) + 1)
      }
      let best: string | undefined
      let bestCount = 0
      for (const [name, count] of counts) if (count > bestCount) { best = name; bestCount = count }
      return best
    }
    const needle = q.trim().toLowerCase()
    // A "narrowing" filter is active when there's a search needle OR a risk filter
    // — both hide empty native groups (an empty group only shows in the unfiltered
    // browse view, so an errored/0-tool provider stays discoverable).
    const active = !!needle || risk !== 'all'
    const match = (t: ToolItem) =>
      (!needle || `${t.name} ${t.description}`.toLowerCase().includes(needle)) &&
      (risk === 'all' || (t.risk_level ?? 'safe') === risk)
    const byProvider = new Map<string, ToolItem[]>()
    for (const t of tools) { const p = t.provider || 'other'; (byProvider.get(p) ?? byProvider.set(p, []).get(p)!).push(t) }
    // The backend only surfaces MCP servers configured in PersonalClaw scope —
    // Claude-Code-only servers are offered as import suggestions instead (see
    // ImportSuggestions), never as live server groups here.
    const serverNames = new Set(servers.map((s) => s.name))

    const out: Group[] = []
    // native providers (those not backed by an MCP server)
    for (const [p, list] of byProvider) {
      if (serverNames.has(p)) continue
      const filtered = list.filter(match)
      // a provider is "off" when ALL its tools report providerDisabled (the backend
      // sets that flag per-tool when the whole provider is disabled).
      const provOff = list.length > 0 && list.every((t) => t.providerDisabled)
      if (filtered.length || !active) out.push({
        key: p, label: p, kind: 'native', tools: filtered,
        providerDisabled: provOff, providerLocked: p === LOCKED_NATIVE_PROVIDER,
        group: groupOf(list),
      })
    }
    out.sort((a, b) => a.label.localeCompare(b.label))
    // MCP servers configured in PersonalClaw (shown even at 0 tools / errored)
    for (const s of servers) {
      const list = (byProvider.get(s.name) ?? []).filter(match)
      out.push({ key: s.name, label: s.name, kind: 'mcp', tools: list, server: s, group: groupOf(byProvider.get(s.name) ?? []) })
    }
    // With a filter active, drop groups with no matching tools — including MCP
    // groups (an errored/0-tool server is only worth showing in the browse view).
    return out.filter((g) => g.tools.length > 0 || (g.kind === 'mcp' && !active) || !active)
  }, [tools, servers, q, risk, groupsEnabled])

  const open = tools?.find((t) => t.name === openName) ?? null
  const openServer = open ? servers.find((s) => s.name === open.provider) : undefined
  // View is "filtered" when a search needle or a risk filter is narrowing it —
  // suppresses the browse-only affordances (load failures, import suggestions).
  const filtered = !!q.trim() || risk !== 'all'

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={<PageTitle>Tools</PageTitle>}
          right={
            <HeaderActions>
              <HeaderControl icon={Plus} label="Add tool server" priority="primary" onClick={() => setAddOpen(true)} />
              <HeaderControl icon={probing ? Loader2 : RefreshCw} label="Re-probe MCP servers" priority="low" disabled={probing} onClick={reprobe} />
            </HeaderActions>
          }
        />
      }
      controls={(tools === null || tools.length > 0)
        ? <ListControls
            search={{ value: q, onChange: setQ, placeholder: 'Search tools', label: 'Search tools' }}
            filter={{
              value: risk, onChange: setRisk, ariaLabel: 'Filter by risk level',
              options: [
                { key: 'all', label: 'All' },
                { key: 'safe', label: 'Safe', tone: 'var(--color-ok)' },
                { key: 'caution', label: 'Caution', tone: 'var(--color-warn)' },
                { key: 'destructive', label: 'Destructive', tone: 'var(--color-danger)' },
              ],
            }}
          />
        : undefined}
      panel={open && (
        <SidePanel key={open.name} fillHeight storeKey="tool-panel-w" icon={<Wrench size={18} className="text-primary" />} title={<span className="font-mono text-[1.0625rem]">{open.name}</span>} onClose={() => setOpenName("")}>
          <ToolInspector tool={open} serverStatus={openServer ? serverHealth(openServer) : undefined} />
        </SidePanel>
      )}
    >
      <>
        <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
          {groups === null ? <ListSkeleton rows={6} what="tools" /> : groups.length === 0 ? (
            // No group survived: nothing matched the filter, or (unfiltered) there are no
            // tools at all. `importable` must NOT gate this — importable servers are ones
            // you COULD add, never search results, so gating on them made the state
            // unreachable on any install with a discoverable MCP config and left a blank
            // body on every no-match search. Import suggestions still render underneath,
            // so on a genuinely empty install adding a server stays one click away.
            <div className="flex flex-col gap-2xl">
              <EmptyState icon={Wrench} title={filtered ? 'No matching tools' : 'No tools'} hint={filtered ? (risk !== 'all' && !q ? `No ${risk} tools.` : 'Try a different search term.') : 'Tools are the capabilities agents can invoke — built-in actions plus anything from connected MCP servers.'} />
              {!filtered && importable.length > 0 && <ImportSuggestions servers={importable} onImported={() => setTimeout(load, 300)} />}
            </div>
          ) : (
            <div className="flex flex-col gap-2xl">
              {!filtered && loadFailures.length > 0 && <LoadFailures failures={loadFailures} />}
              {!filtered && groupsInfo && <ToolGroupsTile data={groupsInfo} onChanged={load} />}
              {!filtered && <McpPoolTile stats={poolStats} />}
              {groups?.map((g) => <GroupBlock key={g.key} g={g} onOpen={setOpenName} onToggleServer={toggleServer} onRemoveServer={removeServer} onToggleTool={toggleTool} onToggleProvider={toggleProvider} onReconnect={reconnectServer} reconnecting={reconnecting} />)}
              {!filtered && importable.length > 0 && <ImportSuggestions servers={importable} onImported={() => setTimeout(load, 300)} />}
            </div>
          )}
        </div>

        {addOpen && <AddToolServerModal onClose={() => setAddOpen(false)} onAdded={() => { setAddOpen(false); setTimeout(load, 300) }} />}
      </>
    </WorkbenchLayout>
  )
}

/** P23d: the MCP connection-pool observability tile — surfaces the live pool snapshot
 *  (shared vs per-session connections) + lifetime spawn/reuse counters so the user can
 *  see pooling working. Hidden when the mcp SDK extra is absent or nothing has connected
 *  yet (no pool activity → no tile clutter). */
/** Exported for test: the gate (which pool states render at all) and the conditional Evicted cell
 *  are only observable by rendering the tile against a stubbed stats object — jsdom reports every
 *  box as 0, so nothing about them is measurable from layout. */
export function McpPoolTile({ stats }: { stats: McpPoolStats | null }) {
  // `configured_servers` joins the gate: the old condition was `live_connections || spawns`, so a
  // pool with servers CONFIGURED but none spawned yet rendered nothing — exactly the state where
  // "the pool knows about N servers and has opened none" is the useful fact. A configured pool with
  // no activity is a real answer; an empty pool is the only thing worth hiding.
  if (!stats || !stats.available) return null
  if (!(stats.live_connections || stats.spawns || stats.configured_servers)) return null
  const cells: Array<{ label: string; value: number | undefined; hint: string }> = [
    // Configured leads: it is the denominator the other numbers are read against — 0 live out of 1
    // configured means something different from 0 live out of 6.
    { label: 'Configured', value: stats.configured_servers, hint: 'MCP servers the pool knows about, whether or not a connection is open' },
    { label: 'Live', value: stats.live_connections, hint: 'Open MCP connections right now' },
    { label: 'Shared', value: stats.shared_conns, hint: 'Poolable servers shared across sessions (one process each)' },
    { label: 'Per-session', value: stats.session_conns, hint: 'Stateful servers isolated to one session' },
    { label: 'Reused', value: stats.reused, hint: 'Calls served by an existing connection instead of a new spawn' },
    { label: 'Spawns', value: stats.spawns, hint: 'Connections started this process lifetime' },
    { label: 'Reaped', value: stats.reaps, hint: 'Idle connections swept to reclaim memory' },
    // Evicted is the ONLY counter here tied to session lifecycle rather than pooling: session
    // expiry drops that session's isolated connections (shared ones are untouched). Shown only
    // when non-zero — on the common single-session install it is permanently 0, and a zero cell
    // beside six live ones reads as a metric that is broken rather than one that has not happened.
    ...(stats.evicted ? [{ label: 'Evicted', value: stats.evicted, hint: 'Per-session connections dropped when their session expired (shared connections are untouched)' }] : []),
  ]
  return (
    <div>
      <div className="mb-s flex items-center gap-s">
        <Server size={14} className="text-on-surface-low" />
        <span className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">MCP connection pool</span>
      </div>
      <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))' }}>
        {cells.map((c) => (
          <div key={c.label} title={c.hint}
            className="rounded-lg border border-outline-variant/40 bg-surface-container/50 px-3 py-2">
            <div className="text-on-surface text-[1.25rem] tabular-nums leading-tight">{c.value ?? 0}</div>
            <div className="text-on-surface-low text-[0.75rem]">{c.label}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function GroupBlock({ g, onOpen, onToggleServer, onRemoveServer, onToggleTool, onToggleProvider, onReconnect, reconnecting }: { g: Group; onOpen: (name: string) => void; onToggleServer: (s: McpServer) => void; onRemoveServer: (s: McpServer) => void; onToggleTool: (g: Group, t: ToolItem) => void; onToggleProvider: (g: Group) => void; onReconnect: (s: McpServer) => void; reconnecting: string | null }) {
  const health = g.server ? serverHealth(g.server) : null
  // A native provider (not the locked platform one) gets a whole-provider toggle.
  const nativeToggleable = g.kind === 'native' && !g.providerLocked
  return (
    <div className={g.providerDisabled ? 'opacity-55' : ''}>
      <div className="mb-s flex items-center gap-s">
        {g.kind === 'mcp' ? <Server size={14} className="text-on-surface-low" /> : <Cpu size={14} className="text-on-surface-low" />}
        <span className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">{g.label}</span>
        {g.kind === 'native'
          ? <span className="rounded-pill bg-surface-high px-2 h-5 inline-flex items-center text-on-surface-low text-[0.75rem]">{g.providerLocked ? 'platform' : 'built-in'}</span>
          : health && <span className="inline-flex items-center gap-1 text-[0.75rem]" style={{ color: health.tone }} title={health.detail}><Circle size={7} fill="currentColor" stroke="none" /> {health.state}</span>}
        <span className="text-on-surface-low text-[0.75rem]">· {g.tools.length}</span>
        {/* Which activation GROUP these tools belong to (Context Economy §5) — the
            page already groups by provider, which IS the group grain, so this just
            names it. Only shown when grouping is on, since it's meaningless off. */}
        {g.group && (
          <span className="rounded-pill bg-surface-high px-2 h-5 inline-flex items-center text-on-surface-low text-[0.75rem]"
            title={g.group === 'core'
              ? 'Always loaded — the primitives an agent cannot work without'
              : `Group "${g.group}" — loaded on demand; a session that doesn't need it doesn't pay for its schemas`}>
            {g.group === 'core' ? 'always loaded' : `group: ${g.group}`}
          </span>
        )}
        {g.server && (
          <div className="ml-auto flex items-center gap-1">
            {/* Reconnect just THIS server (re-probe) — recover a timed-out/errored
                provider without re-probing all. Spins while in flight. */}
            <SquareIconButton label={`Reconnect ${g.server.name}`} title="Reconnect this server"
              disabled={reconnecting === g.server.name} onClick={() => onReconnect(g.server!)}>
              <RefreshCw size={13} className={reconnecting === g.server.name ? 'animate-spin' : ''} />
            </SquareIconButton>
            <button onClick={() => onToggleServer(g.server!)} title={g.server.enabled ? 'Disable server' : 'Enable server'}
              aria-label={`${g.server.enabled ? 'Disable' : 'Enable'} server ${g.server.name}`}>
              <Toggle on={!!g.server.enabled} />
            </button>
            {/* An app-contributed MCP server is namespaced "{app}:{server}" and owned
                by its app — it re-registers on app enable, so it's not standalone-
                deletable here. Show a "via app" marker (delete = uninstall the app)
                instead of a Trash button that would 409 + look broken. */}
            {g.server.name.includes(':') ? (
              <span className="text-on-surface-low text-[0.75rem]" title={`Provided by the '${g.server.name.split(':')[0]}' app — uninstall it from the Store to remove this server.`}>via app</span>
            ) : (
              <SquareIconButton icon={Trash2} iconSize={13} tone="danger"
                label={`Remove ${g.server.name}`} title="Remove server" onClick={() => onRemoveServer(g.server!)} />
            )}
          </div>
        )}
        {nativeToggleable && (
          <div className="ml-auto flex items-center gap-1">
            <button onClick={() => onToggleProvider(g)} title={g.providerDisabled ? 'Enable this provider' : 'Disable this whole provider'}
              aria-label={`${g.providerDisabled ? 'Enable' : 'Disable'} provider ${g.label}`}>
              <Toggle on={!g.providerDisabled} />
            </button>
          </div>
        )}
        {g.providerLocked && (
          <span className="ml-auto text-on-surface-low text-[0.75rem]" title="Required by platform features — can't be disabled">required</span>
        )}
      </div>
      {/* 🔴 TWO COLUMNS WITH NO BREAKPOINT MADE THE TOOL NAME INVISIBLE ON A PHONE. The grid in the
          else-branch below holds, per cell, a wrench, the name, an approval shield, a risk badge and
          sometimes a "Disabled" pill — so at 390px a ~172px half-width cell leaves the name nothing.
          Measured across 99 tools: visible width 0–83px, MEDIAN 65px, several at 0 or 1px;
          `artifact_delete` painted at literally 0px while needing 117. At 768px and 1440px every name
          renders in full (101/101px), so this is purely the phone column count.
          🪤 A `title` WOULD HAVE BEEN THE WRONG FIX, which is why the truncation census classifies
          before it edits: a tooltip needs a pointer and this defect exists only on touch. The name has
          to be VISIBLE, not recoverable — hence one column below `sm:`, the idiom nine other grids here
          already use (SecurityPanel, ArtifactGrid, ArtifactCompare, ScheduleForm, AgentForm,
          AuditPanel, PresetEmptyState, DesignCockpitPage ×2).
          🪤 This comment sits ABOVE the conditional on purpose: a `{…}` comment as the first child of a
          ternary branch is a second child where one expression is allowed, and it does not compile. */}
      {g.kind === 'mcp' && g.tools.length === 0 ? (
        <div className="rounded-lg bg-surface-container px-m py-3 text-on-surface-low text-[0.8125rem] flex items-center gap-s">
          <Plug size={14} />
          {!g.server?.enabled ? 'Server disabled.' : health?.state === 'error' ? `Not responding — ${g.server?.error || 'no tools available'}.` : 'No tools exposed yet.'}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-s sm:grid-cols-2">
          {g.tools.map((t) => {
            const { props } = schemaProps(t.parameters)
            const off = t.disabled === true
            return (
              <div key={t.name}
                className={`group flex items-start gap-s rounded-lg bg-surface-container px-m py-m transition-colors hover:bg-surface-high ${off ? 'opacity-55' : ''}`}>
                <button onClick={() => onOpen(t.name)} className="flex min-w-0 flex-1 items-start gap-s text-left">
                  <Wrench size={16} className="text-primary shrink-0 mt-0.5" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate font-mono text-on-surface text-[0.8125rem]">{t.name}</span>
                      {t.requires_approval && <ShieldAlert size={12} className="text-warn shrink-0" />}
                      <RiskBadge risk={t.risk_level} />
                      {off && <span className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.75rem]">Disabled</span>}
                    </div>
                    <p className="mt-0.5 line-clamp-2 text-on-surface-low text-[0.75rem] leading-snug">{t.description}</p>
                    {props.length > 0 && <div className="mt-1 text-on-surface-low text-[0.75rem]">{props.length} param{props.length === 1 ? '' : 's'}</div>}
                  </div>
                </button>
                {/* per-tool enable/disable. Locked tools show a disabled switch with
                    an explanation; the rest toggle (native → tool_prefs, MCP → mcp.json). */}
                <button
                  onClick={() => { if (!t.locked) onToggleTool(g, t) }}
                  disabled={t.locked}
                  title={t.locked ? 'Required by platform features — can’t be disabled' : off ? 'Enable this tool' : 'Disable this tool'}
                  aria-label={`${off ? 'Enable' : 'Disable'} ${t.name}`}
                  className={`shrink-0 mt-0.5 ${t.locked ? 'cursor-not-allowed opacity-40' : ''}`}>
                  <Toggle on={!off} />
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/** Risk indicator on a tool row (tool risk taxonomy). SAFE is the norm — showing
 *  it on every read tool would be noise — so only caution/destructive get a chip.
 *  Declared (static) risk; the approval gate resolves per-invocation effective risk. */
function RiskBadge({ risk }: { risk?: 'safe' | 'caution' | 'destructive' }) {
  if (!risk || risk === 'safe') return null
  const color = risk === 'destructive' ? 'var(--color-danger)' : 'var(--color-warn)'
  const label = risk === 'destructive' ? 'Destructive' : 'Caution'
  return (
    <span className="rounded-pill px-1.5 py-0.5 text-[0.75rem] shrink-0" title={`Risk: ${label}`}
      style={withWeight({ background: `color-mix(in srgb, ${color} 16%, transparent)`, color }, 600)}>
      {label}
    </span>
  )
}

/** Operator-visible tool-source load failures — a broken provider/MCP source
 *  that contributed zero tools, with the captured error. Without this a failed
 *  source is invisible (the tools just never appear). */
function LoadFailures({ failures }: { failures: ToolLoadFailure[] }) {
  return (
    <div className="rounded-lg border px-m py-3" style={{ borderColor: 'color-mix(in srgb, var(--color-danger) 35%, transparent)', background: 'color-mix(in srgb, var(--color-danger) 8%, transparent)' }}>
      <div className="mb-2 flex items-center gap-s">
        <ShieldAlert size={15} className="text-danger" />
        <span className="text-on-surface text-[0.8125rem] font-medium">{failures.length} tool source{failures.length === 1 ? '' : 's'} failed to load</span>
      </div>
      <div className="flex flex-col gap-1.5">
        {failures.map((f) => (
          <div key={f.provider} className="text-[0.75rem] leading-snug">
            <span className="font-mono text-on-surface">{f.provider}</span>
            <span className="text-on-surface-low"> — {f.error}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// Display-only switch — routes through the canonical readOnly Toggle (a span, so
// it can nest inside the larger clickable tool row without a nested button). It's
// `decorative` because every call site wraps it in a <button aria-label> that IS
// the accessible control — this keeps the switch out of the a11y tree so it
// doesn't surface as a second, unnamed switch duplicating the button.
function Toggle({ on }: { on: boolean }) {
  return <SharedToggle on={on} readOnly decorative size="sm" />
}

/** Collapsed "Discovered in <backend>" list — MCP servers configured in an
 *  external backend (Claude Code) but not yet in PersonalClaw. Importing one
 *  copies its spec into ~/.personalclaw/mcp.json so the native loop can run it. */
function ImportSuggestions({ servers, onImported }: { servers: ImportableMcpServer[]; onImported: () => void }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)

  const importOne = async (s: ImportableMcpServer) => {
    setBusy(s.name)
    try { await api.importMcpServer(s.name); onImported() } finally { setBusy(null) }
  }

  return (
    <div>
      <button onClick={() => setOpen((v) => !v)} aria-expanded={open} className="mb-s flex min-h-6 -my-0.5 items-center gap-s text-on-surface-low hover:text-on-surface transition-colors">
        <ChevronRight size={14} style={{ transform: open ? 'rotate(90deg)' : 'none' }} />
        <Download size={14} />
        <span className="text-[0.75rem] uppercase tracking-wide">Discovered in other tools ({servers.length})</span>
      </button>
      {open && (
        <>
          <p className="mb-2 text-on-surface-low text-[0.75rem] leading-snug">
            These MCP servers are configured in another backend but not in PersonalClaw. Import one to copy its
            configuration here so your agents can use it.
          </p>
          <div className="flex flex-col gap-2">
            {servers.map((s) => (
              <div key={s.name} className="flex items-center gap-3 rounded-lg bg-surface-container px-m py-2.5">
                <Server size={15} className="shrink-0 text-on-surface-low" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-mono text-on-surface text-[0.8125rem]">{s.name}</span>
                    <span className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.75rem]">{s.backend}</span>
                  </div>
                  <p className="mt-0.5 truncate font-mono text-on-surface-low text-[0.75rem]">{s.url || [s.command, ...(s.args ?? [])].join(' ')}</p>
                </div>
                <Button variant="secondary" size="sm" onClick={() => importOne(s)} disabled={busy === s.name}>
                  {busy === s.name ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />} Import
                </Button>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// `mcpInputCls` was DELETED with its last consumer: the raw <textarea> above migrated to the
// shared TextArea, and it had been the only remaining user of this hand-copied field chrome.

/** Add a tool server — either a stdio MCP server (→ PUT /api/mcp/servers/{name},
 *  writes ~/.personalclaw/mcp.json) OR an OpenAI-compatible REST tool server
 *  (→ an `openai-tools` provider instance). The "+" offers both tool-provider
 *  types so the user isn't forced into MCP. */
function AddToolServerModal({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [kind, setKind] = useState<'mcp' | 'openai'>('mcp')
  // MCP fields
  const [name, setName] = useState('')
  const [command, setCommand] = useState('')
  const [args, setArgs] = useState('')
  const [env, setEnv] = useState('')
  // OpenAI tool-server fields
  const [oaName, setOaName] = useState('')
  const [endpoint, setEndpoint] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [toolFilter, setToolFilter] = useState('')
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)

  const validName = /^[a-zA-Z0-9_-]{1,64}$/.test(name)
  const apiErr = (e: unknown) => {
    let msg = e instanceof Error ? e.message : 'Failed to add server'
    try { const p = JSON.parse(msg); msg = p.error || msg } catch { /* raw */ }
    return msg
  }

  const submitMcp = async () => {
    if (!validName) { setErr('Name must be letters, digits, dashes, underscores (1–64).'); return }
    if (!command.trim()) { setErr('Command is required (e.g. npx, node, uvx).'); return }
    const envObj: Record<string, string> = {}
    for (const line of env.split('\n')) {
      const i = line.indexOf('=')
      if (i > 0) envObj[line.slice(0, i).trim()] = line.slice(i + 1).trim()
    }
    setSaving(true); setErr('')
    try {
      await api.addMcpServer(name.trim(), {
        command: command.trim(),
        args: args.trim() ? args.trim().split(/\s+/) : undefined,
        env: Object.keys(envObj).length ? envObj : undefined,
      })
      onAdded()
    } catch (e) { setErr(apiErr(e)); setSaving(false) }
  }

  const submitOpenai = async () => {
    if (!endpoint.trim()) { setErr('Endpoint URL is required (e.g. https://tools.example.com).'); return }
    setSaving(true); setErr('')
    try {
      await api.createProviderInstance('openai-tools', {
        display_name: oaName.trim() || endpoint.trim(),
        config: {
          endpoint: endpoint.trim(),
          ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
          ...(toolFilter.trim() ? { tool_filter: toolFilter.trim() } : {}),
        },
      })
      onAdded()
    } catch (e) { setErr(apiErr(e)); setSaving(false) }
  }

  const canSubmit = kind === 'mcp' ? (!!name && !!command.trim()) : !!endpoint.trim()

  return (
    <Modal title="Add tool server" icon={<Server size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-3">
        {/* type picker — MCP (stdio) vs OpenAI-compatible REST tool server */}
        <Segmented ariaLabel="Server type" value={kind} onChange={(k) => { setKind(k as 'mcp' | 'openai'); setErr('') }}
          options={[{ key: 'mcp', label: 'MCP server' }, { key: 'openai', label: 'OpenAI tool server' }]} />

        {kind === 'mcp' ? (<>
          <Field label="Name" hint="A unique handle (letters, digits, dashes, underscores).">
            <TextInput value={name} onChange={setName} placeholder="filesystem-mcp" size="md" surface="high" />
          </Field>
          <Field label="Command" hint="The executable that starts the server over stdio.">
            <TextInput value={command} onChange={setCommand} placeholder="npx" size="md" surface="high" mono />
          </Field>
          <Field label="Arguments" hint="Space-separated args passed to the command (optional).">
            <TextInput value={args} onChange={setArgs} placeholder="-y @modelcontextprotocol/server-filesystem /path" size="md" surface="high" mono />
          </Field>
          <Field label="Environment" hint="One KEY=value per line (optional).">
            {/* The one raw control left in this modal after the Field migration. A raw element
                cannot read FieldLabelCtx, so it stayed unnamed while its seven TextInput siblings
                were fixed. `TextArea` claims the Field label the same way they do — and this also
                retires the `mcpInputCls.replace('h-9', …)` string surgery, which reached into a
                class string to undo a height the primitive never sets. */}
            <TextArea value={env} onChange={setEnv} rows={2} placeholder="API_KEY=sk-…" mono size="md" />
          </Field>
        </>) : (<>
          <Field label="Name" hint="A label for this tool server (optional — defaults to the endpoint).">
            <TextInput value={oaName} onChange={setOaName} placeholder="my-tools" size="md" surface="high" />
          </Field>
          <Field label="Endpoint URL" hint="Base URL of an OpenAI-compatible tool server (GET /tools, POST /tools/{name}).">
            <TextInput value={endpoint} onChange={setEndpoint} placeholder="https://tools.example.com" size="md" surface="high" mono />
          </Field>
          <Field label="API Key" hint="Optional bearer token for authentication.">
            <TextInput value={apiKey} onChange={setApiKey} placeholder="sk-…" type="password" size="md" surface="high" mono />
          </Field>
          <Field label="Tool filter" hint="Comma-separated tool names to expose. Empty = all.">
            <TextInput value={toolFilter} onChange={setToolFilter} placeholder="search, fetch" size="md" surface="high" mono />
          </Field>
        </>)}

        <div className="flex items-center gap-2">
          {/* `canSubmit` asks for different fields per kind, so the reason follows the kind;
              omitted while `saving`, where the label already reads "Adding…". */}
          <Button size="sm" onClick={kind === 'mcp' ? submitMcp : submitOpenai} disabled={saving || !canSubmit}
            disabledReason={saving ? undefined
              : kind === 'mcp' ? 'Name the server and give it a command' : "Enter the server's endpoint URL"}>{saving ? 'Adding…' : 'Add server'}</Button>
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          {err && <span className="text-[0.75rem]" style={{ color: 'var(--color-danger)' }}>{err}</span>}
        </div>
      </div>
    </Modal>
  )
}

// The local `Field` was DELETED here (it reimplemented ui/forms' Field with a plain label div).
// That was not a style divergence — it was an ACCESSIBILITY defect. `TextInput` reads
// `useFieldLabelId()` and claims its Field's published label via `aria-labelledby`; the shared
// Field is what publishes that id through `FieldLabelCtx`. A local Field provides no context, so
// `claimsFieldLabel` was false and no `ariaLabel` was passed either — measured on the live DOM,
// all seven inputs in this modal had `aria-label: null`, `aria-labelledby: null`, `name: null`.
// A placeholder is not an accessible name.
