import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { RowAction } from '../pages/dashboard/widgets/kit'

// ── A control that acts on ONE row has to name that row ────────────────────────────────────────
//
// Censused every strictly-visible control on 17 routes and grouped them by accessible name, keeping
// only groups whose members act on DIFFERENT rows. What came back:
//
//   #/dashboard   8× "Reply" · 8× "Dismiss" · 6× "Mark complete"
//   #/tasks      30× "Select task"
//   #/projects    3× "Delete project"
//
// Each of those acts on a different item, so a screen-reader user listing the controls hears the same
// two or three words repeated with nothing to choose between them (WCAG 4.1.2). The visible label is
// right as it is — on screen the subject is the row you are looking at — so the fix is the accessible
// NAME only, composed the way the kit already composes it elsewhere: `ui/Toaster`
// (`Dismiss: ${message}`), `ui/forms` (`Remove ${value}`), `ui/WidthPill` (`Content width: ${label}`),
// FileTree / AppsSection (`Actions for ${name}`). Drift, not a taste call — the convention exists.
//
// 🪤 A `title` IS NOT THE NAME WHEN THE BUTTON HAS TEXT. `RowAction`'s Reply button already carried
// `title="Open to reply"`, and it still announced "Reply": the text content wins. Two of these needed
// `aria-label` precisely because the verb was already visible; only the icon-only ones (`Dismiss`,
// `Reject`, `Unpin`, `Mark complete`) were named by their title at all.
//
// 🔑 WHAT IS DELIBERATELY LEFT ALONE, so a later pass does not "finish" it:
//   • SINGLETON actions — `SystemHealth`'s doctor/update buttons, `ActiveWork`'s Send inside the open
//     composer. One per widget, no sibling to be confused with.
//   • REPEATED CHIPS WITH ONE TARGET — `#/knowledge`'s 11× "draid" tag links, `#/tasks`' 8× project
//     chips, `#/inbox`'s 35× "Proposals" deep links. Same name AND same destination, so the repetition
//     carries no ambiguity; naming them per-row would add noise, not information.
//
// 🪤 THE CENSUS UNDERCOUNTS, AND THAT IS WHY THIS RAIL IS SOURCE-LEVEL TOO. Its "different rows" test
// keys on the nearest `li,tr,[rounded-*]` ancestor's first 40 characters, which collapsed `#/projects`'
// three "Delete project" buttons into one group (their rows share a prefix) — the finding came from a
// separate per-route dump. A DOM census is a lead generator, not a gate.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

describe('RowAction carries an explicit accessible name', () => {
  it('renders the composed name, and the visible verb stays short', () => {
    render(<RowAction onClick={() => {}} title="Open to reply" ariaLabel="Reply: Skill: refine-a-skill">Reply</RowAction>)
    const b = screen.getByRole('button', { name: 'Reply: Skill: refine-a-skill' })
    expect(b.textContent, 'the label a sighted user reads is unchanged').toBe('Reply')
    expect(b.getAttribute('title'), 'the tooltip stays the short hint').toBe('Open to reply')
  })

  it('without it, the button falls back to its text — the defect being fixed', () => {
    render(<RowAction onClick={() => {}} title="Open to reply">Reply</RowAction>)
    // Proof that `title` does not win over text content: this is why aria-label was needed.
    expect(screen.getByRole('button', { name: 'Reply' })).toBeTruthy()
  })
})

describe('every row-scoped RowAction names its row', () => {
  /** [file, how many call sites must pass ariaLabel, how many singletons may not] */
  const WIDGETS: [string, number, number][] = [
    ['pages/dashboard/widgets/ActionCenter.tsx', 4, 0],
    ['pages/dashboard/widgets/TasksWidget.tsx', 1, 0],
    ['pages/dashboard/widgets/PinnedArtifacts.tsx', 1, 0],
    ['pages/dashboard/widgets/ActiveWork.tsx', 2, 1],   // the composer's Send is a singleton
    ['pages/dashboard/widgets/SystemHealth.tsx', 0, 2], // doctor + update: one each, no rows
  ]

  for (const [rel, named, singletons] of WIDGETS) {
    it(`${rel.split('/').pop()} names ${named} and leaves ${singletons} singleton(s)`, () => {
      const code = read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      const sites = [...code.matchAll(/<RowAction\b/g)]
      expect(sites.length, `${rel} call sites`).toBe(named + singletons)
      const withName = [...code.matchAll(/ariaLabel=\{`/g)]
      expect(withName.length, `${rel} must compose ${named} name(s) from the row`).toBe(named)
    })
  }

  it('every composed name interpolates a subject, never a bare verb', () => {
    // `ariaLabel="Dismiss"` would satisfy a "passes ariaLabel" check and fix nothing.
    for (const [rel] of WIDGETS) {
      for (const m of read(rel).matchAll(/ariaLabel=\{`([^`]*)`\}/g)) {
        // "verb: subject", where the SUBJECT is the interpolation at the end. The verb may itself be
        // computed — ActionCenter's is `${kind === 'approval' ? 'Approve' : 'Accept'}` — so anchoring
        // on a literal prefix would fail a correct name (it did, first run).
        expect(m[1], `${rel}: ${m[1]} must end in the row's subject`).toMatch(/: \$\{[^}]+\}$/)
      }
    }
  })

  it('no OTHER RowAction call site appears without a name — the census is closed', () => {
    const files = walk(SRC).filter((abs) => readFileSync(abs, 'utf8').includes('<RowAction'))
    expect(files.length, 'widgets using RowAction').toBe(WIDGETS.length)
  })
})

describe('the two list surfaces name their row controls too', () => {
  it("#/tasks' selection checkbox says WHICH task", () => {
    const code = read('pages/tasks/TasksListPage.tsx')
    expect(code).toMatch(/aria-label=\{`\$\{selected \? 'Deselect' : 'Select'\}: \$\{t\.title\}`\}/)
    expect(code, 'the old shared name must be gone').not.toMatch(/'Deselect task' : 'Select task'/)
  })

  it("#/projects' delete button says WHICH project, and keeps a short tooltip", () => {
    const code = read('pages/projects/ProjectsSection.tsx')
    expect(code).toMatch(/label=\{`Delete project: \$\{p\.name\}`\} title="Delete project"/)
  })

  it('the repeated-chip families are left alone on purpose', () => {
    // Pinned so "finish the sweep" cannot turn 11 identical tag links into 11 different names.
    const kn = read('pages/knowledge/KnowledgeListPage.tsx')
    expect(kn, 'tag chips share a name because they share a destination').not.toMatch(/aria-label=\{`Tag: /)
  })
})
