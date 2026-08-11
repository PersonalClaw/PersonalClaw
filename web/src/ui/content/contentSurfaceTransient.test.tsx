import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── ContentSurface's transient + scroll states ───────────────────────────────────────
//
// Cycle 52 covered the two APP-WIDE transient mechanisms (the toast host, and `Button`'s
// `loading` → `aria-busy`). This covers the surface that opts out of both: `ContentSurface`
// builds its toolbar from RAW `<button>`s rather than the `Button` primitive, so it inherits
// none of the primitive's wiring.
//
// Measured on the live DOM (artifacts → open a document), before:
//
//   ContentSurface toolbar: Download / Preview / Split / Edit / Revert / Save (⌘S) / Save as
//   …every one of them  aria-busy: null
//   blocking: scrollable-region-focusable(1)   ← 790px of the document unreachable
//
// TWO DEFECTS, both in states that only exist for a moment or under a condition:
//
// 1. THE PREVIEW SCROLLERS HAD NO TAB STOP. Rendered markdown carries no focusable content,
//    so a keyboard user could not scroll the preview at all (WCAG 2.1.1). There are TWO —
//    the `preview` view and the `split` view each own their own scroller, and the split one
//    is only reachable after a view switch, which is why a route-level scan never sees it.
//    Same resolution as the kanban columns / shell denylist / inbox procedure / diagnostics
//    log: `tabIndex={0}` + `role="group"` + a name, which hands scrolling to the browser.
//    Verified after: keyboard PageDown moved scrollTop 0 → 683, region named
//    "<document title> preview".
//
// 2. A SAVE IN FLIGHT ANNOUNCED NOTHING. `saving` swaps the leading icon for a spinner and
//    disables the button — a purely visual signal. These being raw `<button>`s, cycle 52's
//    `Button loading` → `aria-busy` fix does not reach them, so they need it explicitly.
//
// This is a SOURCE rail on purpose. Driving a real save needs the CodeMirror editor mounted
// against a writable document; the editor did not mount in the validation fixture, so a DOM
// probe here would assert nothing. The scroll fix WAS verified live (above); the `saving`
// wiring is pinned here instead of left unmeasured.

const SRC = join(process.cwd(), 'src')
const source = readFileSync(join(SRC, 'ui/content/ContentSurface.tsx'), 'utf8')

/** The full opening tag starting at `from`, tracking {} depth so a `>` inside an attribute
 *  value (`onClick={() => f()}`) does not truncate it — the cycle-51 lesson. */
function tagAt(from: number): string {
  let depth = 0
  for (let i = from; i < source.length; i++) {
    const ch = source[i]
    if (ch === '{') depth++
    else if (ch === '}') depth--
    else if (ch === '>' && depth === 0) return source.slice(from, i + 1)
  }
  return ''
}

function tagsFor(re: RegExp): string[] {
  return [...source.matchAll(re)].map((m) => tagAt(m.index!))
}

describe('the preview scrollers are keyboard-reachable', () => {
  // Both scrollers, found by their refs rather than by a class string so a restyle does not
  // silently drop one out of scope.
  const scrollers = tagsFor(/<div ref=\{(?:previewScrollRef|splitPreviewRef)\}/g)

  it('finds BOTH of them (not vacuously green)', () => {
    // preview view + split view. If this drops to 1, a scroller was renamed or removed and
    // the assertions below would silently stop covering it.
    expect(scrollers.length, 'expected the preview and split preview scrollers').toBe(2)
  })

  for (const [i, tag] of scrollers.entries()) {
    it(`scroller ${i + 1} owns a tab stop, a role and a name`, () => {
      expect(tag, 'without a tab stop the preview cannot be scrolled by keyboard').toContain('tabIndex={0}')
      expect(tag, 'role=group keeps it announced as a container, not an unnamed widget').toContain('role="group"')
      expect(tag, 'an unnamed region announces nothing useful').toMatch(/aria-label=/)
    })
  }
})

describe('an in-flight save says it is busy', () => {
  // The toolbar is built from RAW <button>s, so it does not inherit Button's loading wiring.
  // Every button gated on `saving` must carry aria-busy itself.
  //
  // Collect the tags FIRST, then filter on their own text. A lookahead like
  // `<button(?=[^>]*disabled=\{[^}]*saving)` reads past the tag's closing `>` into the next
  // element, so it matched only 1 of the 2 real buttons — the same class of mistake as
  // scanning a JSX tag to its first `>`. Filtering complete tags cannot over- or under-reach.
  const gated = tagsFor(/<button\b/g).filter((t) => /\bdisabled=\{[^}]*\bsaving\b/.test(t))

  it('finds the saving-gated buttons (not vacuously green)', () => {
    // Save + the caller-supplied custom actions.
    expect(gated.length, 'expected the save and action buttons').toBeGreaterThanOrEqual(2)
  })

  for (const [i, tag] of gated.entries()) {
    it(`saving-gated button ${i + 1} carries aria-busy`, () => {
      expect(
        tag,
        'a raw <button> disabled by `saving` shows only a decorative spinner — without\n' +
          'aria-busy the action announces nothing while it runs:\n  ' + tag.replace(/\s+/g, ' ').slice(0, 120),
      ).toMatch(/aria-busy=\{saving \|\| undefined\}/)
    })
  }

  it('does not emit aria-busy="false" when idle', () => {
    // `|| undefined` keeps the attribute ABSENT rather than false — a false on every button
    // in the tree is noise, not signal.
    for (const tag of gated) expect(tag).not.toMatch(/aria-busy=\{saving\}/)
  })
})
