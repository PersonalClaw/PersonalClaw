import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { StudioDocEditor, type DocDraft } from './MemoryPanel'

// ── A memory-doc save from a stale copy is refused, and the edit is kept ───────────────────────
//
// The three markdown memory docs are written by the gateway too: the history consolidator rewrites
// preferences and projects, and the agent's memory tool appends a preference. The editor's Save
// replaced the whole file with its copy, so a preference the agent learned while the editor was
// open — or while a restored draft sat in the Studio's cache — was erased without a word. The
// gateway now refuses a stale copy (`409 stale_write`); this pins what the editor does with that.

const DOC = '# User Preferences\n\n- likes short answers\n\n## Learned\n'
// What the agent's memory tool appended while the editor was open.
const LEARNED = DOC + '- prefers tea\n'
const memoryDoc = vi.fn()
const saveMemoryDoc = vi.fn()

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return { ...actual, api: { ...actual.api, memoryDoc: (w: string) => memoryDoc(w), saveMemoryDoc: (w: string, c: string, base: string) => saveMemoryDoc(w, c, base) } }
})

const stale = () => Object.assign(new Error('This write replaces the preferences memory, which changed…'), { status: 409, code: 'stale_write' })

beforeEach(() => {
  // First read: the copy the editor opens with. Every later read: the agent has learned something.
  memoryDoc.mockReset()
    .mockResolvedValueOnce({ value: DOC, revision: 'r1' })
    .mockResolvedValue({ value: LEARNED, revision: 'r2' })
  saveMemoryDoc.mockReset().mockImplementation((_w: string, c: string, base: string) =>
    base === 'r2' ? Promise.resolve({ value: c, revision: 'r3' }) : Promise.reject(stale()))
})
afterEach(() => vi.restoreAllMocks())

const box = () => screen.getByRole('textbox', { name: 'Preferences memory' }) as HTMLTextAreaElement

async function openAndType(drafts: Map<string, DocDraft>, text: string) {
  const view = render(<StudioDocEditor which="preferences" onSaved={() => {}} drafts={drafts} />)
  await waitFor(() => expect(box().value).toBe(DOC))
  fireEvent.change(box(), { target: { value: text } })
  return view
}

const saveButton = () => screen.getByRole('button', { name: /save/i })

describe('a memory-doc save from a stale copy', () => {
  it('is refused with the notice, and the draft stays in the editor and in the cache', async () => {
    const drafts = new Map<string, DocDraft>()
    await openAndType(drafts, DOC + '- my own line\n')
    await act(async () => { fireEvent.click(saveButton()) })
    const notice = await waitFor(() => {
      const el = document.querySelector<HTMLElement>('[data-stale-write="true"]')
      expect(el).not.toBeNull()
      return el!
    })
    expect(notice.getAttribute('role')).toBe('alert')
    expect(notice.textContent).toMatch(/Preferences memory changed elsewhere/)
    expect(saveMemoryDoc).toHaveBeenCalledWith('preferences', DOC + '- my own line\n', 'r1')
    expect(box().value).toBe(DOC + '- my own line\n')
    expect(drafts.get('preferences')?.text).toBe(DOC + '- my own line\n')
  })

  it('Reload and reapply keeps what the agent learned and adds the edit on top', async () => {
    await openAndType(new Map(), DOC.replace('short', 'SHORT'))
    await act(async () => { fireEvent.click(saveButton()) })
    const notice = await waitFor(() => document.querySelector<HTMLElement>('[data-stale-write="true"]')!)
    const reapply = within(notice).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })
    await waitFor(() => expect(saveMemoryDoc).toHaveBeenCalledTimes(2))
    expect(saveMemoryDoc.mock.calls[1]).toEqual(['preferences', LEARNED.replace('short', 'SHORT'), 'r2'])
    // Landed: the editor now holds what was stored, and it is no longer an unsaved change.
    await waitFor(() => expect(box().value).toBe(LEARNED.replace('short', 'SHORT')))
    expect(screen.queryByText('Unsaved changes')).toBeNull()
  })

  it('a draft restored after the doc changed elsewhere names the copy it was typed on', async () => {
    // Type, leave (the Studio unmounts the editor), come back after the agent learned something.
    // The restored draft was built on r1; saving it over the fresh read's r2 would erase "prefers
    // tea" — the overwrite this guards — so it must name r1 and be refused.
    const drafts = new Map<string, DocDraft>()
    const first = await openAndType(drafts, DOC + '- my own line\n')
    first.unmount()
    render(<StudioDocEditor which="preferences" onSaved={() => {}} drafts={drafts} />)
    await waitFor(() => expect(box().value).toBe(DOC + '- my own line\n'))
    await act(async () => { fireEvent.click(saveButton()) })
    await waitFor(() => expect(saveMemoryDoc).toHaveBeenCalledTimes(1))
    expect(saveMemoryDoc.mock.calls[0][2]).toBe('r1')
    await waitFor(() => expect(document.querySelector('[data-stale-write="true"]')).not.toBeNull())
  })

  it('an editor that stays open saves again over the revision its save answered', async () => {
    memoryDoc.mockReset().mockResolvedValue({ value: DOC, revision: 'r1' })
    saveMemoryDoc.mockReset()
      .mockResolvedValueOnce({ value: DOC + '- one\n', revision: 'r2' })
      .mockResolvedValue({ value: DOC + '- one\n- two\n', revision: 'r3' })
    await openAndType(new Map(), DOC + '- one\n')
    await act(async () => { fireEvent.click(saveButton()) })
    await waitFor(() => expect(saveMemoryDoc).toHaveBeenCalledTimes(1))
    fireEvent.change(box(), { target: { value: DOC + '- one\n- two\n' } })
    await act(async () => { fireEvent.click(saveButton()) })
    await waitFor(() => expect(saveMemoryDoc).toHaveBeenCalledTimes(2))
    expect(saveMemoryDoc.mock.calls.map((c) => c[2])).toEqual(['r1', 'r2'])
  })
})
