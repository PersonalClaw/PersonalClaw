import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AlertTriangle, Ban, Check, CheckCircle2, Hand, LayoutGrid, MessageSquare, Play, Plus, ShieldAlert, X } from 'lucide-react'
import {
  api, ApiError, hasApiCode, type ChatSessionSummary, type RunningList, type RunningRow,
} from '../../lib/api'
import { invalidateKeys, useQuery } from '../../lib/data'
import { clockTime, sessionRecencyMs } from '../../lib/epoch'
import type { NeedsYouRow } from '../../lib/needsYou'
import { waitingAnswerId, waitingQuestion, type CardQuestion } from '../../lib/waitingAnswer'
import { approvalOf, isStale, useStaleAt } from '../../lib/waitingList'
import { rowSubject } from '../../lib/rowSubject'
import { phaseOf } from '../../lib/runningRows'
import { sessionTitle } from '../../lib/sessionTitle'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { useCutOff } from '../../lib/useCutOff'
import { useNeedsYou } from '../../lib/useNeedsYou'
import { useIdentity, firstNameOf } from '../../app/identity'
import { useIsMobile } from '../../app/useIsMobile'
import { PendingApprovalCard } from '../../app/PendingApprovalCard'
import { WidgetBoundary } from '../../app/ErrorBoundary'
import type { RouteProps } from '../../app/useQueryState'
import { Button } from '../../ui/Button'
import { HeaderActions, HeaderControl, HeaderSegmented } from '../../ui/HeaderActions'
import { RowHitTarget } from '../../ui/RowHitTarget'
import { StatusPill } from '../../ui/StatusPill'
import { TextLink } from '../../ui/TextLink'
import { TopBar } from '../../ui/TopBar'
import { BUSY_REASON } from '../../ui/unavailable'
import { InlineLoadError } from '../../ui/ListScaffold'
import { SurfaceOverlay } from '../../ui/surfaces/SurfaceOverlay'
import { ListBesideDetail, type CapabilityDetail } from '../capabilities/ListBesideDetail'
import { StartedByApp, startedByLabel, startedByName } from '../chat/StartedByApp'
import { FromChannel, channelOf } from '../chat/FromChannel'
import { HomeOverview } from './DashboardPage'
import { PinnedTiles } from './PinnedTiles'
import { FinishedRows, LARGE_LIST, RunningLane, openable } from './RunningLane'
import { Suggestions } from './widgets/Suggestions'

// ── Home: the attention stack ───────────────────────────────────────────────────────────────────
//
// Opening PersonalClaw answers three questions at a glance, in this order (#3813):
//
//   Needs You            everything waiting on you to answer, from every source (`GET /api/waiting`,
//                        #3888), in the one order every surface keeps: by consequence, then age.
//   Running Now          the one list of live work (`GET /api/running`), each row with its exact
//                        phase and its last update.
//   Recent and completed what just finished, and your own latest threads.
//
// Idle work is no group: it is found by search. A group that empties says what that means, a group
// whose read failed says so, and a source the gateway could not read is named, because an empty
// Needs You is this page's "all clear", the most misleading thing it could show by mistake.
//
// It is a CONTROL surface: the verbs that resolve an item are on its card, so "what needs me" and
// "deal with it" are one glance and one click. A resolved card looks resolved the moment its answer
// returns, and a failed one says why in the gateway's words. Every answer goes to the item's own
// route, through the same `api` call its own page makes.
//
// Selecting an item opens its detail beside the groups where there is room (`ListBesideDetail`'s
// 840 px of the tab's own width) and in their place where there is not, with Back to Home. The
// selection is in the address (`?open=`), so Back, a reload and a copied link open the same one.
//
// The header's View switches to Overview, the bands that have no place in the three groups (the
// Agent world, the desktop and browser live views, tasks, Discover, pinned artifacts and recent
// activity), `?view=overview`.

/** ── THE ONE RECONCILIATION POINT with `dashboard/views_store.py` ──────────────────────────
 *
 *  The locked Mission Control preset registers one `core:` tile ref per group. This map is the only
 *  place those strings appear on the frontend, and `tests/test_dashboard_mission_control_preset.py`
 *  reads it out of this file and asserts set equality with the registry: a tile ref is a string on
 *  both sides, so nothing else can see the two drift apart. */
export const GROUP_REFS: Record<Group, string> = {
  'needs-you': 'core:group-needs-you',
  'running-now': 'core:group-running-now',
  recent: 'core:group-recent',
}

/** The three groups, in the order they are read. */
export const GROUPS = ['needs-you', 'running-now', 'recent'] as const
export type Group = (typeof GROUPS)[number]

