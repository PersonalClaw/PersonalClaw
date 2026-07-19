import { useMemo, useState } from 'react'
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
import { Toggle as SharedToggle } from '../../ui/Toggle'
import { confirm } from '../../ui/dialog'
import { notify } from '../../app/appSdk'
import { useQueryParam, useQueryFlag, type RouteProps } from '../../app/useQueryState'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { api, type ToolItem, type McpServer, type ImportableMcpServer, type ToolLoadFailure, type McpPoolStats } from '../../lib/api'
import { schemaProps } from './schema'
import { ToolInspector } from './ToolInspector'

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
}

export function ToolsPage({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  const { data, refresh } = useCachedData<ToolsIndexData>('tools:index', async () => {
    const [idx, servers, importable, poolStats] = await Promise.all([
      api.toolsIndex().catch(() => ({ tools: [], load_failures: [] as ToolLoadFailure[] })),
      api.mcpServers().catch(() => [] as McpServer[]),
      api.importableMcp().catch(() => [] as ImportableMcpServer[]),
      api.mcpPoolStats().catch(() => ({ available: false } as McpPoolStats)),
    ])
    return { tools: idx.tools, loadFailures: idx.load_failures ?? [], servers, importable, poolStats }
  }, { persist: true })
  const tools = data?.tools ?? null
  const loadFailures = data?.loadFailures ?? []
  const servers = data?.servers ?? []
  const importable = data?.importable ?? []
  const poolStats = data?.poolStats ?? null
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
      })
    }
    out.sort((a, b) => a.label.localeCompare(b.label))
    // MCP servers configured in PersonalClaw (shown even at 0 tools / errored)
    for (const s of servers) {
      const list = (byProvider.get(s.name) ?? []).filter(match)
      out.push({ key: s.name, label: s.name, kind: 'mcp', tools: list, server: s })
    }
    // With a filter active, drop groups with no matching tools — including MCP
    // groups (an errored/0-tool server is only worth showing in the browse view).
    return out.filter((g) => g.tools.length > 0 || (g.kind === 'mcp' && !active) || !active)
  }, [tools, servers, q, risk])

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
          left={<span data-type="title-l" className="text-on-surface">Tools</span>}
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
        <SidePanel key={open.name} fillHeight storeKey="tool-panel-w" icon={<Wrench size={18} className="text-primary" />} title={<span className="font-mono text-[1rem]">{open.name}</span>} onClose={() => setOpenName("")}>
          <ToolInspector tool={open} serverStatus={openServer ? serverHealth(openServer) : undefined} />
        </SidePanel>
      )}
    >
      <>
        <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
          {groups === null ? <ListSkeleton rows={6} /> : groups.length === 0 && importable.length === 0 ? (
            <EmptyState icon={Wrench} title={filtered ? 'No matching tools' : 'No tools'} hint={filtered ? (risk !== 'all' && !q ? `No ${risk} tools.` : 'Try a different search term.') : 'Tools are the capabilities agents can invoke — built-in actions plus anything from connected MCP servers.'} />
          ) : (
            <div className="flex flex-col gap-2xl">
              {!filtered && loadFailures.length > 0 && <LoadFailures failures={loadFailures} />}
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
function McpPoolTile({ stats }: { stats: McpPoolStats | null }) {
  if (!stats || !stats.available || !(stats.live_connections || stats.spawns)) return null
  const cells: Array<{ label: string; value: number | undefined; hint: string }> = [
    { label: 'Live', value: stats.live_connections, hint: 'Open MCP connections right now' },
    { label: 'Shared', value: stats.shared_conns, hint: 'Poolable servers shared across sessions (one process each)' },
    { label: 'Per-session', value: stats.session_conns, hint: 'Stateful servers isolated to one session' },
    { label: 'Reused', value: stats.reused, hint: 'Calls served by an existing connection instead of a new spawn' },
    { label: 'Spawns', value: stats.spawns, hint: 'Connections started this process lifetime' },
    { label: 'Reaped', value: stats.reaps, hint: 'Idle connections swept to reclaim memory' },
  ]
  return (
    <div>
      <div className="mb-s flex items-center gap-s">
        <Server size={14} className="text-on-surface-low" />
        <span className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">MCP connection pool</span>
      </div>
      <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))' }}>
        {cells.map((c) => (
          <div key={c.label} title={c.hint}
            className="rounded-lg border border-outline-variant/40 bg-surface-container/50 px-3 py-2">
            <div className="text-on-surface text-[1.25rem] tabular-nums leading-tight">{c.value ?? 0}</div>
            <div className="text-on-surface-low text-[0.7rem]">{c.label}</div>
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
        <span className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">{g.label}</span>
        {g.kind === 'native'
          ? <span className="rounded-pill bg-surface-high px-2 h-5 inline-flex items-center text-on-surface-low text-[0.65rem]">{g.providerLocked ? 'platform' : 'built-in'}</span>
          : health && <span className="inline-flex items-center gap-1 text-[0.7rem]" style={{ color: health.tone }} title={health.detail}><Circle size={7} fill="currentColor" stroke="none" /> {health.state}</span>}
        <span className="text-on-surface-low text-[0.7rem]">· {g.tools.length}</span>
        {g.server && (
          <div className="ml-auto flex items-center gap-1">
            {/* Reconnect just THIS server (re-probe) — recover a timed-out/errored
                provider without re-probing all. Spins while in flight. */}
            <button onClick={() => onReconnect(g.server!)} disabled={reconnecting === g.server.name}
              title="Reconnect this server" aria-label={`Reconnect ${g.server.name}`}
              className="grid size-7 place-items-center rounded-md text-on-surface-low transition-colors hover:text-on-surface disabled:opacity-50">
              <RefreshCw size={13} className={reconnecting === g.server.name ? 'animate-spin' : ''} />
            </button>
            <button onClick={() => onToggleServer(g.server!)} title={g.server.enabled ? 'Disable server' : 'Enable server'}
              aria-label={`${g.server.enabled ? 'Disable' : 'Enable'} server ${g.server.name}`}>
              <Toggle on={!!g.server.enabled} />
            </button>
            {/* An app-contributed MCP server is namespaced "{app}:{server}" and owned
                by its app — it re-registers on app enable, so it's not standalone-
                deletable here. Show a "via app" marker (delete = uninstall the app)
                instead of a Trash button that would 409 + look broken. */}
            {g.server.name.includes(':') ? (
              <span className="text-on-surface-low text-[0.65rem]" title={`Provided by the '${g.server.name.split(':')[0]}' app — uninstall it from the Store to remove this server.`}>via app</span>
            ) : (
              <button onClick={() => onRemoveServer(g.server!)} title="Remove server" aria-label={`Remove ${g.server.name}`}
                className="grid size-7 place-items-center rounded-md text-on-surface-low transition-colors hover:text-danger">
                <Trash2 size={13} />
              </button>
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
          <span className="ml-auto text-on-surface-low text-[0.65rem]" title="Required by platform features — can't be disabled">required</span>
        )}
      </div>
      {g.kind === 'mcp' && g.tools.length === 0 ? (
        <div className="rounded-lg bg-surface-container px-m py-3 text-on-surface-low text-[0.8125rem] flex items-center gap-s">
          <Plug size={14} />
          {!g.server?.enabled ? 'Server disabled.' : health?.state === 'error' ? `Not responding — ${g.server?.error || 'no tools available'}.` : 'No tools exposed yet.'}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-s">
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
                      {off && <span className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.6rem]">disabled</span>}
                    </div>
                    <p className="mt-0.5 line-clamp-2 text-on-surface-low text-[0.75rem] leading-snug">{t.description}</p>
                    {props.length > 0 && <div className="mt-1 text-on-surface-low text-[0.65rem]">{props.length} param{props.length === 1 ? '' : 's'}</div>}
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
    <span className="rounded-pill px-1.5 py-0.5 text-[0.6rem] shrink-0" title={`Risk: ${label}`}
      style={{ background: `color-mix(in srgb, ${color} 16%, transparent)`, color, fontVariationSettings: '"wght" 600' }}>
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
          <div key={f.provider} className="text-[0.78rem] leading-snug">
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
      <button onClick={() => setOpen((v) => !v)} className="mb-s flex items-center gap-s text-on-surface-low hover:text-on-surface transition-colors">
        <ChevronRight size={14} style={{ transform: open ? 'rotate(90deg)' : 'none' }} />
        <Download size={14} />
        <span className="text-[0.7rem] uppercase tracking-wide">Discovered in other tools ({servers.length})</span>
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
                    <span className="truncate font-mono text-on-surface text-[0.84rem]">{s.name}</span>
                    <span className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.62rem]">{s.backend}</span>
                  </div>
                  <p className="mt-0.5 truncate font-mono text-on-surface-low text-[0.7rem]">{s.url || [s.command, ...(s.args ?? [])].join(' ')}</p>
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

const mcpInputCls = 'h-9 w-full rounded-md bg-surface-high px-3 text-[0.8125rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50'

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
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="filesystem-mcp" className={mcpInputCls} />
          </Field>
          <Field label="Command" hint="The executable that starts the server over stdio.">
            <input value={command} onChange={(e) => setCommand(e.target.value)} placeholder="npx" className={mcpInputCls + ' font-mono'} />
          </Field>
          <Field label="Arguments" hint="Space-separated args passed to the command (optional).">
            <input value={args} onChange={(e) => setArgs(e.target.value)} placeholder="-y @modelcontextprotocol/server-filesystem /path" className={mcpInputCls + ' font-mono'} />
          </Field>
          <Field label="Environment" hint="One KEY=value per line (optional).">
            <textarea value={env} onChange={(e) => setEnv(e.target.value)} rows={2} placeholder="API_KEY=sk-…" className={mcpInputCls.replace('h-9', 'min-h-16 py-2') + ' font-mono'} />
          </Field>
        </>) : (<>
          <Field label="Name" hint="A label for this tool server (optional — defaults to the endpoint).">
            <input value={oaName} onChange={(e) => setOaName(e.target.value)} placeholder="my-tools" className={mcpInputCls} />
          </Field>
          <Field label="Endpoint URL" hint="Base URL of an OpenAI-compatible tool server (GET /tools, POST /tools/{name}).">
            <input value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="https://tools.example.com" className={mcpInputCls + ' font-mono'} />
          </Field>
          <Field label="API Key" hint="Optional bearer token for authentication.">
            <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-…" className={mcpInputCls + ' font-mono'} />
          </Field>
          <Field label="Tool filter" hint="Comma-separated tool names to expose. Empty = all.">
            <input value={toolFilter} onChange={(e) => setToolFilter(e.target.value)} placeholder="search, fetch" className={mcpInputCls + ' font-mono'} />
          </Field>
        </>)}

        <div className="flex items-center gap-2">
          <Button size="sm" onClick={kind === 'mcp' ? submitMcp : submitOpenai} disabled={saving || !canSubmit}>{saving ? 'Adding…' : 'Add server'}</Button>
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          {err && <span className="text-[0.78rem]" style={{ color: 'var(--color-danger)' }}>{err}</span>}
        </div>
      </div>
    </Modal>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-on-surface text-[0.82rem]">{label}</div>
      {hint && <div className="mb-1.5 mt-0.5 text-on-surface-low text-[0.72rem]">{hint}</div>}
      {children}
    </div>
  )
}
