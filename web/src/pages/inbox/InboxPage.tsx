import { useEffect, useMemo, useRef, useState } from 'react'
import { fvs } from '../../design/fontWeight'
import { Inbox as InboxIcon, CheckCheck, RotateCcw, Circle, Reply, Settings as SettingsIcon, ScrollText, Loader2, ExternalLink, LayoutGrid } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { EmptyState, ListRow, ListSkeleton } from '../../ui/ListScaffold'
import { SidePanel } from '../../ui/SidePanel'
import { ListControls } from '../../ui/ListControls'
import { FilterMenu, type FilterSectionDef } from '../../ui/FilterMenu'
import { Popover, MenuRow } from '../../ui/Popover'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { confirm } from '../../ui/dialog'
import { useQueryParam, useQueryFlag, type RouteProps } from '../../app/useQueryState'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { api, type InboxItem, type InboxStatus } from '../../lib/api'
import { rowSubject } from '../../lib/rowSubject'
import { Segmented } from '../../ui/Segmented'
import { classMeta, confMeta, statusMeta, kindMeta, channelLabel, relPast, isOpen, ITEM_KINDS, NON_CHANNEL_ITEM_KINDS, refTarget, refLabel } from './inboxMeta'
import { InboxDetail } from './InboxDetail'
import { InboxSettingsPanel } from './InboxSettingsPanel'
import { ProposalsLens } from './ProposalsLens'
import { ContextMenu, EntranceGroup, EntranceRegion, type ContextMenuItem } from '../../ui/motion'
import { PageTitle } from '../../ui/PageTitle'

// 'open' means unresolved — pending OR seen. It replaces the old 'pending' key, which
// compared status === 'pending' exactly: once viewing an item marks it SEEN, that filter
// would make the row VANISH from the list the user is looking at, even though they haven't
// dealt with it. "Open" is what the user means by their inbox.
const FILTERS = [
  { key: 'open', label: 'Open' },
  { key: 'needs_reply', label: 'Needs reply' },
  { key: 'all', label: 'All' },
  { key: 'handled', label: 'Done' },
]

/** Inbox = a general triage queue fed by pluggable message-source providers
 *  (filesystem today; Slack/email future). Each item is AI-classified with a
 *  confidence and an optional drafted reply. Header shows source health; rows
 *  triage at a glance; the SidePanel is the full triage workspace. */
