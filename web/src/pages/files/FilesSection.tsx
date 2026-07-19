import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Box, Search, FilePlus2, FolderPlus, RefreshCw, GitBranch, Files as FilesIcon, X, Loader2, CornerDownRight, PanelRight,
} from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { SidePanel } from '../../ui/SidePanel'
import { EmptyState, Loading } from '../../ui/ListScaffold'
import { Modal } from '../../ui/Modal'
import { Segmented, TextInput } from '../tasks/formControls'
import { Button } from '../../ui/Button'
import { api, type Artifact, type FsEntry, type ContentMatch } from '../../lib/api'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { confirm } from '../../ui/dialog'
import { notify } from '../../app/appSdk'
import { useFileRoots, useDirCache, useGitStatus } from './filesData'
import { FileTree } from './browse/FileTree'
import { FileViewer, type FileViewerHandle } from './browse/FileViewer'
import { PathBar } from './browse/PathBar'
import { useFileTabs } from './browse/useFileTabs'
import { ArtifactList } from './artifacts/ArtifactList'
import { ArtifactViewer } from './artifacts/ArtifactViewer'
import { newSessionTarget } from '../../ui/content/commentTarget'
import { baseName, fileIcon } from './fileMeta'

const ARTIFACTS_TAB = 'artifacts'
const TAB_KEY = 'files-tab'

/** Unified Files + Artifacts page. ONE top-right tab strip = the file roots
 *  (Workspace/Home/Outbox) + Artifacts. Root tabs show the explorer + a rich
 *  multi-tab editor/preview; the Artifacts tab shows rendered, versioned
 *  artifacts. No roots sidebar (the top-bar tabs ARE the root selector). */
