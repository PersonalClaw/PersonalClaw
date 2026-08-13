import { useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { Bell, Check, CheckCheck, Trash2, Undo2, X, Target } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { InvestigateButton } from '../../ui/InvestigateButton'
import { FilterMenu, type FilterSectionDef } from '../../ui/FilterMenu'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { SidePanel } from '../../ui/SidePanel'
import { ListControls } from '../../ui/ListControls'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { Markdown } from '../../ui/Markdown'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { spring } from '../../design/motion'
import { confirm } from '../../ui/dialog'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { rowSubject } from '../../lib/rowSubject'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { api, type NotificationItem } from '../../lib/api'
import { useAutonomyLadder } from '../../lib/rungs'
import { kindMeta, kindsPresent, bucketOf, BUCKET_ORDER, relTime, clockTime, firstLine, unreadRail, toneChipBg } from './notificationMeta'
import { fvs } from '../../design/fontWeight'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { PageTitle } from '../../ui/PageTitle'
import { accentChip } from '../../design/accent'
import { notify } from '../../app/appSdk'

/** Notifications = a triage feed of agent/schedule/trigger/task events. Items are
 *  keyed by `ts`; the backend supports ack / unack / ack-all / delete / clear
 *  (all by ts). Filter by read-state + kind; grouped Today / Yesterday / Earlier.
 *  Live via the shared WS `notification_ack` / new-notification events. */


export function NotificationsPage({ query, setQuery, navigate }: Pick<RouteProps, 'query' | 'setQuery' | 'navigate'>) {
  const [filter, setFilter] = useQueryParam(query, setQuery, 'filter', 'all', { replace: true })  // 'all' | 'unread' | <kind>
  const [openTsRaw, setOpenTs] = useQueryParam(query, setQuery, 'open', '')
  const openTs = openTsRaw || null
  const [now, setNow] = useState(() => Date.now())

  // Cached feed (instant paint on revisit) that still polls — persist:false so the
  // live read-state never goes stale across a hard reload.
  // No `.catch(() => [])`. An empty list is a CLAIM, and this page states it out loud: with the feed at
  // 500 the surface rendered "You're all caught up", a reassuring sentence produced by a failed request.
  // The read POLLS every 10s, so the failure belongs in the render (once) rather than in a toast (every
  // tick) — hence `LoadError` below rather than `notify`.
  const { data: items, error: loadErr, refresh } = useCachedData('notifications', () => api.notifications().then((d) => d.notifications), { persist: false })
  // A mutation reloads against the changed feed; the WS + interval just revalidate.
  const load = () => { invalidateCache('notifications'); refresh() }
  useEffect(() => {
    const t = window.setInterval(() => { setNow(Date.now()); refresh() }, 10000)
    return () => clearInterval(t)
  }, [refresh])
  useChatSocket((m: WsMessage) => { if (m.type.startsWith('notification')) refresh() })

  // The undo affordance on an `auto_with_undo` notice (AUTONOMY-GUARDRAILS §6.1). The button
  // is rendered from the persisted reversal RECORD, not from the `reversal_id` sitting on the
  // notification: a record that has already been undone (here, in Settings, or on another
  // machine) must not still offer an undo, and the notification itself never changes once
  // written. So the id is the key and the ladder read is the state.
  const { ladder, refresh: refreshLadder } = useAutonomyLadder()
  const undoable = useMemo(
    () => new Set((ladder?.reversals ?? []).filter((r) => !r.reversed_at).map((r) => r.id)),
    [ladder],
  )
  async function undoAction(n: NotificationItem) {
    if (!n.reversal_id) return
    try {
      await api.autonomyUndo(n.reversal_id)
      notify(`Undone. ${n.action_type || 'That action'} will not do this on its own any more.`, 'success')
    } catch (e) {
      notify(`Couldn't undo: ${String((e as Error)?.message || e)}`, 'error')
    }
    invalidateCache('autonomy:ladder'); refreshLadder()
  }

  // newest first (the log is appended chronologically)
  const ordered = useMemo(() => (items ? [...items].reverse() : null), [items])
  const unread = items?.filter((n) => !n.acked).length ?? 0
  const kinds = useMemo(() => (items ? kindsPresent(items) : []), [items])

  const filtered = useMemo(() => {
    if (!ordered) return null
    return ordered.filter((n) => filter === 'all' ? true : filter === 'unread' ? !n.acked : (n.kind || 'info') === filter)
  }, [ordered, filter])

  // group filtered into Today / Yesterday / Earlier (order preserved within)
  const groups = useMemo(() => {
    const g: Record<string, NotificationItem[]> = {}
    for (const n of filtered ?? []) { const b = bucketOf(n.ts, now); (g[b] ??= []).push(n) }
    return g
  }, [filtered, now])

  const open = items?.find((n) => n.ts === openTs) ?? null

  // Every mutation below is followed by `load()`, so a swallowed failure REVERTS the row and the click
  // reads as a no-op — the user marks something read, it stays unread, and nothing explains why. Same
  // `notify` shape the settings panels use.
  async function ack(n: NotificationItem) {
    await api.ackNotification(n.ts).catch((e) => notify(`Couldn't mark this notification read: ${String((e as Error)?.message || e)}`, 'error'))
    load()
  }
  async function unack(n: NotificationItem) {
    await api.unackNotification(n.ts).catch((e) => notify(`Couldn't mark this notification unread: ${String((e as Error)?.message || e)}`, 'error'))
    load()
  }
  async function remove(n: NotificationItem) {
    await api.deleteNotification(n.ts).catch((e) => notify(`Couldn't delete this notification: ${String((e as Error)?.message || e)}`, 'error'))
    if (openTs === n.ts) setOpenTs("")
    load()
  }
  async function ackAll() {
    await api.ackAllNotifications().catch((e) => notify(`Couldn't mark all notifications read: ${String((e as Error)?.message || e)}`, 'error'))
    load()
  }
  // The worst member of the family: the user CONFIRMS a destructive action, it fails, the list comes
  // back unchanged, and silence reads as "cleared".
  async function clearAll() {
    if (!(await confirm({ title: 'Clear all notifications?', danger: true, confirmLabel: 'Clear all' }))) return
    await api.clearNotifications().catch((e) => notify(`Couldn't clear notifications: ${String((e as Error)?.message || e)}`, 'error'))
    setOpenTs("")
    load()
  }

  const filterSection: FilterSectionDef = useMemo(() => ({
    title: 'Show', value: filter, defaultKey: 'all', onChange: setFilter,
    options: [
      { key: 'all', label: 'All', count: items?.length ?? 0 },
      { key: 'unread', label: 'Unread', count: unread },
      ...kinds.map((k) => ({ key: k, label: kindMeta(k).label, count: items?.filter((n) => (n.kind || 'info') === k).length ?? 0 })),
    ],
  }), [items, unread, kinds, filter, setFilter])

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={<PageTitle className="flex items-center gap-s">Notifications {unread > 0 && <span className="rounded-pill px-2 h-5 inline-flex items-center text-[0.75rem]" style={accentChip}>{unread}</span>}</PageTitle>}
          right={items && items.length > 0 ? (
            // Direct header actions (the header is otherwise empty now that the
            // filter lives on the page); each collapses to an icon when tight.
            <HeaderActions>
              {unread > 0 && <HeaderControl icon={CheckCheck} label="Mark all read" priority="primary" onClick={ackAll} />}
              <HeaderControl icon={Trash2} label="Clear all" danger priority="low" onClick={clearAll} />
            </HeaderActions>
          ) : undefined}
        />
      }
      controls={(items === undefined || items.length > 0)
        ? <ListControls results={{ count: (filtered ?? []).length, noun: 'notifications', active: filter !== 'all' }}>
            <FilterMenu sections={[filterSection]} />
          </ListControls>
        : undefined}
      panel={open && (
        <SidePanel key={open.ts} fillHeight storeKey="notif-panel-w" icon={(() => { const km = kindMeta(open.kind); return <km.icon size={18} style={{ color: km.tone }} /> })()} title={open.title} onClose={() => setOpenTs("")}>
          <div className="flex flex-col gap-l">
            <div className="flex flex-wrap items-center gap-s text-[0.8125rem]">
              {(() => { const km = kindMeta(open.kind); return <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7" style={{ background: toneChipBg(km.tone), color: km.tone }}><km.icon size={13} /> {km.label}</span> })()}
              <span className="text-on-surface-low">{clockTime(open.ts)}</span>
              {open.acked && <span className="text-on-surface-low inline-flex items-center gap-1"><Check size={13} /> read</span>}
            </div>
            <div className="text-on-surface-var text-[0.9375rem] leading-relaxed"><Markdown>{open.body}</Markdown></div>
            <div className="flex flex-wrap gap-s border-t border-outline-variant/40 pt-l">
              {/* A loop notification carries the loop_id — let the user jump straight to
                  it (a 'needs your input' / 'stalled' notice is actionable). Route by the
                  loop KIND: a code loop lives at #/code/<id> (the mini-IDE cockpit), every
                  other kind (general/goal/design) at #/loops/<id>. The label stays neutral
                  "Open loop" for the latter — "Open goal" mislabeled a general/design loop. */}
              {open.loop_id && (
                <Button size="sm" onClick={() => { ack(open); navigate(`${open.loop_kind === 'code' ? 'code' : 'loops'}/${open.loop_id}`) }}>
                  <Target size={14} /> {open.loop_kind === 'code' ? 'Open project' : 'Open loop'}
                </Button>
              )}
              {/* Undo the action this notice is ABOUT — offered only while the reversal record
                  is still pending. The label says both halves of what the click does, because
                  "your automation will stop doing this by itself" is not a consequence to
                  discover afterwards. */}
              {open.reversal_id && undoable.has(open.reversal_id) && (
                <Button size="sm" variant="secondary" onClick={() => { undoAction(open); ack(open) }}>
                  <Undo2 size={14} /> Undo & stop doing this automatically
                </Button>
              )}
              {open.acked
                ? <Button size="sm" variant="ghost" onClick={() => unack(open)}><Undo2 size={14} /> Mark unread</Button>
                : <Button size="sm" variant="secondary" onClick={() => ack(open)}><Check size={14} /> Mark read</Button>}
              <Button size="sm" variant="ghost" onClick={() => remove(open)}><Trash2 size={14} /> Delete</Button>
            </div>
          </div>
        </SidePanel>
      )}
    >
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {items === undefined && loadErr ? (
          // Before the skeleton branch, or a failed first read spins it forever.
          <LoadError what="notifications" error={loadErr} onRetry={load} />
        ) : filtered === null ? <ListSkeleton rows={6} what="notifications" /> : items && items.length === 0 ? (
          <EmptyState icon={Bell} title="You're all caught up" hint="Schedule runs, trigger fires, agent updates, and task results surface here for you to review." />
        ) : (
          <>
            {filtered.length === 0 ? (
              <div className="text-center text-on-surface-low text-[0.8125rem] py-2xl">Nothing matches this filter.</div>
            ) : (
              <div className="flex flex-col gap-l">
                {BUCKET_ORDER.filter((b) => groups[b]?.length).map((b) => (
                  <div key={b}>
                    <div className="mb-s text-on-surface-low text-[0.75rem] uppercase tracking-wide">{b}</div>
                    <div className="flex flex-col gap-s">
                      {groups[b].map((n, i) => <Row key={n.ts} n={n} index={i} now={now} onOpen={() => setOpenTs(n.ts)} onAck={() => ack(n)} onUnack={() => unack(n)} onDelete={() => remove(n)} />)}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </WorkbenchLayout>
  )
}

function Row({ n, index, now, onOpen, onAck, onUnack, onDelete }: { n: NotificationItem; index: number; now: number; onOpen: () => void; onAck: () => void; onUnack: () => void; onDelete: () => void }) {
  const km = kindMeta(n.kind)
  // Right-click / long-press → the same actions the row's click + hover buttons
  // already wire (open, ack/unack by read-state, delete) — the shared ContextMenu
  // primitive, no duplicated logic.
  const menuItems: ContextMenuItem[] = [
    { icon: <Bell size={15} />, label: 'Open', onSelect: onOpen },
    n.acked
      ? { icon: <Undo2 size={15} />, label: 'Mark unread', onSelect: onUnack }
      : { icon: <Check size={15} />, label: 'Mark read', onSelect: onAck },
    { icon: <Trash2 size={15} />, label: 'Delete', onSelect: onDelete, danger: true },
  ]
  return (
    <ContextMenu items={menuItems}>
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: Math.min(index * 0.03, 0.3) }}
      className="group relative flex items-center gap-m rounded-lg bg-surface-container px-m py-2.5 cursor-pointer hover:bg-surface-high transition-colors"
      style={unreadRail(km.tone, n.acked)} onClick={onOpen}>
      <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: toneChipBg(km.tone) }}><km.icon size={19} style={{ color: km.tone }} /></span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-s">
          <span className={`truncate text-[0.9375rem] ${n.acked ? 'text-on-surface-var' : 'text-on-surface'}`} style={fvs(500)}>{n.title}</span>
          <span className="shrink-0 text-on-surface-low text-[0.75rem]">{relTime(n.ts, now)}</span>
        </div>
        <p className="mt-0.5 truncate text-on-surface-low text-[0.8125rem]">{firstLine(n.body)}</p>
      </div>
      <div className="shrink-0 flex items-center opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity" onClick={(e) => e.stopPropagation()}>
        {/* Investigate (plan 60): a failure notification carries the link to what
            failed, so the chat opens with the run/job state already resolved. */}
        {/* Each of these acts on THIS notification, and there are 83 rows here. Measured in Chrome's
            AX tree before naming them: 83x "Investigate in chat", 83x "Delete", 81x "Mark unread" —
            three names for 247 controls.

            🪤 `n.title` ALONE IS NOT THE ROW, and re-measuring proved it: naming from the title left
            35x "…: Refine a skill" and 26x "…: Loop progress", because the title is a KIND here, not
            an identity. The row shows title + the body's first line, and so does the name — capped,
            because the other half of this cycle fixed a tile named by 695 characters of its body. */}
        <InvestigateButton kind="notification" id={n.ts} backLink="#/notifications" size={34}
          label={`Investigate in chat: ${rowSubject([n.title, firstLine(n.body ?? '')])}`} />
        {n.acked
          ? <IconButton icon={Undo2} label={`Mark unread: ${rowSubject([n.title, firstLine(n.body ?? '')])}`} title="Mark unread" size={34} onClick={onUnack} />
          : <IconButton icon={Check} label={`Mark read: ${rowSubject([n.title, firstLine(n.body ?? '')])}`} title="Mark read" size={34} onClick={onAck} />}
        <IconButton icon={X} label={`Delete: ${rowSubject([n.title, firstLine(n.body ?? '')])}`} title="Delete" size={34} onClick={onDelete} />
      </div>
    </motion.div>
    </ContextMenu>
  )
}


