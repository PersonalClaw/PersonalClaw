import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { BellRing, Hand, ShieldCheck } from 'lucide-react'
import { useGatewayConnection } from '../../app/gatewayConnection'
import { heldWithAModifier } from '../../app/keymap'
import type { RouteProps } from '../../app/useQueryState'
import { clockTime } from '../../lib/epoch'
import { useProjectList } from '../../lib/loopProject'
import type { NeedsYouRow } from '../../lib/needsYou'
import { useNeedsYou } from '../../lib/useNeedsYou'
import { useStaleAt } from '../../lib/waitingList'
import { typesText } from '../../ui/ApprovalPrompt'
import { Button } from '../../ui/Button'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { EmptyState, InlineLoadError, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { PageTitle } from '../../ui/PageTitle'
import { TextLink } from '../../ui/TextLink'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { TopBar } from '../../ui/TopBar'
import { ListBesideDetail, type CapabilityDetail } from '../capabilities/ListBesideDetail'
import { Group } from './Group'
import { NeedCard, NeedDetail, NeedsUnreadable, needHeading, needName, rowKey, type Outcome } from './NeedCards'
import { LARGE_LIST } from './RunningLane'
import { useNeedActions } from './useNeedActions'

// ── Needs you: the one place to make the decisions waiting on you ─────────────────────────────────
//
// Every ask from every source sits in one list, "Waiting on your answer", in the order
// `GET /api/waiting` serves it (what could change the most first, then the oldest); then "To review",
// the proposals and notes nothing is blocked on, oldest first (`needsYou`). Each is answered on its
// own card through the call its own page makes (`useNeedActions`). It is a queue that should be
// empty most of the time, so it shows nothing else: what runs, what finished and your chats live on
// Home's Overview and in the sidebar.
//
// With nothing open the list takes the tab's whole width and each group's cards sit in a grid that
// reads left to right and then down; a card that grows grows its row below its own answers, and
// moves no other card's. Selecting a card (`?open=`, pushed, so Back closes it) opens its panel beside
// the list from 840 px of the tab's width, and in its place below; with a panel open the list is one
// column.
//
// The list holds still while you work in it: while the pointer is over it, the focus is in it, an
// answer is on its way, or under 2 s have passed since your last press, key or scroll there, a card
// that settles keeps its place and height and says how it went, and new asks wait behind "2 new
// asks · Show them" in the header. Then it takes the latest read, in order.

/** ── THE ONE RECONCILIATION POINT with `dashboard/views_store.py` ──────────────────────────
 *
 *  The locked Needs you preset registers one `core:` tile ref. This map is the only place it appears
 *  on the frontend, and `tests/test_dashboard_mission_control_preset.py` reads it out of this file
 *  and asserts set equality with the registry: a tile ref is a string on both sides, so nothing else
 *  can see the two drift apart. */
export const GROUP_REFS: Record<'needs-you', string> = {
  'needs-you': 'core:group-needs-you',
}

/** The preset's own id, which `#/mission-control` opens: one string, one place. */
export const MISSION_CONTROL_VIEW_ID = 'mission-control'

/** How many proposals and notes To review shows while asks wait, before it fills its last row. */
export const REVIEW_WHILE_ASKS = 3

/** How long after your last press, key or scroll in the list it still holds still. */
export const HOLD_MS = 2000

/** How long the panel waits before it follows a selection ↑↓ moved, so a run of keys opens one. */
const FOLLOW_MS = 150

/** The grid every group's cards sit in while no panel is open: as many columns of at least 24rem as
 *  the width takes, one on a phone and at 200% text. */
const COLUMNS = 'grid-cols-[repeat(auto-fill,minmax(min(100%,24rem),1fr))]'

const GONE = 'It is no longer waiting: it was answered or ended somewhere else.'

/** How many columns *grid* draws now, read off its computed tracks. */
function columnsOf(grid: HTMLElement | null): number {
  if (!grid) return 1
  const tracks = getComputedStyle(grid).gridTemplateColumns
  const n = tracks ? tracks.split(/\s+/).filter((t) => t && t !== 'none').length : 1
  return Math.max(1, n)
}

/** The column count *grid* draws, read when it mounts (it is drawn only once the list has rows)
 *  and again whenever it resizes. */
function useColumns(grid: HTMLElement | null, oneColumn: boolean): number {
  const [n, setN] = useState(1)
  useLayoutEffect(() => {
    if (oneColumn) { setN(1); return }
    const read = () => setN(columnsOf(grid))
    read()
    if (!grid || typeof ResizeObserver !== 'function') return
    const seen = new ResizeObserver(read)
    seen.observe(grid)
    return () => seen.disconnect()
  }, [grid, oneColumn])
  return n
}

/** Whether the list holds still: the pointer over it, the focus in it, an answer on its way, or
 *  under `HOLD_MS` since a press, key or scroll in it. */
function useHold(list: React.RefObject<HTMLElement | null>, busy: boolean): boolean {
  const [inside, setInside] = useState(false)
  const [focused, setFocused] = useState(false)
  const [recent, setRecent] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => {
    const el = list.current
    if (!el) return
    const touch = () => {
      setRecent(true)
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setRecent(false), HOLD_MS)
    }
    const enter = () => setInside(true)
    const leave = () => setInside(false)
    const focusIn = () => setFocused(true)
    const focusOut = (e: FocusEvent) => { if (!(e.relatedTarget instanceof Node && el.contains(e.relatedTarget))) setFocused(false) }
    // A scroll does not bubble: the list's scroller is its ancestor, so it is heard on the document.
    const scrolled = (e: Event) => { if (e.target instanceof Node && e.target.contains(el)) touch() }
    el.addEventListener('pointerenter', enter)
    el.addEventListener('pointerleave', leave)
    el.addEventListener('pointerdown', touch)
    el.addEventListener('keydown', touch)
    el.addEventListener('focusin', focusIn)
    el.addEventListener('focusout', focusOut)
    document.addEventListener('scroll', scrolled, true)
    return () => {
      window.clearTimeout(timer.current)
      el.removeEventListener('pointerenter', enter)
      el.removeEventListener('pointerleave', leave)
      el.removeEventListener('pointerdown', touch)
      el.removeEventListener('keydown', touch)
      el.removeEventListener('focusin', focusIn)
      el.removeEventListener('focusout', focusOut)
      document.removeEventListener('scroll', scrolled, true)
    }
  }, [list])
  return inside || focused || recent || busy
}

