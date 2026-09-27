import { useEffect, useImperativeHandle, useRef, useState, forwardRef } from 'react'
import { fvs } from '../../../design/fontWeight'
import { Download, Loader2, BookmarkPlus, FileWarning, RotateCcw, FolderOpen } from 'lucide-react'
import { api, type FsEntry } from '../../../lib/api'
import { rebaseText, type Revisioned } from '../../../lib/staleWrite'
import { useStaleWriteGuard } from '../../../lib/useStaleWriteGuard'
import { useIsMac } from '../../../app/usePlatform'
import { fmtBytes, monacoLang } from '../fileMeta'
import { useFileWatch } from './useFileWatch'
import { ContentSurface, type ContentSurfaceHandle, type DraftEntry } from '../../../ui/content/ContentSurface'
import { StaleWriteNotice } from '../../../ui/StaleWriteNotice'
import { SquareIconButton } from '../../../ui/SquareIconButton'
import { QuietButton } from '../../../ui/QuietButton'
import { Button } from '../../../ui/Button'
import { TextLink } from '../../../ui/TextLink'
import { Centered } from '../../../ui/Centered'
import { resolveContentType, getContentType } from '../../../ui/content/contentTypes'
import type { CommentTarget } from '../../../ui/content/commentTarget'

export interface FileViewerHandle { save: () => void }

/** "Save as artifact", handed to the host with the write already bound to THIS viewer's copy. The
 *  host names the artifact and supplies the create call (`api.saveFileAsArtifact`), which writes
 *  the draft through to this same file — so the viewer supplies the draft and the base it was built
 *  from, and owns the recovery when the file changed since (`ui/StaleWriteNotice`). Resolves `false`
 *  when the gateway refused the save as stale; the notice is then on this viewer, holding the draft. */
export type SaveAsArtifact = (create: (content: string, base: string) => Promise<unknown>) => Promise<boolean>

/** A guarded write of the draft: resolves with the revision the file now reads at, or `null` when
 *  the write does not say (an artifact save) and the file has to be read again to know. */
type DraftWrite = (next: string, base: string) => Promise<string | null>

interface ViewerProps {
  entry: FsEntry
  /** Fired after a successful save; receives the saved content so a host can keep its
   *  own per-file baseline (e.g. the cockpit's diff-reveal last-seen map) accurate. */
  onSaved: (content?: string) => void
  onSaveAsArtifact: (entry: FsEntry, save: SaveAsArtifact) => void
  onDirtyChange?: (dirty: boolean) => void
  // An optional host-owned per-path draft cache. A multi-tab host (the Code cockpit)
  // mounts only the ACTIVE tab's FileViewer, so switching tabs unmounts the editor and
  // would lose an unsaved edit on the tab left behind. With a draftStore, the surface
  // seeds from a cached draft on mount (instead of disk) and writes the draft back on
  // every edit — so a dirty tab's content survives a switch-away-and-back. Cleared on a
  // successful save / revert. Single-host surfaces (Files page) omit it → unchanged.
  // The entry keeps the revisioned copy the draft was edited against (`DraftEntry.base`), so a
  // remounted viewer saves over exactly that copy: a file rewritten while the tab was away still
  // refuses the save, and the "changed on disk" badge is re-derived from it.
  draftStore?: Map<string, DraftEntry>
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
 *  reading bytes, the live disk-watch, the copy a save is based on, binary/missing/
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

  // The file as it is on disk — the surface's baseline — and the revision that read reported
  // (`null` when the last read or watch frame was truncated or binary, so no copy of the file).
  const [content, setContent] = useState<string | null>(null)
  const [diskRevision, setDiskRevision] = useState<string | null>(null)
  // 🔴 THE COPY THE DRAFT WAS BUILT FROM — the base a save names (`lib/staleWrite.ts`). It follows
  // the disk copy only while the draft holds nothing of the user's that the disk lacks. Under an
  // unsaved edit it stays put, so a save after the file changed on disk (the agent's edit_file,
  // another tab, a write-through from an artifact) is refused and the edit re-applied on top —
  // it used to be written over the newer file, and only an "Overwrite?" prompt stood in the way.
  const [base, setBase] = useState<Revisioned<string> | null>(null)
  const [draft, setDraft] = useState('')          // mirrored from the surface, for save-as-artifact + the base
  const [truncated, setTruncated] = useState(false)
  // Server-detected binary (NUL bytes) for a file whose extension ISN'T a known image/
  // pdf — e.g. .pyc/.so/.db/no-ext executables. Without this they'd render as mojibake.
  const [detectedBinary, setDetectedBinary] = useState(false)
  const [loading, setLoading] = useState(!isBinaryType)
  const [err, setErr] = useState('')
  // Bumped by the load-failure "Try again" so a transient read failure can be retried.
  const [attempt, setAttempt] = useState(0)
  // The latest draft and base, for a save-as-artifact the host completes after its naming step.
  const latest = useRef({ draft, base })
  latest.current = { draft, base }

