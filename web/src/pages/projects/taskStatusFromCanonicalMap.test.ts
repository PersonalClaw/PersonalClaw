import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { STATUSES, statusMeta, TERMINAL } from '../tasks/taskMeta'

// ── A task's status has one owner, and hand-rolling it gets it wrong ─────────────────────────────
//
// `taskMeta.statusMeta()` owns status: **key, label, icon and tone together**, read by 13 files. The
// project card hand-rolled a four-branch ternary instead, and it disagreed with the canonical map on
// THREE of the five statuses:
//
//     in_progress   CircleDot `text-primary`   canonical tone is `--color-info`. Coral is the
//                                              primary/active colour, so this spent it categorically.
//     blocked       AlertTriangle              canonical icon is `CircleSlash`.
//     cancelled     NOT HANDLED — fell to the `else` and rendered as an open circle, so a cancelled
//                                              task looked NOT STARTED. `demo-home` ships one, so this
//                                              was reachable rather than theoretical.
//
// And all four branches were unnamed, so status was carried by an unnamed 14px glyph.
//
// 🪤 THE STRIKE-THROUGH IS PART OF THE SAME DEFECT. It keyed on `t.status === 'done'` while `TERMINAL`
// is `{done, cancelled}` and the sibling list uses `TERMINAL` for grouping and sorting. So a cancelled
// task's TEXT read as active while its icon said otherwise — two halves of one row disagreeing.
//
// This rail asserts the AGREEMENT and the absence of a rival, not a list of icon names: pinning
// `CircleSlash` here would put a fourth copy of the mapping in the tree, which is the defect.

const SRC = join(import.meta.dirname, '..', '..')
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
  })

describe('the project card reads task status from taskMeta', () => {
  const src = read('pages/projects/ProjectsSection.tsx')

  it('reads the real file (not vacuously green)', () => {
    expect(src, 'the task list moved — this rail measures nothing').toMatch(/onOpenTask\(t\.id\)/)
    expect(src.length).toBeGreaterThan(4000)
  })

  it('uses statusMeta rather than its own branch table', () => {
    expect(src, 'the canonical helper must be imported').toMatch(
      /import \{[^}]*\bstatusMeta\b[^}]*\} from '\.\.\/tasks\/taskMeta'/,
    )
    expect(src, 'and called for the row glyph').toMatch(/statusMeta\(t\.status\)/)
  })

  it('names the glyph with the canonical label', () => {
    const at = src.indexOf('statusMeta(t.status)')
    const block = src.slice(at, src.indexOf('</span>', at))
    expect(block.length, 'empty slice — vacuous').toBeGreaterThan(60)
    expect(block, 'role="img" — a graphic whose label is its only text').toMatch(/role="img"/)
    expect(block, "the label must be statusMeta's, not a local string").toMatch(/aria-label=\{sm\.label\}/)
    expect(block, "and the tone must be statusMeta's").toMatch(/color: sm\.tone/)
  })

  it('the strike-through follows TERMINAL, not just done', () => {
    // `=== 'done'` left a cancelled task's text looking active. Asserted because the two halves of the
    // row have to agree about what "finished" means.
    expect(src, 'terminal statuses are struck through').toMatch(/TERMINAL\.has\(t\.status\)/)
    const at = src.indexOf('statusMeta(t.status)')
    const after = src.slice(at, at + 900)
    expect(after, "the row must not re-derive 'finished' from one status").not.toMatch(
      /t\.status === 'done' \? 'text-on-surface-low line-through'/,
    )
  })

  it('no file outside taskMeta keeps a rival status branch table', () => {
    // The derived half: a ternary chain mapping three or more status KEYS to icons is a second source
    // of truth, which is exactly the shape this fixed. Keyed on the status literals, since that is what
    // makes it a status table rather than an unrelated conditional.
    const offenders: string[] = []
    for (const abs of walk(join(SRC, 'pages'))) {
      if (abs.endsWith('taskMeta.tsx')) continue
      const text = strip(readFileSync(abs, 'utf8'))
      // Three or more `status === '<key>' ?` arms within one expression window.
      for (const m of text.matchAll(/status === '(?:open|in_progress|blocked|done|cancelled)'[\s\S]{0,900}?/g)) {
        const window = text.slice(m.index!, m.index! + 900)
        const arms = [...window.matchAll(/status === '(?:open|in_progress|blocked|done|cancelled)'\s*\?/g)].length
        const icons = [...window.matchAll(/<(?:Circle\w*|CheckCircle2|XCircle|AlertTriangle)\b/g)].length
        if (arms >= 3 && icons >= 3) {
          offenders.push(`${abs.slice(abs.indexOf('/pages/') + 7)} (${arms} arms, ${icons} icons)`)
          break
        }
      }
    }
    expect(
      offenders,
      'these map status keys to icons themselves. `taskMeta.statusMeta` owns key+label+icon+tone — ' +
        'import it instead of re-deriving it:\n  ' + offenders.join('\n  '),
    ).toEqual([])
  })

  it('the sweep reads a real tree (vacuity floor)', () => {
    expect(walk(join(SRC, 'pages')).length, 'the pages sweep found nothing').toBeGreaterThan(60)
  })
})

describe('the canonical map still says what this fix relied on', () => {
  // If `taskMeta` ever changed these, the reasoning above would silently stop applying — so the three
  // divergences the fix corrected are pinned at their SOURCE rather than restated in the project card.
  it('in_progress is info-toned, not primary', () => {
    expect(statusMeta('in_progress').tone).toBe('var(--color-info)')
  })

  it('blocked and cancelled each have their own icon', () => {
    const names = new Set(STATUSES.map((s) => s.icon.displayName ?? s.icon.name))
    expect(names.size, 'five statuses must not share icons').toBeGreaterThanOrEqual(4)
    expect(statusMeta('blocked').icon).not.toBe(statusMeta('open').icon)
    expect(statusMeta('cancelled').icon).not.toBe(statusMeta('open').icon)
  })

  it('cancelled resolves to a real entry, so it can never fall through to a default again', () => {
    expect(statusMeta('cancelled').label).not.toBe('Unknown')
    expect(STATUSES.map((s) => s.key)).toContain('cancelled')
  })

  it('TERMINAL is both finished states', () => {
    expect([...TERMINAL].sort()).toEqual(['cancelled', 'done'])
  })
})
