import { useMemo } from 'react'
import { motion } from 'framer-motion'
import { Inbox, ListTodo, ShieldAlert, ShieldCheck } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useDashboardLive } from '../DashboardLive'
import { physics } from '../../../design/motion'
import { toneChipSkin } from '../../../design/accent'
import type { RouteProps } from '../../../app/useQueryState'
import { mirroredApprovalId } from '../../../lib/attentionLanes'


/** Hero Pulse Strip — WS-live counts of what waits on the owner (approvals, tasks ready,
 *  inbox), each a click-through into its section, and only the ones above zero, plus a
 *  "YOLO active" warning while every tool call is auto-approved. Counts bounce on change
 *  (the count is the motion `key`, so it re-mounts + pops when it ticks). Gateway connectivity and
 *  version are NOT here — the shell's top-right SystemWidget owns both (a live
 *  dot on every page + the version in its expanded card).
 *
 *  Two placements, one visible at a time (DashboardPage swaps them at `lg`):
 *  - `header`: compact single-line row, right-aligned in the TopBar. Degrades
 *    in place: full labels from `2xl` up (~1536px, where the three-label row
 *    genuinely fits beside the greeting once the sidebar + shell-corner
 *    clearance are paid for); below that the labels shed and icon + count
 *    carry the signal, with the full reading on the tooltip/aria-label.
 *  - `strip`: the original wrapping body row below the launcher, for below
 *    `lg` (~1024px) where even the minimized header row would crush the
 *    greeting. */
export function HeroPulse({ navigate, variant = 'strip' }: RouteProps & { variant?: 'header' | 'strip' }) {
  const header = variant === 'header'
  const {
    approvals, inbox, tasks,
    approvalsErr, inboxErr, tasksErr, read, status,
  } = useDashboardLive()
  // Every tool call is auto-approved while YOLO is on, the timed mode a chat's YOLO pill turns on
  // included. That is the one fact about the agent's permissions the owner must see wherever the
  // agent can act, so it stays on Home beside the counts until the shell's status line carries it.
  const yolo = !!status?.yolo

  // A loop waiting on the user is counted here all the same: it raises an inbox item, which the
  // inbox pill counts. The "inbox" pill badges MESSAGES needing the user. Proposal mirrors
  // (item_kind 'proposal') are the same skill proposals the Action Center lists
  // with Accept/Reject — counting them here badged "31 inbox" for 1 real
  // message + 30 mirrors, ~doubling the perceived backlog. A pending approval's Inbox
  // row is the same shape of double: the approvals pill beside this one already counts
  // it, so one blocked decision would otherwise read as "1 approval waiting · 1 inbox".
  const inboxMsgs = useMemo(() => {
    const listed = new Set(approvals.map((a) => a.id))
    return inbox.filter((i) => i.item_kind !== 'proposal' && !listed.has(mirroredApprovalId(i))).length
  }, [inbox, approvals])

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
  // 🔴 …AND "NOT READ" HAS TWO CAUSES, OF WHICH ONLY ONE WAS HANDLED. `err ? null : n` covers a
  // read that FAILED. It does not cover a read that has not happened YET — and on the dashboard's
  // first frame every lane is `[]` with no error, so the strip still asserted confident zeros,
  // on the screen the app opens with, for the whole round trip. `read.<slice>` is the missing half.
  //
  // 🪤 AND THE TWO CAUSES MUST NOT SAY THE SAME THING. Both render the em dash, but "couldn't be
  // read" is a FAULT while this is merely pending, so reusing that sentence would claim a failure
  // nobody has measured — the same mirror mistake the note above warns about for tone. Hence three
  // states, and only `failed` speaks of failure.
  type Why = 'ok' | 'pending' | 'failed'
  /** A lane's number, or `null` with the reason it is unknown. */
  const pill = (n: number, err: unknown, wasRead: boolean): { n: number | null; why: Why } => {
    if (err) return { n: null, why: 'failed' }
    if (!wasRead) return { n: null, why: 'pending' }
    return { n, why: 'ok' }
  }
  // Only what WAITS ON THE OWNER is badged: an approval, a task ready for the owner, a message in
  // the owner's inbox. Running loops wait on nobody (the Active work band and #/loops show them), and unread
  // notifications are the shell's bell, which counts them on every page.
  //
  // 🔑 AND A ZERO IS NOT DRAWN. A quiet day used to read "0 approvals waiting · 0 tasks ready ·
  // 0 inbox": statements that nothing is happening, spending the first screen on absence. A count
  // that is UNKNOWN (failed, or not read yet) still draws its dash: hiding it would say "nothing
  // waits" from no data, the mistake the doctrine above exists to prevent. So the filter drops
  // `n === 0` and only that.
  const all: { key: string; icon: LucideIcon; n: number | null; why: Why; label: string; go: string; tone: string }[] = [
    { key: 'appr', icon: ShieldCheck, ...pill(approvals.length, approvalsErr, read.approvals), label: approvals.length === 1 ? 'approval waiting' : 'approvals waiting', go: 'chat', tone: 'var(--color-warn)' },
    { key: 'tasks', icon: ListTodo, ...pill(tasks.length, tasksErr, read.tasks), label: tasks.length === 1 ? 'task ready' : 'tasks ready', go: 'tasks', tone: 'var(--color-info)' },
    { key: 'inbox', icon: Inbox, ...pill(inboxMsgs, inboxErr, read.inbox), label: 'inbox', go: 'inbox', tone: 'var(--color-secondary)' },
  ]
  const pills = all.filter((p) => p.n !== 0)
  // Nothing waits, nothing is unknown and YOLO is off: the strip renders nothing, so the header is
  // the greeting alone.
  if (pills.length === 0 && !yolo) return null
  /** What the pill says. `pending` borrows the app's loading voice (`ui/ListScaffold`'s
   *  "Loading <what>…") rather than inventing a third phrasing for the same idea. */
  const reading = (p: { n: number | null; why: Why; label: string }) =>
    p.why === 'failed' ? `${p.label} — couldn’t be read`
      : p.why === 'pending' ? `Loading ${p.label}…`
        : `${p.n} ${p.label}`

  return (
    <div className={header ? 'flex items-center gap-xs' : 'flex h-full flex-wrap items-center gap-s'}>
      {pills.map((p) => (
        <button
          key={p.key}
          type="button"
          onClick={() => navigate(p.go)}
          // An unknown lane must not SPEAK a number either — the aria-label was the second place
          // the zeros were asserted, and a screen-reader user had no other cue at all.
          title={header ? reading(p) : undefined}
          aria-label={reading(p)}
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
      {yolo && (
        <button
          type="button"
          onClick={() => navigate('settings/agent')}
          title="Every tool call is approved without asking. Open Agent defaults to change it."
          className={`flex items-center rounded-pill transition-opacity hover:opacity-90 ${header ? 'gap-xs px-m py-xs' : 'gap-s px-l py-s'}`}
          style={toneChipSkin('var(--color-warn)', 16)}
        >
          <ShieldAlert size={header ? 14 : 16} className="shrink-0" />
          <span data-type="label-m">YOLO active</span>
        </button>
      )}
      {/* Neither gateway connectivity NOR version is shown here — the app shell's
          top-right corner carries a live connectivity dot (ui/SystemWidget) on
          every page, and its expanded card carries the gateway version, so the
          dashboard strip is just the at-a-glance count pills. */}
    </div>
  )
}
