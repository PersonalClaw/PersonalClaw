import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The Store detail panel carries the card's banner treatment — for EVERY app ────────
//
// The card and the detail panel are one continuous gesture (click card → panel). The
// card renders a banner unconditionally: the app's own hero image, or its deterministic
// token gradient (appArt.ts) when it ships none — PEP-3's fix for the grid where
// hero-less cards read as a different, half-broken component. The detail panel used to
// render its banner ONLY for a heroUrl, so opening any of the ~53 hero-less apps dropped
// the banner at the transition and re-created the exact two-shapes defect inside one
// flow. This pins the panel's banner to the card's real contract.
//
// A source pin rather than a full mount: StoreDetailPanel is module-private and sits
// behind Store data loading; the defect class is structural (a conditional wrapping the
// banner div), which the stripped source states directly — the same idiom as the
// hit-target and side-stripe censuses.

const SRC = join(process.cwd(), 'src')

function code(abs: string): string {
  return readFileSync(abs, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|\s)\/\/.*$/gm, '$1')
}

function panelSlice(): string {
  const src = code(join(SRC, 'pages/apps/AppsSection.tsx'))
  const start = src.indexOf('function StoreDetailPanel')
  expect(start, 'StoreDetailPanel renamed — re-point this rail').toBeGreaterThan(-1)
  const next = src.indexOf('\nfunction ', start + 1)
  return src.slice(start, next === -1 ? undefined : next)
}

describe('StoreDetailPanel banner (the card contract, both art paths)', () => {
  it('renders the banner unconditionally with the two-path art seam', () => {
    const slice = panelSlice()
    expect(slice, 'the data-art seam is gone — the banner lost its two-path contract')
      .toContain("data-art={item.heroUrl ? 'hero' : 'generated'}")
    expect(slice, 'the generated-art fallback is gone — hero-less apps drop the banner again')
      .toContain('artGradient(item.name)')
  })

  it('does not gate the banner block behind heroUrl', () => {
    const slice = panelSlice()
    // The old defect's exact shape: the banner div reachable only inside a
    // `{item.heroUrl && (` conditional. The hero <img> alone may (and does) stay
    // conditional — the DIV carrying data-art must not be.
    const bannerAt = slice.indexOf('data-art=')
    const gate = slice.lastIndexOf('{item.heroUrl && (', bannerAt)
    const divStart = slice.lastIndexOf('<div', bannerAt)
    expect(
      gate === -1 || gate < slice.lastIndexOf('return (', bannerAt) || gate < divStart - 200,
      'the banner div is gated behind item.heroUrl again',
    ).toBe(true)
    expect(divStart, 'no banner div found near the data-art seam').toBeGreaterThan(-1)
  })
})
