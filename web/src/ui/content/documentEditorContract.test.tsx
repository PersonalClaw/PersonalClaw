import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { DocumentBlock, DocumentLossReport, DocumentModelJson, DocumentRun } from '../../lib/api'

// ── §C5, the lossy-edit contract — as a mechanism, not a notice ────────────────
//
// Re-rendering a document can only emit what the model can hold, so a save on a document
// whose parse reported losses DELETES things the user never saw. The repo's standing lesson
// is that a control which appears to work and then quietly reverts is worse than one that
// refuses — so this surface is built to make the loss impossible to walk into unknowingly,
// and these are the assertions that keep it that way:
//
//   1. a lossy document hands over NO editable control until its report is acknowledged
//      (a banner beside a live field is a warning a user types straight past);
//   2. the save confirmation repeats the same report, from the same component, so the two
//      cannot drift;
//   3. cancelling that confirmation writes NOTHING;
//   4. a 409 (the two-tab race) is reported with the draft INTACT — never merged, never
//      silently discarded;
//   5. a lossless document is not made to click through a ceremonial gate, because a gate
//      in front of a safe edit is training to ignore the one that matters.
//
// The save PAYLOAD is asserted too: "bold a word" has to arrive at the server as a split
// run, or the read-back half of this atom (tests/test_document_editing_gate.py) is testing
// a document the browser never sends.

const confirmSpy = vi.fn<(opts: unknown) => Promise<boolean>>()
vi.mock('../dialog', () => ({ confirm: (opts: unknown) => confirmSpy(opts) }))

const artifactModel = vi.fn()
const saveArtifactModel = vi.fn()
vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: { ...actual.api, artifactModel: (s: string) => artifactModel(s), saveArtifactModel: (...a: unknown[]) => saveArtifactModel(...a) },
  }
})

const { DocumentEditor } = await import('./DocumentEditor')
const { ApiError } = await import('../../lib/api')

const run = (text: string, marks: Partial<DocumentRun> = {}): DocumentRun =>
  ({ text, bold: false, italic: false, code: false, link: '', ...marks })

const block = (over: Partial<DocumentBlock>): DocumentBlock => ({
  kind: 'paragraph', text: '', level: 1, items: [], rows: [],
  artifact_slug: '', runs: [], cells: [], style: null, ...over,
})

const MODEL: DocumentModelJson = {
  title: 'Fidelity',
  blocks: [
    block({ kind: 'heading', text: 'Overview', runs: [run('Overview')] }),
    block({ kind: 'paragraph', text: 'a plain word', runs: [run('a plain word')] }),
    block({ kind: 'table', rows: [['h'], ['v']] }),
  ],
  page: null,
}

const LOSSY: DocumentLossReport = {
  lossless: false,
  kinds: ['footnote'],
  summary: '2 things will not survive an edit.',
  items: [
    { kind: 'footnote', detail: 'a footnote reference', where: 'block 1, paragraph 3', block_index: 1, paragraph_ordinal: 3 },
    { kind: 'page_property', detail: 'asymmetric margins', where: 'document', block_index: -1, paragraph_ordinal: -1 },
  ],
}

const CLEAN: DocumentLossReport = { lossless: true, kinds: [], summary: 'Nothing was lost.', items: [] }

function mount(loss: DocumentLossReport, over: { readOnly?: boolean; onDirty?: (d: boolean) => void } = {}) {
  artifactModel.mockResolvedValue({ slug: 'report', kind: 'docx', version: 4, mime: 'x', model: structuredClone(MODEL), loss })
  return render(<DocumentEditor slug="report" title="Report" mode="dark" {...over} />)
}

/** The paragraph field, once the model has loaded. */
const paragraph = () => screen.findByLabelText('Paragraph')

/** Select a span inside a textarea and let the component read it.
 *
 *  `mouseUp` rather than a synthesized `select` event: React's `onSelect` is a polyfilled
 *  synthetic event that a hand-dispatched `new Event('select')` does not reliably reach, and
 *  a helper that silently fails to register the selection makes every assertion below pass
 *  for the wrong reason. `onMouseUp` is a plain delegated handler, and it is one of the three
 *  the field really listens on. */
function select(el: HTMLTextAreaElement, from: number, to: number) {
  el.focus()
  el.setSelectionRange(from, to)
  fireEvent.mouseUp(el)
}

beforeEach(() => {
  confirmSpy.mockReset()
  confirmSpy.mockResolvedValue(true)
  artifactModel.mockReset()
  saveArtifactModel.mockReset()
  saveArtifactModel.mockResolvedValue({ slug: 'report', version: 5, mime: 'x' })
})

describe('a lossy document warns BEFORE the first edit', () => {
  it('names what will not survive, and the fields are not editable yet', async () => {
    mount(LOSSY)
    expect(await screen.findByText('2 things will not survive an edit.')).toBeInTheDocument()
    expect(screen.getByText(/a footnote reference/)).toBeInTheDocument()
    expect(await paragraph()).toBeDisabled()
    // `aria-disabled`, not the native attribute: `Button` softens a disabled state that carries
    // a REASON into a focusable aria-disabled control, so a keyboard user can land on it and
    // read why. Asserting `toBeDisabled()` here would fail on the very wiring that explains it.
    const bold = screen.getByRole('button', { name: 'Bold' })
    expect(bold).toHaveAttribute('aria-disabled', 'true')
    expect(bold).toHaveAttribute('title', expect.stringContaining('formatting notice'))
  })

  it('acknowledging it — and only that — hands over the controls', async () => {
    mount(LOSSY)
    expect(await paragraph()).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: /I understand/ }))
    expect(await paragraph()).toBeEnabled()
  })

  it('a lossless document has no gate to click through', async () => {
    mount(CLEAN)
    expect(await paragraph()).toBeEnabled()
    expect(screen.queryByRole('button', { name: /I understand/ })).not.toBeInTheDocument()
  })

  it('a read-only host stays read-only even after acknowledgement is moot', async () => {
    mount(CLEAN, { readOnly: true })
    expect(await paragraph()).toBeDisabled()
  })
})

