import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── An intent's goal is a sentence the user wrote; `truncate` was eating most of it ────────────
//
// Measured in Chromium at 390px on four real intents. Two truncate, and the longest showed **233px of
// the 651px it needs — 36% of what the user typed**:
//
//   "anything that could improve my homelab, especially storage reliability and backup verification"
//                                                                233 / 651px, title: null
//   "ideas that help me learn agentic engineering"                163 / 308px, title: null
//
// Nothing truncates at 1440px, so a desktop-only sweep sees nothing — and `ux-audit` reported 0
// blocking findings at both themes AND at 390px, because a truncated label is valid, contrasty markup.
//
// 🔑 THE SAME ASYMMETRY THE TAG ROW HAD. `ListRow`'s `label` already carries the FULL goal, so
// assistive tech was the only reader getting the whole sentence while a sighted phone user got a
// third of it. That is the second surface with this shape, so the fix is the app's existing idiom
// rather than a new one.
//
// 🔑 AND IT IS DELIBERATELY NOT A SWEEP. A census of the whole tree found **237 truncating elements
// without a title against 19 with**. Blanket-adding tooltips to 237 sites would be unreviewable, and
// most of them never overflow with real data or sit beside a full copy of the same text. The right
// first step there is measurement — which sites actually clip at 390px with a populated home — not
// editing. This rail therefore pins THIS site and the population count, so the next cycle starts from
// a number rather than a hunch.

const SRC_DIR = join(process.cwd(), 'src')
const SRC = readFileSync(join(SRC_DIR, 'pages/knowledge/KnowledgeListPage.tsx'), 'utf8')
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) walk(abs, out)
    else if (/\.tsx$/.test(name) && !name.includes('.test.')) out.push(abs)
  }
  return out
}

describe("an intent's goal survives truncation", () => {
  it('the truncating goal carries its full text in a title', () => {
    expect(CODE).toMatch(
      /<span className="truncate text-on-surface text-\[0\.9375rem\]" title=\{it\.goal \|\| it\.id\}>\{it\.goal \|\| it\.id\}<\/span>/,
    )
  })

  it('the title and the visible text are the same expression', () => {
    // A title that could drift from the label is worse than none: it would describe a different value.
    const m = /title=\{([^}]+)\}>\{([^}]+)\}<\/span>/.exec(CODE)
    expect(m, 'the pair is readable from source').toBeTruthy()
    expect(m![1].trim(), 'title matches the rendered expression').toBe(m![2].trim())
  })

  it('the row still hands the FULL goal to assistive tech — the half that already worked', () => {
    // The fix closes a gap for sighted users; it must not disturb the path that was already correct.
    expect(CODE).toMatch(/<ListRow[\s\S]{0,200}label=\{it\.goal \|\| it\.id\}/)
  })

  it('and the delete control still names itself from the capped goal', () => {
    // cycle 142's rule — a sentence-long goal must not become a paragraph-long button name.
    expect(CODE).toMatch(/ariaLabel=\{`Delete intent: \$\{rowSubject\(\[it\.goal \|\| it\.id\], 40\)\}`\}/)
  })
})

describe('the population this deliberately does NOT sweep', () => {
  it('is counted, so the next cycle starts from a number', () => {
    // 237 without / 19 with at the time of writing. The assertion is loose on purpose: it exists to
    // prove the pattern is systemic (and therefore its own cycle), not to freeze a count that every
    // unrelated PR would bump.
    const tag = /<(?:span|div|p|h[1-6])\b([^>]*\bclassName="[^"]*\btruncate\b[^"]*"[^>]*)>/g
    let withTitle = 0, without = 0
    for (const abs of walk(SRC_DIR)) {
      for (const m of readFileSync(abs, 'utf8').matchAll(tag)) {
        if (/title=|aria-label/.test(m[1])) withTitle++
        else without++
      }
    }
    expect(withTitle, 'the idiom is real and in use').toBeGreaterThanOrEqual(15)
    expect(without, 'and the untitled population is large enough to need its own measured pass')
      .toBeGreaterThan(100)
  })
})
