import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { X, FilePlus2, FolderPlus } from 'lucide-react'
import { Button } from '../../../../ui/Button'
import { IconButton } from '../../../../ui/IconButton'
import { FieldError } from '../../../../ui/forms'
import { confirm } from '../../../../ui/dialog'
import { api, type FsEntry } from '../../../../lib/api'
import { useVisiblePoll } from '../../../../lib/useVisiblePoll'
import { FileTree } from '../../../files/browse/FileTree'
import { useDirCache, useGitStatus } from '../../../files/filesData'

// ── The cockpit's workspace tree ───────────────────────────────────────────────────────────────
//
// The workspace's files, or a project's own files dir with the engine's bookkeeping hidden when no
// workspace is bound: git status on each row, new files and folders, a move or a rename, a delete,
// files dropped in to upload, and the worker's new files appearing while it runs.

const EMPTY_ARTIFACTS = new Set<string>()

// Engine bookkeeping that lives in a project's files dir alongside the worker's
// real deliverables — hidden from the cockpit tree when rooted there, so the user
// sees what they built (requirements.md etc.), not the plumbing.
export const PROJECT_DIR_HIDDEN = new Set(['brief.md', 'status.json', 'findings', 'verdicts', 'FINDINGS.md', 'guidance.txt', 'questions.json', 'nudges.json', 'STOP', 'plan_session.json', 'plan_steps.json', 'step_artifact.json', 'plan.json'])

// Dynamically-named engine bookkeeping the exact set above can't enumerate: a
// per-task steer in parallel mode is written as `guidance_<task_id>.txt`. Hidden
// by prefix so it doesn't leak into a no-workspace project's file tree.
export const PROJECT_DIR_HIDDEN_PREFIXES = new Set(['guidance_'])

// VCS/build/cache cruft suppressed at EVERY level of the cockpit's mini-IDE tree
// so the user sees their code, not tool plumbing (VSCode's default files.exclude
// spirit). Applies in both bound-workspace and project-dir modes.
export const CODE_TREE_NOISE = new Set(['.git', '.hg', '.svn', '__pycache__', '.pytest_cache',
  '.mypy_cache', '.ruff_cache', '.tox', 'node_modules', '.venv', 'venv', '.DS_Store',
  '.idea', '.next', '.turbo', '.cache', '.gradle', '__snapshots__', '.terraform'])

/** Workspace file tree rooted at the project's workspace dir, or the project's own
 *  files dir when no workspace is bound (isProjectDir) — in which case engine
 *  bookkeeping files are hidden so only the deliverables show. */
