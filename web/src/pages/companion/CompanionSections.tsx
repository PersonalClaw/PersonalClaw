import { useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  Activity, Bell, Check, CheckCheck, Inbox, ListTodo, MessageSquarePlus,
  Pause, Play, Square, X,
} from 'lucide-react'
import {
  api,
  type InboxItem, type Loop, type NotificationItem, type TaskItem,
  type TaskStatus, type UnifiedLoopStatus,
} from '../../lib/api'
import { invalidateKeys, useQuery } from '../../lib/data'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { Button } from '../../ui/Button'
import { TextArea } from '../../ui/forms'
import { useCompanionAction } from './useCompanionAction'

/** `#/companion`'s non-approval sections (MOBILE-COMPANION `MC-6`, the former S2
 *  T2.1/T2.2 breadth deferred by the 2026-07-26 amendment).
 *
 *  Four sections, one shape each: read a list, render the honest state (error before
 *  empty — always), and offer the one or two actions a phone is actually good for.
 *  All four share `useCompanionAction`, so the optimistic-with-revert contract and the
 *  reconcile-against-the-server rule are written once (see that file's trap note).
 *
 *  WHAT THIS DELIBERATELY IS NOT: a second dashboard. Every section is a SHORT list of
 *  what wants a decision now — not a filterable table, not a detail view, not a place
 *  to create anything. Anything past the decision is a tap through to the real surface,
 *  because a phone-sized reimplementation of `#/loops` would be a fork of the UI and
 *  the plan's §Wrapper tier is explicit that there is no forked UI.
 *
 *  Each list is capped at `LIMIT` rows and says so out loud when it truncates. A
 *  silently-truncated attention list is the same lie as an unreconciled optimistic
 *  hide: the user reads "this is everything" and it is not.
 */

/** Rows per section. Small on purpose — this is a triage surface, not a table. */
const LIMIT = 6

/** 🪤 EVERY KEY HERE IS NAMED AFTER ITS COLLECTION, NOT AFTER THIS READER.
 *
 *  `companion:tasks` was the obvious first choice and it is the exact defect
 *  `lib/splitCollectionBusts.test.ts` was written for: a key named after its reader sits in a
 *  namespace the collection's own invalidation can never reach. `#/tasks` busts with
 *  `invalidateKeys('tasks', true)` — prefix mode over the `tasks` namespace — so a phone key
 *  called `companion:tasks` would be dropped by nothing, ever, and the phone would paint a
 *  task's pre-edit state on its next mount. The reverse matters more: a task finished on the
 *  phone must staleten the desktop list, which is why every action below busts the COLLECTION
 *  prefix rather than calling its own `refresh()`.
 *
 *  `-companion` (not `companion:`) is what puts them in the collection's namespace — the rail
 *  derives the namespace as the segment before `:`, or before the first `-`. Same shape as the
 *  existing `tasks-all`.
 *
 *  `companion:approvals` (in `CompanionPage`) deliberately keeps its reader-shaped name:
 *  `GET /api/approvals` has exactly one reader, so there is no collection to split.
 *
 *  🪤 AND EACH KEY IS WRITTEN INLINE AT ITS `useQuery`, NOT HOISTED TO A CONSTANT. That census
 *  matches a LITERAL first argument, so a hoisted-constant one is invisible to it. Measured:
 *  with the four keys behind constants, reverting one to `companion:tasks` left that whole
 *  suite GREEN — hoisting them would have hidden this file from the one rail that caught the
 *  mistake in the first place. `keys.ts` carries their freshness policy; the literals stay
 *  here, at the call site the census reads. */

/** Post-action step for every section: bust the whole COLLECTION, not this reader's key.
 *
 *  Prefix mode reaches both this key and the owning surface's, so finishing a task on the
 *  phone stalens `#/tasks` too. `invalidateKeys` bumps the entry's epoch, which is what a
 *  mounted `useQuery` re-runs on — so this refreshes the section as well and no separate
 *  `refresh()` is needed. Calling `refresh()` here instead would repaint the phone and leave
 *  every other reader of the same collection holding a value the phone just invalidated. */
const bustLoops = () => invalidateKeys('loops', true)
const bustTasks = () => invalidateKeys('tasks', true)
const bustInbox = () => invalidateKeys('inbox', true)
const bustFeed = () => invalidateKeys('notifications', true)