/** The preset's own id, which `#/mission-control` opens: one string, one place. */
export const MISSION_CONTROL_VIEW_ID = 'mission-control'

/** Which group a preset tile ref belongs to, or null for a ref this view does not own. */
export function groupForRef(ref: string): Group | null {
  return GROUPS.find((g) => GROUP_REFS[g] === ref) ?? null
}

export const GROUP_LABEL: Record<Group, string> = {
  'needs-you': 'Needs You',
  'running-now': 'Running Now',
  recent: 'Recent and completed',
}

const GROUP_EMPTY: Record<Group, string> = {
  'needs-you': 'Nothing is waiting on you.',
  'running-now': 'Nothing is running right now.',
  recent: 'Nothing has finished lately, and you have no threads yet.',
}

/** The views Home's header switches between. A phone shows each by its glyph, named by its label. */
export const HOME_VIEWS = [
  { key: 'attention', label: 'Attention', icon: Hand },
  { key: 'overview', label: 'Overview', icon: LayoutGrid },
]

/** Your threads shown under Recent and completed. */
const RECENT_THREADS = 6

const RUNNING_KEY = 'dashboard:running-now'

/** A waiting source's kind, in words, for "Could not read …". */
const SOURCE_WORDS: Record<string, string> = {
  approval: 'approvals',
  question: 'agents’ questions',
  loop_question: 'loops’ questions',
  gate: 'workflow runs',
  park: 'triggers',
  browse_grant: 'browser grants',
  plan_review: 'plans to review',
  bridge_confirmation: 'control bridge actions',
}

// ── Per-card outcome ────────────────────────────────────────────────────────────────────────

type Outcome =
  | { state: 'busy' }
  | { state: 'done'; text: string }
  | { state: 'ended'; text: string }
  | { state: 'failed'; text: string }

/** The gateway's own sentence, plus what the user can do about it. The server text comes first
 *  and verbatim: it is the only part that says WHICH knob to turn. */
function failureText(verb: string, err: unknown): string {
  const detail = err instanceof Error && err.message ? err.message : String(err ?? 'unknown error')
  return `Could not ${verb}: ${detail}. Nothing was recorded — it still needs you, so try again.`
}

/** When an answer is refused because there is nothing left to decide, the sentence for it, or
 *  null for a real failure: the work that asked has ended (409, the gateway names which), or it
 *  was answered or ended somewhere else (404). Retrying either would change nothing. */
function endedText(err: unknown): string | null {
  if (hasApiCode(err, 'approval_owner_ended') && err instanceof Error) return err.message
  if (err instanceof ApiError && err.status === 404) {
    return 'It is no longer waiting: it was answered or ended somewhere else.'
  }
  return null
}

/** A read that failed, in the gateway's own words: nothing was asked of it, so nothing was lost. */
function readFailureText(what: string, err: unknown): string {
  const detail = err instanceof Error && err.message ? err.message : String(err ?? 'unknown error')
  return `Could not read ${what}: ${detail}.`
}

/** What a gate's or a park's answer did, in words. */
function answeredText(q: CardQuestion, value: string | boolean): string {
  if (q.event) return 'Woken — the run is moving again.'
  if (!q.approval) return `Answered “${String(value)}” — the run is moving again.`
  if (q.triggerId) {
    return value === true ? 'Approved — it is running again.' : 'Declined — it asks again the next time it stops.'
  }
  return value === true ? 'Approved — the run is moving again.' : 'Declined — the run ends here.'
}

/** The running list's frames: it says when it moved (`running_changed`). */
function refreshesRunning(m: WsMessage): boolean {
  return m.type === 'running_changed'
}

/** The running list, or undefined for an answer that is not one: Home is drawn from whatever the
 *  read returned, and one malformed answer must not take the page down with it. */
function asRunningList(v: unknown): RunningList | undefined {
  const list = v as RunningList | undefined
  return list && Array.isArray(list.running) && Array.isArray(list.finished) ? list : undefined
}

/** The key a row is selected by, in `?open=`. */
function rowKey(row: NeedsYouRow): string {
  return row.key
}

