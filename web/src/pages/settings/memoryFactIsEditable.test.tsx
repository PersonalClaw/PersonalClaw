/**
 * A memory fact is edited where it is shown, under the key it already has.
 *
 * The Studio showed a fact's value read-only. Changing one meant "+ Fact": typing its key again from
 * memory and its whole value from scratch, and a key the add form's prefix check does not know could
 * not be re-added at all. Driven through the real panel, with the write the Studio makes.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import type { SemanticEntry } from '../../lib/api'

let facts: SemanticEntry[]
let writeSemantic: ReturnType<typeof vi.fn>
let reads: number

async function mountStudio() {
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as object),
        // Only the memory every chat shares: the picker of folders' memories stays out of sight.
        memoryPartitions: () => Promise.resolve([]),
        memoryStats: () => Promise.resolve(null),
        memorySemantic: () => { reads += 1; return Promise.resolve(facts) },
        memoryEpisodic: () => Promise.resolve([]),
        lessons: () => Promise.resolve([]),
        memoryGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntityGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntities: () => Promise.resolve({ entities: [], summary: {}, enabled: true }),
        memorySlots: () => Promise.resolve({ slots: [] }),
        memoryEntityProposals: () => Promise.resolve({ proposals: [], enabled: true }),
        memoryRecordLinks: () => Promise.resolve({ links: [] }),
        writeSemantic,
      },
    }
  })
  const { MemoryPanel } = await import('./MemoryPanel')
  render(<MemoryPanel query={{}} setQuery={() => {}} />)
  await waitFor(() => expect(screen.getByText('user.editor')).toBeTruthy())
}

const open = async (key: string) => {
  fireEvent.click(screen.getByText(key))
  fireEvent.click(await screen.findByRole('button', { name: `Edit the value of ${key}` }))
  return screen.getByRole('textbox', { name: `Value of ${key}` }) as HTMLTextAreaElement
}

beforeEach(() => {
  reads = 0
  facts = [
    { key: 'user.editor', value_json: '"vim"', source: 'consolidation', confidence: 0.8 },
    { key: 'project.kettle.settings', value_json: '{"theme":"dark","width":80}', source: 'user_explicit', confidence: 1 },
  ]
  writeSemantic = vi.fn((key: string, value: unknown) => {
    facts = facts.map((f) => (f.key === key ? { ...f, value_json: JSON.stringify(value), source: 'user_explicit', confidence: 1 } : f))
    return Promise.resolve({ ok: true })
  })
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView
})
afterEach(() => { cleanup(); vi.resetModules(); vi.doUnmock('../../lib/api') })

describe('editing a fact', () => {
  it('saves the new value under the same key and shows it', async () => {
    await mountStudio()
    const box = await open('user.editor')
    expect(box.value).toBe('vim')
    fireEvent.change(box, { target: { value: 'helix' } })
    fireEvent.click(screen.getByRole('button', { name: /Save/ }))
    await waitFor(() => expect(writeSemantic).toHaveBeenCalledWith('user.editor', 'helix', ''))
    // The Studio reads the store again, and the inspector shows what is stored now.
    await waitFor(() => expect(reads).toBeGreaterThan(1))
    await waitFor(() => expect(screen.queryByRole('textbox', { name: 'Value of user.editor' })).toBeNull())
    expect((await screen.findAllByText('helix')).length).toBeGreaterThan(0)
  })

  it('keeps a structured value structured, and refuses JSON that is not', async () => {
    await mountStudio()
    const box = await open('project.kettle.settings')
    expect(JSON.parse(box.value)).toEqual({ theme: 'dark', width: 80 })
    fireEvent.change(box, { target: { value: '{"theme": "light", ' } })
    fireEvent.click(screen.getByRole('button', { name: /Save/ }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/valid JSON/)
    expect(writeSemantic).not.toHaveBeenCalled()
    fireEvent.change(box, { target: { value: '{"theme": "light", "width": 80}' } })
    fireEvent.click(screen.getByRole('button', { name: /Save/ }))
    await waitFor(() => expect(writeSemantic).toHaveBeenCalledWith('project.kettle.settings', { theme: 'light', width: 80 }, ''))
  })

  it('says why a refused save was refused, and keeps the text', async () => {
    writeSemantic = vi.fn(() => Promise.reject(new Error('Value contains blocked content patterns')))
    await mountStudio()
    const box = await open('user.editor')
    fireEvent.change(box, { target: { value: 'emacs' } })
    fireEvent.click(screen.getByRole('button', { name: /Save/ }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/blocked content patterns/)
    expect((screen.getByRole('textbox', { name: 'Value of user.editor' }) as HTMLTextAreaElement).value).toBe('emacs')
  })

  it('an unsaved edit survives looking at another memory and coming back', async () => {
    await mountStudio()
    const box = await open('user.editor')
    fireEvent.change(box, { target: { value: 'kakoune' } })
    fireEvent.click(screen.getByText('project.kettle.settings'))
    await screen.findByRole('button', { name: 'Edit the value of project.kettle.settings' })
    fireEvent.click(screen.getByText('user.editor'))
    const back = (await screen.findByRole('textbox', { name: 'Value of user.editor' })) as HTMLTextAreaElement
    expect(back.value).toBe('kakoune')
    expect(writeSemantic).not.toHaveBeenCalled()
  })

  it('a save sent before Cancel still says what became of it', async () => {
    // Cancel closes the editor at once, but it cannot take back a save already sent — so the
    // outcome of that save must not vanish with the editor.
    let refuse: (e: Error) => void = () => {}
    writeSemantic = vi.fn(() => new Promise((_, reject) => { refuse = reject }))
    await mountStudio()
    const box = await open('user.editor')
    fireEvent.change(box, { target: { value: 'emacs' } })
    fireEvent.click(screen.getByRole('button', { name: /Save/ }))
    await waitFor(() => expect(writeSemantic).toHaveBeenCalledWith('user.editor', 'emacs', ''))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('textbox', { name: 'Value of user.editor' })).toBeNull()
    refuse(new Error('Value contains blocked content patterns'))
    expect((await screen.findByRole('alert')).textContent).toMatch(/blocked content patterns/)
  })

  it('Save waits for a change, and Cancel puts the stored value back', async () => {
    await mountStudio()
    const box = await open('user.editor')
    expect((screen.getByRole('button', { name: /Save/ }) as HTMLButtonElement).getAttribute('aria-disabled') === 'true'
      || (screen.getByRole('button', { name: /Save/ }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(box, { target: { value: 'nano' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('textbox', { name: 'Value of user.editor' })).toBeNull()
    expect(screen.getAllByText('vim').length).toBeGreaterThan(0)
    expect(writeSemantic).not.toHaveBeenCalled()
  })
})
