/** DFE-7 — the grid editor's contract at its own surface.
 *
 *  Three things are asserted here that the pure-helper suite cannot reach, because they are
 *  about what the COMPONENT does rather than what a function returns:
 *
 *    1. an `.xlsx` type mounts THIS editor, not the block editor — the registration is the
 *       call site, and a grid nobody routes to is a page with a green suite;
 *    2. the lossy-edit gate is a MECHANISM: while a loss report is unacknowledged every
 *       cell input is disabled and carries a reason, so it cannot be typed past;
 *    3. the save PAYLOAD keeps a formula a formula and carries the loaded version as
 *       `If-Match` — otherwise the backend's read-back proof is testing a workbook the
 *       browser never sends.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { DocumentLossReport, SheetCellJson, SheetModelJson } from '../../lib/api'

const confirmSpy = vi.fn<(opts: unknown) => Promise<boolean>>()
vi.mock('../dialog', () => ({ confirm: (opts: unknown) => confirmSpy(opts) }))

const artifactSheetModel = vi.fn()
const saveArtifactSheetModel = vi.fn()
vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      artifactSheetModel: (s: string) => artifactSheetModel(s),
      saveArtifactSheetModel: (...a: unknown[]) => saveArtifactSheetModel(...a),
    },
  }
})

const { SheetGrid } = await import('./SheetGrid')
const { ApiError } = await import('../../lib/api')

const cell = (over: Partial<SheetCellJson> = {}): SheetCellJson => ({
  value: null, formula: '', number_format: '',
  bold: false, italic: false, font_color: '', fill: '', align: '', ...over,
})

const MODEL: SheetModelJson = {
  sheets: [{
    name: 'Q1',
    cells: [
      [cell({ value: 'Item', bold: true }), cell({ value: 'Qty', bold: true })],
      [cell({ value: 'Pens' }), cell({ value: 12 })],
      [cell({ value: 'Total' }), cell({ formula: '=SUM(B2:B2)' })],
    ],
    column_widths: [],
    merges: [],
    frozen_header: true,
  }],
}

const LOSSLESS: DocumentLossReport = { lossless: true, kinds: [], summary: 'no losses', items: [] }
const LOSSY: DocumentLossReport = {
  lossless: false,
  kinds: ['sheet_feature'],
  summary: 'sheet_feature×1',
  items: [{ kind: 'sheet_feature', detail: '1 conditional formatting on this sheet are not carried by the model', where: 'Q1', block_index: -1, paragraph_ordinal: -1 }],
}

const clone = (m: SheetModelJson): SheetModelJson => JSON.parse(JSON.stringify(m))

function load(loss: DocumentLossReport = LOSSLESS, model: SheetModelJson = MODEL) {
  artifactSheetModel.mockResolvedValue({ slug: 'budget', kind: 'xlsx', version: 4, mime: 'x', model: clone(model), loss })
}

beforeEach(() => {
  vi.clearAllMocks()
  confirmSpy.mockResolvedValue(true)
  saveArtifactSheetModel.mockResolvedValue({ slug: 'budget', version: 5, mime: 'x' })
})

// ── clause 1: the registration — an .xlsx gets the GRID ──────────────────────

describe('the xlsx content type mounts the grid', () => {
  it('registers SheetGrid for xlsx and the block editor for docx', async () => {
    const { registerBuiltinContentTypes } = await import('./registerBuiltins')
    const { getContentType } = await import('./contentTypes')
    const { setDocumentEditing } = await import('./documentEditing')
    const { DocumentEditor } = await import('./DocumentEditor')

    registerBuiltinContentTypes()
    setDocumentEditing(true)

    expect(getContentType('xlsx')?.edit?.render).toBe(SheetGrid)
    // The vacuity leg: the table has to be capable of a DIFFERENT answer, or "xlsx gets
    // the grid" would hold for a registration that gave every type the same editor.
    expect(getContentType('docx')?.edit?.render).toBe(DocumentEditor)
  })

  it('leaves xlsx with no editor at all while the flag is off', async () => {
    const { registerBuiltinContentTypes } = await import('./registerBuiltins')
    const { getContentType } = await import('./contentTypes')
    const { setDocumentEditing } = await import('./documentEditing')

    registerBuiltinContentTypes()
    setDocumentEditing(true)
    setDocumentEditing(false)

    expect(getContentType('xlsx')?.edit).toBeUndefined()
  })
})

// ── clause 2: the lossy gate is a mechanism ──────────────────────────────────

describe('the lossy-edit gate', () => {
  it('disables every cell and says why until the report is acknowledged', async () => {
    load(LOSSY)
    render(<SheetGrid slug="budget" title="Budget" mode="light" />)

    await screen.findByRole('alert')
    expect(screen.getByText('sheet_feature×1')).toBeTruthy()

    const a1 = await screen.findByRole('textbox', { name: 'Q1!A1' })
    expect(a1).toBeDisabled()
    // A keyboard user who lands on a dead cell must be able to learn what is missing.
    expect(a1.getAttribute('title')).toMatch(/edit anyway/i)
  })

  it('hands over the cells once acknowledged', async () => {
    load(LOSSY)
    render(<SheetGrid slug="budget" title="Budget" mode="light" />)

    await userEvent.click(await screen.findByRole('button', { name: /edit anyway/i }))

    expect(await screen.findByRole('textbox', { name: 'Q1!A1' })).not.toBeDisabled()
  })

  it('asks for no acknowledgement on a lossless workbook', async () => {
    load(LOSSLESS)
    render(<SheetGrid slug="budget" title="Budget" mode="light" />)

    expect(await screen.findByRole('textbox', { name: 'Q1!A1' })).not.toBeDisabled()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('repeats the same report in the save confirmation', async () => {
    load(LOSSY)
    render(<SheetGrid slug="budget" title="Budget" mode="light" />)
    await userEvent.click(await screen.findByRole('button', { name: /edit anyway/i }))
    await userEvent.type(await screen.findByRole('textbox', { name: 'Q1!A2' }), 'x')

    await userEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => expect(confirmSpy).toHaveBeenCalled())
    expect(saveArtifactSheetModel).toHaveBeenCalled()
  })

  it('writes nothing when that confirmation is cancelled', async () => {
    load(LOSSY)
    confirmSpy.mockResolvedValue(false)
    render(<SheetGrid slug="budget" title="Budget" mode="light" />)
    await userEvent.click(await screen.findByRole('button', { name: /edit anyway/i }))
    await userEvent.type(await screen.findByRole('textbox', { name: 'Q1!A2' }), 'x')

    await userEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => expect(confirmSpy).toHaveBeenCalled())
    expect(saveArtifactSheetModel).not.toHaveBeenCalled()
  })
})

// ── clause 3: the payload — a formula stays a formula, If-Match is carried ────

describe('the save payload', () => {
  it('shows a formula as a formula rather than a calculated result', async () => {
    load()
    render(<SheetGrid slug="budget" title="Budget" mode="light" />)

    const b3 = await screen.findByRole('textbox', { name: 'Q1!B3' })
    expect((b3 as HTMLInputElement).value).toBe('=SUM(B2:B2)')
  })

  it('sends the edited cell, the untouched formula and the loaded version', async () => {
    load()
    render(<SheetGrid slug="budget" title="Budget" mode="light" />)

    const a2 = await screen.findByRole('textbox', { name: 'Q1!A2' })
    await userEvent.clear(a2)
    await userEvent.type(a2, 'Notebooks')
    await userEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => expect(saveArtifactSheetModel).toHaveBeenCalled())
    const [slug, version, sent] = saveArtifactSheetModel.mock.calls[0] as [string, number, SheetModelJson]
    expect(slug).toBe('budget')
    expect(version).toBe(4) // the version the editor LOADED, not a guess
    expect(sent.sheets[0].cells[1][0].value).toBe('Notebooks')
    // The formula must survive an edit elsewhere in the sheet, in its own field.
    expect(sent.sheets[0].cells[2][1]).toEqual({ ...cell({ formula: '=SUM(B2:B2)' }) })
    // …and a save must not have flattened the header's formatting.
    expect(sent.sheets[0].cells[0][0].bold).toBe(true)
  })

  it('types a new formula into the formula field, not into value', async () => {
    load()
    render(<SheetGrid slug="budget" title="Budget" mode="light" />)

    const b2 = await screen.findByRole('textbox', { name: 'Q1!B2' })
    await userEvent.clear(b2)
    await userEvent.type(b2, '=A2*2')
    await userEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => expect(saveArtifactSheetModel).toHaveBeenCalled())
    const [, , sent] = saveArtifactSheetModel.mock.calls[0] as [string, number, SheetModelJson]
    expect(sent.sheets[0].cells[1][1].formula).toBe('=A2*2')
    expect(sent.sheets[0].cells[1][1].value).toBeNull()
  })

  it('lets a label that looks like a formula be kept as text', async () => {
    // The half of the defect a guess can never fix: `=TBD` is a label somebody typed, and
    // without this control it would be unreachable — the server would store a formula
    // Excel opens as #NAME?.
    load()
    render(<SheetGrid slug="budget" title="Budget" mode="light" />)

    const a2 = await screen.findByRole('textbox', { name: 'Q1!A2' })
    await userEvent.clear(a2)
    await userEvent.type(a2, '=TBD')
    await userEvent.click(screen.getByRole('button', { name: 'Treat as text' }))
    await userEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => expect(saveArtifactSheetModel).toHaveBeenCalled())
    const [, , sent] = saveArtifactSheetModel.mock.calls[0] as [string, number, SheetModelJson]
    expect(sent.sheets[0].cells[1][0]).toEqual(cell({ value: '=TBD' }))
  })

  it('sends a chosen number format', async () => {
    load()
    render(<SheetGrid slug="budget" title="Budget" mode="light" />)

    const b2 = await screen.findByRole('textbox', { name: 'Q1!B2' })
    await userEvent.click(b2)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: /number format/i }), '0.0%')
    await userEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => expect(saveArtifactSheetModel).toHaveBeenCalled())
    const [, , sent] = saveArtifactSheetModel.mock.calls[0] as [string, number, SheetModelJson]
    expect(sent.sheets[0].cells[1][1].number_format).toBe('0.0%')
    expect(sent.sheets[0].cells[1][1].value).toBe(12) // the format did not eat the value
  })

  it('reports a two-tab collision with the draft intact', async () => {
    load()
    saveArtifactSheetModel.mockRejectedValue(new ApiError('stale', 409))
    render(<SheetGrid slug="budget" title="Budget" mode="light" />)

    const a2 = await screen.findByRole('textbox', { name: 'Q1!A2' })
    await userEvent.clear(a2)
    await userEvent.type(a2, 'Notebooks')
    await userEvent.click(screen.getByRole('button', { name: /save/i }))

    expect(await screen.findByText(/changed somewhere else/i)).toBeTruthy()
    // The edit is STILL THERE — a collision must never discard what was typed.
    expect((screen.getByRole('textbox', { name: 'Q1!A2' }) as HTMLInputElement).value).toBe('Notebooks')
  })
})

// ── the grid's own structure ──────────────────────────────────────────────────

describe('the grid', () => {
  it('gives every cell a programmatic name, not just a visual column header', async () => {
    load()
    render(<SheetGrid slug="budget" title="Budget" mode="light" />)

    await screen.findByRole('textbox', { name: 'Q1!A1' })
    // Six cells, each named by its own ref: a screen reader on an unnamed text box in a
    // grid has no way to say WHICH cell it is on.
    expect(screen.getAllByRole('textbox')).toHaveLength(6)
    expect(screen.getByRole('textbox', { name: 'Q1!B3' })).toBeTruthy()
  })

  it('says it does not calculate, so a formula cell is not mistaken for a result', async () => {
    load()
    render(<SheetGrid slug="budget" title="Budget" mode="light" />)

    expect(await screen.findByText(/does not calculate them/i)).toBeTruthy()
  })

  it('shows an empty state for a workbook with no sheets', async () => {
    load(LOSSLESS, { sheets: [] })
    render(<SheetGrid slug="budget" title="Budget" mode="light" />)

    expect(await screen.findByText(/no sheets/i)).toBeTruthy()
  })

  it('reports a failed read instead of rendering an empty grid', async () => {
    // A failed fetch renders as an empty state unless it is distinguished from "no data".
    artifactSheetModel.mockRejectedValue(new Error('boom'))
    render(<SheetGrid slug="budget" title="Budget" mode="light" />)

    expect(await screen.findByText(/boom/)).toBeTruthy()
    expect(screen.queryByRole('textbox')).toBeNull()
  })
})
