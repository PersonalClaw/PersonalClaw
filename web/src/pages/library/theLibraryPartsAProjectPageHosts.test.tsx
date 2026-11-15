/** The library's parts a project's page hosts. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { answers, calls, resetGateway, row } from '../../test/libraryHarness'

vi.mock('../../lib/investigate', () => ({ investigate: vi.fn(() => Promise.resolve()) }))
vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { LibraryPreview } = await import('../../ui/LibraryPreview')
const { LibraryAddMenu } = await import('../../ui/LibraryAddMenu')
const { LibraryCard } = await import('../../ui/LibraryCard')
const { itemBackOf } = await import('../../app/itemBack')
const { investigate } = await import('../../lib/investigate')
const { resetDataStore } = await import('../../lib/data/store')

const HOME = { id: 'p-home', name: 'Homelab' }
const inputs = (navigate = vi.fn()) => ({
  navigate, writeUps: new Map<string, string>(), project: 'p-home', projects: [HOME], chatTitle: () => '', pickChat: vi.fn(), pickProject: vi.fn(),
})

beforeEach(() => {
  resetDataStore(); resetGateway()
  answers.knowledgeItem = { id: 'k1', title: 'Router manual', content: 'The uplink port.', type: 'note' }
  answers.researchReports = { reports: [] }
  answers.knowledgeIntents = { intents: [] }
})
afterEach(cleanup)

describe('LibraryPreview in a project', () => {
  it('🔑 New chat with this opens a new chat in the project with the thing attached', async () => {
    const navigate = vi.fn()
    render(<LibraryPreview item={row('knowledge:k1', 'Router manual', { project: HOME })} actions={inputs(navigate)} />)
    await userEvent.click(screen.getByRole('button', { name: /Use in chat/ }))
    await userEvent.click(await screen.findByText('New chat with this'))
    expect(navigate).toHaveBeenCalledWith(expect.stringMatching(/^chat\/new\?/))
    const q = new URLSearchParams(navigate.mock.calls[0][0].split('?')[1])
    expect(q.get('project')).toBe('p-home')
    expect(q.get('attach')).toBe('knowledge:k1')
  })

  it('🔑 Open adds back=project:<id>', async () => {
    const navigate = vi.fn()
    render(<LibraryPreview item={row('knowledge:k1', 'Router manual', { project: HOME })} actions={inputs(navigate)} />)
    await userEvent.click(screen.getByRole('button', { name: /^Open$/ }))
    expect(navigate).toHaveBeenCalledWith('knowledge/item/k1?back=project%3Ap-home')
  })

  it('🔑 Ask about this is an unbound investigate chat', async () => {
    render(<LibraryPreview item={row('knowledge:k1', 'Router manual', { project: HOME })} actions={inputs()} />)
    await userEvent.click(screen.getByRole('button', { name: /Use in chat/ }))
    await userEvent.click(await screen.findByText('Ask about this'))
    expect(investigate).toHaveBeenCalledWith('knowledge_item', 'k1', expect.not.objectContaining({ project: expect.anything() }))
  })

  it('🔑 for a thing in that project, About\'s Project row is the name alone and More has no Open project', async () => {
    render(<LibraryPreview item={row('knowledge:k1', 'Router manual', { project: HOME })} actions={inputs()} />)
    expect(screen.queryByText('Open project')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'More for Router manual' }))
    expect(screen.queryByText(/Open project/)).toBeNull()
  })

  it('a thing in another project keeps both', async () => {
    const shared = row('knowledge:k1', 'Router manual', { project: { id: 'p-garden', name: 'Garden' } })
    render(<LibraryPreview item={shared} actions={inputs()} />)
    expect(screen.getByText('Open project')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'More for Router manual' }))
    expect(await screen.findByText('Open project Garden')).toBeTruthy()
  })
})

describe('LibraryAddMenu in a project', () => {
  it('🔑 lists exactly the four add rows, and an upload sends project_id', async () => {
    answers.ingestKnowledgeFile = { item_id: 'k2', status: 'ok' }
    render(<LibraryAddMenu project={HOME} onOpen={vi.fn()} onSetup={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    for (const r of ['Upload files', 'Add a link', 'Write a note', 'Record audio']) expect(await screen.findByText(r)).toBeTruthy()
    for (const r of ['Follow a feed or folder', 'Look out for a topic', 'Schedule a report']) expect(screen.queryByText(r)).toBeNull()
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await userEvent.upload(input, new File(['x'], 'rack.md'))
    await vi.waitFor(() => expect(calls.ingestKnowledgeFile?.[0]?.[3]).toBe('p-home'))
  })
})

describe('LibraryCard\'s project form and the item pages\' back control', () => {
  it('🔑 the project form draws the title and "From <work> · <when>", no chip, never a live page', () => {
    const item = row('artifact:lisbon', 'Lisbon trip plan', { project: HOME, from: { ref: 'chat:c1', title: 'Lisbon weekend' } })
    render(<LibraryCard item={item} onOpen={vi.fn()} form="project" now={Date.parse('2026-11-10T10:00:00Z')} />)
    expect(screen.getByText(/From Lisbon weekend · /)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Filter by project/ })).toBeNull()
    expect(document.querySelector('iframe')).toBeNull()
  })

  it('🔑 back=project:<id> names the project and opens it; without it, Library and the thing selected', () => {
    expect(itemBackOf('knowledge:k1', 'project:p-home', () => 'Homelab')).toEqual({ label: 'Homelab', path: 'projects/p-home' })
    expect(itemBackOf('artifact:lisbon', undefined, () => '')).toEqual({ label: 'Library', path: 'library?item=artifact%3Alisbon' })
  })
})
