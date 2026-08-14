/**
 * PERSONALITY-THEMES §S2 (T2.3) — the two properties that make an error skin safe.
 *
 * **1. A standard scheme is unchanged.** The default identity declares no
 * treatment, so both error surfaces must render exactly what they rendered before
 * any of this code existed. That is asserted against markup CAPTURED FROM THE
 * PRE-CHANGE TREE (commit 323265b6, before PT-4), not against a self-generated
 * snapshot — a snapshot written after the change would happily bless a regression.
 * If a future change to the copy or chrome makes these literals fail, re-capture
 * them deliberately; do not relax the comparison.
 *
 * **2. A treatment is a skin and nothing else.** For every personality in the
 * registry the copy, the accessible names, the actions and the `role` must be
 * identical to the default's — only classes and colours may differ. This is the
 * assertion that keeps a "theme" from quietly rewording or disarming a failure.
 *
 * The personality arrives through the REAL provider (localStorage → context →
 * useErrorTreatment → the surface), so the wiring under test is the shipped wiring.
 * Only `./appearance` is stubbed: it owns colour application, which PT-1 covers and
 * which needs a provider stack that has nothing to do with this atom.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { ErrorBoundary } from './ErrorBoundary'
import { IncidentBanner } from './IncidentBanner'
import { PersonalityProvider } from './personality'
import { DEFAULT_PERSONALITY, PERSONALITIES } from '../design/personalities'
import { ERROR_TREATMENTS } from '../design/errorTreatments'

vi.mock('./appearance', () => ({
  useAppearance: () => ({ applyScheme: () => {}, setSelect: () => {} }),
}))

vi.mock('../lib/api', () => ({
  api: {
    incident: () => Promise.resolve({ active: true, reason: 'disk full' }),
    incidentResume: () => Promise.resolve({}),
  },
}))

// ── The frozen pre-change markup ────────────────────────────────────────────
//
// Captured by rendering the surfaces on 323265b6 (parent of this change) and
// printing `container.innerHTML`. Lucide's `<svg>` CHILDREN are normalised away
// (see `normalise`) so an icon-library upgrade cannot make this red — every svg
// ATTRIBUTE, including the class the treatment overrides, is still compared.

const EB_BEFORE =
  '<div class="flex h-full flex-col items-center justify-center gap-m px-l text-center">' +
  '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
  'class="lucide lucide-triangle-alert text-on-surface-low" aria-hidden="true"></svg>' +
  '<div class="text-on-surface text-[1.0625rem]" style="font-variation-settings: &quot;wght&quot; 500;">' +
  'This page hit an error</div>' +
  '<p class="max-w-md text-on-surface-low text-[0.8125rem]">kaboom</p>' +
  '<button type="button" class="inline-flex items-center gap-1.5 rounded-md px-3 h-9 text-[0.8125rem]" ' +
  'style="background: var(--color-primary); color: var(--color-on-primary);">' +
  '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
  'class="lucide lucide-rotate-ccw" aria-hidden="true"></svg> Retry</button></div>'

/** The IncidentBanner's own chrome, pre-change. Its Resume button is the `Button`
 *  primitive, whose internals carry motion-dependent inline styles — freezing
 *  those would be a flake, so the button is instead compared ACROSS personalities
 *  below (which is the property that matters: the action is untouched). */
const IB_BEFORE = {
  role: 'alert',
  class: 'flex items-center gap-3 px-4 py-2 text-[0.8125rem]',
  style: 'background: var(--color-error-container); color: var(--color-on-error-container);',
  iconClass: 'lucide lucide-triangle-alert shrink-0',
  text:
    'Incident mode is active — all unattended work (cron, hooks, triggers, subagents) is ' +
    'suspended · disk full. Chat still works.Resume',
}

/** Drop lucide's path geometry, keep every `<svg>` attribute. */
function normalise(html: string): string {
  return html.replace(/(<svg[^>]*>)[\s\S]*?<\/svg>/g, '$1</svg>')
}

function activate(id: string) {
  localStorage.setItem('personality', id)
}

function Boom({ fail }: { fail: { current: boolean } }): ReactNode {
  if (fail.current) throw new Error('kaboom')
  return <div>page content</div>
}

function renderBoundary(fail = { current: true }) {
  const r = render(
    <PersonalityProvider>
      <ErrorBoundary>
        <Boom fail={fail} />
      </ErrorBoundary>
    </PersonalityProvider>,
  )
  return { ...r, fail }
}

async function renderBanner() {
  const r = render(
    <PersonalityProvider>
      <IncidentBanner />
    </PersonalityProvider>,
  )
  const alert = await screen.findByRole('alert')
  return { ...r, alert }
}

let consoleError: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  // React logs every caught render error; the boundary is doing its job.
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
})
afterEach(() => {
  consoleError.mockRestore()
  localStorage.clear()
  document.documentElement.removeAttribute('data-personality')
})

