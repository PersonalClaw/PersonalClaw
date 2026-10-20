import type { UiDoc } from './uiDoc'

const doc: UiDoc = {
  name: 'StaleNotice',
  keywords: [
    'stale', 'staleness', 'cache', 'cached', 'revalidate', 'revalidating', 'updating',
    'freshness', 'first paint', 'flicker', 'status',
  ],
  description:
    'The label a cached-but-not-current paint wears. `useQuery` splits three states a surface used to '
    + 'collapse into one — nothing yet (skeleton), something old (this), read failed (LoadError) — and this '
    + 'is the middle one, which the app never had. Measured on the pre-fix build: `#/settings` Inbox tile, '
    + 'retention 30 cached, changed to 7 out of band, hard reload with the revalidation held. First paint '
    + 'read "30 day retention" with zero [data-stale] nodes, zero [aria-busy] nodes and no "updating" copy '
    + 'anywhere — then it silently became 7. Both numbers had been true; the screen never said which one it '
    + 'was showing, which is what reads as a bug even when the data is right. Polite role="status", not an '
    + 'alert: a re-read in progress is not bad news, unlike a failed load.',
  props: [
    { name: 'stale', description: 'Straight from `useQuery`. The component self-gates and returns null when false, so a call site is one line and cannot ship a label that renders unconditionally.' },
    { name: 'what', description: 'The data being re-read, as a lowercase plural noun — the SAME noun this surface\'s LoadError and skeleton already declare ("triggers", "items", "runs"). Copied from the sibling declaration, never invented, so one surface speaks one vocabulary in all three states.' },
    { name: 'className', description: 'Layout only — where the label sits relative to the surface\'s own header or count row.' },
    { name: 'announce', description: 'Whether the label is a live region (default true). Set false where many can be on screen at once: measured on a cold open of #/settings, 22 bento tiles revalidate together, so 22 polite regions would queue 22 announcements for one page load — the same finding BentoCard already recorded for preferring aria-busy over a per-tile role="status". The visible label and the [data-stale] hook are unaffected.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Pass `stale` from `useQuery`, never `loading` or `revalidating`. `loading` is false the moment anything is cached, so a freshness label keyed on it looks correct and never fires; `revalidating` is true on every mount including one over a value that is genuinely fresh, so it would label everything.' },
    { guidance: true, description: 'Put it beside the thing whose freshness is in question — a list\'s count row, a tile\'s header — not in a global corner. Which data is stale is the information.' },
    { guidance: false, description: 'Do not use it for a first load. Nothing is on screen to be stale about; that is what the skeleton is for.' },
    { guidance: false, description: 'Do not use it for a failed read. A failure needs the alert treatment and a retry — `LoadError` — and dressing one up as "updating" is the failed-fetch-renders-as-empty-state defect wearing a different hat.' },
  ],
  anatomy: [
    'span data-stale="true" role="status"',
    'pulsing RefreshCw icon, aria-hidden',
    '"Updating <what>…"',
  ],
}

export default doc
