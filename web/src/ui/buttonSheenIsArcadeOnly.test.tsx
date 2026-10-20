import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'

// ── The pointer-tracking sheen on solid buttons is Claw Arcade's ────────────────────────────────
//
// `web/DESIGN.md` §5 and §7: in Claw Arcade a solid button carries a radial sheen that follows the
// pointer; in the default look, and in Retro Terminal, a solid button stays flat. It used to follow
// the expressiveness dial alone, so the default look (expressiveness 0.8) shipped it on every
// primary button. The identity is picked the way the app picks it, through the stored
// `personality`, under the real providers.

vi.mock('../lib/api', () => ({
  api: { themes: () => new Promise(() => {}), theme: () => new Promise(() => {}) },
}))

const { AppearanceProvider } = await import('../app/appearance')
const { PersonalityProvider } = await import('../app/personality')
const { Button } = await import('./Button')

type Variant = 'primary' | 'danger' | 'secondary'

/** The sheen inside a button rendered under `identity`, or null. */
function sheen(identity: string | undefined, variant: Variant = 'primary'): Element | null {
  localStorage.clear()
  if (identity) localStorage.setItem('personality', identity)
  const { container, unmount } = render(
    <AppearanceProvider>
      <PersonalityProvider>
        <Button variant={variant}>Save</Button>
      </PersonalityProvider>
    </AppearanceProvider>,
  )
  const found = container.querySelector('button')!.querySelector('[style*="radial-gradient"]')
  unmount()
  return found
}

afterEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-personality')
  document.documentElement.removeAttribute('style')
})

describe('a solid button', () => {
  it.each([
    ['the default identity', undefined],
    ['Retro Terminal', 'retro-terminal'],
  ])('stays flat under %s', (_name, identity) => {
    expect(sheen(identity, 'primary')).toBeNull()
    expect(sheen(identity, 'danger')).toBeNull()
  })

  it('carries the sheen under Claw Arcade', () => {
    expect(sheen('claw-arcade', 'primary')).not.toBeNull()
    expect(sheen('claw-arcade', 'danger')).not.toBeNull()
  })
})

describe('a tonal button', () => {
  it('stays flat even under Claw Arcade: the sheen is for solid fills only', () => {
    expect(sheen('claw-arcade', 'secondary')).toBeNull()
  })
})
