import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { renderHook } from '@testing-library/react'
import {
  DEFAULT_NOTIFICATION_ROUTE,
  NOTIFICATION_SOURCE_ROUTES,
  routeForNote,
  shouldDeliverNatively,
  useNativeNotifications,
} from './nativeNotifications'

// ── The renderer half of the `native` delivery target ────────────────────
//
// The audit finding this closes: `native` was in the notification-rules target vocabulary
// with NOTHING dispatching to it. So the legs below are paired — every positive assertion
// has a vacuity twin driven through the same hook, because a relay that shows a banner for
// every note is exactly as wrong as one that shows none, and only the twin can tell them
// apart.
//
// REAL leg (jsdom): the hook, the WS frame shape, the bridge call. SIMULATED: the bridge
// itself is a recording double — `window.pclawDesktop` exists only inside Electron.
// UNRUNNABLE here: whether macOS renders a banner. That needs a launched shell.

/** Captures the callback `useChatSocket` is given, so a test can deliver a frame. */
let deliver: ((m: { type: string; data: Record<string, unknown> }) => void) | null = null
vi.mock('./useChatSocket', () => ({
  useChatSocket: (cb: (m: { type: string; data: Record<string, unknown> }) => void) => {
    deliver = cb
  },
}))

/** The waiting list an alert's banner reads, and the still orbs it asked to have painted. */
const waiting = vi.fn()
vi.mock('./api', async (orig) => ({ ...(await orig<Record<string, unknown>>()), api: { waiting: () => waiting() } }))
const painted: string[] = []
vi.mock('../ui/buddy/paintOrb', () => ({
  stillOrbPng: (state: string) => { painted.push(state); return `data:image/png;base64,${state}` },
}))

function fakeBridge() {
  const shown: Array<{ title: string; body: string; route: string; alert?: string; icon?: string }> = []
  const taps: Array<(p: { route: string }) => void> = []
  let unsubscribed = 0
  const bridge = {
    capabilities: {
      names: () => [],
      probe: vi.fn(),
      snapshot: vi.fn(),
      request: vi.fn(),
      on: () => () => {},
    },
    notifications: {
      show: (note: { title: string; body: string; route: string; alert?: string; icon?: string }) => {
        shown.push(note)
        return Promise.resolve({ ok: true, route: note.route })
      },
      on: (cb: (p: { route: string }) => void) => {
        taps.push(cb)
        return () => { unsubscribed += 1 }
      },
    },
  }
  ;(window as unknown as { pclawDesktop: unknown }).pclawDesktop = bridge
  return { shown, tap: (p: { route: string }) => taps.forEach((t) => t(p)), unsubscribedCount: () => unsubscribed }
}

const note = (extra: Record<string, unknown> = {}) => ({
  kind: 'error', title: 'Loop stalled', body: 'needs an answer', ...extra,
})

beforeEach(() => {
  deliver = null
  painted.length = 0
  waiting.mockReset()
  waiting.mockResolvedValue({ waiting: [], unreadable: [] })
})
afterEach(() => {
  delete (window as unknown as { pclawDesktop?: unknown }).pclawDesktop
  vi.restoreAllMocks()
})

describe('shouldDeliverNatively', () => {
  it('reads native.deliver, not the presence of the key', () => {
    // 🪤 A rule that named `native` while no shell was connected ALSO carries a `native`
    // key — with `deliver: false` and the reason. Treating presence as consent would turn
    // the fallback into a double delivery the moment a shell appeared mid-session.
    expect(shouldDeliverNatively(note({ native: { deliver: true, reason: '' } }))).toBe(true)
    expect(shouldDeliverNatively(note({ native: { deliver: false, reason: 'not connected' } }))).toBe(false)
    expect(shouldDeliverNatively(note())).toBe(false)
    expect(shouldDeliverNatively(note({ native: true }))).toBe(false)
    expect(shouldDeliverNatively(note({ native: 'yes' }))).toBe(false)
  })
})

