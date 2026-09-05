/**
 * #485 — `api.allTasks` collects EVERY task, and says so when it could not.
 *
 * The Tasks page called `api.tasks()` with no `limit`, so the server's default of 50 applied and
 * the page presented that window as the whole set. What made it worse than a short list: every
 * view here derives structure from the array it holds. `dag.ts` filters edges to the ids present,
 * so a task whose prerequisite was row 51 drew as a clean UNBLOCKED node; `TaskDetail` computes
 * "what depends on this" the same way; the prerequisite picker could only offer the first 50
 * candidates. A wrong answer that looks exactly like a right one.
 *
 * These drive the real request helper through a stubbed `fetch`, so what is asserted is the wire
 * traffic — the offsets asked for and when the walk stops — rather than a re-description of the
 * loop.
 */
import { describe, expect, it, afterEach, vi } from 'vitest'
import { api, type TaskItem } from './api'

const PAGE = 500

const rows = (n: number, from = 0): TaskItem[] =>
  Array.from({ length: n }, (_, i) => ({ id: `t-${from + i}`, title: `task ${from + i}` }) as TaskItem)

const body = (o: Record<string, unknown>) =>
  new Response(JSON.stringify(o), { status: 200, headers: { 'Content-Type': 'application/json' } })

/** A gateway holding `total` rows that pages honestly. */
function honestServer(total: number, complete = true) {
  const calls: string[] = []
  const fetchSpy = vi.fn(async (url: string) => {
    calls.push(url)
    const offset = Number(new URL(url, 'http://x').searchParams.get('offset') ?? '0')
    const limit = Number(new URL(url, 'http://x').searchParams.get('limit') ?? '50')
    return body({
      tasks: rows(Math.max(0, Math.min(limit, total - offset)), offset),
      total,
      complete,
      limit,
      offset,
      owner: 'keyur',
    })
  })
  vi.stubGlobal('fetch', fetchSpy)
  return { calls, fetchSpy }
}

const offsets = (calls: string[]) =>
  calls.map((u) => Number(new URL(u, 'http://x').searchParams.get('offset')))

afterEach(() => { vi.unstubAllGlobals() })

describe('collecting every task', () => {
  it('pages until the server is exhausted', async () => {
    const { calls } = honestServer(1201)
    const got = await api.allTasks()
    expect(got.tasks).toHaveLength(1201)
    expect(got.total).toBe(1201)
    expect(got.complete).toBe(true)
    expect(offsets(calls)).toEqual([0, 500, 1000])
  })

  it('sends offset=0 on the FIRST request', async () => {
    // 🪤 The guard was `if (opts.offset)`, which drops a falsy 0 — harmless for the first page by
    // accident, and the reason `!= null` is used instead: the same truthiness bug on `limit`
    // would silently restore the 50-row default.
    const { calls } = honestServer(10)
    await api.allTasks()
    expect(calls[0]).toContain('offset=0')
    expect(calls[0]).toContain(`limit=${PAGE}`)
  })

  it('costs ONE request on an ordinary small install', async () => {
    // Vacuity guard: completeness must not make the common case chatty.
    const { calls } = honestServer(26)
    const got = await api.allTasks()
    expect(got.tasks).toHaveLength(26)
    expect(calls).toHaveLength(1)
  })

  it('settles an exactly-full page from the total instead of probing past it', async () => {
    // A full page cannot be told from "there is more" by its length alone — which is why the
    // SERVER-side loop has to ask again. The client is handed `total` in the same response, so it
    // stops here without a wasted round trip. Asserted because it is a boundary either way:
    // reading it as "more to come" would cost a request, reading a partial page as full would
    // truncate.
    const { calls } = honestServer(500)
    const got = await api.allTasks()
    expect(got.tasks).toHaveLength(500)
    expect(got.complete).toBe(true)
    expect(offsets(calls)).toEqual([0])
  })

  it('carries the filters on EVERY page, not just the first', async () => {
    const { calls } = honestServer(1100)
    await api.allTasks({ task_list: 'tl-7', status: 'open' })
    expect(calls).toHaveLength(3)
    for (const url of calls) {
      expect(url).toContain('task_list=tl-7')
      expect(url).toContain('status=open')
    }
  })

  it('returns the owner the rows came with', async () => {
    honestServer(3)
    expect((await api.allTasks()).owner).toBe('keyur')
  })
})

describe('a collection that could not be completed says so', () => {
  it('passes on the gateway\'s own truncation rather than reading it as exhaustion', async () => {
    // The server collected 600 of 5,000 (its per-provider bound) and said `complete: false`.
    // Without the flag, `tasks.length >= total` is false and the walk would keep asking for
    // pages the server will never serve.
    const fetchSpy = vi.fn(async () =>
      body({ tasks: rows(600), total: 5000, complete: false, limit: PAGE, offset: 0 })
    )
    vi.stubGlobal('fetch', fetchSpy)
    const got = await api.allTasks()
    expect(got.complete).toBe(false)
    expect(got.total).toBe(5000)
    expect(got.tasks).toHaveLength(600)
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })

  it('terminates against a server whose total exceeds what it serves', async () => {
    // `total: 900` with an empty second page. A walk keyed only on `length >= total` would spin
    // to its page bound; the empty page ends it — and honestly, because 300 !== 900.
    let call = 0
    vi.stubGlobal('fetch', vi.fn(async () => {
      call += 1
      return call === 1
        ? body({ tasks: rows(PAGE), total: 900, complete: true, limit: PAGE, offset: 0 })
        : body({ tasks: [], total: 900, complete: true, limit: PAGE, offset: PAGE })
    }))
    const got = await api.allTasks()
    expect(got.tasks).toHaveLength(PAGE)
    expect(call).toBe(2)
  })

  it('stops at its own page bound and does NOT claim completeness', async () => {
    // 🔑 The bound has to be reported for the same reason the server's is: a client that walked
    // 10,000 rows and gave up must not hand a window to a DAG as though it were the graph.
    const { calls } = honestServer(20_000)
    const got = await api.allTasks()
    expect(got.complete).toBe(false)
    expect(got.tasks).toHaveLength(10_000)
    expect(got.total).toBe(20_000)
    expect(calls).toHaveLength(20)
  })

  it('a genuinely empty install is complete, not partial', async () => {
    // The opposite failure: an empty list must not disclose a truncation.
    honestServer(0)
    const got = await api.allTasks()
    expect(got.tasks).toEqual([])
    expect(got.complete).toBe(true)
  })
})

describe('the single-window read is still available for previews', () => {
  it('sends only the params it was given', async () => {
    const { calls } = honestServer(80)
    const page = await api.tasks({ status: 'open', limit: 20 })
    expect(page.tasks).toHaveLength(20)
    expect(page.total).toBe(80)
    expect(calls).toHaveLength(1)
    expect(calls[0]).toContain('limit=20')
    expect(calls[0]).not.toContain('offset=')
  })

  it('reports the whole-set total, so a preview can state what it is not showing', async () => {
    honestServer(300)
    expect((await api.tasks({ limit: 20 })).total).toBe(300)
  })
})
