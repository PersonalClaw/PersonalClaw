import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useQuery, invalidateKeys, writeQuery, REQUEST_DEADLINE_MS, RequestDeadlineError } from './index'

// ── A reader's wait is BOUNDED, so `error` is reachable for a fetch that never settles ────────
//
// The first-run defect the owner reported: "when visiting pages like inbox, apps and settings
// after onboarding, it's just showing spinner and nothing else for long long time."
//
// The measurement that located it. Against a gateway on a fresh home, with every `/api` read
// answered by a hard 500, all three surfaces already reached a legible terminal state with a
// Retry — Inbox "Couldn't load your inbox items", Apps "Couldn't load your apps", Settings-home 34
// per-tile errors, Providers "Couldn't load your provider settings". With the same reads HELD OPEN
// instead (`new Promise(() => {})`), every one of them spun forever: Inbox on "Loading inbox
// items…", Apps on "Loading apps…", Settings-home on 34 `aria-busy` regions and 72 pulse
// skeletons, still spinning at 15s.
//
// So the hook's `error` contract was not missing at the call sites — it was UNREACHABLE one layer
// below the hook. `useQuery` derives `loading` as `data === undefined && inFlight`; a promise that
// never settles never clears `inFlight` and never sets `error`, so `loading` is permanently true
// and no error branch can ever run. That is the (B) subclass of #3394 measured in the store rather
// than at a call site.
//
// These tests pin the four properties the fix rests on. Each one is a property the pre-fix store
// failed: the first two hung forever, the third never repainted, the fourth had no dedup to keep.

const KEY = 'settings:deadline-probe'

describe('the request deadline', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })
  afterEach(() => { vi.useRealTimers() })

  it('is short enough to satisfy the 10s terminal-state bar', () => {
    // The acceptance bar for these defects is explicit: "a spinner still spinning after 10s is a
    // FAIL". A deadline at or above that would satisfy the letter of "it eventually stops" and
    // fail the requirement, so the number is asserted rather than left to a comment.
    expect(REQUEST_DEADLINE_MS).toBeLessThan(10_000)
    expect(REQUEST_DEADLINE_MS).toBeGreaterThan(2_000)  // …and not so eager it reds a slow LAN
  })

  it('turns a fetch that NEVER SETTLES into a rendered error instead of a permanent spinner', async () => {
    const { result } = renderHook(() => useQuery(KEY, () => new Promise<never>(() => {})))

    // The pre-fix state, and the whole defect: nothing to show, and still waiting.
    expect(result.current.loading).toBe(true)
    expect(result.current.status).toBe('loading')
    expect(result.current.error).toBeFalsy()

    await act(async () => { await vi.advanceTimersByTimeAsync(REQUEST_DEADLINE_MS + 50) })

    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBeInstanceOf(RequestDeadlineError)
    // The message is what `LoadError` prints under its headline, so it has to be a sentence a
    // person can act on rather than an engine string.
    expect(String((result.current.error as Error).message)).toMatch(/did not respond/i)
  })

  it('leaves a fetch that REJECTS exactly as it was — the deadline adds a path, it does not replace one', async () => {
    const boom = new Error('the gateway said no')
    const { result } = renderHook(() => useQuery(KEY + ':reject', () => Promise.reject(boom)))
    await waitFor(() => expect(result.current.status).toBe('error'))
    // Not wrapped, not re-typed: the backend's own message still reaches the surface.
    expect(result.current.error).toBe(boom)
  })

  it('still repaints when a SLOW response lands after the deadline, and clears the error', async () => {
    // This is why the deadline bounds the WAIT and not the REQUEST. Aborting would throw away a
    // response that was merely late; and without clearing `error` on a landing, every call site
    // that gates on the raw error (`appsErr ? <LoadError/> : …`) would keep showing "not
    // responding" over data that had already arrived.
    let land!: (v: { n: number }) => void
    const slow = new Promise<{ n: number }>((res) => { land = res })
    const { result } = renderHook(() => useQuery(KEY + ':slow', () => slow))

    await act(async () => { await vi.advanceTimersByTimeAsync(REQUEST_DEADLINE_MS + 50) })
    await waitFor(() => expect(result.current.error).toBeInstanceOf(RequestDeadlineError))

    await act(async () => { land({ n: 7 }) })
    await waitFor(() => expect(result.current.data).toEqual({ n: 7 }))
    expect(result.current.error).toBeFalsy()
    expect(result.current.status).toBe('success')
  })

  it('keeps the dedup guarantee: a second reader joins one request and is bounded too', async () => {
    // The store exists to collapse concurrent readers onto ONE request. A deadline implemented by
    // dropping the in-flight entry would have turned every Retry on a struggling backend into an
    // extra request, so this pins that the fetcher runs once AND that the joiner is not left
    // waiting forever on a promise it did not create.
    const fetcher = vi.fn(() => new Promise<never>(() => {}))
    const a = renderHook(() => useQuery(KEY + ':dedup', fetcher))
    const b = renderHook(() => useQuery(KEY + ':dedup', fetcher))

    await act(async () => { await vi.advanceTimersByTimeAsync(REQUEST_DEADLINE_MS + 50) })

    await waitFor(() => expect(a.result.current.status).toBe('error'))
    await waitFor(() => expect(b.result.current.status).toBe('error'))
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('does not fire for a value already in the cache', async () => {
    // A cached first paint must not be turned into an error by a background revalidation that is
    // merely slow — `data` is on screen, and `stale` is the label for that case.
    writeQuery(KEY + ':cached', { n: 1 })
    const { result } = renderHook(() => useQuery(KEY + ':cached', () => new Promise<never>(() => {})))
    expect(result.current.data).toEqual({ n: 1 })
    await act(async () => { await vi.advanceTimersByTimeAsync(REQUEST_DEADLINE_MS + 50) })
    await waitFor(() => expect(result.current.error).toBeInstanceOf(RequestDeadlineError))
    // The paint survives, and `status` stays `success` because there IS something to show.
    expect(result.current.data).toEqual({ n: 1 })
    expect(result.current.status).toBe('success')
  })
})
