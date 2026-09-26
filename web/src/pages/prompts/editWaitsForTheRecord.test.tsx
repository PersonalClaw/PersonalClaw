import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api, type PromptItem, type PromptSnippet } from '../../lib/api'
import { PromptDetail } from './PromptDetail'
import { SnippetDetail } from './SnippetDetail'

// ── Editing a prompt or a snippet waits for the full record ─────────────────────────────────────
//
// The inspector opens on the LIST row, and the list payload carries no `content`; the full record is
// hydrated on open. The edit form rendered from the list row anyway — the hydrate's error and loading
// branches sat below the edit branch — so while the hydrate was out, or after it failed, the form held
// an EMPTY template body, and Save PUTs the whole record: one click wiped the template. Edit mode is
// `?edit=1`, so a deep link opened straight into that form.
//
// Same family as the onboarding defect (`app/identityReadFailure.test.tsx`): an unread record stood in
// for an empty one, and licensed a write.

const listRow: PromptItem = { name: 'triage', kind: 'user', title: 'Triage', description: 'sort the inbox', source: 'user' }
const fullPrompt: PromptItem = { ...listRow, content: 'Sort {{items}} by urgency.', variables: [{ name: 'items' }] as PromptItem['variables'] }
const snippetRow: PromptSnippet = { name: 'tone', title: 'Tone', source: 'user' }
const fullSnippet: PromptSnippet = { ...snippetRow, content: 'Be brief.' }

const settle = () => act(() => new Promise((r) => setTimeout(r, 30)))

beforeEach(() => sessionStorage.clear())
afterEach(() => vi.restoreAllMocks())

function openPrompt() {
  render(<PromptDetail prompt={listRow} editing onEditingChange={() => {}} onSaved={() => {}} onDeleted={() => {}} onNavigate={() => {}} />)
}
function openSnippet() {
  render(<SnippetDetail snippet={snippetRow} editing onEditingChange={() => {}} onSaved={() => {}} onDeleted={() => {}} />)
}
/** The edit form's Save, if it is on screen. */
const saveButton = () => screen.queryByRole('button', { name: /^Save$/ })
/** Click Save the INSTANT it enters the DOM — from a MutationObserver, which runs before React's
 *  passive effects. That is the window an effect-seeded draft leaves open: the form has committed,
 *  and its draft is still the list row's. A plain `waitFor` + click only lands there under load. */
function clickSaveTheMomentItAppears() {
  const obs = new MutationObserver(() => {
    const b = saveButton()
    if (b) { obs.disconnect(); fireEvent.click(b) }
  })
  obs.observe(document.body, { childList: true, subtree: true })
}

describe('editing a prompt', () => {
  it('a failed read of the prompt shows the failure, not an empty editor, and nothing can be saved', async () => {
    vi.spyOn(api, 'prompt').mockRejectedValue(new Error('prompt store unreadable'))
    const save = vi.spyOn(api, 'savePrompt').mockResolvedValue({ ok: true, prompt: fullPrompt })
    openPrompt()
    expect(await screen.findByRole('heading', { name: "Couldn't load your prompt" })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
    expect(saveButton(), 'an edit form was offered over an unread template').toBeNull()
    await settle()
    expect(save).not.toHaveBeenCalled()
  })

  it('no editor is offered while the prompt is still being read', async () => {
    vi.spyOn(api, 'prompt').mockReturnValue(new Promise(() => {}))
    openPrompt()
    await settle()
    expect(saveButton()).toBeNull()
  })

  it('once the prompt is read, the editor holds its template and saves it', async () => {
    // The control: the whole-record write is right when the record it replaces was read.
    vi.spyOn(api, 'prompt').mockResolvedValue(fullPrompt)
    const save = vi.spyOn(api, 'savePrompt').mockResolvedValue({ ok: true, prompt: fullPrompt })
    clickSaveTheMomentItAppears()
    openPrompt()
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save.mock.calls[0][1], 'a Save in the form\'s first commit sent the list row').toMatchObject({ content: 'Sort {{items}} by urgency.' })
  })
})

describe('editing a snippet', () => {
  it('a failed read of the snippet shows the failure, and nothing can be saved', async () => {
    vi.spyOn(api, 'snippet').mockRejectedValue(new Error('snippet store unreadable'))
    const save = vi.spyOn(api, 'saveSnippet').mockResolvedValue({ ok: true, snippet: fullSnippet })
    openSnippet()
    expect(await screen.findByRole('heading', { name: "Couldn't load your snippet" })).toBeInTheDocument()
    expect(saveButton(), 'an edit form was offered over an unread snippet').toBeNull()
    await settle()
    expect(save).not.toHaveBeenCalled()
  })

  it('once the snippet is read, the editor saves its body', async () => {
    vi.spyOn(api, 'snippet').mockResolvedValue(fullSnippet)
    const save = vi.spyOn(api, 'saveSnippet').mockResolvedValue({ ok: true, snippet: fullSnippet })
    clickSaveTheMomentItAppears()
    openSnippet()
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save.mock.calls[0][1], 'a Save in the form\'s first commit sent the list row').toMatchObject({ content: 'Be brief.' })
  })
})
