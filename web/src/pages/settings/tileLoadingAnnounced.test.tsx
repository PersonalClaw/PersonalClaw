import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
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
    // `&& !failed` joined the expression when tiles gained a failure state: a tile whose read has
    // FAILED is not busy, and leaving `aria-busy` true there would tell assistive tech a request is
    // still in flight forever. Asserted as behaviour two tests below as well as in source here.
    expect(src).toMatch(/aria-busy=\{\(loading && !failed\) \|\| undefined\}/)
  })
})

// ── The same card's FAILED state, driven rather than grepped ─────────────────────────────────────
//
// 🔑 THESE ARE RENDER ASSERTIONS ON PURPOSE. `tileLoadFailure.test.ts` scans `settingsWidgets.tsx`
// for the `failed=` prop, and a source scan can only ever prove that a STRING is present — it cannot
// prove the band mounts, that it beats the skeleton branch, or that the retry is reachable. Both
// halves are needed: the scan covers all 37 tiles cheaply, this covers the component they share.
describe('a tile whose read FAILED says so, and offers a way out', () => {
  it('renders the failure instead of the body', () => {
    render(<BentoCard icon={Blocks} title="Apps" onClick={vi.fn()} failed
      error={new Error('gateway timed out')}><div>the body</div></BentoCard>)
    expect(screen.getByText(/Couldn’t load Apps\./), 'it names the tile').toBeTruthy()
    expect(screen.getByText('gateway timed out'), "and shows the server's own words").toBeTruthy()
    expect(screen.queryByText('the body'), 'the body is NOT rendered against absent data').toBeNull()
  })

  it('beats the loading branch — `failed` wins over `loading`', () => {
    // 🪤 THE REACHABILITY TRAP. `data === undefined` is true for BOTH states, so every call site
    // passes `loading` AND `failed` as true on a failed first read. If the card tested `loading`
    // first the band would be dead code in the only situation it exists for — which is exactly how
    // "shimmer forever" was the accepted behaviour before this.
    const { container } = render(<BentoCard icon={Blocks} title="Apps" onClick={vi.fn()} loading failed
      error={new Error('x')}><div>b</div></BentoCard>)
    expect(container.querySelectorAll('.animate-pulse').length, 'no skeleton under a failure').toBe(0)
    expect(screen.getByText(/Couldn’t load Apps\./)).toBeTruthy()
  })

  it('stops claiming to be busy', () => {
    render(<BentoCard icon={Blocks} title="Apps" onClick={vi.fn()} loading failed error={new Error('x')}><div>b</div></BentoCard>)
    expect(screen.getByRole('button', { name: 'Open Apps settings' }).hasAttribute('aria-busy')).toBe(false)
  })

  it('describes the failure on the nav button WITHOUT renaming it', () => {
    // The ruling this component already applies to `loading`: folding the state into the accessible
    // NAME makes the control unfindable by the name it has when it works. A description adds the fact
    // and leaves the name alone — and it is a description rather than a live region because 22 tiles
    // can be in flight at once (see the measurement above).
    render(<BentoCard icon={Blocks} title="Apps" onClick={vi.fn()} failed error={new Error('gateway timed out')}><div>b</div></BentoCard>)
    const nav = screen.getByRole('button', { name: 'Open Apps settings' })
    const described = nav.getAttribute('aria-describedby')
    expect(described, 'the failure must be reachable from the one node AT lands on').toBeTruthy()
    const desc = document.getElementById(described!)
    expect(desc?.textContent).toMatch(/Couldn’t load Apps\..*gateway timed out/)
    // and the Retry label is NOT in the description — it is its own control with its own name.
    expect(desc?.textContent).not.toMatch(/Retry/)
  })

  it('offers a Retry that re-runs the read and does not open the subpage', () => {
    // The card's whole surface is a nav overlay, so a retry that bubbled would navigate away from the
    // failure the user just tried to clear.
    const onRetry = vi.fn()
    const onClick = vi.fn()
    render(<BentoCard icon={Blocks} title="Apps" onClick={onClick} failed error={new Error('x')} onRetry={onRetry}><div>b</div></BentoCard>)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(onRetry).toHaveBeenCalledTimes(1)
    expect(onClick, 'the click must not reach the nav overlay').not.toHaveBeenCalled()
  })

  it('omits Retry when the surface has no handle on its read', () => {
    render(<BentoCard icon={Blocks} title="Apps" onClick={vi.fn()} failed error={new Error('x')}><div>b</div></BentoCard>)
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull()
  })

  it('writes a sentence when the rejection has nothing readable to say', () => {
    // 🪤 The trap `readableErrText` exists for: a browser fetch rejection's `.message` is "Failed to
    // fetch" / "Load failed", and `errText`'s placeholder for a body it refused to show is "HTTP 502".
    // All three are truthy, so a naive `error.message ||` would let developer-console text displace a
    // written sentence — under a headline that has already named what failed.
    for (const e of [new Error('Failed to fetch'), new Error('Load failed'), new Error('HTTP 502'), {}]) {
      const { unmount } = render(<BentoCard icon={Blocks} title="Apps" onClick={vi.fn()} failed error={e}><div>b</div></BentoCard>)
      expect(screen.getByText(/The server didn’t respond\./), String((e as Error).message)).toBeTruthy()
      unmount()
    }
  })

  it('drops the footer, which describes data the tile no longer has', () => {
    render(<BentoCard icon={Blocks} title="Apps" onClick={vi.fn()} failed error={new Error('x')}
      footer={<>Embedder: local-minilm</>}><div>b</div></BentoCard>)
    expect(screen.queryByText(/Embedder/), 'a value printed under a "could not load" band').toBeNull()
  })

  it('and still adds NO per-tile live region, even for a failure', () => {
    // The contrast with `ui/LoadError` — which IS `role="alert"` — is deliberate and measured. A page
    // of 28 tiles cannot afford 22 assertive announcements; a full-page load failure can.
    const { container } = render(<BentoCard icon={Blocks} title="Apps" onClick={vi.fn()} failed error={new Error('x')}><div>b</div></BentoCard>)
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(container.querySelector('[aria-live]')).toBeNull()
  })
})
