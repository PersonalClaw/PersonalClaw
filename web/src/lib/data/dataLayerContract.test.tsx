import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, renderHook, screen, waitFor, act } from '@testing-library/react'
import { useQuery, useMutation, invalidateKeys, isStale, peekQuery, writeQuery, resetDataStore } from './index'
import { CACHE_NAMESPACES, staleAfterMsFor } from './keys'
import { LoadError, EmptyState } from '../../ui/ListScaffold'
import { StaleNotice } from '../../ui/StaleNotice'

// ── The four properties one data layer has to have ────────────────────────────────────────────
//
// DSC-14 replaced `useCachedData` (124 files reached for it) with `lib/data`. These are the
// behaviours that were not merely refactored but ADDED, each pinned against the shape it replaced.
//
//   1. requests are DEDUPLICATED          N mounts of one key → one request
//   2. a write INVALIDATES what it says    and mounted readers repaint with no manual refetch
//   3. a cached paint is FRESH or LABELLED never a confident stale value that is silently replaced
//   4. a failed read is an ERROR, not EMPTY "we could not load it" ≠ "you have none"
//
// 🪤 The pre-fix behaviour is reproduced inline where the contrast is the claim, rather than
// asserted from memory — the deleted helper's algorithm is nine lines and a "before" that does not
// actually reproduce the defect makes before/after identical and the whole claim vacuous.

beforeEach(() => { resetDataStore(); sessionStorage.clear() })

