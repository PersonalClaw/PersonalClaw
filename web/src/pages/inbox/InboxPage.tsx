import { useEffect, useMemo, useRef, useState } from 'react'
import { Inbox as InboxIcon, CheckCheck, RotateCcw, Circle, Reply, Settings as SettingsIcon, ScrollText, Loader2 } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { EmptyState, ListRow, ListSkeleton } from '../../ui/ListScaffold'
import { SidePanel } from '../../ui/SidePanel'
import { ListControls } from '../../ui/ListControls'
import { FilterMenu, type FilterSectionDef } from '../../ui/FilterMenu'
import { Popover, MenuRow } from '../../ui/Popover'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { useQueryParam, useQueryFlag, type RouteProps } from '../../app/useQueryState'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { api, type InboxItem, type InboxStatus } from '../../lib/api'
import { classMeta, confMeta, statusMeta, channelLabel, relPast } from './inboxMeta'
import { InboxDetail } from './InboxDetail'
import { InboxSettingsPanel } from './InboxSettingsPanel'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'

const FILTERS = [
  { key: 'pending', label: 'Pending' },
  { key: 'needs_reply', label: 'Needs reply' },
  { key: 'all', label: 'All' },
  { key: 'handled', label: 'Done' },
]

/** Inbox = a general triage queue fed by pluggable message-source providers
 *  (filesystem today; Slack/email future). Each item is AI-classified with a
 *  confidence and an optional drafted reply. Header shows source health; rows
 *  triage at a glance; the SidePanel is the full triage workspace. */
