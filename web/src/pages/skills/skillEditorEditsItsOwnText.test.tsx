import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { api, type SkillDocument, type SkillItem, type SkillRefinement } from '../../lib/api'
import { SkillInspector } from './SkillInspector'
import { holdsRefinementCopy } from './skillMeta'

// ── The SKILL.md editor edits the skill's own text ────────────────────────────────────────────
//
// An accepted refinement is kept beside its skill and added after the skill's own text each time it
// loads. The editor used to read the skill as it LOADS, refinements included, and save it back whole:
// each refinement went into SKILL.md as well, loaded twice, and reverting it removed only the copy
// kept beside the skill. The editor now reads and saves the skill's own text, names the refinements
// applied on top of it under the editor, says that an edit does not change them, and offers a revert
// for each. A refinement pasted into the text word for word is named before the save, with what the
// save does about it.

const { confirmDestructive } = vi.hoisted(() => ({ confirmDestructive: vi.fn(async () => true) }))
vi.mock('../../ui/dialog', async (orig) => ({ ...(await orig<Record<string, unknown>>()), confirmDestructive }))

const OWN = '---\nname: notes\ndescription: Take notes.\n---\n# Notes\nKeep them short.\n'
const CITE: SkillRefinement = {
  id: 'r-cite', version: 1, description: 'Refined after you corrected this turn', created_at: '2026-10-02T09:00:00+00:00',
  trigger: 'correction',
  text: '## Refinement v1 (2026-10-02, from a correction)\n\n_Refined after you corrected this turn_\n\nCite the source of every note.',
}
const LINK: SkillRefinement = {
  ...CITE, id: 'r-link', version: 2, text: '## Refinement v2 (2026-10-02, from a correction)\n\nLink each note to its meeting.',
}

function doc(refinements: SkillRefinement[], value = OWN): SkillDocument {
  return {
    value, revision: `rev-${value.length}`, refinements,
    loaded: refinements.length ? `${value.trimEnd()}\n\n${refinements.map((r) => r.text).join('\n\n')}\n` : value,
  }
}

function item(over: Partial<SkillItem> = {}): SkillItem {
  return {
    key: 'notes', name: 'notes', description: 'notes description', always: false,
    path: '/skills/notes', source: 'local', type: 'installed', loaded_by_agents: [], integrity: 'intact', ...over,
  }
}

beforeEach(() => {
  sessionStorage.clear()
  confirmDestructive.mockClear()
  vi.spyOn(api, 'skillFiles').mockResolvedValue({ name: 'notes', files: [] })
})
afterEach(() => vi.restoreAllMocks())

async function openEditor(skill: SkillItem = item()) {
  render(<SkillInspector skill={skill} onDeleted={() => {}} />)
  fireEvent.click(screen.getByRole('button', { name: /Edit SKILL\.md/ }))
  return await waitFor(() => screen.getByRole('textbox') as HTMLTextAreaElement)
}

