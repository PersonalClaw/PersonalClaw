import { useRef } from 'react'
import { api } from '../lib/api'
import { useChatSocket } from '../lib/useChatSocket'
import { playCue } from '../design/soundCues'
import { approvalToastMessage } from './approvalToast'
import { approvalDestination, loopApprovalSession } from './approvalDestination'
import { blastRadiusOf } from '../pages/chat/approvalMeta'

/** Shell-level watcher: surfaces a toast when a tool-approval is requested for a
 *  chat session the user is NOT currently viewing — most importantly a SUBAGENT's
 *  tool call, which escalates to its parent session's approval card. Without this,
 *  an approval raised while the user is on another chat / a different page would
 *  sit unseen until it times out (auto-deny).
 *
 *  The chat page renders the inline approval card for the session in view; this
 *  only fires the out-of-context nudge (never for the active session, to avoid
 *  double-signalling). The nudge names the surface that ANSWERS the approval and links
 *  to it — see `approvalDestination`, which owns that derivation because the session key
 *  is only an openable thing for a chat.
 *
 *  `activeSession` is the chat key currently on screen ("" when not on a chat).
 */
export function useApprovalToasts(activeSession: string) {
  const activeRef = useRef(activeSession)
  activeRef.current = activeSession
  // Dedupe: the queue re-read on a reconnect lists approvals this tab may have nudged already;
  // toast each id once.
  const seen = useRef<Set<string>>(new Set())

  // One approval, from its frame or from that re-read: both carry the registry's entry.
  const nudge = (d: Record<string, unknown>) => {
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
    // A loop's worker asks through the chat path, so its frame carries no source either: its
    // session key is what says a loop is asking.
    const who = source === 'subagent' ? 'A subagent'
      : source ? 'A background task'
        : loopApprovalSession(session) ? 'A loop' : 'Another chat session'
    // The compact form of the same brief the card renders — one shared facet
    // vocabulary, two presentations, no second approval renderer. The radius rides the same
    // `approval` frame the card reads it from, decoded the same way: a shape this build cannot
    // read simply establishes nothing.
    // WHERE TO ANSWER. The sentence used to end in the raw session key, which is openable only
    // for a chat — a workflow stage's `workflow:<run>:<node>` is not in `/api/chat/sessions` and
    // has no route, so the one notification the user got led to a 404 (#258). `approvalDestination`
    // owns the key's grammar and both halves of the nudge read the same call, so the place the
    // sentence names is the place the link goes.
    const dest = approvalDestination(session)
    window.dispatchEvent(new CustomEvent('ne:toast', {
      detail: {
        level: 'info',
        message: approvalToastMessage({
          who, tool, session, blastRadius: blastRadiusOf(d.blast_radius),
        }),
        href: dest.href,
        hrefLabel: dest.linkLabel,
      },
    }))
    // The approval-requested cue point. It sits AFTER the
    // dedupe/active-session guards, so an approval the reconnect re-read lists again and one
    // already visible inline are both silent — one nudge per approval, matching the
    // toast. Silent unless the user opted in; every gate lives inside playCue.
    playCue('approval_needed')
  }

  useChatSocket(
    (m) => { if (m.type === 'approval') nudge(m.data || {}) },
    // A restart drops every socket, and the approvals the resumed work raises go out while the tab
    // is still reconnecting: nobody heard their frames, so the queue is read again and each one
    // this tab has not nudged is.
    () => {
      api.approvals()
        .then((all) => { for (const a of all) nudge(a as unknown as Record<string, unknown>) })
        .catch(() => { /* the queue's own surfaces say it could not be read; a nudge is extra */ })
    },
  )
}
