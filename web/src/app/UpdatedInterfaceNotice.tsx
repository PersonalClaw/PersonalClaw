import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { RefreshCw, X } from 'lucide-react'
import { prefersReducedMotion, spring } from '../design/motion'
import { Button } from '../ui/Button'
import { IconButton } from '../ui/IconButton'
import { useUpdateSheetOpen } from '../ui/updateSheetOpen'
import { markNoticed, noticedBuild, useNewerServedBuild } from './servedBuild'

export const NOTICE_TEXT = "PersonalClaw's interface was updated. The changes take effect when you reload."

/** How long the notice stays before it fades. It waits while the pointer is over it or focus is in
 *  it, and gets the whole time again after (WCAG 2.2.1). */
export const NOTICE_MS = 30_000

function reloadPage(): void {
  // A navigation, so network-first even under the old service worker: the gateway serves the new
  // index.html, its new hashed assets are not in the cache, and the browser checks for a new
  // worker as it navigates, which then waits as `sw.ts` intends.
  window.location.reload()
}

/** The interface-updated notice (#5901): the gateway serves a newer build than this page runs, so
 *  the changes take effect when the page reloads, and it offers Reload.
 *
 *  It shows when a socket open says the gateway serves another build than the one this page booted
 *  (`servedBuild.ts`), which is how a restart after an update reaches an open page. Once per build
 *  in a tab: shown, the build is recorded, and a later reconnect to it says nothing. It waits while
 *  the update sheet is open, which is when that reconnect comes. Mounted inside the toast host, at
 *  the corner end of its column, so toasts stack above it instead of under it.
 *
 *  `ErrorBoundary`'s stale-chunk reload stays as the fallback for a page that missed or dismissed
 *  this and then opens a route whose chunk is gone. */
export function UpdatedInterfaceNotice({ reload = reloadPage }: { reload?: () => void }) {
  const newer = useNewerServedBuild()
  const held = useUpdateSheetOpen()
  // The build the notice is showing for, or '' when it shows nothing.
  const [showing, setShowing] = useState('')
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)

  useEffect(() => {
    if (!newer || held || newer === showing || newer === noticedBuild()) return
    markNoticed(newer)
    setShowing(newer)
  }, [newer, held, showing])

  const visible = showing !== '' && !held
  // Under reduced motion it comes and goes with no fade at all, read at render as `spring` is.
  const still = prefersReducedMotion()
  useEffect(() => {
    if (!visible || hovered || focused) return
    const timer = window.setTimeout(() => setShowing(''), NOTICE_MS)
    return () => window.clearTimeout(timer)
  }, [visible, hovered, focused, showing])

  const dismiss = () => {
    setShowing('')
    setHovered(false)
    setFocused(false)
  }

  return (
    <>
      {/* Mounted before its words, as the toast host's regions are: a live region created with its
          content is not reliably announced. The card's own text is hidden from the tree, so the
          sentence is read once. */}
      <div role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {visible ? NOTICE_TEXT : ''}
      </div>
      <AnimatePresence initial={false}>
        {visible && (
          <motion.div
            key="updated-interface"
            data-updated-interface={still ? 'still' : 'fades'}
            initial={still ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={still ? undefined : { opacity: 0 }}
            transition={spring.effects}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
            onFocus={() => setFocused(true)}
            onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocused(false) }}
            className="glass pointer-events-auto flex max-w-[calc(100vw-2rem)] items-start gap-s rounded-lg px-m py-s"
          >
            <RefreshCw size={16} aria-hidden className="mt-0.5 shrink-0 text-on-surface-var" />
            <div className="min-w-0 flex-1">
              <span aria-hidden data-type="body-m" className="text-on-surface">{NOTICE_TEXT}</span>
              <div className="mt-s">
                <Button variant="secondary" size="xs" onClick={reload} ariaLabel="Reload to use the updated interface">
                  Reload
                </Button>
              </div>
            </div>
            <IconButton icon={X} label="Dismiss: the interface was updated" title="Dismiss" size={32} iconSize={14}
              className="shrink-0" onClick={dismiss} />
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
