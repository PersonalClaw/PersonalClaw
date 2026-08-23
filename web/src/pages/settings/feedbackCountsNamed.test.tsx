import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { FeedbackProducerRow } from '../../lib/api'
import { FeedbackPanel } from './FeedbackPanel'

// ── Two bare integers, and a 10px glyph deciding which is which ───────────────────────────────────
//
// `#/settings/feedback` answers one question per row: was this judgment source right? Measured on the
// live DOM with real verdicts seeded (3 up / 6 down on `task-inbox-classify`), the row's accessible
// text was:
//
//   "task-inbox-classify prompt 3 6 33% suppressed Snooze Clear"
//
// **Two bare numbers.** The only thing distinguishing 3 approvals from 6 rejections was a `size={10}`
// `ThumbsUp`/`ThumbsDown`, and lucide renders a bare `<svg>` with no accessible name — so nothing
// carried the distinction into the tree. Reading them the wrong way round inverts the meaning of the
// entire panel, which is the unusual severity here: not "an unlabelled decoration" but "a number
// whose sign is unstated".
//
// 🔑 THE FORM IS THIS REPO'S OWN, TWICE OVER, so this is drift and not a taste call:
//   · `ModelsPanel` marks its breaker dot `role="img"` with the comment "the dot is the ONLY carrier
//     of the breaker state (no text equivalent)" — the same situation exactly.
//   · `UsagePanel` marks its bar row `role="img"` with a summary label.
//   · `ui/FeedbackThumbs` — the INTERACTIVE twin of these two icons — already names them
//     ("Mark accurate" / "Mark wrong"), so the summary now speaks the control's vocabulary rather
//     than inventing a third one.
//
// The visible text is untouched: this is an accessibility-tree fix and the captures are identical.

const feedbackProducers = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    feedbackProducers: (...a: unknown[]) => feedbackProducers(...a),
    feedbackSnooze: vi.fn(),
    feedbackClear: vi.fn(),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

// `prompt` is NOT an enforced-suppression kind (`feedback.ENFORCED_SUPPRESSION_KINDS` is
// `skill_synthesis` alone), so the route can no longer return `suppressed: true` for it — it returns
// `proposal_only`. This fixture used to carry the impossible shape, which is how the false claim
// stayed invisible: the panel's own test documented it. Chip count is unchanged (one extra pill
// either way), so the wrapping assertion below still measures what it measured.
const ROWS: FeedbackProducerRow[] = [
  { producer_kind: 'prompt', producer_id: 'task-inbox-classify', ups: 3, downs: 6, n: 9, accuracy: 0.333, proposal_only: true },
  { producer_kind: 'prompt', producer_id: 'task-inbox-draft', ups: 7, downs: 0, n: 7, accuracy: 1, suppressed: false },
]

describe('a feedback count says which way it counts', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    feedbackProducers.mockResolvedValue({ producers: ROWS, min_n: 5, window_days: 90 })
  })

  it('every count is a named image, never a bare number', async () => {
    render(<FeedbackPanel />)
    await waitFor(() => expect(screen.getByText('task-inbox-classify')).toBeTruthy())
    // One per count, per row — and the number is IN the name, so it is announced with its sign.
    expect(screen.getByRole('img', { name: '3 marked accurate' })).toBeTruthy()
    expect(screen.getByRole('img', { name: '6 marked wrong' })).toBeTruthy()
    expect(screen.getByRole('img', { name: '7 marked accurate' })).toBeTruthy()
    // Zero must still be named: "0 marked wrong" is a meaningful answer, not an absence.
    expect(screen.getByRole('img', { name: '0 marked wrong' })).toBeTruthy()
  })

  it('the two counts in a row never share a name', async () => {
    render(<FeedbackPanel />)
    await waitFor(() => expect(screen.getByText('task-inbox-classify')).toBeTruthy())
    const names = screen.getAllByRole('img').map((el) => el.getAttribute('aria-label'))
    expect(new Set(names).size, `duplicate count names would re-create the ambiguity:\n${names.join('\n')}`)
      .toBe(names.length)
  })

  it('the visible text is unchanged — this is a tree fix, not a redesign', async () => {
    render(<FeedbackPanel />)
    await waitFor(() => expect(screen.getByText('task-inbox-classify')).toBeTruthy())
    // The digits still render as digits beside their icons; nothing was spelled out on screen.
    expect(screen.getByText('3')).toBeTruthy()
    expect(screen.getByText('6')).toBeTruthy()
  })

  it('the row keeps a floor for its identity, and may wrap instead of starving it', () => {
    // 🔴 The SUPPRESSED row lost its own name at 390px — the row that matters most. Every chip on the
    // right is `shrink-0`, and a suppressed producer carries one more of them (accuracy +
    // "suppressed" + Snooze + Clear), so an identity block free to collapse to zero (`min-w-0
    // flex-1`) was squeezed out and painted UNDERNEATH the pills. Measured at 390×844: the
    // "prompt 3 6" line occupied x 36–78 while the "33%" pill occupied x 48–88 ON TOP of it, and
    // `task-inbox-classify` was unreadable. The healthy sibling row — one chip fewer — rendered
    // correctly, which is exactly why it looked fine at a glance.
    //
    // 🪤 `ux-audit --viewport phone` reported this as a 3.93:1 contrast failure `via: sibling`
    // against `rgb(86,51,50)` — the danger pill's own tint. It was NOT a colour bug: against the real
    // backdrop the ink measures 5.93:1 and passes AA. The audit had no vocabulary for "these two
    // boxes overlap", so it reported the overlap as the contrast it computed. Chasing the number
    // would have re-inked passing text; measuring the geometry found the actual defect. After the
    // fix the same run is clean, twice.
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/FeedbackPanel.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const rowAt = src.indexOf('<div className="flex flex-wrap items-center gap-3 rounded-lg bg-surface-container')
    expect(rowAt, 'the producer row must be allowed to wrap').toBeGreaterThan(-1)
    const identity = src.slice(rowAt, rowAt + 400)
    expect(identity, 'and the identity block needs a floor, not permission to vanish')
      .toMatch(/<div className="min-w-40 flex-1">/)
    expect(identity, 'min-w-0 there is what let the pills paint over the name').not.toMatch(/min-w-0 flex-1/)
  })

  it('the summary speaks the same vocabulary as the control that produces it', () => {
    // `FeedbackThumbs` is where a verdict is recorded. If someone re-words one side, the panel and
    // the button that feeds it start describing the same act differently — the drift this repo's
    // coherence rails exist to stop.
    const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const thumbs = strip(readFileSync(join(process.cwd(), 'src/ui/FeedbackThumbs.tsx'), 'utf8'))
    const panel = strip(readFileSync(join(process.cwd(), 'src/pages/settings/FeedbackPanel.tsx'), 'utf8'))
    expect(thumbs, 'the control names the up verdict "accurate"').toMatch(/Mark accurate/)
    expect(thumbs, 'and the down verdict "wrong"').toMatch(/Mark wrong/)
    expect(panel, 'so the summary counts say accurate').toMatch(/marked accurate/)
    expect(panel, 'and wrong').toMatch(/marked wrong/)
  })
})
