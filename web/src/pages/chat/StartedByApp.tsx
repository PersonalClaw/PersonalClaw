import { Blocks } from 'lucide-react'
import type { AppStarted } from '../../lib/api'
import { StatusPill } from '../../ui/StatusPill'

/** Which app started a conversation — named wherever your chats are listed or opened.
 *
 *  An installed app can hold conversations of its own, and they sit in your history beside
 *  yours: you can read them and speak in them. What a title cannot tell you is whose permissions
 *  a turn there runs under — the APP's, whoever sends the message, and never your approval
 *  switches (`chat_runner.app_conversation_posture`). So every history row names the app, and
 *  inside the chat the composer says what that means before you send.
 */

/** The app's name as install consent showed it; its id when the server sent no name. */
export function startedByName(s: AppStarted): string {
  return (s.created_by_app_name || s.created_by_app || '').trim()
}

/** What a message you send into an app's conversation runs under. `autoApproves` is the server's
 *  answer for the app's grant right now (`app_auto_approves`): the grant approves every call, or
 *  it approves none and each call that needs approval asks you. Neither reads Trust or YOLO. */
export function appPermissionSentence(name: string, autoApproves: boolean): string {
  return autoApproves
    ? `What you send here runs with ${name}'s permissions: its tool calls run without asking you. Your Trust and YOLO settings don't apply in this chat.`
    : `What you send here runs with ${name}'s permissions: ${name} can't approve tool calls, so each one that needs approval asks you. Your Trust and YOLO settings don't apply in this chat.`
}

/** "Started by <App>" on a history row — provenance, not a control, so a span. Renders nothing for
 *  one of yours.
 *
 *  Not in the chat's header: that row is already over-full below ~1300px, where a chip there
 *  slid under the Task and Permission pills or pushed the copy-link button under them (measured
 *  at 500, 700 and 1024px). Inside the chat, the composer note and the Permission pill's reason
 *  name the app at every width. */
export function StartedByApp({ s }: { s: AppStarted }) {
  const name = startedByName(s)
  if (!name) return null
  // Neutral: provenance is not a verdict, and the muted ink is the tone measured to hold AA over
  // every resting tier a history row sits on (`ui/StatusPill.tsx`).
  return (
    <StatusPill tone="neutral" title={`${name} started this chat. A turn in it runs with ${name}'s permissions.`}
      className="min-w-0 max-w-[10rem] gap-1 h-[18px] cursor-default">
      <Blocks size={10} className="shrink-0" aria-hidden />
      <span className="truncate">Started by {name}</span>
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