describe('under a standard scheme both surfaces are identical to before PT-4', () => {
  it('the ErrorBoundary fallback renders the exact pre-change markup', () => {
    activate(DEFAULT_PERSONALITY)
    const { container } = renderBoundary()
    expect(normalise(container.innerHTML)).toBe(EB_BEFORE)
  })

  it('with NO stored personality at all, the fallback is still the pre-change markup', () => {
    // A first-run browser has no `personality` key. This is the real default path.
    const { container } = renderBoundary()
    expect(normalise(container.innerHTML)).toBe(EB_BEFORE)
  })

  it('the IncidentBanner renders the exact pre-change chrome', async () => {
    activate(DEFAULT_PERSONALITY)
    const { alert } = await renderBanner()
    expect(alert.getAttribute('role')).toBe(IB_BEFORE.role)
    expect(alert.getAttribute('class')).toBe(IB_BEFORE.class)
    expect(alert.getAttribute('style')).toBe(IB_BEFORE.style)
    expect(alert.querySelector('svg')?.getAttribute('class')).toBe(IB_BEFORE.iconClass)
    expect(alert.textContent).toBe(IB_BEFORE.text)
  })
})

describe('a treatment changes the skin and only the skin', () => {
  /** The default render, as the reference every personality is compared to. */
  function referenceBoundary() {
    localStorage.clear()
    const { container, unmount } = renderBoundary()
    const text = container.textContent ?? ''
    const button = screen.getByRole('button').getAttribute('class')
    const name = screen.getByRole('button').textContent
    unmount()
    return { text, button, name }
  }

  it('every personality keeps the fallback copy, the button name and the action chrome', () => {
    const ref = referenceBoundary()
    for (const p of PERSONALITIES) {
      activate(p.id)
      const { container, unmount } = renderBoundary()
      expect(container.textContent, `${p.id} copy`).toBe(ref.text)
      const btn = screen.getByRole('button', { name: /retry/i })
      expect(btn.textContent, `${p.id} button name`).toBe(ref.name)
      expect(btn.getAttribute('class'), `${p.id} button chrome`).toBe(ref.button)
      unmount()
    }
  })

  it('every personality keeps the banner copy, its role and its Resume action', async () => {
    for (const p of PERSONALITIES) {
      activate(p.id)
      const { alert, unmount } = await renderBanner()
      expect(alert.getAttribute('role'), `${p.id} role`).toBe('alert')
      expect(alert.textContent, `${p.id} copy`).toBe(IB_BEFORE.text)
      expect(screen.getByRole('button', { name: /resume/i })).toBeTruthy()
      unmount()
    }
  })

  it('the Retry action still resets the boundary under every personality', () => {
    for (const p of PERSONALITIES) {
      activate(p.id)
      const { fail, unmount } = renderBoundary({ current: true })
      fail.current = false
      fireEvent.click(screen.getByRole('button', { name: /retry/i }))
      expect(screen.getByText('page content'), `${p.id} retry`).toBeTruthy()
      unmount()
    }
  })

  it('a personality WITH a treatment actually renders it (both surfaces)', async () => {
    const treated = PERSONALITIES.filter((p) => p.behavior.errorTreatment)
    expect(treated.length, 'no personality declares a treatment — the tests above are vacuous')
      .toBeGreaterThan(0)

    for (const p of treated) {
      const t = ERROR_TREATMENTS[p.behavior.errorTreatment!]
      activate(p.id)

      const boundary = renderBoundary()
      const panel = boundary.container.firstElementChild as HTMLElement
      expect(panel.getAttribute('class'), `${p.id} panel skin`).toContain(t.surfaceClass)
      expect(panel.getAttribute('style'), `${p.id} panel paint`).toContain(`var(${t.paint.bg})`)
      expect(panel.querySelector('svg')?.getAttribute('class')).toContain(t.iconClass)
      // The base ink class must be REPLACED, not appended: two colour utilities on
      // one element resolve by stylesheet order, so appending is not a win.
      expect(panel.querySelector('svg')?.getAttribute('class')).not.toContain('text-on-surface-low')
      expect(normalise(boundary.container.innerHTML), `${p.id} differs from default`).not.toBe(EB_BEFORE)
      boundary.unmount()

      const banner = await renderBanner()
      expect(banner.alert.getAttribute('class'), `${p.id} banner skin`).toContain(t.surfaceClass)
      expect(banner.alert.getAttribute('style'), `${p.id} banner paint`).toContain(`var(${t.paint.ink})`)
      banner.unmount()
    }
  })

  it('the default identity declares NO treatment — that is what makes it pixel-stable', () => {
    const dflt = PERSONALITIES.find((p) => p.id === DEFAULT_PERSONALITY)
    expect(dflt?.behavior.errorTreatment).toBeUndefined()
  })
})
