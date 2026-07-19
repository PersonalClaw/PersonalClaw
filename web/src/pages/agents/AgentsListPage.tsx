import { useMemo, useState } from 'react'
import { Plus, Star, Users, Lock, Cpu, Wrench, Sparkles, Zap, RefreshCw } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { ListControls } from '../../ui/ListControls'
import { EmptyState, ListRow, ListSkeleton } from '../../ui/ListScaffold'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { SidePanel } from '../../ui/SidePanel'
import { useAgentsData, type NativeGroup, type DiscoveredGroup } from './agentsData'
import { providerMeta, isReservedAgent } from './agentMeta'
import { NativeAgentDetail, DiscoveredAgentDetail } from './AgentDetail'
import { api, type SavedAgent, type DiscoveredAgent } from '../../lib/api'
import { useConfigFsWatch } from '../../lib/useConfigFsWatch'
import { useQueryParam, useEditFlag, type RouteProps } from '../../app/useQueryState'

type Open =
  | { kind: 'native'; name: string }
  | { kind: 'discovered'; providerId: string; id: string }

/** Decode the `?open=` composite ref back into an Open.
 *  Encoding uses `|` as the field separator (not `:`) because provider IDs
 *  themselves contain colons (e.g. "acp:kiro-cli"). */
function parseOpen(raw: string): Open | null {
  if (!raw) return null
  if (raw.startsWith('native:')) return { kind: 'native', name: raw.slice(7) }
  // New encoding: acp|<providerId>|<agentId>
  if (raw.startsWith('acp|')) {
    const parts = raw.split('|')
    if (parts.length >= 3) return { kind: 'discovered', providerId: parts[1], id: parts.slice(2).join('|') }
  }
  // Legacy encoding: acp:<providerId>:<agentId> — only works when providerId has no colons.
  if (raw.startsWith('acp:')) {
    const rest = raw.slice(4)
    const i = rest.indexOf(':')
    if (i > 0) return { kind: 'discovered', providerId: rest.slice(0, i), id: rest.slice(i + 1) }
  }
  return null
}

/** Agents library — ALL agent definitions, grouped by source: Native (editable
 *  PClaw profiles) + each ACP runtime's discovered agents (read-only). Fixes the
 *  legacy pain point where only native definitions showed. */
