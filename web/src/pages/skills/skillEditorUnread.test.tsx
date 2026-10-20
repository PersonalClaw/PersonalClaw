import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api, type SkillDocument, type SkillItem } from '../../lib/api'
import { SkillInspector } from './SkillInspector'

// ── The SKILL.md editor waits for a real read ─────────────────────────────────────────────────
//
// Save PUTs the whole document (`PUT /api/skills/<name> {content}`), and the editor's read used to
// swallow its failure into `''`: a failed read opened an EMPTY editor with Save enabled and no error,
// so one click replaced the skill's instructions with nothing — and the `''` was persisted under
// the key the file viewer reads for the same file.
//
// Same family as the onboarding defect (`app/identityReadFailure.test.tsx`): a failed read became
// an empty state, and the empty state licensed a write.

const skill: SkillItem = {
  key: 'notes', name: 'notes', description: 'notes description', always: false,
  path: '/skills/notes', source: 'local', type: 'installed', loaded_by_agents: [], integrity: 'intact',
}

/** The read: the skill's own text and its revision, with nothing applied on top. */
const doc = (value: string, revision: string): SkillDocument => ({ value, revision, refinements: [], loaded: value })

beforeEach(() => {
  sessionStorage.clear()
  vi.spyOn(api, 'skillFiles').mockResolvedValue({ name: skill.name, files: [] })
})
afterEach(() => vi.restoreAllMocks())

async function openEditor() {
  render(<SkillInspector skill={skill} onDeleted={() => {}} />)
  fireEvent.click(screen.getByRole('button', { name: /Edit SKILL\.md/ }))
}

describe('editing SKILL.md', () => {
  it('a failed read shows the failure and a retry, and Save writes nothing', async () => {
    vi.spyOn(api, 'skillDocument').mockRejectedValue(new Error('skill directory unreadable'))
    const update = vi.spyOn(api, 'updateSkill').mockResolvedValue({ ok: true, revision: 'r2' })
    await openEditor()
    expect(await screen.findByRole('heading', { name: "Couldn't load your skill file" })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
    expect(screen.queryByRole('textbox'), 'an empty editor stood in for an unread file').toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Save/ }))
    await act(() => new Promise((r) => setTimeout(r, 30)))
    expect(update, 'Save replaced the skill with an empty document').not.toHaveBeenCalled()
  })

  it('a Retry that reads the file opens it for editing', async () => {
    vi.spyOn(api, 'skillDocument')
      .mockRejectedValueOnce(new Error('skill directory unreadable'))
      .mockResolvedValue(doc('# Notes\nKeep them short.', 'r1'))
    await openEditor()
    fireEvent.click(await screen.findByRole('button', { name: /Retry/ }))
    await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('# Notes\nKeep them short.'))
  })

  it('a successful read saves what the user edited, over the revision it read', async () => {
    // The control: the whole-document write is right when the document it replaces was read — and
    // it names that read's revision, so a copy that went stale in the meantime is refused.
    vi.spyOn(api, 'skillDocument').mockResolvedValue(doc('# Notes', 'r1'))
    const update = vi.spyOn(api, 'updateSkill').mockResolvedValue({ ok: true, revision: 'r2' })
    await openEditor()
    const field = await waitFor(() => screen.getByRole('textbox') as HTMLTextAreaElement)
    fireEvent.change(field, { target: { value: '# Notes\nMore.' } })
    fireEvent.click(screen.getByRole('button', { name: /Save/ }))
    await waitFor(() => expect(update).toHaveBeenCalledWith('notes', '# Notes\nMore.', 'r1'))
  })
})

describe('a SKILL.md save from a stale copy', () => {
  // The gateway rewrites skills on its own — the curator ages them, a consolidation refines an
  // auto-created one — so the copy the editor opened can be older than what is stored by the time it
  // is saved. The old editor saved it anyway; the gateway now refuses it (`409 stale_write`).
  const stale = () => Object.assign(new Error('This write replaces the skill, which changed…'), { status: 409, code: 'stale_write' })

  it('is refused with the notice, and the draft stays in the editor', async () => {
    vi.spyOn(api, 'skillDocument')
      .mockResolvedValueOnce(doc('# Notes\nKeep them short.', 'r1'))
      .mockResolvedValue(doc('---\nstatus: stale\n---\n# Notes\nKeep them short.', 'r2'))
    const update = vi.spyOn(api, 'updateSkill').mockRejectedValue(stale())
    await openEditor()
    const field = await waitFor(() => screen.getByRole('textbox') as HTMLTextAreaElement)
    fireEvent.change(field, { target: { value: '# Notes\nKeep them short. Mine.' } })
    fireEvent.click(screen.getByRole('button', { name: /Save/ }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/This skill changed elsewhere/)
    expect(update).toHaveBeenCalledWith('notes', '# Notes\nKeep them short. Mine.', 'r1')
    // Nothing the user typed was lost: it is still in the editor, which is locked until they choose.
    const kept = screen.getByRole('textbox') as HTMLTextAreaElement
    expect(kept.value).toBe('# Notes\nKeep them short. Mine.')
  })
})
