import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A "stop" that silently did not stop ───────────────────────────────────────────────────────────
//
// The fifth shape in this page's write-failure family, and the one whose failure the user acts on.
// `selectionPersistReported` covers a control left showing a value the server refused; this covers a
// control that says THE WORK ENDED while it carries on. Every cancel here flips local state FIRST:
//
//     markStreaming(false)                                        // "the turn is over"
//     await api.stopChat(s).catch(() => {})                        // swallowed
//
//     setQueued((prev) => prev.filter((q) => q.id !== id))         // the row vanishes
//     api.cancelQueued(s, id).catch(() => {})                      // swallowed
//
//     setSubagents((prev) => prev.map((c) => ({ ...c, done: true, error: 'cancelled' })))
//     await api.cancelFanout(s).catch(() => {})                    // swallowed
//
// 🔑 THE QUEUE'S "EDIT" PATH IS THE WORST OF THE SIX. There is no queue-edit endpoint, so editing
// cancels the pending item and drops its text into the composer for the user to revise and resend. If
// that cancel failed silently, the original sends anyway — so the user gets BOTH their revision and
// the message they thought they had replaced. A duplicate send, from a control that reported nothing.
//
// Six sites, two components ~2000 lines apart, one reporter. The reporter is at MODULE scope for
// exactly that reason: a per-component copy is the drift this converges away from, and TypeScript
// caught the second scope when the first draft made it a closure.
//
// It is named `reportActionFailure` rather than for this concern, because a SECOND one now shares it
// (a permission decision that silently did not register — `approvalDecisionReported`). The sentence
// is the shared part; each rail keeps its own reasoning.
//
// 🪤 REVERTING THE OPTIMISTIC FLIP WAS CONSIDERED AND REJECTED, on the same reasoning as
// `selectionPersistReported`: the family's remedy is to TELL. `markStreaming` is also driven by the
// chat socket, so restoring it here would fight the stream rather than inform the user. Recorded so a
// later pass does not "finish the job" by adding a revert nobody verified against the WS.

const SRC = join(process.cwd(), 'src', 'pages', 'ChatPage.tsx')
const raw = readFileSync(SRC, 'utf8')
// `=>` neutralised before any bounded scan: the catch bodies ARE arrow functions.
const scan = raw.replace(/=>/g, '⇒')

/** The cancel/stop writes this contract covers. */
const STOPS = ['stopChat', 'cancelFanout', 'cancelQueued', 'interruptChat', 'cancelRetag'] as const