export function Home(route: RouteProps) {
  const { query, setQuery, navigate } = route
  const view = query.view === 'overview' ? 'overview' : 'attention'
  const { name } = useIdentity()
  // The header's two actions go through the one responsive cluster, which sheds labels as the
  // room narrows. A phone's New thread keeps a 44 px target beside the shell's corner controls.
  const phone = useIsMobile()
  const header = (
    <TopBar
      contentAligned
      left={<h1 data-type="headline-s" className="min-w-0 truncate text-on-surface">{greetingFor(name)}</h1>}
      right={(
        <HeaderActions>
          <HeaderControl icon={Plus} label="New thread" variant="secondary" priority="primary"
            className={phone ? 'min-h-11 min-w-11' : undefined} onClick={() => navigate('chat/new')} />
          <HeaderSegmented ariaLabel="View" options={HOME_VIEWS} value={view}
            onChange={(k) => setQuery({ view: k === 'overview' ? 'overview' : null, open: null })} />
        </HeaderActions>
      )}
    />
  )
  if (view === 'overview') {
    return (
      <div className="flex h-full flex-col overflow-hidden">
        {header}
        <div className="min-h-0 flex-1 overflow-y-auto"><HomeOverview {...route} /></div>
      </div>
    )
  }
  return <AttentionStack {...route} header={header} />
}

/** Time-of-day greeting, personalised when we know the owner's name: the page's title. */
function greetingFor(name: string | undefined): string {
  const h = new Date().getHours()
  const part = h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening'
  return name ? `Good ${part}, ${firstNameOf(name)}` : `Good ${part}`
}

