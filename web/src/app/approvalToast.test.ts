import { describe, it, expect } from 'vitest'
import { approvalToastMessage } from './approvalToast'
import type { BlastRadius } from '../pages/chat/approvalMeta'

// The COMPACT form of the approval brief. The out-of-context nudge must carry the
// same first fact the card leads with (what the call can touch) without becoming a second
// approval renderer: no verbs, no scope, no decision.

const radius = (over: Partial<BlastRadius>): BlastRadius => ({
  writes: false, network: false, shell: false, saysReadOnly: false, readOnly: false, ...over,
})

describe('approvalToastMessage', () => {
  it('names who is asking, the tool, what it can touch, and where to answer', () => {
    const msg = approvalToastMessage({ who: 'A subagent', tool: 'bash', session: 'main', blastRadius: radius({ shell: true }) })
    expect(msg).toBe('A subagent needs approval to run bash (runs a command) — open main to respond.')
  })

  it('says what the backend established the call does, not what its name suggests', () => {
    // The same `bash`: a command that writes a file, and one that only reads.
    expect(approvalToastMessage({ who: 'A subagent', tool: 'bash', session: 'main', blastRadius: radius({ writes: true }) }))
      .toContain('(writes files)')
    expect(approvalToastMessage({ who: 'A subagent', tool: 'bash', session: 'main', blastRadius: radius({ readOnly: true }) }))
      .toContain('(reads only)')
  })

  it("sends a room member's ask to its room, which the sentence names by its title", () => {
    // `who` is the registry's words for where the call came from (`source_label`), so the room and
    // the member are named there; the place to answer is the room, never the member's session key.
    const msg = approvalToastMessage({
      who: 'Room “Is the demo worth it?” · member “talk-editor”',
      tool: 'edit_file',
      session: 'room:is-the-demo-worth-it:talk-editor',
      blastRadius: radius({ writes: true }),
    })
    expect(msg).toBe(
      'Room “Is the demo worth it?” · member “talk-editor” needs approval to run edit_file (writes files) — open the room to respond.',
    )
  })

  it('uses the SAME facet words as the card, so the two cannot drift', () => {
    expect(approvalToastMessage({ who: 'Another chat session', tool: 'web_fetch', session: 's1', blastRadius: radius({ network: true }) }))
      .toContain('(uses the network)')
  })

  it('omits the clause entirely when nothing is established', () => {
    // Not "(nothing established)": a reader hears that as "nothing happens". Silence is the
    // unknown channel, exactly as it is on the card.
    const msg = approvalToastMessage({ who: 'A background task', tool: 'ponder', session: 's1' })
    expect(msg).toBe('A background task needs approval to run ponder — open s1 to respond.')
    expect(msg).not.toMatch(/\(/)
  })

  it('works without a radius (a row written before one was composed) and claims nothing', () => {
    // A name establishes nothing — `task_list_create` carries "list" — so without the radius the
    // toast says nothing about what the call touches, whatever the tool is called.
    for (const tool of ['read_file', 'grep', 'task_list_create']) {
      expect(approvalToastMessage({ who: 'A subagent', tool, session: 's1' })).not.toMatch(/\(/)
    }
  })

  it('never advocates and never gives an instruction beyond where to answer', () => {
    for (const blastRadius of [radius({ readOnly: true }), radius({ writes: true }), radius({ shell: true })]) {
      const shown = JSON.stringify(blastRadius)
      const msg = approvalToastMessage({ who: 'A subagent', tool: 'bash', session: 'main', blastRadius })
      expect(msg.length, shown).toBeGreaterThan(40)  // vacuity guard
      for (const advocacy of [/safe to/i, /recommend/i, /harmless/i, /go ahead/i, /allow it/i, /just approve/i]) {
        expect(msg, `${shown} ${advocacy}`).not.toMatch(advocacy)
      }
    }
  })
})
