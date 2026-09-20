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
 *
 * 🔑 THE ENTITY DELETE (#524) IS THE SECOND IRREVERSIBLE ONE, and it is irreversible for a DIFFERENT
 * reason than episodic — which is why it gets its own assertion instead of being folded into that one.
 * Episodic rests on `undo_event` refusing a non-semantic event: the event EXISTS, the applier declines
 * it. `MemoryGraph.delete_entity` logs no event at all, so there is no History row to offer Undo on in
 * the first place. Both copies say "This cannot be undone." truthfully, from two different facts, and
 * both facts are asserted below.
 *
 * 🪤 THIS RAIL IS AN ENUMERATION, so it is blind to a delete it does not list — which is exactly how it
 * went red: #524 added the fourth `confirmDelete` and the list still said three. The count assertion is the
 * part that catches that, so it is kept; what changed is that each copy assertion now keys off its
 * NOUN rather than a positional index, so inserting a delete in the middle can no longer silently
 * re-point an existing assertion at the wrong dialog.
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

/** The four `confirmDelete` calls in `remove()`, in source order. */
const calls = [...code.matchAll(/confirmDelete\(\s*'([^']+)'[\s\S]{0,600}?\)\)\)/g)]
/** One call's source by the noun it deletes — see the enumeration trap in the header. */
const forNoun = (noun: string) => {
  const found = calls.filter((m) => m[1] === noun)
  expect(found, `exactly one confirmDelete for '${noun}'`).toHaveLength(1)
  return found[0][0]
}

describe("every delete's copy matches whether it can be undone", () => {
  it('found all four deletes', () => {
    expect(calls.map((m) => m[1])).toEqual(['memory', 'episodic memory', 'lesson', 'entity'])
  })

  it('🔴 the semantic-fact delete no longer claims irreversibility', () => {
    const fact = forNoun('memory')
    // It must pass a body at all — taking the default IS the defect.
    expect(fact, 'a custom body is passed').toMatch(/body:/)
    expect(fact, 'and it names the undo').toMatch(/reversible/i)
    expect(fact, 'pointing at where the undo lives').toMatch(/History tab/)
  })

  it('🪤 the lesson delete says reversible AND names the confidence reset', () => {
    const lesson = forNoun('lesson')
    expect(lesson, 'a custom body is passed').toMatch(/body:/)
    expect(lesson).toMatch(/History tab/)
    // The half that stops this being a second overclaim. Undo restores the rule, not its standing.
    expect(lesson, 'the caveat that makes "undo" honest here').toMatch(/confidence reset/)
  })

  it('🔑 the EPISODIC delete keeps the default — it really cannot be undone', () => {
    const ep = forNoun('episodic memory')
    // No custom body: it takes `confirmDelete`'s 'This cannot be undone.', which is true for this one.
    expect(ep, 'episodic must NOT gain a body claiming reversibility').not.toMatch(/body:/)
  })

  it('🔑 the ENTITY delete states the blast radius AND keeps the irreversibility claim', () => {
    const entity = forNoun('entity')
    // It cannot take the default: the default answers "is it reversible?" and says nothing about
    // the links, which is the question an entity delete actually raises.
    expect(entity, 'a custom body is passed').toMatch(/body:/)
    expect(entity, 'the links pointing at it are what go').toMatch(/dropped/)
    expect(entity, 'and the records they came from are what stays').toMatch(/stay/)
    // The other half: unlike the fact and the lesson, this one has no route back, so the custom
    // body must carry the claim the default would have made — and must not soften it.
    expect(entity, 'irreversibility is still stated').toMatch(/This cannot be undone\./)
    expect(entity, 'and no undo is promised').not.toMatch(/reversible|History tab/i)
  })
})

describe('VACUITY: the undo this copy promises actually exists', () => {
  const vm = readFileSync(join(REPO, 'src/personalclaw/vector_memory.py'), 'utf8')
  const graph = readFileSync(join(REPO, 'src/personalclaw/memory_graph.py'), 'utf8')

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

  it('🔑 an entity delete logs NO event, which is why its copy says so', () => {
    // The episodic claim rests on `undo_event` declining an event that exists; this one rests on
    // there being no event to decline. The History tab lists `memory_events` rows, so a delete that
    // writes none can never surface an Undo. If this ever started logging, the entity copy would
    // become the overclaim in the OTHER direction — a real undo, hidden behind "cannot be undone".
    const fn = graph.match(/def delete_entity\(self[\s\S]*?(?=\n    # |\n    def )/)?.[0] ?? ''
    expect(fn, 'found delete_entity').not.toBe('')
    expect(fn, 'it is a tombstone').toMatch(/SET is_deleted = 1/)
    expect(fn, 'and the links pointing at it are really dropped').toMatch(/DELETE FROM mem_links WHERE to_entity/)
    expect(fn, 'no event is logged, so there is nothing to undo').not.toMatch(/_log_event/)
    // Belt and braces on the same claim from the panel's side: even if an entity event ever appeared,
    // the History tab's Undo is gated to semantic events, so it would still never be offered here.
    expect(code, "and the panel's undo is gated to semantic events, which excludes entities")
      .toMatch(/ev\.memory_type === 'semantic'/)
  })

  it("the default body really is the irreversibility claim, so taking it was the defect", () => {
    const dialog = readFileSync(join(WEB, 'src/ui/dialog/index.ts'), 'utf8')
    expect(dialog).toMatch(/body: opts\?\.body \?\? 'This cannot be undone\.'/)
  })
})
