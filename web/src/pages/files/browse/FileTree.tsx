import { useEffect, useRef, useState } from 'react'
import { ChevronRight, ChevronDown, Pencil, Trash2, Upload, FilePlus2, FolderPlus, MoreHorizontal } from 'lucide-react'
import { createPortal } from 'react-dom'
import type { FsEntry } from '../../../lib/api'
import { fileIcon, gitBadge, gitStatusTitle } from '../fileMeta'
import type { useDirCache } from '../filesData'

interface TreeProps {
  dirs: ReturnType<typeof useDirCache>
  rootPath: string
  activePath: string | null
  gitStatuses: Record<string, string>
  onOpenFile: (entry: FsEntry) => void
  /** slugs of file paths that back an artifact — gets a small dot. */
  artifactPaths: Set<string>
  /** Rename an entry: prompts inline, computes dest in the same parent dir. */
  onRename: (entry: FsEntry, nextName: string) => void
  /** Delete an entry (file or dir). */
  onDelete: (entry: FsEntry) => void
  /** Upload files into a directory entry. */
  onUpload: (dirEntry: FsEntry, files: File[]) => void
  /** Create a new file/folder INSIDE a directory entry (inline name prompt in the
   *  tree). Optional — when omitted, the dir context menu offers no create actions
   *  (e.g. a read-only host). Hosts that wire it get VSCode-style "New file/folder
   *  here" so creation isn't limited to the root. */
  onCreate?: (dirEntry: FsEntry, name: string, kind: 'file' | 'dir') => void
  /** Top-level entry names to hide (e.g. a Code project's engine bookkeeping when
   *  the tree is rooted at its files dir). Applies to the root level only. */
  hideNames?: Set<string>
  /** Top-level name PREFIXES to hide — for dynamically-named engine bookkeeping
   *  families that exact `hideNames` can't enumerate (e.g. per-task steer files
   *  `guidance_<task_id>.txt`). Root level only, like hideNames. */
  hidePrefixes?: Set<string>
  /** Entry names to hide at EVERY level (root + nested) — tool-generated VCS/build
   *  noise (.git, __pycache__, node_modules, …) that clutters a code-focused tree.
   *  Distinct from hideNames, which is root-only bookkeeping. */
  hideNamesDeep?: Set<string>
  /** Message shown when the (visible) tree is empty. Defaults to "Empty". A host
   *  can pass guiding copy (e.g. "Files appear here once the project starts"). */
  emptyLabel?: string
}

/** Lazy file tree rooted at `rootPath`. Children load on first expand. */
export function FileTree({ dirs, rootPath, activePath, gitStatuses, onOpenFile, artifactPaths, onRename, onDelete, onUpload, onCreate, hideNames, hidePrefixes, hideNamesDeep, emptyLabel = 'Empty' }: TreeProps) {
  // Seed synchronously from the (session-persisted) dir cache so a refresh repaints the
  // last-known listing INSTANTLY instead of flashing empty/"Loading…" for a few seconds
  // while the first fetch resolves (observed live). Falls back to null (→ skeleton) only
  // when this root was never cached this session.
  const [entries, setEntries] = useState<FsEntry[] | null>(() => dirs.cache[rootPath] ?? null)
  // Re-sync from the dir cache whenever THIS path's cached listing changes — keyed on
  // the cache slot, not on `dirs` identity. So an 8s poll invalidate (clears the slot →
  // load refetches → slot updates) refreshes the tree, while the cockpit's frequent
  // re-renders (stable `dirs`, unchanged slot) DON'T reload it. `load` is a no-op when
  // the slot is already cached, so this only fetches when the slot was actually cleared.
  const rootCached = dirs.cache[rootPath]
  useEffect(() => {
    let alive = true
    // Paint the cached slot immediately (instant on refresh), then reconcile with the fetch.
    if (rootCached) setEntries(rootCached)
    dirs.load(rootPath).then((e) => { if (alive) setEntries(e) })
    return () => { alive = false }
  }, [rootPath, dirs, rootCached])

  // First-ever load this session (no cached slot) → skeleton rows, not bare "Loading…",
  // so the panel has structure instead of flashing empty.
  if (entries === null) return <FileTreeSkeleton />
  let shown = hideNames?.size ? entries.filter((e) => !hideNames.has(e.name)) : entries
  if (hidePrefixes?.size) shown = shown.filter((e) => ![...hidePrefixes].some((p) => e.name.startsWith(p)))
  if (hideNamesDeep?.size) shown = shown.filter((e) => !hideNamesDeep.has(e.name))
  if (shown.length === 0) return <div className="px-m py-s text-on-surface-low text-[0.875rem]">{emptyLabel}</div>
  return (
    <div>
      {shown.map((e) => (
        <TreeNode key={e.path} entry={e} depth={0} dirs={dirs} activePath={activePath}
          gitStatuses={gitStatuses} onOpenFile={onOpenFile} artifactPaths={artifactPaths}
          onRename={onRename} onDelete={onDelete} onUpload={onUpload} onCreate={onCreate} hideNamesDeep={hideNamesDeep} />
      ))}
    </div>
  )
}

