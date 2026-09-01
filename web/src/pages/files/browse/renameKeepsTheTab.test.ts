/** Renaming an open file must not strand its tab on a path that no longer exists.
 *
 * Issue 654, reproduced through the UI: open a file, Edit, type, do not save. Rename it from the
 * explorer. A dialog asks "Discard unsaved changes to <old name>?" — **after** the rename is
 * already on disk. Click Cancel and the tab is still open on the old name with the edits intact,
 * while the tree and the filesystem carry only the new one. The next Save is a 404 with an
 * unhandled `ApiError` and no toast: `toasts shown to the user: []`.
 *
 * The cause was ordering. `onRename` called `api.fileMove` and then `fileTabs.close()`, and
 * `close()` is what raises the discard prompt — so the prompt was downstream of the irreversible
 * step and its Cancel branch cancelled only the tab close.
 *
 * These assertions are on `useFileTabs` because the re-point is the piece that did not exist. The
 * ordering half lives in `FilesSection.onRename`, which now asks first and returns without calling
 * `fileMove` at all when the answer is no.
 */

import { describe, expect, it } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useFileTabs } from './useFileTabs'

const entry = (path: string) => ({ name: path.split('/').pop() || path, path, is_dir: false })

/** A fresh scope per test — the hook persists to localStorage, so a shared key would let one
 *  test's tabs restore into the next and make an assertion about "the open tabs" meaningless. */
let seq = 0
const freshScope = () => `rename-test-${++seq}`

describe('renamePath', () => {
  it('re-points the tab, its name, and the active path', () => {
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => result.current.open(entry('notes/q3-final.md')))
    expect(result.current.tabs.map((t) => t.path)).toEqual(['notes/q3-final.md'])

    act(() => result.current.renamePath('notes/q3-final.md', 'notes/q3-v2.md'))

    expect(result.current.tabs.map((t) => t.path)).toEqual(['notes/q3-v2.md'])
    // The name is re-derived, not carried: a tab strip still reading "q3-final.md" would be a
    // second copy of the same lie the stranded tab told.
    expect(result.current.tabs[0].name).toBe('q3-v2.md')
    expect(result.current.activePath).toBe('notes/q3-v2.md')
    expect(result.current.active?.path).toBe('notes/q3-v2.md')
  })

  it('carries the dirty flag to the new path rather than leaving it on the old', () => {
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => result.current.open(entry('a.md')))
    act(() => result.current.markDirty('a.md', true))

    act(() => result.current.renamePath('a.md', 'b.md'))

    expect(result.current.dirty['b.md']).toBe(true)
    expect(result.current.dirty['a.md']).toBeUndefined()
  })

  it('moves every tab under a renamed DIRECTORY', () => {
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => {
      result.current.open(entry('notes/a.md'))
      result.current.open(entry('notes/deep/b.md'))
      result.current.open(entry('other/c.md'))
    })

    act(() => result.current.renamePath('notes', 'journal'))

    expect(result.current.tabs.map((t) => t.path).sort()).toEqual([
      'journal/a.md', 'journal/deep/b.md', 'other/c.md',
    ])
  })

  it('does not claim a SIBLING whose name merely starts the same way', () => {
    // 🪤 `startsWith(from)` without the separator would rewrite `notes-archive/` when `notes` is
    // renamed, moving tabs that point at a directory nobody touched.
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => {
      result.current.open(entry('notes/a.md'))
      result.current.open(entry('notes-archive/b.md'))
    })

    act(() => result.current.renamePath('notes', 'journal'))

    expect(result.current.tabs.map((t) => t.path).sort()).toEqual([
      'journal/a.md', 'notes-archive/b.md',
    ])
  })

  it('leaves everything alone when nothing matches', () => {
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => result.current.open(entry('a.md')))
    const before = result.current.tabs

    act(() => result.current.renamePath('somewhere/else.md', 'other.md'))

    // Identity, not just equality: a re-point that rebuilt the array on every unrelated rename
    // would remount every viewer, since the Files page keys them by path.
    expect(result.current.tabs).toBe(before)
  })

  it('ignores a no-op or an empty path', () => {
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => result.current.open(entry('a.md')))

    act(() => result.current.renamePath('a.md', 'a.md'))
    act(() => result.current.renamePath('', 'b.md'))
    act(() => result.current.renamePath('a.md', ''))

    expect(result.current.tabs.map((t) => t.path)).toEqual(['a.md'])
    expect(result.current.activePath).toBe('a.md')
  })
})

describe('tabsUnder', () => {
  it('reports the tabs a rename or delete of a path would affect', () => {
    // This is what lets the caller ask BEFORE the irreversible step, and name the files in the
    // prompt. A directory rename can carry several, so it returns a list rather than a boolean.
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => {
      result.current.open(entry('notes/a.md'))
      result.current.open(entry('notes/deep/b.md'))
      result.current.open(entry('notes-archive/c.md'))
      result.current.open(entry('other.md'))
    })

    expect(result.current.tabsUnder('notes').map((t) => t.path).sort()).toEqual([
      'notes/a.md', 'notes/deep/b.md',
    ])
    expect(result.current.tabsUnder('other.md').map((t) => t.path)).toEqual(['other.md'])
    expect(result.current.tabsUnder('nothing/here').map((t) => t.path)).toEqual([])
  })
})