/** The shared section shell: a named landmark, a heading with a live count, and the
 *  three load branches in the ONE correct order.
 *
 *  🔴 ERROR IS TESTED BEFORE EMPTY. `data === undefined` is true for loading, error AND
 *  the never-fetched case, so an `empty` branch placed first makes the error branch
 *  unreachable — and on an attention surface "nothing needs you" is the single most
 *  dangerous thing to say when the truth is "we could not ask". Same rule as the
 *  approvals section above it in `CompanionPage`.
 */
function Section<T>({ id, icon: Icon, title, what, query, empty, children }: {
  id: string
  icon: LucideIcon
  title: string
  /** The plural noun for the announcements — "running loops", "tasks". */
  what: string
  query: { data: T[] | undefined; loading: boolean; error?: unknown; refresh: () => void }
  empty: { title: string; hint: string }
  children: (rows: T[]) => React.ReactNode
}) {
  const { data, loading, error, refresh } = query
  const rows = data ?? []
  const shown = rows.slice(0, LIMIT)
  return (
    <section aria-labelledby={`${id}-heading`} className="flex flex-col gap-s">
      <h2 id={`${id}-heading`} data-type="title-m" className="flex items-center gap-s text-on-surface">
        <Icon size={16} aria-hidden className="text-on-surface-low" />
        {title}{rows.length > 0 ? ` (${rows.length})` : ''}
      </h2>
      {data === undefined && error ? (
        <LoadError what={what} error={error} onRetry={refresh} />
      ) : data === undefined && loading ? (
        <ListSkeleton rows={2} what={what} />
      ) : rows.length === 0 ? (
        <EmptyState icon={CheckCheck} title={empty.title} hint={empty.hint} />
      ) : (
        <>
          {children(shown)}
          {rows.length > shown.length && (
            <p data-type="body-m" className="text-on-surface-low">
              Showing {shown.length} of {rows.length}. Open the full dashboard for the rest.
            </p>
          )}
        </>
      )}
    </section>
  )
}

/** One row of any section: a tappable-height card with a title line and an action row. */
function Row({ title, sub, meta, actions }: {
  title: string; sub?: string; meta?: string; actions: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-m rounded-lg border border-outline-variant/40 px-l py-l">
      <div className="min-w-0">
        <p data-type="title-m" className="truncate text-on-surface">{title}</p>
        {sub && <p data-type="body-m" className="line-clamp-2 text-on-surface-var">{sub}</p>}
        {meta && <p data-type="body-m" className="text-on-surface-low">{meta}</p>}
      </div>
      <div className="flex flex-wrap gap-s">{actions}</div>
    </div>
  )
}

// ── Running loops ────────────────────────────────────────────────────────────
//
// "Running" means STEERABLE, not literally `status === 'running'`: a loop that is
// paused, stalled, blocked or waiting on input is exactly what a phone should show,
// because those are the ones a person can unstick from a bus stop. A loop that has
// finished, failed or never launched is not a decision, so it stays off the phone.
const STEERABLE: readonly UnifiedLoopStatus[] = ['running', 'paused', 'needs_input', 'stagnant', 'blocked']