function AttentionStack({ header, ...route }: RouteProps & { header: ReactNode }) {
  const { query, setQuery } = route
  const needs = useNeedsYou()
  // Every tool call is approved without asking while YOLO is on, the timed mode a chat's YOLO pill
  // turns on included: the one fact about the agent's permissions the owner must see wherever the
  // agent can act, so Home says it until the shell's status line carries it.
  // Each read is `async`, so a call that throws as it is made is a failed read, never a thrown render.
  const { data: status } = useQuery('dashboard:status', async () => api.status())
  const {
    data: runningData, error: runningError, loading: runningLoading, refresh: refreshRunning,
  } = useQuery<RunningList>(RUNNING_KEY, async () => api.running())
  const {
    data: sessionsData, error: sessionsError, loading: sessionsLoading, refresh: refreshSessions,
  } = useQuery<ChatSessionSummary[]>('chat:sessions:recent', async () => api.chatSessions(), { persist: true })
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({})
  const [allNeeds, setAllNeeds] = useState(false)

  const debounce = useRef<number | undefined>(undefined)
  const onMessage = useCallback((m: WsMessage) => {
    if (!refreshesRunning(m)) return
    if (debounce.current) clearTimeout(debounce.current)
    debounce.current = window.setTimeout(refreshRunning, 300)
  }, [refreshRunning])
  useEffect(() => () => { if (debounce.current) clearTimeout(debounce.current) }, [])
  const runningList = asRunningList(runningData)
  const cut = useCutOff(runningList, refreshRunning, !!runningError)
  useChatSocket(onMessage, cut.onReconnect, cut.onStatus)

  // An item held past its expiry is re-drawn when it expires: one timer, to the soonest expiry,
  // never a tick.
  const waitingItems = useMemo(
    () => needs.rows.flatMap((r) => (r.origin === 'waiting' ? [r.item] : [])),
    [needs.rows],
  )
  const now = useStaleAt(waitingItems)

  const rows = runningList?.running ?? []
  const finished = runningList?.finished ?? []
  const liveChats = useMemo(() => new Set(rows.map((r) => r.session).filter(Boolean)), [rows])
  const threads = useMemo(() => (Array.isArray(sessionsData) ? sessionsData : [])
    .filter((s) => s.title && !s.run_state?.turn && !liveChats.has(s.key))
    .sort((a, b) => sessionRecencyMs(b) - sessionRecencyMs(a))
    .slice(0, RECENT_THREADS), [sessionsData, liveChats])

  const mark = useCallback((key: string, o: Outcome) => setOutcomes((prev) => ({ ...prev, [key]: o })), [])
  const refreshNeeds = needs.refresh

  /** Send one answer and say how it went: done, ended elsewhere, or failed in the gateway's words. */
  const send = useCallback((key: string, sent: Promise<unknown>, done: string, verb: string, after?: () => void) => {
    mark(key, { state: 'busy' })
    return sent
      .then((res) => {
        const refused = res && typeof res === 'object' && 'refused' in res ? (res as { refused?: string }).refused : ''
        if (refused) {
          mark(key, { state: 'failed', text: failureText(verb, new Error(refused)) })
          return false
        }
        mark(key, { state: 'done', text: done })
        after?.()
        refreshNeeds()
        return true
      })
      .catch((err) => {
        const ended = endedText(err)
        if (ended) {
          mark(key, { state: 'ended', text: ended })
          refreshNeeds()
          return false
        }
        mark(key, { state: 'failed', text: failureText(verb, err) })
        return false
      })
  }, [mark, refreshNeeds])

  const actions: CardActions = useMemo(() => ({
    approval: (key, id, action) => send(key, api.resolveApproval(id, action), action === 'approve' ? 'Approved.' : 'Denied.', `${action} this`),
    question: (key, q, value) => {
      const sent = q.triggerId
        ? api.answerTriggerPark(q.triggerId, { resume_token: q.resumeToken, answer: value === true })
        : q.confirm
          ? api.confirmWorkflowRun(q.runId, { verb: value === true ? 'approve' : 'reject', resume_token: q.resumeToken || undefined })
          : api.resumeWorkflowRun(q.runId, { answer: value, resume_token: q.resumeToken || undefined })
      void send(key, sent, answeredText(q, value), 'send that answer')
    },
    grant: (key, id, action) => {
      void send(key, api.browseGrantResolve(id, action), action === 'approve' ? 'Allowed — the browser task goes on.' : 'Refused — the browser task stops here.', `${action} this`)
    },
    bridge: (key, token, run) => {
      void send(key, api.answerBridgeConfirmation(token, run), run ? 'Confirmed — it ran once.' : 'Dropped — nothing ran.', 'send that answer')
    },
    proposal: (key, id, action) => {
      const sent = action === 'accept' ? api.acceptSkillProposal(id) : api.rejectSkillProposal(id)
      void send(key, sent, action === 'accept' ? 'Accepted — the skill is saved.' : 'Rejected.', `${action} this proposal`,
        () => invalidateKeys('skill-proposals', true))
    },
  }), [send])

  // ── The selection ──
  const open = query.open ?? ''
  const select = (key: string) => setQuery({ open: key })
  const close = () => setQuery({ open: null })
  const selectedNeed = needs.rows.find((r) => rowKey(r) === open)
  const selectedRun = open.startsWith('run:') ? [...rows, ...finished].find((r) => `run:${r.work}` === open)
    ?? cut.interrupted.map((i) => i.row).find((r) => `run:${r.work}` === open) : undefined
  const selectedThread = open.startsWith('thread:') && Array.isArray(sessionsData)
    ? sessionsData.find((s) => `thread:${s.key}` === open) : undefined
  const detail: CapabilityDetail | null = selectedNeed ? {
    id: open, title: selectedNeed.title, onClose: close,
    body: <NeedDetail row={selectedNeed} outcome={outcomes[selectedNeed.key]} actions={actions} now={now} />,
  } : selectedRun ? {
    id: open, title: selectedRun.name, onClose: close, body: <RunDetail row={selectedRun} />,
  } : selectedThread ? {
    id: open, title: sessionTitle(selectedThread), onClose: close, body: <ThreadDetail s={selectedThread} />,
  } : null

  const needsFailed = !!needs.error && !needs.data
  const shownNeeds = allNeeds ? needs.rows : needs.rows.slice(0, LARGE_LIST)
  const allClear = !needs.loading && !needs.error && needs.rows.length === 0 && needs.unreadable.length === 0
  const failures = [
    needs.error ? readFailureText('what is waiting on you', needs.error) : '',
    runningError ? readFailureText('what is running', runningError) : '',
  ].filter(Boolean)

  return (
    <ListBesideDetail header={header} detail={detail} noun="Home">
      <section role="region" aria-label="Home" data-tour="approvals"
        className="mx-auto flex w-full min-w-0 flex-col gap-l px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {status?.yolo ? (
          <p role="status" className="flex min-w-0 flex-wrap items-center gap-s">
            <StatusPill tone="warn" title="Every tool call is approved without asking.">
              <ShieldAlert size={13} className="shrink-0" aria-hidden="true" /> YOLO active
            </StatusPill>
            <span data-type="body-s" className="text-on-surface-var">Every tool call is approved without asking.</span>
            <TextLink href="#/settings/agent" ink="emphasis" size="sm">Change it in Agent defaults</TextLink>
          </p>
        ) : null}
        {cut.reconnecting ? (
          <p role="status" data-type="body-s" className="text-on-surface-low">
            Reconnecting… what shows here may be out of date.
          </p>
        ) : null}

        {/* A read failure is stated, never drawn as empty groups. `role="alert"` because it arrives
            after the first paint, and someone who has already looked away must be told. */}
        {failures.length > 0 ? (
          <div role="alert" className="flex min-w-0 items-start gap-s rounded-lg border border-error/40 bg-error/10 p-s text-on-surface">
            <AlertTriangle size={16} className="mt-0.5 shrink-0 text-error" aria-hidden="true" />
            <div className="flex min-w-0 flex-col gap-xs">
              {failures.map((f) => <p key={f} data-type="body-s">{f}</p>)}
              <div>
                <Button size="xs" variant="ghost-accent" onClick={() => { needs.refresh(); refreshRunning() }}>Try again</Button>
              </div>
            </div>
          </div>
        ) : null}

        <Group group="needs-you" count={needs.data ? needs.rows.length : null}>
          {needs.unreadable.length > 0 ? (
            <p role="status" data-type="body-s" className="text-on-surface">
              {`Could not read ${needs.unreadable.map((k) => SOURCE_WORDS[k] ?? k).join(', ')}: what they hold may be waiting on you and is not shown here.`}
            </p>
          ) : null}
          {needs.rows.length === 0 ? (
            <p data-type="body-s" className="text-on-surface-low">
              {needs.loading ? 'Loading…' : needsFailed ? 'Could not read what is waiting on you.'
                : needs.unreadable.length > 0 ? 'Nothing else is waiting on you.' : GROUP_EMPTY['needs-you']}
            </p>
          ) : (
            <ul className="flex min-w-0 flex-col gap-s">
              {shownNeeds.map((r) => (
                <li key={r.key} className="min-w-0">
                  <NeedCard row={r} outcome={outcomes[r.key]} actions={actions} now={now}
                    selected={open === rowKey(r)} onOpen={() => select(rowKey(r))} />
                </li>
              ))}
            </ul>
          )}
          {needs.rows.length > LARGE_LIST ? (
            <div>
              <Button size="xs" variant="ghost-accent" ariaExpanded={allNeeds} onClick={() => setAllNeeds((was) => !was)}>
                {allNeeds ? `Show the first ${LARGE_LIST}` : `Show all ${needs.rows.length}`}
              </Button>
            </div>
          ) : null}
          {/* The empty state is the one place a suggestion belongs on Home. */}
          {allClear && rows.length === 0 ? <WidgetBoundary what="suggestions"><Suggestions {...route} /></WidgetBoundary> : null}
        </Group>

        <RunningLane rows={rows} loading={runningLoading} failed={!!runningError && !runningList}
          empty={GROUP_EMPTY['running-now']} selected={open.startsWith('run:') ? open.slice(4) : undefined}
          onOpen={(r) => select(`run:${r.work}`)} />

        <Group group="recent" count={null}>
          <FinishedRows finished={finished} interrupted={cut.interrupted}
            selected={open.startsWith('run:') ? open.slice(4) : undefined} onOpen={(r) => select(`run:${r.work}`)} />
          {threads.length > 0 ? (
            <div className="flex min-w-0 flex-col gap-xs">
              <p id="home-your-threads" data-type="label-s" className="text-on-surface-low">Your threads</p>
              <ul aria-labelledby="home-your-threads" className="flex min-w-0 flex-col gap-xs">
                {threads.map((s) => (
                  <ThreadRow key={s.key} s={s} selected={open === `thread:${s.key}`} onOpen={() => select(`thread:${s.key}`)} />
                ))}
              </ul>
            </div>
          ) : null}
          {sessionsError && !Array.isArray(sessionsData) ? (
            <InlineLoadError what="your threads" error={sessionsError} onRetry={refreshSessions} />
          ) : finished.length === 0 && cut.interrupted.length === 0 && threads.length === 0 ? (
            <p data-type="body-s" className="text-on-surface-low">
              {sessionsLoading || runningLoading ? 'Loading…' : GROUP_EMPTY.recent}
            </p>
          ) : null}
        </Group>

        {/* What the owner and the agents pinned, and the dashboard's surface overlays, under the
            groups. Each renders nothing when it holds nothing. */}
        <WidgetBoundary what="your pinned tiles"><PinnedTiles /></WidgetBoundary>
        <SurfaceOverlay surface="dashboard" />
      </section>
    </ListBesideDetail>
  )
}

