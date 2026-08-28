import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { confirmCopy } from './DurabilityPanel'
import type {
  DurabilityHistoryEntry, DurabilityHistoryPreview, DurabilityHistoryDiffFile,
} from '../../lib/api'

// ── The sentences a user reads before letting the app set aside work on disk ──────────────────
//
// `#/settings/durability`'s roll-back and undo dialogs are the most consequential copy in the app:
// they are the last thing between a click and history being rewritten under a real workspace. The file
// takes that seriously everywhere else — "Your saved credentials are untouched", "Every other file in
// <root> is left exactly as it is", "you will be told which file blocked it" — and then said
// **`file(s)`** and **`change(s)`** thirteen times, in the titles, both bodies, and the held-preview
// summary. Every count was already in hand one token earlier.
//
// 🔑 THE PROPERTY IS AGREEMENT, NOT THE ABSENCE OF A PARENTHETICAL, and that is why this rail CALLS
// the function instead of scanning it. Three different numbers are in scope and the noun belongs to
// whichever one sits beside it:
//
//     `${n} of ${files.length} ${fileWord}`   → agrees with the DENOMINATOR ("3 of 7 files")
//     `the ${n} ${pickedWord} you picked`     → agrees with the SELECTION  ("the 1 file you picked")
//     `${preview.commits_rolled_away} ${changeWord}` → agrees with the commit count
//
// A source scan can prove no `(s)` survives; only a call can prove `pickedWord` was not used where
// `fileWord` belongs. That substitution is invisible in review, reads fine in the common case where
// both happen to be plural, and produces "the 1 files you picked" the moment a user ticks one box —
// which is exactly the state a careful user reaches before a destructive confirm.
//
// 🪤 AND THIS SURFACE WAS ALREADY RAILED — TWICE — WHICH IS THE INTERESTING PART.
// `timeTravelPerFile.test.tsx` and `timeTravelPreviewGate.test.tsx` both call this exact copy and
// assert its blast-radius wording, and both **pinned the parenthetical** (`2 of 3 file(s)`,
// `Only the 2 file(s) you picked`, `2 later change(s) would be set aside`) for as long as it shipped.
//
// They could not see it, and the reason generalises: **every count in their fixtures is 2 or 3, never
// 1** — and `file(s)` differs from `files` ONLY at n === 1. A rail whose fixtures never cross the
// singular boundary cannot distinguish a hedge from a correct plural, however carefully it asserts the
// sentence. So this rail's contribution is not "the copy is now tested"; it is that the copy is tested
// **at n === 1**, in every position, including the mixed case where the denominator is plural and the
// selection is singular in the same sentence.
//
// 🪤 They were also nearly missed when picking this work: the rule is to grep for a rail whose NAME
// matches the concept, and these are named after the section HEADING ("Time travel"), not the file,
// the route, or the component. `grep -l durab` finds nothing. **Grep the user-visible label too.**

const entry: DurabilityHistoryEntry = {
  sha: 'a'.repeat(40), short: 'aaaaaaa', at: 1_700_000_000, subject: 'tidy the ledger',
  surface: 'workflows', unattended: false,
}
const file = (path: string): DurabilityHistoryDiffFile =>
  ({ path, status: 'M', bytes: 12, rendered: true, diff: '' })
const preview = (files: string[], rolledAway: number): DurabilityHistoryPreview => ({
  operation: 'rollback', root: '/w', target: 'b'.repeat(40), head: 'c'.repeat(40),
  files: files.map(file), commits_rolled_away: rolledAway, reversible: true, paths: [],
})
/** `paths` is the previewed SUBSET (`[]` = whole root); `files` is the selectable universe. */
const pending = (paths: string[], universe: string[], rolledAway: number) => ({
  entry, op: 'rollback' as const, head: 'c'.repeat(40),
  preview: preview(universe, rolledAway), paths, files: universe.map(file),
})

