import { lazy, Suspense, useEffect, useId, useImperativeHandle, useRef, useState, forwardRef, createElement, type ReactNode } from 'react'
import { motion } from 'framer-motion'
import { Save, RotateCcw, Eye, Code2, Columns2, WrapText, Copy, Check, Loader2, FileWarning, Download, type LucideIcon } from 'lucide-react'
import { spring } from '../../design/motion'
import { useMode } from '../../app/theme'
import { CommentLayer } from '../../pages/files/comments/CommentLayer'
import type { CommentTarget } from './commentTarget'
import { type ContentType, isEditable, isCommentable } from './contentTypes'

const MonacoEditor = lazy(() => import('@monaco-editor/react'))

export interface ContentSurfaceHandle { save: () => void }

/** A host-supplied edit-mode action beside Save — e.g. an artifact's "Snapshot"
 *  (save as a new immutable version). Receives the live draft; the surface keeps
 *  ownership of edit/draft state, the host just decides what a variant persist
 *  means. Rendered only when the surface is editable. */
export interface ContentAction {
  icon: LucideIcon
  label: string
  title?: string
  /** Highlighted (primary tone) vs ghost. */
  primary?: boolean
  run: (draft: string) => void | Promise<void>
}

interface ContentSurfaceProps {
  /** The resolved content type — declares preview/edit/security/commentable. */
  type: ContentType
  /** The current source content (string for all text/markup types). */
  content: string
  /** Display title (header + iframe title + comment doc label). */
  title: string
  /** Document id for the comment layer (artifact slug or file path). */
  docId: string
  /** Real file/source path (drives some renderers + comment file-attach). */
  path?: string
  /** Read-only: no edit affordances regardless of the type's edit capability
   *  (historical artifact version, truncated file). */
  readOnly?: boolean
  /** Persist an edit. Omit to make the surface preview-only even for an
   *  editable type (e.g. a chat inline render). Returns when the save settles. */
  onSave?: (draft: string) => void | Promise<void>
  /** Comment routing target — when set AND the type is commentable, the
   *  selection→comment layer is enabled and routes here. */
  commentTarget?: CommentTarget
  /** Compact host (narrow chat side panel): icon-only toolbar. */
  compact?: boolean
  /** Initial view. Defaults to 'preview' when previewable, else 'edit'. */
  initialView?: 'preview' | 'edit' | 'split'
  /** Host-owned per-id draft cache so an unsaved edit survives unmount
   *  (multi-tab hosts mount only the active tab). `warned` carries the
   *  concurrent-edit flag so a remount can re-raise it (the watch that sets it
   *  doesn't run while unmounted). */
  draftStore?: Map<string, { draft: string; base: string; warned?: boolean }>
  /** The content was truncated (large file head only) → read-only, no save. */
  truncated?: boolean
  /** Extra edit-mode persist actions beside Save (e.g. artifact "Snapshot"). */
  actions?: ContentAction[]
  /** Notified whenever the dirty (unsaved-edit) state flips — a multi-tab host
   *  uses it to mark the tab. */
  onDirtyChange?: (dirty: boolean) => void
  /** Notified on every draft edit with the live draft + dirty — lets a file host
   *  compare it against incoming disk content to raise a concurrent-edit warning. */
  onDraftChange?: (draft: string, dirty: boolean) => void
  /** A gate run before persisting (return false to abort) — the file host uses it
   *  for the "file changed on disk, overwrite anyway?" confirm. */
  confirmSave?: () => boolean | Promise<boolean>
  /** Monaco language override. The registry's `edit.language` is a static default;
   *  a file host that knows the real per-extension language (via monacoLang) passes
   *  it here so a `.py`/`.go`/`.rs` opens with correct highlighting. */
  language?: string
  /** Host chrome at the START of the toolbar (filename, size, badges). */
  headerLeft?: ReactNode
  /** Host actions just before Revert/Save (download, save-as-artifact). Always
   *  shown (not edit-gated). */
  headerExtras?: ReactNode
  /** A banner rendered under the toolbar (e.g. concurrent-edit warning, save error). */
  banner?: ReactNode
}

/** The ONE type-aware render/edit surface. Resolves a `ContentType` to its
 *  declared preview + edit renderers and owns the edit↔preview↔split toggle,
 *  dirty/draft state, the comment layer, and proportional scroll-sync — lifted
 *  from the former FileViewer (the best of the three dispatchers) and generalized
 *  so artifacts, files, and chat embeds all inherit the same affordances.
 *
 *  Edit and preview are composed siblings under one shell + shared state, NOT a
 *  forced single abstraction (Monaco and an iframe share nothing internally). */
