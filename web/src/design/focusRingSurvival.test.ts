import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

// ── `outline-none` silently defeats the app-wide keyboard focus ring ────────────
//
// `design/tokens.css` provides ONE global ring for the whole app:
//
//     :focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px }
//
// and its comment says that is what "makes the whole app navigable by keyboard without
// per-component work". `consistencyAudit` asserts that rule EXISTS (`hasGlobalFocusRing`) — and
// nothing asserts it SURVIVES on any given element.
//
// It does not survive `outline-none`. Measured on the BUILT stylesheet
// (`web/dist/assets/index-*.css`), not reasoned from the source:
//
//   :focus-visible{outline:2px solid var(--color-primary);outline-offset:2px}   byte  92269  @layer base
//   .outline-none{--tw-outline-style:none;outline-style:none}                   byte 156514  @layer utilities
//
// `.outline-none` is both LATER in the file and in `@layer utilities`, which beats `@layer base`.
// It sets `outline-style: none`, so the global ring's width and colour survive while its STYLE is
// removed — the ring is simply not painted. An element carrying `outline-none` with no replacement
// therefore takes keyboard focus with no visible indicator, which is the exact failure
// `focusRevealContract` covers for opacity and nobody covered for outline.
//
// ── Why the credit is FILE-WIDE, calibrated rather than assumed ──
//
// A first cut searched +/-3 lines around each `outline-none` and reported 35 sites. Checking them
// showed most were correct by construction, in two distinct ways:
//
//   * **The ring lives on the WRAPPER, sometimes in another file.** `ui/RowHitTarget`'s own
//     docstring prescribes it: the button sits at `-z-10`, so the ring is drawn by the parent via
//     `has-[>button:focus-visible]:ring-2`. All SEVEN consumers already do this, and
//     `ui/ListScaffold` rings its own row through `:has(> button:focus-visible)`. Zero defects
//     there — a negative result worth keeping, since the shape looks alarming.
//   * **The ring is composed in a constant elsewhere in the file.** `ui/SearchField` puts
//     `outline-none` in `INPUT_CHROME` and `focus:ring-2 focus:ring-inset` in `OVERLAY_FOCUS`
//     seven lines below, and documents why its INLINE variant deliberately has no ring (the
//     palette row's focus is carried by the modal context; an inset rectangle inside a round row
//     would redesign a hero surface). A deliberate, written taste call — not drift.
//
// So a near-context window mostly measures formatter line breaks. This rail instead credits a file
// that provides ANY focus treatment ANYWHERE, and counts only files that provide NONE. That took
// the population 35 -> 21 (crediting the wrapper pattern) -> **10** (crediting file-wide
// composition). Ten is worth guarding; thirty-five was mostly noise, and a ratchet whose population
// is noise lets a real regression hide inside churn.
//
// ── What this rail is, and is not ──
//
// It is a GROWTH ratchet at the measured population, following this repo's own doctrine (see
// `scripts/generate_docs_lint_baseline.py`: "SHIP AT THE MEASURED POPULATION, NOT AT ZERO. A
// never-run gate given teeth at zero is an outage").
//
// It still does not claim all 10 are live defects: the scan cannot tell whether an element is
// FOCUSABLE, and confirming each needs a browser Tab-walk reading the computed outline — that is
// `e2e:a11y`'s job. But all 10 are `<input>`/`<textarea>`-shaped controls in files with no focus
// treatment at all, so they are the set actually worth driving DOWN.
//
// The fix for a real one is a replacement treatment on the same element — `focus-visible:ring-2`,
// `focus:border-primary`, or simply deleting the `outline-none` and letting the global ring do its
// job.

const SRC = join(process.cwd(), 'src')

/** `outline-none` (Tailwind) or a hand-written `outline: none` / `outline: 0`. */
const OUTLINE_KILLED = /outline-none|outline:\s*none|outline:\s*0\b/

/** Any focus-state treatment that yields a visible indicator of its own. Deliberately WIDE — a
 *  false negative here would report a site as broken when a real ring exists, and this rail is
 *  about growth, so over-crediting is the safer error. */
const FOCUS_TREATMENT =
  /focus-visible|:focus-visible|focus-within|focus:ring|focus:border|focus:bg|focus:outline|ring-\d|ring-\[|ring-offset|box-shadow|focus:shadow|data-\[focus|has-\[/

/** The measured population on 2026-08-22, after the calibration above and with comments blanked.
 *  May only SHRINK — lower it in the same commit that removes a site. Raising it is how this rail
 *  stops guarding.
 *
 *  Both survivors are correct by design, and each is acknowledged in its own file:
 *    1. `pages/settings/DevicesPanel.tsx` — a `tabIndex={-1}` block focused PROGRAMMATICALLY so a
 *       screen reader announces the pairing code. Not a keyboard stop; a ring would mark a focus the
 *       user never initiated.
 *    2. `ui/RowHitTarget.tsx` — the ring is drawn by the PARENT via `has-[>button:focus-visible]`
 *       because this button sits at `-z-10`. Verified: all seven consumers do it. Its own code
 *       therefore carries no treatment, and once comments stop counting as credit (below) that is
 *       visible rather than hidden — which is the honest state for a cross-file contract this
 *       single-file scan cannot see. */
const BASELINE = 2

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(ts|tsx|css)$/.test(name)) out.push(p)
  }
  return out
}