/** The list as it stands while it holds still: the rows it showed, each with its latest read where
 *  it is still listed, and the asks that came since, which wait to be shown. */
function useHeldRows(latest: NeedsYouRow[], holding: boolean, selected: string) {
  const shown = useRef<NeedsYouRow[]>(latest)
  const [showNow, setShowNow] = useState(0)
  const lastShow = useRef(0)
  const byKey = new Map(latest.map((r) => [r.key, r]))
  let rows: NeedsYouRow[]
  // With no card shown (the all-clear, a failed read, the first read) there is nothing to keep in
  // place: the read is shown as it lands.
  if (holding && lastShow.current === showNow && shown.current.length > 0) {
    rows = shown.current.map((r) => byKey.get(r.key) ?? r)
  } else {
    // The selected card stays until the selection moves, at its place, whatever the read says.
    const kept = selected ? shown.current.findIndex((r) => r.key === selected) : -1
    rows = latest
    if (kept !== -1 && !byKey.has(selected)) rows = [...latest.slice(0, kept), shown.current[kept], ...latest.slice(kept)]
    lastShow.current = showNow
  }
  shown.current = rows
  const listed = new Set(rows.map((r) => r.key))
  const fresh = latest.filter((r) => !listed.has(r.key))
  const gone = new Set(rows.filter((r) => !byKey.has(r.key)).map((r) => r.key))
  return { rows, fresh, gone, show: () => setShowNow((n) => n + 1) }
}

