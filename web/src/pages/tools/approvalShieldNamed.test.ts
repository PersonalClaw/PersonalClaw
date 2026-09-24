import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── The shield that says "this tool will ask first" has to say it out loud ────────────────────────
//
// `#/tools` marks `requires_approval` with a 12px warn-toned `ShieldAlert` and nothing else. Measured on
// the live tree against `demo-home`: **82 of them render, and 0 carried an accessible name** — no
// `aria-label`, no `title`, no `<title>` in the svg. So on 114 tools, the one fact about whether a tool
// stops to ask before acting was invisible to assistive tech.
//
// 🔑 THE INCONSISTENCY WAS ON THE SAME LINE. Its neighbour `RiskBadge` states its dimension in VISIBLE
// text (`Caution` / `Destructive` — 41 rendered) and carries `title={`Risk: …`}`. One of the two
// dimensions on that row was named and the other was not, which is why this is drift rather than a
// judgement call about how much a dense row should say.
//
// 🔑 AND THE FORM IS SETTLED, four sites over. An informational lucide glyph is named DIRECTLY:
//   · `settings/AuditPanel:296`  — this same `ShieldAlert`, `aria-label="Integrity check failed …"`
//   · `skills/SkillsPage:161`    — `role="img"` + `aria-label="Integrity verified"`
//   · `projects/ProjectsSection` — `Star` "Active project", `Lock` "Name locked"
//   · `knowledge/KnowledgeListPage:741` — `Star` "Favorite"
// `role="img"` is included because `design/ariaProhibitedAttr.test.ts` declares it for "a graphic whose
// label is its only text", and the `title` gives sighted users the hover the glyph cannot.
//
// 🪤 NOT A VISIBLE BADGE, deliberately. The row already truncates the tool NAME — a font-mono identifier
// whose tail is the distinguishing part (`automation_delete_all` vs `automation_delete_one`) — so a
// second chip beside `RiskBadge` would take width from the thing that must not lose characters. Same
// call, and the same reasoning, as the agent-row counts.

const SRC = join(import.meta.dirname, '..', '..')
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

/** Sites this sweep found that are REAL but belong to another concern, named so the exemption is a
 *  judgement on record rather than a silent narrowing of scope (the pattern `emptyStateRollout.test.tsx`
 *  uses). Both are queued; neither is fixed here, because one concern per change:
 *
 *   · ~~`projects/ProjectsSection.tsx`~~ — **FIXED, exemption removed.** The card was not merely unnamed:
 *     it hand-rolled a rival status table that disagreed with `taskMeta.statusMeta` on three of five
 *     statuses (`in_progress` coral instead of info, `blocked` the wrong icon, `cancelled` unhandled and
 *     rendering as "not started"). Now reads the canonical map, which names the glyph as a side effect.
 *     See `projects/taskStatusFromCanonicalMap.test.ts`.
 *   · ~~`settings/settingsUI.tsx`~~ — **FIXED, exemption removed.** `{danger && on && <AlertTriangle/>}`
 *     now carries `role="img" aria-label="Relaxes a safety default"` — the prop's own doc sentence, so the
 *     label and the contract cannot drift. See `settings/dangerToggleGlyphNamed.test.tsx`.
 *
 *  🔑 THE SET IS NOW EMPTY, which is the point: every site this sweep found is named. An empty exemption
 *  list is the only honest end state for a census — the ceiling below is 0 and may not rise.
 *
 *  🪤 Keep this set SMALL and dated. An exemption list that grows is how a sweep stops being one. */
const RECORDED_NOT_FIXED = new Set<string>([])

