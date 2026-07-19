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
  const [tabs, setTabs] = useState<OpenTab[]>(() => {
    try { const v = JSON.parse(localStorage.getItem(tabsKey) || '[]'); return Array.isArray(v) ? v : [] } catch { return [] }
  })
  const [activePath, setActivePath] = useState<string>(() => localStorage.getItem(activeKey) || '')
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
    if (dirtyRef.current[path] && !(await confirm({ title: `Discard unsaved changes to ${baseName(path)}?`, body: 'Your edits will be lost.', danger: true, confirmLabel: 'Discard' }))) return
    closeNow(path)
  }, [closeNow])

  const markDirty = useCallback((path: string, isDirty: boolean) => {
    setDirty((d) => (d[path] === isDirty ? d : { ...d, [path]: isDirty }))
  }, [])

  const active = tabs.find((t) => t.path === activePath) ?? null
  return { tabs, active, activePath, dirty, open, close, closeNow, setActivePath, markDirty }
}