export function NeedsYouPage({ query, setQuery, navigate }: RouteProps) {
  const needs = useNeedsYou()
  const listRef = useRef<HTMLElement>(null)
  const [reviewGrid, setReviewGrid] = useState<HTMLUListElement | null>(null)
  const connection = useGatewayConnection()
  const offline = connection.state === 'disconnected'
  const { outcomes, actions } = useNeedActions(needs.refresh)
  const busy = Object.values(outcomes).some((o) => o.state === 'busy')
  const holding = useHold(listRef, busy)
  const open = query.open ?? ''
  const held = useHeldRows(needs.rows, holding, open)
  const rows = held.rows
  const waitingItems = useMemo(() => rows.flatMap((r) => (r.origin === 'waiting' ? [r.item] : [])), [rows])
  const now = useStaleAt(waitingItems)
  const projects = useProjectList(waitingItems.map((item) => item.project_id))
  const projectOf = (id: string | undefined) => {
    const p = id ? projects.byId.get(id) : undefined
    return p ? { id: p.id, name: p.name } : null
  }
  const asks = rows.filter((r) => r.origin === 'waiting')
  const review = rows.filter((r) => r.origin !== 'waiting')

  // ── The selection ──
  const selectedRow = rows.find((r) => rowKey(r) === open)
  const panelOpen = open !== ''
  const columns = useColumns(reviewGrid, panelOpen)
  const [allAsks, setAllAsks] = useState(false)
  const [allReview, setAllReview] = useState(false)
  // An `?open=` past the fold unfolds its group.
  const openIndex = (list: NeedsYouRow[]) => list.findIndex((r) => rowKey(r) === open)
  const asksShown = allAsks || openIndex(asks) >= LARGE_LIST ? asks : asks.slice(0, LARGE_LIST)
  const reviewCap = asks.length > 0 ? Math.ceil(REVIEW_WHILE_ASKS / columns) * columns : LARGE_LIST
  const reviewShown = allReview || openIndex(review) >= reviewCap ? review : review.slice(0, reviewCap)
  const followTimer = useRef<number | undefined>(undefined)
  const [followed, setFollowed] = useState('')
  const select = useCallback((key: string) => { setFollowed(''); setQuery({ open: key }) }, [setQuery])
  const close = useCallback(() => setQuery({ open: null }), [setQuery])

  // The selected card in view after the list reflows (two columns become one when a panel opens),
  // on an explicit open or an arrival at `?open=`, clear of the fade by the scroller's padding.
  useLayoutEffect(() => {
    if (!open || followed === open) return
    const card = listRef.current?.querySelector<HTMLElement>(`[data-need-key="${CSS.escape(open)}"]`)
    card?.scrollIntoView?.({ block: 'nearest' })
  }, [open, followed])
  useEffect(() => () => window.clearTimeout(followTimer.current), [])
  // Closing the panel (Close, Escape, Back) puts the focus back on the card it showed, unless the
  // focus is already in the list.
  const lastOpen = useRef('')
  useEffect(() => {
    if (open) { lastOpen.current = open; return }
    const was = lastOpen.current
    lastOpen.current = ''
    const list = listRef.current
    if (!was || !list) return
    if (document.activeElement && list.contains(document.activeElement)) return
    list.querySelector<HTMLElement>(`[data-need-key="${CSS.escape(was)}"] [data-row-hit-target]`)?.focus()
  }, [open])

  const outcomeOf = (r: NeedsYouRow): Outcome | undefined => outcomes[r.key]
    ?? (held.gone.has(r.key) ? { state: 'ended', text: GONE } : undefined)

  const detail: CapabilityDetail | null = !open ? null : selectedRow ? {
    id: open, title: needName(selectedRow), heading: needHeading(selectedRow), onClose: close,
    followsSelection: followed === open,
    body: <NeedDetail row={selectedRow} outcome={outcomeOf(selectedRow)} actions={actions} now={now} />,
  } : needs.data ? {
    id: open, title: 'This is no longer waiting on you', onClose: close,
    body: <p data-type="body-m" className="text-on-surface">This is no longer waiting on you. It was answered or ended somewhere else.</p>,
  } : null

  // ── The list's own keys: a grid of cards' open targets, never taken from a field ──
  const onListKey = (e: KeyboardEvent<HTMLElement>) => {
    if (heldWithAModifier(e) || typesText(e.target)) return
    const keys = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End']
    if (!keys.includes(e.key)) return
    const card = e.target instanceof Element ? e.target.closest<HTMLElement>('[data-need-card]') : null
    const list = listRef.current
    if (!card || !list) return
    const cards = [...list.querySelectorAll<HTMLElement>('[data-need-card]')]
    const at = cards.indexOf(card)
    if (at === -1) return
    const next = nextCard(cards, at, e.key, panelOpen)
    e.preventDefault()
    e.stopPropagation()
    if (!next) return
    next.querySelector<HTMLElement>('[data-row-hit-target]')?.focus()
    next.scrollIntoView?.({ block: 'nearest' })
    // With the panel open, ↑↓ move the selection too; the panel follows after a moment, with a
    // replace, so one Back closes it, and the focus stays in the list.
    if (panelOpen && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
      const key = next.getAttribute('data-need-key') ?? ''
      window.clearTimeout(followTimer.current)
      followTimer.current = window.setTimeout(() => { setFollowed(key); setQuery({ open: key }, { replace: true }) }, FOLLOW_MS)
    }
  }

  const showNew = () => {
    const first = held.fresh[0]?.key
    held.show()
    if (!first) return
    window.requestAnimationFrame(() => {
      listRef.current?.querySelector<HTMLElement>(`[data-need-key="${CSS.escape(first)}"] [data-row-hit-target]`)?.focus()
    })
  }

  const failed = !!needs.error && !needs.data
  const allClear = !needs.loading && !needs.error && rows.length === 0 && needs.unreadable.length === 0
  const card = (r: NeedsYouRow) => (
    <li key={r.key} className="flex min-w-0">
      <NeedCard row={r} outcome={outcomeOf(r)} actions={actions} now={now}
        selected={open === rowKey(r)} onOpen={() => select(rowKey(r))} off={offline ? "Can't reach PersonalClaw" : undefined}
        project={r.origin === 'waiting' ? projectOf(r.item.project_id) : null} />
    </li>
  )
  const grid = panelOpen ? 'grid-cols-1' : COLUMNS
  const fresh = held.fresh.length

  const header = (
    <TopBar
      contentAligned
      left={(
        <div className="flex min-w-0 items-center gap-s">
          {/* The name is a part the title cannot shed (`data-title-keep`): the bar counts it whole in
              the slot's minimum, so where the band between the shell corners cannot hold it beside the
              `…` (390 px), the row goes under the corners and the name keeps its own line, instead of
              reading "Nee…". */}
          <PageTitle><span data-title-keep className="whitespace-nowrap">Needs you</span></PageTitle>
          {fresh > 0 ? (
            // Outside the list's scroller, so it covers and moves nothing in it.
            <p role="status" data-type="label-m" className="flex min-w-0 items-center gap-xs text-on-surface-var">
              <span className="truncate">{`${fresh} new ${fresh === 1 ? 'ask' : 'asks'}`}</span>
              <span aria-hidden="true">·</span>
              <Button size="xs" variant="ghost-accent" onClick={showNew} className={TOUCH_TARGET}>Show them</Button>
            </p>
          ) : null}
        </div>
      )}
      right={(
        <HeaderActions overflowClassName={TOUCH_TARGET}>
          <HeaderControl icon={ShieldCheck} label="Change what needs your OK" inOverflow onClick={() => navigate('settings/agent')} />
          <HeaderControl icon={BellRing} label="Change when asks interrupt you" inOverflow onClick={() => navigate('settings/notifications')} />
        </HeaderActions>
      )}
    />
  )

  return (
    <ListBesideDetail header={header} detail={detail} noun="Needs you"
      listWidth={720} splitWidth={440} headingScrolls compactTargets focusOnOpen edgeFade>
      <section ref={listRef} role="region" aria-label="Needs you" data-tour="approvals" onKeyDown={onListKey}
        className="flex min-h-full w-full min-w-0 flex-col gap-l px-l py-l">
        {offline ? (
          <p role="status" data-type="body-s" className="text-on-surface-low">
            {`Can't reach PersonalClaw. Showing what was loaded${connection.lastReachedAt ? ` at ${clockTime(connection.lastReachedAt / 1000)}` : ''}.`}
          </p>
        ) : null}
        {needs.error && needs.data ? <InlineLoadError what="what is waiting on you" error={needs.error} onRetry={needs.refresh} /> : null}

        {needs.loading && !needs.data ? (
          <ListSkeleton rows={3} what="waiting list" />
        ) : failed ? (
          <Centred><LoadError what="waiting list" error={needs.error} onRetry={needs.refresh} /></Centred>
        ) : allClear ? (
          <Centred>
            <EmptyState icon={Hand} title="Nothing is waiting on you."
              hint="When an agent asks for your OK or an answer, it shows up here, and the badge at the top counts it."
              footer={<TextLink href="#/dashboard" ink="emphasis">See what your agents are doing on Home</TextLink>} />
          </Centred>
        ) : (
          <>
            {asks.length > 0 || needs.unreadable.length > 0 ? (
              <Group id="needs-you-waiting" label="Waiting on your answer" count={needs.unreadable.length > 0 ? null : asks.length}>
                <NeedsUnreadable unreadable={needs.unreadable} nothingElse={asks.length === 0} />
                {asks.length > 0 ? <ul className={`grid gap-s ${grid}`} data-need-group="waiting">{asksShown.map(card)}</ul> : null}
                {asks.length > asksShown.length || allAsks ? (
                  <div>
                    <Button size="xs" variant="ghost-accent" ariaExpanded={allAsks} className={TOUCH_TARGET} onClick={() => setAllAsks((was) => !was)}>
                      {allAsks ? `Show the first ${LARGE_LIST}` : `Show all ${asks.length}`}
                    </Button>
                  </div>
                ) : null}
              </Group>
            ) : null}
            {review.length > 0 ? (
              <Group id="needs-you-review" label="To review" count={review.length}>
                <ul ref={setReviewGrid} className={`grid gap-s ${grid}`} data-need-group="review">{reviewShown.map(card)}</ul>
                {review.length > reviewShown.length || allReview ? (
                  <div>
                    <Button size="xs" variant="ghost-accent" ariaExpanded={allReview} className={TOUCH_TARGET} onClick={() => setAllReview((was) => !was)}>
                      {allReview ? 'Show fewer to review' : `Show all ${review.length} to review`}
                    </Button>
                  </div>
                ) : null}
              </Group>
            ) : null}
          </>
        )}
      </section>
    </ListBesideDetail>
  )
}

