import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// The knowledge item detail lays its metadata (provider/size/shape/words/age) and the live
// ingestion ProcessingStrip out on ONE row, with the strip pushed right by `ml-auto`. At a
// phone width the two cannot both fit: measured 422px of content in a 390px viewport, and
// because the document does not scroll horizontally those 32px were CLIPPED — the last
// ingestion stage was simply invisible on a phone.
//
// Two classes had to hold together, which is why this rail checks both:
//   `flex-wrap` on the row  — gives the strip a second line to drop to (and is also what
//                             makes the row's `gap-y-1` mean anything; without wrapping
//                             there is no second line to space, so it was inert).
//   NOT `shrink-0` on the strip wrapper — the strip is itself a `flex-wrap` row of stages,
//                             so it wraps its own stages when given a narrower box. Pinned
//                             to max-content (394px) it still exceeded a 390px line even
//                             alone, so its own wrapping never engaged.
//
// 🪤 COMMENTS ARE STRIPPED BEFORE SCANNING. This very comment names `shrink-0` and
// `flex-wrap`, so a scan that reads the raw file would match its own documentation and pass
// no matter what the JSX says. That has produced false green rails in this repo before.
// `process.cwd()` is the repo's idiom for these source-scanning rails and is correct because
// vitest's root is `web/`. (Not `import.meta.url`: vitest rewrites it to a non-file URL, so
// `fileURLToPath` throws at collection time — which surfaces as "0 test", a failure mode that
// reads like the file was skipped rather than broken.)
const FILE = join(process.cwd(), 'src', 'pages', 'knowledge', 'KnowledgeDetail.tsx')

/** Source with line comments, block comments and JSX comments removed. */
function code(): string {
  return readFileSync(FILE, 'utf8')
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^[ \t]*\/\/.*$/gm, '')
}

/** The opening tag of the row that holds the ProcessingStrip, plus that strip's own wrapper.
 *
 *  🪤 Anchored on NAMED classes, not on tag ORDER. Counting opening `<div>`s backwards from the
 *  strip does not track nesting, so "the second-to-last div" resolves to the METADATA BLOCK
 *  (which already carries `flex-wrap`) rather than the enclosing row — making a `flex-wrap`
 *  assertion pass vacuously against the very source it was written to reject. That is exactly
 *  what happened when this rail was first written; the vacuity test below is what caught it. */
function metadataRow(src: string): { row: string; wrapper: string } {
  const strip = src.indexOf('<ProcessingStrip')
  expect(strip, 'ProcessingStrip is no longer rendered here — this rail is measuring nothing').toBeGreaterThan(-1)
  const before = src.slice(0, strip)
  const wrappers = [...before.matchAll(/<div className="[^"]*\bml-auto\b[^"]*">/g)]
  const rows = [...before.matchAll(/<div className="[^"]*\bitems-start\b[^"]*">/g)]
  expect(wrappers.length, 'no ml-auto wrapper found before ProcessingStrip').toBeGreaterThan(0)
  expect(rows.length, 'no items-start row found before ProcessingStrip').toBeGreaterThan(0)
  const wrapper = wrappers[wrappers.length - 1][0]
  const row = rows[rows.length - 1][0]
  // The row and the wrapper must be different elements, and the row must not be the
  // metadata block (that one is `items-center`) — otherwise we are measuring the wrong div.
  expect(row).not.toBe(wrapper)
  expect(row, `resolved the wrong element as the row: ${row}`).not.toMatch(/\bitems-center\b/)
  return { wrapper, row }
}

describe('knowledge item detail — metadata row must survive a phone width', () => {
  it('wraps, so the ingestion strip can drop to its own line instead of being clipped', () => {
    const { row } = metadataRow(code())
    expect(row, `the row holding the metadata + ProcessingStrip must be able to wrap: ${row}`)
      .toMatch(/\bflex-wrap\b/)
  })

  it('lets the strip shrink so its own stage wrapping can engage', () => {
    const { wrapper } = metadataRow(code())
    expect(wrapper, `the ProcessingStrip wrapper must not be pinned to max-content: ${wrapper}`)
      .not.toMatch(/\bshrink-0\b/)
  })

  it('still right-aligns the strip on a wide row', () => {
    const { wrapper } = metadataRow(code())
    expect(wrapper).toMatch(/\bml-auto\b/)
  })

  // Vacuity floor: if comment-stripping ever ate the JSX, every assertion above would pass
  // against an empty string. Prove the scanned source still contains the row's real classes.
  it('the stripped source still contains real JSX (guard against a vacuous pass)', () => {
    const src = code()
    expect(src).toContain('<ProcessingStrip')
    expect(src).toMatch(/className="flex flex-wrap shrink-0 items-start/)
    // and prove the stripper actually removed the prose that names the same utilities
    expect(src).not.toContain('measured 422px')
  })
})