export const ContentSurface = forwardRef<ContentSurfaceHandle, ContentSurfaceProps>(function ContentSurface(
  { type, content, title, docId, path, readOnly, onSave, commentTarget, compact = false, initialView, draftStore, truncated, actions,
    onDirtyChange, onDraftChange, confirmSave, language, headerLeft, headerExtras, banner }, ref,
) {
  const { mode } = useMode()
  const previewScrollRef = useRef<HTMLDivElement | null>(null)
  const editorRef = useRef<any>(null)
  const splitPreviewRef = useRef<HTMLDivElement | null>(null)
  const syncingRef = useRef<'editor' | 'preview' | null>(null)
  // The baseline the current draft was seeded from — lets a content change tell
  // "disk moved under an unedited view" (follow it) from "user is editing" (keep).
  const draftBaseRef = useRef(content)

  const editable = isEditable(type) && !readOnly && !truncated && !!onSave
  const previewable = !!type.preview
  const splittable = editable && previewable && !!type.edit?.split

  // Seed draft from the host cache (survives tab-switch unmount) else from content.
  const [draft, setDraft] = useState(() => draftStore?.get(docId)?.draft ?? content)
  const [saving, setSaving] = useState(false)
  const [copied, setCopied] = useState(false)
  const [view, setView] = useState<'preview' | 'edit' | 'split'>(
    initialView ?? (previewable ? 'preview' : 'edit'),
  )
  const [wrap, setWrap] = useState<boolean>(() => {
    try { return localStorage.getItem('editor-wrap') !== 'off' } catch { return true }
  })
  useEffect(() => { try { localStorage.setItem('editor-wrap', wrap ? 'on' : 'off') } catch { /* quota */ } }, [wrap])

  // Reconcile draft against incoming props. Two cases, kept distinct:
  //  - DOC IDENTITY change (new artifact version / file / tab): full re-seed from
  //    the host draft cache, else the content — any prior draft was the old doc's.
  //  - same doc, BASELINE moved (a live file-watch refresh rewrote `content`):
  //    follow disk only if the user isn't mid-edit; otherwise preserve their
  //    unsaved draft so a background rewrite can't silently clobber it.
  const docIdRef = useRef(docId)
  useEffect(() => {
    if (docIdRef.current !== docId) {
      docIdRef.current = docId
      setDraft(draftStore?.get(docId)?.draft ?? content)
      setView((v) => (previewable ? v : 'edit'))
    } else {
      setDraft((d) => (d === draftBaseRef.current ? content : d))
    }
    draftBaseRef.current = content
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId, content])

  const dirty = editable && draft !== content
  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])
  useEffect(() => { onDraftChange?.(draft, dirty) }, [draft, dirty, onDraftChange])
  // Mirror the unsaved draft into the host cache so it survives unmount; clear when clean.
  useEffect(() => {
    if (!draftStore) return
    if (dirty) draftStore.set(docId, { draft, base: content })
    else draftStore.delete(docId)
  }, [draftStore, docId, draft, content, dirty])

  const save = async () => {
    if (!dirty || saving || !onSave) return
    if (confirmSave && !(await confirmSave())) return
    setSaving(true)
    try { await onSave(draft) }
    finally { setSaving(false) }
  }
  useImperativeHandle(ref, () => ({ save }))

  const runAction = async (action: ContentAction) => {
    if (saving) return
    setSaving(true)
    try { await action.run(draft) }
    finally { setSaving(false) }
  }

  const copy = () => { navigator.clipboard?.writeText(draft).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }).catch(() => {}) }

  const [exportOpen, setExportOpen] = useState(false)
  const exports = type.exports ?? []

  const commentsOn = !!commentTarget && isCommentable(type)
  // Per-surface layoutId so the view-toggle's sliding pill can't fly between two
  // ContentSurfaces mounted at once (e.g. two chat embeds).
  const viewToggleId = `content-view-${useId()}`

  const PreviewEl = type.preview?.render
  function renderPreview() {
    if (!PreviewEl) return null
    return createElement(PreviewEl, { content: draft, mode, title, path, streaming: false })
  }

  function renderEditor(split = false) {
    return (
      <Suspense fallback={<Centered><Loader2 size={18} className="animate-spin text-on-surface-low" /></Centered>}>
        <MonacoEditor height="100%" path={path} language={language || type.edit?.language || 'plaintext'} value={draft}
          onChange={(v) => setDraft(v ?? '')} theme={mode === 'light' ? 'light' : 'vs-dark'}
          onMount={split ? (ed: any) => { editorRef.current = ed; ed?.onDidScrollChange?.(syncFromEditor) } : undefined}
          options={{ readOnly: !editable, fontSize: 13, minimap: { enabled: view !== 'split' }, scrollBeyondLastLine: false, wordWrap: wrap ? 'on' : 'off', lineNumbers: 'on', automaticLayout: true, padding: { top: 10, bottom: 10 }, tabSize: 2, renderWhitespace: 'selection' }} />
      </Suspense>
    )
  }

  // Bidirectional proportional scroll-sync for split view (latched against echo).
  function syncFromEditor() {
    if (syncingRef.current === 'preview') { syncingRef.current = null; return }
    const ed = editorRef.current, pv = splitPreviewRef.current
    if (!ed || !pv) return
    const edMax = ed.getScrollHeight() - ed.getLayoutInfo().height
    if (edMax <= 0) return
    syncingRef.current = 'editor'
    pv.scrollTop = (ed.getScrollTop() / edMax) * (pv.scrollHeight - pv.clientHeight)
    requestAnimationFrame(() => { if (syncingRef.current === 'editor') syncingRef.current = null })
  }
  function syncFromPreview() {
    if (syncingRef.current === 'editor') { syncingRef.current = null; return }
    const ed = editorRef.current, pv = splitPreviewRef.current
    if (!ed || !pv) return
    const pvMax = pv.scrollHeight - pv.clientHeight
    if (pvMax <= 0) return
    syncingRef.current = 'preview'
    ed.setScrollTop((pv.scrollTop / pvMax) * (ed.getScrollHeight() - ed.getLayoutInfo().height))
    requestAnimationFrame(() => { if (syncingRef.current === 'preview') syncingRef.current = null })
  }

  // The toolbar shows if there's anything to put in it.
  const showToolbar = splittable || editable || previewable || !!headerLeft || !!headerExtras || exports.length > 0

  return (
    <div className="flex h-full flex-col">
      {/* toolbar — host chrome (left) + view toggle + edit/host actions (right) */}
      {showToolbar && (
        <div className={`flex items-center gap-s border-b border-outline/40 px-m py-1.5 ${compact ? 'flex-wrap' : ''}`}>
          {headerLeft}
          {truncated && <span className="shrink-0 rounded px-1.5 py-0.5 text-[0.65rem] text-on-surface-low" style={{ background: 'var(--color-surface-high)' }} title="Only the first part of this large file was loaded — read-only so a save can't truncate the rest.">truncated · read-only</span>}
          {dirty && <span className="size-1.5 shrink-0 rounded-full" style={{ background: 'var(--color-primary)' }} title="Unsaved changes" />}
          <div className="ml-auto flex items-center gap-1">
            {/* view toggle — shown for any editable+previewable type so there's
                always a way into edit mode; Split only when the type supports it */}
            {editable && previewable && (
              <div className="mr-1 inline-flex rounded-pill bg-surface-container p-0.5">
                <ToggleBtn icon={Eye} label="Preview" on={view === 'preview'} onClick={() => setView('preview')} compact={compact} indicatorId={viewToggleId} />
                {splittable && <ToggleBtn icon={Columns2} label="Split" on={view === 'split'} onClick={() => setView('split')} compact={compact} indicatorId={viewToggleId} />}
                <ToggleBtn icon={Code2} label="Edit" on={view === 'edit'} onClick={() => setView('edit')} compact={compact} indicatorId={viewToggleId} />
              </div>
            )}
            {view !== 'preview' && editable && (
              <>
                <IconBtn icon={WrapText} label="Toggle word wrap" active={wrap} onClick={() => setWrap((w) => !w)} />
                <IconBtn icon={copied ? Check : Copy} label="Copy contents" onClick={copy} />
              </>
            )}
            {headerExtras}
            {exports.length > 0 && (
              <div className="relative">
                <IconBtn icon={Download} label="Export" onClick={() => setExportOpen((v) => !v)} />
                {exportOpen && (
                  <>
                    <div className="fixed inset-0 z-40" onClick={() => setExportOpen(false)} />
                    <div className="absolute right-0 z-50 mt-1 min-w-[10rem] overflow-hidden rounded-md border border-outline-variant/50 bg-surface shadow-lg">
                      {exports.map((ex) => (
                        <button key={ex.id} type="button"
                          onClick={() => { setExportOpen(false); void ex.run(draft, title) }}
                          className="block w-full px-3 py-1.5 text-left text-[0.78rem] text-on-surface hover:bg-surface-high">{ex.label}</button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}
            {editable && (
              <>
                <button onClick={() => setDraft(content)} disabled={!dirty} type="button"
                  className="inline-flex size-7 items-center justify-center rounded-md text-on-surface-low hover:bg-surface-high hover:text-on-surface disabled:opacity-40" title="Revert unsaved changes"><RotateCcw size={13} /></button>
                <button onClick={save} disabled={!dirty || saving} type="button"
                  className="inline-flex items-center gap-1 rounded-md px-2.5 h-7 text-[0.75rem] disabled:opacity-40"
                  style={{ background: dirty ? 'var(--color-primary)' : 'var(--color-surface-high)', color: dirty ? 'var(--color-on-primary)' : 'var(--color-on-surface-low)' }} title="Save (⌘S)">
                  {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />} {!compact && 'Save'}
                </button>
                {actions?.map((a) => (
                  <button key={a.label} onClick={() => runAction(a)} disabled={saving} type="button"
                    className="inline-flex items-center gap-1 rounded-md px-2.5 h-7 text-[0.75rem] disabled:opacity-40"
                    style={a.primary ? { background: 'var(--color-primary)', color: 'var(--color-on-primary)' } : { color: 'var(--color-on-surface-low)' }}
                    title={a.title || a.label}>
                    {saving ? <Loader2 size={13} className="animate-spin" /> : <a.icon size={13} />} {!compact && a.label}
                  </button>
                ))}
              </>
            )}
          </div>
        </div>
      )}

      {banner}

      {/* body */}
      <div className="min-h-0 flex-1 overflow-hidden">
        {view === 'preview' && previewable ? (
          <div ref={previewScrollRef} className="relative h-full overflow-auto">
            {renderPreview()}
            {commentsOn && (
              <CommentLayer scrollRef={previewScrollRef} docId={docId} docLabel={title} docPath={path}
                content={draft} onSubmit={(message, docPaths) => commentTarget!.submit({ message, docPaths })} />
            )}
          </div>
        ) : view === 'split' && previewable ? (
          <div className="grid h-full grid-cols-1 lg:grid-cols-2">
            <div className="min-h-0 border-b border-outline/40 lg:border-b-0 lg:border-r">{renderEditor(true)}</div>
            <div ref={splitPreviewRef} onScroll={syncFromPreview} className="min-h-0 overflow-auto">{renderPreview()}</div>
          </div>
        ) : (
          <div className="h-full">{renderEditor()}</div>
        )}
      </div>
    </div>
  )
})

function ToggleBtn({ icon: Icon, label, on, onClick, compact, indicatorId }: { icon: typeof Eye; label: string; on: boolean; onClick: () => void; compact?: boolean; indicatorId: string }) {
  return (
    <button onClick={onClick} type="button" title={compact ? label : undefined} aria-label={label}
      className={`relative inline-flex items-center gap-1 rounded-pill h-6 text-[0.7rem] transition-colors ${compact ? 'px-2' : 'px-2.5'}`}
      style={{ color: on ? 'var(--color-on-surface)' : 'var(--color-on-surface-low)' }}>
      {/* liquid active pill — slides between Preview/Split/Edit via layoutId (the
          Segmented pattern) instead of the highlight blink-jumping. */}
      {on && <motion.span layoutId={indicatorId} transition={spring.spatialFast}
        className="absolute inset-0 rounded-pill" style={{ background: 'var(--color-surface-highest)' }} />}
      <Icon size={12} className="relative" /> {!compact && <span className="relative">{label}</span>}
    </button>
  )
}

function IconBtn({ icon: Icon, label, active, onClick }: { icon: typeof Copy; label: string; active?: boolean; onClick: () => void }) {
  return (
    <motion.button onClick={onClick} type="button" title={label} aria-label={label}
      whileTap={{ scale: 0.9 }} transition={spring.spatialFast}
      className="inline-flex size-7 items-center justify-center rounded-md hover:bg-surface-high"
      style={{ color: active ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }}><Icon size={13} /></motion.button>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full items-center justify-center">{children}</div>
}

// FileWarning is imported for parity with the donor shell's error affordances,
// re-exported so callers that compose their own error states can reuse it.
export { FileWarning }
