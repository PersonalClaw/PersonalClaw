import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { StudioDocEditor } from './MemoryPanel'

/** An unsaved memory-doc edit is not thrown away (issue 525).
 *
 *  The three markdown memory docs are hand-curated files injected into every agent prompt.
 *  Measured live: edit Preferences (4206 chars), see "Unsaved changes" appear, click Projects in
 *  the Studio list, come back — 4206 chars again, the edit gone. No confirm, no `beforeunload`,
 *  no retained draft. `dirty` was computed AND rendered and gated nothing but the Save button.
 *
 *  Retention rather than a confirm: the Studio unmounts this editor on every selection change, so
 *  a host-owned cache makes the switch lossless and leaves nothing to ask about. `useUnsavedGuard`
 *  covers the one exit a re-render cannot save (closing the tab / reloading).
 */

const DOC = '# User Preferences\nkeep me\n'
const memoryDoc = vi.fn()
const saveMemoryDoc = vi.fn()

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return { ...actual, api: { ...actual.api, memoryDoc: (w: string) => memoryDoc(w), saveMemoryDoc: (w: string, c: string) => saveMemoryDoc(w, c) } }
})

beforeEach(() => {
  memoryDoc.mockReset().mockResolvedValue(DOC)
  saveMemoryDoc.mockReset().mockResolvedValue(undefined)
})
afterEach(() => vi.restoreAllMocks())

function mount(drafts: Map<string, string>, which: 'preferences' | 'projects' | 'history' = 'preferences') {
  return render(<StudioDocEditor which={which} onSaved={() => {}} drafts={drafts} />)
}

const box = () => screen.getByRole('textbox')
/** The textarea's value. `toHaveValue` does NOT accept an asymmetric matcher — it silently
 *  fails on the positive form and silently PASSES on the negated one, so substring
 *  assertions read the string themselves. */
const text = () => (box() as HTMLTextAreaElement).value

describe('switching Studio items keeps the edit', () => {
  it('restores the draft after the editor is unmounted and remounted', async () => {
    // The measured repro: type, leave (unmount), come back.
    const drafts = new Map<string, string>()
    const first = mount(drafts)
    await waitFor(() => expect(box()).toHaveValue(DOC))
    await userEvent.type(box(), 'zz75 ABANDONED EDIT')
    expect(screen.getByText('Unsaved changes')).toBeTruthy()
    first.unmount()

    const second = mount(drafts)
    await waitFor(() => expect(text()).toContain('zz75 ABANDONED EDIT'))
    expect(screen.getByText('Unsaved changes'), 'and it still reads as unsaved').toBeTruthy()
    second.unmount()
  })

  it('keeps each doc its own draft', async () => {
    // Three docs share one cache; a Preferences edit must not appear under Projects.
    const drafts = new Map<string, string>()
    const first = mount(drafts, 'preferences')
    await waitFor(() => expect(box()).toHaveValue(DOC))
    await userEvent.type(box(), 'PREFS ONLY')
    first.unmount()

    const second = mount(drafts, 'projects')
    await waitFor(() => expect(box()).toHaveValue(DOC))
    expect(text()).not.toContain('PREFS ONLY')
    second.unmount()
  })

  it('re-reads the file on every mount, so a restored draft is measured against CURRENT content', async () => {
    // Only the draft is cached. If the baseline were cached too, a Save would silently overwrite a
    // change made elsewhere while this doc was off screen.
    const drafts = new Map<string, string>()
    const first = mount(drafts)
    await waitFor(() => expect(box()).toHaveValue(DOC))
    await userEvent.type(box(), 'MINE')
    first.unmount()

    memoryDoc.mockResolvedValue('# rewritten elsewhere\n')
    const second = mount(drafts)
    await waitFor(() => expect(text()).toContain('MINE'))
    expect(memoryDoc).toHaveBeenCalledTimes(2)
    expect(screen.getByText('Unsaved changes')).toBeTruthy()
    second.unmount()
  })

  it('holds nothing once the text matches the file again', async () => {
    // Typing back to the original is not an unsaved edit, so the cache must not claim one.
    const drafts = new Map<string, string>()
    const view = mount(drafts)
    await waitFor(() => expect(box()).toHaveValue(DOC))
    await userEvent.type(box(), 'x')
    expect(drafts.has('preferences')).toBe(true)
    await userEvent.type(box(), '{backspace}')
    await waitFor(() => expect(drafts.has('preferences')).toBe(false))
    expect(screen.queryByText('Unsaved changes')).toBeNull()
    view.unmount()
  })

  it('drops the cached draft once it is saved', async () => {
    const drafts = new Map<string, string>()
    const view = mount(drafts)
    await waitFor(() => expect(box()).toHaveValue(DOC))
    await userEvent.type(box(), 'SAVE ME')
    await userEvent.click(screen.getByRole('button', { name: /save/i }))
    await waitFor(() => expect(drafts.has('preferences')).toBe(false))
    expect(saveMemoryDoc).toHaveBeenCalledWith('preferences', expect.stringContaining('SAVE ME'))
    view.unmount()
  })

  it('KEEPS the cached draft when the save is refused', async () => {
    // A failed save must not become the same data loss by another route.
    saveMemoryDoc.mockRejectedValue(new Error('nope'))
    const drafts = new Map<string, string>()
    const view = mount(drafts)
    await waitFor(() => expect(box()).toHaveValue(DOC))
    await userEvent.type(box(), 'KEEP ME')
    await userEvent.click(screen.getByRole('button', { name: /save/i }))
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    expect(drafts.get('preferences')).toContain('KEEP ME')
    view.unmount()
  })
})

describe('the editor names itself', () => {
  it('gives the textarea the doc name a screen reader can hear', async () => {
    // Measured `aria-label: null` — the announcement was "edit text, multi-line" on the control
    // that rewrites what every agent prompt carries.
    const view = mount(new Map())
    await waitFor(() => expect(box()).toHaveValue(DOC))
    expect(screen.getByRole('textbox', { name: 'Preferences memory' })).toBeTruthy()
    view.unmount()
  })
})

// ── the wiring, where the behaviour above cannot reach ─────────────────────────────────────

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('the host owns the cache and the guard has one implementation', () => {
  it('MemoryStudio holds the cache, because the editor is what unmounts', () => {
    const code = read('pages/settings/MemoryPanel.tsx')
    expect(code).toMatch(/const docDrafts = useRef\(new Map<string, string>\(\)\)/)
    expect(code, 'and hands it down').toMatch(/docDrafts=\{docDrafts\.current\}/)
    expect(code, 'through the inspector to the editor').toMatch(/drafts=\{docDrafts\}/)
  })

  it('the editor arms the browser-exit guard on its own dirty state', () => {
    expect(read('pages/settings/MemoryPanel.tsx')).toMatch(/useUnsavedGuard\(dirty\)/)
  })

  it('the guard exists in exactly ONE place', () => {
    // It used to live inline in useFileTabs; a second copy here is what the extraction prevents.
    expect(read('lib/useUnsavedGuard.ts')).toMatch(/addEventListener\('beforeunload'/)
    expect(read('pages/files/browse/useFileTabs.ts'), 'the file tabs delegate now')
      .toMatch(/useUnsavedGuard\(Object\.values\(dirty\)\.some\(Boolean\)\)/)
    expect(read('pages/files/browse/useFileTabs.ts'), 'and hold no copy')
      .not.toMatch(/addEventListener\('beforeunload'/)
    expect(read('pages/settings/MemoryPanel.tsx'), 'nor does the memory panel')
      .not.toMatch(/addEventListener\('beforeunload'/)
  })
})
