import { useEffect, useImperativeHandle, useRef, useState, forwardRef } from 'react'
import { Download, Loader2, BookmarkPlus, FileWarning, RotateCcw, FolderOpen } from 'lucide-react'
import { api, type FsEntry } from '../../../lib/api'
import { useIsMac } from '../../../app/usePlatform'
import { confirm } from '../../../ui/dialog'
import { fmtBytes, baseName, monacoLang } from '../fileMeta'
import { useFileWatch } from './useFileWatch'
import { ContentSurface, type ContentSurfaceHandle } from '../../../ui/content/ContentSurface'
import { resolveContentType, getContentType } from '../../../ui/content/contentTypes'
import type { CommentTarget } from '../../../ui/content/commentTarget'

export interface FileViewerHandle { save: () => void }

interface ViewerProps {
  entry: FsEntry
  /** Fired after a successful save; receives the saved content so a host can keep its
   *  own per-file baseline (e.g. the cockpit's diff-reveal last-seen map) accurate. */
  onSaved: (content?: string) => void
  onSaveAsArtifact: (entry: FsEntry, content: string) => void
  onDirtyChange?: (dirty: boolean) => void
  // An optional host-owned per-path draft cache. A multi-tab host (the Code cockpit)
  // mounts only the ACTIVE tab's FileViewer, so switching tabs unmounts the editor and
  // would lose an unsaved edit on the tab left behind. With a draftStore, the surface
  // seeds from a cached draft on mount (instead of disk) and writes the draft back on
  // every edit — so a dirty tab's content survives a switch-away-and-back. Cleared on a
  // successful save / revert. Single-host surfaces (Files page) omit it → unchanged.
  // We cache the disk `base` the draft was edited against + whether a disk-change was
  // already flagged, so the concurrent-edit warning (below) can be reconstructed on
  // remount — the live-watch that normally raises it doesn't run while unmounted.
  draftStore?: Map<string, { draft: string; base: string; warned?: boolean }>
  // compact = narrow host (the chat side panel): icon-only buttons + a wrapping
  // action bar so the toolbar doesn't crowd. The Files-page column stays roomy.
  compact?: boolean
  // Where selection-comments route (new chat session on the Files page, the SAME
  // session inside a chat). When omitted, the comment layer is disabled.
  commentTarget?: CommentTarget
  // The file is gone on disk (read returned 404) — e.g. a restored tab pointing at a
  // file removed since last session, or one the worker deleted. A multi-tab host wires
  // this to close the stale tab instead of stranding a ghost tab + a console 404.
  // Omitted on single-file hosts (they just show the load-failure placeholder).
  onMissing?: (path: string) => void
}

/** Loads a single file, then hands its content to <ContentSurface> for render +
 *  edit. This component owns only the FILE concerns the surface can't know about:
 *  reading bytes, the live disk-watch + concurrent-edit warning, binary/missing/
 *  truncated detection, and save-as-artifact. Everything visual (preview/edit/
 *  split/comments/scroll-sync) is the shared engine. */
