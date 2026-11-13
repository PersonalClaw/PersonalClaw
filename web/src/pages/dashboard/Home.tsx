import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AlertTriangle, Hand, LayoutGrid, MessageSquare, Plus, ShieldAlert } from 'lucide-react'
import { api, type ChatSessionSummary, type RunningList, type RunningRow } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { clockTime, sessionRecencyMs } from '../../lib/epoch'
import { useProjectList } from '../../lib/loopProject'
import { useStaleAt } from '../../lib/waitingList'
import { rowSubject } from '../../lib/rowSubject'
import { phaseOf } from '../../lib/runningRows'
import { sessionTitle } from '../../lib/sessionTitle'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { useCutOff } from '../../lib/useCutOff'
import { useNeedsYou } from '../../lib/useNeedsYou'
import { useIdentity, firstNameOf } from '../../app/identity'
import { useIsMobile } from '../../app/useIsMobile'
import { WidgetBoundary } from '../../app/ErrorBoundary'
import type { RouteProps } from '../../app/useQueryState'
import { Button } from '../../ui/Button'
import { HeaderActions, HeaderControl, HeaderSegmented } from '../../ui/HeaderActions'
import { HeaderFilter } from '../../ui/HeaderFilter'
import { RowHitTarget } from '../../ui/RowHitTarget'
import { StatusPill } from '../../ui/StatusPill'
import { TextLink } from '../../ui/TextLink'
import { TopBar } from '../../ui/TopBar'
import { InlineLoadError } from '../../ui/ListScaffold'
import { TextInput } from '../../ui/forms'
import { SurfaceOverlay } from '../../ui/surfaces/SurfaceOverlay'
import { ListBesideDetail, type CapabilityDetail } from '../capabilities/ListBesideDetail'
import { StartedByApp, startedByLabel, startedByName } from '../chat/StartedByApp'
import { FromChannel, channelOf } from '../chat/FromChannel'
import { HomeOverview } from './DashboardPage'
import { NeedCard, NeedDetail, NeedsUnreadable, needHeading, needName, rowKey } from './NeedCards'
import { PinnedTiles } from './PinnedTiles'
import { FinishedRows, LARGE_LIST, RunningLane } from './RunningLane'
import { readShown, writeShown } from './homeShown'
import { useNeedActions } from './useNeedActions'
import { Suggestions } from './widgets/Suggestions'
import { MarkdownLine } from '../../ui/MarkdownLine'
import { previewLine } from '../../lib/previewText'
import { cutMarkdownLine } from '../../lib/cutMarkdown'
import { Markdown } from '../../ui/Markdown'

// ── Home: the attention stack ───────────────────────────────────────────────────────────────────
//
// Opening PersonalClaw answers three questions at a glance, in this order (#3813):
//
//   Needs you            everything waiting on you to answer, from every source (`GET /api/waiting`,
//                        #3888), in the one order every surface keeps: by consequence, then age.
//   Running now          the one list of live work (`GET /api/running`), each row with its exact
//                        phase and its last update.
//   Recent and completed what just finished, and your own latest threads.
//
// Idle work is no group: it is found by search. A group that empties says what that means, a group
// whose read failed says so, and a source the gateway could not read is named, because an empty
// Needs you is this page's "all clear", the most misleading thing it could show by mistake.
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
// The header's View switches to Overview, the bands that have no place in the three groups (your
// agents' day, pinned artifacts, the desktop and browser live views, Discover and recent activity),
// `?view=overview`.

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
  'needs-you': 'Needs you',
  'running-now': 'Running now',
  recent: 'Recent and completed',
}

const GROUP_EMPTY: Record<Group, string> = {
  'needs-you': 'Nothing is waiting on you.',
  'running-now': 'Nothing is running right now.',
  recent: 'Nothing has finished lately, and you have no chats yet.',
}

/** The views Home's header switches between, in the owner's order: Overview on the left,
 *  Attention on the right. A phone shows each by its glyph, named by its label. */
export const HOME_VIEWS = [
  { key: 'overview', label: 'Overview', icon: LayoutGrid },
  { key: 'attention', label: 'Attention', icon: Hand },
]

type Shown = 'all' | Group

const SHOWN_OPTIONS = [
  { key: 'all', label: 'All groups' },
  ...GROUPS.map((g) => ({ key: g, label: GROUP_LABEL[g] })),
]

/** Your chats shown under Recent and completed. */
const RECENT_THREADS = 6

