import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Deleting an intent asks first, and says what goes with it ──────────────────────────────────
//
// Both delete controls on the intents tab — the row's icon button and the detail panel's "Delete" —
// called `api.deleteKnowledgeIntent` directly on one click. No confirmation from either place, and
// the thing destroyed is not just the intent: `delete_intent` cascades into
// `delete_intent_outcomes`, and an outcome is stored BY VALUE specifically so it "survives
// source-item deletion". So the gathered matches exist nowhere else, and re-adding the intent does
// not bring them back. The detail panel renders "Gathered (N)" directly above the button that
// destroys those N.
//
// 🔑 THE CANONICAL FORM WAS ALREADY HERE, TWICE OVER. `confirmDelete` has fourteen callers
// (schedules, artifacts, providers, memories, tasks, triggers, workflow definitions…), and this very
// file already confirms a shelf deletion a few hundred lines up — with a body that says what is NOT
// destroyed ("The shelf goes away. The items on it stay in your library"), and a comment explaining
// that a destructive-looking action must state its scope. An intent is the mirror image, so it says
// the opposite thing rather than nothing.
//
// The body is COUNTED, not generic: "12 matches" when there are outcomes, and a shorter sentence
// when there are none — a confirmation that overstates the loss trains people to dismiss it.

const SRC = readFileSync(join(process.cwd(), 'src/pages/knowledge/KnowledgeListPage.tsx'), 'utf8')
/** Comments stripped — the explanation above the helper quotes the API call it replaces, and a raw
 *  scan reads that prose as a live call site. (The "ratchet counts markup in comments" trap.) */
const CODE = SRC.replace(/\/\*\*[\s\S]*?\*\//g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

describe('deleting an intent is confirmed from both places', () => {
  it('no delete call remains unguarded', () => {
    const calls = [...CODE.matchAll(/api\.deleteKnowledgeIntent\(/g)]
    expect(calls.length, 'the row control and the detail control').toBe(2)
    for (const m of calls) {
      // Each call sits after its own guard inside the same handler.
      const before = CODE.slice(Math.max(0, m.index - 260), m.index)
      expect(before, 'a confirm precedes this delete').toMatch(/confirmIntentDelete\([^)]*\)\)\) return/)
    }
  })

  it('goes through the app-wide helper, not a bespoke dialog', () => {
    expect(SRC).toMatch(/import \{ confirm, confirmDelete, promptInput \} from '\.\.\/\.\.\/ui\/dialog'/)
    expect(CODE).toMatch(/confirmDelete\('intent', rowSubject\(\[goal\], 40\)/)
  })

  it('the body states the real consequence, and counts it', () => {
    // Not boilerplate: the number is what makes the warning worth reading.
    expect(CODE).toMatch(/Everything it gathered goes with it/)
    expect(CODE, 'the count is interpolated, not hard-coded').toMatch(/\$\{gathered\}/)
    expect(CODE, 'singular and plural').toMatch(/gathered === 1 \? 'match' : 'matches'/)
    expect(CODE, 'and it does not overstate an empty intent')
      .toMatch(/gathered nothing yet, so only the intent itself goes/)
  })

  it('the name is capped, so a sentence-long goal cannot become the dialog title', () => {
    // cycle 142's rule, the same cap the row's aria-label already uses.
    expect(CODE).toMatch(/rowSubject\(\[goal\], 40\)/)
  })

  it('the shelf precedent it mirrors still ships — the vacuity floor', () => {
    // If `removeCollection` ever stops confirming, the reasoning above needs revisiting rather than
    // this rail quietly passing on a file that no longer contains the pattern it cites.
    expect(CODE, 'the shelf confirmation is still there').toMatch(/The shelf goes away\. The items on it stay/)
    expect(CODE, 'and still through a real dialog').toMatch(/const ok = await confirm\(\{/)
  })

  it('both controls keep the names and stop-propagation they already had', () => {
    // The guard must not have cost the row its accessible name or leaked the click to the row.
    expect(CODE).toMatch(/ariaLabel=\{`Delete intent: \$\{rowSubject\(\[it\.goal \|\| it\.id\], 40\)\}`\}/)
    const spans = [...CODE.matchAll(/<span onClick=\{\(e\) => e\.stopPropagation\(\)\}>/g)]
    expect(spans.length, 'row and detail both still swallow the click').toBeGreaterThanOrEqual(2)
  })
})