describe('the SKILL.md editor', () => {
  it('edits the skill’s own text, names the refinements applied on top, and saves only that text', async () => {
    vi.spyOn(api, 'skillDocument').mockResolvedValue(doc([CITE, LINK]))
    const update = vi.spyOn(api, 'updateSkill').mockResolvedValue({ ok: true, revision: 'r2' })
    const field = await openEditor()
    expect(field.value).toBe(OWN)
    const list = await screen.findByRole('list', { name: 'Accepted refinements of notes' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('Cite the source of every note.'),
      expect.stringContaining('Link each note to its meeting.'),
    ])
    expect(screen.getByText(/This is the skill’s own text\..*Editing this text doesn’t change them, and saving never writes them into SKILL\.md\..*revert it/)).toBeTruthy()
    expect(within(list).getByRole('button', { name: 'Revert refinement v1' })).toBeTruthy()

    fireEvent.change(field, { target: { value: OWN.replace('short.', 'short and dated.') } })
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }))
    await waitFor(() => expect(update).toHaveBeenCalledWith('notes', OWN.replace('short.', 'short and dated.'), `rev-${OWN.length}`))
    expect(update.mock.calls[0][1]).not.toContain('Cite the source')
  })

  it('names a refinement the text holds word for word, and what saving does about it', async () => {
    vi.spyOn(api, 'skillDocument').mockResolvedValue(doc([CITE]))
    const field = await openEditor()
    expect(screen.queryByRole('status')).toBeNull()
    fireEvent.change(field, { target: { value: `${OWN}\n${CITE.text}\n` } })
    const note = await screen.findByRole('status')
    expect(note.textContent).toMatch(/holds refinement v1 word for word.*Saving leaves this copy out of SKILL\.md.*revert v1 first/)
    // Reworded, it is the author's own text, and nothing is said about it.
    fireEvent.change(field, { target: { value: `${OWN}\n${CITE.text.replace('every note', 'each note')}\n` } })
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
  })

  it('reverts one refinement after asking, and keeps what is being typed', async () => {
    const read = vi.spyOn(api, 'skillDocument').mockResolvedValue(doc([CITE, LINK]))
    const revert = vi.spyOn(api, 'revertSkillRefinement').mockImplementation(async () => {
      read.mockResolvedValue(doc([{ ...LINK, version: 1 }]))
      return { ok: true, reverted: 1, refinements: [{ ...LINK, version: 1 }] }
    })
    const field = await openEditor()
    fireEvent.change(field, { target: { value: `${OWN}Mine.\n` } })
    fireEvent.click(await screen.findByRole('button', { name: 'Revert refinement v1' }))
    await waitFor(() => expect(revert).toHaveBeenCalledWith('notes', 'r-cite'))
    expect(confirmDestructive).toHaveBeenCalledWith(
      'Revert refinement v1 of “notes”?', expect.stringMatching(/loads without it/), { confirmLabel: 'Revert' })
    await waitFor(() => expect(screen.queryByText(/Cite the source of every note\./)).toBeNull())
    expect(screen.getByText(/Link each note to its meeting\./)).toBeTruthy()
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(`${OWN}Mine.\n`)
  })

  it('reverts nothing when the question is declined', async () => {
    vi.spyOn(api, 'skillDocument').mockResolvedValue(doc([CITE]))
    const revert = vi.spyOn(api, 'revertSkillRefinement')
    confirmDestructive.mockResolvedValueOnce(false)
    await openEditor()
    fireEvent.click(await screen.findByRole('button', { name: 'Revert refinement v1' }))
    await waitFor(() => expect(confirmDestructive).toHaveBeenCalled())
    expect(revert).not.toHaveBeenCalled()
  })

  it('the control: a skill with no refinement shows none, and saves what was typed', async () => {
    vi.spyOn(api, 'skillDocument').mockResolvedValue(doc([]))
    const update = vi.spyOn(api, 'updateSkill').mockResolvedValue({ ok: true, revision: 'r2' })
    const field = await openEditor()
    expect(screen.queryByRole('list', { name: /Accepted refinements/ })).toBeNull()
    expect(screen.queryByText(/This is the skill’s own text/)).toBeNull()
    fireEvent.change(field, { target: { value: `${OWN}Typed.\n` } })
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }))
    await waitFor(() => expect(update).toHaveBeenCalledWith('notes', `${OWN}Typed.\n`, `rev-${OWN.length}`))
  })
})

describe('the inspector', () => {
  it('names a skill’s accepted refinements and offers to revert each, a skill it cannot edit too', async () => {
    vi.spyOn(api, 'skillDocument').mockResolvedValue(doc([CITE]))
    render(<SkillInspector skill={item({ source: 'shared', type: 'read-only' })} onDeleted={() => {}} />)
    const list = await screen.findByRole('list', { name: 'Accepted refinements of notes' })
    expect(list.textContent).toContain('Cite the source of every note.')
    expect(screen.getByText(/Added after the skill’s own text each time it loads/)).toBeTruthy()
    expect(within(list).getByRole('button', { name: 'Revert refinement v1' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Edit SKILL\.md/ })).toBeNull()
  })

  it('says nothing about refinements for a skill that has none', async () => {
    const read = vi.spyOn(api, 'skillDocument').mockResolvedValue(doc([]))
    render(<SkillInspector skill={item()} onDeleted={() => {}} />)
    await waitFor(() => expect(read).toHaveBeenCalled())
    expect(screen.queryByText('Accepted refinements')).toBeNull()
  })
})

describe('holdsRefinementCopy — the test the gateway applies when it saves', () => {
  const block = CITE.text
  it('finds a copy that stands as a part of its own, anywhere after the frontmatter', () => {
    expect(holdsRefinementCopy(`${OWN}\n${block}\n`, block)).toBe(true)
    expect(holdsRefinementCopy(`${OWN}\n${block}`, block)).toBe(true)
    expect(holdsRefinementCopy(`${OWN}\n${block}\n\nMy note.\n`, block)).toBe(true)
    expect(holdsRefinementCopy(`---\nname: notes\n---\n\n${block}\n`, block)).toBe(true)
  })
  it('keeps what is the author’s: a reworded copy, one run into the next line, the text alone', () => {
    expect(holdsRefinementCopy(`${OWN}\n${block.replace('every', 'each')}\n`, block)).toBe(false)
    expect(holdsRefinementCopy(`${OWN}\n${block}\nand more on the same part.\n`, block)).toBe(false)
    expect(holdsRefinementCopy(OWN, block)).toBe(false)
    expect(holdsRefinementCopy(`${OWN}\n${block}\n`, '  \n')).toBe(false)
  })
})
