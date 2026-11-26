import { useEffect, useState } from 'react'
import { DRAFTS_CHANGED } from './browserStore'
import { draftSessions } from './drafts'

/** The chats with a draft kept in this browser (`lib/drafts`), read again whenever one is written
 *  or goes, for the chat list's pencil and "Draft". */
export function useDraftSessions(): Set<string> {
  const [kept, setKept] = useState<Set<string>>(() => new Set())
  useEffect(() => {
    let live = true
    const read = () => { void draftSessions().then((s) => { if (live) setKept(s) }) }
    read()
    window.addEventListener(DRAFTS_CHANGED, read)
    return () => { live = false; window.removeEventListener(DRAFTS_CHANGED, read) }
  }, [])
  return kept
}