describe('a failed cancel tells the user the work did not stop', () => {
  it('the reporter is the SHARED one, imported, and defined exactly ONCE in the tree', () => {
    // Re-pointed twice now, never relaxed. It began as a closure here, moved to this file's module
    // scope, and moved again to `app/reportingWrite` when `dashboard/PinnedTiles` became a second
    // adopter of the callback form. The assertions follow it; the discipline does not change.
    expect(raw, 'the shared sentence is imported').toMatch(
      /import \{ reportActionFailure, reportingWrite \} from '\.\.\/app\/reportingWrite'/,
    )
    const shared = readFileSync(join(process.cwd(), 'src/app/reportingWrite.ts'), 'utf8')
    expect(shared).toMatch(/^export const reportActionFailure = \(what: string\) => \(e: unknown\) => \{$/m)
    expect(shared).toContain("notify(`Couldn't ${what}: ${e instanceof Error ? e.message : String(e)}`, 'error')")
    // 🪤 UNIQUENESS, not just scope. The first version of this test asserted only that a
    // module-level definition EXISTS — and the PR that added it shipped a second, indented copy
    // inside `ChatPage` as well, which legally shadows the outer one. TypeScript is silent about
    // shadowing, the five in-component call sites bound to the closure, and this very assertion
    // passed because the module-level one it looked for was also there. Asserting existence is not
    // asserting singularity.
    // 🪤 UNIQUENESS, tree-wide now. Asserting a definition EXISTS is not asserting singularity: the PR
    // that first moved this shipped a second, indented copy inside `ChatPage` which legally shadowed
    // the outer one, and this very test passed because the module-level one it looked for was also
    // there. Count across the app, so a re-copy into any page fails wherever it lands.
    const walk = (dir: string, out: string[] = []): string[] => {
      for (const name of readdirSync(dir)) {
        const abs = join(dir, name)
        if (statSync(abs).isDirectory()) walk(abs, out)
        else if (/\.tsx?$/.test(name) && !name.includes('.test.')) out.push(abs)
      }
      return out
    }
    const defs = walk(join(process.cwd(), 'src')).filter((abs) =>
      /(^|\s)(const|function)\s+reportActionFailure\b\s*[=(]/m.test(readFileSync(abs, 'utf8')),
    )
    expect(defs.map((d) => d.replace(process.cwd() + '/', '')), 'exactly one home').toEqual([
      'src/app/reportingWrite.ts',
    ])
  })

  it('every cancel/stop write routes through it', () => {
    const missing: string[] = []
    for (const call of STOPS) {
      for (const m of scan.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))) {
        const chain = scan.slice(m.index!, m.index! + 200)
        if (!/\.catch\(reportActionFailure\('/.test(chain)) {
          missing.push(`${call}:${scan.slice(0, m.index).split('\n').length}`)
        }
      }
    }
    expect(missing, 'a cancel that swallows tells the user the work stopped when it did not').toEqual([])
  })

  it('no cancel/stop write swallows its rejection — the ratchet', () => {
    const offenders: string[] = []
    for (const call of STOPS) {
      for (const m of scan.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))) {
        const chain = scan.slice(m.index!, m.index! + 200)
        if (/\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/.test(chain)) {
          offenders.push(`${call}:${scan.slice(0, m.index).split('\n').length}`)
        }
      }
    }
    expect(offenders).toEqual([])
  })

  it('the ratchet is not vacuous — it finds all six sites', () => {
    // A regex that matched nothing would make the two rules above look enforced while enforcing
    // nothing at all.
    const found = STOPS.flatMap((c) => [...scan.matchAll(new RegExp(`api\\.${c}\\(`, 'g'))])
    expect(found.length, 'the six known cancel sites must be in scope').toBeGreaterThanOrEqual(6)
    expect(new Set(STOPS.filter((c) => scan.includes(`api.${c}(`))).size).toBe(STOPS.length)
  })

  it('each message names WHICH stop failed', () => {
    // "Couldn't cancel" alone is useless on a page with a stop button, a queue strip and a retag run.
    for (const what of [
      'stop this turn',
      'cancel the subagents',
      'cancel that queued message',
      'interrupt this turn',
      'cancel the retag run',
    ]) {
      expect(raw, `no report says ${what}`).toContain(`reportActionFailure('${what}')`)
    }
  })

  it('the optimistic flips are still there — the premise of the whole finding', () => {
    // If a later pass made these data-driven, the failure shape changes from "a lying control" to
    // "nothing happens", which is a different contract with a different remedy (see
    // `tools/toggleFailureReported`). This pins the premise so that change cannot pass unnoticed.
    expect(raw, 'stop() still claims the turn ended before the call').toMatch(
      /markStreaming\(false\)\s*\n\s*if \(sessionRef\.current\) await api\.stopChat/,
    )
    expect(raw, 'the queue row still vanishes first').toMatch(
      /setQueued\(\(prev\) => prev\.filter\(\(q\) => q\.id !== id\)\); const s = sessionRef\.current/,
    )
    expect(raw, 'the subagent cards still read cancelled first').toContain("error: 'cancelled'")
  })

  it('no revert was added — the decision, pinned', () => {
    // Restoring the flipped state would fight the chat socket, which also drives `markStreaming`.
    // Telling the user is the remedy this family settled on.
    //
    // 🪤 The first version of this test scanned for `catch(...) { ... markStreaming(true) }` and a
    // mutation that put the revert in the REPORTER's body sailed past it — the revert does not live
    // in a literal catch block, and `[^}]*` stops at the `}` inside `${what}` anyway. So assert the
    // property instead: the reporter's own body may notify and nothing else.
    // The reporter now lives in `app/reportingWrite`; the property asserted is unchanged.
    const shared = readFileSync(join(process.cwd(), 'src/app/reportingWrite.ts'), 'utf8')
    const at = shared.indexOf('export const reportActionFailure =')
    expect(at, 'the reporter must exist').toBeGreaterThan(-1)
    const body = shared.slice(at, shared.indexOf('\n}', at) + 2)
    expect(body, 'the reporter reports').toContain('notify(')
    for (const forbidden of ['markStreaming(', 'setQueued(', 'setSubagents(', 'setSelection(']) {
      expect(body, `the reporter must not also mutate state — found ${forbidden}`).not.toContain(
        forbidden,
      )
    }
    // …and no cancel call site smuggles one in either.
    for (const call of STOPS) {
      for (const m of scan.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))) {
        const chain = scan.slice(m.index!, m.index! + 200)
        expect(chain, `${call} added a revert`).not.toContain('markStreaming(true)')
      }
    }
  })
})