export function WorkspaceTree({ ws, running, isProjectDir }: { ws: string; running: boolean; isProjectDir: boolean }) {
  const dirs = useDirCache()
  const [gitNonce, setGitNonce] = useState(0)
  const { statuses, state: gitState } = useGitStatus(ws || null, gitNonce)
  // While the worker runs it writes new files across the workspace (root AND
  // subdirs like src/ tests/); the dir cache + git status would otherwise stay
  // stale until a manual reload. Slow-poll (visible only): invalidate the whole
  // loaded subtree (not just the root — else a file the worker drops into an
  // expanded subdir never appears there) + bump git status so freshly created
  // files surface with their badge. Files-page-style live refresh.
  useVisiblePoll(() => {
    if (ws) { dirs.invalidateSubtree(ws); setGitNonce((n) => n + 1) }
  }, running && ws ? 8000 : null)
  // A manual save in the editor → refresh git badges immediately. The poll above only
  // runs while RUNNING, so on a paused/idle project (where the USER is editing) a save
  // would otherwise leave the tree's git badges stale until something else triggered it.
  useEffect(() => {
    const onSaved = () => { if (ws) { dirs.invalidate(ws); setGitNonce((n) => n + 1) } }
    window.addEventListener('ne:code-file-saved', onSaved)
    return () => window.removeEventListener('ne:code-file-saved', onSaved)
  }, [ws, dirs])
  // Detect a worker DELETING a tracked file (git status code contains 'D'): play an
  // autonomous-backspace erase animation of its pre-delete (HEAD) content, once per
  // path. Only while running; the git original is fetched lazily.
  const erasedRef = useRef<Set<string>>(new Set())
  // Deletions present in the FIRST loaded status snapshot are PRE-EXISTING (the worker
  // removed them in an earlier cycle, before this mount) — they must NOT replay their
  // erase animation just because the cockpit was reopened on a still-running project
  // (erasedRef resets on remount, so without this every already-deleted file animates
  // as if it just happened). Seed them as already-erased on the first load; only a
  // deletion that appears in a LATER snapshot is genuinely live → animates. Mirrors the
  // write/diff-reveal path's "seed the baseline on first sight" guard.
  const eraseSeededRef = useRef(false)
  useEffect(() => {
    if (!running || gitState !== 'loaded') return
    const seeding = !eraseSeededRef.current
    for (const [path, code] of Object.entries(statuses)) {
      if (!code.includes('D') || erasedRef.current.has(path)) continue
      erasedRef.current.add(path)
      if (seeding) continue  // pre-existing deletion on first load → record, don't animate
      api.fileGitOriginal(path).then((r) => {
        if (r?.content?.trim()) {
          window.dispatchEvent(new CustomEvent('ne:code-erase-file', {
            detail: { path, name: path.split('/').pop(), text: r.content },
          }))
        }
      }).catch(() => {})
    }
    eraseSeededRef.current = true
  }, [statuses, running, gitState])
  // Bridge file-open to the center editor via a custom event (kept simple: the
  // center editor listens for ne:code-open-file). Avoids prop-drilling the tabs.
  const openFile = useCallback((entry: FsEntry) => {
    window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: entry }))
  }, [])
  // Track the latest-opened file so the tree auto-expands to reveal it (its own
  // clicks + the worker's follow-open both flow through ne:code-open-file). This
  // is what makes a deeply-nested worker-touched file visible in the tree, not
  // buried in collapsed folders.
  const [activePath, setActivePath] = useState<string | null>(null)
  useEffect(() => {
    const onOpen = (e: Event) => {
      const d = (e as CustomEvent).detail as { path?: string; is_dir?: boolean } | null
      if (d?.path && !d.is_dir) setActivePath(d.path)
    }
    window.addEventListener('ne:code-open-file', onOpen as EventListener)
    return () => window.removeEventListener('ne:code-open-file', onOpen as EventListener)
  }, [])
  // Inline create-new at the workspace root — the cockpit's mini-IDE equivalent of
  // the Files page's New file / New folder (the tree itself only renames/deletes).
  const [creating, setCreating] = useState<'file' | 'dir' | null>(null)
  const [newName, setNewName] = useState('')
  const [createErr, setCreateErr] = useState('')
  const createErrId = useId()
  // In-flight guard: the create input fires submitCreate on BOTH Enter and onBlur, so
  // Enter (which can blur) — or any click-away during the async create — would fire a
  // second fileCreate that hits the just-created name as a 409 "already exists". Ignore
  // re-entry while a create is in flight (mirrors the acting-guard pattern).
  const creatingInFlight = useRef(false)
  // Surface rename/delete failures (permission denied, file vanished mid-op, ws
  // unmounted) instead of swallowing them as a silent no-op — matches the create
  // error above + the Files page, which reports "Delete failed: …".
  const [treeErr, setTreeErr] = useState('')
  // Delete a tree entry behind a themed confirm (the shared imperative dialog —
  // never a native, off-theme window.confirm).
  const doDelete = async (entry: FsEntry) => {
    if (!(await confirm({
      title: entry.is_dir ? `Delete folder "${entry.name}"?` : `Delete file "${entry.name}"?`,
      body: entry.is_dir
        ? "This deletes the folder and all its contents. This can't be undone."
        : "This can't be undone.",
      danger: true, confirmLabel: 'Delete',
    }))) return
    setTreeErr('')
    let ok = false
    try {
      await api.fileDelete(entry.path)
      ok = true
      // Close any editor tab(s) for the deleted file (or files under a deleted dir) —
      // ONLY on success. The close was firing unconditionally (before the await settled
      // its outcome), so a FAILED delete (permission / vanished / 5xx) still closed the
      // tab for a file that's still on disk, losing the user's open view + any edits.
      window.dispatchEvent(new CustomEvent('ne:code-close-file', { detail: { path: entry.path } }))
    }
    catch (e) { setTreeErr(`Couldn't delete "${entry.name}": ${(e as Error).message || 'unknown error'}`) }
    // Only refresh the tree/git when something actually changed — a failed delete left
    // the disk untouched, so re-listing is wasted work (and the entry correctly stays).
    if (ok) {
      dirs.invalidate(entry.path.slice(0, entry.path.length - entry.name.length).replace(/\/$/, '') || ws)
      dirs.invalidate(ws)
      window.dispatchEvent(new CustomEvent('ne:code-file-saved'))  // refresh git badges + Changes
    }
  }
  const submitCreate = async () => {
    if (creatingInFlight.current) return
    const name = newName.trim()
    if (!name || !creating) { setCreating(null); setNewName(''); setCreateErr(''); return }
    creatingInFlight.current = true
    try {
      const r = await api.fileCreate(ws, name, creating)
      if (creating === 'file') window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: { name, path: r.path, is_dir: false } }))
      setCreating(null); setNewName(''); setCreateErr(''); dirs.invalidate(ws)
      // Refresh git badges + the Changes tab (a new file is untracked → 'U' badge).
      window.dispatchEvent(new CustomEvent('ne:code-file-saved'))
    } catch (e) {
      // Keep the input open + show why (duplicate name → 409, slash/blocked → 400)
      // so the user can correct it instead of a silent no-op.
      const msg = (e as Error).message || 'Could not create'
      setCreateErr(/already exists/i.test(msg) ? 'A file or folder with that name already exists.'
        : /invalid name/i.test(msg) ? 'Invalid name — no slashes; pick a different name.'
        : msg)
      dirs.invalidate(ws)
    } finally {
      creatingInFlight.current = false
    }
  }
  const cancelCreate = () => { setCreating(null); setNewName(''); setCreateErr('') }
  if (!ws) return <p data-type="body-s" className="px-3 py-6 text-center text-on-surface-low">No files yet for this project.</p>
  return (
    <div className="p-1">
      {/* create-new row: icon buttons reveal an inline name input (Enter creates,
          Escape cancels). Hidden for a no-workspace project dir (isProjectDir). */}
      {!isProjectDir && (
        <div className="flex flex-col gap-1 px-1.5 pb-1">
         <div className="flex items-center gap-1">
          {creating ? (
            // Raw: TextInput takes no onBlur, and leaving the field creates the entry.
            <input autoFocus value={newName} onChange={(e) => { setNewName(e.target.value); if (createErr) setCreateErr('') }}
              onKeyDown={(e) => { if (e.key === 'Enter') submitCreate(); if (e.key === 'Escape') cancelCreate() }}
              onBlur={() => { if (!createErr) submitCreate() }} placeholder={creating === 'file' ? 'new-file.ext' : 'new-folder'}
              aria-invalid={createErr ? true : undefined} aria-describedby={createErr ? createErrId : undefined}
              data-type="body-s" className={`h-7 min-w-0 flex-1 rounded-md bg-surface-high px-2 text-on-surface outline-none focus:ring-2 placeholder:text-on-surface-low ${createErr ? 'focus:ring-danger/50 ring-2 ring-danger/40' : 'focus:ring-primary'}`} />
          ) : (
            <>
              {/* The raw 0.75rem size stays: Button carries its own data-type role and takes no override — no call-site element here to carry caption (see typeScaleRatchet). */}
              <Button variant="ghost" size="xs" title="New file" onClick={() => { setCreating('file'); setNewName('') }}
                className="gap-1 px-1.5 text-[0.75rem] text-on-surface-low"><FilePlus2 size={13} /> File</Button>
              <Button variant="ghost" size="xs" title="New folder" onClick={() => { setCreating('dir'); setNewName('') }}
                className="gap-1 px-1.5 text-[0.75rem] text-on-surface-low"><FolderPlus size={13} /> Folder</Button>
            </>
          )}
         </div>
         {createErr && <FieldError id={createErrId} className="px-0.5">{createErr}</FieldError>}
        </div>
      )}
      {/* rename/delete failure — dismissable, sits above the tree (works in both
          bound-workspace and project-dir modes, where the create row is hidden). */}
      {treeErr && (
        <div role="alert" data-type="caption" className="mx-1.5 mb-1 flex items-start gap-1.5 px-0.5 text-danger">
          <span className="min-w-0 flex-1">{treeErr}</span>
          <IconButton icon={X} label="Dismiss" onClick={() => setTreeErr('')} size={18} iconSize={11} className="shrink-0" />
        </div>
      )}
      <FileTree dirs={dirs} rootPath={ws} activePath={activePath} gitStatuses={statuses}
        onOpenFile={openFile} artifactPaths={EMPTY_ARTIFACTS}
        hideNames={isProjectDir ? PROJECT_DIR_HIDDEN : undefined}
        hidePrefixes={isProjectDir ? PROJECT_DIR_HIDDEN_PREFIXES : undefined}
        hideNamesDeep={CODE_TREE_NOISE}
        emptyLabel={isProjectDir
          ? (running ? 'No files yet — the worker will create them here.' : 'No files yet — files the worker creates will appear here once it runs.')
          : 'Empty'}
        onRename={async (entry, nextName) => {
          const parent = entry.path.slice(0, entry.path.length - entry.name.length).replace(/\/$/, '')
          setTreeErr('')
          try {
            const dest = parent ? `${parent}/${nextName}` : nextName
            await api.fileMove(entry.path, dest)
            // Renaming an OPEN file shouldn't make it vanish from the editor — if it was
            // a tab, follow it to the new path (preserving active state); if not, this is
            // a no-op. A folder rename re-roots its open descendants too. (The handler
            // falls back to closing the stale tab when it can't re-root, never strands one.)
            window.dispatchEvent(new CustomEvent('ne:code-renamed', { detail: { from: entry.path, to: dest, isDir: !!entry.is_dir } }))
          }
          catch (e) {
            // Friendly copy for the common name-collision case (backend: "destination
            // already exists") — matches the create row's mapping rather than leaking the
            // terse server string.
            const msg = (e as Error).message || 'unknown error'
            setTreeErr(/already exists/i.test(msg)
              ? `Couldn't rename "${entry.name}" — a file or folder named "${nextName}" already exists here.`
              : `Couldn't rename "${entry.name}": ${msg}`)
          }
          // Invalidate the renamed item's PARENT (where the entry lived), not just the
          // workspace root — a rename in a subdir wouldn't refresh there otherwise.
          dirs.invalidate(parent || ws); dirs.invalidate(ws)
          window.dispatchEvent(new CustomEvent('ne:code-file-saved'))  // refresh git badges + Changes
        }}
        onDelete={(entry) => { void doDelete(entry) }}
        onUpload={async (dirEntry, files) => {
          // Surface an upload failure (too large, permission denied, dir gone) in the
          // same dismissable tree-error line as create/rename/delete — was a silent
          // no-op (drop files, nothing happens, no reason). fileUpload returns
          // {ok:false,error} on an HTTP error (doesn't throw) AND can throw on network.
          if (!files.length) return
          setTreeErr('')
          try {
            const r = await api.fileUpload(dirEntry.path, files)
            if (!r.ok) setTreeErr(`Couldn't upload to ${dirEntry.name}: ${r.error || 'unknown error'}`)
          } catch (e) {
            setTreeErr(`Couldn't upload to ${dirEntry.name}: ${(e as Error).message || 'unknown error'}`)
          }
          dirs.invalidate(dirEntry.path); dirs.invalidate(ws)
          window.dispatchEvent(new CustomEvent('ne:code-file-saved'))  // refresh git badges + Changes
        }}
        onCreate={async (dirEntry, name, kind) => {
          // Create a file/folder INSIDE the right-clicked dir (not just the root) — the
          // mini-IDE "New file/folder here". Opens a new file in the editor; surfaces
          // failures (dup/invalid) in the same dismissable tree-error line.
          setTreeErr('')
          try {
            const r = await api.fileCreate(dirEntry.path, name, kind)
            if (kind === 'file') window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: { name, path: r.path, is_dir: false } }))
          } catch (e) {
            const msg = (e as Error).message || 'unknown error'
            setTreeErr(/already exists/i.test(msg) ? `"${name}" already exists in ${dirEntry.name}.`
              : /invalid name/i.test(msg) ? `Invalid name "${name}" — no slashes.` : `Couldn't create "${name}": ${msg}`)
          }
          dirs.invalidate(dirEntry.path); dirs.invalidate(ws)
          window.dispatchEvent(new CustomEvent('ne:code-file-saved'))  // refresh git badges + Changes
        }} />
    </div>
  )
}
