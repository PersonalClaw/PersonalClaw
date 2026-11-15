// A test kit for the library's page and parts: rows shaped as `GET /api/library` answers them, and a
// gateway whose reads a case answers by method (every other read stays on its way). Test-only: it is
// imported by `*.test.tsx` files beside it, never by the app.
import { vi } from 'vitest'
import type { LibraryItem, LibraryResponse } from '../lib/api'

/** A library row, as `GET /api/library` answers one: the kind and store read from the ref, the rest empty. */
export function row(ref: string, title: string, extra: Partial<LibraryItem> = {}): LibraryItem {
  const kind = ref.split(':')[0]
  return {
    ref, title,
    kind: kind === 'artifact' ? 'page' : kind === 'report' ? 'report' : kind === 'findings' ? 'findings' : 'note',
    format: kind === 'artifact' ? 'html' : kind === 'report' ? 'report' : kind === 'findings' ? 'findings' : 'note',
    store: kind === 'artifact' ? 'artifacts' : kind === 'findings' ? 'intents' : kind === 'report' ? 'reports' : 'knowledge',
    made: 'you', made_label: 'You', from: null, contributor: '', project: null, collections: [], tags: [],
    created_at: '2026-11-10T08:00:00Z', activity_at: '2026-11-10T08:00:00Z', read: null, pinned: false, archived: false,
    running: false, conflicts: [], missing_tags: [], state: '', excerpt: '', size: null, version: null, thumb: null, match: null,
    ...extra,
  }
}

/** A whole read: every row the filter keeps. */
export function answer(items: LibraryItem[], extra: Partial<LibraryResponse> = {}): LibraryResponse {
  return { items, total: items.length, complete: true, failed: [], facets: {}, sort: 'activity', ...extra }
}

/** The gateway's answers, by method: a value resolves, an `Error` rejects, a function is called with
 *  the call's arguments. Reset before each case. */
export const answers: Record<string, unknown> = {}
/** Every call made, by method, with its arguments. */
export const calls: Record<string, unknown[][]> = {}

export function resetGateway() {
  for (const k of Object.keys(answers)) delete answers[k]
  for (const k of Object.keys(calls)) delete calls[k]
}

/** The `api` the kit's `vi.mock` hands the app: each method answers from `answers`, or never. */
export function gatewayApi() {
  return new Proxy({}, {
    get: (_t, method: string) => (...args: unknown[]) => {
      ;(calls[method] ??= []).push(args)
      if (!(method in answers)) return new Promise(() => {})
      const a = answers[method]
      if (a instanceof Error) return Promise.reject(a)
      if (typeof a === 'function') return Promise.resolve((a as (...x: unknown[]) => unknown)(...args))
      return Promise.resolve(a)
    },
  })
}

/** A route's props, with the address held here, so a case reads what the page wrote. */
export function route(initial: Record<string, string> = {}) {
  const state = { query: { ...initial } as Record<string, string>, pushed: [] as Record<string, string | null | undefined>[], went: [] as string[] }
  const setQuery = vi.fn((patch: Record<string, string | null | undefined>, _opts?: { replace?: boolean; remount?: boolean }) => {
    state.pushed.push(patch)
    const next = { ...state.query }
    for (const [k, v] of Object.entries(patch)) { if (v == null || v === '') delete next[k]; else next[k] = v }
    state.query = next
  })
  const navigate = vi.fn((path: string) => { state.went.push(path) })
  return { state, setQuery, navigate }
}

/** A phone's width (768 px and below) or a desktop's, for `useIsMobile` and `useNarrowerThan`. */
export function atWidth(px: number) {
  window.matchMedia = ((query: string) => {
    const max = /max-width:\s*(\d+)px/.exec(query)
    const min = /min-width:\s*(\d+)px/.exec(query)
    const matches = (!max || px <= Number(max[1])) && (!min || px >= Number(min[1])) && (!!max || !!min)
    return {
      matches, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    } as MediaQueryList
  }) as typeof window.matchMedia
}
