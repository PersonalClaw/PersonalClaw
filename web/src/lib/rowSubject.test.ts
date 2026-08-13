import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { rowSubject } from './rowSubject'

// ── One rule for a row control's name, and one implementation of it ──────────────────────────────
//
// Cycle 139 gave row actions their row's subject. Cycle 141 measured the result in Chrome's computed
// accessibility tree and found the opposite failure — five artifact tiles named by 438-695 characters
// of their own body — so it capped the notification names at 55. Cycle 142 then measured the FIRST
// fix's own output and found it had never been capped:
//
//   surface           row controls   distinct names   max name length   names >80ch
//   #/dashboard  BEFORE     23            22              **107**          **16**
//               AFTER       23            22               78                0
//   #/notifications        249           231               76                0     (unchanged)
//
// 🔑 THE CAP COST NOTHING. Distinctness is identical either side — 22 of 23 on the dashboard, 231 of
// 249 on notifications, and the same worst duplicate (×2 "Mark complete: triage", two tasks that
// really are both called triage). So the uncapped version was buying no information with its extra
// 29 characters.
//
// 🪤 TWO CYCLES SHIPPED THE SAME RULE WITH DIFFERENT NUMBERS — 139 uncapped, 141 at 55 — which is how
// a rule becomes drift. One helper now owns both the joining and the number.
//
// 🔑 NOT FOR LONG DATA. `#/knowledge` has 29 names over 80 characters because its items are titled
// that way ("ZFS Resilver Time Calculator — third-party dRAID drag-factor grid (1.42-1.69x), and why
// it oversells…"). The visible label truncates and the name does not, so a screen-reader user gets
// MORE than a sighted one. Left alone deliberately: capping data truncates an identity, which is a
// different thing from bounding a name you assembled. Recorded as rejected, not deferred.

describe('rowSubject joins the parts that identify a row', () => {
  it('joins with an em dash', () => {
    expect(rowSubject(['Loop progress', 'cycle 4 finished'])).toBe('Loop progress — cycle 4 finished')
  })

  it('drops empty, null and undefined parts', () => {
    expect(rowSubject(['Only this', '', null, undefined])).toBe('Only this')
    expect(rowSubject([null, 'Second'])).toBe('Second')
  })

  it('does not say the same thing twice', () => {
    // The live shape this guards: an entry whose summary line starts with its own title would burn
    // the whole budget repeating itself.
    expect(rowSubject(['Refine a skill', 'Refine a skill'])).toBe('Refine a skill')
    expect(rowSubject(['Refine a skill', 'Refine a skill loop-worker — add a step'])).toBe('Refine a skill')
  })

  it('caps at 55 characters with an ellipsis', () => {
    const long = rowSubject(['skills', 'Refine a skill loop-worker — When producing a quorum/roster planning artifact'])
    expect(long.length).toBe(55)
    expect(long.endsWith('…')).toBe(true)
  })

  it('leaves a short subject exactly as it is', () => {
    expect(rowSubject(['triage'])).toBe('triage')
    expect(rowSubject(['a'.repeat(55)]).length).toBe(55)
    expect(rowSubject(['a'.repeat(56)]).length).toBe(55)
  })

  it('takes a cap override for a caller with a different budget', () => {
    expect(rowSubject(['abcdefghij'], 5)).toBe('abcd…')
  })
})

describe('both composing surfaces use the one helper', () => {
  const SRC = join(process.cwd(), 'src')
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
  const codeOf = (rel: string) => read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it("the dashboard's action centre composes through it", () => {
    const code = codeOf('pages/dashboard/widgets/ActionCenter.tsx')
    expect(code).toMatch(/const subject = rowSubject\(\[e\.title, e\.sub\]\)/)
    expect(code, 'the hand-rolled join must be gone').not.toMatch(/e\.sub \? `\$\{e\.title\} — \$\{e\.sub\}`/)
  })

  it('the notification rows compose through it', () => {
    const code = codeOf('pages/notifications/NotificationsPage.tsx')
    expect(code).toMatch(/rowSubject\(\[n\.title, firstLine\(n\.body \?\? ''\)\]\)/)
    expect(code, 'the local copy of the rule must be gone').not.toMatch(/function rowName/)
    expect(code, 'and its number with it').not.toMatch(/full\.length > 55/)
  })

  it('nothing else re-implements the cap', () => {
    // The whole point: one number, one place. A second `slice(0, 54)` anywhere is the drift returning.
    const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
      })
    const offenders = walk(join(SRC, 'pages')).filter((abs) => /slice\(0, 5[0-9]\)…|length > 5[0-9] \?/.test(readFileSync(abs, 'utf8')))
    expect(offenders).toEqual([])
  })
})
