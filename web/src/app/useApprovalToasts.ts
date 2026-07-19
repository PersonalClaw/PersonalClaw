import { useRef } from 'react'
import { useChatSocket } from '../lib/useChatSocket'

/** Shell-level watcher: surfaces a toast when a tool-approval is requested for a
 *  chat session the user is NOT currently viewing — most importantly a SUBAGENT's
 *  tool call, which escalates to its parent session's approval card. Without this,
 *  an approval raised while the user is on another chat / a different page would
 *  sit unseen until it times out (auto-deny).
 *
 *  The chat page renders the inline approval card for the session in view; this
 *  only fires the out-of-context nudge (never for the active session, to avoid
 *  double-signalling). The message names the owning session so the user can open it.
 *
 *  `activeSession` is the chat key currently on screen ("" when not on a chat).
 */
export function useApprovalToasts(activeSession: string) {
  const activeRef = useRef(activeSession)
  activeRef.current = activeSession
  // Dedupe: an approval is (re)broadcast on connect/resync; toast each id once.
  const seen = useRef<Set<string>>(new Set())

  useChatSocket((m) => {
    if (m.type !== 'approval') return
    const d = m.data || {}
    const session = String(d.session ?? '')
    const id = String(d.id ?? '')
    if (!session || !id) return
    if (session === activeRef.current) return  // visible inline — no nudge
    if (seen.current.has(id)) return
    seen.current.add(id)
    if (seen.current.size > 200) seen.current = new Set([...seen.current].slice(-100))
    const tool = String(d.tool ?? 'a tool')
    // Ordinary chat-session approvals broadcast NO source key (only the
    // subagent/background paths set one) — don't mislabel them as background.
    const source = String(d.source ?? '')
    const who = source === 'subagent' ? 'A subagent'
      : source ? 'A background task' : 'Another chat session'
    window.dispatchEvent(new CustomEvent('ne:toast', {
      detail: { level: 'info', message: `${who} needs approval to run ${tool} — open ${session} to respond.` },
    }))
  })
}
