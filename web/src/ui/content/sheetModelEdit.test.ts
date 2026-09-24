/** The grid's pure rules — the ones a component test would only measure React through. */
import { describe, expect, it } from 'vitest'
import type { SheetCellJson, SheetJson, SheetModelJson } from '../../lib/api'
import {
  asFormula,
  asLiteral,
  canBeFormula,
  cellAt,
  cellRef,
  cellText,
  columnCount,
  columnLabel,
  emptyCell,
  formatOptions,
  parseEntry,
  withCell,
} from './sheetModelEdit'

const cell = (over: Partial<SheetCellJson> = {}): SheetCellJson => ({ ...emptyCell(), ...over })
const sheet = (cells: SheetCellJson[][], name = 'S'): SheetJson =>
  ({ name, cells, column_widths: [], merges: [], frozen_header: false })
const model = (cells: SheetCellJson[][]): SheetModelJson => ({ sheets: [sheet(cells)] })

describe('parseEntry — what makes an entry a formula', () => {
  it('reads a leading = as a formula, in the formula field', () => {
    expect(parseEntry('=SUM(A1:A9)')).toEqual({ value: null, formula: '=SUM(A1:A9)' })
  })

  it('leaves a bare = a literal, because it is not an expression yet', () => {
    // Vacuity for the case above: without the length check every half-typed "=" would
    // become a formula the moment the key landed.
    expect(parseEntry('=')).toEqual({ value: '=', formula: '' })
  })

  it('recovers numbers so a typed 12 stays summable', () => {
    expect(parseEntry('12')).toEqual({ value: 12, formula: '' })
    expect(parseEntry('-1.5')).toEqual({ value: -1.5, formula: '' })
  })

  it('recovers booleans', () => {
    expect(parseEntry('TRUE')).toEqual({ value: true, formula: '' })
    expect(parseEntry('false')).toEqual({ value: false, formula: '' })
  })

  it('keeps text as text and does not turn blank-ish input into zero', () => {
    // `Number(' ')` is 0, so a cell cleared to a space would silently become zero.
    expect(parseEntry('EMEA')).toEqual({ value: 'EMEA', formula: '' })
    expect(parseEntry(' ')).toEqual({ value: ' ', formula: '' })
    expect(parseEntry('')).toEqual({ value: null, formula: '' })
  })
})

describe('the formula/literal override — the half a guess cannot fix', () => {
  it('turns a formula back into the label somebody typed', () => {
    expect(asLiteral(cell({ formula: '=TBD' }))).toEqual(cell({ value: '=TBD', formula: '' }))
  })

  it('turns an =-leading label into a real formula', () => {
    expect(asFormula(cell({ value: '=1+1' }))).toEqual(cell({ value: null, formula: '=1+1' }))
  })

  it('refuses to invent an expression from text that is not one', () => {
    // The vacuity leg: without this, `asFormula` on "hello" would write formula="hello",
    // which the server's own validator rejects — a 400 on save instead of a dead control.
    const plain = cell({ value: 'hello' })
    expect(canBeFormula(plain)).toBe(false)
    expect(asFormula(plain)).toEqual(plain)
  })

  it('is a no-op in each direction when there is nothing to convert', () => {
    const literal = cell({ value: 'x' })
    expect(asLiteral(literal)).toBe(literal)
    const formula = cell({ formula: '=A1' })
    expect(asFormula(formula)).toBe(formula)
  })
})

describe('cellText — the formula bar shows the formula', () => {
  it('shows the expression, never a cached result', () => {
    expect(cellText(cell({ formula: '=A1', value: 'stale' }))).toBe('=A1')
  })

  it('shows an empty string for an empty cell, not "null"', () => {
    expect(cellText(cell())).toBe('')
  })

  it('stringifies a typed value for display', () => {
    expect(cellText(cell({ value: 12 }))).toBe('12')
    expect(cellText(cell({ value: false }))).toBe('false')
  })
})

describe('withCell — one cell changes and nothing else does', () => {
  it('replaces the target and leaves its neighbours identical', () => {
    const before = model([[cell({ value: 'a' }), cell({ value: 'b' })], [cell({ value: 'c' })]])
    const after = withCell(before, 0, 0, 0, cell({ value: 'z' }))

    expect(after.sheets[0].cells[0][0].value).toBe('z')
    expect(after.sheets[0].cells[0][1]).toBe(before.sheets[0].cells[0][1])
    expect(after.sheets[0].cells[1]).toBe(before.sheets[0].cells[1])
  })

  it('returns a NEW model, because the dirty flag is identity-based', () => {
    const before = model([[cell({ value: 'a' })]])
    const after = withCell(before, 0, 0, 0, cell({ value: 'a' }))

    expect(after).not.toBe(before)
    expect(before.sheets[0].cells[0][0].value).toBe('a') // the original is untouched
  })

  it('pads a short row so an addressable column can be typed into', () => {
    const before = model([[cell({ value: 'a' })]])
    const after = withCell(before, 0, 0, 2, cell({ value: 'c' }))

    expect(after.sheets[0].cells[0].map((c) => c.value)).toEqual(['a', null, 'c'])
  })

  it('leaves other sheets untouched', () => {
    const two: SheetModelJson = { sheets: [sheet([[cell({ value: 'a' })]], 'One'), sheet([[cell({ value: 'b' })]], 'Two')] }
    const after = withCell(two, 1, 0, 0, cell({ value: 'z' }))

    expect(after.sheets[0]).toBe(two.sheets[0])
    expect(after.sheets[1].cells[0][0].value).toBe('z')
  })
})

describe('grid geometry', () => {
  it('labels columns the way a person reads them, past Z', () => {
    expect([0, 1, 25, 26, 27, 51, 52].map(columnLabel)).toEqual(['A', 'B', 'Z', 'AA', 'AB', 'AZ', 'BA'])
  })

  it('takes the width from the WIDEST row, not the first', () => {
    // A grid sized to row 0 would hide cells that exist further down the sheet.
    expect(columnCount(sheet([[cell()], [cell(), cell(), cell()]]))).toBe(3)
  })

  it('reads a missing cell as blank rather than crashing', () => {
    expect(cellAt(sheet([[cell({ value: 'a' })]]), 5, 5)).toEqual(emptyCell())
  })

  it('names a cell in the sheet-qualified terms the loss report uses', () => {
    expect(cellRef(sheet([[cell()]], 'Sales'), 1, 2)).toBe('Sales!C2')
  })
})

describe('formatOptions — the control reflects the document it loaded', () => {
  it('offers the presets when the cell has none', () => {
    const options = formatOptions('')
    expect(options[0]).toEqual({ value: '', label: 'Automatic' })
    expect(options.some((o) => o.value === '0.0%')).toBe(true)
    expect(options.some((o) => o.label.startsWith('Custom'))).toBe(false)
  })

  it("carries the document's OWN format when it is not a preset", () => {
    // Otherwise a sheet formatted `#,##0.00" kg"` opens showing "Automatic", and saving
    // strips a format the user never chose to remove.
    const options = formatOptions('#,##0.00" kg"')
    expect(options.at(-1)).toEqual({ value: '#,##0.00" kg"', label: 'Custom (#,##0.00" kg")' })
  })

  it('does not duplicate a format that IS a preset', () => {
    expect(formatOptions('0.0%').filter((o) => o.value === '0.0%')).toHaveLength(1)
  })
})
