import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { mountList, navigate, project, running, waiting } from '../../test/projectListHarness'

// ── A pulse that failed is not nothing ───────────────────────────────────────────────────────────
//
// The list never says nothing needs you when it does not know. A failed waiting or running read
// draws the projects with no pulse and no live rows, and one note saying so, with Try again. A
// source the waiting read could not read (`unreadable`) leaves the counts drawn from what was read,
// and a note names the sources in Home's words. A failed chats read says so too, and is never read
// as no chat matching or a first run. Each note is a tinted box with its warn icon and Try again;
// only the unreadable sources' note announces. A failed list read is `LoadError`, never "No
// projects yet".

const GARDEN = project('p-garden', 'Garden')

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); resetDataStore(); navigate.mockReset() })
afterEach(() => { resetDataStore() })

describe('a pulse that could not be read', () => {
  it.each([
    ['the waiting read', { waiting: new Error('500'), running: [running('loop:f', 'Fix it', 'p-garden')] }],
    ['the running read', { waiting: [waiting('loop_question:q', 'loop_question', 'p-garden')], running: new Error('500') }],
  ])('draws no pulse and says so when %s failed', async (_what, reads) => {
    mountList({ projects: [GARDEN], ...reads })
    expect(await screen.findByText("Couldn't check what's running. Projects are listed without it.")).toBeInTheDocument()
    const garden = (await screen.findByRole('heading', { level: 2, name: /^Garden/ })).closest('li') as HTMLElement
    expect(within(garden).queryByText(/need you|needs you|running/)).toBeNull()
    expect(garden.querySelector('[data-work-row]')).toBeNull()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    const note = screen.getByText("Couldn't check what's running. Projects are listed without it.").closest('[data-read-note]') as HTMLElement
    expect(note.getAttribute('role'), 'read where it is, not announced').toBeNull()
    expect(note.querySelector('svg.text-warn')).toBeTruthy()
  })

  it('says the chats could not be read, never that none matches', async () => {
    mountList({ projects: [GARDEN], sessions: new Error('500'), waiting: [], running: [], query: { q: 'beds' } })
    expect(await screen.findByText("Couldn't read the chats. Projects are listed without them.")).toBeInTheDocument()
    expect(screen.queryByText(/No project or chat matches/)).toBeNull()
  })

  it('never reads a failed chats read as a first run, and its Try again asks the chats again', async () => {
    mountList({ projects: [project('p-personal', 'Personal', { is_builtin: true })], sessions: new Error('500'), waiting: [], running: [] })
    const note = await screen.findByText("Couldn't read the chats. Projects are listed without them.")
    expect(screen.queryByText(/Projects keep related work together/)).toBeNull()
    const { api } = await import('../../lib/api')
    const before = vi.mocked(api.chatSessions).mock.calls.length
    await userEvent.click(within(note.closest('[data-read-note]') as HTMLElement).getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(vi.mocked(api.chatSessions).mock.calls.length).toBeGreaterThan(before))
  })

  it('draws the counts it could read, and names the sources it could not in Home’s words', async () => {
    mountList({ projects: [GARDEN], waiting: [waiting('loop_question:q', 'loop_question', 'p-garden')], unreadable: ['approval', 'question'], running: [] })
    const note = await screen.findByText('Could not read approvals, agents’ questions: what they hold may be waiting on you and is not counted here.')
    expect(note.closest('[data-read-note]')?.getAttribute('role'), 'this one announces').toBe('status')
    const garden = (await screen.findByRole('heading', { level: 2, name: /^Garden/ })).closest('li') as HTMLElement
    await waitFor(() => expect(within(garden).getByText('1 needs you')).toBeInTheDocument())
  })

  it('draws LoadError, never "No projects yet", when the list itself failed', async () => {
    mountList({ projects: new Error('The gateway answered 500') })
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't load your projects")
    expect(screen.queryByText('No projects yet')).toBeNull()
  })
})
