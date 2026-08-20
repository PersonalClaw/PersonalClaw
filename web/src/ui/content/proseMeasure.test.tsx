import { describe, it, expect, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render } from '@testing-library/react'
import { PROSE_MEASURE, PROSE_MEASURE_CLASS } from '../../design/measure'
import { DocumentPreview } from './renderers'
import { exportDocumentHtml } from './exporters'

// ── The two prose measures converge on ONE token, and 72ch is retired ─────────────
//
// A document had TWO line lengths depending on where you met it: the in-app preview
// (`renderers.tsx` DocumentPreview) capped its container at 72 `ch` units and the HTML
// export (`exporters.ts`) set the same 72 on `main` — and that is not 72 characters.
// `ch` is the advance width of "0", 0.66em in this font, so it resolved to 758px and a
// MEASURED 101 characters on a full line, past the 45-90 band a reader can return-sweep
// without losing their place. The knowledge reader had already been corrected to 35rem
// (~75 characters) by measurement and records the finding in its own comment.
//
// So this is a real VISUAL change to the document preview, not a refactor: the preview
// gets narrower, on purpose.
//
// The retired utility is deliberately never spelled out below: Tailwind's scanner reads
// source TEXT, comments included, so naming the old class — even in prose — puts its
// now-dead rule back into the shipped CSS. Verified: it did, until this was reworded.
//
// WHAT THIS ASSERTS, AND WHY IT IS THE CONVERGENCE AND NOT THE CONSTANT. A shared token
// that nobody reads is the exact defect this repo keeps finding, so the constant's
// existence proves nothing on its own. Each consumer is checked at its own consumption
// point: the preview through a real RENDER (the class must land on the rendered `.doc`
// element), the export through the actual EMITTED STYLESHEET (the bytes a user opens),
// and both source files through a `72ch`-is-gone guard that carries a vacuity floor —
// a "does not contain" assertion passes perfectly against a file that is empty, moved,
// or renamed.

const SRC = join(process.cwd(), 'src')
const RENDERERS = join(SRC, 'ui/content/renderers.tsx')
const EXPORTERS = join(SRC, 'ui/content/exporters.ts')

/** Captures the Blob `download()` hands to `URL.createObjectURL`, which is what the
 *  user's file actually contains. jsdom implements neither object-URL function. */
function captureDownload(): { blobs: Blob[]; restore: () => void } {
  const blobs: Blob[] = []
  const create = vi.fn((b: Blob) => { blobs.push(b); return 'blob:captured' })
  const prevCreate = URL.createObjectURL
  const prevRevoke = URL.revokeObjectURL
  URL.createObjectURL = create as unknown as typeof URL.createObjectURL
  URL.revokeObjectURL = (() => {}) as unknown as typeof URL.revokeObjectURL
  return {
    blobs,
    restore: () => { URL.createObjectURL = prevCreate; URL.revokeObjectURL = prevRevoke },
  }
}

describe('the prose measure is one token', () => {
  it('is stated in rem, at the measured value', () => {
    expect(PROSE_MEASURE).toBe('35rem')
    // The unit is the whole point of the finding: `ch` LOOKS like a character count and
    // is not one, so the token must never regress to it.
    expect(PROSE_MEASURE).toMatch(/^\d+(\.\d+)?rem$/)
    // 35rem measured ~75 characters in this font, so the readable 45-90 band lands
    // roughly inside 21-42rem. A value outside that is a redesign, not a tweak, and
    // should re-do the measurement rather than move this bound.
    const rem = Number.parseFloat(PROSE_MEASURE)
    expect(rem).toBeGreaterThanOrEqual(21)
    expect(rem).toBeLessThanOrEqual(42)
  })

  it('offers a Tailwind form that cannot drift from the length', () => {
    // PROSE_MEASURE_CLASS is a literal on purpose (Tailwind v4 only emits utilities for
    // candidate strings it finds in source, so a template-composed class would compile to
    // nothing). This is what stops the literal from drifting.
    expect(PROSE_MEASURE_CLASS).toBe(`max-w-[${PROSE_MEASURE}]`)
  })
})

describe('the document preview applies the token', () => {
  it('renders the measure class on the .doc container, not 72ch', () => {
    const { container } = render(
      <DocumentPreview content="<p>Editorial body</p>" mode="dark" title="A document" />,
    )
    const doc = container.querySelector('.doc')
    // Vacuity floor for THIS assertion: DocumentPreview has three other exits (markdown
    // fallback, SanitizedEmpty, empty content). If any of them ran there would be no
    // `.doc` element at all and a class check would be asserting about nothing.
    expect(doc, 'the document branch must have rendered a .doc container').not.toBeNull()
    expect(doc!.innerHTML).toContain('Editorial body')

    expect(doc!.className.split(/\s+/)).toContain(PROSE_MEASURE_CLASS)
    expect(doc!.className).not.toContain('72ch')
  })
})

describe('the HTML export carries the same measure', () => {
  it('emits the converged measure in its stylesheet and no 72ch', async () => {
    const cap = captureDownload()
    try {
      exportDocumentHtml('<p>Exported body</p>', 'Doc')
      expect(cap.blobs, 'exportDocumentHtml must have produced one downloadable blob').toHaveLength(1)
      const html = await cap.blobs[0].text()

      // Vacuity floor: prove we are reading the real standalone document before
      // asserting what it does NOT contain.
      expect(html).toContain('<main>')
      expect(html).toContain('Exported body')

      expect(html).toContain(`max-width: ${PROSE_MEASURE}`)
      expect(html).not.toContain('72ch')
      // The standalone-document constraint: this file never loads the app's tokens.css,
      // and an unresolved `var()` makes the whole declaration invalid — the export would
      // silently lose its measure entirely. So the value must be INLINED, not referenced.
      expect(html).not.toContain('var(--')
    } finally {
      cap.restore()
    }
  })
})

describe('72ch is retired from both consumers', () => {
  const files: Array<[string, string, string]> = [
    ['ui/content/renderers.tsx', RENDERERS, 'DocumentPreview'],
    ['ui/content/exporters.ts', EXPORTERS, 'exportDocumentHtml'],
  ]

  it.each(files)('%s is found, non-trivial, and reads the shared token', (rel, abs, anchor) => {
    // THE VACUITY FLOOR. `readFileSync` throws on a bad path, but a "does not contain"
    // assertion also passes against a file that was gutted, renamed, or truncated — so
    // prove the file is the real one before trusting its silence.
    const src = readFileSync(abs, 'utf8')
    expect(src.split('\n').length, `${rel} looks truncated`).toBeGreaterThan(50)
    expect(src, `${rel} is not the file this rail was written against`).toContain(anchor)
    // Convergence, at the source: both consumers must IMPORT the token rather than
    // restate a number.
    expect(src, `${rel} must import the shared prose measure`).toMatch(
      /from '\.\.\/\.\.\/design\/measure'/,
    )
  })

  // Deliberately a WHOLE-FILE scan, comments included. The `ch`-unit measurement is
  // history now, and its one home is `design/measure.ts` (plus the knowledge reader's own
  // container comment); a consumer that still names the old cap in prose is stale
  // documentation pointing at a number the code no longer uses. Keeping the rule
  // "the string appears nowhere in these two files" also keeps it un-foolable — no
  // comment-stripper to get subtly wrong.
  it.each(files)('%s contains no 72ch', (rel, abs) => {
    const src = readFileSync(abs, 'utf8')
    expect(src.includes('72ch'), `${rel} still caps prose at 72ch`).toBe(false)
  })
})