  // Where the guarded save goes: this file, or — for "Save as artifact" — the create the host
  // supplied, which writes the same draft through to the same file. Set before every save, so a
  // notice's "Reload and reapply" re-runs exactly the write that was refused.
  const writeFile: DraftWrite = (next, rev) => api.fileWrite(entry.path, next, rev).then((r) => r.revision)
  const writeTo = useRef<DraftWrite>(writeFile)
  const written = useRef<Revisioned<string> | null>(null)

  /** Read the file again and take it as the copy — after a save that did not report the revision
   *  the file now reads at, and after a discarded change (then the draft is the file again too). */
  const retake = async (dropDraft: boolean) => {
    try {
      const r = await api.fileRead(entry.path, true)
      if (r.binary) { setDetectedBinary(true); return }
      setContent(r.content); setDiskRevision(r.revision); setTruncated(r.truncated)
      if (r.revision !== null) setBase({ value: r.content, revision: r.revision })
      if (dropDraft) { surfaceRef.current?.replaceDraft(r.content); setDraft(r.content) }
    } catch (e) { setErr(String((e as Error).message || e)) }
  }

  const guard = useStaleWriteGuard<string>({
    read: async () => {
      const r = await api.fileRead(entry.path, true)
      // No copy anything can be re-applied onto: the notice says so instead of guessing.
      if (r.binary) throw new Error('the file is binary now')
      if (r.truncated || r.revision === null) throw new Error('the file is now too large to edit here')
      return { value: r.content, revision: r.revision }
    },
    write: async (next, rev) => {
      const revision = await writeTo.current(next, rev)
      written.current = revision === null ? null : { value: next, revision }
    },
    onSaved: (saved) => {
      setErr('')
      // What was stored is the new copy — and, after a re-apply, not what the editor held.
      surfaceRef.current?.replaceDraft(saved)
      setContent(saved); setDraft(saved)
      const w = written.current
      if (w && w.value === saved) { setDiskRevision(w.revision); setBase(w) }
      else void retake(false)
      onSaved(saved)
    },
    onDiscard: () => { void retake(true) },
  })

  useEffect(() => {
    if (isBinaryType) { setContent(null); setLoading(false); return }
    let alive = true
    setLoading(true); setErr(''); setDetectedBinary(false)
    api.fileRead(entry.path, true).then((r) => {
      if (!alive) return
      if (r.binary) { setDetectedBinary(true); setContent(''); setDraft(''); setBase(null); setDiskRevision(null); setLoading(false); return }
      const cached = draftStore?.get(entry.path)
      setContent(r.content); setDiskRevision(r.revision); setDraft(cached ? cached.draft : r.content); setTruncated(r.truncated)
      // A cached draft keeps the copy it was edited against — the watch that would have kept it
      // current doesn't run while the viewer is unmounted.
      setBase(cached ? cached.base : r.revision === null ? null : { value: r.content, revision: r.revision })
      setLoading(false)
    }).catch((e) => {
      if (!alive) return
      // 404 = gone on disk → tell the host to close the dead tab; transient errors
      // fall through to the retryable placeholder.
      if ((e as { status?: number }).status === 404) onMissingRef.current?.(entry.path)
      setErr(String(e.message || e)); setLoading(false)
    })
    return () => { alive = false }
  }, [entry.path, isBinaryType, attempt])

  // Live-watch: refresh the baseline on disk change. The surface preserves an unsaved draft
  // across a baseline change and follows an untouched one; the base follows below.
  useFileWatch(entry.path, !isBinaryType, (next, revision) => { setContent(next); setDiskRevision(revision) })

  // While the draft holds nothing of the user's that the disk copy lacks — untouched, reverted,
  // or typed back to match it — the disk copy IS its base.
  useEffect(() => {
    if (content === null || diskRevision === null || draft !== content) return
    setBase((b) => (b && b.revision === diskRevision && b.value === content ? b : { value: content, revision: diskRevision }))
  }, [content, diskRevision, draft])

  // The READ failed (file gone/unreadable) — content never loaded. Distinct from a
  // SAVE error. Show a clean placeholder instead of a blank editor whose Save would
  // silently recreate the file.
  const loadFailed = content === null && !loading && !isBinaryType && !!err
  const noText = isBinaryType || detectedBinary || loadFailed
  // The file moved on disk under an unsaved edit: the copy the draft was built from is not the
  // one on disk now. Saving no longer overwrites it — the gateway refuses the stale copy and the
  // notice offers to re-apply the edit on top — but the user should know before pressing Save.
  const diskChanged = base !== null && content !== null && draft !== content && base.revision !== diskRevision

  const save = async () => surfaceRef.current?.save()
  useImperativeHandle(ref, () => ({ save }))