describe('routeForNote', () => {
  it('maps a rule source to a surface', () => {
    expect(routeForNote(note({ source: 'inbox' }))).toBe('inbox')
    expect(routeForNote(note({ source: 'loop' }))).toBe('loops')
    expect(routeForNote(note({ source: 'planning' }))).toBe('tasks')
  })

  it('🔑 a due-task reminder opens Tasks, not the notification feed', () => {
    // `tasks/due` (`tasks/due_notices.py`) is the `tasks` source's one kind. With no row here a
    // tap on its desktop banner fell through to the feed — the reminder's own surface is Tasks.
    expect(routeForNote(note({ source: 'tasks' }))).toBe('tasks')
  })

  it('falls back to the feed for an absent or unknown source', () => {
    expect(routeForNote(note())).toBe(DEFAULT_NOTIFICATION_ROUTE)
    expect(routeForNote(note({ source: 'not-a-source' }))).toBe(DEFAULT_NOTIFICATION_ROUTE)
    expect(routeForNote(note({ source: 7 }))).toBe(DEFAULT_NOTIFICATION_ROUTE)
  })

  it('covers every source the backend can send', () => {
    // Parsed from the Python rather than restated, so a new notification source reds this
    // instead of quietly deep-linking every one of its notes to the feed.
    const py = readFileSync(join(__dirname, '../../../src/personalclaw/notification_kinds.py'), 'utf8')
    const sources = new Set(
      [...py.matchAll(/NotificationKind\(\s*"([a-z_]+)"/g)].map((m) => m[1]),
    )
    expect(sources.size).toBeGreaterThan(5)
    for (const s of sources) expect(Object.keys(NOTIFICATION_SOURCE_ROUTES)).toContain(s)
  })

  it('only ever names a route App.tsx actually serves', () => {
    // The rail that keeps a renamed route from turning every native tap into a dead link.
    const app = readFileSync(join(__dirname, '../app/App.tsx'), 'utf8')
    const nav = [...app.matchAll(/\{ id: '([a-z-]+)', label:/g)].map((m) => m[1])
    const routable = (app.match(/const ROUTABLE = new Set\(\[([^\]]*)\]/)?.[1] ?? '')
    const extras = [...routable.matchAll(/'([a-z-]+)'/g)].map((m) => m[1])
    const served = new Set([...nav, ...extras])
    expect(served.size).toBeGreaterThan(10)
    for (const route of Object.values(NOTIFICATION_SOURCE_ROUTES)) {
      expect([...served]).toContain(route)
    }
  })
})

describe('useNativeNotifications', () => {
  it('raises a banner for a note the gateway marked deliverable', () => {
    const bridge = fakeBridge()
    renderHook(() => useNativeNotifications(vi.fn()))
    deliver!({ type: 'notification', data: note({ source: 'loop', native: { deliver: true, reason: '' } }) })
    expect(bridge.shown).toEqual([{ title: 'Loop stalled', body: 'needs an answer', route: 'loops' }])
  })

  it('🪤 VACUITY: raises nothing for a note the gateway did not mark', () => {
    // Same hook, same bridge, same frame type. Without this the test above proves only
    // that a banner appears — not that the RULE caused it.
    const bridge = fakeBridge()
    renderHook(() => useNativeNotifications(vi.fn()))
    deliver!({ type: 'notification', data: note({ source: 'loop' }) })
    deliver!({ type: 'notification', data: note({ source: 'loop', native: { deliver: false, reason: 'the desktop shell is not connected' } }) })
    expect(bridge.shown).toEqual([])
  })

  it('ignores WS frames that are not notifications', () => {
    const bridge = fakeBridge()
    renderHook(() => useNativeNotifications(vi.fn()))
    deliver!({ type: 'chat_message', data: note({ native: { deliver: true, reason: '' } }) })
    deliver!({ type: 'refresh', data: { kinds: ['notifications'] } })
    expect(bridge.shown).toEqual([])
  })

  it('does nothing in a browser tab', () => {
    // No bridge is the fallback path: the bell and the feed already have the note.
    const navigate = vi.fn()
    expect(() => {
      renderHook(() => useNativeNotifications(navigate))
      deliver!({ type: 'notification', data: note({ native: { deliver: true, reason: '' } }) })
    }).not.toThrow()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('navigates when a banner is tapped', () => {
    const bridge = fakeBridge()
    const navigate = vi.fn()
    renderHook(() => useNativeNotifications(navigate))
    bridge.tap({ route: 'inbox' })
    expect(navigate).toHaveBeenCalledWith('inbox')
  })

  it('drops a tap route it does not recognize', () => {
    const bridge = fakeBridge()
    const navigate = vi.fn()
    renderHook(() => useNativeNotifications(navigate))
    for (const route of ['', 'not-a-route', 'https://evil.example'])
      bridge.tap({ route })
    expect(navigate).not.toHaveBeenCalled()
  })

  it('unsubscribes from taps on unmount', () => {
    const bridge = fakeBridge()
    const { unmount } = renderHook(() => useNativeNotifications(vi.fn()))
    unmount()
    expect(bridge.unsubscribedCount()).toBe(1)
  })
})

// ── A buddy alert's banner (#3897) ───────────────────────────────────────────────────────────

describe('a buddy alert', () => {
  const live = { native: { deliver: true, reason: '' } }
  const approvalItem = {
    id: 'approval:ap-1', kind: 'approval', work: 'chat:c1', session: 'c1', consequence: 'changes', risk: 'caution',
    brief: { tool: 'shell.run', source_label: 'chat “Release notes”' }, asked_at: null, age_secs: null, expires_at: null,
    current: true, interrupt: 'deliver', route: '#/chat/c1',
    answer: { method: 'POST', path: '/api/approvals/ap-1/{answer}', takes: ['approve', 'reject'], body: {} },
  }

  it('an approval raises its line, its still orb and the chat it asks in, from the waiting list', async () => {
    waiting.mockResolvedValue({ waiting: [approvalItem], unreadable: [] })
    const bridge = fakeBridge()
    renderHook(() => useNativeNotifications(vi.fn()))
    deliver!({ type: 'notification', data: { kind: 'agent_request', source: 'system', title: 'Approval needed: shell.run', body: 'ls', approval: 'ap-1', alert: 'approval', ...live } })
    await vi.waitFor(() => expect(bridge.shown).toHaveLength(1))
    expect(bridge.shown[0]).toEqual({
      title: 'PersonalClaw', body: 'Chat “Release notes” needs your OK: shell.run', route: 'chat/c1',
      alert: 'approval', icon: 'data:image/png;base64,approval',
    })
  })

  it('a question and an error each carry their own still orb', async () => {
    const bridge = fakeBridge()
    renderHook(() => useNativeNotifications(vi.fn()))
    deliver!({ type: 'notification', data: { kind: 'needs_input', source: 'loop', title: 'Loop needs your input', body: 'Keep the old page?', alert: 'question', ...live } })
    deliver!({ type: 'notification', data: { kind: 'error', source: 'loop', title: 'Loop failed', body: 'The model did not answer', alert: 'error', ...live } })
    await vi.waitFor(() => expect(bridge.shown).toHaveLength(2))
    expect(painted.sort()).toEqual(['error', 'input'])
    expect(bridge.shown.map((b) => b.body).sort()).toEqual(['Loop stopped: The model did not answer', 'Your work asks: Keep the old page?'])
    // No item in the list: the tap opens the note's own source, as every native note's does.
    expect(bridge.shown.map((b) => b.route)).toEqual(['loops', 'loops'])
  })

  it('a waiting list that cannot be read still raises the banner, in the note’s words', async () => {
    waiting.mockRejectedValue(new Error('offline'))
    const bridge = fakeBridge()
    renderHook(() => useNativeNotifications(vi.fn()))
    deliver!({ type: 'notification', data: { kind: 'agent_request', source: 'system', title: 'Approval needed: shell.run', body: '', approval: 'ap-1', source_label: 'loop “Docs”', alert: 'approval', ...live } })
    await vi.waitFor(() => expect(bridge.shown).toHaveLength(1))
    expect(bridge.shown[0].body).toBe('Loop “Docs” needs your OK: shell.run')
  })

  it('a note that is no alert is raised as before, at once, with no icon', () => {
    const bridge = fakeBridge()
    renderHook(() => useNativeNotifications(vi.fn()))
    deliver!({ type: 'notification', data: note({ source: 'loop', ...live }) })
    expect(bridge.shown).toEqual([{ title: 'Loop stalled', body: 'needs an answer', route: 'loops' }])
    expect(waiting).not.toHaveBeenCalled()
    expect(painted).toEqual([])
  })

  it('a tap opens the item an alert named, and still nothing else', async () => {
    waiting.mockResolvedValue({ waiting: [{ ...approvalItem, session: 'c7', route: '#/chat/c7' }], unreadable: [] })
    const bridge = fakeBridge()
    const navigate = vi.fn()
    renderHook(() => useNativeNotifications(navigate))
    bridge.tap({ route: 'chat/c7' })
    expect(navigate).not.toHaveBeenCalled()
    deliver!({ type: 'notification', data: { kind: 'agent_request', source: 'system', title: 'Approval needed: shell.run', approval: 'ap-1', alert: 'approval', ...live } })
    await vi.waitFor(() => expect(bridge.shown).toHaveLength(1))
    bridge.tap({ route: 'chat/c7' })
    expect(navigate).toHaveBeenCalledWith('chat/c7')
    bridge.tap({ route: 'chat/c8' })
    expect(navigate).toHaveBeenCalledTimes(1)
  })
})
