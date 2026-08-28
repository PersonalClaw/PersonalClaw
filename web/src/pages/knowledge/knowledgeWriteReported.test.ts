import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A data-driven write on the knowledge rail must report, and must not refetch on failure ────────
//
// Second adopter of the contract `tools/toggleFailureReported` named. Every write on this page is
// DATA-DRIVEN — nothing flips locally; the row re-renders from a refetch:
//
//     await api.setKnowledgeReadState(it.id, next).catch(() => {})   // swallowed
//     invalidateKeys(itemsKey)
//     refreshItems()                                                // ran REGARDLESS
//
// So a failure left **nothing**: the shelf did not gain the item, the read-state pill did not move,
// no message appeared — and the refetch ran anyway, re-rendering the same state, so the click read as
// "nothing happened, twice". Five sites: shelve, unshelve, cycle read state, toggle favourite, rename
// a shelf.
//
// 🔑 The helper was EXTRACTED rather than copied. It began in `ToolsPage`; a second adopter is what
// makes a shared module correct rather than speculative, and copying nine lines is the drift a later
// pass has to converge anyway. `app/reportingWrite` is now the single implementation, and this rail
// asserts no page-local copy shadows it — the failure mode a sibling PR shipped and had to amend.
//
// 🪤 DELIBERATELY NOT INCLUDED: `createCollection` keeps its documented `catch { /* the rail just
// doesn't gain a shelf */ }`. It is the same shape and arguably wants the same fix, but it is a
// CREATE behind a prompt dialog rather than a row control, its comment records a decision someone
// made, and changing it would widen this PR past one concern. Recorded in the handoff instead of
// half-done here.

const SRC = readFileSync(join(process.cwd(), 'src/pages/knowledge/KnowledgeListPage.tsx'), 'utf8')
// Comments out, so a rule can never be satisfied (or broken) by prose describing it.
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

/** The five data-driven writes this contract covers. */
const WRITES = [
  'addToKnowledgeCollection',
  'removeFromKnowledgeCollection',
  'setKnowledgeReadState',
  'setKnowledgeFavorited',
  'updateKnowledgeCollection',
] as const

