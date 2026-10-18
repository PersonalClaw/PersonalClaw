import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render, waitFor } from '@testing-library/react'

// ── A focused composer: a plain coral ring, unless the identity keeps the light ─────────────────
//
// `web/DESIGN.md` §5 and §7: in the default look the composer's focus is a plain 2px coral ring,
// with no animated gradient ring, no rim glow and no spring rise, the same ring every other input
// gets. Claw Arcade, the one identity that keeps the light, adds all three. Retro Terminal keeps
// the default look's rules.
//
// Mounted under the REAL appearance and personality providers, with the identity picked the way
// the app picks it (the stored `personality`), and focused through the editor itself, so the
// composer's own focus state drives what is asserted. jsdom runs framer's springs on its timer
// frames: the rise is read once it has had time to move, and its absence after the same time.

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: new Proxy(real.api, {
      get: (target, key) => {
        if (key === 'dashboardConfig') return () => Promise.resolve({ send_on_enter: true })
        if (key === 'themes' || key === 'theme') return () => new Promise(() => {})
        return (target as Record<string | symbol, unknown>)[key]
      },
    }),
  }
})

const { AppearanceProvider } = await import('../../app/appearance')
const { PersonalityProvider } = await import('../../app/personality')
const { Composer } = await import('../Composer')

const CONTROLS = { agent: false, model: false, approval: false, reasoning: false, attach: false, mic: false, optimize: false }

async function mount(identity?: string) {
  localStorage.clear()
  if (identity) localStorage.setItem('personality', identity)
  const utils = render(
    <AppearanceProvider>
      <PersonalityProvider>
        <Composer value="" onChange={() => {}} onSend={() => {}} controls={CONTROLS} />
      </PersonalityProvider>
    </AppearanceProvider>,
  )
  const editor = utils.container.querySelector<HTMLElement>('.cm-content')!
  // The surface is the editor's sheet: the nearest box around it that carries the composer's
  // shadow, which framer writes on its first frame.
  const surface = await waitFor(() => {
    const found = editor.closest<HTMLElement>('[style*="box-shadow"]')
    expect(found, 'the composer has no surface to ring').not.toBeNull()
    return found!
  })
  // The box that rises: the surface's nearest moving ancestor, the composer's own outer frame.
  const frame = surface.parentElement!
  const ring = () => utils.container.querySelector('[style*="conic-gradient"]')
  return { ...utils, editor, surface, frame, ring }
}

/** Give the editor the caret. The editor reports a focus change a moment after the event (it
 *  waits out a blur-and-refocus pair), so callers wait for its effect, not for the event. */
async function focus(editor: HTMLElement) {
  await act(async () => { editor.focus() })
}

/** Long enough for a spring to have visibly moved. */
const springTime = () => act(async () => { await new Promise((r) => setTimeout(r, 250)) })

afterEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-personality')
  document.documentElement.removeAttribute('style')
})

describe.each([
  ['the default identity', undefined],
  ['Retro Terminal', 'retro-terminal'],
])('%s: a plain 2px coral ring', (_name, identity) => {
  it('focused, it shows the ring, and nothing else changes', async () => {
    const { editor, surface, frame, ring } = await mount(identity)
    expect(surface.style.outlineStyle, 'ringed before it was focused').not.toBe('solid')
    await focus(editor)
    await waitFor(() => expect(surface.style.outlineStyle, 'focused, and no ring').toBe('solid'))
    expect(surface.style.outlineWidth).toBe('2px')
    expect(surface.style.outlineColor).toBe('var(--color-primary)')
    expect(surface.style.outlineOffset).toBe('2px')
    await springTime()
    expect(ring(), 'the animated gradient ring is drawn').toBeNull()
    expect(surface.style.boxShadow, 'the rim glow is drawn: a focused composer keeps its Rest shadow').toBe('var(--shadow-rest)')
    expect(frame.style.transform || 'none', 'the composer rose to meet the caret').toBe('none')
  })

  it('the ring goes when the caret leaves', async () => {
    const { editor, surface } = await mount(identity)
    await focus(editor)
    await waitFor(() => expect(surface.style.outlineStyle).toBe('solid'))
    await act(async () => { editor.blur() })
    await waitFor(() => expect(surface.style.outlineStyle, 'the ring outlived the caret').not.toBe('solid'))
  })
})

describe('Claw Arcade keeps the light', () => {
  it('focused, it draws the gradient ring, the coral rim and the spring rise — and no plain ring', async () => {
    const { editor, surface, frame, ring } = await mount('claw-arcade')
    expect(ring(), 'the gradient ring belongs to the arcade composer').not.toBeNull()
    await focus(editor)
    await waitFor(() => expect(surface.style.boxShadow).toBe('var(--shadow-composer-focus)'))
    await waitFor(() => expect(frame.style.transform).toMatch(/translateY\(-/))
    expect(surface.style.outlineStyle, 'the arcade ring is drawn on top of the plain one').not.toBe('solid')
  })
})