const RUNNING_KEY = 'dashboard:running-now'

/** A read that failed, in the gateway's own words: nothing was asked of it, so nothing was lost. */
function readFailureText(what: string, err: unknown): string {
  const detail = err instanceof Error && err.message ? err.message : String(err ?? 'unknown error')
  return `Could not read ${what}: ${detail}.`
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

/** Home: Overview, or Attention when it is asked for (#5880). *defaultView* is the view the address
 *  opens on with no `?view`: Overview at `#/dashboard`, Attention at `#/mission-control`. */
export function Home({ defaultView = 'overview', ...route }: RouteProps & { defaultView?: 'overview' | 'attention' }) {
  const { query, setQuery, navigate } = route
  const view = query.view === 'overview' || query.view === 'attention' ? query.view : defaultView
  const { name } = useIdentity()
  // The header's controls go through the one responsive cluster, which sheds labels as the room
  // narrows, in the one order every header keeps: the view's filter (Attention's), the View switch,
  // New chat last. A phone's New chat keeps a 44 px target beside the shell's corner controls.
  const phone = useIsMobile()
  const header = (filter?: ReactNode) => (
    <TopBar
      contentAligned
      left={<h1 data-type="headline-s" className="min-w-0 truncate text-on-surface">{greetingFor(name)}</h1>}
      right={(
        <HeaderActions>
          {filter}
          <HeaderSegmented ariaLabel="View" options={HOME_VIEWS} value={view}
            onChange={(k) => setQuery({ view: k === defaultView ? null : k, open: null })} />
          <HeaderControl icon={Plus} label="New chat" variant="secondary" priority="primary"
            className={phone ? 'min-h-11 min-w-11' : undefined} onClick={() => navigate('chat/new')} />
        </HeaderActions>
      )}
    />
  )
  if (view === 'overview') {
    // The column does not clip: its scroller is bounded by `min-h-0`, so the column's own
    // `overflow-hidden` only cut, at the fold, the focus ring of whatever control lay on it (the UI
    // matrix's clipped-ring check, on Schedule's "New trigger" at its viewport).
    return (
      <div className="flex h-full flex-col">
        {header()}
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

function AttentionStack({ header, ...route }: RouteProps & { header: (filter?: ReactNode) => ReactNode }) {
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
  const [allNeeds, setAllNeeds] = useState(false)
  const [shown, setShown] = useState<Shown>(() => readShown(GROUPS))
  const shows = (g: Group) => shown === 'all' || shown === g

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
  const { outcomes, actions } = useNeedActions(needs.refresh)
  // The project each card's work is in, named from the project list: a card names only a project
  // the list knows (`ProjectChip`), and the list is read again once for one made since it was read.
  const projects = useProjectList([
    ...waitingItems.map((item) => item.project_id),
    ...(runningList?.running ?? []).map((r) => r.project_id),
  ])
  const projectOf = (id: string | undefined) => {
    const p = id ? projects.byId.get(id) : undefined
    return p ? { id: p.id, name: p.name } : null
  }

  const rows = runningList?.running ?? []
  const finished = runningList?.finished ?? []
  const liveChats = useMemo(() => new Set(rows.map((r) => r.session).filter(Boolean)), [rows])
  const threads = useMemo(() => (Array.isArray(sessionsData) ? sessionsData : [])
    .filter((s) => s.title && !s.run_state?.turn && !liveChats.has(s.key))
    .sort((a, b) => sessionRecencyMs(b) - sessionRecencyMs(a))
    .slice(0, RECENT_THREADS), [sessionsData, liveChats])

  // ── The selection ──
  const open = query.open ?? ''
  const select = (key: string) => setQuery({ open: key })
  const close = () => setQuery({ open: null })
  const selectedNeed = needs.rows.find((r) => rowKey(r) === open)
  const selectedRun = open.startsWith('run:') ? [...rows, ...finished].find((r) => `run:${r.work}` === open)
    ?? cut.interrupted.map((i) => i.row).find((r) => `run:${r.work}` === open) : undefined
  const selectedThread = open.startsWith('thread:') && Array.isArray(sessionsData)
    ? sessionsData.find((s) => `thread:${s.key}` === open) : undefined
  // A chat named by its first words is cut with "…": read once with the cut repaired, so its heading,
  // its name and its tooltip are one value with no mark left open.
  const threadTitle = selectedThread ? cutMarkdownLine(sessionTitle(selectedThread), Infinity) : ''
  const detail: CapabilityDetail | null = selectedNeed ? {
    id: open, title: needName(selectedNeed), heading: needHeading(selectedNeed), onClose: close,
    body: <NeedDetail row={selectedNeed} outcome={outcomes[selectedNeed.key]} actions={actions} now={now} />,
  } : selectedRun ? {
    id: open, title: previewLine(selectedRun.name), heading: <MarkdownLine text={selectedRun.name} />, onClose: close, body: <RunDetail row={selectedRun} />,
  } : selectedThread ? {
    id: open, title: previewLine(threadTitle), heading: <MarkdownLine text={threadTitle} />, onClose: close, body: <ThreadDetail s={selectedThread} />,
  } : null

  const needsFailed = !!needs.error && !needs.data
  const shownNeeds = allNeeds ? needs.rows : needs.rows.slice(0, LARGE_LIST)
  const allClear = !needs.loading && !needs.error && needs.rows.length === 0 && needs.unreadable.length === 0
  const failures = [
    needs.error ? readFailureText('what is waiting on you', needs.error) : '',
    runningError ? readFailureText('what is running', runningError) : '',
  ].filter(Boolean)

  return (
    <ListBesideDetail header={header(
      // All three groups or one of them, so a narrow window can show the one it is for: a view
      // choice, kept per browser, in the header's filter ("Show: All groups").
      <HeaderFilter sections={[{
        title: 'Show', value: shown, defaultKey: 'all', options: SHOWN_OPTIONS,
        onChange: (k) => { const v = k as Shown; setShown(v); writeShown(v) },
      }]} />,
    )} detail={detail} noun="Home">
      <section role="region" aria-label="Home" data-tour="approvals"
        className="mx-auto flex w-full min-w-0 flex-col gap-l px-l py-l">
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

        <StartSomething navigate={route.navigate} />

        {shows('needs-you') ? <Group group="needs-you" count={needs.data ? needs.rows.length : null}>
          <NeedsUnreadable unreadable={needs.unreadable}
            nothingElse={needs.rows.length === 0 && !needs.loading && !needsFailed} />
          {needs.rows.length === 0 ? (
            needs.loading || needsFailed || needs.unreadable.length === 0 ? (
              <p data-type="body-s" className="text-on-surface-low">
                {needs.loading ? 'Loading…' : needsFailed ? 'Could not read what is waiting on you.' : GROUP_EMPTY['needs-you']}
              </p>
            ) : null
          ) : (
            <ul className="flex min-w-0 flex-col gap-s">
              {shownNeeds.map((r) => (
                <li key={r.key} className="min-w-0">
                  <NeedCard row={r} outcome={outcomes[r.key]} actions={actions} now={now}
                    selected={open === rowKey(r)} onOpen={() => select(rowKey(r))}
                    project={r.origin === 'waiting' ? projectOf(r.item.project_id) : null} />
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
        </Group> : null}

        {shows('running-now') ? <RunningLane rows={rows} loading={runningLoading} failed={!!runningError && !runningList}
          empty={GROUP_EMPTY['running-now']} selected={open.startsWith('run:') ? open.slice(4) : undefined}
          onOpen={(r) => select(`run:${r.work}`)} projectOf={(r) => projectOf(r.project_id)} /> : null}

        {shows('recent') ? <Group group="recent" count={null}>
          <FinishedRows finished={finished} interrupted={cut.interrupted}
            selected={open.startsWith('run:') ? open.slice(4) : undefined} onOpen={(r) => select(`run:${r.work}`)} />
          {threads.length > 0 ? (
            <div className="flex min-w-0 flex-col gap-xs">
              <p id="home-your-chats" data-type="label-s" className="text-on-surface-low">Your chats</p>
              <ul aria-labelledby="home-your-chats" className="flex min-w-0 flex-col gap-xs">
                {threads.map((s) => (
                  <ThreadRow key={s.key} s={s} selected={open === `thread:${s.key}`} onOpen={() => select(`thread:${s.key}`)} />
                ))}
              </ul>
            </div>
          ) : null}
          {sessionsError && !Array.isArray(sessionsData) ? (
            <InlineLoadError what="your chats" error={sessionsError} onRetry={refreshSessions} />
          ) : finished.length === 0 && cut.interrupted.length === 0 && threads.length === 0 ? (
            <p data-type="body-s" className="text-on-surface-low">
              {sessionsLoading || runningLoading ? 'Loading…' : GROUP_EMPTY.recent}
            </p>
          ) : null}
        </Group> : null}

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

/** A running or ended row, in full: its phase in words, what it is doing, who, since when. */
function RunDetail({ row }: { row: RunningRow }) {
  const look = phaseOf(row)
  return (
    <div className="flex min-w-0 flex-col gap-m">
      <div className="flex min-w-0 flex-wrap items-center gap-xs">
        {row.blocked ? <StatusPill tone="neutral">Blocked</StatusPill> : null}
        <StatusPill tone={look.tone}>{look.label}</StatusPill>
      </div>
      {row.line ? <p data-type="body-m" className="min-w-0 text-on-surface"><MarkdownLine text={row.line} /></p> : null}
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

/** One of your chats under Recent and completed: its title and when it last moved. */
function ThreadRow({ s, selected, onOpen }: { s: ChatSessionSummary; selected: boolean; onOpen: () => void }) {
  // A title can be the first words of the chat, cut with "…": repaired once, so no mark is left open in
  // the row, its tooltip or its name.
  const title = cutMarkdownLine(sessionTitle(s), Infinity)
  const at = clockTime(s.last_ts || null)
  // An app's conversation says so in the row's name too: opening it means speaking under the APP's
  // permissions, and a screen reader hears the name before choosing. So does a chat that came in on
  // a chat channel.
  const app = startedByName(s)
  const channel = channelOf(s)
  const named = rowSubject([previewLine(title), app ? startedByLabel(app) : '', channel ? `From ${channel}` : ''], 120)
  return (
    <li className={`flex min-w-0 items-center gap-s rounded-lg border border-outline-variant/30 p-s ${selected ? 'ring-1 ring-primary' : ''} relative cursor-pointer has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary`}
      tabIndex={-1} onClick={onOpen}>
      <RowHitTarget label={`Show the details of ${named}`} selected={selected} />
      <MessageSquare size={14} className="shrink-0 text-on-surface-low" aria-hidden="true" />
      <span data-type="label-m" className="min-w-0 flex-1 truncate text-on-surface-var" title={previewLine(title)}><MarkdownLine text={title} /></span>
      <StartedByApp s={s} />
      <FromChannel s={s} />
      {at ? <span data-type="body-s" className="shrink-0 text-on-surface-low">{at}</span> : null}
    </li>
  )
}

/** A chat, in its detail: its last words and the way into it. */
function ThreadDetail({ s }: { s: ChatSessionSummary }) {
  return (
    <div className="flex min-w-0 flex-col gap-m">
      {/* The gateway cut the last words at 80 characters and "…": repaired, so no mark is left open. */}
      {s.last_message ? <p data-type="body-m" className="min-w-0 text-on-surface"><Markdown inline>{cutMarkdownLine(s.last_message, 240)}</Markdown></p> : null}
      <TextLink href={`#/chat/${encodeURIComponent(s.key)}`} ink="emphasis" size="sm">Open the chat</TextLink>
    </div>
  )
}

/** "Start something": one line that starts a new thread with it as the first message, on the
 *  default agent and model. Enter starts it and opens the thread, through the path the chat's own
 *  first send takes (`api.createChatSession`, then `api.sendChat`); nothing is sent while the owner
 *  types, and a start that fails keeps the line and says why. */
function StartSomething({ navigate }: { navigate: RouteProps['navigate'] }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState('')
  const start = async (line: string) => {
    setBusy(true)
    setFailed('')
    try {
      const chat = await api.createChatSession()
      await api.sendChat(line, chat.key)
      setText('')
      navigate(`chat/${encodeURIComponent(chat.key)}`)
    } catch (err) {
      const detail = err instanceof Error && err.message ? err.message : String(err ?? 'unknown error')
      setFailed(`Could not start a new chat: ${detail}. Nothing was sent, so try again.`)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="flex min-w-0 flex-col gap-xs">
      <TextInput value={text} onChange={setText} ariaLabel="Start something"
        placeholder="Start something: type it and press Enter for a new chat"
        onKeyDown={(e) => {
          if (e.key !== 'Enter' || e.nativeEvent.isComposing || busy) return
          e.preventDefault()
          const line = text.trim()
          if (line) void start(line)
        }} />
      {failed ? (
        <p role="alert" data-type="body-s" className="flex min-w-0 items-start gap-xs text-on-surface">
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-error" aria-hidden="true" />
          {failed}
        </p>
      ) : null}
    </div>
  )
}
