import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render } from '@testing-library/react'

// ── A button that re-renders at rest asks for no animation frame ────────────────────────────────
//
// `web/REDESIGN.md` §8: with the default identity, the window focused and no turn running, the app
// schedules no animation-frame callbacks. Home broke it once per poll: every answer re-renders the
// widgets, every widget holds a Button, and each Button built its pointer sheen with
// `useMotionTemplate` whether or not it drew one. That hook re-subscribes to its motion values in a
// layout effect with no dependency list, so each re-render dropped a subscription, and dropping one
// asks the motion library's frame loop for a frame. Measured in a browser on an idle #/dashboard:
// one frame after each poll answer, from that unsubscribe, and none between them.
//
// The motion library captures `requestAnimationFrame` when it loads, so the counter is installed
// before anything imports it.

const frames = vi.hoisted(() => {
  const state = { asked: 0 }
  globalThis.requestAnimationFrame = ((cb: FrameRequestCallback) => {
    state.asked += 1
    return setTimeout(() => cb(performance.now()), 0) as unknown as number
  }) as typeof requestAnimationFrame
  globalThis.cancelAnimationFrame = ((id: number) => clearTimeout(id)) as typeof cancelAnimationFrame
  return state
})

vi.mock('../lib/api', () => ({
  api: { themes: () => new Promise(() => {}), theme: () => new Promise(() => {}) },
}))

const { AppearanceProvider } = await import('../app/appearance')
const { PersonalityProvider } = await import('../app/personality')
const { Button } = await import('./Button')

type Variant = 'primary' | 'ghost'

function Shell({ variant, label }: { variant: Variant; label: string }) {
  return (
    <AppearanceProvider>
      <PersonalityProvider>
        <Button variant={variant}>{label}</Button>
      </PersonalityProvider>
    </AppearanceProvider>
  )
}

/** Let every frame the mount asked for run, and the frames those ask for, until none is left. */
async function settle() {
  for (let i = 0; i < 20; i++) await act(async () => { await new Promise((r) => setTimeout(r, 5)) })
}

/** Mount a button under `identity`, let it come to rest, then re-render it the way a poll answer
 *  does (its parent renders again, with a new label) and count the frames asked for. */
async function framesForReRenders(identity: string | undefined, variant: Variant) {
  localStorage.clear()
  if (identity) localStorage.setItem('personality', identity)
  const view = render(<Shell variant={variant} label="See all 3" />)
  await settle()
  // The counter is wired only if the motion library loaded after it: a mount asks for frames.
  expect(frames.asked, 'frames asked for by the mount: the counter reaches the motion library').toBeGreaterThan(0)
  frames.asked = 0
  for (const label of ['See all 4', 'See all 5', 'See all 6']) {
    view.rerender(<Shell variant={variant} label={label} />)
    await settle()
  }
  const asked = frames.asked
  return { asked, view }
}

afterEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-personality')
  document.documentElement.removeAttribute('style')
})

describe('a button re-rendered at rest', () => {
  it.each([
    ['a ghost button under the default identity', undefined, 'ghost'],
    ['a primary button under the default identity', undefined, 'primary'],
    ['a primary button under Claw Arcade, which draws the sheen', 'claw-arcade', 'primary'],
  ] as const)('asks for no frame: %s', async (_name, identity, variant) => {
    const { asked, view } = await framesForReRenders(identity, variant)
    expect(asked, 'animation frames asked for by three re-renders at rest').toBe(0)
    view.unmount()
  })
})

describe('the sheen still follows the pointer', () => {
  it('moves its centre to where the pointer is, under Claw Arcade', async () => {
    const { view } = await framesForReRenders('claw-arcade', 'primary')
    const button = view.container.querySelector('button')!
    button.getBoundingClientRect = () => ({ left: 0, top: 0, width: 100, height: 40, right: 100, bottom: 40, x: 0, y: 0, toJSON: () => ({}) })
    const sheen = () => (button.querySelector('[style*="radial-gradient"]') as HTMLElement).style.background
    expect(sheen()).toContain('circle at 50% 50%')
    fireEvent.pointerMove(button, { clientX: 25, clientY: 10 })
    await settle()
    expect(sheen()).toContain('circle at 25% 25%')
    view.unmount()
  })
})
