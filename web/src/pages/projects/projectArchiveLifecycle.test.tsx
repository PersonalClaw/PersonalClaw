import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { ProjectItem } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { mountPage, PID, project } from '../../test/projectPageHarness'

// ── A project's archive and its default, from the page's "…" (PROJ PR 4) ───────────────────────
//
// Archive is the "…"'s row, with the archive confirm word for word and no write when it is
// dismissed; Restore is its row on an archived project, as is the archived notice's Restore. The
// default is an account preference, said in its row's own label ("Make default project", or
// "Default project" while it is), written on the server, never in this browser; an archived project
// cannot become the default and says why.

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); resetDataStore() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); sessionStorage.clear(); resetDataStore() })

function mount(status: ProjectItem['status'], isDefault = false) {
  const p = project(PID, 'Launch notes', { status, brief: 'Ship the release notes.' })
  return mountPage({ project: p, defaultProjectId: isDefault && status !== 'archived' ? PID : '' })
}

/** A row of the header's "…" (each is a button in its menu): the last match, since the page may
 *  draw the same action elsewhere too (the archived notice's Restore). */
async function moreRow(name: RegExp) {
  await userEvent.click(await screen.findByRole('button', { name: 'More actions' }))
  const rows = await screen.findAllByRole('button', { name })
  return rows[rows.length - 1]
}

describe('project archive lifecycle', () => {
  it('makes Archive findable, explains the picker effect, and writes nothing when dismissed', async () => {
    const { updateProject } = mount('active')
    await userEvent.click(await moreRow(/^Archive$/))
    const dialog = await screen.findByRole('dialog', { name: /Archive project "Launch notes"/i })
    expect(dialog.textContent).toMatch(/project pickers/i)
    expect(dialog.textContent).toMatch(/nothing is deleted/i)
    expect(updateProject, 'asking is not writing').not.toHaveBeenCalled()
    await userEvent.click(within(dialog).getByRole('button', { name: /^Cancel$/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(updateProject, 'dismissal must keep the persisted status').not.toHaveBeenCalled()
  })

  it('confirms Archive through the project update sink', async () => {
    const { updateProject } = mount('active')
    await userEvent.click(await moreRow(/^Archive$/))
    const dialog = await screen.findByRole('dialog', { name: /Archive project "Launch notes"/i })
    await userEvent.click(within(dialog).getByRole('button', { name: /^Archive$/i }))
    await waitFor(() => expect(updateProject).toHaveBeenCalledWith(PID, { status: 'archived' }))
  })

  it('an archived project offers Restore in the "…" and in its notice, and restores at once', async () => {
    const { updateProject } = mount('archived')
    expect(await screen.findByText("Launch notes is archived. It's off the project pickers, and nothing in it was deleted.")).toBeTruthy()
    await userEvent.click(screen.getAllByRole('button', { name: /^Restore$/ })[0])
    await waitFor(() => expect(updateProject).toHaveBeenCalledWith(PID, { status: 'active' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(await moreRow(/^Restore$/)).toBeTruthy()
  })

  it('says the default in its own label, and takes no pressed state', async () => {
    mount('active', true)
    const row = await moreRow(/^Default project$/)
    expect(row.hasAttribute('aria-pressed')).toBe(false)
    expect(screen.queryByRole('button', { name: /^Make default project/ })).toBeNull()
  })

  it('makes a project the default through the server, not this browser', async () => {
    const { updateSettings } = mount('active')
    await userEvent.click(await moreRow(/^Make default project$/))
    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith({ default_project_id: PID }))
    expect(localStorage.getItem('active-project'), 'the per-browser pointer is gone').toBeNull()
  })

  it('an archived project cannot become the default, and says why', async () => {
    const { updateSettings } = mount('archived')
    const row = await moreRow(/^Make default project/)
    expect(row.textContent).toMatch(/Restore it first/)
    await userEvent.click(row)
    expect(updateSettings).not.toHaveBeenCalled()
  })

  it('keeps exactly both status writers behind the one shared update sink', () => {
    const source = readFileSync(join(process.cwd(), 'src/pages/projects/ProjectPage.tsx'), 'utf8')
    const code = source
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
    const statuses = [...code.matchAll(/patch\(\{\s*status:\s*'(active|archived)'\s*\}\)/g)].map((m) => m[1]).sort()
    expect(statuses, 'Archive and Restore each reach patch()').toEqual(['active', 'archived'])
    expect(code.match(/api\.updateProject\(/g) ?? [], 'one project update sink').toHaveLength(1)
  })
})
