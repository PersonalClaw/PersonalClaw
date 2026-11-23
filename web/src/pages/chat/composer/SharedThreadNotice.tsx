import { Users } from 'lucide-react'

/** What the composer of a chat linked to a shared thread says before you type. The thread is read
 *  by everyone in its channel or group, so a turn you start here is never posted there: not your
 *  message, not its progress and not its answer (`chat_runner._the_thread_told`). */
export const SHARED_THREAD_NOTICE =
  'This chat is linked to a shared thread. What you write here stays here.'

/** Above the composer of a chat linked to a thread other people read. */
export function SharedThreadNotice() {
  return (
    <div data-type="caption" className="mb-2 flex items-center gap-1.5 text-on-surface-low">
      <Users size={13} className="shrink-0" aria-hidden />
      <span>{SHARED_THREAD_NOTICE}</span>
    </div>
  )
}
