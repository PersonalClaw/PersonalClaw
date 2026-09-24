import { describe, expect, it, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { useFileTabs } from './useFileTabs'

// ── Issue 2279: the Files page keeps your work across a rename, and a discard is final ──
//
// FileViewer documents a host-owned draft cache; the Code cockpit passed one and the
// Files page did not — so a rename (which re-points the tab to a NEW path = a new key)
// remounted the viewer and silently re-read disk over an unsaved edit. Adding the store
// alone would trade a lost edit for a RESURRECTED one (a close-discarded draft reappears
// on reopen), so the lifecycle is pinned here too, matching the cockpit: a confirmed
// close purges; a rename moves the entry.
//
// The hook half is behavioral; the host halves are source-level pins (the wiring is
// literal strings in two large page components, and a label/prop is what carries it).

vi.mock('../../../ui/dialog', () => ({ confirm: vi.fn(() => Promise.resolve(true)) }))
import { confirm } from '../../../ui/dialog'

const entry = (path: string) => ({ name: path.split('/').pop() || path, path, is_dir: false })
let seq = 0
const freshScope = () => `draft-test-${++seq}`

describe('useFileTabs.close reports consent so hosts can purge exactly then', () => {
  beforeEach(() => vi.mocked(confirm).mockClear())

  it('returns true for a clean tab (closed, no prompt)', async () => {
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => result.current.open(entry('a.md')))
    let closed = false
    await act(async () => { closed = await result.current.close('a.md') })
    expect(closed).toBe(true)
    expect(confirm).not.toHaveBeenCalled()
  })

  it('returns true when the user confirms the discard', async () => {
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => { result.current.open(entry('a.md')); result.current.markDirty('a.md', true) })
    let closed = false
    await act(async () => { closed = await result.current.close('a.md') })
    expect(closed).toBe(true)
  })

  it('returns false when the user cancels — the draft must NOT be purged then', async () => {
    vi.mocked(confirm).mockResolvedValueOnce(false)
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => { result.current.open(entry('a.md')); result.current.markDirty('a.md', true) })
    let closed = true
    await act(async () => { closed = await result.current.close('a.md') })
    expect(closed).toBe(false)
    expect(result.current.tabs.map((t) => t.path)).toEqual(['a.md'])
  })
})

describe('the two hosts wire the cache and its lifecycle (source-level)', () => {
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
  const files = strip(readFileSync(resolve(__dirname, '../FilesSection.tsx'), 'utf8'))
  const cockpit = strip(readFileSync(resolve(__dirname, '../../code/CodeCockpitPage.tsx'), 'utf8'))

  it('FilesSection owns a store, passes it to FileViewer, and purges on a consented close', () => {
    expect(files).toMatch(/const draftStore = useRef\(new Map<string, \{ draft: string; base: string; warned\?: boolean \}>\(\)\)\.current/)
    expect(files).toContain('draftStore={draftStore}')
    expect(files).toMatch(/if \(await fileTabs\.close\(path\)\) draftStore\.delete\(path\)/)
    // The missing-file close purges too — a deleted file's draft must not haunt the path.
    expect(files).toMatch(/onMissing=\{\(p\) => \{ draftStore\.delete\(p\); fileTabs\.closeNow\(p\) \}\}/)
  })

  it('FilesSection.onRename moves the draft with the file instead of asking to discard', () => {
    expect(files).toMatch(/draftStore\.delete\(key\); draftStore\.set\(next, val\)/)
    expect(files).not.toContain('Rename and discard unsaved changes?')
  })

  it("the cockpit's programmatic closes purge the draft (they did not — the same resurrect bug)", () => {
    expect(cockpit).toMatch(/if \(!underWs\(t\.path\)\) \{ draftStore\.delete\(t\.path\); closeNow\(t\.path\) \}/)
    expect(cockpit).toMatch(/lastContentRef\.current\.delete\(d\.path\); draftStore\.delete\(d\.path\); closeNow\(d\.path\)/)
  })
})
