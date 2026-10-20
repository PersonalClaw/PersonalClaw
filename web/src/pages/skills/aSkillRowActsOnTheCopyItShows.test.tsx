import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api, type SkillIntegrity, type SkillItem } from '../../lib/api'
import { SkillInspector } from './SkillInspector'

// ── A skill's inspector acts on the copy its row shows, and says what changed honestly ──────────
//
// The Skills list shows the copy of each skill that agents get: the library's copy of a skill that
// comes with PersonalClaw, or an agent's own copy. The inspector is where the owner acts on it.
//
// * A bundled skill's copy is hers to edit. When an update ships a newer version over a copy she
//   changed, her copy is kept and the newer version is offered here: she takes it, or keeps hers.
// * A change since a skill was installed is her edit and reads as one; only a damaged install record
//   reads as something PersonalClaw cannot vouch for. Neither is called tampering.
// * The row of an agent's own skill opens, saves, checks and deletes that copy, never the library's
//   skill of the same name.

const { confirmDestructive, confirmDelete } = vi.hoisted(() => ({
  confirmDestructive: vi.fn(async () => true),
  confirmDelete: vi.fn(async () => true),
}))
vi.mock('../../ui/dialog', async (orig) => ({ ...(await orig<Record<string, unknown>>()), confirmDestructive, confirmDelete }))

const DIGEST = 'a'.repeat(64)

function skill(over: Partial<SkillItem> = {}): SkillItem {
  return {
    key: 'grill', name: 'grill', description: 'Questions first.', always: false,
    path: '/home/user/.personalclaw/skills/grill/SKILL.md', source: 'bundled', type: 'bundled',
    loaded_by_agents: [], integrity: 'edited', bundled_update: { digest: DIGEST }, ...over,
  }
}

function edited(name: string): SkillIntegrity {
  return { name, integrity: 'edited', ok: false, unlocked: false, mutated: ['SKILL.md'], missing: [], added: ['notes.md'], summary: '' }
}

beforeEach(() => {
  sessionStorage.clear()
  confirmDestructive.mockClear()
  vi.spyOn(api, 'skillFiles').mockResolvedValue({ name: 'grill', files: [] })
  vi.spyOn(api, 'skillDocument').mockResolvedValue({ value: '---\nname: grill\n---\n', revision: 'r1', refinements: [], loaded: '' })
})
afterEach(() => vi.restoreAllMocks())

describe('a bundled skill whose copy the owner changed', () => {
  it('offers the newer version, and installs exactly the one offered when she takes it', async () => {
    const take = vi.spyOn(api, 'useBundledSkill').mockResolvedValue({ ok: true, name: 'grill' })
    const saved = vi.fn()
    render(<SkillInspector skill={skill()} onDeleted={() => {}} onSaved={saved} />)

    expect(screen.getByText(/A newer version of this skill comes with PersonalClaw/)).toBeTruthy()
    expect(screen.getByText(/PersonalClaw kept yours and didn’t update it/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Use the new version/ }))

    await waitFor(() => expect(take).toHaveBeenCalledWith('grill', DIGEST))
    expect(confirmDestructive).toHaveBeenCalledWith(
      'Use the version of “grill” that comes with PersonalClaw?', expect.stringMatching(/your changes are not kept/), { confirmLabel: 'Use the new version' })
    await waitFor(() => expect(saved).toHaveBeenCalled())
  })

  it('keeps hers when she says so, and installs nothing', async () => {
    const take = vi.spyOn(api, 'useBundledSkill')
    const keep = vi.spyOn(api, 'keepSkillCopy').mockResolvedValue({ ok: true, name: 'grill' })
    render(<SkillInspector skill={skill()} onDeleted={() => {}} />)

    fireEvent.click(screen.getByRole('button', { name: 'Keep mine' }))

    await waitFor(() => expect(keep).toHaveBeenCalledWith('grill', DIGEST))
    expect(take).not.toHaveBeenCalled()
    expect(confirmDestructive).not.toHaveBeenCalled()
  })

  it('takes nothing when she backs out of the question', async () => {
    confirmDestructive.mockResolvedValueOnce(false)
    const take = vi.spyOn(api, 'useBundledSkill')
    render(<SkillInspector skill={skill()} onDeleted={() => {}} />)

    fireEvent.click(screen.getByRole('button', { name: /Use the new version/ }))

    await waitFor(() => expect(confirmDestructive).toHaveBeenCalled())
    expect(take).not.toHaveBeenCalled()
  })

  it('is hers to edit, and is not deleted from here', () => {
    render(<SkillInspector skill={skill()} onDeleted={() => {}} />)
    expect(screen.getByRole('button', { name: /Edit SKILL\.md/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Delete skill/ })).toBeNull()
    expect(screen.getByText(/when PersonalClaw updates, your version is kept/)).toBeTruthy()
  })

  it('the control: with nothing newer, no version is offered', () => {
    render(<SkillInspector skill={skill({ integrity: 'intact', bundled_update: null })} onDeleted={() => {}} />)
    expect(screen.queryByText(/A newer version of this skill/)).toBeNull()
    expect(screen.queryByRole('button', { name: /Use the new version/ })).toBeNull()
  })
})

