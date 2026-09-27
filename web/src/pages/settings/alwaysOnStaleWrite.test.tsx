import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent, act, waitFor, within } from '@testing-library/react'
import type { AlwaysOnItem, AlwaysOnResponse } from '../../lib/api'

// ── A project-overview save from a stale copy is refused, and the edit is kept ──────────────────
//
// The overview is not the page's alone: every workflow run that completes in the project appends a
// line to it. The editor saved its whole copy over whatever was stored, so a run that finished while
// the editor was open lost its line without a word. The gateway now refuses a stale copy
// (`409 stale_write`); this pins what the editor does with that.

vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

const alwaysOnDoc = vi.fn()
const saveAlwaysOnDoc = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    alwaysOn: () => Promise.resolve(response),
    alwaysOnDoc: (...a: unknown[]) => alwaysOnDoc(...a),
    saveAlwaysOnDoc: (...a: unknown[]) => saveAlwaysOnDoc(...a),
    projects: () => Promise.resolve([{ id: 'p-1', name: 'Roofing Rebuild' }]),
  },
}))

const { AlwaysOnConventions } = await import('./AlwaysOnConventions')

const OVERVIEW: AlwaysOnItem = {
  id: 'project_instruction:overview.md', kind: 'project_instruction', name: 'overview.md',
  scope: 'project', source: 'project:p-1', path: '/home/projects/p-1/context/overview.md',
  chars: 64, editable: true, read_only_reason: '', project_id: 'p-1', preview: 'The deck is stripped.',
}
const response: AlwaysOnResponse = {
  items: [OVERVIEW], project_id: 'p-1',
  counts: { total: 1, always_skills: 0, project_instructions: 1 },
  always_skill_mechanism: 'always: true in a skill’s SKILL.md frontmatter',
}
const OPENED = 'The deck is stripped.\n\nNext: flashing.'
// A run completed while the editor was open and appended its line.
const AFTER_RUN = OPENED + '\n- deploy → complete: shipped'
const stale = () => Object.assign(new Error('This write replaces the project instruction, which changed…'), { status: 409, code: 'stale_write' })

beforeEach(() => {
  alwaysOnDoc.mockReset()
    .mockResolvedValueOnce({ ...OVERVIEW, body: OPENED, revision: 'o1' })
    .mockResolvedValue({ ...OVERVIEW, body: AFTER_RUN, revision: 'o2' })
  saveAlwaysOnDoc.mockReset().mockImplementation((_id: string, _pid: string, body: string, base: string) =>
    base === 'o2' ? Promise.resolve({ ok: true, item: { ...OVERVIEW, body, revision: 'o3' } }) : Promise.reject(stale()))
})

async function openAndEdit(text: string) {
  const view = render(<AlwaysOnConventions />)
  await waitFor(() => expect(view.queryByText('overview.md')).not.toBeNull())
  await act(async () => { fireEvent.click(view.getByRole('button', { name: 'Edit' })) })
  const box = await view.findByRole('textbox') as HTMLTextAreaElement
  fireEvent.change(box, { target: { value: text } })
  await act(async () => { fireEvent.click(view.getByRole('button', { name: 'Save' })) })
  return { view, box }
}

const notice = () => waitFor(() => {
  const el = document.querySelector<HTMLElement>('[data-stale-write="true"]')
  expect(el).not.toBeNull()
  return el!
})

describe('a project-overview save from a stale copy', () => {
  it('is refused with the notice, and the draft stays in the editor', async () => {
    const { box } = await openAndEdit('The deck is stripped and sealed.\n\nNext: flashing.')
    const el = await notice()
    expect(el.getAttribute('role')).toBe('alert')
    expect(el.textContent).toMatch(/changed elsewhere/)
    expect(saveAlwaysOnDoc).toHaveBeenCalledWith('project_instruction:overview.md', 'p-1', 'The deck is stripped and sealed.\n\nNext: flashing.', 'o1')
    expect(box.value).toBe('The deck is stripped and sealed.\n\nNext: flashing.')
  })

  it('Reload and reapply keeps the run’s line and lands the edit over the new revision', async () => {
    const { box } = await openAndEdit('The deck is stripped and sealed.\n\nNext: flashing.')
    const reapply = within(await notice()).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })
    await waitFor(() => expect(saveAlwaysOnDoc).toHaveBeenCalledTimes(2))
    const merged = 'The deck is stripped and sealed.\n\nNext: flashing.\n- deploy → complete: shipped'
    expect(saveAlwaysOnDoc.mock.calls[1]).toEqual(['project_instruction:overview.md', 'p-1', merged, 'o2'])
    await waitFor(() => expect(box.value).toBe(merged))
    expect(document.querySelector('[data-stale-write="true"]')).toBeNull()
  })
})
