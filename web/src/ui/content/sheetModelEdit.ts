/** Pure edits over a `SheetModelJson` — no React, no fetch, no DOM.
 *
 *  The sheet-side counterpart of `documentModelEdit.ts`, and separate from it for the same
 *  reason that module exists: the interesting rules (what makes an entry a formula, how a
 *  cell is replaced without disturbing its neighbours) are testable as data, and a
 *  component test that had to render a grid to check them would be measuring React.
 *
 *  **Every function returns a NEW model.** The editor's dirty flag is identity-based
 *  (`model !== loaded.model`), so an in-place mutation would edit the document and leave
 *  the Save button convinced there was nothing to save.
 */
import type { SheetCellJson, SheetJson, SheetModelJson } from '../../lib/api'

/** A blank cell, spelled once so every insertion path agrees on what "empty" is. */
export function emptyCell(): SheetCellJson {
  return {
    value: null, formula: '', number_format: '',
    bold: false, italic: false, font_color: '', fill: '', align: '',
  }
}

/** 0-based column index → `A`, `B`, … `AA`. The header a person expects to read. */
export function columnLabel(index: number): string {
  let label = ''
  for (let n = index; n >= 0; n = Math.floor(n / 26) - 1) label = String.fromCharCode(65 + (n % 26)) + label
  return label
}

/** The widest row's length — rows in a sheet need not be equal, and a grid rendered to
 *  the FIRST row's width would hide cells that exist further down. */
export function columnCount(sheet: SheetJson): number {
  return sheet.cells.reduce((widest, row) => Math.max(widest, row.length), 0)
}

/** A1-notation address of a cell, for a label and for a loss report's own vocabulary. */
export function cellRef(sheet: SheetJson, row: number, col: number): string {
  return `${sheet.name}!${columnLabel(col)}${row + 1}`
}

/** What the input shows: the formula if there is one, else the literal.
 *
 *  Mirrors `SheetCell.display` on the server deliberately — this is the formula-bar
 *  convention (a spreadsheet shows you the expression, not last night's cached number),
 *  and the cached value is a snapshot that may already be stale. */
export function cellText(cell: SheetCellJson): string {
  if (cell.formula) return cell.formula
  if (cell.value === null) return ''
  return String(cell.value)
}

/** Read a typed entry out of what somebody typed into a cell.
 *
 *  **A leading `=` declares a formula, and here that is not sniffing.** The server refuses
 *  to guess because no human is present when a writer runs; in a grid the convention IS
 *  the interface — it is how every spreadsheet has worked for forty years, the user typed
 *  it deliberately, and the result is visible to them immediately. What makes it safe is
 *  that the guess is OVERRIDABLE: `asLiteral` turns the cell back into text, so a label
 *  like `=TBD` is reachable in two clicks rather than impossible.
 *
 *  Numbers and booleans are recovered so a typed `12` stays summable — a spreadsheet of
 *  text-formatted numbers is the thing the model went out of its way to prevent. */
export function parseEntry(text: string): Pick<SheetCellJson, 'value' | 'formula'> {
  if (text.startsWith('=') && text.length > 1) return { value: null, formula: text }
  if (text === '') return { value: null, formula: '' }
  const lowered = text.trim().toLowerCase()
  if (lowered === 'true' || lowered === 'false') return { value: lowered === 'true', formula: '' }
  // `Number('')` is 0 and `Number(' ')` is 0, so the emptiness check above has to come
  // first — otherwise a cell cleared to a space would silently become zero.
  const numeric = Number(text)
  if (text.trim() !== '' && Number.isFinite(numeric)) return { value: numeric, formula: '' }
  return { value: text, formula: '' }
}

/** Force a cell to hold TEXT — the override for a label that starts with `=`. */
export function asLiteral(cell: SheetCellJson): SheetCellJson {
  if (!cell.formula) return cell
  return { ...cell, value: cell.formula, formula: '' }
}

/** Force a cell to hold a FORMULA. Only meaningful when its text already looks like one,
 *  which is what `canBeFormula` reports, so the control can say why it is unavailable. */
export function asFormula(cell: SheetCellJson): SheetCellJson {
  if (cell.formula) return cell
  const text = cellText(cell)
  if (!canBeFormula(cell)) return cell
  return { ...cell, value: null, formula: text }
}

/** Whether `asFormula` would do anything: a formula has to start with `=`, so text that
 *  does not cannot become one without inventing an expression the user never wrote. */
export function canBeFormula(cell: SheetCellJson): boolean {
  const text = cellText(cell)
  return !cell.formula && text.startsWith('=') && text.length > 1
}

/** Replace one cell, leaving every other cell — and every sheet — untouched.
 *
 *  Row padding is deliberate: the grid renders the widest row's width, so a short row has
 *  addressable columns that do not exist in the data yet. Typing into one has to create
 *  it rather than silently do nothing. */
export function withCell(
  model: SheetModelJson,
  sheetIndex: number,
  row: number,
  col: number,
  next: SheetCellJson,
): SheetModelJson {
  return {
    sheets: model.sheets.map((sheet, s) => {
      if (s !== sheetIndex) return sheet
      const cells = sheet.cells.map((r, ri) => {
        if (ri !== row) return r
        const padded = r.length > col ? r.slice() : [...r, ...Array.from({ length: col + 1 - r.length }, emptyCell)]
        padded[col] = next
        return padded
      })
      return { ...sheet, cells }
    }),
  }
}

/** The cell at an address, or a blank one when the row is shorter than the grid is wide. */
export function cellAt(sheet: SheetJson, row: number, col: number): SheetCellJson {
  return sheet.cells[row]?.[col] ?? emptyCell()
}

/** The number-format presets the inspector offers, plus what each one means in words.
 *
 *  `''` is "Automatic" rather than Excel's `General`, matching the model: a cell with no
 *  format must not acquire one just by being looked at. */
export const NUMBER_FORMATS: { code: string; label: string }[] = [
  { code: '', label: 'Automatic' },
  { code: '0', label: 'Whole number (1235)' },
  { code: '#,##0.00', label: 'Number (1,234.50)' },
  { code: '0.0%', label: 'Percent (12.5%)' },
  { code: '$#,##0.00', label: 'Currency ($1,234.50)' },
  { code: '0.00E+00', label: 'Scientific (1.23E+03)' },
  { code: 'yyyy-mm-dd', label: 'Date (2026-08-26)' },
  { code: '@', label: 'Text' },
]

/** Preset options plus the loaded document's OWN format when it is not one of them.
 *
 *  Without this a sheet formatted `#,##0.00" kg"` would open showing "Automatic", and
 *  saving would strip a format the user never chose to remove — a control that silently
 *  misreports the document it loaded is worse than no control. */
export function formatOptions(current: string): { value: string; label: string }[] {
  const known = NUMBER_FORMATS.some((f) => f.code === current)
  const extra = known || !current ? [] : [{ value: current, label: `Custom (${current})` }]
  return [...NUMBER_FORMATS.map((f) => ({ value: f.code, label: f.label })), ...extra]
}