export function RunningLoopsSection() {
  // Read-only consumption of `loop_routes`: list + PATCH action + nudge, exactly the
  // three the atom names. No loop endpoint is invented or reshaped here.
  const query = useQuery<Loop[]>('loops-companion', () =>
    api.uLoops().then((ls) => ls.filter((l) => STEERABLE.includes(l.status))))
  const { act, view, busy } = useCompanionAction<{ status: UnifiedLoopStatus }>(query.data)
  const [nudging, setNudging] = useState<string | null>(null)
  const [text, setText] = useState('')

  const send = async (loop: Loop) => {
    const body = text.trim()
    if (!body) return
    // The composer closes optimistically. On failure it comes BACK with the text still
    // in it — retyping a nudge you already wrote is the worst possible apology.
    setNudging(null)
    const ok = await act(loop.id, { status: view(loop.id, loop).status }, () => api.uLoopNudge(loop.id, body),
      `nudge ${loop.name}`, bustLoops)
    if (ok) setText('')
    else setNudging(loop.id)
  }

  return (
    <Section id="companion-loops" icon={Activity} title="Running" what="running loops" query={query}
      empty={{ title: 'Nothing running', hint: 'Loops you start appear here while they run.' }}>
      {(loops) => loops.map((raw) => {
        const l = view(raw.id, raw)
        const working = busy.has(l.id)
        return (
          <div key={l.id} className="flex flex-col gap-m">
            <Row title={l.name || l.task} sub={l.name ? l.task : undefined}
              meta={`${l.status.replace(/_/g, ' ')} · cycle ${l.total_cycles}`}
              actions={<>
                {l.status === 'running' ? (
                  <Button size="sm" variant="secondary" loading={working}
                    ariaLabel={`Pause ${l.name || l.task}`}
                    onClick={() => act(l.id, { status: 'paused' }, () => api.uLoopAction(l.id, 'pause'), `pause ${l.name}`, bustLoops)}>
                    <Pause size={15} /> Pause
                  </Button>
                ) : (
                  <Button size="sm" variant="secondary" loading={working}
                    ariaLabel={`Resume ${l.name || l.task}`}
                    onClick={() => act(l.id, { status: 'running' }, () => api.uLoopAction(l.id, 'resume'), `resume ${l.name}`, bustLoops)}>
                    <Play size={15} /> Resume
                  </Button>
                )}
                <Button size="sm" variant="secondary" ariaExpanded={nudging === l.id}
                  ariaLabel={`Nudge ${l.name || l.task}`}
                  onClick={() => { setNudging(nudging === l.id ? null : l.id); setText('') }}>
                  <MessageSquarePlus size={15} /> Nudge
                </Button>
                <Button size="sm" variant="danger" loading={working}
                  ariaLabel={`Stop ${l.name || l.task}`}
                  onClick={() => act(l.id, { status: 'stopped' }, () => api.uLoopAction(l.id, 'stop'), `stop ${l.name}`, bustLoops)}>
                  <Square size={15} /> Stop
                </Button>
              </>} />
            {nudging === l.id && (
              <div className="flex flex-col gap-s rounded-lg border border-outline-variant/40 px-l py-l">
                <TextArea value={text} onChange={setText} rows={3} autoFocus
                  ariaLabel={`What should ${l.name || l.task} do next?`}
                  placeholder="Steer it — what should it do next?" />
                {/* A disabled control that will not say why is a dead end for a keyboard or
                    screen-reader user — `ui/disabledReason*` is the house rail for it. */}
                <Button size="sm" onClick={() => send(l)} disabled={!text.trim()}
                  disabledReason="Type what it should do next.">
                  Send nudge
                </Button>
              </div>
            )}
          </div>
        )
      })}
    </Section>
  )
}

// ── Tasks ────────────────────────────────────────────────────────────────────
//
// Two status-scoped reads rather than one wide one. `GET /api/tasks` takes a single
// `status` and a `limit`, so an unfiltered read would let a project's DONE history eat
// the window and the phone would say "nothing to pick up" while open tasks existed.
// Both legs share one query key, so a failure in either paints ONE LoadError.
const OPEN_STATUSES: readonly TaskStatus[] = ['in_progress', 'open']
// `TaskItem.status` is typed `string` (the backend serves several providers, not one enum),
// so the membership test is a string set — not `OPEN_STATUSES.includes`, which would demand a
// narrowing the wire type cannot give.
const IS_OPEN: ReadonlySet<string> = new Set(OPEN_STATUSES)

export function TasksSection() {
  const query = useQuery<TaskItem[]>('tasks-companion', () =>
    Promise.all(OPEN_STATUSES.map((s) => api.tasks({ status: s, limit: 20 })))
      .then((pages) => pages.flatMap((p) => p.tasks)))
  const { act, view, busy } = useCompanionAction<{ status: TaskStatus }>(query.data)

  const move = (t: TaskItem, status: TaskStatus, verb: string) =>
    act(t.id, { status }, () => api.updateTask(t.id, { status }), `${verb} "${t.title}"`, bustTasks)

  return (
    <Section id="companion-tasks" icon={ListTodo} title="Tasks" what="tasks" query={query}
      empty={{ title: 'No open tasks', hint: 'Tasks assigned in a project appear here while they are open.' }}>
      {(tasks) => tasks
        // A row optimistically moved to done/cancelled leaves the list immediately; the
        // reconciling fetch is what makes that permanent.
        .filter((raw) => IS_OPEN.has(view(raw.id, raw).status))
        .map((raw) => {
          const t = view(raw.id, raw)
          const working = busy.has(t.id)
          return (
            <Row key={t.id} title={t.title} sub={t.description}
              meta={[t.status.replace(/_/g, ' '), t.priority, t.project].filter(Boolean).join(' · ')}
              actions={<>
                {t.status === 'open' && (
                  <Button size="sm" variant="secondary" loading={working} ariaLabel={`Start ${t.title}`}
                    onClick={() => move(t, 'in_progress', 'start')}>
                    <Play size={15} /> Start
                  </Button>
                )}
                <Button size="sm" loading={working} ariaLabel={`Mark ${t.title} done`}
                  onClick={() => move(t, 'done', 'finish')}>
                  <Check size={15} /> Done
                </Button>
              </>} />
          )
        })}
    </Section>
  )
}