function TreeNode({ entry, depth, dirs, activePath, gitStatuses, onOpenFile, artifactPaths, onRename, onDelete, onUpload, onCreate, hideNamesDeep }: {
  entry: FsEntry; depth: number
} & Omit<TreeProps, 'rootPath'>) {
  const [open, setOpen] = useState(false)
  const [children, setChildren] = useState<FsEntry[] | null>(null)
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)
  const [renaming, setRenaming] = useState(false)
  // One-shot latch so Enter→(unmount)→onBlur doesn't fire commitRename twice (→ a
  // duplicate fileMove that 404s). Reset when a fresh rename starts.
  const committedRename = useRef(false)
  // Set by Escape so the onBlur that fires as the input unmounts SKIPS the commit —
  // else Escape (which calls setRenaming(false)) triggers blur → commitRename, renaming
  // the file with whatever was typed, defeating Escape's cancel.
  const cancelledRename = useRef(false)
  const [draft, setDraft] = useState(entry.name)
  const [dropActive, setDropActive] = useState(false)
  // Inline "new file/folder HERE" (in this directory) — null when not creating.
  const [creating, setCreating] = useState<'file' | 'dir' | null>(null)
  const [createDraft, setCreateDraft] = useState('')
  // Mirrors the rename latch/cancel: committedCreate stops Enter→blur double-firing
  // onCreate; cancelledCreate lets Escape back out without the unmount-blur committing.
  const committedCreate = useRef(false)
  const cancelledCreate = useRef(false)
  const uploadInput = useRef<HTMLInputElement>(null)
  const Icon = fileIcon(entry.name, entry.is_dir)
  const badge = gitBadge(gitStatuses[entry.path])
  const isActive = activePath === entry.path

  // Auto-expand to reveal the active file: a directory that is an ANCESTOR of
  // activePath opens itself (loading children on demand). As each ancestor mounts
  // its children, the chain expands recursively, so a worker-touched (follow-open)
  // or Changes-panel-opened file deep in the tree is always shown in context —
  // not hidden inside collapsed folders.
  useEffect(() => {
    if (!entry.is_dir || !activePath) return
    // Tolerate a macOS realpath-prefix difference between the tree's entry paths
    // (/private/tmp/…) and an active path emitted in unresolved form (/tmp/…) by
    // normalizing a leading /private off both before the ancestor check.
    const norm = (p: string) => p.replace(/^\/private(\/|$)/, '/')
    if (!norm(activePath).startsWith(norm(entry.path).replace(/\/$/, '') + '/')) return
    setOpen(true)
    if (children === null) dirs.load(entry.path).then(setChildren)
    // children load is idempotent (dir cache); only the open flag + first load matter
  }, [activePath, entry.is_dir, entry.path, children, dirs])

  // Re-sync an OPEN dir's children when its cached listing changes (e.g. the 8s poll
  // invalidated the subtree after the worker wrote a file here). Keyed on the cache
  // slot, not `dirs` identity, so the cockpit's frequent re-renders don't reload it;
  // only fires while open + after a real cache change. `load` is a cached no-op
  // otherwise. This is what keeps a worker-created file appearing in an expanded dir.
  const dirCached = entry.is_dir ? dirs.cache[entry.path] : undefined
  useEffect(() => {
    if (!entry.is_dir || !open) return
    let alive = true
    dirs.load(entry.path).then((c) => { if (alive) setChildren(c) })
    return () => { alive = false }
  }, [entry.is_dir, entry.path, open, dirs, dirCached])

  const toggle = async () => {
    if (entry.is_dir) {
      const next = !open
      setOpen(next)
      if (next && children === null) setChildren(await dirs.load(entry.path))
    } else {
      onOpenFile(entry)
    }
  }

  const startRename = () => { committedRename.current = false; cancelledRename.current = false; setDraft(entry.name); setRenaming(true) }
  const commitRename = () => {
    if (cancelledRename.current) return  // Escape cancelled this rename — don't commit
    // Enter calls this, which setRenaming(false) → unmounts the input → fires onBlur →
    // commitRename AGAIN. The 2nd call still sees the stale `entry.name` (props update
    // async after the parent refetches), so the next!==entry.name guard doesn't catch
    // it → a DUPLICATE onRename → a second fileMove of old→new that 404s (old path gone)
    // and surfaces a spurious "Couldn't rename" after a successful rename. Latch it.
    if (committedRename.current) return
    committedRename.current = true
    const next = draft.trim()
    setRenaming(false)
    if (next && next !== entry.name) onRename(entry, next)
  }

  // Begin creating a child in THIS dir: expand it (so the inline input + the new
  // entry are visible) then show the input.
  const startCreate = async (kind: 'file' | 'dir') => {
    if (!entry.is_dir) return
    if (!open) { setOpen(true); if (children === null) setChildren(await dirs.load(entry.path)) }
    committedCreate.current = false; cancelledCreate.current = false
    setCreateDraft(''); setCreating(kind)
  }
  const commitCreate = () => {
    // One-shot latch: Enter calls this → setCreating(null) unmounts the input → onBlur
    // fires commitCreate AGAIN. Whether the 2nd call's closure sees the nulled `creating`
    // is render-timing-dependent, so don't rely on it — latch like commitRename, else a
    // double onCreate → a 2nd fileCreate of the same name → spurious 409 "already exists"
    // after a successful create. Reset whenever a fresh create starts (startCreate).
    if (committedCreate.current || cancelledCreate.current) return
    committedCreate.current = true
    const name = createDraft.trim()
    const kind = creating
    setCreating(null); setCreateDraft('')
    if (name && kind && onCreate) onCreate(entry, name, kind)
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation()
    setDropActive(false)
    if (!entry.is_dir) return
    const files = Array.from(e.dataTransfer.files)
    if (files.length) onUpload(entry, files)
  }

  // Open the row's action menu from the "⋯" button (keyboard/touch path — the
  // right-click contextmenu is mouse-only) anchored to the button's rect.
  const openMenuFromButton = (e: React.MouseEvent) => {
    e.preventDefault(); e.stopPropagation()
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    setMenu({ x: Math.round(r.right), y: Math.round(r.bottom) })
  }

  return (
    <div>
      <div className="group/row relative"
        onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); setMenu({ x: e.clientX, y: e.clientY }) }}
        onDragOver={entry.is_dir ? (e) => { e.preventDefault(); e.stopPropagation(); setDropActive(true) } : undefined}
        onDragLeave={entry.is_dir ? () => setDropActive(false) : undefined}
        onDrop={entry.is_dir ? onDrop : undefined}
      >
        {renaming ? (
          <div className="flex items-center gap-1.5 py-1.5 pr-2" style={{ paddingLeft: 10 + depth * 16 }}>
            <span className="w-[15px] shrink-0" />
            <Icon size={16} className="shrink-0" style={{ color: entry.is_dir ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
            <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') { cancelledRename.current = true; setRenaming(false) } }}
              onBlur={commitRename}
              className="h-6 min-w-0 flex-1 rounded-md bg-surface-high px-1.5 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
          </div>
        ) : (
          <button onClick={toggle} type="button"
            // Expose the folder's open/closed state to assistive tech (a dir row
            // toggles expand; files carry no expanded state).
            aria-expanded={entry.is_dir ? open : undefined}
            className="group flex w-full items-center gap-1.5 rounded-md py-1.5 pr-2 text-left transition-colors hover:bg-surface-high"
            style={{
              paddingLeft: 10 + depth * 16,
              background: dropActive ? 'color-mix(in srgb, var(--color-primary) 18%, transparent)'
                : isActive ? 'color-mix(in srgb, var(--color-primary) 14%, transparent)' : undefined,
              boxShadow: dropActive ? 'inset 0 0 0 1px var(--color-primary)' : undefined,
            }}>
            {entry.is_dir
              ? (open ? <ChevronDown size={15} className="shrink-0 text-on-surface-low" /> : <ChevronRight size={15} className="shrink-0 text-on-surface-low" />)
              : <span className="w-[15px] shrink-0" />}
            <Icon size={16} className="shrink-0" style={{ color: entry.is_dir ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
            <span className="truncate text-[0.875rem]" style={{ color: isActive ? 'var(--color-on-surface)' : undefined }}>{entry.name}</span>
            {!entry.is_dir && artifactPaths.has(entry.path) && (
              <span className="ml-1 size-1.5 shrink-0 rounded-full" style={{ background: 'var(--color-primary)' }} title="Saved as an artifact" />
            )}
            {badge && (
              // On row hover/focus the "⋯" actions button overlays the right edge —
              // shift the git badge left so it stays visible instead of being covered.
              <span className="ml-auto shrink-0 rounded px-1 text-[0.625rem] font-semibold leading-tight transition-[margin] group-hover/row:mr-5 group-focus-within/row:mr-5"
                style={{ color: badge.tone, border: `1px solid color-mix(in srgb, ${badge.tone} 40%, transparent)` }} title={gitStatusTitle(gitStatuses[entry.path])}>
                {badge.label}
              </span>
            )}
          </button>
        )}
        {/* "⋯" actions trigger — the keyboard/touch path to the row menu (the
            right-click contextmenu is mouse-only). Hidden until row hover/focus so
            it doesn't clutter; opens the SAME menu. */}
        {!renaming && (
          <button type="button" onClick={openMenuFromButton} aria-label={`Actions for ${entry.name}`}
            className="absolute right-1 top-1/2 -translate-y-1/2 grid size-5 place-items-center rounded text-on-surface-low opacity-0 transition-opacity hover:bg-surface-highest hover:text-on-surface focus-visible:opacity-100 group-hover/row:opacity-100">
            <MoreHorizontal size={13} />
          </button>
        )}
      </div>
      {entry.is_dir && (
        <input ref={uploadInput} type="file" multiple className="hidden"
          name={`upload-${entry.path}`} aria-label={`Upload files to ${entry.name}`} tabIndex={-1}
          onChange={(e) => { const fs = Array.from(e.target.files ?? []); if (fs.length) onUpload(entry, fs); e.target.value = '' }} />
      )}
      {menu && (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)}
          items={[
            // Create-here actions for a directory (only when the host wires onCreate).
            ...(entry.is_dir && onCreate ? [
              { icon: FilePlus2, label: 'New file here', onClick: () => { void startCreate('file') } },
              { icon: FolderPlus, label: 'New folder here', onClick: () => { void startCreate('dir') } },
            ] : []),
            { icon: Pencil, label: 'Rename', onClick: startRename },
            ...(entry.is_dir ? [{ icon: Upload, label: 'Upload here', onClick: () => uploadInput.current?.click() }] : []),
            { icon: Trash2, label: 'Delete', tone: 'danger' as const, onClick: () => onDelete(entry) },
          ]} />
      )}
      {entry.is_dir && open && children !== null && (() => {
        const kids = hideNamesDeep?.size ? children.filter((c) => !hideNamesDeep.has(c.name)) : children
        return (
        <div>
          {/* inline "new file/folder here" input — sits at the top of the dir's children */}
          {creating && (
            <div className="flex items-center gap-1.5 py-1.5 pr-2" style={{ paddingLeft: 10 + (depth + 1) * 16 }}>
              <span className="w-[15px] shrink-0" />
              {creating === 'dir'
                ? <FolderPlus size={16} className="shrink-0 text-primary" />
                : <FilePlus2 size={16} className="shrink-0 text-on-surface-low" />}
              <input autoFocus value={createDraft} onChange={(e) => setCreateDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') commitCreate(); if (e.key === 'Escape') { cancelledCreate.current = true; setCreating(null); setCreateDraft('') } }}
                onBlur={commitCreate} placeholder={creating === 'file' ? 'new-file.ext' : 'new-folder'}
                className="h-6 min-w-0 flex-1 rounded-md bg-surface-high px-1.5 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 placeholder:text-on-surface-low" />
            </div>
          )}
          {kids.length === 0 && !creating
            ? <div className="py-1 text-on-surface-low text-[0.8125rem]" style={{ paddingLeft: 10 + (depth + 1) * 16 + 15 }}>empty</div>
            : kids.map((c) => (
              <TreeNode key={c.path} entry={c} depth={depth + 1} dirs={dirs} activePath={activePath}
                gitStatuses={gitStatuses} onOpenFile={onOpenFile} artifactPaths={artifactPaths}
                onRename={onRename} onDelete={onDelete} onUpload={onUpload} onCreate={onCreate} hideNamesDeep={hideNamesDeep} />
            ))}
        </div>
        )
      })()}
    </div>
  )
}

/** Placeholder rows shown only on the FIRST load of a root this session (no cached slot
 *  to paint). Gives the panel structure instead of flashing empty / bare "Loading…";
 *  on a refresh the session-persisted cache paints the real tree instantly and this
 *  never shows. Indentation + varied widths mimic a file listing. */
function FileTreeSkeleton() {
  const widths = ['62%', '78%', '45%', '70%', '55%', '83%', '50%']
  return (
    <div className="px-m py-s flex flex-col gap-2 animate-pulse" aria-hidden="true">
      {widths.map((w, i) => (
        <div key={i} className="flex items-center gap-1.5" style={{ paddingLeft: (i % 3) * 14 }}>
          <span className="size-3.5 shrink-0 rounded-sm bg-surface-high" />
          <span className="h-3 rounded bg-surface-high" style={{ width: w }} />
        </div>
      ))}
    </div>
  )
}

interface MenuItem { icon: typeof Pencil; label: string; onClick: () => void; tone?: 'danger' }

/** Fixed-position right-click menu, portaled to body so it escapes the scroll
 *  container; closes on outside-click, scroll, or Escape. The requested (x,y) is
 *  clamped to the viewport so a menu opened near the panel's right/bottom edge
 *  (e.g. from the row's "⋯" button, anchored at its right edge) flips inward
 *  instead of overflowing off-screen — mirroring the ui/motion/ContextMenu clamp. */
function ContextMenu({ x, y, items, onClose }: { x: number; y: number; items: MenuItem[]; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  // Measure the real menu size post-mount and clamp within the viewport; falls
  // back to a width/row estimate for the first paint so it never opens overflowing.
  const estW = 200, estH = Math.min(items.length * 40 + 16, 360)
  const [pos, setPos] = useState(() => ({
    left: Math.min(x, Math.max(8, window.innerWidth - estW - 8)),
    top: Math.min(y, Math.max(8, window.innerHeight - estH - 8)),
  }))
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const r = el.getBoundingClientRect()
    setPos({
      left: Math.min(x, Math.max(8, window.innerWidth - r.width - 8)),
      top: Math.min(y, Math.max(8, window.innerHeight - r.height - 8)),
    })
  }, [x, y, items.length])
  useEffect(() => {
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose() }
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onEsc)
    window.addEventListener('scroll', onClose, true)
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onEsc); window.removeEventListener('scroll', onClose, true) }
  }, [onClose])

  return createPortal(
    <div ref={ref} className="fixed z-50 min-w-[160px] rounded-lgi bg-surface-container p-s"
      style={{ left: pos.left, top: pos.top, boxShadow: 'var(--shadow-menu)' }}>
      {items.map((it) => (
        <button key={it.label} type="button"
          onClick={() => { it.onClick(); onClose() }}
          className="flex w-full items-center gap-s rounded-md px-m py-2 text-left text-[0.8125rem] transition-colors hover:bg-surface-high"
          style={{ color: it.tone === 'danger' ? 'var(--color-danger)' : 'var(--color-on-surface)' }}>
          <it.icon size={15} className="shrink-0" />
          <span className="truncate">{it.label}</span>
        </button>
      ))}
    </div>,
    document.body,
  )
}