describe('what changed since a skill was installed', () => {
  it('reads as her edit, with what changed, and never as tampering', async () => {
    vi.spyOn(api, 'verifySkill').mockResolvedValue(edited('notes'))
    render(<SkillInspector skill={skill({ name: 'notes', key: 'notes', source: 'local', type: 'installed', bundled_update: null })} onDeleted={() => {}} />)

    expect(screen.getByText('Edited — changed since it was installed')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Re-verify/ }))
    await waitFor(() => expect(screen.getByText('changed: SKILL.md')).toBeTruthy())
    expect(screen.getByText('added: notes.md')).toBeTruthy()
    expect(screen.queryByText(/tamper/i)).toBeNull()
  })

  it('a damaged install record is the one thing it cannot vouch for', () => {
    render(<SkillInspector skill={skill({ source: 'local', type: 'installed', integrity: 'tampered', bundled_update: null })} onDeleted={() => {}} />)
    expect(screen.getByText(/Can’t verify — its install record is damaged/)).toBeTruthy()
  })
})

describe('the row of an agent’s own skill', () => {
  const own = skill({
    key: 'researcher/grill', name: 'grill', source: 'agent-local', type: 'agent-local', agent: 'researcher',
    integrity: 'unverified', bundled_update: null,
  })

  it('lists, checks and deletes that agent’s copy', async () => {
    const files = vi.mocked(api.skillFiles)
    const verify = vi.spyOn(api, 'verifySkill').mockResolvedValue(edited('grill'))
    const remove = vi.spyOn(api, 'deleteSkill').mockResolvedValue(undefined)
    const deleted = vi.fn()
    render(<SkillInspector skill={own} onDeleted={deleted} />)

    await waitFor(() => expect(files).toHaveBeenCalledWith('grill', undefined, 'researcher'))
    fireEvent.click(screen.getByRole('button', { name: /Re-verify/ }))
    await waitFor(() => expect(verify).toHaveBeenCalledWith('grill', 'researcher'))
    fireEvent.click(screen.getByRole('button', { name: /Delete skill/ }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith('grill', 'researcher'))
    await waitFor(() => expect(deleted).toHaveBeenCalled())
  })

  it('opens and saves that agent’s copy in the editor', async () => {
    const read = vi.mocked(api.skillDocument)
    const update = vi.spyOn(api, 'updateSkill').mockResolvedValue({ ok: true, revision: 'r2' })
    render(<SkillInspector skill={own} onDeleted={() => {}} />)

    fireEvent.click(screen.getByRole('button', { name: /Edit SKILL\.md/ }))
    const field = await waitFor(() => screen.getByRole('textbox') as HTMLTextAreaElement)
    expect(read).toHaveBeenCalledWith('grill', 'researcher')
    fireEvent.change(field, { target: { value: '---\nname: grill\n---\nMine.\n' } })
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }))
    await waitFor(() => expect(update).toHaveBeenCalledWith('grill', '---\nname: grill\n---\nMine.\n', 'r1', 'researcher'))
  })
})
