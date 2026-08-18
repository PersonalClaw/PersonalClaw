import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A permission decision that silently did not register ──────────────────────────────────────────
//
// The sixth shape in this page's write-failure family, and the only one where the lie is about a
// SECURITY POSTURE rather than a value or a piece of work.
//
//     const approve = useCallback((id, action) => {
//       const s = sessionRef.current
//       if (s) api.approve(s, action, id).catch(() => {})        // swallowed
//       …
//       const raised = action === 'yolo' ? 'yolo' : …
//       if (raised) setSelection((sel) => ({ ...sel, approval: raised }))   // ran REGARDLESS
//     }, [])
//
// 🔑 THE MIRROR IS THE DEFECT, NOT JUST THE SWALLOW. That `setSelection` exists to keep the
// Permission-mode pill honest — its own comment says so: *"otherwise the pill keeps claiming 'Normal
// — ask before every tool' while the session silently auto-approves (a dishonest state)"*. But it ran
// whether or not the write landed, so a failed `yolo` left the pill claiming this chat auto-approves
// EVERYTHING while the server was still asking before every tool. **The exact inverse of the state
// that comment sets out to prevent.**
//
// The pill is a mirror of a server flag, so it may only move once the server has the flag. It is now
// gated on the write, and the failure is reported. The cost is one round trip before the pill moves,
// which is the honest trade for a claim about what may run unattended.
//
// 🪤 The card needs nothing here, and that was checked rather than assumed: `ApprovalCard` renders
// from `seg.resolved`, which the BACKEND persists, so an unapproved tool call correctly stays
// unresolved and still asking. That also means a silent failure had a second symptom — the card not
// resolving — which reads as "my click did nothing" (the data-driven shape in
// `tools/toggleFailureReported`). Both symptoms, one missing report.

const SRC = join(process.cwd(), 'src', 'pages', 'ChatPage.tsx')
const raw = readFileSync(SRC, 'utf8')
const scan = raw.replace(/=>/g, '⇒')

/** `approve`'s body, so every assertion below is about THIS control and not the file at large. */
function approveBody(): string {
  const at = raw.indexOf('const approve = useCallback(')
  expect(at, 'the approve callback must exist').toBeGreaterThan(-1)
  const end = raw.indexOf('}, [])', at)
  expect(end, 'the callback must be closed').toBeGreaterThan(at)
  return raw.slice(at, end + 6)
}

describe('a permission decision that fails says so, and moves nothing', () => {
  it('the decision reports its failure with the server’s message', () => {
    expect(approveBody()).toContain(".catch(reportActionFailure('record your decision'))")
  })

  it('the write no longer swallows', () => {
    const body = approveBody().replace(/=>/g, '⇒')
    expect(body, 'a swallowed permission decision tells the user nothing').not.toMatch(
      /api\.approve\([^)]*\)\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/,
    )
  })

  it('the posture pill only moves after the write LANDS', () => {
    const body = approveBody()
    // The mirror must be inside the success continuation, not a sibling statement.
    const then = body.indexOf('.then(')
    const mirror = body.indexOf('setSelection((sel)')
    expect(then, 'the write must have a success continuation').toBeGreaterThan(-1)
    expect(mirror, 'the mirror must exist').toBeGreaterThan(-1)
    expect(mirror, 'the mirror must sit INSIDE .then(), not run unconditionally').toBeGreaterThan(then)
    expect(body.indexOf('.catch('), 'and the failure path comes after it').toBeGreaterThan(mirror)
  })

  it('the raised posture is still derived from the action, not invented', () => {
    // The mapping is the reason the mirror exists at all; gating it must not change WHICH mode a
    // card action implies. `approved`/`rejected` are single-shot and must stay null.
    const body = approveBody()
    for (const pair of [
      "action === 'trust' || action === 'trust_agent' ? 'trust'",
      "action === 'trust_reads' ? 'trust_reads'",
      "action === 'yolo' ? 'yolo'",
    ]) {
      expect(body, `the ${pair} mapping must survive`).toContain(pair)
    }
    expect(body, 'a single-shot decision raises nothing').toMatch(/:\s*null\b/)
  })

  it('the card is left to the backend — it is not cleared optimistically here', () => {
    // If a later pass "helped" by hiding the card on click, a failed decision would erase the only
    // remaining sign that the tool call is still waiting.
    const body = approveBody()
    for (const forbidden of ['setTurns(', 'resolved:', 'setSegments(']) {
      expect(body, `approve must not touch transcript state — found ${forbidden}`).not.toContain(
        forbidden,
      )
    }
    const card = readFileSync(join(process.cwd(), 'src', 'pages', 'chat', 'ApprovalCard.tsx'), 'utf8')
    expect(card, 'the card renders the backend’s outcome').toContain('if (seg.resolved) {')
  })

  it('no OTHER api.approve call in the file swallows — the ratchet', () => {
    const offenders: string[] = []
    for (const m of scan.matchAll(/api\.approve\(/g)) {
      const chain = scan.slice(m.index!, m.index! + 200)
      if (/\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/.test(chain)) {
        offenders.push(`line ${scan.slice(0, m.index).split('\n').length}`)
      }
    }
    expect(offenders).toEqual([])
    // Vacuity: the sweep must actually find the call it is guarding.
    expect([...scan.matchAll(/api\.approve\(/g)].length).toBeGreaterThanOrEqual(1)
  })
})
