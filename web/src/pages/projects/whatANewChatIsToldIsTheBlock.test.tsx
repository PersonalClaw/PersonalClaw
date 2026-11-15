import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { mountPage, TOLD } from '../../test/projectPageHarness'

// ── What a new chat here is told is the block itself (§5.10, §5.11 `told`) ──────────────────────
//
// See it shows `project_block.text`, word for word, each part labelled in the gutter; the summary
// line comes from `parts`, which the code that writes each line counts; "Not in it" names the
// instructions and the knowledge.

beforeEach(() => { resetDataStore() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDataStore() })

describe('what a new chat in a project is told', () => {
  it("summarises the block from its parts, and See it shows the block's own text", async () => {
    const { address } = mountPage({ told: TOLD })
    const part = (await screen.findByRole('heading', { level: 3, name: 'What a new chat here is told' })).closest('section')!
    await waitFor(() => expect(part.textContent).toContain('Each new chat here starts with the brief and 1 decision.'))
    await userEvent.click(within(part).getByRole('button', { name: 'See it' }))
    expect(address.query.open).toBe('told')
    const block = await screen.findByRole('group', { name: 'What a new chat in Garden is told' })
    for (const line of TOLD.project_block!.text.split('\n')) expect(block.textContent).toContain(line.trim())
    expect(within(block).getByText('Brief')).toBeTruthy()
    expect(within(block).getByText('Decisions')).toBeTruthy()
    expect(screen.getByText(/^Not in it: the instructions, .* and the knowledge, /)).toBeTruthy()
  })

  it('says nothing is told yet while the block carries nothing of the owner', async () => {
    mountPage({ told: { ...TOLD, project_block: { text: '', parts: { brief: 0, overview: 0, decisions: 0, open_questions: 0, out_of_scope: 0 } } } })
    expect(await screen.findByText('Nothing yet. Once you write the brief, every new chat here starts with it.')).toBeTruthy()
  })

  it('says it could not load the block, with Try again, and the rest of the page draws', async () => {
    mountPage({ told: new Error('down') })
    expect(await screen.findByText("Couldn't load what a new chat here is told.")).toBeTruthy()
    expect(screen.getByRole('heading', { level: 3, name: 'Brief' })).toBeTruthy()
  })
})