export function AgentsListPage({ onCreate, query, setQuery }: { onCreate: () => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const { groups, loading, reload } = useAgentsData()
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  // `open` is a composite ref encoded into one query param:
  //   native:<name>  |  acp:<providerId>:<id>
  const [openRaw] = useQueryParam(query, setQuery, 'open', '')
  const open: Open | null = parseOpen(openRaw)
  const [editing, setEditing] = useEditFlag(query, setQuery)
  const setOpen = (o: Open | null) => {
    // Opening/switching a row lands in view mode; closing clears both keys.
    // Use `|` separator for discovered agents (provider IDs contain colons).
    if (!o) return setQuery({ open: null, edit: null })
    setQuery({ open: o.kind === 'native' ? `native:${o.name}` : `acp|${o.providerId}|${o.id}`, edit: null })
  }

  const native = groups.find((g): g is NativeGroup => g.kind === 'native')
  const discovered = groups.filter((g): g is DiscoveredGroup => g.kind === 'discovered')

  const n = q.trim().toLowerCase()
  const match = (s: string) => !n || s.toLowerCase().includes(n)

  const openNative = open?.kind === 'native' ? native?.agents.find((a) => a.name === open.name) ?? null : null
  const openDiscovered = useMemo(() => {
    if (open?.kind !== 'discovered') return null
    const g = discovered.find((d) => d.providerId === open.providerId)
    return g?.agents.find((a) => a.id === open.id) ?? null
  }, [open, discovered])

  async function setDefault(name: string) {
    await api.setDefaultAgent(name).catch(() => {})
    reload()
  }
  const [syncing, setSyncing] = useState(false)
  async function syncAgents() {
    setSyncing(true)
    try { await api.syncAgents() } catch { /* best-effort */ }
    setSyncing(false); reload()
  }

  // Filesystem-as-truth (#44): an agent profile edited on disk / by an agent
  // live-refreshes the list. Hold off while a detail panel is open so a reload
  // can't clobber an in-flight view/edit; reopening picks up the change.
  useConfigFsWatch(open === null, (path) => {
    if (path.includes('/agents/') || path.endsWith('config.json')) reload()
  })

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={<span data-type="title-l" className="text-on-surface">Agents</span>}
          right={<HeaderActions>
            <HeaderControl icon={RefreshCw} label={syncing ? 'Syncing…' : 'Sync agents'} priority="low" onClick={syncAgents} />
            <HeaderControl icon={Plus} label="New agent" variant="primary" priority="primary" onClick={onCreate} />
          </HeaderActions>}
        />
      }
      controls={<ListControls search={{ value: q, onChange: setQ, placeholder: 'Search agents', label: 'Search agents' }} />}
      panel={
        <>
          {openNative && native && (
            <SidePanel key={`n:${openNative.name}`} fillHeight storeKey="agent-panel-w" icon={<Users size={18} className="text-primary" />} title={openNative.name} onClose={() => setOpen(null)}>
              <NativeAgentDetail agent={openNative} isDefault={native.defaultAgent === openNative.name} editing={editing} onEditingChange={setEditing}
                onSaved={reload} onDeleted={() => { setOpen(null); reload() }} onSetDefault={() => setDefault(openNative.name)} />
            </SidePanel>
          )}
          {openDiscovered && open?.kind === 'discovered' && (
            <SidePanel key={`d:${open.id}`} fillHeight storeKey="agent-panel-w" icon={(() => { const pm = providerMeta(open.providerId); return <pm.icon size={18} style={{ color: pm.tone }} /> })()} title={openDiscovered.name} onClose={() => setOpen(null)}>
              <DiscoveredAgentDetail agent={openDiscovered} providerId={open.providerId} />
            </SidePanel>
          )}
        </>
      }
    >
      <div className="mx-auto px-l py-l flex flex-col gap-2xl" style={{ maxWidth: 'var(--content-width)' }}>
        {loading && groups.length === 0 ? <ListSkeleton rows={6} /> : (
              <>
                {/* Native */}
                {native && (
                  <GroupSection title="Native" icon={Users} tone="var(--color-primary)" subtitle="Your PersonalClaw agent definitions — fully editable." count={native.agents.length}>
                    {native.agents.filter((a) => match(`${a.name} ${a.description ?? ''}`)).length === 0 ? (
                      <EmptyState icon={Users} title="No native agents" hint="Create an agent to define its model, system prompt, skills, tools, triggers, and workflows." action={{ label: 'New agent', onClick: onCreate, icon: Plus }} />
                    ) : (
                      <div className="flex flex-col gap-s">
                        {native.agents.filter((a) => match(`${a.name} ${a.description ?? ''}`)).map((a, i) => (
                          <NativeRow key={a.name} agent={a} index={i} isDefault={native.defaultAgent === a.name} onClick={() => setOpen({ kind: 'native', name: a.name })} />
                        ))}
                      </div>
                    )}
                  </GroupSection>
                )}

                {/* Discovered per ACP provider */}
                {discovered.map((g) => {
                  const pm = providerMeta(g.providerId)
                  const items = g.agents.filter((a) => match(`${a.name} ${a.description ?? ''}`))
                  return (
                    <GroupSection key={g.providerId} title={pm.label} icon={pm.icon} tone={pm.tone} count={g.agents.length}
                      subtitle={g.ready ? 'Provided by the runtime — read-only.' : `Unavailable — ${g.detail || 'runtime not ready'}`} ready={g.ready}>
                      {!g.ready ? null : items.length === 0 ? (
                        <p className="text-on-surface-low text-[0.8125rem]">No agents discovered.</p>
                      ) : (
                        <div className="flex flex-col gap-s">
                          {items.map((a, i) => (
                            <DiscoveredRow key={a.id} agent={a} index={i} tone={pm.tone} icon={pm.icon} onClick={() => setOpen({ kind: 'discovered', providerId: g.providerId, id: a.id })} />
                          ))}
                        </div>
                      )}
                    </GroupSection>
                  )
                })}
              </>
            )}
      </div>
    </WorkbenchLayout>
  )
}

