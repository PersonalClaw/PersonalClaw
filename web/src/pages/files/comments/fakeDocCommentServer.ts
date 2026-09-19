import type { WireDocComment } from '../../../lib/api'

/** An in-memory `/api/doc-comments` for the store's tests.
 *
 *  Stubs `fetch` rather than mocking the `api` module on purpose: the store's job is now
 *  partly the camelCase↔snake_case mapping and the `ts` seconds→milliseconds conversion,
 *  and a module mock would replace exactly the code that does it. This drives the real
 *  `api` client, so a mapping that drifts from the wire reds a test instead of shipping.
 *
 *  Not named `*.test.ts` so the runner does not collect it as a suite.
 */
export interface FakeDocCommentServer {
  /** The rows the fake is holding — assert against this to check what was PERSISTED,
   *  as opposed to what the optimistic local list is showing. */
  rows(): WireDocComment[]
  /** Make every subsequent call reject, to drive the write-failure paths. */
  breakWrites(message?: string): void
  restore(): void
}

interface Handled { status: number; body: unknown }

export function installFakeDocCommentServer(seed: Partial<WireDocComment>[] = []): FakeDocCommentServer {
  let seq = 0
  const row = (p: Partial<WireDocComment>): WireDocComment => ({
    id: p.id ?? `srv-${++seq}`,
    doc_id: p.doc_id ?? '',
    doc_label: p.doc_label ?? '',
    doc_path: p.doc_path ?? '',
    quote: p.quote ?? '',
    comment: p.comment ?? '',
    line: p.line ?? null,
    column: p.column ?? null,
    context: p.context ?? '',
    // Epoch SECONDS, as the server emits — the store multiplies by 1000.
    ts: p.ts ?? Math.floor(Date.now() / 1000) + seq,
  })
  let store: WireDocComment[] = seed.map(row)
  let broken: string | null = null

  const real = globalThis.fetch
  const route = (path: string, method: string, body: Record<string, unknown>): Handled => {
    const single = /^\/api\/doc-comments\/(.+)$/.exec(path)
    if (path === '/api/doc-comments' && method === 'GET') return { status: 200, body: { comments: store } }
    if (path === '/api/doc-comments' && method === 'POST') {
      const created = row(body as Partial<WireDocComment>)
      store = [...store, created]
      return { status: 201, body: { comment: created } }
    }
    if (path === '/api/doc-comments' && method === 'DELETE') {
      const removed = store.length
      store = []
      return { status: 200, body: { ok: true, removed } }
    }
    if (path === '/api/doc-comments/delete' && method === 'POST') {
      const ids = new Set((body.ids as string[]) ?? [])
      const kept = store.filter((r) => !ids.has(r.id))
      const removed = store.length - kept.length
      store = kept
      return { status: 200, body: { ok: true, removed } }
    }
    if (single && method === 'PATCH') {
      const target = store.find((r) => r.id === decodeURIComponent(single[1]))
      if (!target) return { status: 404, body: { error: { code: 'not_found', message: 'no comment with that id' } } }
      target.comment = String(body.comment ?? '')
      return { status: 200, body: { comment: target } }
    }
    if (single && method === 'DELETE') {
      const id = decodeURIComponent(single[1])
      const kept = store.filter((r) => r.id !== id)
      if (kept.length === store.length) {
        return { status: 404, body: { error: { code: 'not_found', message: 'no comment with that id' } } }
      }
      store = kept
      return { status: 200, body: { ok: true, removed: 1 } }
    }
    return { status: 404, body: { error: { code: 'not_found', message: `unrouted ${method} ${path}` } } }
  }

  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (!path.startsWith('/api/doc-comments')) {
      if (real) return real(input as RequestInfo, init)
      throw new Error(`unexpected fetch in test: ${path}`)
    }
    if (broken) throw new Error(broken)
    const method = (init?.method ?? 'GET').toUpperCase()
    let body: Record<string, unknown> = {}
    if (typeof init?.body === 'string') {
      try { body = JSON.parse(init.body) as Record<string, unknown> } catch { body = {} }
    }
    const { status, body: payload } = route(path, method, body)
    return new Response(JSON.stringify(payload), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof globalThis.fetch

  return {
    rows: () => store,
    breakWrites: (message = 'network down') => { broken = message },
    restore: () => { globalThis.fetch = real },
  }
}
