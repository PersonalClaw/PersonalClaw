import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { api } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { libraryItem, libraryRead, mountPage, navigate, PID, project } from '../../test/projectPageHarness'

// ── A project's Results and Knowledge are the library's rows (§5.8, §5.10, §5.16) ───────────────
//
// One library read (`GET /api/library?project=<id>`), split by store: Results are the artifacts,
// as the library's cards in their project form ("From <work> · <when>"), and About › Knowledge the
// knowledge items, as its compact rows. "See all 12" and "6 items" are the read's store facet, not
// the rows drawn, and their links carry `store=`. A tile or a row opens the library's preview beside
// the work (`?open=<ref>`): its Open adds `back=project:<id>`, and its New chat with this binds the
// project. An address naming an item outside the project's own read opens no preview, Add's forms
// file into the project, and the links carry the project's id encoded.

Element.prototype.scrollIntoView ??= () => {}

beforeEach(() => { resetDataStore(); localStorage.clear(); navigate.mockReset() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDataStore() })

const yesterday = new Date(Date.now() - 86_400_000).toISOString()
const REPORT = libraryItem('artifact:report-1', 'Spring planting plan', { from: { ref: 'loop:l-1', title: 'Plan the spring beds' }, activity_at: yesterday })
const ITEMS = [
  REPORT,
  libraryItem('artifact:sensor-chart', 'Soil sensor chart'),
  libraryItem('knowledge:k-1', 'Tomato varieties'),
  libraryItem('knowledge:k-2', 'Bed layout notes'),
]

const results = async () => (await screen.findByRole('heading', { level: 2, name: 'Results' })).closest('section')!
const knowledge = async () => (await screen.findByRole('heading', { level: 3, name: 'Knowledge' })).closest('section')!