function GroupSection({ title, icon: Icon, tone, subtitle, count, ready = true, children }: {
  title: string; icon: typeof Users; tone: string; subtitle: string; count: number; ready?: boolean; children: React.ReactNode
}) {
  return (
    <section>
      <div className="mb-m flex items-center gap-s">
        <Icon size={16} style={{ color: tone }} />
        <span className="text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 600' }}>{title}</span>
        <span className="text-on-surface-low text-[0.75rem] tabular-nums">{count}</span>
        {!ready && <span className="inline-flex items-center gap-1 text-on-surface-low text-[0.7rem]"><Lock size={11} /> unavailable</span>}
        <span className="ml-auto text-on-surface-low text-[0.75rem]">{subtitle}</span>
      </div>
      {children}
    </section>
  )
}

function NativeRow({ agent, index, isDefault, onClick }: { agent: SavedAgent; index: number; isDefault: boolean; onClick: () => void }) {
  // Right-click / long-press → scoped actions. This row only performs "open" (the
  // detail panel owns save/delete/set-default); a single-item menu still aids
  // discoverability, matching the shared ContextMenu idiom used across rows.
  const menuItems: ContextMenuItem[] = [
    { icon: <Users size={15} />, label: 'Open', onSelect: onClick },
  ]
  return (
    <ContextMenu items={menuItems}>
    <ListRow index={index} accent="var(--color-primary)" onClick={onClick}>
      <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: 'color-mix(in srgb, var(--color-primary) 16%, transparent)' }}><Users size={19} className="text-primary" /></span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-s">
          <span className="truncate text-on-surface text-[0.9375rem] font-mono" style={{ fontVariationSettings: '"wght" 500' }}>{agent.name}</span>
          {isDefault && <span className="shrink-0 inline-flex items-center gap-1 text-primary text-[0.7rem]"><Star size={11} fill="currentColor" /> default</span>}
          {isReservedAgent(agent) && <span className="shrink-0 inline-flex items-center gap-1 text-on-surface-low text-[0.7rem]"><Lock size={10} /> built-in</span>}
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-m gap-y-0.5 text-on-surface-low text-[0.8125rem]">
          {agent.model && <span className="font-mono text-[0.75rem]">{agent.model}</span>}
          {agent.description && <span className="truncate">· {agent.description}</span>}
        </div>
      </div>
      <div className="hidden sm:flex shrink-0 items-center gap-m text-on-surface-low text-[0.7rem]">
        {(agent.skills?.length ?? 0) > 0 && <span className="inline-flex items-center gap-1"><Sparkles size={11} /> {agent.skills!.length}</span>}
        {(agent.tools?.length ?? 0) > 0 && <span className="inline-flex items-center gap-1"><Wrench size={11} /> {agent.tools!.length}</span>}
        {(agent.triggers?.length ?? 0) > 0 && <span className="inline-flex items-center gap-1"><Zap size={11} /> {agent.triggers!.length}</span>}
      </div>
    </ListRow>
    </ContextMenu>
  )
}

function DiscoveredRow({ agent, index, tone, icon: Icon, onClick }: { agent: DiscoveredAgent; index: number; tone: string; icon: typeof Cpu; onClick: () => void }) {
  // Discovered agents are runtime-provided + read-only, so "open" (the detail
  // panel) is the only action this row performs — a single-item ContextMenu,
  // consistent with the shared right-click idiom across list rows.
  const menuItems: ContextMenuItem[] = [
    { icon: <Icon size={15} />, label: 'Open', onSelect: onClick },
  ]
  return (
    <ContextMenu items={menuItems}>
    <ListRow index={index} onClick={onClick}>
      <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: `color-mix(in srgb, ${tone} 16%, transparent)` }}><Icon size={19} style={{ color: tone }} /></span>
      <div className="flex-1 min-w-0">
        <span className="block truncate text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 500' }}>{agent.name}</span>
        {agent.description && <p className="mt-0.5 truncate text-on-surface-low text-[0.8125rem]">{agent.description}</p>}
      </div>
      <Lock size={13} className="shrink-0 text-on-surface-low" />
    </ListRow>
    </ContextMenu>
  )
}
