import { useEffect, useRef } from 'react'
import { reportActionFailure } from '../../app/reportingWrite'
import { api } from '../../lib/api'

// ── You have seen a chat only while it is in front of you (#3814 B2) ──────────────────────────
//
// How far you have seen each chat is the gateway's record (`chat_seen`), so a mark cleared here
// clears on every window and the phone. A chat open in a background tab, or in a window behind
// another app, is not in front of you: nothing is marked until its page is visible and its window
// focused. It is marked when it opens in view, when it comes back into view, and when a turn ends
// while it is in view.
//
// A mark that fails leaves the chat unread, which is the safe side, and the next one tries again.
// It is said once a page, not on every focus: a failure that lasts (a seen record the gateway
// cannot read) would otherwise toast each time you came back to the window.

let failureSaid = false

/** The chat is in front of you: its page shown and its window the focused one. */
export function inView(doc: Document = document): boolean {
  return doc.visibilityState === 'visible' && doc.hasFocus()
}

/** Marks *session* seen whenever it is in view: on open, on coming back into view, and when
 *  *running* turns false (a turn ended) while it is. A failed mark is said once a page. */
export function useSeenWhileInView(session: string | null, running: boolean): void {
  const sessionRef = useRef(session)
  sessionRef.current = session

  useEffect(() => {
    const mark = () => {
      const chat = sessionRef.current
      if (!chat || !inView()) return
      api.markChatSeen(chat).catch((e: unknown) => {
        if (failureSaid) return
        failureSaid = true
        reportActionFailure('mark this chat as read')(e)
      })
    }
    mark()
    const onVisible = () => { if (document.visibilityState === 'visible') mark() }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', mark)
    return () => {
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', mark)
    }
  }, [session, running])
}
