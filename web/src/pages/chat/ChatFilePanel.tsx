import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { motion } from 'framer-motion'
import { X, Maximize2, Minimize2, Box } from 'lucide-react'
import { IconButton } from '../../ui/IconButton'
import { Modal } from '../../ui/Modal'
import { Button } from '../../ui/Button'
import { TextInput } from '../tasks/formControls'
import { spring } from '../../design/motion'
import { api, type FsEntry } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { FileViewer, type FileViewerHandle } from '../files/browse/FileViewer'
import { baseName } from '../files/fileMeta'
import type { CommentTarget } from '../../ui/content/commentTarget'

const MIN_W = 360, MAX_W = 900, DEFAULT_W = 480

/** Right-docked, resizable, full-featured single-file editor for chat — the SAME
 *  `FileViewer` the Files page uses (Monaco edit + save, dirty/revert, word-wrap,
 *  copy, preview/split/edit, image/pdf/csv/json/html previews, save-as-artifact,
 *  download, live file-watch), minus the multi-tab strip. Opened by clicking an
 *  inline file reference in a chat response. Three modes: docked (drag the left
 *  edge to resize), expanded (full-viewport overlay), close. ⌘S saves. */
export function ChatFilePanel({ path, onClose, commentTarget }: { path: string; onClose: () => void; commentTarget?: CommentTarget }) {
  const [width, setWidth] = useState<number>(() => {
    const v = Number(localStorage.getItem('chat-file-w'))
    return v >= MIN_W && v <= MAX_W ? v : DEFAULT_W
  })
  const [expanded, setExpanded] = useState(false)
  const [artModal, setArtModal] = useState<{ entry: FsEntry; content: string; name: string } | null>(null)
  const viewerRef = useRef<FileViewerHandle>(null)
  const entry: FsEntry = { name: baseName(path), path, is_dir: false }

  useEffect(() => { localStorage.setItem('chat-file-w', String(width)) }, [width])

  // ⌘S saves the open file; Esc collapses an expanded panel, else closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); viewerRef.current?.save() }
      else if (e.key === 'Escape') { if (expanded) setExpanded(false); else onClose() }
    }
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey)
  }, [expanded, onClose])

  const onHandleDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    const startX = e.clientX, startW = width
    const move = (ev: PointerEvent) => setWidth(Math.max(MIN_W, Math.min(MAX_W, startW + (startX - ev.clientX))))
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }, [width])

  const saveAsArtifact = (e: FsEntry, content: string) => setArtModal({ entry: e, content, name: baseName(e.path) })
  const confirmArtifact = async () => {
    if (!artModal || !artModal.name.trim()) return
    try {
      await api.createArtifact({ name: artModal.name.trim(), content: artModal.content, source: 'manual', source_path: artModal.entry.path, kind: guessKind(artModal.entry.name) })
      setArtModal(null)
    } catch (e) { notify(`Could not save artifact: ${(e as Error).message}`, 'error') }
  }

  // a dedicated chrome row owns the panel frame (full path + expand/close) so the
  // FileViewer's own compact action bar sits cleanly below it — no overlap, no
  // crowding. The path is shown here (truncated, with the basename emphasized).
  const dir = path.replace(/\/+$/, '').replace(/\/[^/]*$/, '')
  const chrome = (
    <div className="flex items-center gap-2 border-b border-outline-variant/40 px-m py-1.5">
      <span className="min-w-0 flex-1 truncate text-on-surface-low text-[0.7rem]" title={path}>
        {dir && <span className="opacity-60">{dir}/</span>}
        <span className="text-on-surface" style={{ fontVariationSettings: '"wght" 500' }}>{baseName(path)}</span>
      </span>
      <div className="flex shrink-0 items-center gap-0.5">
        <IconButton icon={expanded ? Minimize2 : Maximize2} label={expanded ? 'Collapse to panel' : 'Expand to full width'} size={28} onClick={() => setExpanded((v) => !v)} />
        <IconButton icon={X} label="Close (Esc)" size={28} onClick={onClose} />
      </div>
    </div>
  )

  const body = (
    <div className="flex h-full flex-col">
      {chrome}
      <div className="min-h-0 flex-1">
        <FileViewer ref={viewerRef} entry={entry} compact={!expanded} onSaved={() => {}} onSaveAsArtifact={saveAsArtifact} commentTarget={commentTarget} />
      </div>
      {artModal && (
        <Modal title="Save as artifact" icon={<Box size={18} className="text-primary" />} onClose={() => setArtModal(null)}>
          <div className="flex flex-col gap-m p-l" style={{ minWidth: 360 }}>
            <p className="text-on-surface-low text-[0.8125rem]">Creates a versioned artifact that live-points at <span className="font-mono">{baseName(artModal.entry.path)}</span>. Re-saving bumps it instead of duplicating.</p>
            <TextInput value={artModal.name} onChange={(v) => setArtModal((m) => m && { ...m, name: v })} placeholder="Artifact name" autoFocus />
            <div className="flex justify-end gap-s">
              <Button variant="ghost" size="sm" onClick={() => setArtModal(null)}>Cancel</Button>
              <Button size="sm" onClick={confirmArtifact}>Save artifact</Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )

  if (expanded) {
    // full-viewport overlay, portaled to <body> (an animated/transformed ancestor
    // would otherwise become the containing block for position:fixed).
    return createPortal(
      <motion.div className="fixed inset-0 z-50 flex flex-col bg-surface" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}>
        {body}
      </motion.div>,
      document.body,
    )
  }

  return (
    <motion.div className="relative shrink-0 overflow-hidden border-l border-outline-variant/40 bg-surface"
      initial={{ width: 0, opacity: 0 }} animate={{ width, opacity: 1 }} exit={{ width: 0, opacity: 0 }} transition={spring.spatialDefault}>
      {/* left-edge resize handle */}
      <div onPointerDown={onHandleDown} className="absolute left-0 top-0 bottom-0 z-20 w-1.5 cursor-ew-resize group">
        <span className="absolute left-0 top-0 bottom-0 w-px bg-outline-variant/40 group-hover:bg-primary transition-colors" />
      </div>
      <div className="h-full" style={{ width }}>{body}</div>
    </motion.div>
  )
}

function guessKind(name: string): string {
  const ext = name.toLowerCase().split('.').pop() || ''
  if (ext === 'html' || ext === 'htm') return 'html'
  if (ext === 'svg') return 'svg'
  if (ext === 'json') return 'json'
  if (['md', 'markdown', 'mdx', 'txt'].includes(ext)) return 'markdown'
  return 'text'
}