/** What stands alone on the list's area, centred on it both ways: the all-clear, a failed read. */
function Centred({ children }: { children: ReactNode }) {
  return <div className="grid flex-1 place-items-center">{children}</div>
}

/** The card a list key moves to from *cards[at]*, or none at an end. ↑↓ go to the card above or
 *  below in the same column (across a group's heading to the nearest card by position), ←→ to the
 *  previous or next in reading order, Home and End to the first and last. In one column (a phone,
 *  a panel open) ↑↓ and ←→ both step in order. */
export function nextCard(cards: HTMLElement[], at: number, key: string, oneColumn: boolean): HTMLElement | null {
  if (key === 'Home') return cards[0] ?? null
  if (key === 'End') return cards[cards.length - 1] ?? null
  const step = key === 'ArrowLeft' || key === 'ArrowUp' ? -1 : 1
  const here = cards[at].getBoundingClientRect()
  const sameColumn = !oneColumn && (key === 'ArrowUp' || key === 'ArrowDown')
    && cards.some((c, i) => i !== at && Math.abs(c.getBoundingClientRect().left - here.left) > 1)
  if (!sameColumn) return cards[at + step] ?? null
  // The nearest card above or below, preferring the same column, else the nearest by position.
  const candidates = cards
    .map((c, i) => ({ c, i, r: c.getBoundingClientRect() }))
    .filter(({ i, r }) => i !== at && (step > 0 ? r.top > here.top + 1 : r.top < here.top - 1))
  if (candidates.length === 0) return null
  const rowTop = step > 0 ? Math.min(...candidates.map(({ r }) => r.top)) : Math.max(...candidates.map(({ r }) => r.top))
  const row = candidates.filter(({ r }) => Math.abs(r.top - rowTop) <= 1)
  row.sort((a, b) => Math.abs(a.r.left - here.left) - Math.abs(b.r.left - here.left))
  return row[0]?.c ?? null
}
