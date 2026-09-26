import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api, type SkillItem } from '../../lib/api'
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
    vi.spyOn(api, 'skillContent').mockRejectedValue(new Error('skill directory unreadable'))
    const update = vi.spyOn(api, 'updateSkill').mockResolvedValue({ ok: true } as never)
    await openEditor()
    expect(await screen.findByRole('heading', { name: "Couldn't load your SKILL.md" })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
    expect(screen.queryByRole('textbox'), 'an empty editor stood in for an unread file').toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Save/ }))
    await act(() => new Promise((r) => setTimeout(r, 30)))
    expect(update, 'Save replaced the skill with an empty document').not.toHaveBeenCalled()
  })

  it('a Retry that reads the file opens it for editing', async () => {
    vi.spyOn(api, 'skillContent')
      .mockRejectedValueOnce(new Error('skill directory unreadable'))
      .mockResolvedValue('# Notes\nKeep them short.')
    await openEditor()
    fireEvent.click(await screen.findByRole('button', { name: /Retry/ }))
    await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('# Notes\nKeep them short.'))
  })

  it('a successful read saves what the user edited', async () => {
    // The control: the whole-document write is right when the document it replaces was read.
    vi.spyOn(api, 'skillContent').mockResolvedValue('# Notes')
    const update = vi.spyOn(api, 'updateSkill').mockResolvedValue({ ok: true } as never)
    await openEditor()
    const field = await waitFor(() => screen.getByRole('textbox') as HTMLTextAreaElement)
    fireEvent.change(field, { target: { value: '# Notes\nMore.' } })
    fireEvent.click(screen.getByRole('button', { name: /Save/ }))
    await waitFor(() => expect(update).toHaveBeenCalledWith('notes', '# Notes\nMore.'))
  })
})
