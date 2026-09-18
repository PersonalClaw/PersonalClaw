import { useCallback, useEffect, useRef, useState } from 'react'
import type { FsEntry } from '../../../lib/api'
import { baseName } from '../fileMeta'
import { confirm } from '../../../ui/dialog'

export interface OpenTab { path: string; name: string }

const DEFAULT_TABS_KEY = 'files-open-tabs'
// Cap open tabs so follow-the-worker (which opens a new file most cycles over a long
// autonomous run) can't grow the strip — and localStorage — without bound. When the
// cap is hit, evict the OLDEST tab that's neither active nor dirty, so recent files,
// the focused tab, and any unsaved edits are always kept.
const MAX_TABS = 12

/** Restore the persisted tab list — validating ELEMENT SHAPE, not just the container.
 *
 *  The old read was `Array.isArray(v) ? v : []` inside a `try/catch`, which guards JSON *syntax*
 *  and nothing else. Anything structurally valid got through, and a tab without a string `path`
 *  reached `baseName(path)` → `TypeError: Cannot read properties of undefined (reading
 *  'lastIndexOf')`, so the error boundary replaced the whole page. Measured in issue 515: `[{name}]`,
 *  `['a.md']`, `[null]`, `[[…]]` — every malformed shape crashed. Because the poison is PERSISTED,
 *  the boundary's Retry re-read it and navigating away and back re-read it too; the only escape was
 *  devtools. So this list is untrusted input: it outlives releases (a shape change ships a poisoned
 *  key to every existing user) and any script on the origin can write it.
 *
 *  Drops what it cannot repair and repairs what it can (a missing/blank `name` is re-derived from
 *  the path, which is what `open()` does), so a partly-bad list costs the bad tabs, not the page. */
function restoreTabs(key: string): OpenTab[] {
  let raw: unknown
  try { raw = JSON.parse(localStorage.getItem(key) || '[]') } catch { return [] }
  if (!Array.isArray(raw)) return []
  const out: OpenTab[] = []
  const seen = new Set<string>()
  for (const t of raw) {
    if (!t || typeof t !== 'object') continue
    const { path, name } = t as Partial<OpenTab>
    // A duplicate path is as bad as a missing one: tabs are keyed by path, so two would
    // mount under one React key and the strip would offer two tabs that close as one.
    if (typeof path !== 'string' || !path || seen.has(path)) continue
    seen.add(path)
    out.push({ path, name: typeof name === 'string' && name ? name : baseName(path) })
  }
  // `open()` bounds the list at MAX_TABS; a persisted list is the OTHER way into the same state,
  // so it gets the same bound — keeping the most recent, which is what open()'s eviction does.
  return out.length > MAX_TABS ? out.slice(-MAX_TABS) : out
}

/** Multi-tab open-file state, persisted to localStorage and restored on load.
 *  Holds only {path,name} per tab — content/draft live in the FileViewer keyed
 *  by path, so each tab keeps its own editor instance + dirty state.
 *
 *  ``scope`` namespaces the persisted tabs so independent surfaces don't collide:
 *  the Files page uses the default, while each Code cockpit passes a per-workspace
 *  scope so its open tabs are isolated (and never restore a tab pointing at a
 *  different — possibly deleted — project's directory). */