/** A group's heading row and its body: an h2 under the page's h1, the count in the heading's text
 *  (a colour cannot say "how many"), and none at 0, when the group's sentence says it is empty, or
 *  while the count is not known. */
function Group({ group, count, children }: { group: Group; count: number | null; children: ReactNode }) {
  const headingId = `home-group-${group}`
  return (
    <section aria-labelledby={headingId} data-home-group={group} className="flex min-w-0 flex-col gap-s">
      <div className="flex items-center gap-s">
        <h2 id={headingId} data-type="label-l" className="text-on-surface-var">{GROUP_LABEL[group]}</h2>
        {count ? <span data-type="label-s" data-group-count className="text-on-surface-low">{count}</span> : null}
        <span className="h-px flex-1 bg-outline-variant/40" />
      </div>
      {children}
    </section>
  )
}

interface CardActions {
  approval: (key: string, id: string, action: 'approve' | 'reject') => Promise<unknown>
  question: (key: string, q: CardQuestion, value: string | boolean) => void
  grant: (key: string, id: string, action: 'approve' | 'reject') => void
  bridge: (key: string, token: string, run: boolean) => void
  proposal: (key: string, id: string, action: 'accept' | 'reject') => void
}

/** One Needs You card in the group: what asked, what it asks, and its answer. The card opens its
 *  detail; its own controls keep their clicks. */