describe("a project's Results and Knowledge", () => {
  it('come from one library read, split by store, with the facet counts and store links', async () => {
    mountPage({ library: libraryRead(ITEMS, { artifacts: 12, knowledge: 6 }) })
    const r = await results()
    expect(within(r).getAllByRole('listitem')).toHaveLength(2)
    expect(within(r).getByRole('link', { name: 'See all 12 in Library' }).getAttribute('href'))
      .toBe(`#/library?project=${PID}&store=artifacts`)
    const k = await knowledge()
    expect(within(k).getByText('6 items')).toBeTruthy()
    expect(within(k).getByText('Tomato varieties')).toBeTruthy()
    expect(within(k).getByRole('link', { name: 'Open in Library' }).getAttribute('href')).toBe(`#/library?project=${PID}&store=knowledge`)
    expect(vi.mocked(api.library)).toHaveBeenCalledTimes(1)
    expect(vi.mocked(api.library)).toHaveBeenCalledWith({ project: PID })
  })

  it('a tile says what it came from and when', async () => {
    mountPage({ library: libraryRead(ITEMS) })
    const tile = await within(await results()).findByRole('button', { name: /^Spring planting plan, From Plan the spring beds · /i })
    expect(tile).toBeTruthy()
  })

  it("a tile opens the library's preview beside the work, whose Open adds back=project and whose New chat binds the project", async () => {
    const { address } = mountPage({ library: libraryRead(ITEMS) })
    await userEvent.click(await within(await results()).findByRole('button', { name: /^Spring planting plan/ }))
    expect(address.query.open).toBe('artifact:report-1')
    const preview = (await screen.findByRole('heading', { level: 2, name: 'Spring planting plan' })).closest('section')!
    await userEvent.click(within(preview).getByRole('button', { name: /^Open$/ }))
    expect(navigate).toHaveBeenCalledWith(`artifacts/report-1?back=${encodeURIComponent(`project:${PID}`)}`)
    await userEvent.click(within(preview).getByRole('button', { name: /^Use in chat/ }))
    await userEvent.click(await screen.findByRole('button', { name: /^New chat with this/ }))
    expect(navigate).toHaveBeenLastCalledWith(`chat/new?project=${PID}&attach=${encodeURIComponent('artifact:report-1')}`)
  })

  it("a knowledge row opens its preview beside the work too", async () => {
    const { address } = mountPage({ library: libraryRead(ITEMS) })
    await userEvent.click(await within(await knowledge()).findByRole('button', { name: /Tomato varieties/ }))
    expect(address.query.open).toBe('knowledge:k-1')
    await waitFor(() => expect(screen.getByRole('heading', { level: 2, name: 'Tomato varieties' })).toBeTruthy())
  })

  it('draws no Results group while the project has none, and Knowledge says nothing is saved yet', async () => {
    mountPage({ library: libraryRead([]) })
    const k = await knowledge()
    expect(within(k).getByText('Nothing saved for Garden yet. Notes and files saved from its chats show up here.')).toBeTruthy()
    expect(screen.queryByRole('heading', { level: 2, name: 'Results' })).toBeNull()
  })

  it("opens no preview for an item outside the project's own read, and says so", async () => {
    mountPage({ library: libraryRead(ITEMS), query: { open: 'artifact:not-in-garden' } })
    expect(await screen.findByText('Nothing in your library has this address.')).toBeTruthy()
    expect(screen.queryByRole('heading', { level: 2, name: 'Spring planting plan' })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Use in chat/ })).toBeNull()
  })

  it("Knowledge's Add opens the note form beside the work, and the note it adds is filed in the project", async () => {
    const add = vi.spyOn(api, 'createKnowledgeItem').mockResolvedValue({ id: 'k-new' } as Awaited<ReturnType<typeof api.createKnowledgeItem>>)
    const { address } = mountPage({ library: libraryRead(ITEMS) })
    await userEvent.click(within(await knowledge()).getByRole('button', { name: /^Add/ }))
    await userEvent.click(await screen.findByRole('menuitem', { name: /^Write a note/ }))
    expect(address.query.open).toBe('note:new')
    await userEvent.type(await screen.findByRole('textbox', { name: 'Title' }), 'Seed order')
    await userEvent.click(screen.getByRole('button', { name: /^Add$/ }))
    await waitFor(() => expect(add).toHaveBeenCalledTimes(1))
    expect(add.mock.calls[0][0]).toMatchObject({ title: 'Seed order', project_id: PID })
  })

  it("the library links carry a project's id encoded", async () => {
    // The address's first segment is the id (`ProjectsSection`), so it has no `/`; each of these
    // would end the id or start another key in an unencoded link.
    const odd = 'a b&store=knowledge#x?section=y'
    mountPage({ id: odd, project: project(odd, 'Garden', { workspace_dir: '', folder: null }), library: libraryRead(ITEMS, { artifacts: 12, knowledge: 6 }) })
    const enc = encodeURIComponent(odd)
    expect(enc).toBe('a%20b%26store%3Dknowledge%23x%3Fsection%3Dy')
    expect(within(await results()).getByRole('link', { name: 'See all 12 in Library' }).getAttribute('href'))
      .toBe(`#/library?project=${enc}&store=artifacts`)
    expect(within(await knowledge()).getByRole('link', { name: 'Open in Library' }).getAttribute('href'))
      .toBe(`#/library?project=${enc}&store=knowledge`)
  })

  it("a knowledge row says who it is from only when that is not this project: a teammate's, a shared item's", async () => {
    mountPage({ library: libraryRead([
      libraryItem('knowledge:k-own', 'Bed layout notes'),
      libraryItem('knowledge:k-shared', 'Frost dates', { project: { id: 'p-personal', name: 'Personal' } }),
      libraryItem('knowledge:k-sam', 'Seed suppliers', { contributor: 'Sam' }),
    ]) })
    const k = await knowledge()
    const row = (name: RegExp) => within(k).getByRole('button', { name })
    expect(row(/^Bed layout notes/).closest('li')!.textContent).not.toMatch(/From |Added by/)
    expect(row(/^Frost dates/).closest('li')!.textContent).toContain('From Personal')
    expect(row(/^Seed suppliers/).closest('li')!.textContent).toContain('Added by Sam')
  })
})
