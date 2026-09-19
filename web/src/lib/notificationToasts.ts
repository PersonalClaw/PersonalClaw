/** The `immediate` delivery mode's actual toast (issue #343).
 *
 *  🔴 WHY THIS EXISTS. The rules matrix explains the four modes as *"Never = dropped; Badge =
 *  kept in the list without interrupting; **Notify = a toast**; Digest = batched"* — and no
 *  notification raised a toast, in any mode. The `ne:toast` event had six dispatchers and not one
 *  of them was on the notification path: all three consumers of a `notification` WS envelope
 *  (`NotificationBell`, `NotificationsPage`, the dashboard pulse widget) just refetched the list.
 *  So `Badge` and `Notify` were observationally identical apart from up to 15s of poll latency,
 *  and the registry's own stated safety property — *"today every emitter that passes the global
 *  gate produces a toast"* — was describing behaviour the app did not have.
 *
 *  The gateway had already decided everything needed: the three quieter modes return BEFORE
 *  `_broadcast`, so **a `notification` frame on the wire is by construction an `immediate`
 *  delivery** (`dashboard/state.notify`). This relays that decision to the toast host rather than
 *  re-deriving it — the same division as `nativeNotifications.ts`, where the gateway decides and
 *  the renderer actuates.
 *
 *  Mounted ONCE in the app shell, so the promise holds on every route rather than only on the
 *  notifications page. The bell and the feed still poll the same frame, so the toast is an
 *  addition to the record, never a replacement for it.
 */
import { useChatSocket, type WsMessage } from './useChatSocket'
import { kindMeta, firstLine } from '../pages/notifications/notificationMeta'

/** The three levels `Toaster` renders. */
export type ToastLevel = 'info' | 'success' | 'error'

/** The toast level for a notification kind.
 *
 *  Derived from the kind's TONE in `notificationMeta` rather than a second table: one kind→colour
 *  vocabulary already exists and the feed row, the bell chip and the toast must not disagree about
 *  whether something went wrong.
 *
 *  Clamped to three levels because that is what the host has, so `warn` lands on `info`. That is
 *  the deliberate direction: `error` is the level that plays the error cue and takes the assertive
 *  live region, and promoting every warning into it would chime for a stalled loop.
 */
export function toastLevelForKind(kind: string): ToastLevel {
  const tone = kindMeta(kind).tone
  if (tone.includes('danger')) return 'error'
  if (tone.includes('--color-ok')) return 'success'
  return 'info'
}

/** The toast text for a note — its title, plus the first line of the body when there is one.
 *
 *  The title alone is often just a category ("Loop failed"), which tells the user a class of thing
 *  happened without saying which of their loops it was. The body's first line carries the subject.
 */
export function toastMessageForNote(note: Record<string, unknown>): string {
  const title = String(note.title ?? '').trim()
  const detail = firstLine(String(note.body ?? ''), 80).trim()
  if (!title) return detail
  if (!detail || detail === title) return title
  return `${title} — ${detail}`
}

/** Whether this frame should interrupt.
 *
 *  `badge_only` is belt-and-braces: the gateway returns before broadcasting a `badge`, so the flag
 *  should never arrive here. It is honoured anyway because that invariant lives in another
 *  process, and "badge means no interruption" is exactly the promise this file is restoring — it
 *  must not be the thing that breaks it. This is also what makes quiet hours hold for an attention
 *  kind, which is recorded as a badge overnight rather than dropped (#341).
 */
export function shouldToastNote(note: Record<string, unknown>): boolean {
  if (note.badge_only) return false
  return !!toastMessageForNote(note)
}

/** Raise a toast for every `immediate` notification. Mounted once, in the app shell. */
export function useNotificationToasts(): void {
  useChatSocket((m: WsMessage) => {
    if (m.type !== 'notification') return
    const note = m.data || {}
    if (!shouldToastNote(note)) return
    window.dispatchEvent(new CustomEvent('ne:toast', {
      detail: {
        level: toastLevelForKind(String(note.kind ?? '')),
        message: toastMessageForNote(note),
      },
    }))
  })
}
