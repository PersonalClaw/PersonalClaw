import { useEffect, useState } from 'react'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useCachedData, invalidateCache, writeCache } from './useCachedData'

// ── The invalidate-then-refresh contract ──────────────────────────────────────
// Every settings panel and the apps grid reload with the same idiom:
//     const reload = () => { invalidateCache(key); refresh() }
// and gate their render on `if (!data) return <Skeleton/>`. So if `data` ever drops
// to undefined during a same-key revalidation, the WHOLE panel remounts to a
// skeleton — the "full page refresh" flash. These tests pin that `data` survives a
// refresh (with or without a prior invalidate), while a genuine KEY CHANGE still
// clears, since showing one resource's data under another key would be wrong.

describe('useCachedData holds data across a same-key refresh', () => {
  beforeEach(() => {
    invalidateCache('', true)   // drop every key (prefix mode)
    sessionStorage.clear()
  })

  it('keeps the previous value visible through invalidateCache + refresh', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ n: 1 })
      .mockResolvedValueOnce({ n: 2 })
    const { result } = renderHook(() => useCachedData('k:hold', fetcher))
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))

    // The panel idiom, verbatim.
    const seen: (unknown)[] = []
    act(() => { invalidateCache('k:hold'); result.current.refresh() })
    seen.push(result.current.data)
    await waitFor(() => expect(result.current.data).toEqual({ n: 2 }))

    // The frame right after refresh must NOT be undefined — that frame is what
    // fired every panel's skeleton gate.
    expect(seen[0]).not.toBeUndefined()
    expect(seen[0]).toEqual({ n: 1 })
  })

  it('keeps data across a bare refresh (no invalidate)', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ n: 1 })
      .mockResolvedValueOnce({ n: 2 })
    const { result } = renderHook(() => useCachedData('k:bare', fetcher))
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))

    act(() => { result.current.refresh() })
    expect(result.current.data).toEqual({ n: 1 })   // never blanks
    await waitFor(() => expect(result.current.data).toEqual({ n: 2 }))
  })

  it('still reports loading during a refresh, so a panel can show a subtle spinner', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ n: 1 })
      .mockResolvedValueOnce({ n: 2 })
    const { result } = renderHook(() => useCachedData('k:loading', fetcher))
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))

    act(() => { invalidateCache('k:loading'); result.current.refresh() })
    // Data held AND loading true: that pair is what "stale-while-revalidate" means.
    expect(result.current.loading).toBe(true)
    expect(result.current.data).toEqual({ n: 1 })
    await waitFor(() => expect(result.current.loading).toBe(false))
  })

  it('CLEARS on a genuine key change — one resource must not paint under another key', async () => {
    const fetcher = vi.fn(async () => ({ n: 1 }))
    const { result, rerender } = renderHook(
      ({ k }: { k: string }) => useCachedData(k, fetcher),
      { initialProps: { k: 'k:a' } },
    )
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))

    // Switching key with nothing cached for the new key MUST blank — otherwise the
    // old resource's rows render under the new filter/id.
    rerender({ k: 'k:b' })
    expect(result.current.data).toBeUndefined()
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))
  })

  it('paints a warm key instantly on a key change (no flash when the cache has it)', async () => {
    writeCache('k:warm', { n: 9 })
    const fetcher = vi.fn(async () => ({ n: 9 }))
    const { result, rerender } = renderHook(
      ({ k }: { k: string }) => useCachedData(k, fetcher),
      { initialProps: { k: 'k:cold' } },
    )
    await waitFor(() => expect(result.current.data).toEqual({ n: 9 }))
    rerender({ k: 'k:warm' })
    expect(result.current.data).toEqual({ n: 9 })   // seeded from cache, no blank
  })

  it('a failed refresh leaves the last good value on screen', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ n: 1 })
      .mockRejectedValueOnce(new Error('network'))
    const { result } = renderHook(() => useCachedData('k:err', fetcher))
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))

    act(() => { invalidateCache('k:err'); result.current.refresh() })
    await waitFor(() => expect(result.current.error).toBeTruthy())
    // An error must not also blank the panel — the user keeps the stale view plus
    // whatever error affordance the panel renders.
    expect(result.current.data).toEqual({ n: 1 })
  })

  it('hands back the SAME refresh function across renders', async () => {
    // An unstable identity is a real outage, not a lint nit. A consumer that lists `refresh`
    // in a dependency array — `useEffect(() => { if (reloadKey) refresh() }, [reloadKey,
    // refresh])` in `dashboard/PinnedTiles` is the shipped example — re-runs that effect on
    // every render if the closure is new each time, which calls refresh, bumps the fetch, and
    // re-renders. Driven in a browser before the fix: 289,116 requests and
    // net::ERR_INSUFFICIENT_RESOURCES, after which every fetch failed and the surface sat in
    // its loading state forever. Every jsdom test still passed, which is why this one exists.
    const fetcher = vi.fn(async () => ({ n: 1 }))
    const { result, rerender } = renderHook(() => useCachedData('k:stable', fetcher))
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))
    const first = result.current.refresh
    rerender()
    rerender()
    expect(result.current.refresh).toBe(first)
  })

  it('does not refetch in a loop when a consumer depends on refresh', async () => {
    // The consumer shape from PinnedTiles, reproduced directly: the effect depends on
    // `refresh`, so an unstable identity turns one click into an unbounded fetch storm.
    const fetcher = vi.fn(async () => ({ n: 1 }))
    function Consumer() {
      const { data, refresh } = useCachedData('k:noloop', fetcher)
      const [reloadKey, setReloadKey] = useState(0)
      useEffect(() => { if (reloadKey) refresh() }, [reloadKey, refresh])
      useEffect(() => { setReloadKey(1) }, [])
      return data ? 'ready' : 'pending'
    }
    renderHook(() => Consumer())
    await waitFor(() => expect(fetcher).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 60))
    // One initial fetch plus the one the reload asked for. A loop shows up as tens or more.
    expect(fetcher.mock.calls.length).toBeLessThanOrEqual(3)
  })

  it('revalidating covers a cached re-read, unlike loading', async () => {
    // `loading` is set only when NOTHING is cached, because it gates the
    // `if (!data) return <Skeleton/>` idiom. A caller that wants "nothing is re-reading this"
    // needs a flag that is true during a revalidation over a value already on screen.
    let release: (() => void) | null = null
    const fetcher = vi.fn(async () => {
      if (release) await new Promise<void>((r) => { release = r })
      return { n: 1 }
    })
    const { result } = renderHook(() => useCachedData('k:reval', fetcher))
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))
    expect(result.current.loading).toBe(false)
    expect(result.current.revalidating).toBe(false)

    let resolveHeld: (() => void) | undefined
    const held = new Promise<void>((r) => { resolveHeld = r })
    fetcher.mockImplementationOnce(async () => { await held; return { n: 2 } })
    act(() => { result.current.refresh() })
    await waitFor(() => expect(result.current.revalidating).toBe(true))
    // The cached value stays painted and `loading` stays false — that is the whole point.
    expect(result.current.loading).toBe(false)
    expect(result.current.data).toEqual({ n: 1 })

    await act(async () => { resolveHeld?.(); await Promise.resolve() })
    await waitFor(() => expect(result.current.revalidating).toBe(false))
    expect(result.current.data).toEqual({ n: 2 })
  })
})
