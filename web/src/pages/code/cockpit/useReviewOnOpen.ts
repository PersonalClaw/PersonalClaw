import { useEffect, useRef } from 'react'
import { openChange } from './files/changes'

// ── A code session opens on its uncommitted changes ───────────────────────────────────────────
//
// A cockpit opened at its bare address (`#/code/<id>`, as the list, the palette and a notification
// link it) opens the review pane on the first uncommitted change, beside the editor, through the
// same open event a Changes row sends, so the address then names it (`diff=<path>`) as any open
// diff does. An address that names anything is followed as it is, and a clean tree opens nothing.
// A review closed from its pane stays closed for this workspace while the tab lives, so a reload
// does not bring it back; a new tab opens it again.

const closedKey = (ws: string) => `code-review-closed:${ws}`

function closedInTab(ws: string): boolean {
  try { return sessionStorage.getItem(closedKey(ws)) === '1' } catch { return false }
}

/** Remembers, for this tab, that the review was closed from its pane. */
export function closeReviewInTab(ws: string) {
  try { sessionStorage.setItem(closedKey(ws), '1') } catch { /* storage is unavailable: it reopens */ }
}

export function useReviewOnOpen({ ws, query, changes, state, diffOpen, commitOpen }: {
  ws: string
  /** The cockpit's address; none where the host keeps no address. */
  query?: Record<string, string>
  changes: [string, string][]
  state: 'idle' | 'loading' | 'loaded' | 'error'
  diffOpen: boolean
  commitOpen: boolean
}) {
  // Whether the cockpit opened at its bare address, read once the workspace is known: a tab
  // restored from an earlier visit writes its file to the address after that, and still gets the
  // review beside it.
  const openedBare = useRef<{ ws: string; bare: boolean } | null>(null)
  if (ws && openedBare.current?.ws !== ws) openedBare.current = { ws, bare: !!query && Object.keys(query).length === 0 }
  const taken = useRef('')
  useEffect(() => {
    if (!ws || taken.current === ws || state === 'idle' || state === 'loading') return
    taken.current = ws
    if (!openedBare.current?.bare || diffOpen || commitOpen || changes.length === 0 || closedInTab(ws)) return
    const [path, code] = changes[0]
    openChange(ws, path, code)
  }, [ws, state]) // eslint-disable-line react-hooks/exhaustive-deps
  // Opening a diff again, by any path, is the review wanted back.
  useEffect(() => {
    if (!ws || !diffOpen) return
    try { sessionStorage.removeItem(closedKey(ws)) } catch { /* nothing kept */ }
  }, [ws, diffOpen])
}
