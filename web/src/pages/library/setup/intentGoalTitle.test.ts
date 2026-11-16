// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { join } from 'node:path'
import { filesUnder, readSource } from '../../../test/sourceTree'

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
// The old Knowledge list's intents view is the library's set-up panel now (#6101), where an intent's
// row is named by its goal.
const SRC = readSource(join(SRC_DIR, 'pages/library/setup/SetupPanel.tsx'))
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

function walk(dir: string): string[] {
  return filesUnder(dir, (name) => /\.tsx$/.test(name) && !name.includes('.test.'))
}

describe("an intent's goal is drawn whole", () => {
  it("🔑 the set-up row's name wraps rather than truncating", () => {
    // The fix the old list needed (a title on a truncating label) is not needed here: the row's name
    // is a paragraph that wraps, so a sighted phone user reads the whole sentence.
    expect(CODE).toMatch(/<p data-type="body-m" className="text-on-surface">\{name\}<\/p>/)
  })

  it('the row hands its intent the whole goal as its name', () => {
    expect(CODE).toMatch(/<SetupRow name=\{goal\}/)
    expect(CODE).toMatch(/const goal = intent\.goal \|\| 'An intent with no goal'/)
  })

  it('and its controls name themselves from it', () => {
    expect(CODE, 'the switch').toMatch(/label=\{`\$\{toggle\.words\}: \$\{name\}`\}/)
    expect(CODE, 'the ⋯').toMatch(/label=\{`More for \$\{name\}`\}/)
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
      for (const m of readSource(abs).matchAll(tag)) {
        if (/title=|aria-label/.test(m[1])) withTitle++
        else without++
      }
    }
    expect(withTitle, 'the idiom is real and in use').toBeGreaterThanOrEqual(15)
    expect(without, 'and the untitled population is large enough to need its own measured pass')
      .toBeGreaterThan(100)
  })
})