export function InboxPage({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  const { data: items, refresh: refreshItems } = useCachedData<InboxItem[]>('inbox:items', () => api.inbox().catch(() => []), { persist: false })
  const { data: status, refresh: refreshStatus } = useCachedData<InboxStatus | null>('inbox:status', () => api.inboxStatus().catch(() => null), { persist: false })
  const [filter, setFilter] = useQueryParam(query, setQuery, 'filter', 'pending', { replace: true })
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [openIdRaw, setOpenId] = useQueryParam(query, setQuery, 'open', '')
  const openId = openIdRaw || null
  const [settingsOpen, setSettingsOpen] = useQueryFlag(query, setQuery, 'settings')
  const [busy, setBusy] = useState(false)

  const load = () => { refreshItems(); refreshStatus() }
  // Live: triage layer pushes new/updated items over the shared WS.
  useChatSocket((m: WsMessage) => { if (m.type === 'inbox_item_updated' || m.type === 'inbox_new_item') load() })

  const filtered = useMemo(() => {
    if (!items) return null
    const n = q.trim().toLowerCase()
    return items
      .filter((it) => filter === 'all' ? true : filter === 'pending' ? it.status === 'pending' : filter === 'handled' ? (it.status === 'handled' || it.status === 'sent' || it.status === 'dismissed') : it.classification === filter && it.status === 'pending')
      .filter((it) => !n || `${it.sender_name} ${it.channel_name} ${it.message}`.toLowerCase().includes(n))
  }, [items, filter, q])
  const open = items?.find((it) => it.id === openId) ?? null

  // P11: fire the "open" engagement signal when the user opens an item's panel. Once per
  // distinct id (a ref-guard so re-renders / a reopen of the same panel don't re-fire).
  // Fire-and-forget + backend-gated — a no-op unless engagement ranking is enabled.
  const openedRef = useRef<string | null>(null)
  useEffect(() => {
    if (openId && open && openedRef.current !== openId) {
      openedRef.current = openId
      api.openInboxItem(openId).catch(() => { /* best-effort signal */ })
    }
    if (!openId) openedRef.current = null
  }, [openId, open])

  const reload = () => { invalidateCache('inbox:items'); invalidateCache('inbox:status'); refreshItems(); refreshStatus() }
  async function dismissAll() { setBusy(true); try { await api.dismissAllInbox(); reload() } finally { setBusy(false) } }
  async function restart() { setBusy(true); try { await api.restartInbox(); setTimeout(reload, 800) } finally { setBusy(false) } }
  // Generate a catch-up digest for a channel → arrives as a new inbox item (also
  // pushed live over the WS); open it so the user lands on the summary.
  async function digest(channelId: string) {
    setBusy(true)
    try { const it = await api.digestInboxChannel(channelId); reload(); if (it?.id) setOpenId(it.id) }
    finally { setBusy(false) }
  }
  // Digest picker channels: watched channels ∪ channels present in stored items
  // (the backend digests any channel's stored items — gating on watched_channels
  // alone made Digest unreachable for filesystem/native items). Agent-pushed
  // items all share the synthetic "agent" channel; skip it (a digest of your own
  // agents' pings is noise) unless it's all there is? No — skip it always.
  const watched = useMemo(() => {
    const byId = new Map<string, { id: string; name: string }>()
    for (const ch of status?.watched_channels ?? []) byId.set(ch.id, ch)
    for (const it of items ?? []) {
      if (it.channel && it.channel !== 'agent' && it.source !== 'digest' && !byId.has(it.channel)) {
        byId.set(it.channel, { id: it.channel, name: it.channel_name || it.channel })
      }
    }
    return Array.from(byId.values())
  }, [status, items])

  const health = status?.health
  const disabled = status ? !status.enabled : false

  // Live per-filter counts so the menu shows where items sit.
  const filterCount = (key: string) => {
    if (!items) return undefined
    if (key === 'all') return items.length
    if (key === 'pending') return items.filter((it) => it.status === 'pending').length
    if (key === 'handled') return items.filter((it) => it.status === 'handled' || it.status === 'sent' || it.status === 'dismissed').length
    return items.filter((it) => it.classification === key && it.status === 'pending').length
  }
  const filterSections: FilterSectionDef[] = [{
    title: 'Show', value: filter, defaultKey: 'pending', onChange: setFilter,
    options: FILTERS.map((f) => ({ key: f.key, label: f.label, count: filterCount(f.key) })),
  }]
  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={<span data-type="title-l" className="text-on-surface flex items-baseline gap-s">Inbox {status && <span className="text-on-surface-low text-[0.75rem] font-normal">{status.pending_count} pending · {status.total_count} total</span>}</span>}
          right={
            // The header has room now (search/filter live on the page), so surface
            // the actions directly — the cluster collapses them (icon-only → …) if tight.
            <div className="flex items-center gap-1">
              {/* Channel digest — only meaningful when channels are watched. Picks a
                  channel, generates a catch-up summary that lands as a new item. */}
              {watched.length > 0 && (
                <Popover placement="bottom" align="right" trigger={(open, toggle) => (
                  <button onClick={toggle} disabled={busy}
                    className="inline-flex items-center gap-1.5 rounded-pill h-9 px-m text-[0.8125rem] text-on-surface-var hover:bg-surface-high hover:text-on-surface transition-colors disabled:opacity-40"
                    style={{ background: open ? 'var(--color-surface-high)' : undefined }}
                    title="Generate a catch-up digest for a watched channel">
                    {busy ? <Loader2 size={15} className="animate-spin" /> : <ScrollText size={15} />}
                    <span className="hidden sm:inline">Digest</span>
                  </button>
                )}>
                  {(close) => (
                    <div className="flex flex-col gap-0.5" style={{ minWidth: 220 }}>
                      <div className="px-m pt-1 pb-1.5 text-[0.7rem] uppercase tracking-wide text-on-surface-low">Catch-up digest · last 4h</div>
                      {watched.map((ch) => (
                        <MenuRow key={ch.id} icon={<ScrollText size={15} />} label={ch.name || ch.id}
                          onClick={() => { close(); digest(ch.id) }} />
                      ))}
                    </div>
                  )}
                </Popover>
              )}
              <HeaderActions>
                {(status?.pending_count ?? 0) > 0 && (
                  <HeaderControl icon={CheckCheck} label="Dismiss all" priority="primary" onClick={dismissAll} disabled={busy} />
                )}
                <HeaderControl icon={RotateCcw} label="Restart sources" priority="low" onClick={restart} disabled={busy} />
                <HeaderControl icon={SettingsIcon} label="Inbox settings" active={settingsOpen} priority="low" onClick={() => setSettingsOpen(!settingsOpen)} />
              </HeaderActions>
            </div>
          }
        />
      }
      controls={
        <ListControls search={(items === undefined || items.length > 0)
          ? { value: q, onChange: setQ, placeholder: 'Search inbox', label: 'Search inbox' }
          : undefined}>
          <FilterMenu sections={filterSections} label="Show" />
        </ListControls>
      }
      panel={
        <>
          {open && (
            <SidePanel key={open.id} fillHeight storeKey="inbox-panel-w" urlKey={{ key: 'open', setQuery }} icon={(() => { const cm = classMeta(open.classification); return <cm.icon size={18} style={{ color: cm.tone }} /> })()} title={open.sender_name || open.sender_id || 'Item'} onClose={() => setOpenId("")}>
              <InboxDetail item={open} onChanged={load} />
            </SidePanel>
          )}
          {settingsOpen && (
            <SidePanel key="inbox-settings" fillHeight storeKey="inbox-panel-w" urlKey={{ key: 'settings', setQuery }} icon={<SettingsIcon size={18} className="text-primary" />} title="Inbox settings" onClose={() => setSettingsOpen(false)}>
              <InboxSettingsPanel />
            </SidePanel>
          )}
        </>
      }
    >
      {/* source health banner — the native agent→inbox source is ALWAYS active
          (push), so the inbox is never "off"; poll providers are extra. */}
      {status && (() => {
        const pollActive = (status.sources ?? []).filter((s) => s.kind === 'poll' && s.active)
        const hasPollProviders = (status.sources ?? []).some((s) => s.kind === 'poll')
        return (
          <div className="mx-auto w-full px-l" style={{ maxWidth: 'var(--content-width)' }}>
            <div className="flex items-center gap-s rounded-md px-m py-2 text-[0.8125rem]" style={{ background: 'var(--color-surface-container)' }}>
              <span className="relative flex size-2">
                <span className="relative inline-flex size-2 rounded-pill" style={{ background: 'var(--color-ok)' }} />
              </span>
              <span className="text-on-surface-var">
                Native source active — agents post here directly.
                {pollActive.length > 0
                  ? ` Also polling ${pollActive.map((s) => s.name).join(', ')}${health?.last_poll_at ? ` · last checked ${relPast(health.last_poll_at)}` : ''}.`
                  : hasPollProviders ? ' Connect a message source (filesystem/Slack) to collect more.' : ''}
              </span>
            </div>
          </div>
        )
      })()}

      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {filtered === null ? <ListSkeleton rows={6} /> : filtered.length === 0 ? (
          <EmptyState icon={InboxIcon} title={q || filter !== 'pending' ? 'Nothing here' : 'Inbox zero'} hint={disabled ? 'Inbox collects messages, questions, and notifications from your agents and connected sources (filesystem and Slack; email coming). Enable a source to begin.' : 'Messages your agents and connected sources surface for triage land here. You’re all caught up.'} />
        ) : (
          <div className="flex flex-col gap-s">
            {filtered.map((it, i) => {
              const cm = classMeta(it.classification)
              const cf = confMeta(it.confidence)
              const sm = statusMeta(it.status)
              const pending = it.status === 'pending'
              // Right-click / long-press → scoped actions. Only "open" is wired at
              // the row level here (triage actions live in the detail panel); reuse
              // the SAME open the row's onClick calls — no duplicated behavior.
              const menuItems: ContextMenuItem[] = [
                { icon: <InboxIcon size={15} />, label: 'Open', onSelect: () => setOpenId(it.id) },
              ]
              return (
                <ContextMenu key={it.id} items={menuItems}>
                <ListRow index={i} accent={pending ? cm.tone : undefined} onClick={() => setOpenId(it.id)}>
                  <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: `color-mix(in srgb, ${cm.tone} 16%, transparent)` }}><cm.icon size={18} style={{ color: cm.tone }} /></span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-s">
                      <span className={`truncate text-[0.9375rem] ${pending ? 'text-on-surface' : 'text-on-surface-var'}`} style={{ fontVariationSettings: '"wght" 500' }}>{it.sender_name || it.sender_id || 'Unknown'}</span>
                      {channelLabel(it) && <span className="shrink-0 text-on-surface-low text-[0.75rem]">{channelLabel(it)}</span>}
                      {it.draft && <span className="shrink-0 inline-flex items-center gap-1 text-ok text-[0.7rem]"><Reply size={11} /> draft</span>}
                    </div>
                    <p className="mt-0.5 truncate text-on-surface-low text-[0.8125rem]">{it.message}</p>
                  </div>
                  <div className="hidden sm:flex shrink-0 items-center gap-s">
                    <span className="inline-flex items-center gap-1 text-[0.7rem]" style={{ color: cf.tone }} title={cf.label}><cf.icon size={12} /></span>
                    {!pending ? <span className="inline-flex items-center gap-1 text-on-surface-low text-[0.7rem]"><sm.icon size={12} style={{ color: sm.tone }} /> {sm.label}</span> : it.created_at && <span className="text-on-surface-low text-[0.75rem]">{relPast(it.created_at)}</span>}
                    {pending && <Circle size={7} fill={cm.tone} stroke="none" />}
                  </div>
                </ListRow>
                </ContextMenu>
              )
            })}
          </div>
        )}
      </div>
    </WorkbenchLayout>
  )
}
