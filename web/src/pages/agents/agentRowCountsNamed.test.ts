import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── Three bare integers, and an 11px glyph deciding which is which ───────────────────────────────
//
// A native agent row ends with up to three counts: skills, tools, triggers. Each was
// `<Sparkles size={11} /> {n}` — a glyph and a number, with **no `title`, no `aria-label`, and no
// `<title>` in the svg**. Measured on the live DOM against `demo-home`: `personalclaw-loop` exposed
// `"1"` and `personalclaw-template-refiner` exposed `"2"`, and nothing said what was being counted.
// One skill and one tool are indistinguishable.
//
// 🔑 WHY `role="img"` AND NOT A VISIBLE NOUN. Both forms exist in this tree, and the census says which
// belongs here. Seven sites put the noun in visible text — `{items.length} queued`,
// `{bottlenecks.length} bottleneck{s}`, `{plan.length} stage{s} planned`, `{activity.length} steps`,
// `{proposals.length} name{s} to decide on`, … — all of them in a row with room to spare. The one site
// shaped like THIS one is `settings/FeedbackPanel`: two bare counts side by side in a dense row, a 10px
// ThumbsUp/ThumbsDown as the only carrier, fixed with `role="img"` + a label and its rail stating "the
// visible text is untouched: this is an accessibility-tree fix and the captures are identical". The
// agents cluster is `hidden sm:flex shrink-0` with up to three counts abreast, so it is that shape.
//
// 🪤 AND THE ROLE IS LOAD-BEARING, NOT BOILERPLATE. `settings/ModelsPanel` records it on its breaker
// dot: **on a role-less `<span>`, `aria-label` is a PROHIBITED attribute and the name is DISCARDED.**
// So labelling these without `role="img"` reads as a complete fix in the diff and changes nothing in
// the accessibility tree — which is why this rail asserts the pair, never the label alone.
//
// 🪤 SOURCE-LEVEL BECAUSE THE ROW IS NOT EXPORTED. `NativeRow` is a local function, so there is no
// component to render — the same reason `ui/danglingSeparator.test.ts` reads this file as text. The
// live DOM reading above is the evidence and lives in the PR.

const SRC = join(import.meta.dirname, '..', '..')
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

const COUNTS = [
  { field: 'skills', glyph: 'Sparkles', noun: 'skill' },
  { field: 'tools', glyph: 'Wrench', noun: 'tool' },
  { field: 'triggers', glyph: 'Zap', noun: 'trigger' },
] as const

describe('every count in an agent row says what it counts', () => {
  const src = read('pages/agents/AgentsListPage.tsx')

  it('reads the real file (not vacuously green)', () => {
    expect(src, 'the row component moved — this rail measures nothing').toMatch(/function NativeRow\(/)
    expect(src.length).toBeGreaterThan(4000)
    for (const c of COUNTS) {
      expect(src, `the ${c.field} badge must still exist to be asserted about`).toMatch(
        new RegExp(`agent\\.${c.field}!\\.length`),
      )
    }
  })

  it.each(COUNTS)('the $field count carries role="img" AND a label naming it', ({ field, noun }) => {
    // One slice per badge, so a label landing on the wrong count is caught rather than averaged away.
    const at = src.indexOf(`agent.${field}?.length`)
    expect(at, `the ${field} badge was not found`).toBeGreaterThan(-1)
    const badge = src.slice(at, src.indexOf('</span>', at))
    expect(badge, `${field}: role="img" is what makes aria-label legal on a span`).toMatch(/role="img"/)
    expect(badge, `${field}: the label must name the dimension, not just repeat the number`).toMatch(
      new RegExp(`aria-label=\\{\`\\$\\{agent\\.${field}!\\.length\\} ${noun}`),
    )
  })

  it('and it pluralises, because "1 skills" is the tell that a label was pasted', () => {
    for (const { field, noun } of COUNTS) {
      const at = src.indexOf(`agent.${field}?.length`)
      const badge = src.slice(at, src.indexOf('</span>', at))
      expect(badge, `${field}: singular/plural must follow the count`).toMatch(
        new RegExp(`${noun}\\$\\{agent\\.${field}!\\.length === 1 \\? '' : 's'\\}`),
      )
    }
  })

  it('no glyph+bare-count badge is left unnamed anywhere in pages/', () => {
    // The derived half. Every OTHER site in the tree states its noun in visible text; this catches a
    // new bare-count badge appearing without either treatment, rather than trusting the three above to
    // stay the whole population.
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
      })
    const offenders: string[] = []
    for (const abs of walk(join(SRC, 'pages'))) {
      const text = strip(readFileSync(abs, 'utf8'))
      for (const m of text.matchAll(/<span([^>]*)>\s*<[A-Z]\w+ size=\{1[0-4]\}[^>]*\/>\s*\{([a-zA-Z_.!?]+\.length)\}\s*<\/span>/g)) {
        if (!/role="img"/.test(m[1])) {
          offenders.push(`${abs.slice(abs.indexOf('/pages/') + 7)}: {${m[2]}}`)
        }
      }
    }
    expect(
      offenders,
      'a glyph and a bare count with no noun — visible or accessible. Either state the noun in text ' +
        '(the seven-site majority) or use role="img" + aria-label (FeedbackPanel\'s dense-row form):\n  ' +
        offenders.join('\n  '),
    ).toEqual([])
  })

  it('the pages sweep reads a real tree (vacuity floor)', () => {
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
      })
    expect(walk(join(SRC, 'pages')).length, 'the pages sweep found nothing').toBeGreaterThan(60)
  })
})
