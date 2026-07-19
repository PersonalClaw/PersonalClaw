import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Bell, Check, CheckCheck, X } from 'lucide-react'
import { spring, bounce, stagger, listItemEnter } from '../design/motion'
import { api, type NotificationItem } from '../lib/api'
import { useChatSocket, type WsMessage } from '../lib/useChatSocket'
import { useVisiblePoll } from '../lib/useVisiblePoll'
import { kindMeta, relTime, firstLine } from '../pages/notifications/notificationMeta'

const MAX_SHADE = 5

/** Shell-corner notification control: a bell with an unread counter that opens a
 *  shade of the few most-recent notifications. Each can be marked read / dismissed
 *  in place, opened (jumps to the full feed, deep-linked to the item), and the
 *  footer navigates to the all-notifications page. */
export function NotificationBell({ navigate }: { navigate: (path: string) => void }) {
  const [items, setItems] = useState<NotificationItem[] | null>(null)
  const [open, setOpen] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const ref = useRef<HTMLDivElement>(null)

  const load = () => api.notifications().then((d) => setItems(d.notifications)).catch(() => {})
  useVisiblePoll(() => { setNow(Date.now()); load() }, 15000)
  useChatSocket((m: WsMessage) => { if (m.type.startsWith('notification')) load() })

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onEsc)
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onEsc) }
  }, [open])

  const unread = items?.filter((n) => !n.acked).length ?? 0
  // newest first, capped to the shade size
  const recent = items ? [...items].reverse().slice(0, MAX_SHADE) : []

  async function ack(n: NotificationItem) { await api.ackNotification(n.ts).catch(() => {}); load() }
  async function remove(n: NotificationItem) { await api.deleteNotification(n.ts).catch(() => {}); load() }
  async function ackAll() { await api.ackAllNotifications().catch(() => {}); load() }
  function openItem(n: NotificationItem) {
    setOpen(false)
    navigate(`notifications?open=${encodeURIComponent(n.ts)}`)
  }

  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu" aria-expanded={open}
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : 'Notifications'}
        title={unread > 0 ? `${unread} unread notification${unread === 1 ? '' : 's'}` : 'Notifications'}
        className="relative grid size-7 place-items-center rounded-pill transition-colors"
        style={open
          ? { background: 'color-mix(in srgb, var(--color-primary) 16%, transparent)', color: 'var(--color-primary)' }
          : { color: 'var(--color-on-surface-low)' }}>
        <Bell size={16} />
        {/* unread badge springs in on a playful bounce, and RE-POPS on each count
            change (key=unread) so a freshly-arrived notification announces itself
            with a little jump rather than a silent number tick. */}
        <AnimatePresence>
          {unread > 0 && (
            <motion.span key={unread}
              initial={{ scale: 0 }} animate={{ scale: 1 }} exit={{ scale: 0 }}
              transition={bounce.playful}
              className="absolute -right-0.5 -top-0.5 grid min-w-[15px] h-[15px] place-items-center rounded-pill px-1 text-[0.625rem] leading-none"
              style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)', fontVariationSettings: '"wght" 600' }}>
              {unread > 9 ? '9+' : unread}
            </motion.span>
          )}
        </AnimatePresence>
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, scale: 0.96, y: -4 }}
            animate={{ opacity: 1, scale: 1, y: 0, transition: spring.spatialFast }}
            exit={{ opacity: 0, scale: 0.98, transition: spring.effects }}
            style={{ transformOrigin: 'top right', boxShadow: 'var(--shadow-menu)' }}
            className="absolute right-0 top-full z-40 mt-2 w-[340px] overflow-hidden rounded-lgi bg-surface-container">
            <div className="flex items-center justify-between border-b border-outline-variant/40 px-m py-2.5">
              <span data-type="title-s" className="text-on-surface flex items-center gap-s">
                Notifications
                {unread > 0 && <span className="rounded-pill px-1.5 h-5 inline-flex items-center text-[0.7rem]" style={{ background: 'color-mix(in srgb, var(--color-primary) 20%, transparent)', color: 'var(--color-primary)' }}>{unread}</span>}
              </span>
              {unread > 0 && (
                <button type="button" onClick={ackAll}
                  className="inline-flex items-center gap-1 text-[0.75rem] text-on-surface-low hover:text-on-surface transition-colors">
                  <CheckCheck size={13} /> Mark all read
                </button>
              )}
            </div>
            <div className="max-h-[min(60vh,420px)] overflow-y-auto p-1">
              {items === null ? (
                <div className="px-m py-l text-center text-[0.8125rem] text-on-surface-low">Loading…</div>
              ) : recent.length === 0 ? (
                <div className="grid place-items-center px-m py-2xl text-center">
                  <Bell size={22} className="mb-s text-on-surface-low" />
                  <div className="text-[0.875rem] text-on-surface" style={{ fontVariationSettings: '"wght" 500' }}>You're all caught up</div>
                </div>
              ) : (
                <motion.div variants={{ animate: { transition: stagger(0.05) } }} initial="initial" animate="animate">
                  <AnimatePresence initial={false}>
                    {recent.map((n) => <ShadeRow key={n.ts} n={n} now={now} onOpen={() => openItem(n)} onAck={() => ack(n)} onDelete={() => remove(n)} />)}
                  </AnimatePresence>
                </motion.div>
              )}
            </div>
            <button type="button" onClick={() => { setOpen(false); navigate('notifications') }}
              className="block w-full border-t border-outline-variant/40 px-m py-2.5 text-center text-[0.8125rem] text-on-surface-var hover:bg-surface-high hover:text-on-surface transition-colors"
              style={{ fontVariationSettings: '"wght" 500' }}>
              View all notifications
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function ShadeRow({ n, now, onOpen, onAck, onDelete }: { n: NotificationItem; now: number; onOpen: () => void; onAck: () => void; onDelete: () => void }) {
  const km = kindMeta(n.kind)
  return (
    <motion.div layout variants={listItemEnter}
      exit={{ opacity: 0, height: 0, marginTop: 0, transition: spring.spatialFast }}
      className="group relative flex items-start gap-s rounded-md px-2 py-2 cursor-pointer hover:bg-surface-high transition-colors"
      style={n.acked ? undefined : { boxShadow: `inset 2px 0 0 0 ${km.tone}` }} onClick={onOpen}>
      <span className="mt-0.5 shrink-0 inline-flex size-7 items-center justify-center rounded-md" style={{ background: `color-mix(in srgb, ${km.tone} 16%, transparent)` }}><km.icon size={14} style={{ color: km.tone }} /></span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-s">
          <span className={`truncate text-[0.8125rem] ${n.acked ? 'text-on-surface-var' : 'text-on-surface'}`} style={{ fontVariationSettings: '"wght" 500' }}>{n.title}</span>
          <span className="shrink-0 text-on-surface-low text-[0.6875rem]">{relTime(n.ts, now)}</span>
        </div>
        <p className="mt-0.5 truncate text-on-surface-low text-[0.75rem]">{firstLine(n.body)}</p>
      </div>
      <div className="shrink-0 flex items-center opacity-0 group-hover:opacity-100 transition-opacity" onClick={(e) => e.stopPropagation()}>
        {!n.acked && (
          <button type="button" onClick={onAck} aria-label="Mark read" title="Mark read"
            className="grid size-7 place-items-center rounded-pill text-on-surface-low hover:bg-surface-highest hover:text-on-surface transition-colors"><Check size={14} /></button>
        )}
        <button type="button" onClick={onDelete} aria-label="Dismiss" title="Dismiss"
          className="grid size-7 place-items-center rounded-pill text-on-surface-low hover:bg-surface-highest hover:text-on-surface transition-colors"><X size={14} /></button>
      </div>
    </motion.div>
  )
}