describe('the durability dialogs agree with their own counts', () => {
  it('never ships a parenthetical plural (the shortcut this replaced)', () => {
    // Both helpers, both ops, across the singular/plural boundary in every position.
    const shapes = [
      confirmCopy('rollback', '/w', entry, pending([], ['a.ts'], 1)),
      confirmCopy('rollback', '/w', entry, pending(['a.ts'], ['a.ts'], 1)),
      confirmCopy('rollback', '/w', entry, pending(['a.ts'], ['a.ts', 'b.ts'], 4)),
      confirmCopy('revert', '/w', entry, pending([], ['a.ts'], 1)),
      confirmCopy('revert', '/w', entry, pending(['a.ts'], ['a.ts', 'b.ts'], 4)),
    ]
    for (const s of shapes) {
      expect(s.title, `"${s.title}" still hedges`).not.toMatch(/\((s|es)\)/)
      expect(s.body, `"${s.body.slice(0, 60)}…" still hedges`).not.toMatch(/\((s|es)\)/)
    }
    // Vacuity floor: the assertion above is equally satisfied by a function returning ''.
    expect(shapes.every((s) => s.title.length > 20 && s.body.length > 80)).toBe(true)
  })

  it('ONE picked file reads singular in the title and in the body', () => {
    // The state a careful user reaches before a destructive confirm: one box ticked.
    const one = confirmCopy('rollback', '/w', entry, pending(['a.ts'], ['a.ts'], 1))
    expect(one.title).toBe('Roll back 1 of 1 file to this point?')
    expect(one.body, 'the selection noun, not the denominator noun').toContain('Only the 1 file you picked')
    expect(one.body, 'and the commit count agrees too').toContain('The 1 change made since')
    expect(one.body).toContain('set aside for those 1 file')
  })

  it('the DENOMINATOR governs the "n of m" noun, not the selection', () => {
    // 🔑 The substitution this rail exists to catch: one file picked out of two is "1 of 2 files".
    // Using the selection's noun there yields "1 of 2 file", which reads as a typo in a confirm.
    const mixed = confirmCopy('rollback', '/w', entry, pending(['a.ts'], ['a.ts', 'b.ts'], 4))
    expect(mixed.title).toBe('Roll back 1 of 2 files to this point?')
    expect(mixed.body, 'while the SELECTION stays singular in the same sentence').toContain(
      'Only the 1 file you picked',
    )
    expect(mixed.body).toContain('The 4 changes made since')
  })

  it('the whole-root body pluralises on the commit count alone', () => {
    expect(confirmCopy('rollback', '/w', entry, pending([], ['a.ts', 'b.ts'], 1)).body)
      .toContain('The 1 change made since')
    expect(confirmCopy('rollback', '/w', entry, pending([], ['a.ts', 'b.ts'], 9)).body)
      .toContain('The 9 changes made since')
  })

  it('the undo dialog agrees on both numbers too', () => {
    expect(confirmCopy('revert', '/w', entry, pending(['a.ts'], ['a.ts'], 1)).title)
      .toBe('Undo just this change in 1 of 1 file?')
    expect(confirmCopy('revert', '/w', entry, pending(['a.ts'], ['a.ts', 'b.ts'], 1)).title)
      .toBe('Undo just this change in 1 of 2 files?')
    expect(confirmCopy('revert', '/w', entry, pending(['a.ts'], ['a.ts'], 1)).body)
      .toContain('to the 1 file you picked')
  })

  it('and the held-preview summary — the one line NOT reachable through confirmCopy', () => {
    // `PreviewCard` assembles its own sentence, so calling `confirmCopy` cannot cover it. Asserted at
    // the source, and bounded to that component so a later `(s)` elsewhere in this 1000-line file
    // cannot satisfy it — the "bound the slice to the construct" rule.
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/DurabilityPanel.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const at = src.indexOf('function PreviewCard(')
    expect(at, 'PreviewCard must still exist').toBeGreaterThan(-1)
    // 🪤 NOT `indexOf('\n}', at)` — that was the first draft and it matched the `}) {` closing this
    // component's multi-line PROPS TYPE, so the slice ended at the signature and never reached the
    // body it was supposed to bound. Bounded to the next top-level `function` instead, which cannot
    // land inside a parameter list.
    const next = src.indexOf('\nfunction ', at + 1)
    expect(next, 'a top-level function must follow PreviewCard').toBeGreaterThan(at)
    const body = src.slice(at, next)
    expect(body, 'the summary must not hedge either').not.toMatch(/\((s|es)\)/)
    expect(body, 'and it must derive its three nouns from its three counts').toMatch(
      /const previewWord = preview\.files\.length === 1/,
    )
    expect(body).toMatch(/const fileWord = files\.length === 1/)
    expect(body).toMatch(/const changeWord = preview\.commits_rolled_away === 1/)
  })
})