describe('the approval shield names itself', () => {
  const src = read('pages/tools/ToolsPage.tsx')

  it('reads the real file (not vacuously green)', () => {
    expect(src, 'the tools page moved — this rail measures nothing').toMatch(/function RiskBadge\(/)
    expect(src, 'the approval flag must still be rendered to be asserted about').toMatch(/t\.requires_approval &&/)
    expect(src.length).toBeGreaterThan(4000)
  })

  it('the requires_approval glyph carries a name AND the role that makes it stick', () => {
    const at = src.indexOf('t.requires_approval &&')
    const badge = src.slice(at, src.indexOf('<RiskBadge', at))
    expect(badge.length, 'empty slice — vacuous').toBeGreaterThan(30)
    expect(badge, 'the glyph must be named').toMatch(/aria-label="[^"]{12,}"/)
    expect(badge, 'role="img" — a graphic whose label is its only text').toMatch(/role="img"/)
    // No `title` assertion: lucide's `LucideProps` rejects the prop (TS2322), and all six precedent
    // sites in this tree carry `aria-label` alone or with `role`. Asserting a `title` here would demand
    // a wrapper none of them use.
  })

  it('the name says what happens, not what the field is called', () => {
    // "requires_approval" is the flag; a user needs the behaviour. The wording follows the composer's
    // own permission copy ("ask before every tool") rather than inventing a third vocabulary.
    const at = src.indexOf('t.requires_approval &&')
    const badge = src.slice(at, src.indexOf('<RiskBadge', at))
    const label = /aria-label="([^"]+)"/.exec(badge)?.[1] ?? ''
    expect(label.toLowerCase(), `"${label}" should describe asking, not the flag name`).toMatch(/ask/)
    expect(label, 'and must not leak the snake_case field name').not.toMatch(/requires_approval/)
  })

  it('RiskBadge still states its own dimension in visible text', () => {
    // The comparison this fix rests on. If RiskBadge ever went glyph-only too, the argument above
    // ("one dimension named, the other mute") would no longer describe the row.
    // 🪤 BOUNDED TO RiskBadge'S OWN BODY. Slicing to end-of-file let `{label}` match somewhere further
    // down the module, so deleting RiskBadge's visible label left this GREEN — mutation caught it. A
    // window that is too wide does not fail loudly; it answers about the wrong code.
    const at = src.indexOf('function RiskBadge(')
    expect(at, 'RiskBadge moved — this rail measures nothing').toBeGreaterThan(-1)
    const badge = src.slice(at, src.indexOf('\n}', at))
    expect(badge.length, 'empty slice — vacuous').toBeGreaterThan(80)
    // 🪤 ANCHORED TO THE JSX CHILD POSITION. `/\{label\}/` alone also matches the `{label}` INSIDE
    // `title={`Risk: ${label}`}` — a superstring — so deleting the visible child left it green through
    // TWO mutation rounds. The label must sit between the tag and its close.
    expect(badge, 'the risk tier is visible text, not a glyph').toMatch(/\{label\}\s*<\/span>/)
    expect(badge, 'and it names its dimension on hover').toMatch(/title=\{`Risk: \$\{label\}`\}/)
  })

  it('no informational warn/danger glyph in pages/ is left unnamed beside a named sibling', () => {
    // The derived half: a `Shield*`/`Alert*` glyph that is the sole carrier of a fact must be named.
    // Scoped to warn/danger tones, because those are the ones asserting something a user must act on;
    // a decorative glyph next to its own sentence is fine and is excluded by the adjacent-text check.
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
      })
    const offenders: string[] = []
    for (const abs of walk(join(SRC, 'pages'))) {
      const text = strip(readFileSync(abs, 'utf8'))
      for (const m of text.matchAll(/<(Shield\w+|AlertTriangle|TriangleAlert)\b([^>]*)\/>/g)) {
        const attrs = m[2]
        if (!/text-(warn|danger)|--color-(warn|danger)/.test(attrs)) continue
        // 🪤 `aria-hidden` in JSX is usually the BOOLEAN shorthand — no `=`. Requiring `aria-hidden=`
        // flagged three already-correct sites (`ToolCard`, `CompanionPage`, `ConflictPanel`).
        if (/aria-label=/.test(attrs) || /\baria-hidden\b/.test(attrs)) continue
        // A glyph immediately followed by visible content is decorative — the sentence carries it.
        // 🪤 The allowlist has to be ANY element or expression, not a hand-picked few: my first draft
        // listed `<span|<p|{` and flagged `ContributedPage`, whose glyph is followed by a `<div>`. It
        // also has to tolerate the JSX punctuation a conditional leaves behind — a `}` closing an
        // `{x && <Glyph/>}` and the `:` of a ternary arm both sit between the tag and the text.
        const after = text.slice(m.index! + m[0].length, m.index! + m[0].length + 160)
        if (/^[\s}:)]*(<[a-zA-Z]|\{)/.test(after)) continue
        const rel = abs.slice(abs.indexOf('/pages/') + 7)
        if (RECORDED_NOT_FIXED.has(`${rel}: <${m[1]}>`)) continue
        offenders.push(`${rel}: <${m[1]}>`)
      }
    }
    expect(
      offenders,
      'these warn/danger glyphs carry a fact with no accessible name and no adjacent sentence. ' +
        'Name them (aria-label + role="img") or mark them aria-hidden if the text beside them says it:\n  ' +
        offenders.join('\n  '),
    ).toEqual([])
  })

  it('every recorded-not-fixed exemption still exists and is still unnamed', () => {
    // An exemption for a site that has since been FIXED would silently keep the sweep narrower than it
    // needs to be — the same staleness that let the triggers census fence outlive TSE-4.
    for (const entry of RECORDED_NOT_FIXED) {
      const [rel, glyph] = entry.split(': ')
      const text = strip(readFileSync(join(SRC, 'pages', rel), 'utf8'))
      const tag = glyph.replace(/[<>]/g, '')
      const found = [...text.matchAll(new RegExp(`<${tag}\\b([^>]*)\\/>`, 'g'))]
        .some((m) => /text-(warn|danger)|--color-(warn|danger)/.test(m[1]) && !/aria-label=|\baria-hidden\b/.test(m[1]))
      expect(found, `${entry} is exempted but no longer matches — drop the exemption`).toBe(true)
    }
    // Ratcheted down as each was fixed: 2 → 1 → 0. It may not rise.
    expect(RECORDED_NOT_FIXED.size, 'the exemption list should shrink, never grow').toBe(0)
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
