import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A dismissal whose failure arrives LATER ────────────────────────────────────────────────────────
//
// The seventh shape in this write-failure family, and the only one where the user cannot connect the
// symptom to the click. Four dismissals swallowed their rejection, in two sub-shapes:
//
//   THE CHIPS clear locally, so the dismissal LOOKS settled and is not.
//     `OrganizeChip.decline`  → `session_organize.record_decline` is what suppresses the proposal,
//                               documented "so it is never proposed again". A failed write means the
//                               SAME proposal returns on the next scan — the nag the backend's own
//                               tests call "worse than no feature".
//     `RoutingChip.dismiss`   → bumps a counter that mutes the agent at a threshold. A failed write
//                               means the suggestion keeps coming and never mutes, so the user's
//                               repeated dismissals quietly amount to nothing.
//
//   THE TIPS never cleared at all, so the click read as doing nothing.
//     `DashboardLive` / `DiscoverPage` → both already gated their refetch on `.then()`; only the
//                               failure was silent, so the tip simply stayed put with nothing said.
//
// 🔑 THE CHIPS STILL CLEAR, DELIBERATELY. A dismissal is a request to get something out of the way, so
// refusing to hide it would fight the click; the report is what makes a later reappearance explicable.
// That is the OPPOSITE ruling from `chat/approvalDecisionReported`, where the pill may not move until
// the server agrees — and the distinction is what the control is claiming. A permission pill asserts a
// server fact; a dismissed chip only hides a suggestion. Both are pinned so a later pass cannot
// "normalise" one into the other.
//
// 🪤 NOT FIXED HERE: `RoutingChip`'s second call, `api.recordFeedback(...)`, also swallows. It is a
// different concern (feedback capture, not dismissal) with its own roadmap owner, and folding it in
// would widen this past one thing. Recorded in the handoff.

const F = (rel: string) => readFileSync(join(process.cwd(), 'src', 'pages', rel), 'utf8')
const strip = (s: string) =>
  s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const SITES: Array<[string, string, string]> = [
  ['chat/OrganizeChip.tsx', 'organizeDecline', 'decline that suggestion'],
  ['chat/RoutingChip.tsx', 'routingDismiss', 'dismiss the ${suggestion.agent} suggestion'],
  ['dashboard/DashboardLive.tsx', 'dismissDiscoverTip', 'dismiss that tip'],
  ['discover/DiscoverPage.tsx', 'dismissDiscoverTip', 'dismiss that tip'],
]

describe('a dismissal that fails says so', () => {
  it('every site uses the SHARED reporter and keeps no local copy', () => {
    for (const [rel] of SITES) {
      const src = strip(F(rel))
      expect(src, `${rel} must import the shared contract`).toMatch(
        /import \{ reportingWrite \} from '\.\.\/\.\.\/app\/reportingWrite'/,
      )
      const local = [...src.matchAll(/(function|const)\s+reportingWrite\b\s*[=(]/g)]
      expect(local.length, `${rel}: a page-local copy would shadow the shared one`).toBe(0)
    }
  })

  it('no dismissal swallows its rejection — the ratchet, keyed on the WRITES', () => {
    // 🪤 Keyed on the population (each `api.<call>(`), never on the compliant pattern: a sweep that
    // iterates the FIXED form can only ever visit sites that already comply, which is how an earlier
    // rail in this family missed a dropped guard.
    const offenders: string[] = []
    for (const [rel, call] of SITES) {
      const scan = strip(F(rel)).replace(/=>/g, '⇒')
      const found = [...scan.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))]
      expect(found.length, `${rel}: api.${call} must still be called`).toBeGreaterThan(0)
      for (const m of found) {
        const chain = scan.slice(m.index!, m.index! + 200)
        if (/\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/.test(chain)) offenders.push(`${rel}:${call}`)
      }
    }
    expect(offenders, 'a silent dismissal fails later, where the user cannot trace it').toEqual([])
  })

  it('every dismissal routes through the reporter, and names its subject', () => {
    for (const [rel, call, what] of SITES) {
      const src = strip(F(rel))
      const at = src.indexOf(`api.${call}(`)
      const before = src.slice(Math.max(0, at - 240), at)
      expect(before, `${rel}: api.${call} must be wrapped`).toContain('reportingWrite(')
      expect(src, `${rel}: the message must name what was dismissed`).toContain(what)
    }
  })

  it('the TIPS still gate their refetch on success', () => {
    // These were already right in structure; the fix must not lose that.
    for (const rel of ['dashboard/DashboardLive.tsx', 'discover/DiscoverPage.tsx']) {
      const src = strip(F(rel))
      const at = src.indexOf('reportingWrite(')
      const after = src.slice(at, at + 320)
      expect(after, `${rel}: the guard must return`).toMatch(/\)\)\) return/)
      const guard = after.indexOf(')) return')
      const refetch = after.search(/loadDiscover\(|refresh\(/)
      expect(refetch, `${rel}: a refetch must follow`).toBeGreaterThan(-1)
      expect(guard, `${rel}: and the guard must precede it`).toBeLessThan(refetch)
    }
  })

  it('the CHIPS still hide on a failure — the opposite ruling, pinned', () => {
    // If a later pass "fixed" these to keep the chip visible, it would be fighting the click. The
    // reasoning lives in this file's header; this is the assertion that makes changing it deliberate.
    const organize = strip(F('chat/OrganizeChip.tsx'))
    const at = organize.indexOf('reportingWrite(')
    const after = organize.slice(at, at + 260)
    expect(after, 'the chip clears regardless of the outcome').toContain('setProposal(null)')
    expect(after, 'so no guard may gate the hide').not.toMatch(/\)\)\) return/)

    const routing = strip(F('chat/RoutingChip.tsx'))
    const rAt = routing.indexOf('reportingWrite(')
    expect(routing.slice(rAt, rAt + 520), 'the routing chip hides too').toContain('onDismiss()')
  })

  it('the accept paths keep their own reporting — this converged onto them', () => {
    // Both chips already told the user when the POSITIVE action failed. The dismissal was the half
    // that did not, which is why the fix reads as completing a pattern rather than inventing one.
    expect(F('chat/OrganizeChip.tsx')).toContain("notify(`Couldn't organize:")
    expect(F('chat/RoutingChip.tsx')).toContain("notify(`Couldn't route:")
  })
})
