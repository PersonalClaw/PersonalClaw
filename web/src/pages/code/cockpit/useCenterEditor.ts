import { useCallback, useEffect, useRef, useState } from 'react'
import { confirm } from '../../../ui/dialog'
import { api, type FsEntry } from '../../../lib/api'
import { type ArtifactCopy, type FileViewerHandle, type SaveAsArtifact } from '../../files/browse/FileViewer'
import type { DraftEntry } from '../../../ui/content/ContentSurface'
import { useFileTabs } from '../../files/browse/useFileTabs'
import { useMode } from '../../../app/theme'
import type { RouteProps } from '../../../app/useQueryState'
import { centerFromQuery, centerPatch, fromReviewPath, isReviewCommit, toReviewPath, type Center } from './reviewState'

/** A center's identity, for comparing what the address names with what is shown. */
const centerKey = (c: Center | null | 'invalid'): string =>
  c === 'invalid' ? '!' : c ? `${c.kind}:${c.kind === 'commit' ? c.hash : c.path}` : ''

// ── The cockpit editor's controller ───────────────────────────────────────────────────────────
//
// What the center of the code cockpit keeps and does: the workspace's file tabs and their unsaved
// drafts, ⌘S, closing a dirty tab only once asked, the re-open chips for a collapsed side panel,
// the terminal drawer's latch, saving a file as an artifact, and the file the worker is writing, the
// diff or the commit the center shows in place of the editor. `CenterEditor.tsx` draws it.

// Above this content length the worker-driven write/erase reveal animation is
// skipped (the editor opens directly): char-by-char animating hundreds of KB mounts
// a giant DOM string + janks, with no readability payoff for machine-generated bulk.
const REVEAL_MAX_CHARS = 40_000

