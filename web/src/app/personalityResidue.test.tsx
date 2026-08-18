/**
 * PERSONALITY-THEMES §S2 (PT-6) — "switching back to a standard scheme leaves ZERO
 * residue", pinned as a test instead of as a tour note.
 *
 * 🔴 WHAT THE DRIVEN TOUR FOUND. `activate` restored the chrome perfectly, so the
 * provider's full-restore contract held — but the Design panel's scheme tiles never
 * called `activate`. They called `applyScheme` directly, so picking a plain scheme while
 * a personality was on swapped the palette and left EVERYTHING else: measured on the
 * real bundle, `document.title` stayed `TERM://PersonalClaw`, the favicon stayed the
 * identity's, the wordmark stayed `TERM://PC`, `data-personality` stayed
 * `retro-terminal`, the scanline overlay stayed mounted, `--ui-density` stayed `cli` and
 * the four dials stayed at the terminal's temperament. And because the identity is
 * persisted, a RELOAD did not clear it either — the residue was permanent, reachable in
 * two clicks from a fresh install.
 *
 * So the assertions here are deliberately on the FOUR THINGS THE ATOM NAMES — title,
 * favicon, name/wordmark, DOM — plus the density and dials that ride along, and every
 * one is checked against the value captured BEFORE any personality was active rather
 * than against a hardcoded expectation. A restore that writes the right constant into
 * the wrong document is not a restore.
 *
 * 🪤 THREE WAYS THIS FILE COULD PASS WITHOUT MEASURING ANYTHING, each closed below:
 *
 *  1. **jsdom has no favicon link.** `setFavicon` returns early when
 *     `link[rel~="icon"]` is absent, and `pristine.favicon` is captured at module
 *     scope — so without a link inserted BEFORE `./personality` is imported, every
 *     favicon assertion would compare `null` to `null` forever. One is inserted, and
 *     the precondition below asserts the personalized href actually differed.
 *  2. **Identities that change nothing.** Every restore assertion is preceded by a
 *     precondition that the value MOVED under the personality, so an identity that
 *     declared no title (or the same favicon as the default) cannot make its own
 *     round trip vacuous.
 *  3. **A fixed exit path.** `pickScheme` is asserted to be the panel's ONLY scheme
 *     gesture by reading the panel's source, because a green provider test says
 *     nothing about whether the shipped control calls it.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render, waitFor } from '@testing-library/react'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// The appearance store fetches saved themes on mount and nothing here cares. Left
// PENDING deliberately (the pattern personalityDials.test.tsx documents): a promise
// settling after render lands a setState outside act().
vi.mock('../lib/api', () => ({
  api: { themes: () => new Promise(() => {}), theme: () => new Promise(() => {}) },
}))

// ── the pristine chrome, established BEFORE personality.tsx captures it ──────
document.title = 'PersonalClaw'
const iconLink = document.createElement('link')
iconLink.setAttribute('rel', 'icon')
iconLink.setAttribute('href', '/claw.svg')
document.head.appendChild(iconLink)

const { AppearanceProvider, useAppearance } = await import('./appearance')
const { PersonalityProvider, PersonalityShellElement, usePersonality } = await import('./personality')
const { DEFAULT_PERSONALITY, PERSONALITIES, PERSONALITY_DIAL_TOKENS, getPersonality } =
  await import('../design/personalities')
const { TOKENS } = await import('../design/tokenRegistry')

const WEB = process.cwd()
/** The registry ENTRY behind a var name — `scalarValue`/`selectValue` take the token,
 *  not its name. Throws rather than falling back, so renaming a dial token reddens this
 *  file instead of quietly reading a default. */
function tokenFor(varName: string) {
  const t = TOKENS.find((tk) => tk.varName === varName)
  if (!t) throw new Error(`no token declares ${varName}`)
  return t
}
const DENSITY_TOKEN = tokenFor('--ui-density')
const DIAL_TOKENS = Object.values(PERSONALITY_DIAL_TOKENS).map(tokenFor)
const DEFAULT_ENTRY = getPersonality(DEFAULT_PERSONALITY)!
const OTHERS = PERSONALITIES.filter((p) => p.id !== DEFAULT_PERSONALITY)
/** A curated scheme that is nobody's `baseScheme` — a genuine "standard look" exit. */
const NEUTRAL_SCHEME = 'ocean'

type Ctl = {
  activate: (id: string) => void
  pickScheme: (id: string) => void
  personalityId: string
  wordmarkLabel: string
  activeScheme: string
  density: string
  dials: string
}
let ctl: Ctl

