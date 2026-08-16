/**
 * PERSONALITY-THEMES §S1 — structural invariants for the personality registry.
 *
 * A personality is an identity a user can switch to, so the guarantees have to be
 * structural rather than promised in prose:
 *
 * - Its palette MUST come from `SCHEMES`, because that's what puts it inside
 *   `schemeContrast.test.ts`'s WCAG sweep. A personality carrying its own colors
 *   could ship an inaccessible palette that nothing checks.
 * - Its behavior block MUST stay within the closed, typed set — the property that
 *   lets a future app-contributed personality be validated against an allowlist
 *   instead of opening an arbitrary-code path.
 * - The default identity MUST exist and be fully restorable, or "switch back"
 *   silently leaves the shell personalized.
 */

import { describe, expect, it } from 'vitest'
import { SCHEMES } from './schemes'
import {
  DEFAULT_PERSONALITY,
  PERSONALITIES,
  getPersonality,
  resolvePersonality,
} from './personalities'
import { getShellElement } from './personalities'
import { getErrorTreatment } from './errorTreatments'

const SCHEME_IDS = new Set(SCHEMES.map((s) => s.id))

// The closed set of behavior keys. Adding one here is the deliberate act; a typo
// or a smuggled-in field fails this test.
const ALLOWED_BEHAVIOR_KEYS = new Set([
  'displayName',
  'wordmarkLabel',
  'faviconHref',
  'personaSnippet',
  'uiDensity',
  'documentTitle',
  'shellElement',
  'errorTreatment',
])

const ALLOWED_DENSITY = new Set(['comfortable', 'dense', 'cli'])

describe('personality registry invariants', () => {
  it('has at least the default plus one alternative to prove switching', () => {
    expect(PERSONALITIES.length).toBeGreaterThanOrEqual(2)
  })

  it('declares unique ids', () => {
    const ids = PERSONALITIES.map((p) => p.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('every baseScheme exists in SCHEMES', () => {
    // This is what puts every personality's palette through the contrast sweep.
    for (const p of PERSONALITIES) {
      expect(SCHEME_IDS.has(p.baseScheme), `${p.id} → ${p.baseScheme}`).toBe(true)
    }
  })

  it('every behavior key is in the closed set', () => {
    for (const p of PERSONALITIES) {
      for (const key of Object.keys(p.behavior)) {
        expect(ALLOWED_BEHAVIOR_KEYS.has(key), `${p.id}.behavior.${key}`).toBe(true)
      }
    }
  })

  it('uiDensity maps onto the existing density axis', () => {
    for (const p of PERSONALITIES) {
      if (p.behavior.uiDensity) {
        expect(ALLOWED_DENSITY.has(p.behavior.uiDensity), p.id).toBe(true)
      }
    }
  })

  it('favicons are bundled local assets, never remote URLs', () => {
    // A remote favicon would be an outbound request keyed to a UI preference.
    for (const p of PERSONALITIES) {
      const href = p.behavior.faviconHref
      if (href) {
        expect(href.startsWith('/'), `${p.id} → ${href}`).toBe(true)
        expect(/^https?:/i.test(href)).toBe(false)
      }
    }
  })

  it('persona snippets follow the bundled persona-<id> naming the backend validates', () => {
    for (const p of PERSONALITIES) {
      const snippet = p.behavior.personaSnippet
      if (snippet) expect(snippet).toBe(`persona-${p.id}`)
    }
  })

  it('every shellElement id resolves in its closed map', () => {
    // Same failure shape as a dangling treatment id, one layer worse: a shell
    // element that does not resolve mounts NOTHING, which is also what a correct
    // standard scheme does — so the personality would look merely plain rather
    // than broken. The registry's own contract (laziness, the decorative
    // invariants) is asserted by rendering in `shellElements.test.tsx`.
    for (const p of PERSONALITIES) {
      const id = p.behavior.shellElement
      if (id) expect(getShellElement(id), `${p.id} → ${id}`).not.toBeNull()
    }
  })

  it('every errorTreatment id resolves in its closed map', () => {
    // A dangling id would silently degrade to "no treatment" — the safe outcome,
    // and therefore the one nothing would notice. §S2's structural invariant.
    for (const p of PERSONALITIES) {
      const id = p.behavior.errorTreatment
      if (id) expect(getErrorTreatment(id), `${p.id} → ${id}`).not.toBeNull()
    }
  })

  it('every personality has a human label and hint', () => {
    for (const p of PERSONALITIES) {
      expect(p.label.trim().length).toBeGreaterThan(0)
      expect(p.hint.trim().length).toBeGreaterThan(0)
    }
  })
})

describe('the default identity is restorable', () => {
  it('the default id resolves to a real entry', () => {
    expect(getPersonality(DEFAULT_PERSONALITY)).toBeDefined()
  })

  it('the default carries no assistant rename', () => {
    // Restoring the default must not itself propose a name — it CLEARS the name.
    expect(getPersonality(DEFAULT_PERSONALITY)?.behavior.displayName).toBeUndefined()
  })

  it('an unknown or removed id falls back to the default, never a broken state', () => {
    expect(resolvePersonality('was-removed-in-a-later-release').id).toBe(DEFAULT_PERSONALITY)
    expect(resolvePersonality(undefined).id).toBe(DEFAULT_PERSONALITY)
  })
})