function NeedCard({ row, outcome, actions, now, selected, onOpen }: {
  row: NeedsYouRow
  outcome: Outcome | undefined
  actions: CardActions
  now: number
  selected: boolean
  onOpen: () => void
}) {
  const open = openable(row.title, onOpen, selected)
  return (
    <div {...open.props}
      className={`flex min-w-0 flex-col gap-xs rounded-lg border border-outline-variant/40 bg-surface-low/60 p-s ${open.className}`}>
      {open.target}
      <NeedBody row={row} outcome={outcome} actions={actions} now={now} />
    </div>
  )
}

/** The selected Needs You item, in full: the same card and the same answers, and where it asks. */
function NeedDetail({ row, outcome, actions, now }: {
  row: NeedsYouRow
  outcome: Outcome | undefined
  actions: CardActions
  now: number
}) {
  const route = row.origin === 'waiting' ? row.item.route : row.origin === 'proposal' ? '#/skills?mode=proposals'
    : `#/inbox?open=${encodeURIComponent(row.note.id)}`
  const asked = row.origin === 'waiting' ? clockTime(row.item.asked_at) : ''
  return (
    <div className="flex min-w-0 flex-col gap-m">
      <NeedBody row={row} outcome={outcome} actions={actions} now={now} />
      <dl data-type="body-s" className="grid grid-cols-[auto_1fr] gap-x-m gap-y-xs text-on-surface-var">
        {row.raisedBy ? <><dt className="text-on-surface-low">Asked by</dt><dd>{row.raisedBy}</dd></> : null}
        {asked ? <><dt className="text-on-surface-low">Asked</dt><dd>{asked}</dd></> : null}
        {row.origin === 'waiting' && row.item.expires_at ? (
          <><dt className="text-on-surface-low">Answer by</dt><dd>{clockTime(row.item.expires_at)}</dd></>
        ) : null}
      </dl>
      {route ? <TextLink href={route} ink="emphasis" size="sm">Open where it asks</TextLink> : null}
    </div>
  )
}