export const FileViewer = forwardRef<FileViewerHandle, ViewerProps>(function FileViewer(
  { entry, onSaved, onSaveAsArtifact, onDirtyChange, draftStore, compact = false, commentTarget, onMissing }, ref,
) {
  const surfaceRef = useRef<ContentSurfaceHandle>(null)
  const isMac = useIsMac()
  // Resolve the registered type. A source-code file (.py/.go/.rs/.ts/…) has no
  // richer registered type and would fall through to `text` (which opens in a
  // <pre> PREVIEW first) — but a code file should open straight in the editor with
  // correct highlighting, as the old FileViewer did. So when the fallback would be
  // text/code AND monacoLang recognizes a real language, use the edit-only `code`
  // type + pass that language through. (Registry stays source-of-truth; the host
  // supplies the per-extension Monaco language the static registry can't enumerate.)
  const lang = monacoLang(entry.name)
  const resolved = resolveContentType({ name: entry.name })
  const isCodeFile = (resolved.id === 'text') && lang !== 'plaintext'
  const type = isCodeFile ? (getContentType('code') ?? resolved) : resolved
  // Image/PDF render by PATH (binary, fetched raw) — the surface's preview handles
  // it; we just skip the text read for them.
  const isBinaryType = type.id === 'image' || type.id === 'pdf'

  // Hold onMissing in a ref so the content-load effect can call the latest handler
  // WITHOUT listing it as a dependency (hosts pass inline arrows; a fresh identity
  // each render would re-fire the load effect and empty the open editor).
  const onMissingRef = useRef(onMissing)
  onMissingRef.current = onMissing

  const [content, setContent] = useState<string | null>(null)
  const [draft, setDraft] = useState('')          // mirrored from the surface, for save-as-artifact + watch compare
  const [truncated, setTruncated] = useState(false)
  // Server-detected binary (NUL bytes) for a file whose extension ISN'T a known image/
  // pdf — e.g. .pyc/.so/.db/no-ext executables. Without this they'd render as mojibake.
  const [detectedBinary, setDetectedBinary] = useState(false)
  const [loading, setLoading] = useState(!isBinaryType)
  const [err, setErr] = useState('')
  // Bumped by the load-failure "Try again" so a transient read failure can be retried.
  const [attempt, setAttempt] = useState(0)
  // The file changed ON DISK while the user had UNSAVED edits — set by the live-watch.
  // Without surfacing this, the user's next Save would silently overwrite the newer
  // version with their stale draft. We warn + gate the save on "overwrite anyway".
  const [diskChanged, setDiskChanged] = useState(false)

  useEffect(() => {
    if (isBinaryType) { setContent(null); setLoading(false); return }
    let alive = true
    setLoading(true); setErr(''); setDetectedBinary(false)
    api.fileRead(entry.path, true).then((r) => {
      if (!alive) return
      if (r.binary) { setDetectedBinary(true); setContent(''); setDraft(''); setLoading(false); return }
      const cached = draftStore?.get(entry.path)
      setContent(r.content); setDraft(cached ? cached.draft : r.content); setTruncated(r.truncated); setLoading(false)
      // Reconstruct the concurrent-edit warning the watch can't raise while unmounted:
      // a cached draft edited against a base that no longer matches disk = rewritten
      // under the edit; re-flag so Save still gates, or carry a flag set earlier.
      if (cached) setDiskChanged(cached.warned || cached.base !== r.content)
      else setDiskChanged(false)
    }).catch((e) => {
      if (!alive) return
      // 404 = gone on disk → tell the host to close the dead tab; transient errors
      // fall through to the retryable placeholder.
      if ((e as { status?: number }).status === 404) onMissingRef.current?.(entry.path)
      setErr(String(e.message || e)); setLoading(false)
    })
    return () => { alive = false }
  }, [entry.path, isBinaryType, attempt])

  // Live-watch: refresh the baseline on disk change. The surface preserves an
  // unsaved draft across a baseline change; here we only need to raise the
  // concurrent-edit flag when the new disk content diverges from the user's draft.
  useFileWatch(entry.path, !isBinaryType, (next) => {
    setContent(next)
    setDraft((d) => { if (content !== null && d !== content && d !== next) setDiskChanged(true); return d })
  })

  // The READ failed (file gone/unreadable) — content never loaded. Distinct from a
  // SAVE error. Show a clean placeholder instead of a blank editor whose Save would
  // silently recreate the file.
  const loadFailed = content === null && !loading && !isBinaryType && !!err
  const noText = isBinaryType || detectedBinary || loadFailed

  const save = async () => surfaceRef.current?.save()
  useImperativeHandle(ref, () => ({ save }))

  const onSurfaceSave = async (next: string) => {
    setErr('')
    try { await api.fileWrite(entry.path, next); setContent(next); setDiskChanged(false); onSaved(next) }
    catch (e) { setErr(String((e as Error).message || e)); throw e }
  }
  const confirmSave = async () => {
    if (!diskChanged) return true
    return confirm({
      title: 'File changed on disk',
      body: 'This file changed on disk (another process — or an agent — may have rewritten it) while you were editing. '
        + 'Saving will overwrite those changes with your version. Save anyway?',
      danger: true,
      confirmLabel: 'Overwrite',
    })
  }

  const fileName = baseName(entry.path)

  // ── non-content states: loading / load-failure / detected-binary placeholders ──
  if (loading) return <Centered><Loader2 size={20} className="animate-spin text-on-surface-low" /></Centered>
  if (loadFailed) {
    return (
      <Centered>
        <div className="flex flex-col items-center gap-2 text-on-surface-low">
          <FileWarning size={26} className="opacity-40" />
          <p className="text-[0.875rem]">Couldn't open this file.</p>
          <p className="text-[0.75rem] text-on-surface-low/80">{err}</p>
          <button type="button" onClick={() => setAttempt((n) => n + 1)}
            className="mt-1 inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[0.75rem] text-primary hover:bg-surface-high">
            <RotateCcw size={13} /> Try again
          </button>
        </div>
      </Centered>
    )
  }
  if (detectedBinary) {
    return (
      <Centered>
        <div className="flex flex-col items-center gap-2 text-on-surface-low">
          <FileWarning size={26} className="opacity-40" />
          <p className="text-[0.875rem]">This looks like a binary file — it can't be shown as text.</p>
          <a href={api.fileRawUrl(entry.path, true)} target="_blank" rel="noreferrer"
            className="text-[0.8125rem] text-primary hover:underline">Download to inspect it</a>
        </div>
      </Centered>
    )
  }

  // ── content state: the shared render/edit engine ──
  // Header chrome the file host owns: name + size + on-disk-change badge.
  const headerLeft = (
    <>
      <span className="truncate text-on-surface text-[0.875rem] font-mono" style={{ fontVariationSettings: '"wght" 500' }}>{fileName}</span>
      {diskChanged && !noText && (
        <span className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[0.65rem]"
          style={{ background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)', color: 'var(--color-warn)' }}
          title="The file changed on disk (another process — or an agent — rewrote it) while you were editing. Saving overwrites those changes; Revert to take the disk version.">
          <FileWarning size={11} /> changed on disk
        </span>
      )}
      {!compact && <span className="shrink-0 text-on-surface-low text-[0.7rem] tabular-nums">{fmtBytes(entry.size)}</span>}
    </>
  )
  // Host actions: save-as-artifact (text only) + download (always, incl. binary).
  const headerExtras = (
    <>
      {!noText && !truncated && content !== null && (
        compact
          ? <button onClick={() => onSaveAsArtifact(entry, draft)} type="button" title="Save as a versioned artifact"
              className="inline-flex size-7 items-center justify-center rounded-md text-on-surface-low hover:bg-surface-high hover:text-on-surface"><BookmarkPlus size={13} /></button>
          : <button onClick={() => onSaveAsArtifact(entry, draft)} type="button" title="Save as a versioned artifact"
              className="inline-flex items-center gap-1 rounded-md px-2 h-7 text-[0.75rem] text-on-surface-low hover:bg-surface-high hover:text-on-surface">
              <BookmarkPlus size={13} /> Artifact
            </button>
      )}
      {/* Reveal in Finder — macOS only (the gateway runs `open -R`). */}
      {isMac && (
        <button onClick={() => { void api.revealPath(entry.path, 'reveal').catch(() => {}) }} type="button" title="Reveal in Finder"
          className="inline-flex size-7 items-center justify-center rounded-md text-on-surface-low hover:bg-surface-high hover:text-on-surface"><FolderOpen size={13} /></button>
      )}
      <a href={api.fileRawUrl(entry.path, true)} download={fileName} target="_blank" rel="noreferrer"
        className="inline-flex size-7 items-center justify-center rounded-md text-on-surface-low hover:bg-surface-high hover:text-on-surface" title="Download"><Download size={13} /></a>
    </>
  )
  const banner = err && !loadFailed
    ? <div className="mx-m mt-2 flex items-center gap-2 rounded-md px-3 py-2 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-error) 12%, transparent)', color: 'var(--color-error)' }}><FileWarning size={14} /> {err}</div>
    : null

  return (
    <ContentSurface
      ref={surfaceRef}
      type={type}
      content={isBinaryType ? '' : (content ?? '')}
      title={entry.name}
      docId={entry.path}
      path={entry.path}
      language={lang}
      readOnly={noText}
      truncated={truncated && !noText}
      onSave={noText ? undefined : onSurfaceSave}
      confirmSave={confirmSave}
      onDirtyChange={onDirtyChange}
      onDraftChange={(d) => setDraft(d)}
      commentTarget={commentTarget}
      compact={compact}
      draftStore={draftStore}
      headerLeft={headerLeft}
      headerExtras={headerExtras}
      banner={banner}
    />
  )
})

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full items-center justify-center">{children}</div>
}
