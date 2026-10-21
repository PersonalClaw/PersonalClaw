import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import type { NotificationItem } from '../lib/api'

// ── Notifications are a filter of the Inbox (#3818) ──────────────────────────────────────────────
//
// What arrived lived in two places: the Inbox held messages, questions and drafts, and the
// notification feed had a page of its own, `#/notifications`, so the owner checked both. The Inbox
// now has a Notifications filter that shows the feed as that page showed it, and the old address
// is retired to it by one row of `routeRedirects.ts`. Held here:
//
//   · the filter lists what the page listed: every note, in the order the server sends it (newest
//     first), under the same day groups, and narrowed the page's way (unread, or one kind);
//   · it is one of the Inbox's own filters: the Show menu offers it and marks it, and from it leads
//     back to each of the others, in place of the history entry. Its header is the Inbox's, with
//     the feed's count and the feed's own actions;
//   · a note opens there, and marking it read is the call the bell's own Mark read makes, so the
//     bell's count follows;
//   · the old address, with the keys the page read, opens the filter in place, and the bell writes
//     the new address itself;
//   · a `personalclaw://notifications` link and a native notification's tap open the filter and
//     nothing else, and Allow and Deny there are the one trust call the page made, which opening a
//     note never makes.

const { API, frames } = vi.hoisted(() => ({
  API: {
    notifications: vi.fn(),
    ackNotification: vi.fn(),
    answerUnknownSender: vi.fn(),
    autonomyLadder: vi.fn(),
    inbox: vi.fn(),
    inboxStatus: vi.fn(),
    proactiveDigest: vi.fn(),
  },
  /** Every live-frame listener a surface registered, so a test can send the frame the gateway
   *  sends after an ack. */
  frames: new Set<(m: { type: string }) => void>(),
}))

