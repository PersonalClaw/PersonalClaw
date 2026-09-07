import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A failed mid-stream steer used to destroy the only copy of what the user typed ─────────────────
//
// `ChatSession`'s send path, on the `isStreaming` branch:
//
//     if (isStreaming) {
//       setInput('')                                   // ← BEFORE the request
//       ensureSession()
//         .then((s) => api.sendChat(t, s, undefined, 'steer'))
//         .then((r) => { if (r?.steered) setSteered((prev) => [...prev, t]) })
//         .catch(() => {})                             // ← and the rejection is swallowed
//       return
//     }
//
// 🔴 THE FILE'S OWN COMMENT CLAIMED THIS WAS SAFE. Three lines above sat "Either way nothing is
// dropped — a queued message still echoes `queue_push`". That is true of the two SUCCESS shapes the
// server returns (`steered` / `queued`) and false on REJECTION — the third outcome, which the empty
// catch hid. The comment described the happy paths and was read as describing the whole branch.
//
// The scenario: a user watches a long answer stream, types a correction ("no, use Postgres"), presses
// Enter. `ensureSession()` or `sendChat` rejects — gateway restart, 500, dropped connection. The
// composer empties. No `steered` chip appears, because `setSteered` is in the `.then` that never ran.
// No toast, because the catch is empty. The text is now **nowhere**: not on screen, not in `queued`,
// not on the server — nothing to copy and nothing to retry, while the model keeps streaming the
// answer they were trying to correct.
//
// 🪤 THE OBVIOUS FIX INTRODUCES THE OPPOSITE BUG. Moving `setInput('')` into the success path
// UNCONDITIONALLY trades one data-loss defect for another: the request is in flight for a round trip,
// and a user who begins their next message during it would have it wiped by the late clear. The
// functional setter compares against exactly the text that was sent, so an untouched composer clears
// and a re-typed one is left alone. Both halves are asserted below.
//
// 🪤 WHY THIS IS A SOURCE-STRUCTURE RAIL AND NOT A DOM TEST. Reaching this branch needs `isStreaming`
// true inside `ChatSession`, which is driven by a live WS stream; nothing in the tree currently
// exercises `isStreaming` or `steered` (grepped: zero test files reference either), so there is no
// harness to reuse and building one would mean faking the stream for one ordering property. The
// sibling defect made the same call: `loops/loopActionReported.test.ts`'s "a failed nudge KEEPS the
// message and the panel open" is an index-ordering assertion over the source, and this mirrors it.

const CHAT = readFileSync(
  join(process.cwd(), 'src/pages/ChatPage.tsx'),
  'utf8',
)

/** Strip comments so a quoted example in prose cannot satisfy — or break — an assertion. */
const strip = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

/** The `isStreaming` steer branch only. */
function steerBranch(): string {
  const body = strip(CHAT)
  const at = body.indexOf('if (isStreaming) {')
  expect(at, 'the steer branch must be found before it can be measured').toBeGreaterThan(-1)
  // Ends at the `return` that closes the branch.
  const end = body.indexOf('return', at)
  expect(end).toBeGreaterThan(at)
  return body.slice(at, end)
}

describe('a failed mid-stream steer keeps the text the user typed', () => {
  it('the draft is NOT cleared before the request goes out', () => {
    // The defect, stated as the thing that must not come back.
    const fn = steerBranch()
    const send = fn.indexOf('api.sendChat')
    const clear = fn.indexOf('setInput(')
    expect(send, 'the steer send must be in this branch').toBeGreaterThan(-1)
    expect(clear, 'and the branch must still clear the draft somewhere').toBeGreaterThan(-1)
    expect(clear, 'clearing before the send is what destroyed the text').toBeGreaterThan(send)
  })

  it('the clear is guarded, so a composer the user re-typed into is not wiped', () => {
    // 🪤 Without this, the fix for one data-loss bug is another one: a late unconditional clear
    // deletes whatever the user started typing while the steer was in flight.
    const fn = steerBranch()
    expect(fn, 'clear only if the composer still holds exactly what was sent')
      .toMatch(/setInput\(\(cur\) => \(cur === t \? '' : cur\)\)/)
  })

  it('the rejection is reported instead of swallowed', () => {
    const fn = steerBranch()
    expect(fn, 'an empty catch is the defect').not.toMatch(/\.catch\(\(\) => \{\s*\}\)/)
    expect(fn, "and it uses this file's own convention for a user-initiated write")
      .toMatch(/\.catch\(reportActionFailure\(/)
  })

  it('the steered chip still only appears when the server said it steered', () => {
    // The other half: the fix must not start claiming a steer landed when the server said `queued`.
    const fn = steerBranch()
    expect(fn).toMatch(/if \(r\?\.steered\) setSteered\(/)
  })

  it('the reporter is imported, so the catch is a real call and not a stray identifier', () => {
    // A `.catch(reportActionFailure(...))` on an undefined name would throw INSIDE the failure path —
    // turning a silent loss into a crash. tsc would catch it; this states the dependency locally.
    expect(strip(CHAT)).toMatch(/import \{[^}]*\breportActionFailure\b[^}]*\} from '\.\.\/app\/reportingWrite'/)
  })
})

describe("the file no longer claims a safety it does not have", () => {
  it('the "nothing is dropped" sentence is scoped to the two success outcomes', () => {
    // 🔑 The comment was not decoration — it is why the defect survived review. It asserted the
    // branch was safe, so a reader checking "is the draft protected?" found a sentence saying yes.
    // It now says which outcomes it covers, and names the third one.
    expect(CHAT, 'the unqualified claim must not return')
      .not.toMatch(/Either way nothing is dropped/)
    expect(CHAT, 'and the rejection outcome must be named where the claim used to sit')
      .toMatch(/THIRD OUTCOME/)
  })
})