export function useCenterEditor({ ws, showTerm, onCloseTerm, query, setQuery }: {
  ws: string; showTerm: boolean; onCloseTerm: () => void
} & Partial<Pick<RouteProps, 'query' | 'setQuery'>>) {
  // Latch: once the terminal is opened it stays MOUNTED (toggled via CSS) so the header
  // Hide/Show button preserves the PTY + running process + scrollback. The terminal's
  // own X is a real CLOSE → reset the latch to tear the PTY down. Header toggle just
  // hides (showTerm=false) but leaves termOpened true; the X resets termOpened.
  const [termOpened, setTermOpened] = useState(false)
  useEffect(() => { if (showTerm) setTermOpened(true) }, [showTerm])
  const closeTerm = useCallback(() => { setTermOpened(false); onCloseTerm() }, [onCloseTerm])
  // Tabs are scoped to THIS project's workspace, so they never restore a tab from
  // the Files page or another (possibly deleted) project's directory.
  const { tabs, active, activePath, dirty, open, closeNow, setActivePath, markDirty } = useFileTabs(ws)
  // Latest tabs for event listeners (which close over a render's `tabs` and would
  // otherwise act on a stale snapshot — e.g. the close-deleted-file handler).
  const tabsRef = useRef(tabs); tabsRef.current = tabs
  // Latest activePath for the (stably-bound) rename listener, which must restore focus
  // to whatever was active — without re-binding every listener on each tab focus.
  const activePathRef = useRef(activePath); activePathRef.current = activePath
  // A tab the user asked to close that has unsaved edits → confirm with the THEMED
  // dialog. Programmatic closes (ws cleanup, worker-erase) use closeNow and never prompt.
  const requestClose = useCallback(async (path: string, name: string) => {
    if (!dirty[path]) { closeNow(path); return }
    if (!(await confirm({
      title: 'Discard unsaved changes?',
      body: `"${name}" has edits that haven't been saved. Closing the tab discards them.`,
      danger: true, confirmLabel: 'Discard',
    }))) return
    draftStore.delete(path); closeNow(path)
  }, [dirty, closeNow])
  // (The browser-level unsaved-edits exit guard now lives in useFileTabs, so every
  // FileViewer host — cockpit, Files page, chat panel — gets it uniformly.)
  // ⌘S / Ctrl-S saves the active file — the most-used editor action, expected in a
  // mini-IDE (the FileViewer's Save button already advertises "⌘S"). Mirrors the
  // Files page + chat file panel wiring: a handle ref + a window keydown listener.
  const viewerRef = useRef<FileViewerHandle>(null)
  // Per-path unsaved-draft cache: only the ACTIVE tab's FileViewer is mounted, so a tab
  // switch unmounts the editor and would drop an in-progress edit (the dirty dot would
  // even lie — switching back showed clean disk content). The viewer mirrors its draft
  // here on edit + re-seeds from it on mount, so a dirty tab's content survives switches.
  const draftStore = useRef(new Map<string, DraftEntry>()).current
  // Collapsed side panels surface as pull-out tabs on THIS editor bar (no persistent
  // vertical rail in the body). Each CollapsiblePanel broadcasts its collapsed state;
  // we track {panelKey → {side,label}} for the currently-collapsed ones + render a
  // re-open chip on the matching edge of the tab strip. Clicking dispatches the same
  // ne:code-expand-panel event the panel already listens for.
  // Seed from the persisted collapse flags (the two panel keys are fixed) so a panel
  // that STARTS collapsed shows its re-open chip immediately — without relying on
  // catching the panel's mount broadcast (which fires before this listener attaches).
  const [collapsedPanels, setCollapsedPanels] = useState<Record<string, { side: 'left' | 'right'; label: string }>>(() => {
    const seed: Record<string, { side: 'left' | 'right'; label: string }> = {}
    try {
      if (localStorage.getItem('code-left-collapsed') === '1') seed['code-left'] = { side: 'left', label: 'Files' }
      if (localStorage.getItem('code-right-collapsed') === '1') seed['code-right'] = { side: 'right', label: 'Tasks' }
    } catch { /* ignore */ }
    return seed
  })
  useEffect(() => {
    const onCollapsed = (e: Event) => {
      const d = (e as CustomEvent).detail as { panelKey: string; side: 'left' | 'right'; label: string; collapsed: boolean }
      setCollapsedPanels((prev) => {
        const next = { ...prev }
        if (d.collapsed) next[d.panelKey] = { side: d.side, label: d.label }
        else delete next[d.panelKey]
        return next
      })
    }
    window.addEventListener('ne:code-panel-collapsed', onCollapsed as EventListener)
    return () => window.removeEventListener('ne:code-panel-collapsed', onCollapsed as EventListener)
  }, [])
  const collapsedLeft = Object.entries(collapsedPanels).filter(([, v]) => v.side === 'left')
  const collapsedRight = Object.entries(collapsedPanels).filter(([, v]) => v.side === 'right')
  const reopenPanel = (key: string) => window.dispatchEvent(new CustomEvent('ne:code-expand-panel', { detail: key }))
  // Snapshot the open file into the Artifacts system (the editor's "Artifact"
  // button). Was a no-op here (dead button) — the Files page wires this via a naming
  // modal; the cockpit has no Artifacts tab, so create directly with the file's
  // basename + a kind inferred from its extension, and report via ne:code-toast.
  const saveFileAsArtifact = useCallback((entry: FsEntry, save: SaveAsArtifact | ArtifactCopy) => {
    const base = entry.name || entry.path.split('/').pop() || 'file'
    const saved = () => { window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'ok', text: `Saved “${base}” as an artifact.` } })) }
    const failed = (e: unknown) => window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't save artifact: ${(e as Error).message || 'unknown error'}` } }))
    if (typeof save !== 'function') {
      // An image or PDF: a copy of the file's bytes, which the gateway reads and checks.
      api.saveFileCopyAsArtifact({ name: base, kind: save.kind, source_path: entry.path }).then(saved).catch(failed)
      return
    }
    const ext = base.includes('.') ? base.split('.').pop()!.toLowerCase() : ''
    // Map to an ALLOWED artifact kind (widget/html/react/markdown/svg/json/text).
    // Source code has no dedicated kind → use 'text' (renders as a <pre>); NOT 'code'
    // (rejected by the backend → normalized to 'widget', which renders the file in a
    // sandboxed IFRAME — a .py shown as a broken HTML doc). md/json/svg keep their real
    // kinds; html is deliberately 'text' too (a code file the user is reviewing, not a
    // live widget to execute).
    const kind = ext === 'md' ? 'markdown' : ext === 'json' ? 'json'
      : ext === 'svg' ? 'svg' : 'text'
    // Through the viewer's own save: the draft goes over the copy it was built from, and a file
    // changed since is refused into the viewer's notice. The toast rides the create itself, so it
    // also reports the artifact a re-applied create makes from that notice.
    save((content, revision) => api.saveFileAsArtifact({ name: base, content, source_path: entry.path, kind }, revision)
      .then(saved))
      .catch(failed)
  }, [])
  // Defensively drop any restored tab that isn't under this workspace (e.g. a
  // stale entry from before scoping) so a forbidden file-read can't 400.
  // The file tree lists REALPATH-resolved paths (macOS symlinks /tmp→/private/tmp,
  // /var→/private/var), which differ from the unresolved `workspace_dir` by a
  // prefix — so a plain startsWith(ws) would nuke every tree-opened tab. Match on
  // the workspace's last segment too (same approach as the Changes panel's `rel`).
  useEffect(() => {
    if (!ws) return
    const wsBase = ws.replace(/\/$/, '').split('/').pop() || ''
    const underWs = (p: string) => p.startsWith(ws) || (!!wsBase && p.includes(`/${wsBase}/`))
    for (const t of tabs) {
      // Purge the draft with the tab (issue 2279): a ws-switch close is programmatic,
      // and a kept entry would resurrect the edit if the path reopens under a later ws.
      if (!underWs(t.path)) { draftStore.delete(t.path); closeNow(t.path) }
    }
  }, [ws, tabs, closeNow])
  // Canonicalize an incoming path to the workspace (`ws`) form. Different sources
  // name the same file differently: the file tree emits REALPATH paths (macOS
  // /private/tmp/…), follow-the-worker emits unresolved `workspace_dir` paths
  // (/tmp/…). useFileTabs dedups by exact string, so without this the same file
  // opened from two sources becomes two tabs. Splice at the workspace's last
  // segment and re-root under `ws`.
  const canonPath = useCallback((p: string): string => {
    if (!ws || p.startsWith(ws)) return p
    const wsBase = ws.replace(/\/$/, '').split('/').pop() || ''
    const marker = `/${wsBase}/`
    const i = wsBase ? p.lastIndexOf(marker) : -1
    return i >= 0 ? `${ws.replace(/\/$/, '')}/${p.slice(i + marker.length)}` : p
  }, [ws])
  // A working-vs-HEAD diff shown over the editor (Changes tab). Opening a plain
  // file (tree) clears it; closing it returns to the editor.
  const [diff, setDiff] = useState<{ path: string; name: string; deleted?: boolean } | null>(null)
  // A whole-commit unified diff (History → click a commit), shown as a patch.
  const [commit, setCommit] = useState<{ hash: string; subject: string } | null>(null)
  const { mode } = useMode()
  // Autonomous-writing reveal: when the worker creates/changes a file (a _follow
  // open), play a typing animation before handing off to the live editor. A NEW
  // file animates as a write (TypingReveal); a MODIFIED file (we've seen it
  // before) animates as a diff — new lines in, then old lines out (DiffReveal).
  // `reveal` holds the file being animated; once done it opens normally.
  const [reveal, setReveal] = useState<
    | { path: string; name: string; kind: 'write'; text: string }
    | { path: string; name: string; kind: 'diff'; oldText: string; newText: string }
    | { path: string; name: string; kind: 'erase'; text: string }
    | null
  >(null)
  // Last content we've shown per path, so a re-edit can diff old→new.
  const lastContentRef = useRef<Map<string, string>>(new Map())
  // The editor (FileViewer) is the visible center surface only when nothing overlays
  // it — a diff/commit/reveal view replaces it. Mirrors the body render guard below.
  const editorShowing = !reveal && !commit && !diff && !!active
  // ⌘S / Ctrl-S saves the active file — but ONLY while the editor is showing, so a
  // diff/commit/reveal view doesn't swallow the browser save + call a stale viewer ref.
  // ⌘W / Ctrl-W closes the active tab (the universal IDE shortcut, expected in a mini-
  // IDE) — routed through requestClose so a dirty tab still gets the discard confirm.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Let the embedded terminal own its keystrokes: Ctrl-W is shell "delete word",
      // Ctrl-S is XOFF — hijacking them to close-tab / save while typing in the shell is
      // disruptive (Linux/Windows Ctrl; macOS ⌘ is safe but the guard is harmless there).
      if ((e.metaKey || e.ctrlKey) && (document.activeElement as HTMLElement | null)?.closest('.xterm')) return
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's' && editorShowing) {
        e.preventDefault(); viewerRef.current?.save()
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'w' && active) {
        // Only when a file tab is the active surface (not a diff/commit/reveal) — and
        // preventDefault so the browser doesn't close the whole tab/window.
        e.preventDefault(); requestClose(active.path, active.name)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [editorShowing, active, requestClose])
  // Listen for file-open + diff-open + commit-open requests.
  useEffect(() => {
    const onOpen = (e: Event) => {
      const raw = (e as CustomEvent).detail as FsEntry & { _follow?: boolean }
      if (!raw || raw.is_dir) return
      // Normalize to the workspace path form so every source maps to one tab.
      const entry = { ...raw, path: canonPath(raw.path) }
      setDiff(null); setCommit(null)
      // A USER-initiated open (tree click, FilesTouched chip — NOT _follow) must cancel
      // any in-progress worker reveal: the reveal overlay covers the editor (editorShowing
      // = !reveal), so leaving it up would show the PREVIOUS file's animation over the
      // file the user just clicked, and its onDone would re-open that file, stealing the
      // navigation. A _follow open starts its own reveal below (replacing this one), so
      // only clear for the non-follow path.
      if (!entry._follow) setReveal(null)
      // Worker-driven open → animate. New file (unseen) = write reveal; a file we've
      // shown before that changed = diff reveal (new lines in, old lines out).
      if (entry._follow) {
        const prev = lastContentRef.current.get(entry.path)
        api.fileRead(entry.path, true).then((r) => {
          const text = r.content ?? ''
          // Skip the typing/diff animation for content that can't or shouldn't be
          // animated: a BINARY file the worker wrote (compiled output, an image —
          // r.binary means the body is NUL-laden mojibake, not typeable text), or a
          // very LARGE file (animating hundreds of KB char-by-char mounts a giant
          // string + janks; the duration is already clamped, but the DOM cost isn't).
          // Record the baseline + open the real viewer directly, which renders binary
          // as a placeholder and large text normally. (DiffReveal already self-skips a
          // too-large diff; this guards the write-reveal + the binary case both paths
          // share.) TRUNCATED reads (r.truncated) also skip: a diff against a partial
          // body would falsely show the unread tail as removed.
          if (r.binary || r.truncated || text.length > REVEAL_MAX_CHARS) {
            lastContentRef.current.set(entry.path, text)
            open(entry)
            return
          }
          if (prev === undefined) {
            // first time we see it → write reveal (skip empty/binary)
            if (text.trim()) { lastContentRef.current.set(entry.path, text); setReveal({ path: entry.path, name: entry.name, kind: 'write', text }) }
            else { lastContentRef.current.set(entry.path, text); open(entry) }
          } else if (prev !== text && text.trim()) {
            // changed since last shown → diff reveal
            lastContentRef.current.set(entry.path, text)
            setReveal({ path: entry.path, name: entry.name, kind: 'diff', oldText: prev, newText: text })
          } else {
            open(entry)  // unchanged → just (re)open
          }
        }).catch(() => open(entry))
        return
      }
      // MANUAL open (tree click, FilesTouched chip): seed the diff-reveal baseline with
      // the current on-disk content (fire-and-forget) — else when the worker later edits
      // THIS file (a _follow open), prev is undefined → it animates as a write-from-empty
      // reveal instead of a diff of just the changed lines, misrepresenting an edit of a
      // file the user already had open. Skip if we already have a baseline for it.
      if (lastContentRef.current.get(entry.path) === undefined) {
        api.fileRead(entry.path, true).then((r) => {
          if (!r.binary && !r.truncated && typeof r.content === 'string') {
            lastContentRef.current.set(entry.path, r.content)
          }
        }).catch(() => {})
      }
      open(entry)
    }
    // Clear any in-progress reveal too: it renders ABOVE diff/commit (the body branch
    // order is reveal → commit → diff), so a user opening a diff/commit mid-reveal would
    // otherwise stay hidden behind the lingering animation.
    const onDiff = (e: Event) => { const d = (e as CustomEvent).detail as { path: string; name: string; deleted?: boolean }; if (d?.path) { setReveal(null); setCommit(null); setDiff(d) } }
    const onCommit = (e: Event) => { const c = (e as CustomEvent).detail as { hash: string; subject: string }; if (c?.hash) { setReveal(null); setDiff(null); setCommit(c) } }
    // The worker deleted a file → play an erase (autonomous-backspace) animation of
    // its last content, then close any tab for it. detail carries {path,name,text}.
    const onErase = (e: Event) => {
      const raw = (e as CustomEvent).detail as { path: string; name: string; text?: string }
      const d = raw?.path ? { ...raw, path: canonPath(raw.path) } : raw
      const text = d?.text || lastContentRef.current.get(d?.path)
      // Skip the erase animation for empty OR very-large last-content (a big generated
      // file animating char-by-char janks + mounts a giant string) — just close the tab.
      if (!d?.path || !text || !text.trim() || text.length > REVEAL_MAX_CHARS) {
        if (d?.path) { lastContentRef.current.delete(d.path); draftStore.delete(d.path); closeNow(d.path) }
        return
      }
      setDiff(null); setCommit(null)
      lastContentRef.current.delete(d.path)
      setReveal({ path: d.path, name: d.name, kind: 'erase', text })
    }
    // A file/folder was deleted or renamed in the tree → close its (stale) editor
    // tab(s) immediately, no animation. For a folder, close every open tab beneath
    // it. Matches both the canonical (ws) and raw path forms.
    const onClose = (e: Event) => {
      const p = (e as CustomEvent).detail?.path as string | undefined
      if (!p) return
      const cp = canonPath(p)
      for (const t of tabsRef.current) {
        if (t.path === p || t.path === cp || t.path.startsWith(cp.replace(/\/$/, '') + '/') || t.path.startsWith(p.replace(/\/$/, '') + '/')) {
          draftStore.delete(t.path)  // purge any cached draft for a closed/deleted tab
          closeNow(t.path)
        }
      }
    }
    // A USER save updates the diff-reveal baseline for that file: without this,
    // lastContentRef still holds the pre-save (worker-era) content, so the NEXT worker
    // touch would diff against stale text — showing the user's own just-saved lines as
    // "removed". Keep the baseline current with what's actually on disk now.
    const onSavedFile = (e: Event) => {
      const d = (e as CustomEvent).detail as { path?: string; content?: string } | undefined
      if (d?.path && typeof d.content === 'string') lastContentRef.current.set(canonPath(d.path), d.content)
    }
    // A file/folder was RENAMED in the tree → follow any open tab(s) to the new path so
    // renaming the file you're editing doesn't make it vanish from the editor. Matches
    // the renamed item itself (file) AND, for a folder rename, every open descendant;
    // re-roots each by swapping the `from` prefix → `to`. Preserves which tab was active.
    const onRenamed = (e: Event) => {
      const d = (e as CustomEvent).detail as { from?: string; to?: string; isDir?: boolean } | undefined
      if (!d?.from || !d?.to) return
      const from = canonPath(d.from), to = canonPath(d.to)
      const fromPfx = from.replace(/\/$/, '') + '/'
      const wasActive = activePathRef.current
      for (const t of tabsRef.current) {
        let next: string | null = null
        if (t.path === from || t.path === d.from) next = to
        else if (t.path.startsWith(fromPfx)) next = to.replace(/\/$/, '') + '/' + t.path.slice(fromPfx.length)
        else if (t.path.startsWith(d.from.replace(/\/$/, '') + '/')) next = to.replace(/\/$/, '') + '/' + t.path.slice(d.from.replace(/\/$/, '').length + 1)
        if (!next) continue
        const wasThisActive = t.path === wasActive
        closeNow(t.path)
        // carry the diff-reveal baseline to the new path so the next worker touch diffs right
        const base = lastContentRef.current.get(t.path)
        if (base !== undefined) { lastContentRef.current.set(next, base); lastContentRef.current.delete(t.path) }
        // Carry an UNSAVED draft to the new path too — otherwise renaming a file you're
        // editing re-seeds the remounted editor from disk and silently drops the edit
        // (the dirty dot followed the tab, but the content didn't). Re-key old → new.
        const d2 = draftStore.get(t.path)
        if (d2 !== undefined) { draftStore.set(next, d2); draftStore.delete(t.path) }
        open({ name: next.split('/').pop() || next, path: next, is_dir: false } as FsEntry)
        if (!wasThisActive) setActivePath(wasActive)  // open() focuses the new tab; restore if it wasn't the active one
      }
    }
    window.addEventListener('ne:code-open-file', onOpen as EventListener)
    window.addEventListener('ne:code-open-diff', onDiff as EventListener)
    window.addEventListener('ne:code-open-commit', onCommit as EventListener)
    window.addEventListener('ne:code-erase-file', onErase as EventListener)
    window.addEventListener('ne:code-close-file', onClose as EventListener)
    window.addEventListener('ne:code-file-saved', onSavedFile as EventListener)
    window.addEventListener('ne:code-renamed', onRenamed as EventListener)
    return () => {
      window.removeEventListener('ne:code-open-file', onOpen as EventListener)
      window.removeEventListener('ne:code-open-diff', onDiff as EventListener)
      window.removeEventListener('ne:code-open-commit', onCommit as EventListener)
      window.removeEventListener('ne:code-erase-file', onErase as EventListener)
      window.removeEventListener('ne:code-file-saved', onSavedFile as EventListener)
      window.removeEventListener('ne:code-close-file', onClose as EventListener)
      window.removeEventListener('ne:code-renamed', onRenamed as EventListener)
    }
  }, [open, closeNow, canonPath])

  // Disambiguate tabs whose basenames collide (e.g. two `index.ts`, two
  // `__init__.py`) by suffixing the parent dir segment — VSCode-style — so identical
  // names aren't indistinguishable. Only the colliding tabs get the suffix; unique
  // names stay clean.
  const tabLabels = (() => {
    const counts: Record<string, number> = {}
    for (const t of tabs) counts[t.name] = (counts[t.name] ?? 0) + 1
    const label: Record<string, string> = {}
    for (const t of tabs) {
      if (counts[t.name] > 1) {
        const parent = t.path.replace(/\/$/, '').split('/').slice(-2, -1)[0]
        label[t.path] = parent ? `${t.name} — ${parent}` : t.name
      } else label[t.path] = t.name
    }
    return label
  })()
  // Scroll the active tab into view when it changes — the strip scrolls horizontally
  // (cap 12 tabs), so opening a file whose tab is off-screen (a tree click on the Nth
  // file, or a worker follow-open) would otherwise leave the now-active tab hidden +
  // the user unsure anything happened. 'nearest' is a no-op when it's already visible.
  const activeTabRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    activeTabRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [activePath])

  // The one thing the center shows is in the cockpit's address (`file`, `diff` or `commit`, see
  // `reviewState`), so a reload, Back, a copied address or a link opens the same review. The
  // address is read once the workspace is known, and only when it MOVES: then the center opens
  // what it names through the same open events a click sends, so it is opened as if she had
  // clicked it. An address naming more than one, or a path outside the workspace or a hash outside
  // a commit's shape, shows nothing and is corrected. What the center shows is written back as a
  // replace whenever it changes. A commit's hash goes only to the read of that commit. A file
  // outside the workspace, or a commit by a short hash the address cannot hold, is shown and
  // written as none of the three.
  const wanted = ws && setQuery ? centerFromQuery(query ?? {}) : null
  const wantedKey = centerKey(wanted)
  const shownPath = (abs: string) => toReviewPath(ws, abs)
  const shown: Center | null = commit ? (isReviewCommit(commit.hash) ? { kind: 'commit', hash: commit.hash } : null)
    : diff ? (shownPath(diff.path) ? { kind: 'diff', path: shownPath(diff.path)! } : null)
    : active ? (shownPath(active.path) ? { kind: 'file', path: shownPath(active.path)! } : null)
    : null
  const shownKey = centerKey(shown)
  const setQueryRef = useRef(setQuery); setQueryRef.current = setQuery
  const shownKeyRef = useRef(shownKey); shownKeyRef.current = shownKey
  useEffect(() => {
    if (!wanted || wantedKey === shownKeyRef.current) return
    if (wanted === 'invalid') {
      setDiff(null); setCommit(null)
      setQueryRef.current?.(centerPatch(null), { replace: true })
      return
    }
    const send = (name: string, detail: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }))
    if (wanted.kind === 'commit') send('ne:code-open-commit', { hash: wanted.hash, subject: '' })
    else {
      const path = fromReviewPath(ws, wanted.path)
      const name = wanted.path.split('/').pop() || wanted.path
      if (wanted.kind === 'diff') send('ne:code-open-diff', { path, name })
      else send('ne:code-open-file', { name, path, is_dir: false })
    }
  }, [wantedKey, ws]) // eslint-disable-line react-hooks/exhaustive-deps
  // Written back only when what is shown CHANGES, so a tab restored from an earlier visit never
  // overwrites what the address names before the address has been opened. In a render where the
  // address moved, or the workspace became known, the address wins: the read above opens what it
  // names, and what is shown is not written over it. Without that the two can chase each other,
  // each opening or writing what the other just replaced.
  const lastShownKey = useRef(shownKey)
  const lastRead = useRef({ wantedKey, ws })
  useEffect(() => {
    const moved = lastRead.current.wantedKey !== wantedKey || lastRead.current.ws !== ws
    lastRead.current = { wantedKey, ws }
    if (lastShownKey.current === shownKey) return
    lastShownKey.current = shownKey
    if (moved || !ws || !setQueryRef.current || shownKey === wantedKey) return
    setQueryRef.current(centerPatch(shown), { replace: true })
  }, [shownKey, wantedKey, ws]) // eslint-disable-line react-hooks/exhaustive-deps
  return {
    termOpened, closeTerm, tabs, active, activePath, dirty, open, closeNow, setActivePath, markDirty,
    requestClose, viewerRef, draftStore, collapsedLeft, collapsedRight, reopenPanel, saveFileAsArtifact,
    diff, setDiff, commit, setCommit, mode, reveal, setReveal, tabLabels, activeTabRef,
  }
}
