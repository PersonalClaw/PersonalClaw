import type { UiDoc } from './uiDoc'

// Doc object for NeedsYouBadge — the toolbar's one count of what waits on the owner.
const doc: UiDoc = {
  name: 'NeedsYouBadge',
  keywords: ['needs you', 'waiting', 'badge', 'count', 'approvals', 'questions', 'attention', 'shell', 'toolbar', 'home'],
  description:
    "The shell corner's needs-you badge: a raised-hand glyph with the number of asks in Needs you's first group (everything waiting on the owner's answer, from `GET /api/waiting`), on every page, the number the tray and the buddy count. With none waiting and proposals or notes to review, it shows how many muted. Clicking it opens Needs you (`#/mission-control`). It reads the same query as the page (`useNeedsYou`), so the badge and the page never disagree. It renders nothing when both are 0, and since its absence says nothing waits, a count not known (before the first read lands, or after a read that failed) shows a muted dash instead of disappearing. Its tip is the themed Tooltip, never a native title.",
  props: [
    { name: 'navigate', description: 'The shell router; the badge sends the owner to `mission-control` (Needs you).' },
    { name: 'className', description: "Extra classes on the badge, e.g. the compact widths' 44 px hit box (`TOUCH_TARGET`) the shell gives it." },
  ],
  bestPractices: [
    { guidance: true, description: 'Mount it once, in ShellCornerRight beside the bell. Home replaced its count pills with it, so it is the one count of what waits on the owner.' },
    { guidance: true, description: 'Count through useNeedsYou only, never a second rule: the toolbar, Needs you, the tray and the buddy agree because they read one list.' },
    { guidance: false, description: 'Do not hide it while the count is unknown: an absent badge reads as nothing waiting, which the badge must never say without having read it.' },
    { guidance: false, description: 'Do not animate the number in on change: it swaps in place (design/countsDoNotPop.test.tsx), since a count that ticks all day is not an event.' },
  ],
  anatomy: ['round icon button (Hand glyph, on-surface-low ink)', 'count badge (primary fill, 9+ above nine), the count to review muted, or a muted dash while unknown'],
}

export default doc
