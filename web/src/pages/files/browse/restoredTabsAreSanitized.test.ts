/** Issue 515, second defect: ONE malformed persisted tab bricked the Files page unrecoverably.
 *
 * `useFileTabs` restored its tab list with `Array.isArray(v) ? v : []` inside a `try/catch`. That
 * guards JSON *syntax* and nothing else, so any structurally-valid shape got through — and a tab
 * without a string `path` reached `baseName(path)` → `TypeError: Cannot read properties of
 * undefined (reading 'lastIndexOf')`, which the error boundary answered by replacing the page.
 * Measured in the report, every one of these crashed the page: `[{path}]` (no name), `[{name}]`
 * (no path), `['a.md']`, `[null]`, `[[…]]`.
 *
 * 🔑 WHAT MADE IT UNRECOVERABLE, and why this is worth a rail rather than a shrug: the bad value is
 * PERSISTED. The boundary's Retry re-read the same key (3 clicks, still crashed), and so did
 * navigating away and back. The only escape was devtools — which is not a recovery path a user has.
 *
 * So the persisted list is untrusted input on two counts: it outlives releases (a shape change
 * ships a poisoned key to everyone who already has one) and any script on the origin can write it.
 *
 * These tests execute the CRASH SITE (`baseName(t.path)`) against the restored list, not just the
 * shape of it — a sanitizer that returned `{path: undefined}` typed as a string would satisfy a
 * shape assertion and still take the page down.
 */

import { describe, expect, it, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useFileTabs } from './useFileTabs'
import { baseName } from '../fileMeta'

let seq = 0
/** A fresh scope per test: the hook persists to localStorage, so a shared key leaks one test's
 *  tabs into the next and makes any assertion about "the restored tabs" meaningless. */
const freshScope = () => `sanitize-test-${++seq}`

/** Seed the persisted list exactly as a previous session (or anything else) would have. */
function persist(scope: string, value: unknown, active?: string): void {
  localStorage.setItem(`files-open-tabs:${scope}`, typeof value === 'string' ? value : JSON.stringify(value))
  if (active !== undefined) localStorage.setItem(`files-open-tabs:${scope}-active`, active)
}

beforeEach(() => localStorage.clear())

describe('restoring a malformed persisted tab list', () => {
  const cases: [string, unknown][] = [
    ['an entry with no path', [{ name: 'a.md' }]],
    ['an array of strings', ['a.md']],
    ['an array of nulls', [null]],
    ['a nested array', [['a.md']]],
    ['a numeric path', [{ path: 42, name: 'a.md' }]],
    ['an empty-string path', [{ path: '', name: 'a.md' }]],
    ['an object instead of an array', { path: 'a.md' }],
    ['unparseable JSON', '{not json'],
  ]

  for (const [label, value] of cases) {
    it(`drops ${label} instead of crashing the page`, () => {
      const scope = freshScope()
      persist(scope, value)
      const { result } = renderHook(() => useFileTabs(scope))

      // The crash site itself, run over whatever survived restore.
      expect(() => result.current.tabs.map((t) => baseName(t.path))).not.toThrow()
      expect(result.current.tabs).toEqual([])
      expect(result.current.active).toBeNull()
    })
  }

  it('keeps the GOOD tabs when only some entries are bad', () => {
    // The page should cost the user the unreadable tabs, not the session.
    const scope = freshScope()
    persist(scope, [{ path: '/ws/a.md', name: 'a.md' }, null, { name: 'orphan' }, { path: '/ws/b.md', name: 'b.md' }])
    const { result } = renderHook(() => useFileTabs(scope))

    expect(result.current.tabs.map((t) => t.path)).toEqual(['/ws/a.md', '/ws/b.md'])
  })

  it('re-derives a missing name from the path rather than dropping the tab', () => {
    // A name is recoverable — it is what `open()` computes when an entry has none — so a tab
    // that only lost its label is repaired, not discarded.
    const scope = freshScope()
    persist(scope, [{ path: '/ws/deep/notes.md' }, { path: '/ws/blank.md', name: '' }])
    const { result } = renderHook(() => useFileTabs(scope))

    expect(result.current.tabs).toEqual([
      { path: '/ws/deep/notes.md', name: 'notes.md' },
      { path: '/ws/blank.md', name: 'blank.md' },
    ])
  })

  it('collapses a duplicated path — two tabs would share one React key and close as one', () => {
    const scope = freshScope()
    persist(scope, [{ path: '/ws/a.md', name: 'a.md' }, { path: '/ws/a.md', name: 'a.md' }])
    const { result } = renderHook(() => useFileTabs(scope))

    expect(result.current.tabs).toHaveLength(1)
  })

  it('bounds a persisted list at the same cap `open()` enforces, keeping the most recent', () => {
    // Restoring is the OTHER way into this state; without the bound, a list written before the cap
    // existed (or by anything else) reopens 500 tabs and 500 editors.
    const scope = freshScope()
    persist(scope, Array.from({ length: 30 }, (_, i) => ({ path: `/ws/f${i}.md`, name: `f${i}.md` })))
    const { result } = renderHook(() => useFileTabs(scope))

    expect(result.current.tabs).toHaveLength(12)
    expect(result.current.tabs[11].path).toBe('/ws/f29.md')
  })
})

describe('the restored active path', () => {
  it('falls back to a real tab when the saved one is not in the list', () => {
    // The two keys are written independently, so the active key can name a tab that was dropped as
    // malformed. `active` would then be null with tabs open — and a host renders its "no file open"
    // state over a populated tab strip.
    const scope = freshScope()
    persist(scope, [{ path: '/ws/a.md', name: 'a.md' }, { path: '/ws/b.md', name: 'b.md' }], '/ws/gone.md')
    const { result } = renderHook(() => useFileTabs(scope))

    expect(result.current.activePath).toBe('/ws/b.md')
    expect(result.current.active?.path).toBe('/ws/b.md')
  })

  it('honours a saved active path that IS in the list', () => {
    const scope = freshScope()
    persist(scope, [{ path: '/ws/a.md', name: 'a.md' }, { path: '/ws/b.md', name: 'b.md' }], '/ws/a.md')
    const { result } = renderHook(() => useFileTabs(scope))

    expect(result.current.activePath).toBe('/ws/a.md')
  })

  it('stays empty when there are no tabs to be active in', () => {
    const scope = freshScope()
    persist(scope, [], '/ws/gone.md')
    const { result } = renderHook(() => useFileTabs(scope))

    expect(result.current.activePath).toBe('')
  })
})
