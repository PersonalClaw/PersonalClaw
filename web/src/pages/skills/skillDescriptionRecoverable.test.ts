import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A skill's description is what tells you what the skill DOES, and it was unreachable ───────────
//
// Measured at 390px on a populated home, the same descriptions clipped on two different surfaces:
//
//   #/skills      `mt-0.5 truncate text-on-surface-low`   **192px of up to 3217px**   — 16.8x over
//   #/agents/new  `block truncate text-on-surface-low`    **310px of up to 2977px**   — 9.6x over
//
// 14 rows on each. A phone user saw roughly the first six words of a sentence written specifically to
// explain the skill, with no way to read the rest — and this is a *choosing* surface: `#/agents/new` is
// where you tick which skills an agent gets, so the clipped text is the only basis for the decision.
//
// 🔑 TWO SURFACES, ONE CONCEPT. `#/skills` renders it from `SkillsPage`'s list row; `#/agents/new`
// renders it as `o.hint` through `AgentForm`'s GENERIC multi-select picker, so that half fixes every
// picker built on it rather than the skills case alone.
//
// 🔑 THE RECOVERY IDIOM WAS ALREADY SETTLED — a `title` on the truncating element, 19 sites, and the
// same row already used it for its own `always` and `tampered` chips. Nothing re-layouts: `title` is an
// attribute, so the captures are pixel-identical, which is the expected result rather than a missing
// screenshot.
//
// 🪤 THE LABEL IS INCLUDED BUT WAS NOT MEASURED CLIPPED. `AgentForm`'s `o.label` truncates one line
// above the hint and did NOT clip with this data. It is in because leaving it bare would ship a row
// whose subtitle recovers and whose title does not — but the claim is stated honestly rather than
// dressed up as a measurement.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const strip = (s: string) => s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')

describe('a clipped skill description can still be read', () => {
  const SKILLS = strip(read('pages/skills/SkillsPage.tsx'))
  const FORM = strip(read('pages/agents/AgentForm.tsx'))

  it('the skills list row hands over the whole description', () => {
    expect(SKILLS).toMatch(/className="mt-0\.5 truncate text-on-surface-low text-\[0\.8125rem\]" title=\{s\.description\}>\{s\.description\}<\/p>/)
  })

  it("the agent form's picker hands over both its texts", () => {
    expect(FORM).toMatch(/className="truncate text-on-surface text-\[0\.8125rem\]" title=\{o\.label\}>\{o\.label\}<\/span>/)
    expect(FORM).toMatch(/className="block truncate text-on-surface-low text-\[0\.75rem\]" title=\{o\.hint\}>\{o\.hint\}<\/span>/)
  })

  it('each title is the rendered value, not a paraphrase', () => {
    // A title that could drift would describe a different skill than the row shows.
    for (const [name, src, expr] of [
      ['skills', SKILLS, 's.description'],
      ['form label', FORM, 'o.label'],
      ['form hint', FORM, 'o.hint'],
    ] as const) {
      const re = new RegExp(`title=\\{${expr.replace('.', '\\.')}\\}>\\{${expr.replace('.', '\\.')}\\}<`)
      expect(re.test(src), `${name}: title and text are one expression`).toBe(true)
    }
  })

  it('all three still truncate — the fix is recovery, not re-layout', () => {
    // If one stops truncating, its `title` becomes noise and this rail should be revisited.
    expect(SKILLS).toMatch(/mt-0\.5 truncate text-on-surface-low/)
    expect(FORM).toMatch(/className="truncate text-on-surface text-\[0\.8125rem\]"/)
    expect(FORM).toMatch(/className="block truncate text-on-surface-low/)
  })

  it('the picker is generic, so the fix is not skills-only — the leverage claim', () => {
    // The vacuity floor for "every picker built on it". If `AgentForm`'s row stops being driven by an
    // options array, the leverage argument in the header is stale.
    expect(FORM).toMatch(/\{o\.hint &&/)
    expect(FORM, 'driven by an options list').toMatch(/options\.map\(|options\.length/)
  })

  it('the row keeps the titles it already had on its status chips', () => {
    // Those are the local precedent this change converges onto; if they vanish, the "settled idiom"
    // reasoning needs rewriting rather than silently passing.
    expect(SKILLS).toMatch(/title="Always loaded"/)
    expect(SKILLS).toMatch(/title="Integrity check failed — files changed since install"/)
  })
})
