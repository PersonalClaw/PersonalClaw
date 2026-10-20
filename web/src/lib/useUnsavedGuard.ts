import { useEffect } from 'react'

/** Warn before the BROWSER discards unsaved work — closing the tab, reloading, or navigating
 *  off the app entirely. Armed only while `dirty` is true, so a clean editor never nags.
 *
 *  This is the one place the handler lives. In-app navigation is each surface's own problem
 *  (a confirm, or a retained draft — the better answer where the text can simply survive), but
 *  the browser-level exit is identical everywhere and was previously implemented inside
 *  `useFileTabs`, whose comment already stated the intent: every consumer should get the
 *  protection uniformly, with no per-surface duplication. Extracted here when the memory
 *  markdown editors needed the same guard, so the second consumer is a call rather than a copy.
 *
 *  Deliberately just the guard: `preventDefault` + a non-empty `returnValue` is the whole
 *  browser contract, and the string is ignored by every modern engine — passing custom copy
 *  would imply control the page does not have.
 */
export function useUnsavedGuard(dirty: boolean): void {
  useEffect(() => {
    if (!dirty) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [dirty])
}
