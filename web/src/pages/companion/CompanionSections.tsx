import { useId, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  Activity, Bell, Check, CheckCheck, Inbox, ListTodo, MessageSquarePlus,
  Pause, Play, RotateCcw, Square, X,
} from 'lucide-react'
import {
  api,
  type InboxItem, type Loop, type NotificationItem, type RunningList, type RunningRow, type TaskItem,
  type TaskStatus, type UnifiedLoopStatus,
} from '../../lib/api'
import { invalidateKeys, useQuery } from '../../lib/data'
import { readableErrText } from '../../lib/errText'
import { clockTime } from '../../lib/epoch'
import { loopRoute } from '../../lib/loopKind'
import { rowSubject } from '../../lib/rowSubject'
import { INTERRUPTED_LINE, KIND_WORD, phaseLine, phaseOf, wordAndLine } from '../../lib/runningRows'
import { useChatSocket } from '../../lib/useChatSocket'
import { useCutOff } from '../../lib/useCutOff'
import { TextLink } from '../../ui/TextLink'
import { inboxRaisedBy, isOpenStatus } from '../../lib/attentionLanes'
import { effectiveLoopStatus, loopStatusLabel, shownCycle } from '../../lib/loopStatus'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { Button } from '../../ui/Button'
import { TextArea } from '../../ui/forms'
import { useCompanionAction } from './useCompanionAction'
import { FOLLOWS, useLiveLane } from './useLiveLane'
import { signalPriority } from '../tasks/taskMeta'
import { isChannelItem } from '../inbox/inboxMeta'
import { noteMeta } from '../notifications/notificationMeta'

/** `#/companion`'s non-approval sections.
 *
 *  Four sections, one shape each: read a list, keep it current while the page is open
 *  (`useLiveLane`), render the honest state (error before empty — always), and offer the
 *  one or two actions a phone is actually good for.
 *  All four share `useCompanionAction`, so the optimistic-with-revert contract and the
 *  reconcile-against-the-server rule are written once (see that file's trap note).
 *
 *  WHAT THIS DELIBERATELY IS NOT: a second dashboard. Every section is a SHORT list of
 *  what wants a decision now — not a filterable table, not a detail view, not a place
 *  to create anything. Anything past the decision is a tap through to the real surface,
 *  because a phone-sized reimplementation of `#/loops` would be a fork of the UI, and
 *  the companion wraps the one UI rather than forking it.
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
function Section<T>({ id, icon: Icon, title, what, query, empty, note, after, children }: {
  id: string
  icon: LucideIcon
  title: string
  /** The plural noun for the announcements — "running work", "tasks". */
  what: string
  query: { data: T[] | undefined; loading: boolean; error?: unknown; refresh: () => void }
  empty: { title: string; hint: string }
  /** Said under the heading whatever the list holds: the Running section's reconnecting note. */
  note?: React.ReactNode
  /** Shown after the list whatever it holds: the Running section's just-finished tail. */
  after?: React.ReactNode
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
      {note}
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
      {after}
    </section>
  )
}

/** One row of any section: a tappable-height card with a title line and an action row. */
function Row({ title, titleId, sub, meta, actions }: {
  title: string; titleId?: string; sub?: string; meta?: string; actions: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-m rounded-lg border border-outline-variant/40 px-l py-l">
      <div className="min-w-0">
        <p id={titleId} data-type="title-m" className="truncate text-on-surface">{title}</p>
        {sub && <p data-type="body-m" className="line-clamp-2 text-on-surface-var">{sub}</p>}
        {meta && <p data-type="body-m" className="text-on-surface-low">{meta}</p>}
      </div>
      <div className="flex flex-wrap gap-s">{actions}</div>
    </div>
  )
}

// ── Running ──────────────────────────────────────────────────────────────────
//
// Everything running now, from the one list every reader of live work shares (`GET /api/running`):
// a chat's turn, a helper, a loop, a workflow run, an automation's fire and a webhook's turn, each
// with its name, its exact phase, what it is doing and when it last changed, and a tap through to
// where it runs. A loop's row keeps its steering, joined to its loop by id; a list of loops that
// cannot be read costs a loop its controls, never the rows.
//
// "Steerable" is wider than live: a loop that is paused runs nothing, so it is on no list of live
// work, and it is exactly what a phone should still offer to resume. The steerable loops the list
// does not hold are their own list under the rows, so a long running list never pushes them out of
// sight. A loop that has finished, failed or never launched is not a decision, so it stays off.
//
// Under them, the just-finished tail: the gateway's last endings, and what a restart cut off
// (`useCutOff`), as Mission Control says them.
const STEERABLE: readonly UnifiedLoopStatus[] = ['running', 'paused', 'needs_input', 'stagnant', 'blocked']