export function useFileTabs(scope = '') {
  const tabsKey = scope ? `${DEFAULT_TABS_KEY}:${scope}` : DEFAULT_TABS_KEY
  const activeKey = `${tabsKey}-active`
  const [tabs, setTabs] = useState<OpenTab[]>(() => restoreTabs(tabsKey))
  const [activePath, setActivePath] = useState<string>(() => {
    // The two keys are written independently, so a restored active path can name a tab that
    // isn't in the list (it was dropped as malformed, or the write of one key lost a race with
    // the other). `active` would then be null with tabs open — a host renders its no-file
    // state over a populated tab strip. Fall back to the most recent tab, as closeNow does.
    const saved = localStorage.getItem(activeKey) || ''
    const restored = restoreTabs(tabsKey)
    if (saved && restored.some((t) => t.path === saved)) return saved
    return restored.length ? restored[restored.length - 1].path : ''
  })
  // Per-path dirty flags, surfaced by the viewer so the tab strip can show a dot
  // and the close-guard can prompt.
  const [dirty, setDirty] = useState<Record<string, boolean>>({})
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty
  const activePathRef = useRef(activePath); activePathRef.current = activePath

  useEffect(() => { try { localStorage.setItem(tabsKey, JSON.stringify(tabs)) } catch { /* quota */ } }, [tabs, tabsKey])
  useEffect(() => { localStorage.setItem(activeKey, activePath) }, [activePath, activeKey])

  // Browser-level exit guard for unsaved edits — closing the tab, reloading, or
  // navigating away with any open file dirty bypasses the in-app discard confirm and
  // silently drops the edits. Armed only while something is dirty (a clean editor
  // never nags). Lives in the hook so EVERY consumer (Files page, Code cockpit, chat
  // file panel) gets it uniformly — no per-surface duplication.
  const anyDirty = Object.values(dirty).some(Boolean)
  useEffect(() => {
    if (!anyDirty) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [anyDirty])

  const open = useCallback((entry: FsEntry) => {
    setTabs((prev) => {
      if (prev.some((t) => t.path === entry.path)) return prev  // already open → just refocus
      let next = [...prev, { path: entry.path, name: entry.name || baseName(entry.path) }]
      // Over the cap → evict the oldest tab that's NOT the one being focused, NOT
      // currently active, and NOT dirty (don't discard unsaved work). If every tab is
      // protected (all dirty/active), let it exceed the cap rather than lose edits.
      while (next.length > MAX_TABS) {
        const victim = next.find((t) =>
          t.path !== entry.path && t.path !== activePathRef.current && !dirtyRef.current[t.path])
        if (!victim) break
        next = next.filter((t) => t.path !== victim.path)
      }
      return next
    })
    setActivePath(entry.path)
  }, [])

  // Close WITHOUT prompting — used for programmatic closes (a vanished workspace,
  // a worker-deleted file) and by callers that run their own (themed) confirm
  // before calling. Idempotent.
  const closeNow = useCallback((path: string) => {
    setTabs((prev) => {
      const next = prev.filter((t) => t.path !== path)
      setActivePath((cur) => cur === path ? (next.length ? next[next.length - 1].path : '') : cur)
      return next
    })
    setDirty((d) => { const n = { ...d }; delete n[path]; return n })
  }, [])

  const close = useCallback(async (path: string) => {
    // Reports whether the tab actually closed, so a host that owns a draft cache
    // (FileViewer's draftStore contract) can purge its entry exactly when the user
    // consented — a draft kept past a confirmed discard resurrects the edit on the
    // next open of the same path (issue 2279).
    if (dirtyRef.current[path] && !(await confirm({ title: `Discard unsaved changes to ${baseName(path)}?`, body: 'Your edits will be lost.', danger: true, confirmLabel: 'Discard' }))) return false
    closeNow(path)
    return true
  }, [closeNow])

  const markDirty = useCallback((path: string, isDirty: boolean) => {
    setDirty((d) => (d[path] === isDirty ? d : { ...d, [path]: isDirty }))
  }, [])

  /** Re-point every tab at or under `from` to the same place under `to`.
   *
   *  A rename is not a reason to close a tab. It used to be: the Files page renamed the file and
   *  then called `close()`, so the discard prompt appeared AFTER the rename was already on disk
   *  and its Cancel branch cancelled only the tab close. Cancel therefore left a tab bound to a
   *  path that no longer existed, and the next Save 404'd with nothing on screen (issue 654).
   *
   *  Prefix-aware, because a DIRECTORY rename moves every file under it. `from + '/'` rather than
   *  a bare `startsWith(from)`, or renaming `notes` would also claim `notes-archive/x.md`.
   *
   *  Callers must re-point only AFTER the move succeeds, and must decide what to do about a dirty
   *  tab first — the draft lives in the viewer keyed by path, so a re-point remounts it and a
   *  surface with no host draft store cannot carry an unsaved edit across. This function does not
   *  pretend otherwise; it moves the tab and its dirty flag and nothing else.
   */
  const renamePath = useCallback((from: string, to: string) => {
    if (!from || !to || from === to) return
    const moved = (p: string) => (p === from ? to : p.startsWith(`${from}/`) ? to + p.slice(from.length) : p)
    setTabs((prev) => {
      if (!prev.some((t) => moved(t.path) !== t.path)) return prev
      return prev.map((t) => {
        const next = moved(t.path)
        return next === t.path ? t : { path: next, name: baseName(next) }
      })
    })
    setActivePath((cur) => moved(cur))
    setDirty((d) => {
      const n: Record<string, boolean> = {}
      for (const [p, v] of Object.entries(d)) n[moved(p)] = v
      return n
    })
  }, [])

  /** Every open tab at or under `path` — what a rename or delete of `path` would affect. */
  const tabsUnder = useCallback(
    (path: string) => tabs.filter((t) => t.path === path || t.path.startsWith(`${path}/`)),
    [tabs],
  )

  const active = tabs.find((t) => t.path === activePath) ?? null
  return {
    tabs, active, activePath, dirty, open, close, closeNow, setActivePath, markDirty,
    renamePath, tabsUnder,
  }
}
