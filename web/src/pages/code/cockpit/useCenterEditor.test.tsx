import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor } from '@testing-library/react'

// ── The cockpit editor's controller ───────────────────────────────────────────────────────────
//
// `useCenterEditor` keeps what the center of the code cockpit shows: the workspace's file tabs, a
// dirty tab closed only once asked, the re-open chips for a collapsed side panel, the terminal
// drawer's latch, and a diff, a commit, or a file the worker is writing in place of the editor.

const h = vi.hoisted(() => ({ fileRead: vi.fn(), confirm: vi.fn() }))
vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  return { ...real, api: { ...real.api, fileRead: h.fileRead } }
})
vi.mock('../../../ui/dialog', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../ui/dialog')>()
  return { ...real, confirm: h.confirm }
})

let useCenterEditor: typeof import('./useCenterEditor')['useCenterEditor']
const WS = '/repo/feed'
const fire = (name: string, detail: unknown) => act(() => { window.dispatchEvent(new CustomEvent(name, { detail })) })
const file = (name: string, extra: Record<string, unknown> = {}) => ({ name, path: `${WS}/${name}`, is_dir: false, ...extra })
const mount = (props: { showTerm?: boolean; onCloseTerm?: () => void } = {}) => renderHook(
  ({ showTerm, onCloseTerm }) => useCenterEditor({ ws: WS, showTerm, onCloseTerm }),
  { initialProps: { showTerm: props.showTerm ?? false, onCloseTerm: props.onCloseTerm ?? (() => {}) } },
)

beforeEach(async () => {
  localStorage.clear()
  for (const f of Object.values(h)) f.mockReset()
  h.fileRead.mockResolvedValue({ content: 'export {}\n' })
  ;({ useCenterEditor } = await import('./useCenterEditor'))
})
afterEach(() => cleanup())

describe('useCenterEditor', () => {
  it('opens a file as a tab, and a diff or a commit takes the editor until a file is opened', async () => {
    const { result } = mount()
    fire('ne:code-open-file', file('app.ts'))
    expect(result.current.active?.path).toBe(`${WS}/app.ts`)
    fire('ne:code-open-diff', { path: `${WS}/app.ts`, name: 'app.ts' })
    expect(result.current.diff).toEqual({ path: `${WS}/app.ts`, name: 'app.ts' })
    fire('ne:code-open-commit', { hash: 'abc123', subject: 'Add the feed' })
    expect(result.current.commit).toEqual({ hash: 'abc123', subject: 'Add the feed' })
    expect(result.current.diff).toBeNull()
    fire('ne:code-open-file', file('app.ts'))
    expect(result.current.commit).toBeNull()
    expect(result.current.tabs.map((t) => t.path)).toEqual([`${WS}/app.ts`])
  })

  it('plays a file the worker writes as it lands, and an edit of it as a diff', async () => {
    const { result } = mount()
    h.fileRead.mockResolvedValueOnce({ content: 'one\n' })
    fire('ne:code-open-file', file('feed.ts', { _follow: true }))
    await waitFor(() => expect(result.current.reveal).toEqual({ path: `${WS}/feed.ts`, name: 'feed.ts', kind: 'write', text: 'one\n' }))
    h.fileRead.mockResolvedValueOnce({ content: 'one\ntwo\n' })
    fire('ne:code-open-file', file('feed.ts', { _follow: true }))
    await waitFor(() => expect(result.current.reveal).toEqual({ path: `${WS}/feed.ts`, name: 'feed.ts', kind: 'diff', oldText: 'one\n', newText: 'one\ntwo\n' }))
  })

  it('opens a large file the worker writes straight in the editor', async () => {
    const { result } = mount()
    h.fileRead.mockResolvedValueOnce({ content: 'x'.repeat(40_001) })
    fire('ne:code-open-file', file('bundle.js', { _follow: true }))
    await waitFor(() => expect(result.current.active?.path).toBe(`${WS}/bundle.js`))
    expect(result.current.reveal).toBeNull()
  })

  it('asks before closing a tab with unsaved edits, and keeps it when declined', async () => {
    const { result } = mount()
    fire('ne:code-open-file', file('app.ts'))
    act(() => result.current.markDirty(`${WS}/app.ts`, true))
    h.confirm.mockResolvedValueOnce(false)
    await act(() => result.current.requestClose(`${WS}/app.ts`, 'app.ts'))
    expect(h.confirm).toHaveBeenCalledTimes(1)
    expect(result.current.tabs).toHaveLength(1)
    h.confirm.mockResolvedValueOnce(true)
    await act(() => result.current.requestClose(`${WS}/app.ts`, 'app.ts'))
    expect(result.current.tabs).toHaveLength(0)
  })

  it('follows a renamed file to its new path', async () => {
    const { result } = mount()
    fire('ne:code-open-file', file('app.ts'))
    fire('ne:code-renamed', { from: `${WS}/app.ts`, to: `${WS}/main.ts` })
    expect(result.current.tabs.map((t) => t.path)).toEqual([`${WS}/main.ts`])
    fire('ne:code-close-file', { path: `${WS}/main.ts` })
    expect(result.current.tabs).toHaveLength(0)
  })

  it('shows a re-open chip for a side panel that starts or becomes collapsed', () => {
    localStorage.setItem('code-left-collapsed', '1')
    const { result } = mount()
    expect(result.current.collapsedLeft).toEqual([['code-left', { side: 'left', label: 'Files' }]])
    fire('ne:code-panel-collapsed', { panelKey: 'code-right', side: 'right', label: 'Tasks', collapsed: true })
    expect(result.current.collapsedRight).toEqual([['code-right', { side: 'right', label: 'Tasks' }]])
    const expand = vi.fn()
    window.addEventListener('ne:code-expand-panel', expand)
    act(() => result.current.reopenPanel('code-right'))
    window.removeEventListener('ne:code-expand-panel', expand)
    expect((expand.mock.calls[0][0] as CustomEvent).detail).toBe('code-right')
  })

  it('keeps the terminal mounted once opened, until its own close', () => {
    const onCloseTerm = vi.fn()
    const { result, rerender } = mount({ onCloseTerm })
    expect(result.current.termOpened).toBe(false)
    rerender({ showTerm: true, onCloseTerm })
    expect(result.current.termOpened).toBe(true)
    rerender({ showTerm: false, onCloseTerm })
    expect(result.current.termOpened).toBe(true)
    act(() => result.current.closeTerm())
    expect(result.current.termOpened).toBe(false)
    expect(onCloseTerm).toHaveBeenCalledTimes(1)
  })
})