function Probe() {
  const p = usePersonality()
  const a = useAppearance()
  ctl = {
    activate: p.activate,
    pickScheme: p.pickScheme,
    personalityId: p.personality.id,
    wordmarkLabel: p.wordmarkLabel,
    activeScheme: a.activeScheme,
    density: a.selectValue(DENSITY_TOKEN),
    dials: DIAL_TOKENS.map((t) => (t.kind === 'scalar' ? a.scalarValue(t) : a.selectValue(t))).join('|'),
  }
  return null
}

function mount() {
  return render(
    <AppearanceProvider>
      <PersonalityProvider>
        <Probe />
        <PersonalityShellElement />
      </PersonalityProvider>
    </AppearanceProvider>,
  )
}

/** Everything the atom's residue clause names, read off the live document + store. */
function chrome() {
  return {
    title: document.title,
    favicon: document.querySelector<HTMLLinkElement>('link[rel~="icon"]')?.getAttribute('href') ?? null,
    dataPersonality: document.documentElement.dataset.personality ?? null,
    wordmark: ctl.wordmarkLabel,
    density: ctl.density,
    dials: ctl.dials,
  }
}

afterEach(() => {
  localStorage.clear()
  document.title = 'PersonalClaw'
  iconLink.setAttribute('href', '/claw.svg')
  document.documentElement.removeAttribute('data-personality')
})

describe('the residue population is real', () => {
  it('the jsdom document has the favicon link the restore path needs', () => {
    // Without it `setFavicon` returns early and every favicon assertion compares
    // null to null. Asserted here rather than trusted, because the insertion happens
    // at module scope where a failure would be silent.
    expect(document.querySelector('link[rel~="icon"]')).not.toBeNull()
  })

  it('at least one identity moves every value the restore assertions read', () => {
    expect(OTHERS.map((p) => p.id), 'no non-default identity exists — every test here is vacuous')
      .not.toEqual([])
    const movesTitle = OTHERS.filter((p) => p.behavior.documentTitle && p.behavior.documentTitle !== DEFAULT_ENTRY.behavior.documentTitle)
    const movesFavicon = OTHERS.filter((p) => p.behavior.faviconHref && p.behavior.faviconHref !== DEFAULT_ENTRY.behavior.faviconHref)
    const movesWordmark = OTHERS.filter((p) => p.behavior.wordmarkLabel !== DEFAULT_ENTRY.behavior.wordmarkLabel)
    const movesDials = OTHERS.filter((p) => p.behavior.dials && Object.keys(p.behavior.dials).length > 0)
    expect(movesTitle.map((p) => p.id), 'no identity changes the tab title').not.toEqual([])
    expect(movesFavicon.map((p) => p.id), 'no identity changes the favicon — the favicon restore is untested').not.toEqual([])
    expect(movesWordmark.map((p) => p.id), 'no identity changes the wordmark').not.toEqual([])
    expect(movesDials.map((p) => p.id), 'no identity moves a dial').not.toEqual([])
  })
})

