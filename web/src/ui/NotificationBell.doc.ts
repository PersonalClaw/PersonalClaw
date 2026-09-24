import type { UiDoc } from './uiDoc'

// Doc object for NotificationBell — the shell-corner notification control.
const doc: UiDoc = {
  name: 'NotificationBell',
  keywords: ['notification', 'bell', 'unread', 'shade', 'badge', 'alerts', 'feed', 'shell'],
  description:
    'The shell-corner notification control: a bell with an unread counter that opens a shade of the few most-recent notifications. Each can be marked read / dismissed in place or opened (jumps to the full feed, deep-linked to the item); the footer navigates to the all-notifications page. Self-refreshes via a visibility-aware poll plus live WS updates.',
  props: [
    { name: 'navigate', description: 'Router navigation function. Opening a notification calls navigate(`notifications?open=<ts>`); the footer calls navigate(`notifications`).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Wire navigate to the app router so opening a notification deep-links to `notifications?open=<ts>` and the footer routes to the full feed.' },
    { guidance: true, description: 'Reach for it in the shell corner cluster (ShellCornerRight) rather than building a bespoke bell — it owns its own polling + WS refresh; do not wrap it in another poller.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens.' },
  ],
  anatomy: ['bell trigger button (unread badge, bounce re-pop on count change)', 'AnimatePresence popover shade (top-right anchored)', 'header (title + count + Mark all read)', 'scrollable recent list (ShadeRow: mark read / dismiss / open)', 'footer (View all notifications)'],
}

export default doc
