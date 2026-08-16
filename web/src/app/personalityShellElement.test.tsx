/**
 * PERSONALITY-THEMES §S2 (PT-3) — the App-shell slot mounts a shell element ONLY
 * under the personality that declares one.
 *
 * "Only under its personality" is an ABSENCE claim, and absence claims are the easiest
 * thing in a test suite to satisfy by accident: a slot that is broken and mounts
 * nothing, ever, passes every absence assertion in this file. So each one is paired:
 *
 *  - the strip is ABSENT under every personality whose `behavior.shellElement` is
 *    unset, and under no stored personality at all (the real first-run path);
 *  - the strip is PRESENT under every personality that declares one;
 *  - and both populations are asserted NON-EMPTY, so removing the declaration from
 *    the registry reddens this file instead of making it trivially green.
 *
 * Absent means absent, not hidden: the assertion is on the NODE COUNT, so a slot that
 * mounted the overlay with `display: none`, `opacity: 0` or `hidden` under the wrong
 * personality would still fail. That matters because the element is `fixed inset-0`
 * over the whole viewport — a "hidden" one is a stacking-context and compositor cost
 * every standard-scheme user would pay for chrome they never chose.
 *
 * The personality arrives through the REAL provider (localStorage → context →
 * getShellElement → Suspense), so the wiring under test is the shipped wiring. Only
 * `./appearance` is stubbed, exactly as `errorTreatmentSkin.test.tsx` does: it owns
 * colour application, which PT-1 covers and which has nothing to do with this atom.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import { PersonalityProvider, PersonalityShellElement } from './personality'
import { DEFAULT_PERSONALITY, PERSONALITIES, SHELL_ELEMENTS } from '../design/personalities'

vi.mock('./appearance', () => ({
  useAppearance: () => ({ applyScheme: () => {}, setSelect: () => {} }),
}))

const WITH = PERSONALITIES.filter((p) => p.behavior.shellElement)
const WITHOUT = PERSONALITIES.filter((p) => !p.behavior.shellElement)

function mount(id?: string) {
  if (id) localStorage.setItem('personality', id)
  return render(
    <PersonalityProvider>
      <PersonalityShellElement />
    </PersonalityProvider>,
  )
}

/** Every mounted shell element, whichever registry entry it came from. */
const elements = (c: HTMLElement) => c.querySelectorAll('[data-shell-element]')

afterEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-personality')
})

describe('the two populations are both real (or every assertion below is vacuous)', () => {
  it('at least one personality declares a shell element', () => {
    expect(WITH.map((p) => p.id), 'no personality declares one — the presence tests are dead')
      .not.toEqual([])
  })

  it('at least one personality declares none, including the default identity', () => {
    expect(WITHOUT.map((p) => p.id), 'every personality declares one — the absence tests are dead')
      .not.toEqual([])
    // The default is what "off" means, so it specifically must be in that population.
    expect(PERSONALITIES.find((p) => p.id === DEFAULT_PERSONALITY)?.behavior.shellElement)
      .toBeUndefined()
  })
})

describe('a shell element mounts under its own personality', () => {
  it.each(WITH.map((p) => [p.id, p.behavior.shellElement!] as const))(
    '%s mounts %s',
    async (id, elementId) => {
      const { container } = mount(id)
      // The entry is lazy, so the first paint is Suspense's null fallback.
      await waitFor(() => expect(elements(container)).toHaveLength(1))
      expect(elements(container)[0].getAttribute('data-shell-element')).toBe(elementId)
      expect(elementId in SHELL_ELEMENTS).toBe(true)
    },
  )
})

describe('and under no other personality', () => {
  it.each(WITHOUT.map((p) => [p.id] as const))('%s mounts nothing at all', async (id) => {
    const { container } = mount(id)
    // Give the lazy chunk the same window the presence test needs, so this is a real
    // absence and not just "the element had not arrived yet".
    await new Promise((r) => setTimeout(r, 50))
    expect(elements(container), `${id} must mount no shell element`).toHaveLength(0)
    // Nothing hidden, either — no wrapper, no empty node, no overlay with `hidden`.
    expect(container.innerHTML, `${id} must render an empty slot`).toBe('')
  })

  it('with NO stored personality at all, nothing mounts — the first-run path', async () => {
    const { container } = mount()
    await new Promise((r) => setTimeout(r, 50))
    expect(elements(container)).toHaveLength(0)
    expect(container.innerHTML).toBe('')
  })

  it('a stored id from a removed entry falls back to the default and mounts nothing', async () => {
    // `resolvePersonality` degrades an unknown id to the default. If it ever degraded
    // to "keep the last shell element", a removed personality would leave a permanent
    // overlay no picker could turn off.
    const { container } = mount('was-removed-in-a-later-release')
    await new Promise((r) => setTimeout(r, 50))
    expect(elements(container)).toHaveLength(0)
    expect(container.innerHTML).toBe('')
  })
})

describe('switching identity adds and removes the element', () => {
  it('activating then leaving the declaring personality leaves no residue', async () => {
    const declaring = WITH[0]
    const plain = WITHOUT[0]

    const first = mount(declaring.id)
    await waitFor(() => expect(elements(first.container)).toHaveLength(1))
    first.unmount()

    // A fresh mount under a plain identity is the reload-after-switching-back case:
    // the shell must come up with no overlay, not with a cached one.
    localStorage.setItem('personality', plain.id)
    const second = render(
      <PersonalityProvider>
        <PersonalityShellElement />
      </PersonalityProvider>,
    )
    await new Promise((r) => setTimeout(r, 50))
    expect(elements(second.container), 'residue after switching back').toHaveLength(0)
  })
})
