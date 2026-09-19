import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { renderHook } from '@testing-library/react'
import {
  shouldToastNote,
  toastLevelForKind,
  toastMessageForNote,
  useNotificationToasts,
} from './notificationToasts'

// ── The `immediate` mode's toast (issue #343) ─────────────────────────────
//
// The finding this closes: the rules matrix explains the modes as *"Notify = a toast"* and NO
// notification raised one, in any mode. `ne:toast` had six dispatchers and none was on the
// notification path — all three consumers of a `notification` WS frame just refetched the list — so
// `Badge` and `Notify` differed only by up to 15s of poll latency.
//
// Every positive leg below has a vacuity twin driven through the same hook, because a relay that
// toasts EVERY frame is as wrong as one that toasts none, and only the twin separates them. The
// one that matters most is `badge_only`: "Badge = kept in the list without interrupting" is the
// other half of the same sentence this file is making true, and it must not be the casualty.
//
// REAL here (jsdom): the hook, the frame shape, the dispatched event and its detail. UNRUNNABLE:
// whether `Toaster` paints — that has its own suite; this asserts the event it listens for.

/** Captures the callback `useChatSocket` is given, so a test can deliver a frame. */
let deliver: ((m: { type: string; data: Record<string, unknown> }) => void) | null = null
vi.mock('./useChatSocket', () => ({
  useChatSocket: (cb: (m: { type: string; data: Record<string, unknown> }) => void) => {
    deliver = cb
  },
}))

/** Records every `ne:toast` the hook raises. */
function captureToasts() {
  const seen: Array<{ level: string; message: string }> = []
  const onToast = (e: Event) => {
    const d = (e as CustomEvent).detail || {}
    seen.push({ level: String(d.level), message: String(d.message) })
  }
  window.addEventListener('ne:toast', onToast)
  return { seen, stop: () => window.removeEventListener('ne:toast', onToast) }
}

const note = (extra: Record<string, unknown> = {}) => ({
  kind: 'loop_failed', title: 'Loop failed', body: 'refactor-auth', ...extra,
})

beforeEach(() => { deliver = null })
afterEach(() => { vi.restoreAllMocks() })

describe('toastLevelForKind', () => {
  it('maps a failure kind to error and a completion to success', () => {
    expect(toastLevelForKind('loop_failed')).toBe('error')
    expect(toastLevelForKind('error')).toBe('error')
    expect(toastLevelForKind('cron_failed')).toBe('error')
    expect(toastLevelForKind('loop_complete')).toBe('success')
    expect(toastLevelForKind('success')).toBe('success')
  })

  it('clamps everything else to info, including warnings', () => {
    // Deliberate: `error` is the level that plays the error cue and takes the assertive live
    // region, so promoting every warning into it would chime for a stalled loop.
    expect(toastLevelForKind('warning')).toBe('info')
    expect(toastLevelForKind('needs_input')).toBe('info')
    expect(toastLevelForKind('info')).toBe('info')
  })

  it('does not throw on a kind the display map has never heard of', () => {
    // The backend registry is fail-OPEN, so an unregistered pair still delivers as system/generic.
    expect(toastLevelForKind('a-kind-from-a-newer-build')).toBe('info')
    expect(toastLevelForKind('')).toBe('info')
  })
})

describe('toastMessageForNote', () => {
  it('names the subject, not just the category', () => {
    // "Loop failed" alone says a class of thing happened without saying which loop.
    expect(toastMessageForNote(note())).toBe('Loop failed — refactor-auth')
  })

  it('falls back to the title alone when there is no body', () => {
    expect(toastMessageForNote(note({ body: '' }))).toBe('Loop failed')
    expect(toastMessageForNote(note({ body: '   ' }))).toBe('Loop failed')
  })

  it('does not repeat one string twice', () => {
    expect(toastMessageForNote(note({ body: 'Loop failed' }))).toBe('Loop failed')
  })

  it('takes only the first line, so a multi-line body cannot become a wall of text', () => {
    expect(toastMessageForNote(note({ body: 'first line\nsecond line' })))
      .toBe('Loop failed — first line')
  })
})

describe('shouldToastNote', () => {
  it('toasts a note that has something to say', () => {
    expect(shouldToastNote(note())).toBe(true)
  })

  it('NEVER toasts a badge_only note', () => {
    // "Badge = kept in the list without interrupting" is the promise on the other side of the same
    // help text. The gateway returns before broadcasting a badge, so this should be unreachable —
    // honoured anyway because that invariant lives in another process.
    expect(shouldToastNote(note({ badge_only: true }))).toBe(false)
  })

  it('stays silent on a note with no text at all', () => {
    expect(shouldToastNote({ kind: 'info', title: '', body: '' })).toBe(false)
  })
})

describe('useNotificationToasts', () => {
  it('raises a toast for a notification frame', () => {
    const { seen, stop } = captureToasts()
    renderHook(() => useNotificationToasts())
    deliver!({ type: 'notification', data: note() })
    stop()
    expect(seen).toEqual([{ level: 'error', message: 'Loop failed — refactor-auth' }])
  })

  it('🪤 ignores every other frame type', () => {
    // The vacuity twin: without it, a hook that toasted on every WS message would pass above.
    const { seen, stop } = captureToasts()
    renderHook(() => useNotificationToasts())
    deliver!({ type: 'chat_message', data: { content: 'hello' } })
    deliver!({ type: 'refresh', data: { kinds: ['loops'] } })
    deliver!({ type: 'approval', data: { id: 'a1', session: 's', tool: 'Bash' } })
    stop()
    expect(seen).toEqual([])
  })

  it('🪤 does not toast a badge_only frame, through the hook', () => {
    const { seen, stop } = captureToasts()
    renderHook(() => useNotificationToasts())
    deliver!({ type: 'notification', data: note({ badge_only: true }) })
    stop()
    expect(seen).toEqual([])
  })

  it('raises one toast per frame, not one per note in the log', () => {
    const { seen, stop } = captureToasts()
    renderHook(() => useNotificationToasts())
    deliver!({ type: 'notification', data: note({ title: 'One' }) })
    deliver!({ type: 'notification', data: note({ title: 'Two' }) })
    stop()
    expect(seen.map((t) => t.message)).toEqual(['One — refactor-auth', 'Two — refactor-auth'])
  })
})

describe('the hook is actually mounted in the shell', () => {
  it('App.tsx calls useNotificationToasts', () => {
    // 🔴 THE DEFECT THIS FILE EXISTS FOR WAS A HOOK-SHAPED HOLE, NOT A BROKEN HOOK. Every unit test
    // above passes against a module nothing imports — which is precisely the state `ne:toast` was
    // in for the notification path. The promise has to hold on every route, so the mount point is
    // the app shell and this is the assertion that it is still there.
    const app = readFileSync(join(__dirname, '..', 'app', 'App.tsx'), 'utf8')
    expect(app).toContain("from '../lib/notificationToasts'")
    expect(app).toMatch(/useNotificationToasts\(\)/)
  })
})
