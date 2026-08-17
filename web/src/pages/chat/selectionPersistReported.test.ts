import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The composer must not show a selection the session does not have ──────────────────────────────
//
// `applySelection` flips the composer's local state FIRST and then fires the persistence calls:
//
//     const nextSel = { ...selection, ...patch }
//     setSelection(nextSel)                                    // optimistic
//     api.setSessionAgent(s, patch.agent).catch(() => {})       // swallowed
//     api.setApprovalMode(...).catch(() => {})                  // swallowed
//     …
//
// That is exactly the shape `saveFailureReported` names — "the local state flips first, then the PUT goes
// out, and a swallowed rejection is a lie, because the control is left showing a value the server
// refused" — but in `ChatPage`, outside the settings panels that rail sweeps.
//
// 🔑 AND THE STAKES ARE HIGHER HERE THAN FOR A SETTINGS TOGGLE. The composer can end up showing an agent,
// a model, or an APPROVAL MODE the session is not using, so the user's next message runs under settings
// they can see and did not get. Approval mode is the one with a safety cost rather than a cosmetic one.
//
// Eleven sites, two functions, one concern: `applySelection` (7) and the creation-time persistence of a
// pre-start pick (4). All now route through ONE reporter, because eleven catch blocks is how one gets
// missed.
//
// 🪤 REVERTING `setSelection` WAS CONSIDERED AND REJECTED. It is the tempting "fix the lie" move, but the
// family's remedy for this shape is to TELL, not to fight input the user is still editing — and these
// calls are deliberately fire-and-forget so the composer never blocks on a round-trip. Recorded so the
// next pass does not "finish the job" by adding a revert.

const SRC = readFileSync(join(process.cwd(), 'src/pages/ChatPage.tsx'), 'utf8')
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

/** Every api call that persists a piece of the composer's selection onto a session. */
const SELECTION_WRITES = [
  'setSessionAcpAgent', 'setSessionAgent', 'setSessionModel',
  'setApprovalMode', 'setTaskMode', 'setReasoningEffort',
]

describe('a selection that fails to persist tells the user', () => {
  it('the reporter exists and carries the server’s message', () => {
    expect(CODE).toMatch(/const persistSelection = <T,>\(what: string, p: Promise<T>\)/)
    expect(CODE).toMatch(/notify\(`Couldn't apply \$\{what\} to this session: \$\{String\(\(e as Error\)\?\.message \|\| e\)\}`, 'error'\)/)
  })

  it('no selection write swallows its rejection', () => {
    // The ratchet. Any of these six reintroduced with a bare catch fails here, wherever in the file.
    const offenders: string[] = []
    for (const call of SELECTION_WRITES) {
      for (const m of CODE.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))) {
        const chain = CODE.slice(m.index!, m.index! + 320)
        if (/\.catch\(\(\)\s*=>\s*\{\s*\}\)/.test(chain)) offenders.push(`${call} @${CODE.slice(0, m.index).split('\n').length}`)
      }
    }
    expect(offenders, 'a swallowed selection write leaves the composer lying').toEqual([])
  })

  it('every selection write goes through the one reporter', () => {
    // Not just "does not swallow" — they must share the reporter, or the copy and the level drift.
    let routed = 0
    for (const m of CODE.matchAll(/persistSelection\('([^']+)', api\.(\w+)\(/g)) {
      expect(SELECTION_WRITES, `unexpected call routed: ${m[2]}`).toContain(m[2])
      routed++
    }
    expect(routed, 'selection writes routed through persistSelection').toBe(11)
  })

  it('each report names WHICH pick failed', () => {
    // "Couldn't apply this session" would be useless when five things can fail independently.
    for (const what of ['this agent', 'this model', 'this approval mode', 'this task mode', 'this reasoning effort'])
      expect(CODE, `missing a report for ${what}`).toContain(`persistSelection('${what}'`)
  })

  it('the composer still updates OPTIMISTICALLY — the fix reports, it does not block', () => {
    // If this ever awaits the round-trip, the composer stalls on every pick and the reasoning in the
    // header ("deliberately fire-and-forget") is stale.
    expect(CODE).toMatch(/const nextSel = \{ \.\.\.selection, \.\.\.patch \}\s*\n\s*setSelection\(nextSel\)/)
  })

  it('and it does NOT revert the selection — the deliberate non-fix', () => {
    // Recorded as a decision, not an omission. A revert would fight input the user is still editing.
    const at = CODE.indexOf('const persistSelection')
    const body = CODE.slice(at, at + 320)
    expect(body, 'the reporter must not roll local state back').not.toMatch(/setSelection\(/)
  })

  it('the consent-gated escalation reports AND stops, rather than sending anyway', () => {
    // 🪤 THE TWELFTH SITE, which my own enumeration missed and the ratchet above caught.
    // `switchToAgentAndRun` is the "Switch to Agent & run it" click — the consent that makes an escalation
    // out of a read-only posture safe. Its own comment states the invariant ("the backend flip is awaited
    // ... so the continuation turn runs under Agent's gate"), and it used to swallow the flip and send
    // anyway, running the turn under the posture the user was escalating OUT of while the composer showed
    // Agent. Proceeding on a failed flip is the one outcome the click did not authorise.
    const at = CODE.indexOf('async function switchToAgentAndRun')
    const body = CODE.slice(at, at + 700)
    expect(body, 'the flip must be captured').toMatch(/try \{ await api\.setTaskMode\('agent', s\) \}/)
    expect(body, 'it must report').toMatch(/notify\(`Couldn't switch this session to Agent/)
    // 🪤 No `\n` in this regex: the generator that wrote this file turned an escaped newline into a real
    // one and split the literal across lines. `[\s\S]` says the same thing and cannot be mangled.
    expect(body, 'and it must NOT fall through to the send').toMatch(/return[\s\S]{0,12}\}[\s\S]{0,12}\}/)
    const sendAt = body.indexOf('await send(text)')
    const returnAt = body.indexOf('return')
    expect(returnAt, 'the early return precedes the send').toBeLessThan(sendAt)
  })

  it('notify is the file’s own idiom, not a new import — the convergence claim', () => {
    expect(SRC).toMatch(/import \{ notify \} from '\.\.\/app\/appSdk'/)
    const uses = (CODE.match(/notify\(/g) || []).length
    expect(uses, 'ChatPage already reported failures this way').toBeGreaterThan(5)
  })
})