/** A Needs You item's content and answer, shared by its card and its detail. */
function NeedBody({ row, outcome, actions, now }: {
  row: NeedsYouRow
  outcome: Outcome | undefined
  actions: CardActions
  now: number
}) {
  const settled = outcome?.state === 'done' || outcome?.state === 'ended'
  const busy = outcome?.state === 'busy'
  const subject = rowSubject([row.title, row.raisedBy, row.subtitle])
  const item = row.origin === 'waiting' ? row.item : null
  const expired = item ? isStale(item, now) : false

  if (item?.kind === 'approval') {
    const approval = approvalOf(item)
    if (approval && !settled && !expired) {
      return (
        <div className="flex min-w-0 flex-col gap-xs">
          <PendingApprovalCard approval={approval} busy={busy} opensSource sourceHref={item.route || undefined}
            onDecide={(action) => actions.approval(row.key, approval.id, action)} />
          <CardOutcome outcome={outcome} />
        </div>
      )
    }
  }

  const question = item ? waitingQuestion(item) : null
  const answerId = item ? waitingAnswerId(item) : ''
  const proposal = row.origin === 'proposal' ? row.proposal : null
  const opensWhereAsked = item && !question && !answerId
  return (
    <div className="flex min-w-0 flex-col gap-xs">
      {row.raisedBy ? <p data-type="label-s" className="min-w-0 truncate text-on-surface-low">{row.raisedBy}</p> : null}
      <p data-type="label-m" className="min-w-0 text-on-surface-var">{row.title}</p>
      {row.subtitle ? <p data-type="body-s" className="min-w-0 text-on-surface-low">{row.subtitle}</p> : null}
      {question?.prompt && question.prompt !== row.title ? (
        <p data-type="body-s" className="min-w-0 text-on-surface">{question.prompt}</p>
      ) : null}
      {expired && !settled ? (
        <p role="status" data-type="body-s" className="flex min-w-0 items-start gap-xs text-on-surface-var">
          <Ban size={14} className="mt-0.5 shrink-0 text-on-surface-low" aria-hidden="true" />
          {item?.expires_at
            ? `This stopped waiting at ${clockTime(item.expires_at)}, so it can no longer be answered here.`
            : 'This can no longer be answered here.'}
        </p>
      ) : null}
      {opensWhereAsked && item.route ? (
        <TextLink href={item.route} ink="emphasis" size="sm" aria-label={`Answer it where it asks: ${subject}`}>
          Answer it where it asks
        </TextLink>
      ) : null}
      {proposal ? (
        <TextLink href="#/skills?mode=proposals" ink="emphasis" size="sm" aria-label={`Review it in Skills: ${subject}`}>
          Review it in Skills
        </TextLink>
      ) : null}
      {row.origin === 'note' ? (
        <TextLink href={`#/inbox?open=${encodeURIComponent(row.note.id)}`} ink="emphasis" size="sm" aria-label={`Open it in the Inbox: ${subject}`}>
          Open it in the Inbox
        </TextLink>
      ) : null}

      <CardOutcome outcome={outcome} />

      {/* Settled or expired, the verbs are GONE, not disabled: a second answer on a settled item
          is a real action. A FAILED card keeps them: retrying is the point of being told. */}
      {settled || expired ? null : (
        <div className="flex min-w-0 flex-wrap items-center gap-xs">
          {proposal ? (
            <YesNo subject={subject} busy={busy} yes="Accept" no="Reject"
              onYes={() => actions.proposal(row.key, proposal.id, 'accept')}
              onNo={() => actions.proposal(row.key, proposal.id, 'reject')} />
          ) : null}
          {question ? (
            <QuestionActions question={question} subject={subject} busy={busy}
              onAnswer={(value) => actions.question(row.key, question, value)} />
          ) : null}
          {item?.kind === 'browse_grant' && answerId ? (
            <YesNo subject={subject} busy={busy} yes="Allow" no="Refuse"
              onYes={() => actions.grant(row.key, answerId, 'approve')}
              onNo={() => actions.grant(row.key, answerId, 'reject')} />
          ) : null}
          {item?.kind === 'bridge_confirmation' && answerId ? (
            <YesNo subject={subject} busy={busy} yes="Run it once" no="Drop it"
              onYes={() => actions.bridge(row.key, answerId, true)}
              onNo={() => actions.bridge(row.key, answerId, false)} />
          ) : null}
        </div>
      )}
    </div>
  )
}

/** A yes and a no, each named with what it answers. Both carry `loading` while the answer is on
 *  its way, so neither can be sent twice and assistive tech hears it is busy. */
function YesNo({ subject, busy, yes, no, onYes, onNo }: {
  subject: string; busy: boolean; yes: string; no: string; onYes: () => void; onNo: () => void
}) {
  return (
    <>
      <Button size="xs" variant="primary" loading={busy} ariaLabel={`${yes} ${subject}`} onClick={onYes}>
        <Check size={13} aria-hidden="true" /> {yes}
      </Button>
      <Button size="xs" variant="secondary" loading={busy} ariaLabel={`${no} ${subject}`} onClick={onNo}>
        <X size={13} aria-hidden="true" /> {no}
      </Button>
    </>
  )
}

/** How a card's answer went, in words, in a live region: a card that only changed colour is one a
 *  screen-reader user cannot tell from a pending one. */
function CardOutcome({ outcome }: { outcome: Outcome | undefined }) {
  if (!outcome || outcome.state === 'busy') return null
  if (outcome.state === 'failed') {
    return (
      <p role="alert" data-type="body-s" className="flex min-w-0 items-start gap-xs text-on-surface">
        <AlertTriangle size={14} className="mt-0.5 shrink-0 text-error" aria-hidden="true" />
        {outcome.text}
      </p>
    )
  }
  return (
    <p role="status" data-type="body-s" className="flex min-w-0 items-start gap-xs text-on-surface-var">
      {outcome.state === 'done'
        ? <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-success" aria-hidden="true" />
        : <Ban size={14} className="mt-0.5 shrink-0 text-on-surface-low" aria-hidden="true" />}
      {outcome.text}
    </p>
  )
}

/** A gate's or a park's answers, as buttons: one click per option. A question with no preset options
 *  says where to answer it in your own words rather than offering a text box this card could not
 *  honestly submit. A parked run decides nothing, so its one button wakes it. */
