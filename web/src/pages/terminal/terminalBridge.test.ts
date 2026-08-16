/** Regression: runInTerminalWhenReady must retry until the SEND succeeds — not
 *  merely until the session registers. TerminalView registers its sender
 *  synchronously at mount, while its WebSocket is still CONNECTING; a sender in
 *  that window returns false. The old cockpit dispatch checked mere registration
 *  and fired ONCE, so every cold "Run tests" click was silently
 *  dropped (S3 round-1 as-a-user find). */
import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  registerTerminal,
  unregisterTerminal,
  runInTerminal,
  runInTerminalWhenReady,
} from './terminalBridge'

afterEach(() => {
  // the bridge is a module-level singleton — drain anything a test registered
  for (const id of ['t1', 't2']) unregisterTerminal(id)
  vi.useRealTimers()
})

describe('a dropped command says so on screen', () => {
  // 🔑 THE SAME DEFECT THIS FILE WAS WRITTEN ABOUT, at the other end of the retry. The retry exists
  // because a one-shot send dropped every cold "Run tests" click (the docblock above). At the 15s
  // boundary the silent drop returned: the user pressed Run, waited fifteen seconds, and got nothing.
  // The only record was a `console.warn`, which no user reads — the same shape as
  // `serviceWorkerBlockedReason` reaching only `console.info` (cycle 181).
  it('raises a toast when the terminal never becomes ready, not just a console warning', () => {
    vi.useFakeTimers()
    const toasts: { message: string; level: string }[] = []
    const onToast = (e: Event) => toasts.push((e as CustomEvent).detail)
    window.addEventListener('ne:toast', onToast)
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      // never registered → every attempt fails until the deadline
      runInTerminalWhenReady('pytest -x', () => 'never-live')
      expect(toasts, 'silent while it is still retrying').toHaveLength(0)
      vi.advanceTimersByTime(15_100)
      expect(toasts, 'exactly one toast, once').toHaveLength(1)
      expect(toasts[0].level, 'a dropped user action is an error, not info').toBe('error')
      expect(toasts[0].message).toMatch(/never became ready/)
      expect(toasts[0].message, 'and it says what to do next').toMatch(/Open a terminal and try again/)
      // the developer signal stays — it carries the command, which the toast deliberately omits
      expect(warn).toHaveBeenCalledTimes(1)
      expect(String(warn.mock.calls[0][1])).toBe('pytest -x')
    } finally {
      window.removeEventListener('ne:toast', onToast)
      warn.mockRestore()
    }
  })

  it('says nothing when the command DOES land — no toast on the happy path', () => {
    vi.useFakeTimers()
    const toasts: unknown[] = []
    const onToast = (e: Event) => toasts.push((e as CustomEvent).detail)
    window.addEventListener('ne:toast', onToast)
    try {
      let open = false
      registerTerminal('t1', (t) => (open ? (delivered.push(t), true) : false))
      const delivered: string[] = []
      runInTerminalWhenReady('ls', () => 't1')
      open = true
      vi.advanceTimersByTime(300)
      expect(toasts, 'a successful run must stay quiet').toHaveLength(0)
      vi.advanceTimersByTime(20_000)
      expect(toasts, 'and must not toast later either').toHaveLength(0)
    } finally {
      window.removeEventListener('ne:toast', onToast)
    }
  })
})

describe('runInTerminalWhenReady', () => {
  it('retries a sender that is registered but not yet OPEN, then delivers exactly once', () => {
    vi.useFakeTimers()
    const delivered: string[] = []
    let open = false
    registerTerminal('t1', (text) => {
      if (!open) return false // WS still CONNECTING
      delivered.push(text)
      return true
    })
    // old behavior: registration gated a ONE-SHOT runInTerminal → dropped here
    // (t1 IS registered, but its socket is still CONNECTING so the send fails)
    expect(runInTerminal('pytest', 't1')).toBe(false)

    const cancel = runInTerminalWhenReady('pytest', () => 't1')
    vi.advanceTimersByTime(350) // a few failed attempts while CONNECTING
    expect(delivered).toEqual([])
    open = true // socket opens
    vi.advanceTimersByTime(200)
    expect(delivered).toEqual(['pytest\n'])
    vi.advanceTimersByTime(2000) // no re-delivery after success
    expect(delivered).toEqual(['pytest\n'])
    cancel()
  })

  it('re-resolves the target id each attempt (cockpit Restart mints a new session mid-wait)', () => {
    vi.useFakeTimers()
    const delivered: string[] = []
    let live = 't1'
    // t1 never becomes ready (restarted away); t2 is live from the start
    registerTerminal('t1', () => false)
    runInTerminalWhenReady('make test', () => live)
    vi.advanceTimersByTime(300)
    registerTerminal('t2', (text) => { delivered.push(text); return true })
    live = 't2'
    vi.advanceTimersByTime(200)
    expect(delivered).toEqual(['make test\n'])
  })

  it('gives up after the 15s cap with a console.warn instead of spinning forever', () => {
    vi.useFakeTimers()
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    registerTerminal('t1', () => false)
    runInTerminalWhenReady('npm run build', () => 't1')
    vi.advanceTimersByTime(16_000)
    expect(warn).toHaveBeenCalledWith(
      'Run command dropped — terminal never became ready:', 'npm run build')
    warn.mockRestore()
  })

  it('cancel() stops the retry loop (unmount mid-wait must not fire into a later terminal)', () => {
    vi.useFakeTimers()
    const delivered: string[] = []
    let open = false
    registerTerminal('t1', (text) => { if (!open) return false; delivered.push(text); return true })
    const cancel = runInTerminalWhenReady('ls', () => 't1')
    vi.advanceTimersByTime(200)
    cancel()
    open = true
    vi.advanceTimersByTime(2000)
    expect(delivered).toEqual([])
  })
})
