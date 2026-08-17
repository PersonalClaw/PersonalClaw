import { describe, it, expect, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen, cleanup } from '@testing-library/react'
import { ScreenShareChip } from './ScreenShareChip'

/**
 * MI-4 — the done-when clause requiring "in-app pulsing chip + browser indicator
 * both showing".
 *
 * Only ONE of the two halves is testable here, and it is worth being explicit about
 * which. The BROWSER's capture indicator (tab badge, OS overlay, the floating "Stop
 * sharing" bar) is drawn by the user agent in response to a live `getDisplayMedia`
 * track. jsdom implements neither `getDisplayMedia` nor any capture chrome, so there
 * is no object to assert against and no way to observe its presence from a test —
 * that half is verified only by driving a real browser. What IS asserted below:
 *
 *  - the in-app chip renders, with an accessible name, and offers the stop action;
 *  - it is bound to the LIVE stream rather than to a "share requested" flag, so the
 *    browser's own stop button clears it (a source-level rail, since the binding is
 *    the property and jsdom cannot produce the stream to demonstrate it);
 *  - the hook tears the tracks down and never keeps a capture running past the
 *    indicator it is paired with.
 */
describe('ScreenShareChip — the in-app half of the indicator pair', () => {
  it('renders a named, pulsing stop control', () => {
    const onStop = vi.fn()
    render(<ScreenShareChip onStop={onStop} />)
    const btn = screen.getByRole('button', { name: /sharing your screen/i })
    expect(btn).toBeTruthy()
    // The pulse rides the shared token, not a bespoke animation.
    expect(btn.querySelector('.status-pulse')).toBeTruthy()
    btn.click()
    expect(onStop).toHaveBeenCalledTimes(1)
    cleanup()
  })

  it('names the action, so the chip is findable as the way to stop', () => {
    render(<ScreenShareChip onStop={() => {}} />)
    const name = screen.getByRole('button').getAttribute('aria-label') ?? ''
    expect(name.toLowerCase()).toContain('stop sharing')
    cleanup()
  })
})

describe('useScreenShare — capture lifecycle rails', () => {
  const src = readFileSync(join(process.cwd(), 'src/ui/composer/useScreenShare.ts'), 'utf8')

  it("honours the browser's own stop button by listening for track end", () => {
    // Without this the chip would keep pulsing over a dead stream — the app claiming
    // to see a screen it cannot. Asserted at the source because jsdom cannot mint a
    // MediaStreamTrack to end.
    expect(src).toMatch(/addEventListener\('ended'/)
  })

  it('stops every track on teardown rather than only hiding the chip', () => {
    // The teardown moved into the shared acquisition module (CHAT-CRAFT CC-4): screen
    // SHARE and screen SNIP now go through ONE getDisplayMedia call site, so there is
    // one place a track could be left running. The claim is unchanged — assert it where
    // the code now lives, plus the fact that sharing routes through it.
    const shared = readFileSync(join(process.cwd(), 'src/ui/composer/displayCapture.ts'), 'utf8')
    expect(shared).toMatch(/getTracks\(\)\.forEach\(\(t\) => t\.stop\(\)\)/)
    expect(src).toMatch(/stopStream\(stream\)/)
  })

  it('stops sharing when the component unmounts', () => {
    expect(src).toMatch(/useEffect\(\(\) => \(\) => teardown\(true\), \[teardown\]\)/)
  })

  it('never streams: a frame is captured only on an explicit send', () => {
    // No timer/interval anywhere — the frame is grabbed by captureAndStage at send
    // time. A polling capture loop would be a different feature with a different
    // consent story.
    expect(src).not.toMatch(/setInterval|requestAnimationFrame/)
  })

  it('drops the server-side slot when sharing stops', () => {
    expect(src).toMatch(/screenShareSignal\(sessionRef\.current, 'stop'\)/)
  })
})

describe('the composer control is gated by the config flag', () => {
  const src = readFileSync(join(process.cwd(), 'src/ui/Composer.tsx'), 'utf8')

  it('renders no capture affordance at all unless screenShare.available', () => {
    expect(src).toMatch(/\{screenShare\?\.available && \(/)
  })

  it('puts the unavailable reason in disabledReason, never in the label', () => {
    // The label must stay "Share screen" in every state, or the control stops being
    // findable by the name it has when it works.
    const region = src.slice(src.indexOf('{screenShare?.available'), src.indexOf('Hands-free: keeps listening'))
    expect(region).toMatch(/disabledReason=\{screenShare\.disabledReason\}/)
    expect(region).toMatch(/label=\{screenShare\.sharing \? 'Stop sharing screen' : 'Share screen'\}/)
  })
})
