import { useCallback, useEffect, useMemo, useState } from 'react'
import { Folder, FolderPlus, CornerLeftUp, Loader2, Check, GitBranch, Search, X } from 'lucide-react'
import { Modal } from '../../ui/Modal'
import { Button } from '../../ui/Button'
import { api } from '../../lib/api'

/** Filesystem navigator for choosing a Code project's workspace directory.
 *
 *  Brownfield: pick an EXISTING directory (the codebase to work in). Greenfield:
 *  navigate to a parent and create a NEW folder for the project. Walks arbitrary
 *  non-sensitive dirs via /api/browse-dirs (not the dashboard allowlist), so any
 *  real project on disk is reachable. The chosen absolute path is returned to the
 *  caller, which stores it on the code project's workspace_dir.
 */
export function WorkspacePicker({ mode, allowCreate, onPick, onClose }: {
  mode: 'brownfield' | 'greenfield'
  // Force-enable the "New folder here" create path regardless of mode. Greenfield
  // always allows it; a project workspace bind is brownfield (it shows git status +
  // can use an existing codebase) but ALSO needs create — a project built from
  // scratch has no workspace dir yet, so without this the user could only bind a
  // pre-existing folder and never make the new one.
  allowCreate?: boolean
  onPick: (absPath: string) => void
  onClose: () => void
}) {
  const canCreate = mode === 'greenfield' || !!allowCreate
  const [path, setPath] = useState('')          // current dir being browsed
  const [parent, setParent] = useState('')
  const [dirs, setDirs] = useState<{ name: string; path: string; is_repo?: boolean }[]>([])
  // Whether the CURRENT dir is inside a git repo — surfaced near "Use this folder" so a
  // brownfield pick confirms it lands on a version-tracked codebase (a non-repo pick
  // means no diff/history; see the cockpit Changes tab).
  const [inRepo, setInRepo] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  // In-flight guard for the folder-create POST: createDir is async, so without it a
  // double-click / held Enter fires two creates — the second hits the now-existing
  // dir and surfaces a spurious "already exists" error AFTER onPick already advanced
  // the wizard. Disables the submit + ignores re-entry until the call settles.
  const [submitting, setSubmitting] = useState(false)
  // The path bar is an editable input so the user can type/paste a known absolute
  // path (e.g. their repo root) and jump straight there — clicking down from root
  // through many levels is tedious when you already know the destination.
  const [pathDraft, setPathDraft] = useState('')
  // Filter within the current dir — real parents (home, a workspace root, /usr/local)
  // routinely hold dozens of folders, so scanning the whole list to find one repo is
  // tedious. A substring filter narrows it in place; cleared on every navigation.
  const [filter, setFilter] = useState('')

  // `to` is the destination; `from` is the dir we're still actually in (the current
  // `path`) so a FAILED jump can restore the path-bar draft to it — else the bar keeps
  // showing the bad path the user typed while the list still shows the old dir (the bar
  // and list silently disagree until a blur resets it).
  const browse = useCallback(async (to?: string, from?: string) => {
    setLoading(true); setError(null)
    try {
      const r = await api.browseDirs(to)
      setPath(r.path); setParent(r.parent); setDirs(r.dirs); setPathDraft(r.path); setFilter(''); setInRepo(!!r.in_repo)
    } catch (e) {
      setError((e as Error).message || 'Could not open that directory')
      // Snap the path bar back to where we still are, so it doesn't keep showing the
      // rejected path while the list below shows the prior (valid) directory.
      if (from !== undefined) setPathDraft(from)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void browse() }, [browse])

  const shownDirs = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    return needle ? dirs.filter((d) => d.name.toLowerCase().includes(needle)) : dirs
  }, [dirs, filter])

  async function createFolder() {
    if (submitting) return
    const name = newName.trim()
    if (!name) return
    // "New folder here" must create exactly ONE folder in the current dir. A name
    // with slashes silently created a nested tree (foo/bar/baz) and buried the
    // workspace at the deepest level; '.'/'..' escape the current dir. Reject those
    // with an inline reason instead of a surprising directory chain.
    if (/[/\\]/.test(name) || name === '.' || name === '..') {
      setError('Folder name can’t contain slashes — it’s created here. Navigate into a folder first if you want it nested.')
      return
    }
    const target = `${path.replace(/\/$/, '')}/${name}`
    setSubmitting(true)
    try {
      // Bind the RESOLVED path the backend returns (realpath — e.g. /tmp →
      // /private/tmp on macOS), not the unresolved `target` we sent. The file tree /
      // git status / follow-worker all emit realpath paths, so binding the unresolved
      // form as workspace_dir reintroduces the exact /tmp-vs-/private/tmp mismatch the
      // cockpit then has to paper over (canonPath/rel/wsBase). Resolve at the source.
      const r = await api.createDir(target)
      setCreating(false); setNewName(''); setError(null)
      onPick(r?.path || target)  // greenfield: the freshly-created dir IS the workspace
    } catch (e) {
      setError((e as Error).message || 'Could not create the folder')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title={mode === 'brownfield' ? 'Choose the codebase directory' : 'Choose where to create the project'}
      icon={<Folder size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex min-h-[360px] flex-col gap-3">
        {/* current path + up */}
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => parent && parent !== path && browse(parent)}
            disabled={!parent || parent === path} aria-label="Up one level"
            className="inline-flex size-8 shrink-0 items-center justify-center rounded-md text-on-surface-low hover:bg-surface-high hover:text-on-surface disabled:opacity-40">
            <CornerLeftUp size={16} />
          </button>
          {/* Editable: type/paste an absolute path + Enter to jump there. Blurring
              or pressing Escape resets to the current dir. */}
          <input value={pathDraft} onChange={(e) => setPathDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { const t = pathDraft.trim(); if (t && t !== path) void browse(t, path) }
              else if (e.key === 'Escape') setPathDraft(path)
            }}
            onBlur={() => setPathDraft(path)}
            spellCheck={false} autoCapitalize="off" autoCorrect="off" aria-label="Workspace path"
            placeholder="/absolute/path/to/folder"
            className="min-w-0 flex-1 rounded-md bg-surface-high px-2.5 py-1.5 font-mono text-[0.8125rem] text-on-surface-var outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        </div>

        {/* in-dir filter — only worth showing once the list is long enough to scan */}
        {!loading && dirs.length > 8 && (
          <div className="flex items-center gap-2 rounded-md bg-surface-high px-2.5 py-1.5">
            <Search size={14} className="shrink-0 text-on-surface-low" />
            <input value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter folders"
              placeholder={`Filter ${dirs.length} folders…`} spellCheck={false} autoCapitalize="off" autoCorrect="off"
              onKeyDown={(e) => {
                // Enter opens (navigates into) the first matching folder — the natural
                // "type a few chars → Enter → I'm in" flow (the path bar already has its
                // own Enter-to-jump). Escape clears the filter.
                if (e.key === 'Enter' && shownDirs.length > 0) { e.preventDefault(); void browse(shownDirs[0].path) }
                else if (e.key === 'Escape' && filter) { e.preventDefault(); setFilter('') }
              }}
              className="min-w-0 flex-1 bg-transparent text-[0.8125rem] text-on-surface outline-none placeholder:text-on-surface-low" />
            {filter && (
              <button type="button" onClick={() => setFilter('')} aria-label="Clear filter"
                className="shrink-0 text-on-surface-low hover:text-on-surface"><X size={13} /></button>
            )}
          </div>
        )}

        {/* dir list */}
        <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-outline-variant/40">
          {loading ? (
            <div className="flex h-40 items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>
          ) : dirs.length === 0 ? (
            <div className="flex h-40 items-center justify-center px-4 text-center text-on-surface-low text-[0.8125rem]">No sub-folders here.</div>
          ) : shownDirs.length === 0 ? (
            <div className="flex h-40 items-center justify-center px-4 text-center text-on-surface-low text-[0.8125rem]">No folders match “{filter.trim()}”.</div>
          ) : (
            <div className="flex flex-col">
              {shownDirs.map((d) => (
                // The row opens the folder (navigate in); a trailing "Use" button
                // selects it directly — so the user can pick a repo they can SEE in
                // the list without having to navigate into it then hit the footer.
                <div key={d.path} className="group/row flex items-center gap-2 px-3 py-2 text-[0.8125rem] text-on-surface-var transition-colors hover:bg-surface-high">
                  <button type="button" onClick={() => browse(d.path)}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left hover:text-on-surface" title={`Open ${d.name}`}>
                    <Folder size={15} className="shrink-0 text-on-surface-low" />
                    <span className="truncate">{d.name}</span>
                    {d.is_repo && (
                      <span className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[0.65rem] font-medium"
                        style={{ color: 'var(--color-primary)', border: '1px solid color-mix(in srgb, var(--color-primary) 40%, transparent)' }}
                        title="This folder is a git repository">
                        <GitBranch size={10} /> repo
                      </span>
                    )}
                  </button>
                  {/* Faintly visible by default (opacity-50), full on hover/focus — a
                      pure opacity-0 hid this primary "select without navigating in"
                      action from touch users entirely (no hover, no easy focus), leaving
                      the footer "Use this folder" as their only path. */}
                  <button type="button" onClick={() => onPick(d.path)}
                    className="shrink-0 rounded px-1.5 py-0.5 text-[0.7rem] text-on-surface-low opacity-50 transition-opacity hover:bg-surface-highest hover:text-primary hover:opacity-100 focus-visible:opacity-100 group-hover/row:opacity-100"
                    title={mode === 'brownfield' ? `Use ${d.name} as the codebase` : `Use ${d.name} as the project home`}>
                    Use
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {error && (
          <div role="alert" className="rounded-lg px-3 py-2 text-[0.8125rem]"
            style={{ background: 'color-mix(in srgb, var(--color-danger) 8%, transparent)', color: 'var(--color-danger)' }}>{error}</div>
        )}

        {/* create a new folder here (greenfield always; brownfield when allowCreate) */}
        {canCreate && (
          creating ? (
            <div className="flex items-center gap-2">
              <input autoFocus value={newName} onChange={(e) => { setNewName(e.target.value); if (error) setError(null) }}
                onKeyDown={(e) => { if (e.key === 'Enter') createFolder(); else if (e.key === 'Escape' && !submitting) { setCreating(false); setError(null) } }}
                disabled={submitting} placeholder="new-project-folder"
                className="h-9 min-w-0 flex-1 rounded-md bg-surface-high px-2.5 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 disabled:opacity-60" />
              <Button size="sm" onClick={createFolder} disabled={!newName.trim() || submitting}>
                {submitting ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Create + use
              </Button>
            </div>
          ) : (
            <button type="button" onClick={() => setCreating(true)}
              className="inline-flex items-center gap-1.5 self-start rounded-md px-2 py-1.5 text-[0.8125rem] text-on-surface-low hover:text-on-surface">
              <FolderPlus size={15} /> New folder here
            </button>
          )
        )}

        {/* footer: select the current dir. Brownfield = the codebase to work in;
            greenfield = use an existing (e.g. pre-made empty) folder as the new
            project's home, alongside the "New folder here" create-new path above. */}
        <div className="flex items-center justify-between gap-2 border-t border-outline-variant/40 pt-3">
          <span className="min-w-0 text-on-surface-low text-[0.75rem]">
            {mode === 'brownfield' ? 'Open a folder to navigate; use the current one as the codebase.' : 'Create a new folder, or use the current one as the project home.'}
            {/* Brownfield: confirm the current dir's git status — a non-repo pick means
                no diff/history tracking in the cockpit. Only shown once a dir is loaded. */}
            {mode === 'brownfield' && path && !loading && (
              inRepo
                ? <span className="ml-1.5 inline-flex items-center gap-1" style={{ color: 'var(--color-ok)' }}><GitBranch size={11} /> git repo</span>
                : <span className="ml-1.5 text-on-surface-low/70">· not a git repo (changes won’t be version-tracked)</span>
            )}
          </span>
          <Button size="sm" onClick={() => path && onPick(path)} disabled={!path || loading}>
            <Check size={14} /> Use this folder
          </Button>
        </div>
      </div>
    </Modal>
  )
}
