import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { api, type ProjectItem } from '../lib/api'
import { resetDataStore } from '../lib/data'
import { OWN_PROJECT } from '../pages/loop/LoopComposer'
import { ProjectPicker, pickerProjects } from './ProjectPicker'

// ── A picker never dead-ends ─────────────────────────────────────────────────────────────────────
//
// Every list of projects ends with "New project…", which opens the New project dialog and picks
// what it made, under a rule of its own. Its rows take every list's order: the pins first, in their
// order, each with its pin, then latest activity, ties by name, the ones a loop made for itself
// after the owner's own. Its empty option is "No project" unless the caller says what its flow does
// with none; the loop composer's says what that kind does.

const p = (id: string, name: string, over: Partial<ProjectItem> = {}): ProjectItem => ({ id, name, status: 'active', ...over }) as ProjectItem
const LIST = [
  p('p-trip', 'Trip'),
  p('p-gate', 'Fix the gate', { made_for: { work: 'loop:c1', kind: 'code', name: 'Fix the gate' } }),
  p('p-garden', 'Garden'),
  p('p-old', 'Old', { status: 'archived' }),
  p('p-taxes', 'Taxes'),
]

beforeEach(() => { resetDataStore() })
afterEach(() => { vi.restoreAllMocks(); resetDataStore() })

describe("a picker's rows", () => {
  it('are the unarchived projects, pins first, then latest activity, ties by name, made-for ones after', () => {
    expect(pickerProjects(LIST, ['p-taxes']).map((x) => x.name)).toEqual(['Taxes', 'Garden', 'Trip', 'Fix the gate'])
    const busy = [
      p('p-a', 'Alder', { updated_at: '2026-11-01T10:00:00Z' }),
      p('p-b', 'Birch', { updated_at: '2026-11-12T10:00:00Z' }),
      p('p-c', 'Cedar', { updated_at: '2026-11-12T10:00:00Z' }),
    ]
    expect(pickerProjects(busy, []).map((x) => x.name)).toEqual(['Birch', 'Cedar', 'Alder'])
  })

  it('say "No project" for the empty option by default, and mark a pinned row with its pin', async () => {
    vi.spyOn(api, 'projects').mockResolvedValue(LIST)
    vi.spyOn(api, 'projectSettings').mockResolvedValue({ default_project_id: '', pinned_project_ids: ['p-taxes'] })
    render(<ProjectPicker value="" onChange={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: 'Project: No project' }))
    const list = await screen.findByRole('listbox', { name: 'Project' })
    await waitFor(() => expect(within(list).getByRole('option', { name: /Taxes/ }).querySelector('[data-pinned]')).toBeTruthy())
    expect(within(list).getByRole('option', { name: /Garden/ }).querySelector('[data-pinned]')).toBeNull()
    const options = within(list).getAllByRole('option')
    expect(options[0].textContent).toBe('No project ')
    expect(options.filter((o) => o.textContent?.startsWith('New project'))).toHaveLength(1)
    const rules = [...list.querySelectorAll('[data-picker-rule]')]
    expect(rules.at(-1)?.nextElementSibling?.textContent, 'its own rule, right before the last row').toBe('New project…')
    expect(options[options.length - 1].className).not.toContain('border-t')
    expect(list.textContent, 'no eyebrow over the projects').not.toMatch(/Existing/)
    expect(options[0].querySelector('[data-empty-glyph]'), '"No project" draws the projects glyph, not the + of making one').toBeTruthy()
  })

  it('end with "New project…", which opens the dialog and picks what it made', async () => {
    vi.spyOn(api, 'projects').mockResolvedValue(LIST)
    vi.spyOn(api, 'projectSettings').mockResolvedValue({ default_project_id: '', pinned_project_ids: [] })
    const made = p('p-beds', 'Beds')
    vi.spyOn(api, 'createProject').mockResolvedValue(made)
    const onChange = vi.fn()
    render(<ProjectPicker value="" onChange={onChange} />)
    await userEvent.click(screen.getByRole('button', { name: /^Project/ }))
    const list = await screen.findByRole('listbox', { name: 'Project' })
    await waitFor(() => expect(within(list).getAllByRole('option').length).toBeGreaterThan(2))
    const options = within(list).getAllByRole('option')
    expect(options[options.length - 1].textContent).toBe('New project…')
    await userEvent.click(options[options.length - 1])
    await userEvent.type(await screen.findByRole('textbox', { name: 'Name' }), 'Beds')
    await userEvent.click(screen.getByRole('button', { name: /Create project/ }))
    await waitFor(() => expect(onChange).toHaveBeenCalledWith('p-beds'))
  })
})

describe("the loop composer's empty option", () => {
  it('says what each kind does with no project', () => {
    expect(OWN_PROJECT.code).toBe('Its own project (named from the task)')
    expect(OWN_PROJECT.goal).toBe('Its own project (if its plan has sub-goals)')
    for (const kind of ['research', 'design', 'general'] as const) expect(OWN_PROJECT[kind]).toBe('No project')
  })
})