// ─────────────────────────────────────────────────────────────────────────────────────────────
describe('1. requests are deduplicated', () => {
  it('five components mounting one key at once produce ONE request', async () => {
    const fetcher = vi.fn().mockResolvedValue(['row'])
    function Reader() {
      const { data } = useQuery<string[]>('inbox:dedup', fetcher)
      return <span>{data ? data.length : '-'}</span>
    }
    render(<><Reader /><Reader /><Reader /><Reader /><Reader /></>)
    await waitFor(() => expect(screen.getAllByText('1')).toHaveLength(5))
    // The number is the assertion. The helper this replaced fired one request PER HOOK INSTANCE,
    // so this same tree cost five identical GETs — and five widgets over one dashboard key is a
    // shipped shape, not a hypothetical.
    expect(fetcher, 'one key, one request').toHaveBeenCalledTimes(1)
  })

  it('and all five paint the SAME bytes, because there is one entry', async () => {
    let n = 0
    const fetcher = vi.fn(() => Promise.resolve([`v${n++}`]))
    function Reader() {
      const { data } = useQuery<string[]>('inbox:same', fetcher)
      return <span>{data?.[0] ?? '-'}</span>
    }
    render(<><Reader /><Reader /><Reader /></>)
    await waitFor(() => expect(screen.getAllByText('v0')).toHaveLength(3))
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('a fetcher whose identity changes every render does NOT multiply requests', async () => {
    // 🪤 THIS IS THE 289k-REQUEST SHAPE, bounded. A previous incident here measured 289,116 failed
    // requests and `net::ERR_INSUFFICIENT_RESOURCES` from an unstable callback identity that a
    // consumer had dependency-listed. Dedup keyed on the FETCHER would be no dedup at all, because
    // essentially every call site passes a fresh arrow. So it is keyed on the cache key, and both
    // halves are bounded here: the fetcher is a new function on every render, and `refresh` — the
    // identity that caused the incident — is asserted stable.
    const calls = { n: 0 }
    let renders = 0
    const seen: (() => void)[] = []
    function Reader() {
      renders++
      const { data, refresh } = useQuery<number>('inbox:unstable', () => {
        calls.n++
        return Promise.resolve(calls.n)
      })
      seen.push(refresh)
      return <span>{data ?? '-'}</span>
    }
    render(<><Reader /><Reader /><Reader /></>)
    await waitFor(() => expect(screen.getAllByText('1')).toHaveLength(3))
    await new Promise((r) => setTimeout(r, 50))
    expect(calls.n, 'a per-render fetcher identity must not re-trigger the fetch').toBe(1)
    expect(renders, 'and the tree must settle rather than spin').toBeLessThan(20)
    expect(new Set(seen).size, 'refresh keeps ONE identity per reader').toBeLessThanOrEqual(3)
  })

  it('a mounted reader joins an in-flight request instead of starting a second', async () => {
    let release: (v: string[]) => void = () => {}
    const fetcher = vi.fn(() => new Promise<string[]>((res) => { release = res }))
    function Reader() {
      const { data } = useQuery<string[]>('inbox:join', fetcher)
      return <span>{data ? 'got' : 'wait'}</span>
    }
    const { rerender } = render(<Reader />)
    rerender(<><Reader /><Reader /></>)
    expect(fetcher).toHaveBeenCalledTimes(1)
    await act(async () => { release(['x']) })
    await waitFor(() => expect(screen.getAllByText('got').length).toBeGreaterThan(0))
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────────
describe('2. a mutation invalidates the keys it declares', () => {
  it('the write is reflected in a MOUNTED reader with no manual refetch and no reload', async () => {
    const server = { rows: ['a'] }
    const fetcher = vi.fn(() => Promise.resolve([...server.rows]))
    function Surface() {
      const { data } = useQuery<string[]>('tasks:list', fetcher)
      const create = useMutation({
        // The map is HERE, in the same object literal as the write. Nothing infers it, and a
        // reviewer reading the write sees its blast radius without grepping for busts.
        run: async (name: string) => { server.rows = [...server.rows, name] },
        invalidates: [{ prefix: 'tasks' }],
      })
      return (
        <>
          <span data-testid="rows">{(data ?? []).join(',')}</span>
          <button onClick={() => void create.mutate('b')}>add</button>
        </>
      )
    }
    render(<Surface />)
    await waitFor(() => expect(screen.getByTestId('rows').textContent).toBe('a'))
    await act(async () => { screen.getByText('add').click() })
    // No `refresh()` anywhere in that component. The old idiom was `await write();
    // invalidateCache(k); refresh()`, and the `refresh()` reached only the caller's own hook.
    await waitFor(() => expect(screen.getByTestId('rows').textContent).toBe('a,b'))
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('reaches a SIBLING reader of the same collection under a different key', async () => {
    // The four measured instances of this family are written up in `splitCollectionBusts.test.ts`
    // and `siblingCacheStaleness.test.ts`: one collection, two keys, a bust that could only ever
    // reach one. A prefix map plus store subscriptions closes both halves at once.
    const list = vi.fn().mockResolvedValue(['a'])
    const picker = vi.fn().mockResolvedValue(['a'])
    function Both() {
      useQuery<string[]>('tasks', list)
      useQuery<string[]>('tasks-all', picker)
      const m = useMutation({ run: async () => {}, invalidates: [{ prefix: 'tasks' }] })
      return <button onClick={() => void m.mutate()}>go</button>
    }
    render(<Both />)
    await waitFor(() => expect(picker).toHaveBeenCalledTimes(1))
    await act(async () => { screen.getByText('go').click() })
    await waitFor(() => expect(picker, 'the sibling key re-read itself').toHaveBeenCalledTimes(2))
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('a key OUTSIDE the declared map is left alone', async () => {
    const other = vi.fn().mockResolvedValue(['keep'])
    function Both() {
      useQuery<string[]>('triggers:list', other)
      const m = useMutation({ run: async () => {}, invalidates: [{ prefix: 'tasks' }] })
      return <button onClick={() => void m.mutate()}>go</button>
    }
    render(<Both />)
    await waitFor(() => expect(other).toHaveBeenCalledTimes(1))
    await act(async () => { screen.getByText('go').click() })
    await new Promise((r) => setTimeout(r, 30))
    expect(other, 'an over-broad map is its own defect').toHaveBeenCalledTimes(1)
  })

  it('invalidation HOLDS the paint — it does not blank the surface to a skeleton', async () => {
    // The old store DELETED the entry, so `data` went undefined and every panel's
    // `if (!data) return <Skeleton/>` gate fired: a full remount flash on an interaction that
    // changed almost nothing. Here the value is stamped not-current and kept.
    const fetcher = vi.fn().mockResolvedValue(['a'])
    const { result } = renderHook(() => useQuery<string[]>('tasks:hold', fetcher))
    await waitFor(() => expect(result.current.data).toEqual(['a']))
    act(() => { invalidateKeys('tasks:hold') })
    expect(result.current.data, 'still on screen').toEqual(['a'])
    expect(result.current.loading, 'so this is NOT a loading state').toBe(false)
    expect(result.current.stale, 'it is a labelled one').toBe(true)
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────────
describe('3. a cached first paint is FRESH or explicitly labelled', () => {
  // The deleted helper's persistence algorithm, reproduced verbatim so the contrast is measured
  // rather than remembered. This IS the pre-fix code: a JSON value under a `cache:` key, and
  // nothing else — no age, no way to compute one.
  const PRE_FIX_PREFIX = 'cache:'
  const preFixWrite = (key: string, val: unknown) => sessionStorage.setItem(PRE_FIX_PREFIX + key, JSON.stringify(val))
  const preFixSeed = (key: string): unknown => {
    const raw = sessionStorage.getItem(PRE_FIX_PREFIX + key)
    return raw == null ? undefined : JSON.parse(raw)
  }

  it('THE UNFIXED SHAPE: a persisted record carries no age, so nothing CAN be labelled', () => {
    preFixWrite('settings:inbox', { retention_days: 30 })
    const seeded = preFixSeed('settings:inbox') as { retention_days: number }
    expect(seeded.retention_days, 'the old value is painted').toBe(30)
    // There is no third state to reach for. The record is the value; the helper set `loading` from
    // "is anything cached" and `revalidating` from "is a request in flight", and neither can
    // express "what you are looking at is old". Measured in a browser against this exact shape:
    // `#/settings` Inbox tile, retention 30 cached, changed to 7 out of band, hard reload with the
    // revalidation held → FIRST PAINT "30 day retention", `[data-stale]` 0, `[aria-busy]` 0, no
    // "updating" copy — then it silently became 7.
    expect(Object.keys(seeded), 'no age is recorded, so no staleness is derivable').toEqual(['retention_days'])
  })

  it('THE FIX: a value seeded from a previous page load is stale, and the hook says so', async () => {
    // Written the way the layer writes it, INCLUDING a fresh timestamp — the point is that the
    // timestamp is deliberately not trusted across a reload.
    sessionStorage.setItem('cache:settings:inbox', JSON.stringify({ v: { retention_days: 30 }, at: Date.now() }))
    const fetcher = vi.fn().mockResolvedValue({ retention_days: 7 })
    const { result } = renderHook(() => useQuery<{ retention_days: number }>('settings:inbox', fetcher, { persist: true }))
    // First paint: the old value IS shown — an instant paint is the whole point of a cache …
    expect(result.current.data?.retention_days).toBe(30)
    // … and it is labelled, which is the difference.
    expect(result.current.stale, 'a value from a previous page load is not current').toBe(true)
    expect(result.current.loading, 'but there IS something on screen, so not a skeleton either').toBe(false)
    await waitFor(() => expect(result.current.data?.retention_days).toBe(7))
    expect(result.current.stale, 'and the label clears when fresh data lands').toBe(false)
  })

  it('a value that just landed in THIS session is fresh, and carries no label', async () => {
    const fetcher = vi.fn().mockResolvedValue(['a'])
    const { result } = renderHook(() => useQuery<string[]>('settings:fresh', fetcher))
    await waitFor(() => expect(result.current.data).toEqual(['a']))
    expect(result.current.stale, 'nothing to say about a value from a moment ago').toBe(false)
    // The window is DECLARED, not inferred per call site.
    expect(staleAfterMsFor('settings:fresh')).toBe(CACHE_NAMESPACES.settings.staleAfterMs)
  })

  it('past its declared window, the same value is labelled', async () => {
    // The first read lands and is fresh; the window is then 1ms, so 30ms later the SAME entry is
    // past it. The second reader's fetch is held open on purpose — otherwise the mocked resolve
    // makes the value fresh again before anything can be observed, and the test would pass by
    // measuring nothing.
    writeQuery('inbox:aged', ['a'])
    await new Promise((r) => setTimeout(r, 30))
    const held = vi.fn(() => new Promise<string[]>(() => {}))
    const { result } = renderHook(() => useQuery<string[]>('inbox:aged', held, { staleAfterMs: 1 }))
    expect(result.current.data, 'the cached value still paints instantly').toEqual(['a'])
    expect(result.current.stale, 'and it is labelled, because it is past its window').toBe(true)
    expect(result.current.revalidating, 'while the re-read is on the wire').toBe(true)
    expect(result.current.loading, 'and it is not a skeleton — there is something to show').toBe(false)
  })

  it('the freshness predicate is the DECLARED window, not a constant', () => {
    // `settings` is CONFIG (minutes) and `inbox` is LIVE (seconds): the same 10-second-old entry
    // is fresh under one and stale under the other, which is the whole reason the registry exists.
    const tenSecondsAgo = { value: ['a'], at: Date.now() - 10_000, epoch: 0 }
    expect(isStale('settings:x', tenSecondsAgo), 'config tolerates minutes').toBe(false)
    expect(isStale('inbox:x', tenSecondsAgo), 'live data does not').toBe(true)
    expect(isStale('inbox:x', undefined), 'an ABSENT entry is loading, not stale').toBe(false)
  })

  it('the label is findable structurally, not by copy', () => {
    const { container } = render(<StaleNotice stale what="items" />)
    expect(container.querySelector('[data-stale="true"]'), 'the probe and the tests both key on this').not.toBeNull()
    expect(container.querySelector('[role="status"]'), 'polite — a re-read is not bad news').not.toBeNull()
  })

  it('and renders NOTHING when the paint is fresh', () => {
    const { container } = render(<StaleNotice stale={false} what="items" />)
    expect(container.textContent).toBe('')
  })

  it('peekQuery hands back a FRESH value only — a caller that cannot label gets nothing', async () => {
    // `#/chat` seeds its transcript from this. It has no place to hang an "updating" label on a
    // transcript, so it must not be handed a stale one: fresh, or fall through to loading.
    writeQuery('chat:detail:abc', { messages: [1] })
    expect(peekQuery('chat:detail:abc')).toEqual({ messages: [1] })
    act(() => { invalidateKeys('chat:detail:abc') })
    expect(peekQuery('chat:detail:abc'), 'not-current means not handed out').toBeUndefined()
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────────
describe('4. a failed read is an ERROR state, distinct from an empty one', () => {
  it('the layer reports a rejection instead of resolving empty', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('probe-induced failure'))
    const { result } = renderHook(() => useQuery<string[]>('inbox:fail', fetcher))
    await waitFor(() => expect(result.current.error).toBeTruthy())
    expect(result.current.data, 'never substituted with []').toBeUndefined()
    expect(result.current.status).toBe('error')
    expect(result.current.loading, 'and it stops loading — a failed read is not a permanent shimmer').toBe(false)
  })

  it('an EMPTY success and a FAILURE are different statuses, so a surface can branch', async () => {
    const empty = renderHook(() => useQuery<string[]>('inbox:empty', () => Promise.resolve([])))
    await waitFor(() => expect(empty.result.current.status).toBe('success'))
    expect(empty.result.current.data).toEqual([])
    const failed = renderHook(() => useQuery<string[]>('inbox:broke', () => Promise.reject(new Error('x'))))
    await waitFor(() => expect(failed.result.current.status).toBe('error'))
    // 🔑 The two branches must be REACHABLE in that order. `data === undefined` is true for the
    // loading, error AND empty branches, so a surface that tests emptiness first can never render
    // the error — the failed-fetch-renders-as-empty-state defect, which this repo has catalogued
    // by name. The status field is what makes the order hard to get wrong.
    expect(empty.result.current.status).not.toBe(failed.result.current.status)
  })

  it('the two states are structurally different on screen, not differently worded', () => {
    const err = render(<LoadError what="items" error={new Error('gateway timed out')} onRetry={() => {}} />)
    expect(err.container.querySelector('[role="alert"]'), 'a failure interrupts').not.toBeNull()
    expect(err.getByRole('button', { name: /retry/i }), 'and offers recovery').toBeTruthy()
    const none = render(<EmptyState title="No items yet" />)
    expect(none.container.querySelector('[role="alert"]'), '"you have none" is a normal answer').toBeNull()
  })
})
