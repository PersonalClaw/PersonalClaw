import type { UiDoc } from './uiDoc'

// Doc object for NeedsYouBadge — the toolbar's one count of what waits on the owner.
const doc: UiDoc = {
  name: 'NeedsYouBadge',
  keywords: ['needs you', 'waiting', 'badge', 'count', 'approvals', 'questions', 'attention', 'shell', 'toolbar', 'home'],
  description:
    "The shell corner's needs-you badge: a raised-hand glyph with the number of rows in Home's Needs You (everything waiting on the owner's answer from `GET /api/waiting`, then the Inbox's notes and skill proposals), on every page. Clicking it opens Home. It reads the same query as Home (`useNeedsYou`), so the badge and the group never disagree. It renders nothing at 0, and since its absence says nothing waits, a count not known (before the first read lands, or after a read that failed) shows a muted dash instead of disappearing.",
  props: [
    { name: 'navigate', description: 'The shell router; the badge sends the owner to `dashboard` (Home).' },
    { name: 'className', description: "Extra classes on the badge, e.g. the compact widths' 44 px hit box (`TOUCH_TARGET`) the shell gives it." },
  ],
  bestPractices: [
    { guidance: true, description: 'Mount it once, in ShellCornerRight beside the bell. Home replaced its count pills with it, so it is the one count of what waits on the owner.' },
    { guidance: true, description: 'Count through useNeedsYou only, never a second rule: the toolbar, Home and the buddy agree because they read one list.' },
    { guidance: false, description: 'Do not hide it while the count is unknown: an absent badge reads as nothing waiting, which the badge must never say without having read it.' },
    { guidance: false, description: 'Do not animate the number in on change: it swaps in place (design/countsDoNotPop.test.tsx), since a count that ticks all day is not an event.' },
  ],
  anatomy: ['round icon button (Hand glyph, on-surface-low ink)', 'count badge (primary fill, 9+ above nine) or a muted dash while unknown'],
}

export default doc
