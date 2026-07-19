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
