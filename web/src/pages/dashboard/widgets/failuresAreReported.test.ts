import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A failed action that says nothing is its own defect ──────────────────────────────────────────
//
// Three filed members of the silent-swallow family (#324, #478, #549), pinned in the same
// source-contract style `toggleFailureReported.test.ts` established for exactly this class:
// the DOM cannot show a toast that was never sent, so the rail reads the source and asserts
// the reporting seam is wired — shared reporter imported, no empty catch on the write path,
// and the response fields that carry refusals are actually read.
//
// 🪤 Comments are stripped before matching: each fix's own doc comment NAMES the old defect
// ("empty catch", "await without reading the body"), and an un-stripped regex would match the
// explanation instead of the code. Same trap the toggle rail recorded.

const read = (rel: string) => {
  const src = readFileSync(join(process.cwd(), rel), 'utf8')
  return src.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
}

describe('ActionCenter actions report their failures (#324)', () => {
  const CODE = read('src/pages/dashboard/widgets/ActionCenter.tsx')

  it('routes every action through the shared reporter, keeping no local copy', () => {
    expect(CODE).toMatch(/import \{ reportingWrite \} from '\.\.\/\.\.\/\.\.\/app\/reportingWrite'/)
    expect([...CODE.matchAll(/(function|const)\s+reportingWrite\b\s*[=(]/g)].length).toBe(0)
    expect(CODE).toMatch(/await reportingWrite\(what, fn\)/)
  })

  it('keeps no empty catch on the action path, and only marks done on success', () => {
    expect(CODE).not.toMatch(/catch\s*\{\s*\}/)
    expect(CODE).toMatch(/if \(ok\) setDone/)
  })

  it('every withBusy caller supplies a human sentence for the toast', () => {
    // Three action kinds × two buttons − the navigate-only reply = 5 labelled calls.
    const calls = [...CODE.matchAll(/withBusy\(e\.key, `[^`]+`/g)]
    expect(calls.length).toBeGreaterThanOrEqual(5)
  })
})

describe('bulk task ops read the per-item outcomes (#478)', () => {
  const CODE = read('src/pages/tasks/TasksListPage.tsx')

  it('reads `failed` from the 200 body instead of discarding the response', () => {
    expect(CODE).toMatch(/const r = await api\.tasksBulk\(op, items\)/)
    expect(CODE).toMatch(/r\.failed > 0/)
    expect(CODE).toMatch(/notify\(/)
  })

  it('reports transport failures through the shared funnel, not an empty catch', () => {
    expect(CODE).toMatch(/reportActionFailure\(/)
    expect(CODE).not.toMatch(/tasksBulk\(op, items\) \} catch \{/)
  })
})

describe('WorkflowProgressCard only vanishes on a real 404 (#549)', () => {
  const CODE = read('src/pages/chat/WorkflowProgressCard.tsx')

  it('branches the catch on ApiError.status — 404 collapses, anything else keeps the card', () => {
    expect(CODE).toMatch(/e instanceof ApiError && e\.status === 404\) setGone\(true\)/)
    expect(CODE).toMatch(/setLoadFailed\(true\)/)
  })

  it('a never-loaded card with a failed read renders a retry line, not null and not a skeleton', () => {
    expect(CODE).toMatch(/!vm && loadFailed/)
    expect(CODE).toMatch(/Try again/)
  })

  it('a successful load clears the failure mark so recovery is visible', () => {
    expect(CODE).toMatch(/setVm\(foldSnapshot\(snap\)\); setLoadFailed\(false\)/)
  })
})