vi.mock('../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))
vi.mock('../lib/useChatSocket', () => ({
  useChatSocket: (onMessage: (m: { type: string }) => void) => { frames.add(onMessage) },
}))

const { InboxPage } = await import('../pages/inbox/InboxPage')
const { NotificationBell } = await import('../ui/NotificationBell')
const { useHashRoute } = await import('./useHashRoute')
const { redirectFor } = await import('./routeRedirects')
const { bucketOf, BUCKET_ORDER } = await import('../pages/notifications/notificationMeta')
const { invalidateKeys } = await import('../lib/data')
const { DEFAULT_NOTIFICATION_ROUTE, NOTIFICATION_SOURCE_ROUTES } = await import('../lib/nativeNotifications')

const desktop = createRequire(join(import.meta.dirname, '..', '..', '..', 'desktop', 'package.json'))
const links = desktop('./deepLinks.js') as {
  PARAMETERS: [string, unknown][]
  parseDeepLink: (raw: string) => { ok: true; route: string; hash: string } | { ok: false; reason: string }
}
const shell = desktop('./nativeNotifications.js') as { normalizeRoute: (raw: unknown) => string }

const MINUTE = 60_000
const DAY = 24 * 60 * MINUTE

/** Five notes, newest first as the server sends them, across today, a day ago and last week. */
const NOTES: readonly { title: string; kind: string; ago: number; acked: boolean }[] = [
  { title: 'The backup finished', kind: 'result', ago: 2 * MINUTE, acked: false },
  { title: 'A loop needs an answer', kind: 'loop', ago: 5 * MINUTE, acked: false },
  { title: 'The nightly digest ran', kind: 'result', ago: DAY + 2 * MINUTE, acked: true },
  { title: 'The weekly cleanup failed', kind: 'cron_failed', ago: 5 * DAY, acked: false },
  { title: 'Your agent checked in', kind: 'heartbeat', ago: 9 * DAY, acked: true },
]
const TITLES = NOTES.map((n) => n.title)
const TITLE = new RegExp(`^(${TITLES.join('|')})$`)

/** The notes' times, fixed once per test so a group never moves under it. */
let stamps: string[] = []
/** The notes marked read since the test began, by `ts`. */
let read = new Set<string>()

const feed = (): NotificationItem[] => NOTES.map((n, i) => ({
  kind: n.kind, title: n.title, body: `About: ${n.title.toLowerCase()}`,
  ts: stamps[i], acked: n.acked || read.has(stamps[i]),
}))
const tsOf = (title: string) => stamps[TITLES.indexOf(title)]

/** The note titles the list shows, top to bottom, once it shows any. */
const shown = async () => (await screen.findAllByText(TITLE)).map((el) => el.textContent)

/** The day headings the list shows, top to bottom. */
const groupsShown = () => screen.queryAllByText(/^(Today|Yesterday|Earlier)$/).map((el) => el.textContent)

/** Send every surface the frame the gateway sends when a note changes. */
const frame = (type: string) => act(() => { for (const on of [...frames]) on({ type }) })

/** The Inbox as the shell hands it its address: the hash router's own reading of the bar. */
function Shell() {
  const r = useHashRoute('dashboard')
  return r.route === 'inbox'
    ? <InboxPage query={r.query} setQuery={r.setQuery} navigate={r.navigate} />
    : <p>{`another page: ${r.route}`}</p>
}

/** An address as the router reads it: no `#/`, and its query in one order. */
function normal(address: string): string {
  const bare = address.replace(/^#?\/?/, '')
  const at = bare.indexOf('?')
  if (at < 0) return bare
  const query = [...new URLSearchParams(bare.slice(at + 1))].sort(([a, x], [b, y]) => a.localeCompare(b) || x.localeCompare(y))
  return `${bare.slice(0, at)}?${new URLSearchParams(query).toString()}`
}

const openMenu = () => fireEvent.click(screen.getByRole('button', { name: 'Filter & sort' }))
const option = (name: RegExp) => screen.findByRole('button', { name })
/** The open menu's section under *title*, as the menu draws it: its caption, then its rows. */
const section = async (title: string) =>
  (await screen.findByText(title, { selector: 'span[data-type="caption"]' })).parentElement!.parentElement!
/** The rows of a menu section that carry the check, the menu's mark for the one selected. */
const checked = (rows: HTMLElement) =>
  within(rows).getAllByRole('button').filter((b) => b.querySelector('svg.lucide-check')).map((b) => b.textContent)

/** The Inbox's sources, as the items' view reads them: nothing open. */
const STATUS = {
  enabled: false, native_source_active: true, health: { last_poll_at: 0 },
  sources: [{ name: 'native', active: true, kind: 'push', can_reply: true }],
  open_count: 0, total_count: 0,
}
/** The items' header actions, and the feed's. */
const ITEMS_ACTIONS = ['Dismiss all', 'Restart sources', 'Capture a note', 'Inbox settings']
const FEED_ACTIONS = ['Mark all read', 'Clear all']

beforeEach(() => {
  const now = Date.now()
  stamps = NOTES.map((n) => new Date(now - n.ago).toISOString())
  read = new Set()
  frames.clear()
  sessionStorage.clear()
  localStorage.clear()
  invalidateKeys('notifications')
  invalidateKeys('inbox:items')
  invalidateKeys('inbox:status')
  history.replaceState(null, '', '#/dashboard')
  API.notifications.mockReset().mockImplementation(async () => ({ notifications: feed() }))
  API.ackNotification.mockReset().mockImplementation(async (ts: string) => { read.add(ts); return { ok: true } })
  API.answerUnknownSender.mockReset().mockResolvedValue({ ok: true })
  API.autonomyLadder.mockReset().mockRejectedValue(new Error('no ladder in this test'))
  API.inbox.mockReset().mockResolvedValue([])
  API.inboxStatus.mockReset().mockResolvedValue(STATUS)
  API.proactiveDigest.mockReset().mockResolvedValue({ installed: false })
})
afterEach(cleanup)

describe('the Inbox\'s Notifications filter lists the feed the Notifications page listed', () => {
  it('🔑 every note, in the order the server sends it, under the same day groups, on the Inbox', async () => {
    render(<InboxPage query={{ filter: 'notifications' }} setQuery={vi.fn()} navigate={vi.fn()} />)
    expect(await shown()).toEqual(TITLES)
    const now = Date.now()
    const buckets = new Set(stamps.map((ts) => bucketOf(ts, now)))
    expect(groupsShown()).toEqual(BUCKET_ORDER.filter((b) => buckets.has(b)))
    expect(screen.getByRole('heading', { level: 1, name: /^Inbox/ }), 'it is the Inbox, filtered').toBeTruthy()
    expect(API.inbox, 'the feed is the notification log, not the Inbox\'s items').not.toHaveBeenCalled()
  })

  it('🔑 its header is the Inbox\'s, with the feed\'s count, and the feed\'s own actions in place of the items\'', async () => {
    // One item open, so the items' view offers every action it has.
    API.inboxStatus.mockResolvedValue({ ...STATUS, open_count: 1, total_count: 1 })
    history.replaceState(null, '', '#/inbox')
    render(<Shell />)
    for (const name of ITEMS_ACTIONS) expect(await screen.findByRole('button', { name }), name).toBeTruthy()
    openMenu()
    fireEvent.click(await option(/^Notifications/))
    expect(await screen.findByRole('heading', { level: 1, name: 'Inbox 3 unread · 5 notifications' })).toBeTruthy()
    for (const name of FEED_ACTIONS) expect(screen.getByRole('button', { name }), name).toBeTruthy()
    for (const name of ITEMS_ACTIONS) expect(screen.queryByRole('button', { name }), `${name}, on the feed`).toBeNull()
  })

  it('🔑 narrowed the page\'s way: the unread notes, or one kind\'s, still newest first', async () => {
    render(<InboxPage query={{ filter: 'notifications', show: 'unread' }} setQuery={vi.fn()} navigate={vi.fn()} />)
    expect(await shown()).toEqual(NOTES.filter((n) => !n.acked).map((n) => n.title))
    cleanup()
    render(<InboxPage query={{ filter: 'notifications', show: 'result' }} setQuery={vi.fn()} navigate={vi.fn()} />)
    expect(await shown()).toEqual(['The backup finished', 'The nightly digest ran'])
  })

  it('🔑 the Show menu offers it beside the Inbox\'s own filters, and from it leads back', async () => {
    history.replaceState(null, '', '#/inbox')
    render(<Shell />)
    await screen.findByText('Nothing has arrived yet')
    openMenu()
    fireEvent.click(await option(/^Notifications/))
    expect(await shown()).toEqual(TITLES)
    expect(normal(location.hash)).toBe('inbox?filter=notifications')
    // Its own narrowing, written where the filter reads it. (The menu stays open on a choice.)
    openMenu()
    fireEvent.click(await option(/^Unread\s*3$/))
    await waitFor(() => expect(screen.queryByText('The nightly digest ran')).toBeNull())
    expect(await shown()).toEqual(NOTES.filter((n) => !n.acked).map((n) => n.title))
    expect(normal(location.hash)).toBe(normal('inbox?filter=notifications&show=unread'))
    // And back to the Inbox's items, leaving the feed's narrowing behind.
    fireEvent.click(await option(/^Open$/))
    await screen.findByText('Nothing has arrived yet')
    expect(screen.queryByText(TITLE)).toBeNull()
    expect(location.hash).toBe('#/inbox')
  })

  it('🔑 moving between the two leaves the other list\'s open record behind', async () => {
    // An item's id and a note's time are both `open`; neither names a record of the other list.
    history.replaceState(null, '', '#/inbox?open=item-1')
    render(<Shell />)
    const entries = history.length
    openMenu()
    fireEvent.click(await option(/^Notifications/))
    expect(await shown()).toEqual(TITLES)
    expect(normal(location.hash), 'the item stays behind').toBe('inbox?filter=notifications')
    expect(history.length, 'replaced, as a filter change is').toBe(entries)
    fireEvent.click(screen.getByText('The backup finished'))
    await screen.findByRole('button', { name: 'Mark read' })
    expect(normal(location.hash)).toBe(normal(`inbox?filter=notifications&open=${encodeURIComponent(tsOf('The backup finished'))}`))
    openMenu()
    fireEvent.click(await option(/^Open$/))
    await screen.findByText('Nothing has arrived yet')
    expect(location.hash, 'the note stays behind').toBe('#/inbox')
  })

  it.each([
    ['Done', 'handled'],
    ['Needs reply', 'needs_reply'],
    ['All', 'all'],
  ] as const)('🔑 from the feed, Show › %s is the Inbox\'s own filter, written in place of the entry', async (label, key) => {
    history.replaceState(null, '', '#/inbox?filter=notifications&show=unread')
    render(<Shell />)
    await shown()
    const entries = history.length
    openMenu()
    const show = await section('Show')
    expect(checked(show), 'the feed is the Show option selected').toEqual(['Notifications'])
    expect(checked(await section('Notifications')), 'and its own narrowing beside it').toEqual(['Unread3'])
    fireEvent.click(within(show).getByRole('button', { name: label }))
    await waitFor(() => expect(location.hash).toBe(`#/inbox?filter=${key}`))
    expect(screen.queryByText(TITLE), 'the feed is gone').toBeNull()
    expect(history.length, 'replaced, as a filter change is').toBe(entries)
  })

  it('🔑 on an empty feed the menu is still there, and leads back to the items', async () => {
    API.notifications.mockImplementation(async () => ({ notifications: [] }))
    history.replaceState(null, '', '#/inbox?filter=notifications')
    render(<Shell />)
    await screen.findByText('You\'re all caught up')
    openMenu()
    fireEvent.click(await option(/^Open$/))
    await screen.findByText('Nothing has arrived yet')
    expect(location.hash).toBe('#/inbox')
  })

  it('🔑 a note opens there, and Mark read is the bell\'s own call, so the bell\'s count follows', async () => {
    const note = 'A loop needs an answer'
    render(<>
      <NotificationBell navigate={vi.fn()} />
      <InboxPage query={{ filter: 'notifications', open: tsOf(note) }} setQuery={vi.fn()} navigate={vi.fn()} />
    </>)
    await screen.findByRole('button', { name: 'Notifications, 3 unread' })
    // The open panel's Mark read; each row's own is named for its row.
    const markRead = await screen.findByRole('button', { name: 'Mark read' })
    expect(screen.getAllByText(note).length, 'the row, and the open panel\'s title').toBeGreaterThanOrEqual(2)
    fireEvent.click(markRead)
    await waitFor(() => expect(API.ackNotification).toHaveBeenCalledWith(tsOf(note)))
    frame('notification_ack')
    expect(await screen.findByRole('button', { name: 'Notifications, 2 unread' })).toBeTruthy()
  })
})

describe('#/notifications is retired to the filter', () => {
  it('🔑 the old address opens the filter in place, with the narrowing and the note it named', async () => {
    const note = 'The weekly cleanup failed'
    history.replaceState(null, '', `#/notifications?filter=unread&open=${encodeURIComponent(tsOf(note))}`)
    const entries = history.length
    render(<Shell />)
    expect(await shown()).toEqual(expect.arrayContaining(NOTES.filter((n) => !n.acked).map((n) => n.title)))
    expect(normal(location.hash)).toBe(normal(`inbox?filter=notifications&show=unread&open=${encodeURIComponent(tsOf(note))}`))
    expect(history.length, 'replaced, not pushed: Back never returns to the old address').toBe(entries)
    expect(await screen.findByRole('button', { name: 'Mark read' }), 'the note it named is open').toBeTruthy()
    expect(screen.queryByText('The nightly digest ran'), 'a read note stays out of Unread').toBeNull()
  })

  it('🔑 it keeps the keys the page read, under the names the filter reads them by, and nothing else', () => {
    expect(redirectFor('notifications')).toBe('inbox?filter=notifications')
    expect(normal(redirectFor('#/notifications?filter=cron_failed&open=2026-10-20T09%3A00%3A00Z&q=backup') ?? ''))
      .toBe(normal('inbox?filter=notifications&show=cron_failed&open=2026-10-20T09%3A00%3A00Z'))
    // The filter's own `show` among them: the old page never read one, so it narrows nothing.
    expect(redirectFor('notifications?show=unread'), 'a show the old page never read').toBe('inbox?filter=notifications')
    expect(redirectFor('notifications?show=unread&filter=result')).toBe('inbox?filter=notifications&show=result')
  })

  it('🔑 it never chains: where it lands is an address no row retires', () => {
    for (const old of ['notifications', 'notifications?filter=unread', 'notifications?open=x']) {
      const target = redirectFor(old)
      expect(target, old).not.toBeNull()
      expect(redirectFor(target!), `#/${old} -> #/${target}`).toBeNull()
    }
  })

  it('🔑 the bell opens the filter itself, on the note it shows', async () => {
    const navigate = vi.fn()
    render(<NotificationBell navigate={navigate} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Notifications, 3 unread' }))
    const subject = (await screen.findAllByRole('button', { name: /^The backup finished/ }))[0]
    fireEvent.click(subject)
    expect(navigate).toHaveBeenLastCalledWith(`inbox?filter=notifications&open=${encodeURIComponent(tsOf('The backup finished'))}`)
    fireEvent.click(await screen.findByRole('button', { name: 'Notifications, 3 unread' }))
    fireEvent.click(await screen.findByRole('button', { name: 'View all notifications' }))
    expect(navigate).toHaveBeenLastCalledWith('inbox?filter=notifications')
  })
})

describe('a link from outside the page opens the filter and nothing else', () => {
  /** A value of each state parameter's own shape, so the parser takes each one it allows here. */
  const SAMPLES: Record<string, string> = {
    project: 'project-1', mode: 'plan', pane: 'changes', file: 'notes/today.md', diff: 'notes/today.md',
    commit: '0a1b2c3', task: 'task-1', prompt: 'hello',
  }

  it('🔑 personalclaw://notifications, bare or with any state parameter the parser takes, lands on the filter alone', () => {
    const opened: string[] = []
    for (const link of ['personalclaw://notifications', 'personalclaw://notifications/',
      ...links.PARAMETERS.map(([key]) => `personalclaw://notifications?${key}=${encodeURIComponent(SAMPLES[key])}`)]) {
      const parsed = links.parseDeepLink(link)
      if (!parsed.ok) continue
      opened.push(link)
      expect(redirectFor(parsed.hash), link).toBe('inbox?filter=notifications')
    }
    expect(opened.length, 'vacuity floor: the bare link and the parameters it takes').toBeGreaterThanOrEqual(6)
  })

  it('🔑 a native tap that lands on the feed lands on the filter, and every other tap on its own page', () => {
    const taps = [...new Set([...Object.values(NOTIFICATION_SOURCE_ROUTES), DEFAULT_NOTIFICATION_ROUTE])]
    expect(taps, 'vacuity floor').toContain('notifications')
    for (const route of taps) {
      // The shell hands back the route as the page gave it, and a route carries no query.
      expect(shell.normalizeRoute(route), route).toBe(route)
      expect(redirectFor(route), route).toBe(route === 'notifications' ? 'inbox?filter=notifications' : null)
    }
  })
})

describe('Allow and Deny under the filter are the trust call the page made', () => {
  const ASKS: NotificationItem = {
    kind: 'warning', title: 'Someone new messaged you',
    body: 'Pat Example messaged your agent and is not paired. Allow them to talk to it, or deny.',
    ts: '2026-10-20T09:00:00+00:00', acked: false,
    event: 'channel.unknown_sender', provider: 'example-chat', sender_id: '4242', sender_name: 'Pat Example',
    actions: ['allow', 'deny'],
  }

  it.each([
    ['allow', /^allow pat example$/i],
    ['deny', /^deny$/i],
  ] as const)('🔑 %s: the old address opens the note and answers nothing; the button is the one call', async (action, button) => {
    API.notifications.mockImplementation(async () => ({ notifications: [ASKS, ...feed()] }))
    history.replaceState(null, '', `#/notifications?open=${encodeURIComponent(ASKS.ts)}`)
    render(<Shell />)
    const answer = await screen.findByRole('button', { name: button })
    expect(normal(location.hash)).toBe(normal(`inbox?filter=notifications&open=${encodeURIComponent(ASKS.ts)}`))
    expect(API.answerUnknownSender, 'opening the note answers no one').not.toHaveBeenCalled()
    expect(API.ackNotification, 'opening the note marks nothing read').not.toHaveBeenCalled()
    fireEvent.click(answer)
    await waitFor(() => expect(API.answerUnknownSender).toHaveBeenCalled())
    expect(API.answerUnknownSender.mock.calls).toEqual([[ASKS.ts, action]])
  })
})
