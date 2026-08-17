import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A snake_case identifier loses its meaning in the last few characters ──────────────────────────
//
// Measured at 390px on a populated home, `#/tools`:
//
//   `automation_delete_all`        162px of 164px   1.01x
//   `template_save_from_session`   184px of 203px   1.10x
//
// 🔑 1.01x SOUNDS LIKE NOTHING AND IS NOT. A tool name's distinguishing part is its TAIL:
// `automation_delete_all` against a hypothetical `automation_delete_one` differ only in the last word,
// which is precisely what a 2px overflow eats. The same argument the agent-row cycle made at 1.1x — and
// the reason the ratio alone is a bad classifier for this family.
//
// 🔑 THIS IS THE LAST UNBLOCKED SLICE OF THE TRUNCATION FAMILY. The app-wide census had 52 remaining
// hits; classified, they are: these identifiers, 37 long-prose clips on `#/prompts` (one component, one
// decision, blocked on the owner's prose-vs-layout ruling), the page/header labels that belong to the
// open header left-slot taste call, and singles of user prose. Nothing else is shippable without a
// ruling, which is why this cycle is small.
//
// 🪤 THE RATIO HEURISTIC MISCLASSIFIED THREE ROWS AND I ALMOST SHIPPED THEM. `#/prompts` has three hits
// at 1.29-1.5x that look identifier-shaped by ratio but are `· `-prefixed PROSE from the same component
// as its 34 long ones. Fixing 3 of 37 would have fragmented the family — the exact anti-pattern the
// coherence rules warn about. Read the TEXT, not the ratio.
//
// The server line is included though it did NOT clip with this seed: it is a URL or a full command line,
// the most tail-heavy string on the surface, and leaving it bare would ship a row whose name recovers
// and whose address does not. Stated rather than dressed up as a measurement.
//
// Nothing re-layouts — `title` is an attribute — so the captures are pixel-identical.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const strip = (s: string) => s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')

describe('a clipped tool identifier can still be read', () => {
  const PAGE = strip(read('pages/tools/ToolsPage.tsx'))

  it('the tool name carries its own value as a title', () => {
    expect(PAGE).toMatch(/className="truncate font-mono text-on-surface text-\[0\.8125rem\]" title=\{t\.name\}>\{t\.name\}<\/span>/)
  })

  it('the server name does too', () => {
    expect(PAGE).toMatch(/className="truncate font-mono text-on-surface text-\[0\.8125rem\]" title=\{s\.name\}>\{s\.name\}<\/span>/)
  })

  it('and the server address line, which is the most tail-heavy string here', () => {
    expect(PAGE).toMatch(/title=\{s\.url \|\| \[s\.command, \.\.\.\(s\.args \?\? \[\]\)\]\.join\(' '\)\}>\{s\.url \|\| \[s\.command, \.\.\.\(s\.args \?\? \[\]\)\]\.join\(' '\)\}<\/p>/)
  })

  it('every title is the rendered expression, not a paraphrase', () => {
    // A title that could drift would name a different tool than the row shows. The address line matters
    // most here: it is built from three fields, so a hand-written title would rot silently.
    for (const [what, expr] of [['tool', 't.name'], ['server', 's.name']] as const) {
      const e = expr.replace('.', '\\.')
      expect(new RegExp(`title=\\{${e}\\}>\\{${e}\\}<`).test(PAGE), `${what}`).toBe(true)
    }
    const addr = /title=\{(s\.url \|\| \[s\.command[^}]*)\}>\{(s\.url \|\| \[s\.command[^}]*)\}</.exec(PAGE)
    expect(addr, 'address title and text are the same expression').toBeTruthy()
    expect(addr?.[1]).toBe(addr?.[2])
  })

  it('all three still truncate — the fix is recovery, not re-layout', () => {
    expect(PAGE).toMatch(/className="truncate font-mono text-on-surface text-\[0\.8125rem\]"/)
    expect(PAGE).toMatch(/className="mt-0\.5 truncate font-mono text-on-surface-low text-\[0\.75rem\]"/)
  })

  it('the mono type survives — it is what makes these read as identifiers', () => {
    // If these ever stop being monospace they are being presented as prose, and the "identifier, so
    // `title` is right" reasoning above would need revisiting rather than silently passing.
    expect((PAGE.match(/truncate font-mono/g) || []).length, 'monospace truncating identifiers').toBeGreaterThanOrEqual(3)
  })
})