export function InboxPage({ query, setQuery, navigate }: Pick<RouteProps, 'query' | 'setQuery' | 'navigate'>) {
  const { data: items, refresh: refreshItems } = useCachedData<InboxItem[]>('inbox:items', () => api.inbox().catch(() => []), { persist: false })
  const { data: status, refresh: refreshStatus } = useCachedData<InboxStatus | null>('inbox:status', () => api.inboxStatus().catch(() => null), { persist: false })
  const [filter, setFilter] = useQueryParam(query, setQuery, 'filter', 'open', { replace: true })
  const [kind, setKind] = useQueryParam(query, setQuery, 'kind', '', { replace: true })
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
      .filter((it) => filter === 'all' ? true : filter === 'open' ? isOpen(it.status) : filter === 'handled' ? (it.status === 'handled' || it.status === 'sent' || it.status === 'dismissed') : filter === 'filtered' ? it.status === 'filtered' : it.classification === filter && isOpen(it.status))
      .filter((it) => !kind || (it.item_kind || 'message') === kind)
      .filter((it) => !n || `${it.sender_name} ${it.channel_name} ${it.message} ${kindMeta(it.item_kind).label}`.toLowerCase().includes(n))
  }, [items, filter, kind, q])
  const open = items?.find((it) => it.id === openId) ?? null

  // P11: fire the "open" engagement signal when the user opens an item's panel. Once per
  // distinct id (a ref-guard so re-renders / a reopen of the same panel don't re-fire).
  // Fire-and-forget + backend-gated — a no-op unless engagement ranking is enabled.
  const openedRef = useRef<string | null>(null)
  useEffect(() => {
    if (openId && open && openedRef.current !== openId) {
      openedRef.current = openId
      api.openInboxItem(openId).catch(() => { /* best-effort signal */ })
      // Opening an item IS having seen it — that is what makes SEEN mean anything. Only
      // for a still-PENDING item: re-marking a resolved one would drag it backwards, and
      // the backend refuses that anyway. Scoped to this id so a re-open doesn't re-fire.
      if (open.status === 'pending') {
        api.markInboxSeen({ ids: [openId] }).then(() => { refreshItems() }).catch(() => { /* non-fatal */ })
      }
    }
    if (!openId) openedRef.current = null
  }, [openId, open])

  const reload = () => { invalidateCache('inbox:items'); invalidateCache('inbox:status'); refreshItems(); refreshStatus() }
  // Dismiss all sweeps EVERY pending item of every kind (proposals, messages, digests
  // alike) with no undo, so it gets the same danger confirm as the analogous
  // Notifications → "Clear all". The count comes from the same status the header shows,
  // so the blast radius is on screen before the click lands.
  async function dismissAll() {
    const n = status?.pending_count ?? 0
    if (!(await confirm({ title: `Dismiss all ${n} pending item${n === 1 ? '' : 's'}?`, body: 'Every pending item of every kind is dismissed at once. There is no undo.', danger: true, confirmLabel: 'Dismiss all' }))) return
    setBusy(true); try { await api.dismissAllInbox(); reload() } finally { setBusy(false) }
  }
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
  // This surface's own definition of "the user has narrowed": a query, a status filter off its
  // default (`open` — NOT `all`, which is the trap the results-announcement rail records), or a kind
  // chip. Shared by the empty state's title AND hint so the two can never disagree about which state
  // the list is in.
  const narrowed = !!(q.trim() || filter !== 'open' || kind)

  // Live per-filter counts so the menu shows where items sit. Counts respect the active
  // KIND chip — a count that ignored it would disagree with the list right beside it.
  const inKind = (it: InboxItem) => !kind || (it.item_kind || 'message') === kind
  const filterCount = (key: string) => {
    if (!items) return undefined
    const scoped = items.filter(inKind)
    if (key === 'all') return scoped.length
    if (key === 'open') return scoped.filter((it) => isOpen(it.status)).length
    if (key === 'handled') return scoped.filter((it) => it.status === 'handled' || it.status === 'sent' || it.status === 'dismissed').length
    if (key === 'filtered') return scoped.filter((it) => it.status === 'filtered').length
    return scoped.filter((it) => it.classification === key && isOpen(it.status)).length
  }
  // Kind chips are driven by what's PRESENT (kinds with zero items are dead controls), and
  // the counts are of OPEN items — the chip badge answers "how much is waiting here".
  const kindChips = useMemo(() => {
    if (!items) return []
    const counts = new Map<string, number>()
    for (const it of items) {
      const k = it.item_kind || 'message'
      counts.set(k, (counts.get(k) ?? 0) + (isOpen(it.status) ? 1 : 0))
    }
    return ITEM_KINDS.filter((k) => counts.has(k.key)).map((k) => ({ ...k, open: counts.get(k.key) ?? 0 }))
  }, [items])
  const filterSections: FilterSectionDef[] = [{
    title: 'Show', value: filter, defaultKey: 'open', onChange: setFilter,
    // The Filtered view is only offered when something is actually withheld — an always-on
    // chip that reads 0 for most users would be a dead control (INU-6).
    options: [
      ...FILTERS.map((f) => ({ key: f.key, label: f.label, count: filterCount(f.key) })),
      ...((filterCount('filtered') ?? 0) > 0 ? [{ key: 'filtered', label: 'Filtered', count: filterCount('filtered') }] : []),
    ],
  }]
  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          // The name and the count are SEPARATE children so the count can shrink first: a
          // `truncate` on this flex container does nothing (it has no text of its own), which
          // left the row 111px under the controls at 390px. Now "Inbox" holds its width and
          // the secondary count truncates — the same shape `notifications` uses.
          left={<PageTitle className="flex min-w-0 items-baseline gap-s"><span className="shrink-0">Inbox</span> {status && <span className="min-w-0 truncate text-on-surface-low text-[0.75rem] font-normal">{status.pending_count} pending · {status.total_count} total</span>}</PageTitle>}
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
                      <div className="px-m pt-1 pb-1.5 text-[0.75rem] uppercase tracking-wide text-on-surface-low">Catch-up digest · last 4h</div>
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
                  <HeaderControl icon={CheckCheck} label="Dismiss all" danger priority="low" onClick={dismissAll} disabled={busy} />
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
          : undefined}
          // `active` compares against the DEFAULT filter, not 'all': inbox opens on 'open', so
          // `filter !== 'all'` was true on mount and the list announced "39 items" before the user
          // did anything. The announcement is for a query the USER made.
          results={{ count: (filtered ?? []).length, noun: 'items', active: narrowed }}>
          <FilterMenu sections={filterSections} label="Show" />
        </ListControls>
      }
      panel={
        <>
          {open && (
            <SidePanel key={open.id} fillHeight storeKey="inbox-panel-w" urlKey={{ key: 'open', setQuery }} icon={(() => { const cm = classMeta(open.classification); return <cm.icon size={18} style={{ color: cm.tone }} /> })()} title={open.sender_name || open.sender_id || 'Item'} onClose={() => setOpenId("")}>
              <InboxDetail item={open} onChanged={load} navigate={navigate} />
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
      {/* The inbox body is one ENTRANCE GROUP (FLUID-MOTION §S3 T3.2): the source-health
          banner lands, then the queue — context first, then the work, rather than the
          two arriving in the same frame. This surface has exactly those two regions, and
          the group sits above BOTH the `status` fetch and the items fetch, so the live
          WebSocket pushes this page takes all day (`inbox_new_item`) re-render inside a
          mounted group and never replay the entrance (see `ui/motion/Entrance`). */}
      <EntranceGroup>
        {/* source health banner — the native agent→inbox source is ALWAYS active
            (push), so the inbox is never "off"; poll providers are extra. */}
        {status && (() => {
          const pollActive = (status.sources ?? []).filter((s) => s.kind === 'poll' && s.active)
          const hasPollProviders = (status.sources ?? []).some((s) => s.kind === 'poll')
          return (
            <EntranceRegion className="mx-auto w-full px-l" style={{ maxWidth: 'var(--content-width)' }}>
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
            </EntranceRegion>
          )
        })()}

        {/* `data-tour="inbox"` — the product tour's inbox stop points at the queue column
            (ONBOARDING-UX T5.1). The wrapper, not the list, so an empty inbox still anchors.
            The region wraps it rather than replacing it: the tour anchor and the centered
            column stay one element, so neither the tour nor the layout learns about motion. */}
        <EntranceRegion>
        <div data-tour="inbox" className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {/* Kind chips. Only rendered once MORE THAN ONE kind is present: on an inbox that
            only ever receives messages, a single "Messages" chip is a control with nothing
            to choose. Uses the canonical Segmented so it matches every other pick-one in
            the app rather than inventing a chip style here. */}
        {kindChips.length > 1 && (
          <div className="mb-m">
            <Segmented
              size="sm"
              collapse="scroll"
              ariaLabel="Filter by kind"
              value={kind || 'all'}
              onChange={(k) => setKind(k === 'all' ? '' : k)}
              options={[
                { key: 'all', label: 'All', icon: LayoutGrid },
                ...kindChips.map((k) => ({
                  key: k.key,
                  label: k.open > 0 ? `${k.label} ${k.open}` : k.label,
                  icon: k.icon,
                  tone: k.tone,
                  title: `${k.label} — ${k.open} open`,
                })),
              ]}
            />
          </div>
        )}
        {/* INU-7 — the Proposals LENS. Narrowing to `proposal` swaps the triage list for a
            surface built for deciding: each row shows what approving would DO (its apply
            case), batch-approve is offered only within one (provenance, kind) group, and an
            editable payload can be edited before it is applied. A proposal row in the plain
            list could only be opened, one at a time. */}
        {filtered !== null && kind === 'proposal' ? (
          <ProposalsLens items={filtered} onChanged={reload} />
        ) : filtered === null ? <ListSkeleton rows={6} what="items" /> : filtered.length === 0 ? (
          // 🪤 NARROWED FIRST, THEN THE BLANK SLATE. The title always distinguished the two
          // ('Nothing here' vs 'Inbox zero'), but the HINT tested `disabled` first — so a user with
          // items who searched for something that does not match was told "Enable a source to begin",
          // advice for a completely different problem, under a title saying their filter found
          // nothing. Measured on `#/inbox`: filtering to no matches rendered the blank-slate
          // onboarding paragraph. Eleven other list surfaces answer the same state with "Try a
          // different …"; this is the twelfth.
          <EmptyState icon={InboxIcon}
            title={narrowed ? 'Nothing here' : 'Inbox zero'}
            hint={narrowed
              ? (kind ? `No ${kindMeta(kind).label.toLowerCase()} matches the current search or filter.` : 'Try a different search or filter.')
              : disabled
                ? 'Inbox collects messages, questions, and notifications from your agents and connected sources (filesystem and Slack; email coming). Enable a source to begin.'
                : 'Messages your agents and connected sources surface for triage land here. You’re all caught up.'} />
        ) : (
          <div className="flex flex-col gap-s">
            {filtered.map((it, i) => {
              const cm = classMeta(it.classification)
              const cf = confMeta(it.confidence)
              const sm = statusMeta(it.status)
              const km = kindMeta(it.item_kind)
              const unread = it.status === 'pending'
              const open = isOpen(it.status)
              // A non-channel kind (needs_input, proposal, …) has no sender and no channel:
              // its `sender_name` is the emitting subsystem. Rendering "Unknown" + a
              // #channel chip for it would be noise pretending to be provenance, so those
              // rows lead with the KIND and its own icon instead.
              const channelBacked = !NON_CHANNEL_ITEM_KINDS.includes(it.item_kind || 'message')
              const target = refTarget(it)
              // Right-click / long-press → scoped actions. Only "open" is wired at
              // the row level here (triage actions live in the detail panel); reuse
              // the SAME open the row's onClick calls — no duplicated behavior.
              const menuItems: ContextMenuItem[] = [
                { icon: <InboxIcon size={15} />, label: 'Open', onSelect: () => setOpenId(it.id) },
              ]
              if (target) menuItems.push({ icon: <ExternalLink size={15} />, label: refLabel(it), onSelect: () => navigate(target) })
              // The accent rail marks UNREAD only. Keeping it on `seen` would leave every
              // glanced-at row visually shouting, which is the noise this plan set out to fix.
              const accentTone = channelBacked ? cm.tone : km.tone
              return (
                <ContextMenu key={it.id} items={menuItems}>
                {/* 🔴 THE ROW WAS NAMED BY ITS KIND, NOT ITS IDENTITY. `km.label` is the kind word, so
                    measured on this surface: **39 rows → 3 distinct names**, 35 of them "Proposals"
                    (36 buttons whose whole computed name is a kind word). A screen-reader user tabbing
                    the inbox heard "Proposals, button" thirty-five times while every row's own text
                    identified it. Same defect cycle 141 measured for rows named by kind, and the helper
                    cycle 142 built for it — join the identifying parts, drop repeats, cap at 55 — is
                    what `#/notifications` already uses for exactly this. The sender stays first for a
                    channel-backed row (that IS its identity); the message line distinguishes the rest.

                    🪤 `firstLine()` — which `#/notifications` uses — was WRONG here, and only measuring
                    showed it: an inbox message wraps its subject onto several lines, so the first line
                    is often just "Refine a skill" and 34 rows still collapsed to one name. The visible
                    `<p>` renders those newlines as spaces, so a sighted user reads the whole subject.
                    Collapsing whitespace is what matches what is on screen. */}
                <ListRow index={i} accent={unread ? accentTone : undefined} onClick={() => setOpenId(it.id)}
                  label={rowSubject([channelBacked ? (it.sender_name || it.sender_id || 'Unknown') : km.label, (it.message ?? '').replace(/\s+/g, ' ')])}>
                  <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: `color-mix(in srgb, ${accentTone} 16%, transparent)` }}>
                    {channelBacked ? <cm.icon size={18} style={{ color: cm.tone }} /> : <km.icon size={18} style={{ color: km.tone }} />}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-s">
                      <span className={`truncate text-[0.9375rem] ${open ? 'text-on-surface' : 'text-on-surface-var'}`} style={fvs(500)}>{channelBacked ? (it.sender_name || it.sender_id || 'Unknown') : km.label}</span>
                      {channelBacked && channelLabel(it) && <span className="shrink-0 text-on-surface-low text-[0.75rem]">{channelLabel(it)}</span>}
                      {!channelBacked && target && <span className="shrink-0 inline-flex items-center gap-1 text-on-surface-low text-[0.75rem]"><ExternalLink size={11} /> deep link</span>}
                      {it.draft && <span className="shrink-0 inline-flex items-center gap-1 text-ok text-[0.75rem]"><Reply size={11} /> draft</span>}
                    </div>
                    <p className="mt-0.5 truncate text-on-surface-low text-[0.8125rem]">{it.message}</p>
                  </div>
                  <div className="hidden sm:flex shrink-0 items-center gap-s">
                    {/* Confidence is a TRIAGE judgment; a needs_input row was never triaged,
                        so showing "needs review" against it would invent a verdict. */}
                    {channelBacked && <span className="inline-flex items-center gap-1 text-[0.75rem]" style={{ color: cf.tone }} title={cf.label}><cf.icon size={12} /></span>}
                    {!open ? <span className="inline-flex items-center gap-1 text-on-surface-low text-[0.75rem]"><sm.icon size={12} style={{ color: sm.tone }} /> {sm.label}</span> : it.created_at && <span className="text-on-surface-low text-[0.75rem]">{relPast(it.created_at)}</span>}
                    {unread && <Circle size={7} fill={accentTone} stroke="none" />}
                  </div>
                </ListRow>
                </ContextMenu>
              )
            })}
          </div>
        )}
        </div>
        </EntranceRegion>
      </EntranceGroup>
    </WorkbenchLayout>
  )
}
