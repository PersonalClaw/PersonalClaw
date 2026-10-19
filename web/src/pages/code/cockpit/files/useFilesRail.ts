import { useEffect, useRef, useState } from 'react'
import { useVisiblePoll } from '../../../../lib/useVisiblePoll'
import { useGitStatus } from '../../../files/filesData'

// ── The cockpit files rail's controller ───────────────────────────────────────────────────────
//
// Which of the rail's two tabs is showing, the Changes tab's count badge while the Files tab is up
// (a light git status, polled while the worker runs and re-read on a save), and ⌘P switching to the
// Files tab so the finder is there to focus. `FilesRail.tsx` draws it.

export function useFilesRail({ ws, isProjectDir, running }: { ws: string; isProjectDir: boolean; running: boolean }) {
  const [tab, setTab] = useState<'files' | 'changes'>('files')
  // A light, rail-level git-status read JUST for the Changes-tab count badge — so the
  // user sees review-worthy changes are waiting while they're on the Files tab (the
  // ChangesPanel, which owns the full status + history, is unmounted then). A
  // no-workspace project (engine dir) or a non-repo workspace has no tracked changes,
  // so skip the fetch there. Refreshes on the same triggers the panels use: a slow
  // poll while the worker runs + a manual editor save.
  // Only fetch the badge git-status while the FILES tab is showing (the only time the
  // badge renders). On the Changes tab the ChangesPanel already fetches the full status,
  // so a second badge fetch there is pure duplication — gate it to null so the two don't
  // both spawn a `git status` subprocess every poll. (Skipped too for a no-workspace /
  // non-repo project, which has no tracked changes.)
  const gitWs = (isProjectDir || tab !== 'files') ? null : (ws || null)
  const [badgeNonce, setBadgeNonce] = useState(0)
  const { statuses: badgeStatuses } = useGitStatus(gitWs, badgeNonce)
  const changeCount = Object.keys(badgeStatuses).length
  useVisiblePoll(() => { if (gitWs) setBadgeNonce((n) => n + 1) }, running && gitWs ? 8000 : null)
  useEffect(() => {
    const onSaved = () => { if (gitWs) setBadgeNonce((n) => n + 1) }
    window.addEventListener('ne:code-file-saved', onSaved)
    return () => window.removeEventListener('ne:code-file-saved', onSaved)
  }, [gitWs])
  // ⌘P switches to the Files tab so the finder is visible when the event arrives. The
  // KEYBIND itself lives at cockpit level (always mounted, so it fires even when this
  // whole rail is collapsed); here we ensure the Files tab is showing. If we were on the
  // CHANGES tab, FileFinder isn't mounted yet when this focus-find fires, so its own
  // listener misses it — re-dispatch on the next tick (after the tab switch mounts it) so
  // the finder actually focuses. The ref avoids re-dispatching when already on Files
  // (FileFinder caught the original) → no double-fire.
  const tabRef = useRef(tab); tabRef.current = tab
  useEffect(() => {
    const onFocusFind = () => {
      if (tabRef.current !== 'files') {
        setTab('files')
        setTimeout(() => window.dispatchEvent(new CustomEvent('ne:code-focus-find')), 0)
      }
    }
    window.addEventListener('ne:code-focus-find', onFocusFind)
    return () => window.removeEventListener('ne:code-focus-find', onFocusFind)
  }, [])
  return { tab, setTab, changeCount }
}
