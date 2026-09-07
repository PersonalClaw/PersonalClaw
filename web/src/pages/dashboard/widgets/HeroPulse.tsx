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
  const {
    approvals, inbox, tasks, loops, notifications,
    approvalsErr, inboxErr, tasksErr, loopsErr, notificationsErr,
  } = useDashboardLive()

  const runningLoops = useMemo(
    () => loops.filter((l) => ACTIVE_LOOP_STATUSES.has(l.status)).length,
    [loops],
  )
  const unread = useMemo(() => notifications.filter((n) => !n.acked).length, [notifications])
  // The "inbox" pill badges MESSAGES needing the user. Proposal mirrors
  // (item_kind 'proposal') are the same skill proposals the Action Center lists
  // with Accept/Reject — counting them here badged "31 inbox" for 1 real
  // message + 30 mirrors, ~doubling the perceived backlog.
  const inboxMsgs = useMemo(() => inbox.filter((i) => i.item_kind !== 'proposal').length, [inbox])

  // 🔴 A COUNT IS A CLAIM, AND `0` IS THE MOST REASSURING ONE THIS STRIP CAN MAKE.
  // Every lane below arrives as `[]` when its read fails, so before this the hero — the FIRST
  // thing the app shows — stated "0 loops running · 0 approvals waiting · 0 tasks ready · 0 inbox
  // · 0 unread" as fact whenever the gateway was unreachable. Five confident zeros, and the
  // `aria-label` spoke them too, so the screen-reader reading was just as wrong.
  //
  // `null` means "not read", and it renders as an em dash rather than a number. This is the
  // doctrine `dashboard/healthUnknown.test.tsx` already states for the health strip — "on a health
  // surface, silence is a claim" — applied to the counted lanes, including its nuance: the unknown
  // pill keeps its NEUTRAL tone and is not styled as an alarm. We do not know anything is wrong,
  // only that we could not look, and claiming a fault we have not measured is the mirror mistake.
  const pills: { key: string; icon: LucideIcon; n: number | null; label: string; go: string; tone: string }[] = [
    { key: 'loops', icon: Activity, n: loopsErr ? null : runningLoops, label: runningLoops === 1 ? 'loop running' : 'loops running', go: 'projects', tone: 'var(--color-primary)' },
    { key: 'appr', icon: ShieldCheck, n: approvalsErr ? null : approvals.length, label: approvals.length === 1 ? 'approval waiting' : 'approvals waiting', go: 'chat', tone: 'var(--color-warn)' },
    { key: 'tasks', icon: ListTodo, n: tasksErr ? null : tasks.length, label: tasks.length === 1 ? 'task ready' : 'tasks ready', go: 'tasks', tone: 'var(--color-info)' },
    { key: 'inbox', icon: Inbox, n: inboxErr ? null : inboxMsgs, label: 'inbox', go: 'inbox', tone: 'var(--color-secondary)' },
    { key: 'notif', icon: Bell, n: notificationsErr ? null : unread, label: 'unread', go: 'notifications', tone: 'var(--color-on-surface-low)' },
  ]

  return (
    <div className={header ? 'flex items-center gap-xs' : 'flex h-full flex-wrap items-center gap-s'}>
      {pills.map((p) => (
        <button
          key={p.key}
          type="button"
          onClick={() => navigate(p.go)}
          // An unknown lane must not SPEAK a number either — the aria-label was the second place
          // the zeros were asserted, and a screen-reader user had no other cue at all.
          title={header ? (p.n === null ? `${p.label} — couldn’t be read` : `${p.n} ${p.label}`) : undefined}
          aria-label={p.n === null ? `${p.label} — couldn’t be read` : `${p.n} ${p.label}`}
          className={`group flex items-center rounded-pill bg-surface-low transition-colors hover:bg-surface-high ${header ? 'gap-xs px-m py-xs' : 'gap-s px-l py-s'}`}
        >
          <p.icon size={header ? 14 : 16} style={{ color: p.tone }} className="shrink-0" />
          <motion.span
            key={`${p.key}-${p.n}`}
            initial={{ scale: 0.6, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={physics.playful}
            data-type="title-m"
            // The dash is dimmer than a real count, which is the whole visual message: this is
            // an absence of information, not a measured value. Still not danger-toned.
            className={`tabular-nums ${p.n === null ? 'text-on-surface-low' : 'text-on-surface'}`}
          >
            {p.n === null ? '—' : p.n}
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
