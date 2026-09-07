/** Two of Memory's three deletes said "This cannot be undone" over a one-click Undo.
 *
 * `confirmDelete`'s default body is `'This cannot be undone.'`, and all three deletes in
 * `MemoryPanel.remove()` took it. For two of them it is false:
 *
 *   · **a semantic fact** — `vector_memory.delete_semantic` is a TOMBSTONE
 *     (`UPDATE semantic_memory SET is_deleted = 1`), the row keeps its `value_json`, AND
 *     `_log_event("delete", "semantic", key, existing["value_json"], …)` records the prior value. The
 *     data survives in two places.
 *   · **a lesson** — `delete_lesson` calls `delete_semantic` per match, so its event is
 *     `memory_type='semantic'` and undoes by the same route.
 *
 * And this very panel ships the undo: the History tab's `canUndo` is
 * `ev.memory_type === 'semantic' && UNDOABLE.has(ev.event_type)`, with `'delete'` in `UNDOABLE`.
 *
 * 🪤 OVERSTATING A LOSS IS ITS OWN DEFECT, not the safe direction to err in. Warnings work by being
 * scarce; one that cries irreversible over a reversible action is what teaches people to click through
 * the ones that mean it. This codebase already argues that from the other side — `settingsWriteReported`
 * is titled *"Reconciling is not the same as reporting"* for the mirror case.
 *
 * 🔑 THE DISCRIMINATOR IS IN THE SAME FUNCTION, and it is what makes this a precise correction rather
 * than a blanket softening: **episodic delete genuinely cannot be undone.** It is also a tombstone, but
 * `undo_event` refuses a non-semantic event and `canUndo` gates on `memory_type === 'semantic'`. Its copy
 * is correct and is deliberately untouched — asserted below, so a later sweep cannot "finish the job".
 *
 * 🪤 AND THE LESSON UNDO IS NOT THE SAME UNDO — saying only "reversible" would have been a second
 * overclaim in the other direction. `delete_lesson` also calls `_reverse_lesson`, which voids the
 * accumulated observations on purpose (its comment: the key is deterministic, so re-writing the rule
 * un-tombstones this row and "without the reversal it would return at the confidence it had when the
 * user threw it away"). `undo_event` only clears `is_deleted`. So the rule returns at RESET confidence,
 * and the copy says so.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const WEB = process.cwd()
const REPO = join(WEB, '..')
const panel = readFileSync(join(WEB, 'src/pages/settings/MemoryPanel.tsx'), 'utf8')
/** Comments blanked in place — this fix's own comments quote the false default to explain it. */
const code = panel
  .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/^(\s*)\/\/.*$/gm, '$1')

/** The three `confirmDelete` calls in `remove()`, in source order. */
const calls = [...code.matchAll(/confirmDelete\(\s*'([^']+)'[\s\S]{0,600}?\)\)\)/g)]

describe('the two reversible deletes say they are reversible', () => {
  it('found all three deletes', () => {
    expect(calls.map((m) => m[1])).toEqual(['memory', 'episodic memory', 'lesson'])
  })

  it('🔴 the semantic-fact delete no longer claims irreversibility', () => {
    const fact = calls[0][0]
    // It must pass a body at all — taking the default IS the defect.
    expect(fact, 'a custom body is passed').toMatch(/body:/)
    expect(fact, 'and it names the undo').toMatch(/reversible/i)
    expect(fact, 'pointing at where the undo lives').toMatch(/History tab/)
  })

  it('🪤 the lesson delete says reversible AND names the confidence reset', () => {
    const lesson = calls[2][0]
    expect(lesson, 'a custom body is passed').toMatch(/body:/)
    expect(lesson).toMatch(/History tab/)
    // The half that stops this being a second overclaim. Undo restores the rule, not its standing.
    expect(lesson, 'the caveat that makes "undo" honest here').toMatch(/confidence reset/)
  })

  it('🔑 the EPISODIC delete keeps the default — it really cannot be undone', () => {
    const ep = calls[1][0]
    // No custom body: it takes `confirmDelete`'s 'This cannot be undone.', which is true for this one.
    expect(ep, 'episodic must NOT gain a body claiming reversibility').not.toMatch(/body:/)
  })
})

describe('VACUITY: the undo this copy promises actually exists', () => {
  const vm = readFileSync(join(REPO, 'src/personalclaw/vector_memory.py'), 'utf8')

  it('delete_semantic is a tombstone that keeps the value, not a row deletion', () => {
    // 🪤 Bounded to the next method, not to a blank line — a `\n\n` window stops inside the docstring.
    const fn = vm.match(/def delete_semantic\(self[\s\S]*?(?=\n    def )/)?.[0] ?? ''
    expect(fn, 'found delete_semantic').not.toBe('')
    expect(fn, 'a tombstone, not a DELETE').toMatch(/SET is_deleted = 1/)
    expect(fn, 'and the prior value is logged too').toMatch(/_log_event\("delete", "semantic", key, existing\["value_json"\]/)
  })

  it('a lesson delete routes through it, which is why it shares the undo', () => {
    const fn = vm.match(/def delete_lesson\(self[\s\S]*?(?=\n    def )/)?.[0] ?? ''
    expect(fn, 'found delete_lesson').not.toBe('')
    expect(fn, 'it delegates to the semantic tombstone').toMatch(/self\.delete_semantic\(/)
    // The caveat's source. If this call ever goes, the "confidence reset" clause becomes wrong.
    expect(fn, 'and it voids the earned observations').toMatch(/self\._reverse_lesson\(/)
  })

  it('the UI really offers Undo for a semantic delete', () => {
    expect(code, "'delete' is undoable").toMatch(/const UNDOABLE = new Set\(\[[^\]]*'delete'/)
    expect(code, 'gated to semantic events, which is why episodic has no route back')
      .toMatch(/ev\.memory_type === 'semantic' && UNDOABLE\.has\(ev\.event_type\)/)
  })

  it('🔑 undo_event refuses a NON-semantic event — the episodic copy rests on this', () => {
    // If this guard were ever relaxed, episodic delete would become undoable and its "cannot be undone"
    // would turn into the third overclaim. This reds first.
    const fn = vm.match(/def undo_event\(self[\s\S]*?(?=\n    def )/)?.[0] ?? ''
    expect(fn, 'found undo_event').not.toBe('')
    expect(fn, 'a semantic-only guard exists').toMatch(/semantic/)
  })

  it("the default body really is the irreversibility claim, so taking it was the defect", () => {
    const dialog = readFileSync(join(WEB, 'src/ui/dialog/index.ts'), 'utf8')
    expect(dialog).toMatch(/body: opts\?\.body \?\? 'This cannot be undone\.'/)
  })
})
