import { Blocks } from 'lucide-react'
import type { AppStarted } from '../../lib/api'
import { StatusPill } from '../../ui/StatusPill'

/** Which app started a conversation — named wherever your chats are listed or opened.
 *
 *  An installed app can hold conversations of its own, and they sit in your history beside
 *  yours: you can read them and speak in them. What a title cannot tell you is whose permissions
 *  a turn there runs under — the APP's, whoever sends the message, and never your approval
 *  switches (`chat_runner.app_conversation_posture`). So every history row and every "Jump back
 *  in" link names the app, the chat's header says it under the title, and the composer says what
 *  it means before you send. This module is the one place those words are written.
 */

/** The app's name as install consent showed it; its id when the server sent no name. */
export function startedByName(s: AppStarted): string {
  return (s.created_by_app_name || s.created_by_app || '').trim()
}

/** The chip's words: "Started by <App>". */
export function startedByLabel(name: string): string {
  return `Started by ${name}`
}

/** The chip's tooltip — what being started by an app means for a turn in the chat. */
export function startedByTitle(name: string): string {
  return `${name} started this chat. A turn in it runs with ${name}'s permissions.`
}

/** What a message you send into an app's conversation runs under. `autoApproves` is the server's
 *  answer for the app's grant right now (`app_auto_approves`): the grant approves every call, or
 *  it approves none and each call that needs approval asks you. Neither reads Trust or YOLO. */
export function appPermissionSentence(name: string, autoApproves: boolean): string {
  return autoApproves
    ? `What you send here runs with ${name}'s permissions: its tool calls run without asking you. Your Trust and YOLO settings don't apply in this chat.`
    : `What you send here runs with ${name}'s permissions: ${name} can't approve tool calls, so each one that needs approval asks you. Your Trust and YOLO settings don't apply in this chat.`
}

/** "Started by <App>" on a row that lists a chat — a history row, a "Jump back in" link.
 *  Provenance, not a control, so a span. Renders nothing for one of yours.
 *
 *  Inside the chat the header's context line carries the same words (`ChatContextLine`), at the
 *  line's own chip size: that line wraps, so the chip no longer has to fight the Task and
 *  Permission pills for the title's row, which is where #3632 measured it sliding under them. */
export function StartedByApp({ s }: { s: AppStarted }) {
  const name = startedByName(s)
  if (!name) return null
  // Neutral: provenance is not a verdict, and the muted ink is the tone measured to hold AA over
  // every resting tier a history row sits on (`ui/StatusPill.tsx`).
  return (
    <StatusPill tone="neutral" title={startedByTitle(name)}
      className="min-w-0 max-w-[10rem] gap-1 h-[18px] cursor-default">
      <Blocks size={10} className="shrink-0" aria-hidden />
      <span className="truncate">{startedByLabel(name)}</span>
    </StatusPill>
  )
}

/** Above the composer of an app's conversation: whose permissions what you send runs under. */
export function AppPermissionNotice({ name, autoApproves }: { name: string; autoApproves: boolean }) {
  return (
    <div className="mb-2 flex items-center gap-1.5 text-[0.75rem] text-on-surface-low">
      <Blocks size={13} className="shrink-0" aria-hidden />
      <span>{appPermissionSentence(name, autoApproves)}</span>
    </div>
  )
}
