/** The spreadsheet editor — a real grid of controlled inputs over a `SheetModel`.
 *
 *  Mounted by `<ContentSurface>` for an `.xlsx` artifact, in place of Monaco, through the
 *  same non-Monaco renderer slot the document editor uses (DOCUMENT-FIDELITY-EDITOR §C4).
 *
 *  **Structural, not an embedded spreadsheet.** This follows DFE-5's ratified decision
 *  (owner task 2, option (c)) for the same reason and with the same cost: a spreadsheet
 *  widget's whole value is owning its own grid model, so adopting one would mean a second
 *  representation of a workbook plus a lossy mapping to ours — exactly the second fidelity
 *  story the plan refuses. So: controlled `<input>`s in a real `<table>`, no new frontend
 *  dependency, and no recalculation engine.
 *
 *  **Formulas stay formulas, and are edited as formulas.** Nothing here evaluates
 *  anything. A cell showing `=SUM(B2:B9)` shows the expression, edits the expression, and
 *  saves the expression — the spreadsheet the user opens the file in is what computes it.
 *  A grid that displayed a stale cached number would be lying about what it is about to
 *  save. The entry convention (a leading `=` means formula) is the one every spreadsheet
 *  uses, and it is OVERRIDABLE both ways in the inspector, so a label like `=TBD` is
 *  reachable — which is the half of the defect a guess can never fix.
 *
 *  **The lossy-edit contract is a MECHANISM, not a notice** — same posture as
 *  `DocumentEditor`: while the parse's loss report is non-empty and unacknowledged, every
 *  input is disabled and every control carries a reason. A warning a user can type past
 *  has already failed. (Deliberately its own copy rather than a shared component; see this
 *  atom's execution-log entry — `DFE-6` is editing the document editor on an unmerged
 *  branch, and extracting a "shared" contract against code nobody can read is how two
 *  branches become a three-way conflict.)
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Bold, FileWarning, FunctionSquare, Italic, Loader2, Save, Type } from 'lucide-react'
import { api, ApiError, type DocumentLossReport, type SheetModelJson } from '../../lib/api'
import { Button } from '../Button'
import { Centered } from '../Centered'
import { InlineError } from '../InlineError'
import { MoreRow } from '../MoreRow'
import { EmptyState } from '../ListScaffold'
import { Segmented } from '../Segmented'
import { Select } from '../forms'
import { confirm } from '../dialog'
import {
  asFormula,
  asLiteral,
  canBeFormula,
  cellAt,
  cellRef,
  cellText,
  columnCount,
  columnLabel,
  formatOptions,
  parseEntry,
  withCell,
} from './sheetModelEdit'
import type { DocumentEditorProps } from './contentTypes'

/** The cell the inspector is pointed at. */
interface Cursor { sheet: number; row: number; col: number }

/** The parse's losses as prose a person can act on. Rendered in the pre-edit gate AND in
 *  the save confirmation from one place here, so this surface's two copies cannot drift. */
function SheetLossList({ loss }: { loss: DocumentLossReport }) {
  return (
    <div className="text-[0.8125rem]">
      <p className="text-on-surface">{loss.summary}</p>
      <ul className="mt-2 space-y-1 text-on-surface-var">
        {loss.items.slice(0, 12).map((item, i) => (
          <li key={`${item.kind}-${item.where}-${i}`}>
            <span className="text-on-surface">{item.kind}</span>
            {' · '}{item.where}{' — '}{item.detail}
          </li>
        ))}
      </ul>
      {/* The summary above states the FULL count, so a truncated list owes this line. */}
      <MoreRow total={loss.items.length} shown={12} noun="losses" />
    </div>
  )
}