/** A steering action stalens the loops and the running list both: a stopped loop leaves the list
 *  before its `running_changed` frame comes. */
const bustSteered = () => { bustLoops(); invalidateKeys('running-companion') }

/** The loop a row is, when it is one: its row names it by its own id (`loop:<id>`), or a
 *  run-backed loop by its run's (`workflow:<run>`), which is the id the loop list gives it. */
function loopIdOf(row: RunningRow): string {
  return row.kind === 'loop' ? row.work.slice(row.work.indexOf(':') + 1) : ''
}

/** The Running section's socket handler: its lanes read again on their own (`useLiveLane`), so it
 *  listens only for the socket's drop and return (`useCutOff`). */
const IGNORE = () => {}

export function RunningSection() {
  const runningQuery = useQuery<RunningList>('running-companion', () => api.running())
  useLiveLane('running-companion', FOLLOWS.running)
  // Read-only consumption of `loop_routes`: list + PATCH action + nudge, exactly the
  // three the change names. No loop endpoint is invented or reshaped here.
  const loopsQuery = useQuery<Loop[]>('loops-companion', () =>
    api.uLoops().then((ls) => ls.filter((l) => STEERABLE.includes(l.status))))
  useLiveLane('loops-companion', FOLLOWS.loops)
  const cut = useCutOff(runningQuery.data, runningQuery.refresh, !!runningQuery.error)
  useChatSocket(IGNORE, cut.onReconnect, cut.onStatus)
  const { act, view, busy } = useCompanionAction<{ status: UnifiedLoopStatus }>(loopsQuery.data)
  const [nudging, setNudging] = useState<string | null>(null)
  const [text, setText] = useState('')
  const otherId = useId()
  const tailId = useId()

  const list = runningQuery.data
  const loops = loopsQuery.data ?? []
  const byId = new Map(loops.map((l) => [l.id, l]))
  const held = new Set((list?.running ?? []).map(loopIdOf).filter((id) => byId.has(id)))
  // A running list that cannot be read holds no loop, so every steerable loop is offered under its
  // error: Pause and Stop matter most when the rest of the screen cannot say what runs.
  const others = list ? loops.filter((l) => !held.has(l.id)) : runningQuery.error ? loops : []
  const query = { data: list?.running, loading: runningQuery.loading, error: runningQuery.error, refresh: runningQuery.refresh }

  const send = async (loop: Loop) => {
    const body = text.trim()
    if (!body) return
    // The composer closes optimistically. On failure it comes BACK with the text still
    // in it — retyping a nudge you already wrote is the worst possible apology.
    setNudging(null)
    const ok = await act(loop.id, { status: view(loop.id, loop).status }, () => api.uLoopNudge(loop.id, body),
      `nudge ${loop.name}`, bustSteered)
    if (ok) setText('')
    else setNudging(loop.id)
  }

  const steering = (l: Loop) => {
    const working = busy.has(l.id)
    return <>
      {l.status === 'running' ? (
        <Button size="sm" variant="secondary" loading={working}
          ariaLabel={`Pause ${l.name || l.task}`}
          onClick={() => act(l.id, { status: 'paused' }, () => api.uLoopAction(l.id, 'pause'), `pause ${l.name}`, bustSteered)}>
          <Pause size={15} /> Pause
        </Button>
      ) : (
        <Button size="sm" variant="secondary" loading={working}
          ariaLabel={`Resume ${l.name || l.task}`}
          onClick={() => act(l.id, { status: 'running' }, () => api.uLoopAction(l.id, 'resume'), `resume ${l.name}`, bustSteered)}>
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
        onClick={() => act(l.id, { status: 'stopped' }, () => api.uLoopAction(l.id, 'stop'), `stop ${l.name}`, bustSteered)}>
        <Square size={15} /> Stop
      </Button>
    </>
  }

  const composer = (l: Loop) => nudging === l.id && (
    <div className="flex flex-col gap-s rounded-lg border border-outline-variant/40 px-l py-l">
      <TextArea value={text} onChange={setText} rows={3} autoFocus
        ariaLabel={`What should ${l.name || l.task} do next?`}
        placeholder="Steer it — what should it do next?" />
      {/* A disabled control that will not say why is a dead end for a keyboard or
          screen-reader user — `ui/disabledReason*` is the house rail for it. */}
      <Button size="sm" onClick={() => send(l)} disabled={!text.trim()}
        ariaLabel={`Send nudge to ${l.name || l.task}`}
        disabledReason="Type what it should do next.">
        Send nudge
      </Button>
    </div>
  )

  // A cut-off row the gateway has since kept a real ending for, or that runs again, is said by
  // that row, not twice.
  const known = new Set([...(list?.running ?? []), ...(list?.finished ?? [])].map((r) => r.work))
  const ended = [
    ...cut.interrupted.filter((i) => !known.has(i.row.work))
      .map((i) => ({ row: i.row, word: 'Interrupted', line: INTERRUPTED_LINE, at: '' })),
    ...(list?.finished ?? []).map((row) => ({ row, word: phaseOf(row).label, line: row.line, at: clockTime(row.since) })),
  ]

  return (
    <Section id="companion-running" icon={Activity} title="Running" what="running work" query={query}
      empty={{ title: 'Nothing running', hint: 'Work you start appears here while it runs.' }}
      note={<>
        {/* Mounted whatever the socket does, so the words changing in it are announced: a
            status region that arrives with its text in it is one a screen reader may not read. */}
        <p role="status" data-type="body-m" className={cut.reconnecting ? 'text-on-surface-low' : 'sr-only'}>
          {cut.reconnecting ? 'Reconnecting… what shows here may be out of date.' : ''}
        </p>
        {loopsQuery.data === undefined && loopsQuery.error ? (
          <div role="alert" className="flex flex-wrap items-center gap-s">
            <p data-type="body-m" className="text-on-surface-var">
              Couldn't load your loops, so a loop here has no Pause, Nudge or Stop.
              {' '}{readableErrText(loopsQuery.error)}
            </p>
            <Button size="sm" variant="secondary" onClick={loopsQuery.refresh}>
              <RotateCcw size={15} /> Retry
            </Button>
          </div>
        ) : null}
      </>}
      after={<>
        {others.length > 0 && (
          <Capped id={otherId} label={list?.running.length ? 'Other loops you can steer' : 'Loops you can steer'} total={others.length}>
            {others.slice(0, LIMIT).map((item) => {
              const l = view(item.id, item)
              return (
                <Item key={l.id} title={l.name || l.task} sub={l.name ? l.task : undefined}
                  // The registry's word and cycle, as the cockpit, the Loops list and Home read the
                  // same loop: the raw wire value read "running" for a loop incident mode holds (it
                  // starts no cycle), and the completed count read one cycle behind every other surface.
                  meta={`${loopStatusLabel(effectiveLoopStatus(l.status, l.stop_reason, l.held))} · cycle ${shownCycle(l.status, l.total_cycles)}`}
                  actions={<>
                    <Opens route={`#/${loopRoute(l)}`} subject={rowSubject([l.name || l.task, 'loop'])} />
                    {steering(l)}
                  </>}>
                  {composer(l)}
                </Item>
              )
            })}
          </Capped>
        )}
        {ended.length > 0 && (
          <Capped id={tailId} label="Just finished" total={ended.length}>
            {ended.slice(0, LIMIT).map((e) => (
              <Item key={`${e.word}:${e.row.work}`} title={e.row.name} sub={wordAndLine(e.word, e.line)}
                meta={e.at ? `Ended ${e.at}` : undefined}
                actions={<Opens route={e.row.route} subject={rowSubject([e.row.name, e.word])} />} />
            ))}
          </Capped>
        )}
      </>}>
      {(shown) => (
        <ul className="flex flex-col gap-m">
          {shown.map((row) => {
            const found = byId.get(loopIdOf(row))
            const l = found ? view(found.id, found) : undefined
            const updated = clockTime(row.since)
            const waits = Array.isArray(row.waiting) ? row.waiting.length : 0
            const meta = [row.agent, updated ? `Updated ${updated}` : '', waits > 0 ? `${waits} waiting on you` : '']
              .filter(Boolean).join(' · ')
            return (
              <Item key={row.work} title={row.name} sub={phaseLine(row)} meta={meta || undefined}
                actions={<>
                  <Opens route={row.route} subject={rowSubject([row.name, KIND_WORD[row.kind] ?? row.kind])} />
                  {l ? steering(l) : null}
                </>}>
                {l ? composer(l) : null}
              </Item>
            )
          })}
        </ul>
      )}
    </Section>
  )
}

/** A list under the Running rows, named, and capped as every list on the page is. */
function Capped({ id, label, total, children }: {
  id: string; label: string; total: number; children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-s">
      <p id={id} data-type="title-s" className="text-on-surface-var">{label}</p>
      <ul aria-labelledby={id} className="flex flex-col gap-s">{children}</ul>
      {total > LIMIT && (
        <p data-type="body-m" className="text-on-surface-low">
          Showing {LIMIT} of {total}. Open the full dashboard for the rest.
        </p>
      )}
    </div>
  )
}

/** One row of the Running section, named by its title. */
function Item({ title, sub, meta, actions, children }: {
  title: string; sub?: string; meta?: string; actions: React.ReactNode; children?: React.ReactNode
}) {
  const titleId = useId()
  return (
    <li aria-labelledby={titleId} className="flex flex-col gap-m">
      <Row title={title} titleId={titleId} sub={sub} meta={meta} actions={actions} />
      {children}
    </li>
  )
}

/** A tap through to where the work runs. Work no page opens (a webhook's turn) has none, as in
 *  Mission Control: a link to somewhere its name does not say is worse than no link. */
function Opens({ route, subject }: { route: string; subject: string }) {
  if (!route) return null
  return (
    <TextLink href={route} ink="emphasis" size="sm" aria-label={`Open ${subject}`}>
      Open
    </TextLink>
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
  useLiveLane('tasks-companion', FOLLOWS.tasks)
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
              // 🔑 `signalPriority`, not `t.priority`. The raw value was rendered verbatim, which put a
              // lowercase "medium" on nearly every row — `medium` is the backend default
              // (`models.py`: `priority: TaskPriority = MEDIUM`) so it is indistinguishable from never
              // set, and the helper exists to return null for exactly that. It also carries the
              // canonical label, so the rung reads "High" rather than "high".
              meta={[t.status.replace(/_/g, ' '), signalPriority(t.priority)?.label, t.project]
                .filter(Boolean)
                .join(' · ')}
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
// Resolve only — the attention lifecycle (PENDING → SEEN → HANDLED | DISMISSED)
// through the inbox's own routes (`PUT /api/inbox/{id}` with a `status`). No second
// notion of "dealt with" is minted here, and no reply/draft path: composing a reply is
// a desk job, deciding whether something still needs one is not.
export function InboxSection() {
  const query = useQuery<InboxItem[]>('inbox-companion', () => api.inboxOpen())
  useLiveLane('inbox-companion', FOLLOWS.inbox)
  const { act, view, busy } = useCompanionAction<{ status: InboxItem['status'] }>(query.data)

  const resolve = (i: InboxItem, status: 'handled' | 'dismissed', verb: string) =>
    act(i.id, { status }, () => api.updateInboxItem(i.id, { status }), `${verb} this ${i.item_kind || 'message'}`, bustInbox)

  return (
    <Section id="companion-inbox" icon={Inbox} title="Inbox" what="inbox items" query={query}
      empty={{ title: 'Inbox clear', hint: 'Messages and requests waiting on you appear here.' }}>
      {/* Filtered on the OPTIMISTIC row (`view`), so a row the user just resolved leaves the list
          before the refetch. The predicate is the shared `isOpenStatus` — comparing to `'pending'`
          made this list a tenth definition of "open" and hid every row the user had already opened
          on the desk, on the one surface whose entire job is resolving them (issue 493). */}
      {(items) => items
        .filter((raw) => isOpenStatus(view(raw.id, raw).status))
        .map((raw) => {
          const i = view(raw.id, raw)
          const working = busy.has(i.id)
          // Who raised it (`inboxRaisedBy`, as Home and Mission Control name it): a platform row's
          // sender is the notification source it rode (`app:<name>`, `loop`), not who asked.
          const who = inboxRaisedBy(i) || i.channel || 'Unknown sender'
          return (
            <Row key={i.id} title={who} sub={i.message}
              // The triage verdict belongs to a channel message only: a note or a notice was never
              // triaged, and its stored default read "needs reply" (`isChannelItem`).
              meta={[i.item_kind?.replace(/_/g, ' '), isChannelItem(i) ? i.classification.replace(/_/g, ' ') : ''].filter(Boolean).join(' · ')}
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
  // The log comes newest first (`api.notifications`), and `Section` shows its first `LIMIT` rows:
  // what happened last.
  const query = useQuery<NotificationItem[]>('notifications-companion', () =>
    api.notifications().then((d) => d.notifications))
  useLiveLane('notifications-companion', FOLLOWS.notifications)
  const { act, view, busy } = useCompanionAction<{ acked: boolean }>(query.data)

  return (
    <Section id="companion-recent" icon={Bell} title="Recent" what="notifications" query={query}
      empty={{ title: 'Nothing recent', hint: 'Notifications your agent raises appear here.' }}>
      {(items) => items.map((raw) => {
        // Keyed on `ts` — the notification log has no id, and `ts` is what every ack /
        // unack / delete route takes, so it is the row identity by construction.
        const n = view(raw.ts, raw)
        return (
          <Row key={n.ts} title={n.title} sub={n.body} meta={noteMeta(n).label}
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
