import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Blocks } from 'lucide-react'
import { BentoCard, CardSkeleton } from './bento'

// ── 22 shimmering tiles that told assistive tech nothing ────────────────────────────────────
//
// Cycle 122 gave the four failure-capable tiles a failure line and logged the remaining half: a tile in
// its LOADING state was silent to assistive tech. `CardSkeleton` is `aria-hidden` (correct — it is
// decoration), and the only node an AT user reaches on a card is its nav overlay button, so they heard
// "Open Apps settings" with no indication the card was empty because the data had not arrived.
//
// 🔑 THE MEASUREMENT IS WHY THIS IS `aria-busy` AND NOT A LIVE REGION. Sampled every 100ms on a cold
// open of `#/settings`:
//
//   tiles on the hub                        28 nav buttons
//   peak simultaneous shimmering tiles      **22**
//   still shimmering at 1.8s / 2.4s / 3.0s  20 / 13 / 11
//   last tile settles                       **3.6s**  (Speech & Transcription, Chat, Inbox, Notifications)
//
// A `role="status"` per tile would queue **22 polite announcements for one page load** — unusable, and
// the reason the ledger asked for the number before the fix. `aria-busy` is a PROPERTY, not a live
// region: it announces nothing on its own and is read only if the user lands on the control. One
// `role="status"` per SECTION (as `RemoteProvidersSkeleton` ships) is fine; per tile is not.
//
// Driven before → after on `#/settings` (parent tree vs this one, cold cache):
//
//                        while loading                    after settling
//   before   22 shimmering · **0 busy**                   0 · 0
//   after    22 shimmering · **22 busy** ("Open Security   0 · **0 busy**  ← clears
//            settings" …)
//   role=status regions   1 → 1   (the app's existing toast host; no 22nd region was added)

describe('a loading tile says it is busy on the node AT can reach', () => {
  it('marks the nav button busy while loading', () => {
    render(<BentoCard icon={Blocks} title="Apps" onClick={vi.fn()} loading><div>body</div></BentoCard>)
    expect(screen.getByRole('button', { name: 'Open Apps settings' }).getAttribute('aria-busy')).toBe('true')
  })

  it('drops the attribute entirely once loaded — not aria-busy="false"', () => {
    // `undefined` rather than `false` keeps the DOM quiet in the common case; a settled tile should look
    // exactly like a tile that never loaded anything.
    render(<BentoCard icon={Blocks} title="Apps" onClick={vi.fn()}><div>body</div></BentoCard>)
    expect(screen.getByRole('button', { name: 'Open Apps settings' }).hasAttribute('aria-busy')).toBe(false)
  })

  it('does not touch the accessible NAME while busy', () => {
    // Folding "loading" into the label would rename the action mid-flight, so the control stops being
    // findable by the name it has when it works — the ruling cycle 56 measured and this session has
    // re-applied twice (Composer's send, the Toggle preconditions).
    render(<BentoCard icon={Blocks} title="Apps" onClick={vi.fn()} loading><div>body</div></BentoCard>)
    expect(screen.getByRole('button', { name: 'Open Apps settings' })).toBeTruthy()
  })

  it('keeps the skeleton itself hidden — it is decoration, not content', () => {
    const { container } = render(<CardSkeleton rows={3} />)
    expect(container.firstElementChild?.getAttribute('aria-hidden')).toBe('true')
    expect(container.querySelectorAll('.animate-pulse').length).toBe(3)
  })

  it('adds NO per-tile live region', () => {
    // The whole point of the measurement: 22 of these would fire at once.
    const { container } = render(<BentoCard icon={Blocks} title="Apps" onClick={vi.fn()} loading><div>b</div></BentoCard>)
    expect(container.querySelector('[role="status"]')).toBeNull()
    expect(container.querySelector('[aria-live]')).toBeNull()
  })

  it('the source records the count that made this decision', () => {
    // A future pass WILL be tempted to "finish the job" with a status region. The number has to travel
    // with the code, not just with a PR description.
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/bento.tsx'), 'utf8')
    expect(src).toMatch(/22 tiles shimmer/)
    expect(src).toMatch(/aria-busy=\{loading \|\| undefined\}/)
  })
})