function QuestionActions({ question, subject, busy, onAnswer }: {
  question: CardQuestion
  subject: string
  busy: boolean
  onAnswer: (value: string | boolean) => void
}) {
  if (question.event) {
    return (
      <Button size="xs" variant="primary" loading={busy} ariaLabel={`Wake ${subject} now`} onClick={() => onAnswer(true)}>
        <Play size={13} aria-hidden="true" /> Wake it now
      </Button>
    )
  }
  if (question.approval) {
    return <YesNo subject={subject} busy={busy} yes="Approve" no="Deny" onYes={() => onAnswer(true)} onNo={() => onAnswer(false)} />
  }
  if (question.choices.length === 0) {
    return (
      <p data-type="body-s" className="text-on-surface-low">
        This question has no preset options —{' '}
        <TextLink href={`#/workflows/runs/${encodeURIComponent(question.runId)}`} ink="emphasis" aria-label={`Open the run: ${subject}`}>
          open the run
        </TextLink>{' '}
        to answer it in your own words.
      </p>
    )
  }
  return (
    <>
      {question.choices.map((choice) => (
        <Button key={choice} size="xs" variant="tonal" disabled={busy} disabledReason={BUSY_REASON}
          ariaLabel={`Answer ${subject} — ${choice}`} onClick={() => onAnswer(choice)}>
          {choice}
        </Button>
      ))}
    </>
  )
}

/** A running or ended row, in full: its phase in words, what it is doing, who, since when. */
function RunDetail({ row }: { row: RunningRow }) {
  const look = phaseOf(row)
  return (
    <div className="flex min-w-0 flex-col gap-m">
      <div className="flex min-w-0 flex-wrap items-center gap-xs">
        {row.blocked ? <StatusPill tone="neutral">Blocked</StatusPill> : null}
        <StatusPill tone={look.tone}>{look.label}</StatusPill>
      </div>
      {row.line ? <p data-type="body-m" className="min-w-0 text-on-surface">{row.line}</p> : null}
      <dl data-type="body-s" className="grid grid-cols-[auto_1fr] gap-x-m gap-y-xs text-on-surface-var">
        {row.agent ? <><dt className="text-on-surface-low">Agent</dt><dd>{row.agent}</dd></> : null}
        {row.started_at ? <><dt className="text-on-surface-low">Started</dt><dd>{clockTime(row.started_at)}</dd></> : null}
        {row.since ? <><dt className="text-on-surface-low">Last update</dt><dd>{clockTime(row.since)}</dd></> : null}
        {row.waiting?.length ? <><dt className="text-on-surface-low">Waiting on you</dt><dd>{row.waiting.length}</dd></> : null}
      </dl>
      {row.route ? <TextLink href={row.route} ink="emphasis" size="sm">Open it</TextLink> : null}
    </div>
  )
}

/** One of your threads under Recent and completed: its title and when it last moved. */
function ThreadRow({ s, selected, onOpen }: { s: ChatSessionSummary; selected: boolean; onOpen: () => void }) {
  const title = sessionTitle(s)
  const at = clockTime(s.last_ts || null)
  // An app's conversation says so in the row's name too: opening it means speaking under the APP's
  // permissions, and a screen reader hears the name before choosing. So does a chat that came in on
  // a chat channel.
  const app = startedByName(s)
  const channel = channelOf(s)
  const named = rowSubject([title, app ? startedByLabel(app) : '', channel ? `From ${channel}` : ''], 120)
  return (
    <li className={`flex min-w-0 items-center gap-s rounded-lg border border-outline-variant/30 p-s ${selected ? 'ring-1 ring-primary' : ''} relative cursor-pointer has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary`}
      tabIndex={-1} onClick={onOpen}>
      <RowHitTarget label={`Show the details of ${named}`} selected={selected} />
      <MessageSquare size={14} className="shrink-0 text-on-surface-low" aria-hidden="true" />
      <span data-type="label-m" className="min-w-0 flex-1 truncate text-on-surface-var">{title}</span>
      <StartedByApp s={s} />
      <FromChannel s={s} />
      {at ? <span data-type="body-s" className="shrink-0 text-on-surface-low">{at}</span> : null}
    </li>
  )
}

/** A thread, in its detail: its last words and the way into it. */
function ThreadDetail({ s }: { s: ChatSessionSummary }) {
  return (
    <div className="flex min-w-0 flex-col gap-m">
      {s.last_message ? <p data-type="body-m" className="min-w-0 whitespace-pre-line text-on-surface">{s.last_message}</p> : null}
      <TextLink href={`#/chat/${encodeURIComponent(s.key)}`} ink="emphasis" size="sm">Open the thread</TextLink>
    </div>
  )
}
