/** Deterministic card art for an app that ships NO hero image (PEP-3).
 *
 *  The Store card is art-forward: a banner caps every card. An app with a
 *  `heroUrl` shows its own image; an app without one used to show nothing at all,
 *  so the two rendered as different card SHAPES — one banner-topped, one not — and
 *  a grid mixing them read as half-broken. This module supplies the missing half:
 *  a stable gradient derived from the app NAME, so the same app always draws the
 *  same art (across reloads, machines and installs) and two adjacent cards rarely
 *  collide.
 *
 *  🔑 No literal colors. The gradient is composed from SCHEME TOKENS via
 *  `color-mix`, which is what keeps it correct in all twelve schemes and light/dark
 *  (and what keeps `design/tokenLint.test.ts` green — hex in app source is a hard
 *  failure there). A fixed hex pair would have been one line shorter and would have
 *  looked wrong the moment the user picked a non-coral scheme.
 */

/** The accent tokens the art may draw from. Deliberately the SEMANTIC + brand
 *  accents rather than the surface ramp: the art has to read as art, and a
 *  surface-on-surface gradient is invisible. Every entry is a real registered
 *  token (`design/tokenRegistry.ts`). */
const ART_TOKENS = [
  '--color-primary',
  '--color-secondary',
  '--color-info',
  '--color-ok',
  '--color-warn',
  '--color-danger',
] as const

/** FNV-1a over the name. Any stable hash works; this one is 4 lines, has no
 *  dependency, and is deterministic in a way `Math.random`/insertion order is not. */
export function artHash(name: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < name.length; i++) {
    h ^= name.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return h >>> 0
}

/** The token pair + angle this name draws. Exported for the test, which asserts
 *  determinism and that the two tokens always DIFFER (a same-token gradient is a
 *  flat wash — the fallback would read as a bug rather than as art). */
export function artStops(name: string): { from: string; to: string; angle: number } {
  const h = artHash(name)
  const n = ART_TOKENS.length
  const a = h % n
  // `+ 1 + (… % (n - 1))` cannot land back on `a`, so the pair is always distinct.
  const b = (a + 1 + ((h >>> 5) % (n - 1))) % n
  return { from: ART_TOKENS[a], to: ART_TOKENS[b], angle: 100 + ((h >>> 11) % 8) * 20 }
}

/** The CSS `background` for a hero-less app's banner. */
export function artGradient(name: string): string {
  const { from, to, angle } = artStops(name)
  return `linear-gradient(${angle}deg, `
    + `color-mix(in srgb, var(${from}) 30%, transparent), `
    + `color-mix(in srgb, var(${to}) 12%, transparent))`
}
