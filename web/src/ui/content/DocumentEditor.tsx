/** The model editor — the non-Monaco renderer that `ContentSurface` mounts for an office
 *  document (DOCUMENT-FIDELITY-EDITOR §C4/§C5).
 *
 *  **It edits the MODEL, never the bytes.** It asks the server for a parsed
 *  `DocumentModelJson` + the parse's `LossReport`, edits that structure with the pure
 *  helpers in `documentModelEdit.ts`, and posts the structure back for the shipped writer
 *  to re-render. No OOXML is constructed, read, or even seen in the browser — the same
 *  reasoning that keeps vendor format strings inside `documents/writers/`.
 *
 *  **The lossy-edit contract is a MECHANISM here, not a notice.** Re-rendering a document
 *  can only emit what the model can hold, so anything the parse could not represent is
 *  gone the moment a save lands. This surface therefore:
 *
 *    1. refuses to hand over the controls at all until the loss report has been read and
 *       acknowledged — the report is not a banner beside a live editor, it is a gate in
 *       front of one, because a warning a user can type straight past is a warning that
 *       has already failed;
 *    2. repeats the same report inside the save confirmation, since the acknowledgement
 *       may be many minutes and many edits old by then;
 *    3. names the fact that the pre-edit version stays one revert away — which is what
 *       makes this recoverable rather than destructive, and is true because every model
 *       write bumps a version (§C3).
 *
 *  **A stale save is refused, not merged.** `If-Match` carries the version the editor
 *  loaded, so two tabs editing one document collide with a 409 and the second one is
 *  TOLD — with its draft intact — instead of silently overwriting the first.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Bold, Code, FileWarning, Italic, Loader2, Ruler, Save } from 'lucide-react'
import { api, ApiError, type DocumentBlock, type DocumentLossReport, type DocumentModelJson } from '../../lib/api'
import { Button } from '../Button'
import { Centered } from '../Centered'
import { InlineError } from '../InlineError'
import { MoreRow } from '../MoreRow'
import { confirm } from '../dialog'
import {
  applyMark,
  blockText,
  isTextBlock,
  pageOf,
  selectionHasMark,
  setBlockText,
  styleOf,
  withBlock,
  withPage,
  withStyle,
  type RunMark,
} from './documentModelEdit'
import { PageSetupControls, ParagraphLayoutControls } from './DocumentLayout'
import type { DocumentEditorProps } from './contentTypes'

/** A live text selection inside one block's field, in that block's own characters. */
interface Selection { block: number; start: number; end: number }

const MARKS: { mark: RunMark; label: string; icon: typeof Bold }[] = [
  { mark: 'bold', label: 'Bold', icon: Bold },
  { mark: 'italic', label: 'Italic', icon: Italic },
  { mark: 'code', label: 'Code', icon: Code },
]

const KIND_LABEL: Record<DocumentBlock['kind'], string> = {
  heading: 'Heading', paragraph: 'Paragraph', code: 'Code block',
  bullets: 'Bulleted list', numbered: 'Numbered list', table: 'Table',
  image: 'Image', pagebreak: 'Page break',
}

/** The loss report as prose a person can act on — used BOTH in the pre-edit gate and in
 *  the save confirmation, from one place, so the two can never drift apart. */
