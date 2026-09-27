import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { api, type PromptItem, type PromptSnippet } from '../../lib/api'
import { writeQuery } from '../../lib/data'
import { PromptDetail } from './PromptDetail'
import { SnippetDetail } from './SnippetDetail'

// ── A prompt or snippet save from a stale copy is refused, and the edit is kept ──────────────
//
// The editor rebuilds EVERY field of the record from the copy it opened with and PUTs it whole. Tab
// A opened the prompt, another tab (or an app update, or a history rollback) then changed it, and
// tab A's Save replaced that newer record with its own copy — the other change gone, nothing said.
// The gateway now refuses a stale copy (`409 stale_write`); this pins what the editors do with it.

const listRow: PromptItem = { name: 'triage', kind: 'user', title: 'Triage', description: '', source: 'user', tags: [] }
const mine: PromptItem = { ...listRow, content: 'Sort {{items}} by urgency.', variables: [], revision: 'p1' }
const theirs: PromptItem = { ...mine, title: 'Triage (renamed elsewhere)', revision: 'p2' }

const staleWrite = () =>
  Object.assign(new Error('This write replaces the prompt, which changed…'), { status: 409, code: 'stale_write' })

beforeEach(() => {
  sessionStorage.clear()
  // The editor's live preview and syntax reference read on their own; answered here so their
  // failures (jsdom has no gateway) do not put a second alert on screen.
  vi.spyOn(api, 'previewPrompt').mockResolvedValue({ ok: true, rendered: '', detected_variables: [], includes: [] })
  vi.spyOn(api, 'promptSyntax').mockResolvedValue({ functions: [], constructs: [] })
})
afterEach(() => vi.restoreAllMocks())

/** The stale-write notice — found by its own marker, since the form can carry other alerts, and
 *  held to being an alert: the user pressed Save and it did not save. */
async function staleNotice(): Promise<HTMLElement> {
  const el = await waitFor(() => {
    const found = document.querySelector<HTMLElement>('[data-stale-write="true"]')
    expect(found).not.toBeNull()
    return found!
  })
  expect(el.getAttribute('role')).toBe('alert')
  return el
}

function openPrompt() {
  render(<PromptDetail prompt={listRow} editing onEditingChange={() => {}} onSaved={() => {}} onDeleted={() => {}} onNavigate={() => {}} />)
}

async function editTemplate(to: string) {
  const template = await screen.findByLabelText('Prompt template') as HTMLTextAreaElement
  fireEvent.change(template, { target: { value: to } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Save$/ })) })
}

describe('a prompt save from a stale copy', () => {
  it('is refused with the notice, keeps the draft, and the refused save named the read revision', async () => {
    vi.spyOn(api, 'prompt').mockResolvedValueOnce(mine).mockResolvedValue(theirs)
    const save = vi.spyOn(api, 'savePrompt').mockRejectedValue(staleWrite())
    openPrompt()
    await editTemplate('Sort {{items}} by urgency, then by age.')
    const alert = await staleNotice()
    expect(alert.textContent).toMatch(/This prompt changed elsewhere/)
    expect(save).toHaveBeenCalledTimes(1)
    expect(save.mock.calls[0][1]).toMatchObject({ content: 'Sort {{items}} by urgency, then by age.' })
    expect(save.mock.calls[0][2]).toBe('p1')
    // The user's text survived the refusal.
    expect((screen.getByLabelText('Prompt template') as HTMLTextAreaElement).value).toBe('Sort {{items}} by urgency, then by age.')
  })

  it('Reload and reapply keeps the other change and lands the edit over the NEW revision', async () => {
    vi.spyOn(api, 'prompt').mockResolvedValueOnce(mine).mockResolvedValue(theirs)
    const save = vi.spyOn(api, 'savePrompt').mockImplementation((_name, _body, base) =>
      base === 'p2' ? Promise.resolve({ ok: true, prompt: theirs, revision: 'p3' }) : Promise.reject(staleWrite()))
    openPrompt()
    await editTemplate('Sort {{items}} by urgency, then by age.')
    const alert = await staleNotice()
    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2))
    // The title the other tab gave it survives; the template edit made here lands on top.
    expect(save.mock.calls[1][1]).toMatchObject({ title: 'Triage (renamed elsewhere)', content: 'Sort {{items}} by urgency, then by age.' })
    expect(save.mock.calls[1][2]).toBe('p2')
  })

  it('a record already in the cache seeds the draft before the form can save it', async () => {
    // The panel re-opens a prompt it read before: the full record paints from the cache while the
    // re-read is still out. It was taken as "seeded" while the draft still held the LIST ROW, so a
    // Save there sent an empty template — over the cached record's revision, which the gateway
    // would accept.
    writeQuery('prompt:triage', mine, true)
    vi.spyOn(api, 'prompt').mockReturnValue(new Promise(() => {}))
    const save = vi.spyOn(api, 'savePrompt').mockResolvedValue({ ok: true, prompt: mine, revision: 'p2' })
    openPrompt()
    const template = await screen.findByLabelText('Prompt template') as HTMLTextAreaElement
    expect(template.value).toBe('Sort {{items}} by urgency.')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Save$/ })) })
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save.mock.calls[0][1]).toMatchObject({ content: 'Sort {{items}} by urgency.' })
    expect(save.mock.calls[0][2]).toBe('p1')
  })
})

describe('a snippet save from a stale copy', () => {
  const row: PromptSnippet = { name: 'tone', title: 'Tone', source: 'user' }
  const read: PromptSnippet = { ...row, content: 'Be brief.', revision: 's1' }

  it('is refused with the notice, keeps the draft, and the refused save named the read revision', async () => {
    vi.spyOn(api, 'snippet').mockResolvedValueOnce(read).mockResolvedValue({ ...read, title: 'Voice', revision: 's2' })
    const save = vi.spyOn(api, 'saveSnippet').mockRejectedValue(staleWrite())
    render(<SnippetDetail snippet={row} editing onEditingChange={() => {}} onSaved={() => {}} onDeleted={() => {}} />)
    const content = await screen.findByLabelText('Snippet content') as HTMLTextAreaElement
    fireEvent.change(content, { target: { value: 'Be brief. Be kind.' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Save$/ })) })
    const alert = await staleNotice()
    expect(alert.textContent).toMatch(/This snippet changed elsewhere/)
    expect(save.mock.calls[0][2]).toBe('s1')
    expect((screen.getByLabelText('Snippet content') as HTMLTextAreaElement).value).toBe('Be brief. Be kind.')
  })
})
