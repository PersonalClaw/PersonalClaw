import { useMemo } from 'react'
import { motion } from 'framer-motion'
import {
  Activity, Inbox, ListTodo, Bell, ShieldCheck,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useDashboardLive } from '../DashboardLive'
import { physics } from '../../../design/motion'
import type { RouteProps } from '../../../app/useQueryState'
import { ACTIVE_LOOP_STATUSES } from '../../../lib/loopStatus'


/** Hero Pulse Strip — WS-live at-a-glance counts across the whole system, each a
 *  click-through into its section. Counts bounce on change (the count is the
 *  motion `key`, so it re-mounts + pops when it ticks). Gateway connectivity and
 *  version are NOT here — the shell's top-right SystemWidget owns both (a live
 *  dot on every page + the version in its expanded card).
 *
 *  Two placements, one visible at a time (DashboardPage swaps them at `lg`):
 *  - `header`: compact single-line row, right-aligned in the TopBar. Degrades
 *    in place: full labels from `2xl` up (~1536px, where the five-label row
 *    genuinely fits beside the greeting once the sidebar + shell-corner
 *    clearance are paid for); below that the labels shed and icon + count
 *    carry the signal, with the full reading on the tooltip/aria-label.
 *  - `strip`: the original wrapping body row below the launcher, for below
 *    `lg` (~1024px) where even the minimized header row would crush the
 *    greeting. */
export function HeroPulse({ navigate, variant = 'strip' }: RouteProps & { variant?: 'header' | 'strip' }) {
  const header = variant === 'header'
  const { approvals, inbox, tasks, loops, notifications } = useDashboardLive()

  const runningLoops = useMemo(
    () => loops.filter((l) => ACTIVE_LOOP_STATUSES.has(l.status)).length,
    [loops],
  )
  const unread = useMemo(() => notifications.filter((n) => !n.acked).length, [notifications])

  const pills: { key: string; icon: LucideIcon; n: number; label: string; go: string; tone: string }[] = [
    { key: 'loops', icon: Activity, n: runningLoops, label: runningLoops === 1 ? 'loop running' : 'loops running', go: 'projects', tone: 'var(--color-primary)' },
    { key: 'appr', icon: ShieldCheck, n: approvals.length, label: approvals.length === 1 ? 'approval waiting' : 'approvals waiting', go: 'chat', tone: 'var(--color-warn)' },
    { key: 'tasks', icon: ListTodo, n: tasks.length, label: tasks.length === 1 ? 'task ready' : 'tasks ready', go: 'tasks', tone: 'var(--color-info)' },
    { key: 'inbox', icon: Inbox, n: inbox.length, label: 'inbox', go: 'inbox', tone: 'var(--color-secondary)' },
    { key: 'notif', icon: Bell, n: unread, label: 'unread', go: 'notifications', tone: 'var(--color-on-surface-low)' },
  ]

  return (
    <div className={header ? 'flex items-center gap-xs' : 'flex h-full flex-wrap items-center gap-s'}>
      {pills.map((p) => (
        <button
          key={p.key}
          type="button"
          onClick={() => navigate(p.go)}
          title={header ? `${p.n} ${p.label}` : undefined}
          aria-label={`${p.n} ${p.label}`}
          className={`group flex items-center rounded-pill bg-surface-low transition-colors hover:bg-surface-high ${header ? 'gap-xs px-m py-xs' : 'gap-s px-l py-s'}`}
        >
          <p.icon size={header ? 14 : 16} style={{ color: p.tone }} className="shrink-0" />
          <motion.span
            key={`${p.key}-${p.n}`}
            initial={{ scale: 0.6, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={physics.playful}
            data-type="title-m"
            className="tabular-nums text-on-surface"
          >
            {p.n}
          </motion.span>
          {/* In the header the reading sheds below 2xl (icon + count stay, the
              tooltip carries the words); the body strip always keeps it. */}
          <span data-type="body-m" className={`text-on-surface-low group-hover:text-on-surface-var ${header ? 'hidden 2xl:inline' : ''}`}>{p.label}</span>
        </button>
      ))}
      {/* Neither gateway connectivity NOR version is shown here — the app shell's
          top-right corner carries a live connectivity dot (ui/SystemWidget) on
          every page, and its expanded card carries the gateway version, so the
          dashboard strip is just the at-a-glance count pills. */}
    </div>
  )
}