describe('bolding a word posts a split run', () => {
  it('sends three runs with only the selected word bold', async () => {
    mount(CLEAN)
    const field = await paragraph() as HTMLTextAreaElement
    select(field, 2, 7)
    await userEvent.click(screen.getByRole('button', { name: 'Bold' }))
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))

    await waitFor(() => expect(saveArtifactModel).toHaveBeenCalledTimes(1))
    const [slug, version, model] = saveArtifactModel.mock.calls[0] as [string, number, DocumentModelJson]
    expect(slug).toBe('report')
    // The version the editor LOADED — this is the If-Match that makes the two-tab race a 409.
    expect(version).toBe(4)
    expect(model.blocks[1].runs.map((r) => [r.text, r.bold])).toEqual([
      ['a ', false], ['plain', true], [' word', false],
    ])
    // The untouched blocks go back exactly as they came, including the table this editor
    // does not offer controls for.
    expect(model.blocks[2].rows).toEqual([['h'], ['v']])
  })

  it('the save button is dead until something actually changed, and says so', async () => {
    mount(CLEAN)
    await paragraph()
    const save = screen.getByRole('button', { name: /^Save/ })
    expect(save).toHaveAttribute('aria-disabled', 'true')
    expect(save).toHaveAttribute('title', expect.stringContaining('No changes to save'))
  })
})

describe('the save confirmation repeats the report', () => {
  it('asks, with the same loss items in the body, before writing', async () => {
    mount(LOSSY)
    await userEvent.click(await screen.findByRole('button', { name: /I understand/ }))
    const field = await paragraph() as HTMLTextAreaElement
    await userEvent.type(field, '!')
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))

    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1))
    const opts = confirmSpy.mock.calls[0][0] as { title: string; body: React.ReactNode; danger?: boolean }
    expect(opts.danger).toBe(true)
    // A destructive dialog must name WHAT it is about — a bare 'Save this document?' over two
    // open tabs is a question the user cannot answer.
    expect(opts.title).toContain('Report')
    // Render the dialog body: asserting the STRING would let the two copies drift, and the
    // point of the clause is that the user sees the same report twice.
    render(<>{opts.body}</>)
    expect(screen.getAllByText('2 things will not survive an edit.').length).toBeGreaterThan(0)
    expect(screen.getByText(/asymmetric margins/)).toBeInTheDocument()
    // And it says the recovery, because "recoverable" is what makes this acceptable at all.
    expect(screen.getByText(/Version 4 is kept/)).toBeInTheDocument()
  })

  it('a DISMISSED confirmation writes nothing', async () => {
    confirmSpy.mockResolvedValue(false)
    mount(LOSSY)
    await userEvent.click(await screen.findByRole('button', { name: /I understand/ }))
    await userEvent.type(await paragraph(), '!')
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))
    await waitFor(() => expect(confirmSpy).toHaveBeenCalled())
    expect(saveArtifactModel).not.toHaveBeenCalled()
  })

  it('a lossless save is not confirmed — there is nothing to confirm', async () => {
    mount(CLEAN)
    await userEvent.type(await paragraph(), '!')
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))
    await waitFor(() => expect(saveArtifactModel).toHaveBeenCalled())
    expect(confirmSpy).not.toHaveBeenCalled()
  })
})

describe('the two-tab race is reported, not merged', () => {
  it('a 409 says what happened and leaves the draft saveable', async () => {
    saveArtifactModel.mockRejectedValue(new ApiError('stale', 409))
    mount(CLEAN)
    const field = await paragraph() as HTMLTextAreaElement
    await userEvent.type(field, '!')
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))

    expect(await screen.findByText(/changed somewhere else/)).toBeInTheDocument()
    // The edit is STILL THERE. A 409 that cleared the draft would lose exactly the work the
    // refusal was protecting.
    expect(field).toHaveValue('a plain word!')
    expect(screen.getByRole('button', { name: /^Save/ })).toBeEnabled()
  })

  it('any other failure is surfaced too, not swallowed into a silent no-op', async () => {
    saveArtifactModel.mockRejectedValue(new ApiError('in-place document editing is off', 403))
    mount(CLEAN)
    await userEvent.type(await paragraph(), '!')
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))
    expect(await screen.findByText(/editing is off/)).toBeInTheDocument()
  })
})

describe('the host learns about unsaved work', () => {
  it('reports dirty on the first edit and clean again after a successful save', async () => {
    const onDirty = vi.fn()
    mount(CLEAN, { onDirty })
    await userEvent.type(await paragraph(), '!')
    await waitFor(() => expect(onDirty).toHaveBeenCalledWith(true))
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))
    await waitFor(() => expect(onDirty).toHaveBeenLastCalledWith(false))
  })
})

describe('a failed read is not an empty document', () => {
  it('says the read failed instead of offering an editor over nothing', async () => {
    artifactModel.mockRejectedValue(new Error('nope'))
    render(<DocumentEditor slug="report" title="Report" mode="dark" />)
    expect(await screen.findByText(/Couldn’t read Report/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Paragraph')).not.toBeInTheDocument()
  })
})
