/** Plan-42's `native` delivery target, renderer half (DESKTOP-CAPABILITIES DC-5).
 *
 *  `native` sat in the notification-rules target vocabulary from the start as an
 *  accepted-and-persisted string with nothing behind it. The dispatch is now three
 *  processes wide, and this is the middle one:
 *
 *    gateway  decides   — a rule naming `native` + a shell reporting the capability
 *                         available ⇒ the WS note carries `native: {deliver: true}`
 *                         (`notification_rules.native_delivery`)
 *    renderer relays    — HERE. It holds the multiplexed WS the main process does not,
 *                         and it owns the SPA's route vocabulary.
 *    shell    actuates  — `desktop/nativeNotifications.js` raises the OS banner and,
 *                         on a tap, focuses the window and hands the route back.
 *
 *  Why the ROUTE is decided here and not in Python: hash routes are frontend IA. The
 *  gateway sends the rule's `source` (the rule key's source — NOT a prefix of the flat
 *  wire string, which lies for legacy kinds like `app.route.drift`), and this module maps
 *  that to a surface. A second route vocabulary in the backend would be a second thing to
 *  keep in sync with `App.tsx`, and the one that drifts is always the remote copy.
 *
 *  In a browser tab there is no bridge and every function here is a no-op — the dashboard
 *  bell that runs anyway IS the fallback, which is why the fallback needed no code.
 */
import { useEffect } from 'react'
import { desktopBridge } from './desktopBridge'
import { useChatSocket, type WsMessage } from './useChatSocket'

/** Where a tap should land, by notification SOURCE.
 *
 *  Keyed on source rather than kind because source is the surface family — the thirteen
 *  values here are every source in `notification_kinds.py`, and each value is a route
 *  `App.tsx` already serves (`nativeNotifications.test.ts` pins that against `App.tsx`'s
 *  own NAV + ROUTABLE lists, so a renamed route reds this file instead of silently
 *  deep-linking nowhere).
 *
 *  `notifications` is the honest answer for the sources with no surface of their own — a
 *  heartbeat or a hook firing has no page to open, and the feed is where you act on it.
 */
export const NOTIFICATION_SOURCE_ROUTES: Record<string, string> = {
  agent: 'agents',
  // `approval` (MOBILE-COMPANION `MC-5`) lands on CHAT, not `#/companion`. The phone's
  // deep link is `#/companion?approval=<id>` because that surface exists for a phone, but
  // a NATIVE tap comes from the desktop shell — and on a desktop the decision is answered
  // by `pages/chat/ApprovalCard` (rendered from `ChatPage.tsx`), which is the surface that
  // carries the tool, its full arguments and the Allow/Deny pair. `companion` is also
  // deliberately absent from `App.tsx`'s ROUTABLE (it is a full-screen special case), so
  // naming it here would red the sibling rail that pins every route to one App.tsx serves.
  approval: 'chat',
  apps: 'apps',
  cron: 'triggers',
  heartbeat: 'notifications',
  hook: 'notifications',
  inbox: 'inbox',
  knowledge: 'knowledge',
  learning: 'learning',
  loop: 'loops',
  planning: 'tasks',
  skills: 'skills',
  system: 'notifications',
  // INU-9 — a note you captured lives in the inbox, so a tap goes there and not to the feed.
  // Defaults to `badge`, which raises nothing native at all; this row is what makes the route
  // right for the user who switches the rule to Notify with Desktop ticked, rather than
  // leaving them a banner that lands on the notification list their note is not in.
  user: 'inbox',
}

/** The fallback surface. A native tap must always land somewhere real. */
export const DEFAULT_NOTIFICATION_ROUTE = 'notifications'

/** The route a note's tap should focus. Unknown/absent source ⇒ the feed. */
export function routeForNote(note: Record<string, unknown>): string {
  const source = typeof note.source === 'string' ? note.source : ''
  return NOTIFICATION_SOURCE_ROUTES[source] || DEFAULT_NOTIFICATION_ROUTE
}

/** True only when the gateway said THIS note is a native one.
 *
 *  Reads `native.deliver` and nothing else — deliberately not "does the note have a
 *  `native` key", because a rule that named the target while no shell was connected also
 *  carries one (with `deliver: false` and the reason why). Treating presence as consent
 *  would turn the not-connected fallback into a double delivery the moment a shell
 *  appeared mid-session.
 */
export function shouldDeliverNatively(note: Record<string, unknown>): boolean {
  const native = note.native
  return !!native && typeof native === 'object' && (native as { deliver?: unknown }).deliver === true
}

/** Every route this module will ever navigate to. A tap payload outside it is dropped. */
const KNOWN_ROUTES = new Set(Object.values(NOTIFICATION_SOURCE_ROUTES))

/**
 * Raise native OS notifications for notes whose rule named the `native` target, and
 * navigate when one is tapped. Mounted ONCE, in the app shell.
 *
 * No-ops outside the Electron shell. Notice there is no in-app suppression: the bell and
 * the feed are driven by the same WS frame and stay authoritative either way, so a native
 * banner is an addition to the record, never a replacement for it.
 */
export function useNativeNotifications(navigate: (route: string) => void): void {
  useChatSocket((m: WsMessage) => {
    if (m.type !== 'notification') return
    const bridge = desktopBridge()
    if (!bridge?.notifications) return
    const note = m.data || {}
    if (!shouldDeliverNatively(note)) return
    bridge.notifications
      .show({
        title: String(note.title ?? ''),
        body: String(note.body ?? ''),
        route: routeForNote(note),
      })
      // A refused banner is not an error worth surfacing: the note is already in the feed
      // and the bell has already counted it.
      .catch(() => {})
  })

  useEffect(() => {
    const bridge = desktopBridge()
    if (!bridge?.notifications) return
    return bridge.notifications.on((payload) => {
      const route = payload?.route ?? ''
      if (KNOWN_ROUTES.has(route)) navigate(route)
    })
  }, [navigate])
}
