import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A data-driven write on the knowledge rail must report, and must not refetch on failure ────────
//
// Second adopter of the contract `tools/toggleFailureReported` named. Every write on this page is
// DATA-DRIVEN — nothing flips locally; the row re-renders from a refetch:
//
//     await api.setKnowledgeReadState(it.id, next).catch(() => {})   // swallowed
//     invalidateCache(itemsKey)
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
    expect(CODE).toMatch(/import \{ reportingWrite \} from '\.\.\/\.\.\/app\/reportingWrite'/)
    const localDefs = [...CODE.matchAll(/(function|const)\s+reportingWrite\b\s*[=(]/g)]
    expect(localDefs.length, 'a page-local copy would shadow the shared one silently').toBe(0)
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
      const refresh = after.search(/invalidateCache\(|refreshItems\(|refreshCollections\(/)
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
      const before = CODE.slice(Math.max(0, at - 220), at)
      expect(before, `${call} gained an optimistic flip`).not.toMatch(/set[A-Z]\w*\(/)
    }
  })
})