// ── Inbox ────────────────────────────────────────────────────────────────────
//
// Resolve only — plan 42's attention lifecycle (PENDING → SEEN → HANDLED | DISMISSED)
// through the routes plan 42 owns (`PUT /api/inbox/{id}` with a `status`). No second
// notion of "dealt with" is minted here, and no reply/draft path: composing a reply is
// a desk job, deciding whether something still needs one is not.
export function InboxSection() {
  const query = useQuery<InboxItem[]>('inbox-companion', () => api.inboxPending())
  const { act, view, busy } = useCompanionAction<{ status: InboxItem['status'] }>(query.data)

  const resolve = (i: InboxItem, status: 'handled' | 'dismissed', verb: string) =>
    act(i.id, { status }, () => api.updateInboxItem(i.id, { status }), `${verb} this ${i.item_kind || 'message'}`, bustInbox)

  return (
    <Section id="companion-inbox" icon={Inbox} title="Inbox" what="inbox items" query={query}
      empty={{ title: 'Inbox clear', hint: 'Messages and requests waiting on you appear here.' }}>
      {(items) => items
        .filter((raw) => view(raw.id, raw).status === 'pending')
        .map((raw) => {
          const i = view(raw.id, raw)
          const working = busy.has(i.id)
          const who = i.sender_name || i.channel_name || i.channel || 'Unknown sender'
          return (
            <Row key={i.id} title={who} sub={i.message}
              meta={[i.item_kind?.replace(/_/g, ' '), i.classification.replace(/_/g, ' ')].filter(Boolean).join(' · ')}
              actions={<>
                <Button size="sm" loading={working} ariaLabel={`Mark the message from ${who} handled`}
                  onClick={() => resolve(i, 'handled', 'resolve')}>
                  <Check size={15} /> Handled
                </Button>
                <Button size="sm" variant="secondary" loading={working}
                  ariaLabel={`Dismiss the message from ${who}`}
                  onClick={() => resolve(i, 'dismissed', 'dismiss')}>
                  <X size={15} /> Dismiss
                </Button>
              </>} />
          )
        })}
    </Section>
  )
}

// ── Recent notifications ─────────────────────────────────────────────────────
//
// The one section with no decision in it: a feed of what already happened, so a person
// who just picked the phone up can see whether anything fired while they were away.
// The single action is "mark read", which is the notification log's own vocabulary
// (`POST /api/notifications/ack`) — the phone does not delete or clear, because a
// destructive action on a list this small is all downside.
export function RecentSection() {
  const query = useQuery<NotificationItem[]>('notifications-companion', () =>
    api.notifications().then((d) => d.notifications))
  const { act, view, busy } = useCompanionAction<{ acked: boolean }>(query.data)

  return (
    <Section id="companion-recent" icon={Bell} title="Recent" what="notifications" query={query}
      empty={{ title: 'Nothing recent', hint: 'Notifications your agent raises appear here.' }}>
      {(items) => items.map((raw) => {
        // Keyed on `ts` — the notification log has no id, and `ts` is what every ack /
        // unack / delete route takes, so it is the row identity by construction.
        const n = view(raw.ts, raw)
        return (
          <Row key={n.ts} title={n.title} sub={n.body} meta={n.kind || 'info'}
            actions={n.acked ? (
              <span data-type="body-m" className="inline-flex items-center gap-xs text-on-surface-low">
                <Check size={14} aria-hidden /> read
              </span>
            ) : (
              <Button size="sm" variant="secondary" loading={busy.has(n.ts)}
                ariaLabel={`Mark "${n.title}" read`}
                onClick={() => act(n.ts, { acked: true }, () => api.ackNotification(n.ts), `mark "${n.title}" read`, bustFeed)}>
                <Check size={15} /> Mark read
              </Button>
            )} />
        )
      })}
    </Section>
  )
}