  const onSurfaceSave = async (next: string) => {
    if (!base) return
    setErr('')
    writeTo.current = writeFile
    // `false` is a stale refusal: the notice holds the change, and the draft stays unsaved.
    try { await guard.save(base, next, rebaseText(base.value, next)) }
    catch (e) { setErr(String((e as Error).message || e)); throw e }
  }
  const saveAsArtifact: SaveAsArtifact = (create) => {
    const { draft: text, base: from } = latest.current
    if (!from) return Promise.resolve(false)
    setErr('')
    writeTo.current = (next, rev) => create(next, rev).then(() => null)
    return guard.save(from, text, rebaseText(from.value, text))
  }

  // The entry's own name, not its path's: a host may name a file other than as it is stored (the
  // chat's file panel names an attachment as it was attached, not `<uuid-hex>_image.png`).
  const fileName = entry.name

  // ── non-content states: loading / load-failure / detected-binary placeholders ──
  if (loading) return <Centered><Loader2 size={20} className="animate-spin text-on-surface-low" /></Centered>
  if (loadFailed) {
    return (
      <Centered>
        <div className="flex flex-col items-center gap-2 text-on-surface-low">
          <FileWarning size={26} className="opacity-40" />
          <p className="text-[0.8125rem]">Couldn't open this file.</p>
          <p className="text-[0.75rem] text-on-surface-low/80">{err}</p>
          <Button variant="ghost-accent" size="xs" onClick={() => setAttempt((n) => n + 1)} className="mt-1"><RotateCcw size={13} /> Try again</Button>
        </div>
      </Centered>
    )
  }
  if (detectedBinary) {
    return (
      <Centered>
        <div className="flex flex-col items-center gap-2 text-on-surface-low">
          <FileWarning size={26} className="opacity-40" />
          <p className="text-[0.8125rem]">This looks like a binary file — it can't be shown as text.</p>
          <TextLink href={api.fileRawUrl(entry.path, true)} external size="sm">Download to inspect it</TextLink>
        </div>
      </Centered>
    )
  }

  // ── content state: the shared render/edit engine ──
  // Header chrome the file host owns: name + size + on-disk-change badge.
  const headerLeft = (
    <>
      <span className="truncate text-on-surface text-[0.8125rem] font-mono" style={fvs(500)}>{fileName}</span>
      {diskChanged && !noText && (
        <span className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[0.75rem]"
          style={{ background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)', color: 'var(--color-warn)' }}
          title="The file changed on disk (another process — or an agent — rewrote it) while you were editing. Saving won't overwrite that change: you'll be offered to re-apply your edit on top of it. Revert takes the disk version.">
          <FileWarning size={11} /> changed on disk
        </span>
      )}
      {!compact && <span className="shrink-0 text-on-surface-low text-[0.75rem] tabular-nums">{fmtBytes(entry.size)}</span>}
    </>
  )
  // Host actions: save-as-artifact (text only) + download (always, incl. binary).
  const headerExtras = (
    <>
      {!noText && !truncated && content !== null && (
        compact
          ? <SquareIconButton icon={BookmarkPlus} iconSize={13} label="Save as a versioned artifact" onClick={() => onSaveAsArtifact(entry, saveAsArtifact)} />
          : <QuietButton onClick={() => onSaveAsArtifact(entry, saveAsArtifact)} title="Save as a versioned artifact">
              <BookmarkPlus size={13} /> Artifact
            </QuietButton>
      )}
      {/* Reveal in Finder — macOS only (the gateway runs `open -R`). */}
      {isMac && (
        <SquareIconButton icon={FolderOpen} iconSize={13} label="Reveal in Finder" onClick={() => { void api.revealPath(entry.path, 'reveal').catch(() => {}) }} />
      )}
      <a href={api.fileRawUrl(entry.path, true)} download={fileName} target="_blank" rel="noreferrer"
        className="inline-flex size-7 items-center justify-center rounded-md text-on-surface-low hover:bg-surface-high hover:text-on-surface" title="Download"><Download size={13} /></a>
    </>
  )
  // The one recovery for a save refused because the file changed since this copy was read, then
  // any other save failure.
  const banner = (
    <>
      <StaleWriteNotice guard={guard} what="This file" className="mx-m mt-s" />
      {err && !loadFailed && <div className="mx-m mt-s flex items-center gap-s rounded-md px-m py-s text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-error) 12%, transparent)', color: 'var(--color-error)' }}><FileWarning size={14} /> {err}</div>}
    </>
  )

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
      onDirtyChange={onDirtyChange}
      onDraftChange={(d) => setDraft(d)}
      commentTarget={commentTarget}
      compact={compact}
      draftStore={draftStore}
      draftBase={base ?? undefined}
      locked={guard.conflict !== null}
      headerLeft={headerLeft}
      headerExtras={headerExtras}
      banner={banner}
    />
  )
})