describe('a failed knowledge write says so, and does not refetch', () => {
  it('uses the SHARED reporter and keeps no local copy', () => {
    // 🪤 THIS WAS AN EXACT IMPORT PIN AND IT BROKE ON A LEGITIMATE CHANGE. It read
    // `/import \{ reportingWrite \} from …/`, so the moment this page also needed the module's OTHER
    // export — `reportActionFailure`, for a create whose RESULT the caller needs — the import became
    // `{ reportActionFailure, reportingWrite }` and this test failed while the property it names was
    // MORE satisfied than before. That is the "exact pin standing in for a property" defect this tree
    // has now hit four times. Assert the property: the reporter is imported FROM the shared module,
    // whatever else travels with it.
    expect(CODE, 'the reporter must come from the shared module').toMatch(
      /import \{[^}]*\breportingWrite\b[^}]*\} from '\.\.\/\.\.\/app\/reportingWrite'/)
    // And the real risk the test exists for, now covering BOTH exports: a page-local definition would
    // shadow the shared one silently and the two sentences would drift.
    const localDefs = [...CODE.matchAll(/(function|const)\s+(reportingWrite|reportActionFailure)\b\s*[=(]/g)]
    expect(localDefs.length, 'a page-local copy would shadow the shared one silently').toBe(0)
  })

  it('the create-shelf write reports too — it takes the module\'s OTHER form', () => {
    // `createKnowledgeCollection` is deliberately not in `WRITES`: that list is the `reportingWrite`
    // (boolean) shape, and this call needs its RESULT (`collection.id` selects the new shelf), which is
    // the split `app/reportingWrite` documents. So it is asserted here rather than bent into the loop.
    //
    // Why it matters: the user is prompted TWICE and clicks "Create shelf", and this used to be
    // `catch { /* the rail just doesn't gain a shelf */ }` — a stated OUTCOME, not a reason. It sat
    // eleven lines above this file's own first `reportingWrite` call.
    const at = CODE.indexOf('api.createKnowledgeCollection(')
    expect(at, 'the create-shelf call must still exist').toBeGreaterThan(-1)
    const region = CODE.slice(at, at + 320)
    expect(region, 'a failed create must be reported').toMatch(/reportActionFailure\(/)
    expect(region, 'and its follow-ups gated, so no cache invalidation or selection on a failure')
      .toMatch(/if \(!res\) return/)
    const gate = region.indexOf('if (!res) return')
    expect(region.indexOf('invalidateKeys'), 'invalidate must come AFTER the gate').toBeGreaterThan(gate)
    expect(region.indexOf('setCollectionTok'), 'and so must the selection').toBeGreaterThan(gate)
  })

  it('every one of the five writes routes through it', () => {
    const missing: string[] = []
    for (const call of WRITES) {
      const at = CODE.indexOf(`api.${call}(`)
      expect(at, `${call} must still be performed`).toBeGreaterThan(-1)
      // The call is the second argument of reportingWrite, so the helper opens before it.
      const before = CODE.slice(Math.max(0, at - 220), at)
      if (!before.includes('reportingWrite(')) missing.push(call)
    }
    expect(missing, 'a swallowed data-driven write leaves nothing at all').toEqual([])
  })

  it('none of them swallows its rejection — the ratchet', () => {
    const scan = CODE.replace(/=>/g, '⇒')
    const offenders: string[] = []
    for (const call of WRITES) {
      for (const m of scan.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))) {
        if (/\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/.test(scan.slice(m.index!, m.index! + 200))) {
          offenders.push(`${call}:${scan.slice(0, m.index).split('\n').length}`)
        }
      }
    }
    expect(offenders).toEqual([])
  })

  it('the refetch is GATED on the outcome, not run regardless', () => {
    // The half that distinguishes this contract from the optimistic one. Every `reportingWrite`
    // here must be followed by an early return before any cache invalidation or refresh.
    const gates = [...CODE.matchAll(/const ok = await reportingWrite\(/g)]
    expect(gates.length, 'each write captures its outcome').toBe(WRITES.length)
    for (const m of gates) {
      const after = CODE.slice(m.index!, m.index! + 420)
      expect(after, 'the outcome must gate what follows').toMatch(/if \(!ok\) return/)
      const guard = after.indexOf('if (!ok) return')
      const refresh = after.search(/invalidateKeys\(|refreshItems\(|refreshCollections\(/)
      expect(refresh, 'the write is followed by a refetch').toBeGreaterThan(-1)
      expect(guard, 'and the guard comes BEFORE it').toBeLessThan(refresh)
    }
  })

  it('each message names WHICH item or shelf failed', () => {
    // "Couldn't add" alone is useless on a page that is a grid of items and a rail of shelves.
    for (const frag of [
      'add "${it.title',
      'remove "${it.title',
      'mark "${it.title',
      'rename "${c.name}"',
    ]) {
      expect(SRC, `no message names the subject for ${frag}`).toContain(frag)
    }
    expect(SRC, 'the favourite toggle names both direction and subject').toMatch(
      /\$\{it\.favorited \? 'unfavourite' : 'favourite'\} "\$\{it\.title/,
    )
  })

  it('the writes are still data-driven — the premise of this contract', () => {
    // If a later pass made them optimistic (a local flip before the call), the failure shape changes
    // to "a control showing a value the server refused" and the remedy changes with it. Pinned so
    // that cannot happen silently: no state setter may sit between the handler's start and its write.
    for (const call of WRITES) {
      const at = CODE.indexOf(`api.${call}(`)
      expect(at, `${call} must still exist`).toBeGreaterThan(-1)
      // 🪤 THIS WAS A 220-CHARACTER WINDOW AND IT CROSSED A FUNCTION BOUNDARY. `CODE` strips comments
      // to blank lines, so adding a comment block to the PRECEDING handler shortened the character
      // distance until that handler's own trailing `setCollectionTok(...)` fell inside this window —
      // reporting an "optimistic flip" in a handler that has none. A proximity window is the exact
      // instrument shape this suite has been bitten by three times before. Bound it to the enclosing
      // handler instead; all eight writes here are `async function` declarations.
      const fnStart = CODE.lastIndexOf('async function ', at)
      expect(fnStart, `${call} must sit inside a handler`).toBeGreaterThan(-1)
      const before = CODE.slice(fnStart, at)
      expect(before, `${call} gained an optimistic flip`).not.toMatch(/set[A-Z]\w*\(/)
    }
  })
})