export function SheetGrid({ slug, title, readOnly, onDirty }: DocumentEditorProps) {
  const [loaded, setLoaded] = useState<{ model: SheetModelJson; loss: DocumentLossReport; version: number } | null>(null)
  const [model, setModel] = useState<SheetModelJson | null>(null)
  const [loadError, setLoadError] = useState('')
  const [saveError, setSaveError] = useState('')
  const [saving, setSaving] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)
  const [sheetIndex, setSheetIndex] = useState(0)
  const [cursor, setCursor] = useState<Cursor | null>(null)
  // Identity-stable across renders so the effect below cannot become an unbounded fetch.
  const dirtyRef = useRef(onDirty)
  dirtyRef.current = onDirty

  useEffect(() => {
    let alive = true
    setLoadError('')
    api.artifactSheetModel(slug)
      .then((r) => {
        if (!alive) return
        setLoaded({ model: r.model, loss: r.loss, version: r.version })
        setModel(r.model)
        // A lossless workbook needs no acknowledgement — a ceremonial gate in front of a
        // safe edit only teaches people to click past the one that matters.
        setAcknowledged(r.loss.lossless)
      })
      .catch((e) => { if (alive) setLoadError(e instanceof Error ? e.message : String(e)) })
    return () => { alive = false }
  }, [slug])

  const dirty = !!model && !!loaded && model !== loaded.model
  useEffect(() => { dirtyRef.current?.(dirty) }, [dirty])

  const editing = !readOnly && acknowledged
  // ONE sentence for why nothing can be typed, reused by every control that goes off — a
  // keyboard user who tabs onto a dead Bold has to be able to learn what is missing.
  const blockedReason = readOnly
    ? 'This version is read-only — open the current version to edit it.'
    : !acknowledged
      ? 'Read the formatting notice above, then choose “edit anyway”.'
      : ''

  const sheet = model?.sheets[sheetIndex] ?? null
  const width = useMemo(() => (sheet ? columnCount(sheet) : 0), [sheet])
  const active = sheet && cursor && cursor.sheet === sheetIndex ? cellAt(sheet, cursor.row, cursor.col) : null

  const patch = useCallback((next: Parameters<typeof withCell>[4]) => {
    if (!model || !cursor) return
    setModel(withCell(model, cursor.sheet, cursor.row, cursor.col, next))
  }, [model, cursor])

  const save = async () => {
    if (!model || !loaded || saving) return
    if (!loaded.loss.lossless) {
      const ok = await confirm({
        title: `Save and re-render “${title}”?`,
        body: (
          <div className="space-y-2">
            <p className="text-[0.8125rem] text-on-surface">
              Saving re-creates the workbook from the cells below, so the things
              PersonalClaw cannot represent will not be in the saved copy:
            </p>
            <SheetLossList loss={loaded.loss} />
            <p className="text-[0.8125rem] text-on-surface-var">
              Version {loaded.version} is kept — you can restore it from Details › Versions
              at any time.
            </p>
          </div>
        ),
        danger: true,
        confirmLabel: 'Save and re-render',
        icon: FileWarning,
      })
      if (!ok) return
    }
    setSaving(true)
    setSaveError('')
    try {
      const res = await api.saveArtifactSheetModel(slug, loaded.version, model)
      // Re-baseline on what the server accepted: the next save must carry the NEW version
      // or it would fail its own If-Match.
      setLoaded({ ...loaded, model, version: res.version })
    } catch (e) {
      const stale = e instanceof ApiError && e.status === 409
      setSaveError(
        stale
          ? 'This workbook changed somewhere else (another tab, or the agent) since you opened it. Your edits are still here — reopen it to get the current version, then re-apply them.'
          : e instanceof Error ? e.message : String(e),
      )
    } finally {
      setSaving(false)
    }
  }

  if (loadError) {
    return (
      <div className="p-l">
        <InlineError icon multiline>Couldn’t read {title}: {loadError}</InlineError>
      </div>
    )
  }
  if (!model || !loaded) {
    return <Centered><Loader2 size={18} className="animate-spin text-on-surface-low" /></Centered>
  }
  if (!sheet || !model.sheets.length) {
    return (
      <EmptyState
        icon={FunctionSquare}
        title="This workbook has no sheets"
        hint="There is nothing to edit yet. Add a sheet in a spreadsheet app and re-upload, or ask the agent to generate one."
      />
    )
  }

  const formatLabel = `Number format for ${cellRef(sheet, cursor?.row ?? 0, cursor?.col ?? 0)}`

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* ── the pre-edit gate ── */}
      {!loaded.loss.lossless && !acknowledged && (
        <div role="alert" className="border-b border-outline/40 p-l" style={{ background: 'color-mix(in srgb, var(--color-warning) 8%, transparent)' }}>
          <div className="flex items-start gap-2">
            <AlertTriangle size={16} className="mt-0.5 shrink-0 text-on-surface-var" aria-hidden="true" />
            <div className="min-w-0">
              <p className="text-[0.875rem] text-on-surface">Editing this workbook loses formatting</p>
              <p className="mt-1 text-[0.8125rem] text-on-surface-var">
                It contains things this editor’s sheet model cannot hold. Saving re-creates
                the file, so they will not be in the saved copy. The version you have now is
                kept and can be restored from Details › Versions.
              </p>
              <div className="mt-2"><SheetLossList loss={loaded.loss} /></div>
              <Button size="xs" variant="tonal" className="mt-3" onClick={() => setAcknowledged(true)}>
                I understand — edit anyway
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* ── the cell inspector: what the selected cell IS, and how it is formatted ── */}
      <div className="flex flex-wrap items-center gap-2 border-b border-outline/40 px-m py-1.5">
        <span className="min-w-[5.5rem] font-mono text-[0.75rem] text-on-surface-var">
          {cursor ? cellRef(sheet, cursor.row, cursor.col) : '—'}
        </span>
        <Button size="xs" variant="ghost" shape="squircle"
          disabled={!editing || !active || !active.formula}
          disabledReason={blockedReason || 'Select a formula cell to turn it back into plain text.'}
          ariaLabel="Treat as text"
          title="Keep the leading = as a literal label instead of a formula"
          onClick={() => active && patch(asLiteral(active))}>
          <Type size={13} aria-hidden="true" />
        </Button>
        <Button size="xs" variant="ghost" shape="squircle"
          disabled={!editing || !active || !canBeFormula(active)}
          disabledReason={blockedReason || 'A formula has to start with “=”. Type one first, then mark it.'}
          ariaLabel="Treat as formula"
          title="Store this cell as a formula the spreadsheet will calculate"
          onClick={() => active && patch(asFormula(active))}>
          <FunctionSquare size={13} aria-hidden="true" />
        </Button>
        {([['bold', Bold, 'Bold'], ['italic', Italic, 'Italic']] as const).map(([field, Icon, label]) => (
          <Button key={field} size="xs" variant="ghost" shape="squircle"
            disabled={!editing || !active}
            disabledReason={blockedReason || 'Select a cell first.'}
            ariaPressed={!!active?.[field]}
            ariaLabel={label}
            title={`${label} the selected cell`}
            onClick={() => active && patch({ ...active, [field]: !active[field] })}>
            <Icon size={13} aria-hidden="true" />
          </Button>
        ))}
        <label className="flex items-center gap-1.5 text-[0.75rem] text-on-surface-low">
          <span id="sheet-format-label">Format</span>
          <Select value={active?.number_format ?? ''}
            options={formatOptions(active?.number_format ?? '')}
            disabled={!editing || !active}
            disabledReason={blockedReason || 'Select a cell first, then choose how its value is displayed.'}
            ariaLabel={formatLabel}
            onChange={(code) => active && patch({ ...active, number_format: code })} />
        </label>
        <span className="text-[0.75rem] text-on-surface-low">
          {active?.formula
            ? 'Formula — saved as written; your spreadsheet calculates it.'
            : cursor ? 'Start a cell with “=” to make it a formula.' : 'Select a cell to format it.'}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {dirty && <span className="text-[0.75rem] text-on-surface-low">Unsaved changes</span>}
          <Button size="xs" variant="primary" shape="squircle" loading={saving}
            disabled={!editing || !dirty}
            disabledReason={blockedReason || 'No changes to save yet.'}
            onClick={() => void save()}>
            <Save size={13} aria-hidden="true" /> Save
          </Button>
        </div>
      </div>

      {/* ── the sheet tabs ── */}
      {model.sheets.length > 1 && (
        <div className="border-b border-outline/40 px-m py-1">
          <Segmented
            size="sm"
            collapse="scroll"
            ariaLabel="Sheets"
            value={String(sheetIndex)}
            options={model.sheets.map((s, i) => ({ key: String(i), label: s.name || `Sheet ${i + 1}` }))}
            onChange={(key) => { setSheetIndex(Number(key)); setCursor(null) }} />
        </div>
      )}

      {saveError && <InlineError icon multiline className="mx-m mt-2" onDismiss={() => setSaveError('')}>{saveError}</InlineError>}

      {/* ── the grid ── */}
      <div className="min-h-0 flex-1 overflow-auto p-m">
        <table className="border-separate border-spacing-0 text-[0.8125rem]">
          <caption className="sr-only">
            {sheet.name} — {sheet.cells.length} rows by {width} columns. Cells holding a
            formula show the formula, not a calculated result.
          </caption>
          <thead>
            <tr>
              <th scope="col" className="sticky left-0 z-10 bg-surface px-2 py-1 text-on-surface-low">
                <span className="sr-only">Row</span>
              </th>
              {Array.from({ length: width }, (_, col) => (
                <th key={col} scope="col" className="border-b border-outline/40 bg-surface-container/40 px-2 py-1 font-medium text-on-surface-var">
                  {columnLabel(col)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sheet.cells.map((_row, rowIndex) => (
              <tr key={rowIndex}>
                <th scope="row" className="sticky left-0 z-10 border-r border-outline/40 bg-surface-container/40 px-2 py-1 text-right font-normal text-on-surface-low">
                  {rowIndex + 1}
                </th>
                {Array.from({ length: width }, (_, col) => {
                  const cell = cellAt(sheet, rowIndex, col)
                  const selected = cursor?.sheet === sheetIndex && cursor.row === rowIndex && cursor.col === col
                  return (
                    <td key={col} className="border-b border-r border-outline/20 p-0">
                      <input
                        type="text"
                        value={cellText(cell)}
                        readOnly={!editing}
                        disabled={!editing}
                        title={blockedReason || undefined}
                        // The header names the column and the row header names the row, but
                        // neither is a programmatic name for the INPUT, so a screen reader
                        // would announce an unlabelled text box. The ref is the name.
                        aria-label={cellRef(sheet, rowIndex, col)}
                        className={`w-[9rem] bg-transparent px-2 py-1 text-on-surface tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary disabled:opacity-60 ${selected ? 'ring-2 ring-inset ring-primary/50' : ''} ${cell.bold ? 'font-semibold' : ''} ${cell.italic ? 'italic' : ''} ${cell.formula ? 'text-primary' : ''}`}
                        style={{ textAlign: (cell.align || 'left') as 'left' | 'center' | 'right' }}
                        onFocus={() => setCursor({ sheet: sheetIndex, row: rowIndex, col })}
                        onChange={(e) => {
                          setCursor({ sheet: sheetIndex, row: rowIndex, col })
                          setModel(withCell(model, sheetIndex, rowIndex, col, { ...cell, ...parseEntry(e.target.value) }))
                        }} />
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-3 text-[0.75rem] text-on-surface-low">
          Formulas are saved exactly as written — this editor does not calculate them, so a
          cell shows its formula rather than a result. Charts, pivot tables and conditional
          formatting are listed above if present; they are not carried through a save.
        </p>
      </div>
    </div>
  )
}