/** Blank out comments, preserving line numbers so reported positions stay accurate.
 *
 *  Necessary, not defensive: this rail's first version scanned raw text, and the comment written to
 *  EXPLAIN the one legitimate `outline-none` mentioned the utility by name — so the scan counted the
 *  explanation as a site and reported 2 where the truth was 1. A gate that reds because someone
 *  DOCUMENTED the thing it guards trains people to delete the documentation.
 *
 *  Deliberately narrow, so it cannot eat code: block comments (which is also the JSX `{/* … *\/}`
 *  form) and whole-line `//` comments only. A trailing `//` is left alone precisely because
 *  `bg-[url(https://…)]` and similar would be mangled by a naive end-of-line rule — and a token
 *  hiding after code on the same line is not the failure mode this fixes.
 */
function withoutComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/^[ \t]*\/\/.*$/gm, (m) => ' '.repeat(m.length))
}

function unringedSites(): string[] {
  const hits: string[] = []
  for (const file of walk(SRC)) {
    const rel = relative(SRC, file).replace(/\\/g, '/')
    // `design/` is this rail's own home (its regexes would match themselves); tests are not shipped.
    if (rel.startsWith('design/') || rel.includes('.test.')) continue
    // Comments are blanked FIRST: both the detection and the credit must read code, not prose.
    const text = withoutComments(readFileSync(file, 'utf8'))
    if (!OUTLINE_KILLED.test(text)) continue
    // FILE-WIDE credit — see the calibration note above. A file that provides any focus treatment
    // is composing its ring somewhere (a constant, a wrapper, a `has-` variant on the parent), and
    // a line-window check cannot see that.
    if (FOCUS_TREATMENT.test(text)) continue
    text.split('\n').forEach((line, i) => {
      if (OUTLINE_KILLED.test(line)) hits.push(`${rel}:${i + 1}`)
    })
  }
  return hits
}

describe('the global focus ring must survive `outline-none`', () => {
  it('a comment that NAMES the utility is not counted as a site', () => {
    // The regression this guards: the first version of this rail scanned raw text, so the comment
    // written to explain the legitimate `DevicesPanel` case mentioned the utility and the scan
    // counted the explanation — reporting 2 where the truth was 1. A gate that reds because someone
    // documented the thing it guards teaches people to delete the documentation.
    const jsx = '{/* we deliberately set outline-none here */}\nconst a = 1\n'
    const line = '  // outline: none is fine on this one\nconst b = 2\n'
    expect(withoutComments(jsx)).not.toMatch(OUTLINE_KILLED)
    expect(withoutComments(line)).not.toMatch(OUTLINE_KILLED)
    // line numbers must survive, or reported positions would be wrong
    expect(withoutComments(jsx).split('\n').length).toBe(jsx.split('\n').length)
  })

  it('a trailing comment is left alone, so a URL cannot be mangled', () => {
    // `bg-[url(https://…)]` contains `//`; an end-of-line rule would eat the rest of a real line.
    const code = 'const u = "https://example.com/x" // outline-none\n'
    expect(withoutComments(code)).toContain('https://example.com/x')
  })

  it('the detector finds the population it is about (vacuity floor)', () => {
    // A regex that silently stopped matching would make the ratchet below vacuously green — the
    // failure mode every baseline in this repo carries a floor against.
    expect(
      unringedSites().length,
      'the scan found NO outline-none sites at all — the detector broke, it did not get clean',
    ).toBeGreaterThan(0)
  })

  it('tokens.css still provides the one global ring this rail protects', () => {
    // If the global rule is ever removed or renamed, `outline-none` stops being the interesting
    // signal and this rail is measuring the wrong thing.
    const tokens = readFileSync(join(SRC, 'design', 'tokens.css'), 'utf8')
    expect(tokens, 'the global :focus-visible ring is gone — re-derive this rail').toMatch(
      /:focus-visible\s*\{[^}]*outline:/,
    )
  })

  it(`no NEW element kills the focus ring without replacing it (baseline ${BASELINE})`, () => {
    const sites = unringedSites()
    expect(
      sites.length,
      `${sites.length} sites set outline-none in a file with NO focus treatment anywhere, ` +
        `above the baseline of ${BASELINE}. Add a replacement on the same element ` +
        `(focus-visible:ring-2, focus:border-primary) or drop the outline-none and let the global ` +
        `ring paint. New sites:\n  ${sites.slice(BASELINE).join('\n  ')}`,
    ).toBeLessThanOrEqual(BASELINE)
  })
})