export function FilesSection({ sub, navigate, query: routeQuery, setQuery }: RouteProps) {
  const { roots, loading: rootsLoading } = useFileRoots()
  // A deep-link `#/files/<slug>` opens the Artifacts tab on that artifact.
  const deepSlug = (sub || '').split('/')[0] || ''
  // The explorer's current directory rides the URL (?dir=<abspath>, push → Back =
  // up a dir; deep-link/refresh restores it). It is the SINGLE source of truth for
  // where the explorer is pointing — a bare ?dir-less route means "the root tab".
  // The dir lives under a registered root (Home/Workspace), so the allowlist permits
  // it. Content search rides ?q/?include (replace — an in-place refinement).
  const [dir, setDir] = useQueryParam(routeQuery, setQuery, 'dir', '')
  const [tab, setTab] = useState<string>(() => (deepSlug ? ARTIFACTS_TAB : (localStorage.getItem(TAB_KEY) || '')))
  useEffect(() => { if (tab) localStorage.setItem(TAB_KEY, tab) }, [tab])

  const isArtifacts = tab === ARTIFACTS_TAB
  // The explorer browses ?dir when set, else the root tab. Navigating the path bar
  // writes ?dir (push → Back = up a dir); switching root tabs clears it so a stale
  // subdir from one root can't leak into another (the seed effect would otherwise
  // flip the tab back). A user-driven tab/artifact switch goes through switchTab.
  const activeRoot = isArtifacts ? '' : (dir || tab)
  const switchTab = useCallback((t: string) => {
    setTab(t)
    if (dir) setQuery({ dir: null }, { replace: true })
  }, [dir, setQuery])

  const dirs = useDirCache()
  const [nonce, setNonce] = useState(0)
  const { branch, statuses } = useGitStatus(activeRoot || null, nonce)

  // Multi-tab open files.
  const fileTabs = useFileTabs()
  const viewerRefs = useRef(new Map<string, FileViewerHandle>())

  // Content (grep) search — lives in the always-visible header box. URL-backed
  // (?q/?include, replace) so a search is shareable + refresh-stable.
  const [grep, setGrep] = useQueryParam(routeQuery, setQuery, 'q', '', { replace: true })
  const [include, setInclude] = useQueryParam(routeQuery, setQuery, 'include', '', { replace: true })
  const [results, setResults] = useState<ContentMatch[]>([])
  const [searchEngine, setSearchEngine] = useState<'rg' | 'python' | ''>('')
  const [searchBusy, setSearchBusy] = useState(false)

  // Inline create row + artifact-name modal.
  const [creating, setCreating] = useState<null | 'file' | 'dir'>(null)
  const [newName, setNewName] = useState('')
  // Inline error banner for file ops (move/delete/upload).
  const [fileErr, setFileErr] = useState<string | null>(null)
  // Live progress for large (chunked/resumable) uploads; small files finish in one POST.
  const [uploadRows, setUploadRows] = useState<{ name: string; pct: number }[]>([])
  // Controller for the in-flight upload batch, so a Cancel button can abort it (parity
  // with the composer attach). Cleared when the batch settles.
  const uploadAbortRef = useRef<AbortController | null>(null)
  const [rootDrop, setRootDrop] = useState(false)
  const [artModal, setArtModal] = useState<{ entry: FsEntry; content: string; name: string } | null>(null)

  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [artLoading, setArtLoading] = useState(false)
  const [activeArtifact, setActiveArtifact] = useState<Artifact | null>(null)
  // The file explorer is a right-docked, hidable panel (standard SidePanel),
  // open by default so browsing works on arrival.
  const [explorerOpen, setExplorerOpen] = useState(true)

  // When ?dir is set (deep-link, refresh, or in-tree nav), keep the root tab aligned
  // to the dir's OWNING root (longest path prefix) so the explorer + path bar show the
  // right root. Runs whenever ?dir or roots change — no one-shot ref needed, because
  // ?dir is now the durable source (not a fleeting seed).
  useEffect(() => {
    if (!dir || !roots.length) return
    const owning = roots.filter((r) => dir === r.path || dir.startsWith(r.path.replace(/\/$/, '') + '/'))
      .sort((a, b) => b.path.length - a.path.length)[0]
    if (owning && owning.path !== tab) setTab(owning.path)
  }, [dir, roots]) // eslint-disable-line react-hooks/exhaustive-deps
  // Default to the first root once roots arrive, unless the saved tab is valid.
  useEffect(() => {
    if (!roots.length) return
    const valid = tab === ARTIFACTS_TAB || roots.some((r) => r.path === tab)
    if (!valid) setTab(roots[0].path)
  }, [roots, tab])

  const loadArtifacts = useCallback(async () => {
    setArtLoading(true)
    try { setArtifacts(await api.artifacts()) } catch { setArtifacts([]) }
    finally { setArtLoading(false) }
  }, [])
  useEffect(() => { loadArtifacts() }, [loadArtifacts])

  // Deep-link `#/files/<slug>` → select that artifact once the list is in.
  useEffect(() => {
    if (!deepSlug || !artifacts.length) return
    const match = artifacts.find((a) => a.slug === deepSlug)
    if (match) { setTab(ARTIFACTS_TAB); setActiveArtifact(match) }
  }, [deepSlug, artifacts])

  const artifactPaths = useMemo(() => new Set(artifacts.filter((a) => a.source_path).map((a) => a.source_path)), [artifacts])

  const refresh = useCallback(() => { dirs.invalidate(activeRoot); setNonce((n) => n + 1) }, [dirs, activeRoot])

  const openByPath = useCallback((path: string, rootPath?: string) => {
    if (rootPath) setTab(rootPath)
    else setTab((t) => (t === ARTIFACTS_TAB ? (roots[0]?.path ?? '') : t))
    fileTabs.open({ name: baseName(path), path, is_dir: false })
  }, [fileTabs, roots])

  // Debounced content search under the active root.
  useEffect(() => {
    if (isArtifacts || !activeRoot || grep.trim().length < 2) { setResults([]); setSearchEngine(''); return }
    setSearchBusy(true)
    const t = setTimeout(() => {
      api.fileContentSearch(activeRoot, grep.trim(), include.trim() || undefined)
        .then((r) => { setResults(r.results); setSearchEngine(r.engine) })
        .catch(() => { setResults([]); setSearchEngine('') })
        .finally(() => setSearchBusy(false))
    }, 250)
    return () => clearTimeout(t)
  }, [grep, include, activeRoot, isArtifacts])

  // Keyboard: ⌘S saves the focused tab, ⌘F focuses the search box.
  const searchRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return
      const k = e.key.toLowerCase()
      if (k === 's' && !isArtifacts && fileTabs.activePath) { e.preventDefault(); viewerRefs.current.get(fileTabs.activePath)?.save() }
      else if (k === 'f' && !isArtifacts) { e.preventDefault(); searchRef.current?.focus() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isArtifacts, fileTabs.activePath])

  const saveAsArtifact = (entry: FsEntry, content: string) => setArtModal({ entry, content, name: baseName(entry.path) })
  const confirmArtifact = async () => {
    if (!artModal || !artModal.name.trim()) return
    try {
      const created = await api.createArtifact({ name: artModal.name.trim(), content: artModal.content, source: 'manual', source_path: artModal.entry.path, kind: guessKind(artModal.entry.name) })
      setArtModal(null); await loadArtifacts()
      setTab(ARTIFACTS_TAB); setActiveArtifact(created)
    } catch (e) { notify(`Could not save artifact: ${(e as Error).message}`, 'error') }
  }

  const openSourceFile = useCallback((path: string) => {
    const owning = roots.find((r) => path === r.path || path.startsWith(r.path + '/'))
    openByPath(path, owning?.path ?? roots[0]?.path)
  }, [roots, openByPath])

  const submitCreate = async () => {
    const name = newName.trim()
    if (!name || !creating || !activeRoot) { setCreating(null); setNewName(''); return }
    try {
      const r = await api.fileCreate(activeRoot, name, creating)
      setCreating(null); setNewName(''); refresh()
      if (creating === 'file') fileTabs.open({ name, path: r.path, is_dir: false })
    } catch (e) { notify(`Could not create: ${(e as Error).message}`, 'error') }
  }

  const onRename = useCallback(async (entry: FsEntry, nextName: string) => {
    const parent = entry.path.slice(0, entry.path.length - entry.name.length).replace(/\/$/, '')
    const dest = parent ? `${parent}/${nextName}` : nextName
    setFileErr(null)
    try {
      await api.fileMove(entry.path, dest)
      if (fileTabs.tabs.some((t) => t.path === entry.path)) fileTabs.close(entry.path)
      refresh()
    } catch (e) { setFileErr(`Rename failed: ${(e as Error).message}`) }
  }, [fileTabs, refresh])

  const onDelete = useCallback(async (entry: FsEntry) => {
    const ok = await confirm({
      title: entry.is_dir ? `Delete folder "${entry.name}"?` : `Delete file "${entry.name}"?`,
      body: entry.is_dir ? 'This deletes the folder and all its contents. This cannot be undone.' : 'This cannot be undone.',
      danger: true,
      confirmLabel: 'Delete',
    })
    if (!ok) return
    setFileErr(null)
    try {
      await api.fileDelete(entry.path)
      if (fileTabs.tabs.some((t) => t.path === entry.path)) fileTabs.close(entry.path)
      refresh()
    } catch (e) { setFileErr(`Delete failed: ${(e as Error).message}`) }
  }, [fileTabs, refresh])

  const onUpload = useCallback(async (dir: string, files: File[]) => {
    if (!files.length) return
    setFileErr(null)
    // Client-side per-filetype pre-check — reject oversize before uploading a byte.
    const { precheck } = await import('../../lib/chunkedUpload')
    const ok: File[] = []
    for (const f of files) {
      const err = await precheck(f)
      if (err) setFileErr(err)
      else ok.push(f)
    }
    if (!ok.length) return
    setUploadRows(ok.map((f) => ({ name: f.name, pct: 0 })))
    const ctrl = new AbortController()
    uploadAbortRef.current = ctrl
    try {
      const r = await api.fileUpload(dir, ok, (idx, p) => {
        setUploadRows((prev) => prev.map((u, i) => (i === idx ? { ...u, pct: p.pct } : u)))
      }, ctrl.signal)
      if (r.ok) refresh()
      else setFileErr(`Upload failed: ${r.error ?? 'unknown error'}`)
    } catch (e) {
      // A user cancel is a silent clear, not a failure (abort is named inconsistently
      // across engines — isAbortError normalises it).
      const { isAbortError } = await import('../../lib/chunkedUpload')
      if (!isAbortError(e)) setFileErr(`Upload failed: ${(e as Error).message}`)
    } finally {
      uploadAbortRef.current = null
      setUploadRows([])
    }
  }, [refresh])

  const tabOptions = [
    ...roots.map((r) => ({ key: r.path, label: r.label })),
    { key: ARTIFACTS_TAB, label: 'Artifacts', icon: Box },
  ]
  const showResults = grep.trim().length >= 2

  return (
    <div className="flex h-full flex-col">
      <TopBar
        keepCornerPadding
        // The root-tab strip is variable-count NAVIGATION (Workspace/Home/roots/
        // Artifacts) — it lives in the LEFT slot (flex-1 min-w-0), which bounds its
        // width so the overflow-x-auto actually engages and it self-scrolls instead of
        // pushing the header wide. (In the right/shrink-0 slot it had no width bound
        // and overflowed under a docked panel.) Tab-strip overflow is its own concern
        // (plan) — separate from the action cluster, which holds only the toggle.
        left={<div className="flex min-w-0 items-center gap-m">
          <span data-type="title-l" className="text-on-surface shrink-0">Files</span>
          {!rootsLoading && roots.length > 0 && <div className="min-w-0 overflow-x-auto"><Segmented value={tab} onChange={switchTab} options={tabOptions} /></div>}
        </div>}
        right={
          <HeaderActions>
            <HeaderControl icon={PanelRight}
              label={explorerOpen ? (isArtifacts ? 'Hide artifact list' : 'Hide explorer') : (isArtifacts ? 'Show artifact list' : 'Show explorer')}
              active={explorerOpen} onClick={() => setExplorerOpen((v) => !v)} />
          </HeaderActions>
        }
      />

      {/* Workbench body is centered + bounded to the shell content-width preset
          (the 'full' preset still fills — min(1600px,100%)); its internal columns
          flex within that. */}
      <div className="mx-auto flex min-h-0 w-full flex-1" style={{ maxWidth: 'var(--content-width)' }}>
        {!isArtifacts ? (
          <>
            {/* editor column — tab strip + the focused file's viewer (fills width;
                the explorer is a right-docked, hidable panel beside it) */}
            <div className="flex min-w-0 flex-1 flex-col">
              {fileTabs.tabs.length > 0 && (
                <div className="flex items-stretch gap-1 overflow-x-auto border-b border-outline/40 px-2 pt-2">
                  {fileTabs.tabs.map((t) => {
                    const Icon = fileIcon(t.name, false)
                    const on = t.path === fileTabs.activePath
                    return (
                      <div key={t.path} role="tab" tabIndex={0} onClick={() => fileTabs.setActivePath(t.path)} title={t.path}
                        className="group/tab inline-flex h-10 shrink-0 cursor-pointer items-center gap-2 rounded-t-lg border border-b-0 pl-3.5 pr-2 text-[0.8125rem] transition-colors"
                        style={on ? { background: 'var(--color-surface-container)', color: 'var(--color-on-surface)', borderColor: 'var(--color-outline)' } : { color: 'var(--color-on-surface-low)', borderColor: 'transparent' }}>
                        <Icon size={14} className="shrink-0 opacity-70" />
                        <span className="max-w-[180px] truncate">{t.name}</span>
                        {fileTabs.dirty[t.path] && <span className="size-1.5 shrink-0 rounded-full" style={{ background: 'var(--color-primary)' }} />}
                        <button onClick={(e) => { e.stopPropagation(); fileTabs.close(t.path) }} aria-label={`Close ${t.name}`}
                          className="rounded p-1 opacity-50 hover:bg-surface-high hover:opacity-100"><X size={13} /></button>
                      </div>
                    )
                  })}
                </div>
              )}
              <div className="relative min-h-0 flex-1">
                {fileTabs.tabs.length === 0 ? (
                  <EmptyState icon={FilesIcon} title="No file open" hint="Pick a file from the explorer to view or edit it. Type in the search box to grep contents (⌘F)." />
                ) : (
                  fileTabs.tabs.map((t) => (
                    <div key={t.path} className="absolute inset-0" style={{ display: t.path === fileTabs.activePath ? 'block' : 'none' }}>
                      <FileViewer ref={(h) => { if (h) viewerRefs.current.set(t.path, h); else viewerRefs.current.delete(t.path) }}
                        entry={{ name: t.name, path: t.path, is_dir: false }} onSaved={refresh} onSaveAsArtifact={saveAsArtifact}
                        onDirtyChange={(d) => fileTabs.markDirty(t.path, d)} onMissing={(p) => fileTabs.closeNow(p)}
                        commentTarget={navigate ? newSessionTarget(navigate, { name: `Comments: ${t.name}` }) : undefined} />
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* explorer — a standard right-docked, hidable SidePanel */}
            {explorerOpen && (
            <SidePanel title="Explorer" icon={<FilesIcon size={18} />} storeKey="files-explorer-w" fillHeight onClose={() => setExplorerOpen(false)}>
            <div className="flex h-full flex-col">
              <div className="flex flex-col gap-2 border-b border-outline/40 p-m">
                <div className="relative">
                  <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-on-surface-low" />
                  <input ref={searchRef} value={grep} onChange={(e) => setGrep(e.target.value)} placeholder="Search contents…  ⌘F"
                    type="search" name="workspace-grep" aria-label="Search file contents"
                    className="h-8 w-full rounded-md bg-surface-high pl-8 pr-7 text-[0.8125rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
                  {grep && <button onClick={() => setGrep('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-on-surface-low hover:text-on-surface"><X size={13} /></button>}
                </div>
                {showResults && (
                  <input value={include} onChange={(e) => setInclude(e.target.value)} placeholder="include glob e.g. *.py"
                    name="workspace-grep-include" aria-label="Restrict search to files matching glob"
                    className="h-7 w-full rounded-md bg-surface-high px-2.5 text-[0.75rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
                )}
                {!showResults && (
                  <>
                    {/* full-width path bar on its own row */}
                    <PathBar value={activeRoot} onNavigate={setDir} />
                    {/* actions on a separate row below */}
                    <div className="flex items-center gap-0.5">
                      <RailBtn icon={FilePlus2} label="New file" onClick={() => { setCreating('file'); setNewName('') }} />
                      <RailBtn icon={FolderPlus} label="New folder" onClick={() => { setCreating('dir'); setNewName('') }} />
                      <RailBtn icon={RefreshCw} label="Refresh" onClick={refresh} />
                    </div>
                  </>
                )}
                {(branch || searchEngine) && (
                  <div className="flex items-center gap-2 text-on-surface-low text-[0.7rem]">
                    {showResults
                      ? <span>{searchBusy ? 'searching…' : `${results.length} match${results.length === 1 ? '' : 'es'}`}{searchEngine && ` · ${searchEngine === 'rg' ? 'ripgrep' : 'python'}`}</span>
                      : branch && <span className="inline-flex items-center gap-1"><GitBranch size={11} /> {branch}</span>}
                  </div>
                )}
              </div>

              {fileErr && (
                <div role="alert" className="mx-m mt-2 flex items-start gap-1.5 rounded-md px-2.5 py-1.5 text-[0.75rem]"
                  style={{ background: 'color-mix(in srgb, var(--color-danger) 14%, transparent)', color: 'var(--color-danger)' }}>
                  <span className="min-w-0 flex-1 break-words">{fileErr}</span>
                  <button onClick={() => setFileErr(null)} aria-label="Dismiss" className="shrink-0 opacity-70 hover:opacity-100"><X size={12} /></button>
                </div>
              )}
              {uploadRows.length > 0 && (
                <div className="mx-m mt-2 flex flex-col gap-1 rounded-lg bg-surface-container/60 px-3 py-2">
                  {uploadRows.map((u) => (
                    <div key={u.name} className="flex items-center gap-2.5 text-[0.75rem] text-on-surface-var">
                      <Loader2 size={13} className="shrink-0 animate-spin text-primary" />
                      <span className="max-w-[40%] shrink-0 truncate" title={u.name}>{u.name}</span>
                      <span className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-high">
                        <span className="block h-full rounded-full bg-primary transition-[width] duration-200" style={{ width: `${u.pct}%` }} />
                      </span>
                      <span className="shrink-0 tabular-nums text-on-surface-low">{u.pct}%</span>
                      <button type="button" aria-label="Cancel upload"
                        className="shrink-0 rounded p-0.5 text-on-surface-low hover:text-danger"
                        onClick={() => uploadAbortRef.current?.abort()}>
                        <X size={13} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <div className="min-h-0 flex-1 overflow-y-auto py-1"
                onDragOver={!showResults && activeRoot ? (e) => { e.preventDefault(); setRootDrop(true) } : undefined}
                onDragLeave={() => setRootDrop(false)}
                onDrop={!showResults && activeRoot ? (e) => {
                  e.preventDefault(); setRootDrop(false)
                  const files = Array.from(e.dataTransfer.files)
                  if (files.length) onUpload(activeRoot, files)
                } : undefined}
                style={rootDrop ? { boxShadow: 'inset 0 0 0 2px var(--color-primary)' } : undefined}>
                {creating && (
                  <div className="flex items-center gap-1.5 px-m py-1.5">
                    {creating === 'dir' ? <FolderPlus size={14} className="text-primary" /> : <FilePlus2 size={14} className="text-primary" />}
                    <input autoFocus value={newName} onChange={(e) => setNewName(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') submitCreate(); if (e.key === 'Escape') { setCreating(null); setNewName('') } }}
                      onBlur={submitCreate} placeholder={creating === 'dir' ? 'folder name' : 'file name'}
                      className="h-7 flex-1 rounded-md bg-surface-high px-2 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
                  </div>
                )}
                {showResults
                  ? <SearchResults results={results} busy={searchBusy} onOpen={(m) => openByPath(m.file)} />
                  : activeRoot
                    ? <FileTree key={`${activeRoot}:${nonce}`} dirs={dirs} rootPath={activeRoot} activePath={fileTabs.activePath || null}
                        gitStatuses={statuses} onOpenFile={fileTabs.open} artifactPaths={artifactPaths}
                        onRename={onRename} onDelete={onDelete} onUpload={(entry, files) => onUpload(entry.path, files)} />
                    : <Loading />}
              </div>
            </div>
            </SidePanel>
            )}
          </>
        ) : (
          <>
            {/* artifact viewer fills width; the list is a right-docked, hidable
                SidePanel (same pattern as the file explorer). */}
            <div className="min-w-0 flex-1">
              {activeArtifact
                ? <ArtifactViewer key={activeArtifact.slug} slug={activeArtifact.slug} onChanged={loadArtifacts}
                    onDeleted={() => { setActiveArtifact(null); loadArtifacts() }} onOpenSourceFile={openSourceFile}
                    commentTarget={navigate ? newSessionTarget(navigate, { name: `Comments: ${activeArtifact.name}` }) : undefined} />
                : <EmptyState icon={Box} title="No artifact selected" hint="Artifacts are named, versioned snapshots — widgets, docs, and files agents produce. Pick one to view its render, history, and timeline." />}
            </div>
            {explorerOpen && (
              <SidePanel title="Artifacts" icon={<Box size={18} />} storeKey="artifacts-list-w" fillHeight onClose={() => setExplorerOpen(false)}>
                {artLoading && artifacts.length === 0 ? <Loading /> : <ArtifactList artifacts={artifacts} activeSlug={activeArtifact?.slug ?? null} onSelect={setActiveArtifact} />}
              </SidePanel>
            )}
          </>
        )}
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
}

function SearchResults({ results, busy, onOpen }: { results: ContentMatch[]; busy: boolean; onOpen: (m: ContentMatch) => void }) {
  if (busy && results.length === 0) return <div className="flex items-center justify-center py-8"><Loader2 size={18} className="animate-spin text-on-surface-low" /></div>
  if (results.length === 0) return <div className="px-m py-s text-on-surface-low text-[0.8125rem]">No matches.</div>
  return (
    <div className="flex flex-col">
      {results.map((m, i) => (
        <button key={i} onClick={() => onOpen(m)} type="button" className="flex w-full flex-col items-start gap-0.5 rounded-md px-m py-1.5 text-left transition-colors hover:bg-surface-high">
          <span className="flex items-center gap-1 text-on-surface text-[0.8125rem] font-mono"><CornerDownRight size={11} className="text-on-surface-low" /> {baseName(m.file)}<span className="text-on-surface-low">:{m.line}</span></span>
          <span className="w-full truncate font-mono text-on-surface-low text-[0.7rem]">{m.preview}</span>
        </button>
      ))}
    </div>
  )
}

function RailBtn({ icon: Icon, label, active, onClick }: { icon: typeof Search; label: string; active?: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} type="button" title={label} aria-label={label}
      className="inline-flex size-8 shrink-0 items-center justify-center rounded-md transition-colors hover:bg-surface-high"
      style={{ color: active ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }}><Icon size={15} /></button>
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