describe.each(OTHERS.map((p) => [p.id] as const))('%s → a standard scheme', (id) => {
  const entry = getPersonality(id)!

  it('applies the identity, then leaves nothing behind when a plain scheme is picked', async () => {
    const { container } = mount()
    const pristine = chrome()
    expect(pristine.title).toBe('PersonalClaw')
    expect(pristine.favicon).toBe('/claw.svg')

    act(() => ctl.activate(id))
    const on = chrome()
    // ── precondition: the identity really landed (else the restore proves nothing) ──
    expect(on.dataPersonality).toBe(id)
    if (entry.behavior.documentTitle) expect(on.title).toBe(entry.behavior.documentTitle)
    if (entry.behavior.faviconHref) expect(on.favicon).toBe(entry.behavior.faviconHref)
    expect(on.wordmark).toBe(entry.behavior.wordmarkLabel)
    expect(ctl.activeScheme).toBe(entry.baseScheme)
    if (entry.behavior.shellElement) {
      await waitFor(() => expect(container.querySelectorAll('[data-shell-element]')).toHaveLength(1))
    }

    // ── the exit: pick a scheme that belongs to no identity ──
    act(() => ctl.pickScheme(NEUTRAL_SCHEME))
    const off = chrome()
    expect(ctl.activeScheme, 'the picked scheme must still win — restoring must not undo the pick')
      .toBe(NEUTRAL_SCHEME)
    expect(off.title, 'residue 1/4: tab title').toBe(pristine.title)
    expect(off.favicon, 'residue 2/4: favicon').toBe(pristine.favicon)
    expect(off.wordmark, 'residue 3/4: product name in the shell').toBe(pristine.wordmark)
    expect(off.dataPersonality, 'residue 4/4: data-personality on <html>').toBe(DEFAULT_PERSONALITY)
    expect(off.density, 'residue 4/4: interface density').toBe(pristine.density)
    expect(off.dials, 'residue 4/4: motion + backdrop dials').toBe(pristine.dials)
    expect(container.querySelectorAll('[data-shell-element]'), 'residue 4/4: contributed shell element')
      .toHaveLength(0)
    // Persisted, so the restore survives a reload rather than being undone by one.
    expect(localStorage.getItem('personality')).toBe(DEFAULT_PERSONALITY)
  })

  it("stays on when the picked scheme is the identity's OWN base scheme", () => {
    mount()
    act(() => ctl.activate(id))
    act(() => ctl.pickScheme(entry.baseScheme))
    expect(ctl.personalityId, 'clicking the tile that is already lit must not drop the identity')
      .toBe(id)
    expect(document.documentElement.dataset.personality).toBe(id)
    expect(ctl.activeScheme).toBe(entry.baseScheme)
  })

  it('is taken with "reset everything to defaults"', () => {
    mount()
    act(() => ctl.activate(id))
    expect(document.documentElement.dataset.personality).toBe(id)
    // The exact pair DesignPanel's reset button runs (see the source rail below).
    act(() => ctl.activate(DEFAULT_PERSONALITY))
    expect(document.title).toBe('PersonalClaw')
    expect(document.querySelector<HTMLLinkElement>('link[rel~="icon"]')?.getAttribute('href')).toBe('/claw.svg')
    expect(document.documentElement.dataset.personality).toBe(DEFAULT_PERSONALITY)
    expect(ctl.wordmarkLabel).toBe('PersonalClaw')
  })
})

describe('the Design panel has no way to change a scheme that skips the identity', () => {
  const src = readFileSync(join(WEB, 'src/pages/settings/DesignPanel.tsx'), 'utf8')

  it('reads the panel it is asserting about', () => {
    expect(src.length, 'DesignPanel.tsx not found — this rail is measuring nothing').toBeGreaterThan(2000)
    expect(src).toContain('SchemeTile')
  })

  it('routes the scheme tiles through pickScheme', () => {
    expect(src).toContain('onPick={() => pickScheme(s.id)}')
  })

  it('never destructures applyScheme, the bypass that caused the residue', () => {
    // Narrow on purpose: the rail is "this panel does not hold applyScheme", not "the
    // string never appears" — the comment above the hook explains why it is absent.
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code).not.toMatch(/applyScheme/)
  })

  it('takes the identity with "reset everything to defaults"', () => {
    expect(src).toContain('onClick={resetEverything}')
    expect(src).toMatch(/resetEverything = \(\) => \{[\s\S]*activate\(DEFAULT_PERSONALITY\)[\s\S]*resetAll\(\)/)
  })
})

describe('every declared favicon is a file the gateway actually serves', () => {
  // 🔴 All three identities declared `/favicon.svg`, which exists NOWHERE in the repo.
  // `web/public/` has only `claw.svg`, and the gateway routes exactly one dist-root SVG
  // (`/claw.svg`) plus the `/icons/` directory — so `GET /favicon.svg` fell through to
  // the SPA catch-all and returned **200 text/html, 15860 bytes** of index.html. Measured
  // on the live gateway during the PT-6 tour. A 200 means no console error and no failed
  // request: the only symptom was a tab with no mark.
  const ROUTED = ['/claw.svg', '/icons/']

  it.each(PERSONALITIES.filter((p) => p.behavior.faviconHref).map((p) => [p.id, p.behavior.faviconHref!] as const))(
    '%s → %s exists and sits under a routed prefix',
    (_id, href) => {
      expect(href.startsWith('/'), 'a favicon must be a bundled local path').toBe(true)
      expect(existsSync(join(WEB, 'public', href.slice(1)))).toBe(true)
      expect(
        ROUTED.some((r) => (r.endsWith('/') ? href.startsWith(r) : href === r)),
        `${href} is not under a static route (${ROUTED.join(', ')}), so the gateway would ` +
          `serve index.html for it — a 200 that is not an image`,
      ).toBe(true)
    },
  )

  it('at least one identity declares a favicon of its own', () => {
    const distinct = OTHERS.filter((p) => p.behavior.faviconHref && p.behavior.faviconHref !== DEFAULT_ENTRY.behavior.faviconHref)
    expect(distinct.map((p) => p.id), 'every identity shares the default favicon — the swap and its restore are both untested')
      .not.toEqual([])
  })
})
