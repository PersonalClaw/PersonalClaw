/**
 * The rig spike's harness (#3887): both candidates side by side, driven by one set of controls.
 *
 * The owner watches the candidates through these controls, and the measurement window drives the
 * same harness through its probe (`window.__buddyRig`), so both see one thing: the transitions into
 * the four families and the focused card, both dials, the colour scheme and reduced motion. The
 * dials and the scheme are the app's own, set through the appearance store as Settings › Design
 * sets them, so what the candidates read is what the app would hand them.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('../lib/api', () => ({
  api: { themes: () => new Promise(() => {}), theme: () => new Promise(() => {}) },
}))

const { ThemeProvider } = await import('../app/theme')
const { AppearanceProvider } = await import('../app/appearance')
const { BuddyRigHarness } = await import('./BuddyRigHarness')
const { runtime } = await import('../design/runtime')

const ORIGINAL = { bounciness: runtime.bounciness, expressiveness: runtime.expressiveness, hash: location.hash }

afterEach(() => {
  runtime.bounciness = ORIGINAL.bounciness
  runtime.expressiveness = ORIGINAL.expressiveness
  history.replaceState(null, '', ORIGINAL.hash || '#/')
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
})

function mount(hash = '#/buddy-rig') {
  history.replaceState(null, '', hash)
  return render(
    <ThemeProvider>
      <AppearanceProvider>
        <BuddyRigHarness />
      </AppearanceProvider>
    </ThemeProvider>,
  )
}

const candidates = (c: HTMLElement) => [...c.querySelectorAll<HTMLElement>('[data-rig-candidate]')]
/** Each candidate is its own chunk, so it arrives after the harness does. */
const arrived = (c: HTMLElement, n: number) => waitFor(() => expect(candidates(c)).toHaveLength(n))

describe('the harness', () => {
  it('opens on both candidates, side by side, in the quiet family', async () => {
    const { container } = mount()
    await arrived(container, 2)
    expect(candidates(container).map((el) => el.dataset.rigCandidate)).toEqual(['in-place', 'layered'])
    for (const el of candidates(container)) expect(el.dataset.rigPhase).toBe('idle')
  })

  it('mounts one candidate when the measurement asks for one, and none for the baseline', async () => {
    const { container, unmount } = mount('#/buddy-rig?view=layered')
    await arrived(container, 1)
    expect(candidates(container).map((el) => el.dataset.rigCandidate)).toEqual(['layered'])
    unmount()
    const baseline = mount('#/buddy-rig?view=none').container
    expect(screen.getByRole('button', { name: 'Approval' })).toBeInTheDocument()
    expect(candidates(baseline)).toHaveLength(0)
  })

  it('a state control moves both candidates into that family', async () => {
    const { container } = mount()
    await arrived(container, 2)
    fireEvent.click(screen.getByRole('button', { name: 'Approval' }))
    for (const el of candidates(container)) expect(el.dataset.rigPhase).toBe('approval')
  })

  it('opens the focused card with each candidate’s orb in it', async () => {
    const { container } = mount()
    await arrived(container, 2)
    fireEvent.click(screen.getByRole('button', { name: 'Open the card' }))
    await waitFor(() => expect(container.querySelectorAll('[data-rig-card] [data-rig-candidate]')).toHaveLength(2))
    expect(screen.getByRole('button', { name: 'Close the card' })).toBeInTheDocument()
  })

  it('the dials are the app’s own: moving one sets the motion runtime the presets read', () => {
    mount()
    const bounciness = screen.getByRole('slider', { name: 'Bounciness' })
    fireEvent.change(bounciness, { target: { value: '0' } })
    expect(runtime.bounciness).toBe(0)
    const expressiveness = screen.getByRole('slider', { name: 'Expressiveness' })
    fireEvent.change(expressiveness, { target: { value: '1' } })
    expect(runtime.expressiveness).toBe(1)
  })

  it('the scheme control applies an app scheme, which is what retints the candidates', () => {
    mount()
    fireEvent.click(screen.getByRole('radio', { name: 'Jade' }))
    expect(document.documentElement.dataset.theme).toBe('jade')
  })

  it('says whether reduced motion is on, since only the system setting turns it on', () => {
    mount()
    expect(screen.getByText(/Reduced motion is off/)).toBeInTheDocument()
  })

  it('hands the measurement window a probe that drives the same controls', async () => {
    const { container } = mount()
    await arrived(container, 2)
    const probe = (window as unknown as { __buddyRig?: { ready: boolean; show: (p: string) => void } }).__buddyRig
    expect(probe?.ready).toBe(true)
    act(() => probe!.show('error'))
    for (const el of candidates(container)) expect(el.dataset.rigPhase).toBe('error')
  })
})