function LossList({ loss }: { loss: DocumentLossReport }) {
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

export function DocumentEditor({ slug, title, readOnly, onDirty }: DocumentEditorProps) {
  const [loaded, setLoaded] = useState<{ model: DocumentModelJson; loss: DocumentLossReport; version: number } | null>(null)
  const [model, setModel] = useState<DocumentModelJson | null>(null)
  const [loadError, setLoadError] = useState('')
  const [saveError, setSaveError] = useState('')
  const [saving, setSaving] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)
  const [sel, setSel] = useState<Selection | null>(null)
  // Which panels are open. Layout is per-block and opt-in: eight controls under every
  // paragraph at once buries the text the user came to edit, and the block a control
  // belongs to has to be unambiguous.
  const [pageOpen, setPageOpen] = useState(false)
  const [layoutOpen, setLayoutOpen] = useState<number | null>(null)
  const fields = useRef(new Map<number, HTMLTextAreaElement>())

  useEffect(() => {
    let alive = true
    setLoadError('')
    api.artifactModel(slug)
      .then((r) => {
        if (!alive) return
        setLoaded({ model: r.model, loss: r.loss, version: r.version })
        setModel(r.model)
        // A lossless document needs no acknowledgement — there is nothing to warn about,
        // and a ceremonial gate in front of a safe edit only teaches people to click past
        // the one that matters.
        setAcknowledged(r.loss.lossless)
      })
      .catch((e) => { if (alive) setLoadError(e instanceof Error ? e.message : String(e)) })
    return () => { alive = false }
  }, [slug])

  const dirty = !!model && !!loaded && model !== loaded.model
  useEffect(() => { onDirty?.(dirty) }, [dirty, onDirty])

  const editing = !readOnly && acknowledged
  // ONE sentence for why nothing can be typed, reused by every control that goes off — a
  // keyboard user who tabs onto a dead Bold button has to be able to learn what is missing.
  const blockedReason = readOnly
    ? 'This version is read-only — open the current version to edit it.'
    : !acknowledged
      ? 'Read the formatting notice above, then choose “edit anyway”.'
      : ''
  const blocks = model?.blocks ?? []

  const onFieldSelect = useCallback((index: number, el: HTMLTextAreaElement) => {
    const start = el.selectionStart ?? 0
    const end = el.selectionEnd ?? 0
    setSel(end > start ? { block: index, start, end } : null)
  }, [])

  const toggleMark = (mark: RunMark) => {
    if (!model || !sel) return
    const block = model.blocks[sel.block]
    if (!block) return
    const on = !selectionHasMark(block, sel.start, sel.end, mark)
    setModel(withBlock(model, sel.block, applyMark(block, sel.start, sel.end, mark, on)))
    // Keep the selection so a user can stack marks (bold THEN italic) without re-selecting;
    // the field lost nothing, only the runs under it changed.
    fields.current.get(sel.block)?.setSelectionRange(sel.start, sel.end)
  }

  const markActive = (mark: RunMark): boolean => {
    if (!model || !sel) return false
    const block = model.blocks[sel.block]
    return !!block && selectionHasMark(block, sel.start, sel.end, mark)
  }

  const save = async () => {
    if (!model || !loaded || saving) return
    if (!loaded.loss.lossless) {
      const ok = await confirm({
        title: `Save and re-render “${title}”?`,
        body: (
          <div className="space-y-2">
            <p className="text-[0.8125rem] text-on-surface">
              Saving re-creates the file from the structure below, so the constructs
              PersonalClaw cannot represent will not be in the saved copy:
            </p>
            <LossList loss={loaded.loss} />
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
      const res = await api.saveArtifactModel(slug, loaded.version, model)
      // Re-baseline on what the server accepted: the next save must carry the NEW version
      // or it would fail its own If-Match.
      setLoaded({ ...loaded, model, version: res.version })
    } catch (e) {
      const stale = e instanceof ApiError && e.status === 409
      setSaveError(
        stale
          ? 'This document changed somewhere else (another tab, or the agent) since you opened it. Your edits are still here — reopen the document to get the current version, then re-apply them.'
          : e instanceof Error ? e.message : String(e),
      )
    } finally {
      setSaving(false)
    }
  }

  const nonText = useMemo(() => blocks.filter((b) => !isTextBlock(b)).length, [blocks])

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

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* ── the pre-edit gate (§C5.1) ── */}
      {!loaded.loss.lossless && !acknowledged && (
        <div role="alert" className="border-b border-outline/40 p-l" style={{ background: 'color-mix(in srgb, var(--color-warning) 8%, transparent)' }}>
          <div className="flex items-start gap-2">
            <AlertTriangle size={16} className="mt-0.5 shrink-0 text-on-surface-var" aria-hidden="true" />
            <div className="min-w-0">
              <p className="text-[0.875rem] text-on-surface">Editing this document loses formatting</p>
              <p className="mt-1 text-[0.8125rem] text-on-surface-var">
                It contains things this editor’s document model cannot hold. Saving re-creates
                the file, so they will not be in the saved copy. The version you have now is
                kept and can be restored from Details › Versions.
              </p>
              <div className="mt-2"><LossList loss={loaded.loss} /></div>
              <Button size="xs" variant="tonal" className="mt-3" onClick={() => setAcknowledged(true)}>
                I understand — edit anyway
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* ── the formatting toolbar ── */}
      <div className="flex flex-wrap items-center gap-1 border-b border-outline/40 px-m py-1.5">
        {MARKS.map(({ mark, label, icon: Icon }) => (
          <Button key={mark} size="xs" variant="ghost" shape="squircle"
            disabled={!editing || !sel}
            disabledReason={blockedReason || `Select text in a paragraph, then ${label.toLowerCase()} it.`}
            ariaPressed={markActive(mark)}
            ariaLabel={label}
            title={`${label} the selected text`}
            onClick={() => toggleMark(mark)}>
            <Icon size={13} aria-hidden="true" />
          </Button>
        ))}
        <span className="ml-1 text-[0.75rem] text-on-surface-low">
          {sel ? 'Formats the selected text' : 'Select text in a paragraph to format it'}
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

      {saveError && <InlineError icon multiline className="mx-m mt-2" onDismiss={() => setSaveError('')}>{saveError}</InlineError>}

      {/* ── the blocks ── */}
      <div className="min-h-0 flex-1 overflow-y-auto p-l">
        <div className="mx-auto flex w-full max-w-[46rem] flex-col gap-3">
          {/* ── page layout (DFE-6 T3.1/T3.3/T3.4) ── */}
          <div className="rounded-lg border border-outline/40 bg-surface-container/30">
            <Button size="xs" variant="ghost" className="w-full justify-start"
              ariaExpanded={pageOpen}
              onClick={() => setPageOpen(!pageOpen)}>
              <Ruler size={13} aria-hidden="true" /> Page layout
            </Button>
            {pageOpen && (
              <div className="border-t border-outline/30 p-3">
                <PageSetupControls
                  page={pageOf(model)}
                  readOnly={!editing}
                  disabledReason={blockedReason}
                  onChange={(patch) => setModel(withPage(model, patch))} />
              </div>
            )}
          </div>

          {blocks.map((block, index) => {
            const label = KIND_LABEL[block.kind] ?? block.kind
            if (!isTextBlock(block)) {
              return (
                <div key={index} className="rounded-lg border border-outline/40 bg-surface-container/40 px-3 py-2 text-[0.8125rem] text-on-surface-var">
                  {label} — kept exactly as it was parsed. This editor does not change it.
                </div>
              )
            }
            const fieldId = `doc-block-${index}`
            const open = layoutOpen === index
            return (
              <div key={index}>
                <div className="flex items-center justify-between gap-2">
                  <label htmlFor={fieldId} className="block text-[0.75rem] uppercase tracking-wide text-on-surface-low">
                    {label}{block.kind === 'heading' ? ` ${block.level}` : ''}
                  </label>
                  <Button size="xs" variant="ghost" shape="squircle"
                    ariaExpanded={open}
                    ariaLabel={`Layout for this ${label.toLowerCase()}`}
                    title="Alignment, spacing and indents for this paragraph"
                    onClick={() => setLayoutOpen(open ? null : index)}>
                    <Ruler size={12} aria-hidden="true" />
                  </Button>
                </div>
                <textarea id={fieldId} rows={block.kind === 'paragraph' ? 3 : 2}
                  ref={(el) => { if (el) fields.current.set(index, el); else fields.current.delete(index) }}
                  className="mt-1 w-full resize-y rounded-lg border border-outline/40 bg-surface px-3 py-2 text-[0.875rem] text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-60"
                  value={blockText(block)}
                  readOnly={!editing}
                  disabled={!editing}
                  title={blockedReason || undefined}
                  onSelect={(e) => onFieldSelect(index, e.currentTarget)}
                  onKeyUp={(e) => onFieldSelect(index, e.currentTarget)}
                  onMouseUp={(e) => onFieldSelect(index, e.currentTarget)}
                  onChange={(e) => setModel(withBlock(model, index, setBlockText(block, e.target.value)))} />
                {open && (
                  <ParagraphLayoutControls
                    block={block}
                    style={styleOf(block)}
                    readOnly={!editing}
                    disabledReason={blockedReason}
                    onChange={(patch) => setModel(withStyle(model, index, patch))} />
                )}
              </div>
            )
          })}
          {nonText > 0 && (
            <p className="text-[0.75rem] text-on-surface-low">
              {nonText} block{nonText === 1 ? '' : 's'} of this document (tables, images, page
              breaks) are shown above but not editable here — they are written back unchanged.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
