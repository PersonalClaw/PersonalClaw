import { describe, it, expect } from 'vitest'
import { approvalToastMessage } from './approvalToast'

// OU-8 — the COMPACT form of the approval brief. The out-of-context nudge must carry the
// same first fact the card leads with (what the call can touch) without becoming a second
// approval renderer: no verbs, no scope, no decision.

describe('approvalToastMessage', () => {
  it('names who is asking, the tool, what it can touch, and where to answer', () => {
    const msg = approvalToastMessage({ who: 'A subagent', tool: 'bash', session: 'main', risk: 'destructive' })
    expect(msg).toBe('A subagent needs approval to run bash (runs a command) — open main to respond.')
  })

  it('uses the SAME facet words as the card, so the two cannot drift', () => {
    expect(approvalToastMessage({ who: 'Another chat session', tool: 'web_fetch', session: 's1', risk: 'caution' }))
      .toContain('(uses the network)')
  })

  it('omits the clause entirely when nothing is established', () => {
    // Not "(nothing established)": a reader hears that as "nothing happens". Silence is the
    // unknown channel, exactly as it is on the card.
    const msg = approvalToastMessage({ who: 'A background task', tool: 'ponder', session: 's1' })
    expect(msg).toBe('A background task needs approval to run ponder — open s1 to respond.')
    expect(msg).not.toMatch(/\(/)
  })

  it('works without a risk (the field is absent on some paths) and claims nothing extra', () => {
    // A read VERB in the name establishes the read on its own, so the clause survives a
    // missing risk...
    const named = approvalToastMessage({ who: 'A subagent', tool: 'read_file', session: 's1' })
    expect(named).toContain('reads only')
    expect(named).not.toContain('writes files')
    // ...but a verbless read (`grep`) establishes nothing without the risk field, and the
    // toast then says nothing rather than guessing. Guessing is what OU-7 refused to do.
    expect(approvalToastMessage({ who: 'A subagent', tool: 'grep', session: 's1' })).not.toMatch(/\(/)
  })

  it('never advocates and never gives an instruction beyond where to answer', () => {
    for (const risk of ['safe', 'caution', 'destructive'] as const) {
      const msg = approvalToastMessage({ who: 'A subagent', tool: 'bash', session: 'main', risk })
      expect(msg.length, risk).toBeGreaterThan(40)  // vacuity guard
      for (const advocacy of [/safe to/i, /recommend/i, /harmless/i, /go ahead/i, /allow it/i, /just approve/i]) {
        expect(msg, `${risk} ${advocacy}`).not.toMatch(advocacy)
      }
    }
  })
})
