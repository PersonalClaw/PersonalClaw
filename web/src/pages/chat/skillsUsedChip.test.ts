import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { insertActivity } from './coalesceReducers'
import {
  hydrateTurns,
  learnedSurface,
  skillsUsedLabel,
  skillsUsedTitle,
  stampActivityOrigin,
  type ActivitySegment,
  type HistMsg,
  type Segment,
  type SkillUsed,
} from './chatTypes'

// ── LV-2 (LEARNING-VISIBILITY T2.1 + T2.2) ────────────────────────────────────────────────
//
// Two additive backend contracts, and the three ways their frontend could be wrong:
//
//  1. THE COUNT. `meta.skills_used` lists only the skills whose content reached the prompt —
//     `admitted` (body) and `reduced` (summary). A REFUSED skill is deliberately absent from
//     the wire: it was NAMED to the agent but never loaded. So the chip's N is the list
//     length, and the two ways to get it wrong are counting a subset (dropping `reduced`,
//     which DID load something) and rendering a "used 0 skills" for a turn the backend
//     said nothing about (the key is omitted, never `[]`).
//
//  2. THE ROUTING. All three learning captures share `activityKind: 'learned'`; only
//     `origin` says which surface can approve or edit the artifact. Wiring one to the wrong
//     page is invisible to types and to a smoke test — the chip is tappable either way.
//
//  3. THE DEGRADE. Every message persisted before T2.2 lacks `origin`, and any future
//     emitter will too. That must leave the chip VISIBLE and merely not a link — not throw,
//     not hide the chip, and above all not guess a surface.
//
// Each block below carries its own vacuity floor (an exclusion, a singular/plural pair, or a
// discrimination assertion), because every positive case here is satisfiable by a constant.

const admitted = (name: string, tokens = 900): SkillUsed => ({ name, state: 'admitted', loaded_tokens: tokens })
const reduced = (name: string, tokens = 120): SkillUsed => ({ name, state: 'reduced', loaded_tokens: tokens })

describe('skillsUsedLabel — the count', () => {
  it('counts every entry the allocator loaded', () => {
    expect(skillsUsedLabel([admitted('a'), admitted('b'), admitted('c')])).toBe('used 3 skills')
  })

  it('counts a `reduced` skill: it loaded a SUMMARY, not nothing', () => {
    // The falsification target. An `admitted`-only filter reads 2 here, and would silently
    // under-report every turn where the allocator had to shrink a skill to fit.
    expect(skillsUsedLabel([admitted('a'), admitted('b'), reduced('c')])).toBe('used 3 skills')
  })

  it('says "skill", singular, for one', () => {
    // Vacuity floor for the label: a hardcoded `used N skills` passes every case above.
    expect(skillsUsedLabel([admitted('only')])).toBe('used 1 skill')
  })

  it('renders NOTHING for an empty list rather than a measured-looking zero', () => {
    // The backend omits `skills_used` entirely when the turn loaded nothing, so "used 0
    // skills" would be a claim it never made.
    expect(skillsUsedLabel([])).toBe('')
  })
})

describe('skillsUsedTitle — the names on hover', () => {
  it('lists the names in the ALLOCATOR’S order, not sorted', () => {
    const t = skillsUsedTitle([admitted('zebra'), admitted('alpha'), admitted('middle')])
    expect(t).toBe('Skills used this turn:\nzebra\nalpha\nmiddle')
    // Vacuity floor: a sort() would produce alpha first, which is what a "tidy" rewrite does.
    expect(t.indexOf('zebra')).toBeLessThan(t.indexOf('alpha'))
  })

  it('marks a `reduced` skill so a summary-only load never reads as a full one', () => {
    const t = skillsUsedTitle([admitted('full-body'), reduced('shrunk')])
    expect(t).toContain('shrunk — summary only')
    // The exclusion is the point: the marker must be SPECIFIC to reduced, or it says nothing.
    expect(t).not.toContain('full-body — summary only')
  })

  it('names an unnamed skill honestly instead of rendering a blank line', () => {
    expect(skillsUsedTitle([{ name: '', state: 'admitted', loaded_tokens: 0 }]))
      .toContain('(unnamed skill)')
  })

  it('is empty for an empty list', () => {
    expect(skillsUsedTitle([])).toBe('')
  })

  it('treats an unknown future state as a full load rather than inventing a marker', () => {
    // `state` is typed as the raw wire string on purpose. Only `reduced` is called out; a
    // state this build has never heard of must not be labelled "summary only".
    expect(skillsUsedTitle([{ name: 'novel', state: 'promoted', loaded_tokens: 10 }]))
      .toBe('Skills used this turn:\nnovel')
  })
})

describe('learnedSurface — a tap lands where the artifact can be approved or edited', () => {
  it('routes a skill-ladder PROPOSAL to the Skills page’s proposals view', () => {
    const s = learnedSurface('proposal')
    expect(s?.href).toBe('#/skills?mode=proposals')
    // Not the bare route: `#/skills` renders Installed skills, which lists no proposal at
    // all. `SkillsPage` selects `SkillProposals` off the `?mode` query param.
    expect(s?.href).not.toBe('#/skills')
  })

  it('routes an after-turn LESSON to the Memory Studio, which reads the lesson store', () => {
    // `run_after_turn_review` calls `service.write_lesson()`, and `MemoryPanel` fetches
    // exactly that store via `api.lessons()` with an editing inspector.
    expect(learnedSurface('lesson')?.href).toBe('#/settings/memory?tab=studio')
    // NOT the Learning page. That page is the `/api/learning/proposals` inbox — a different
    // artifact class (flywheel `lesson_batch` proposals) that can neither show nor edit an
    // after-turn lesson. This exclusion is the whole finding; see the DISCOVERY note.
    expect(learnedSurface('lesson')?.href).not.toContain('/learning')
  })

  it('routes a preference FACET to the Memory Studio', () => {
    expect(learnedSurface('facet')?.href).toBe('#/settings/memory?tab=studio')
  })

  it('discriminates: a proposal and a lesson do NOT land on the same surface', () => {
    // Vacuity floor for the whole block. One hardcoded href satisfies every positive
    // assertion above, and that is precisely the bug T2.2 exists to fix — the row used to
    // link all three origins to Memory, which was right for a facet and wrong for a proposal.
    expect(learnedSurface('proposal')?.href).not.toBe(learnedSurface('lesson')?.href)
  })

  it('every mapped origin carries its own words, so the link never mislabels its target', () => {
    expect(learnedSurface('proposal')?.label).toContain('Skill proposals')
    expect(learnedSurface('lesson')?.label).toContain('lessons')
    expect(learnedSurface('proposal')?.label).not.toBe(learnedSurface('lesson')?.label)
  })

  describe('the degrade', () => {
    it('returns null for an ABSENT origin (every message persisted before T2.2)', () => {
      expect(learnedSurface(undefined)).toBeNull()
      expect(learnedSurface('')).toBeNull()
      expect(learnedSurface(null)).toBeNull()
    })

    it('returns null for an UNRECOGNISED origin rather than guessing a surface', () => {
      // A future fourth emitter arrives here. Sending the user to a page that cannot show
      // its artifact is worse than a chip that simply isn't a link.
      expect(learnedSurface('sop')).toBeNull()
      expect(learnedSurface('PROPOSAL')).toBeNull()  // case is the wire's, not ours to coerce
    })

    it('does not throw on any of them — the chip must still render', () => {
      for (const o of [undefined, null, '', 'sop', 'facet']) {
        expect(() => learnedSurface(o as string | null | undefined)).not.toThrow()
      }
    })
  })
})

describe('stampActivityOrigin — origin survives the LIVE stream, not just a reload', () => {
  // Driven through the REAL `insertActivity`, not a hand-built pair of arrays: the helper's
  // whole correctness rests on that function's actual splice/early-out behaviour, and a
  // fabricated `next` would prove nothing about it.
  const learned = (t: string) => ['learned', t] as const

  it('stamps the segment insertActivity spliced in', () => {
    const prev: Segment[] = []
    const [k, t] = learned('Learned: prefers tabs')
    const next = stampActivityOrigin(prev, insertActivity(prev, t, k, false), 'facet')
    const seg = next.find((s) => s.kind === 'activity') as ActivitySegment
    expect(seg.origin).toBe('facet')
    expect(seg.activityKind).toBe('learned')
  })

  it('stamps the NEW line only, leaving an earlier learned line’s origin alone', () => {
    // The falsification target for "which segment": stamping the first match instead of the
    // new one would rewrite the previous turn-step's origin and route its chip elsewhere.
    const first: Segment[] = stampActivityOrigin([], insertActivity([], 'Learned: A', 'learned', false), 'facet')
    const second = stampActivityOrigin(first, insertActivity(first, 'Learned: B', 'learned', false), 'proposal')
    const segs = second.filter((s) => s.kind === 'activity') as ActivitySegment[]
    expect(segs).toHaveLength(2)
    expect(segs[0].origin).toBe('facet')
    expect(segs[1].origin).toBe('proposal')
  })

  it('is a no-op when insertActivity declined to insert (tool cards win)', () => {
    // insertActivity's early-out returns the SAME array. Stamping anything here would put an
    // origin on an unrelated pre-existing line.
    const withTool: Segment[] = [{ kind: 'tool', id: 't1', tool: 'Read', done: false } as Segment]
    const next = stampActivityOrigin(withTool, insertActivity(withTool, 'Learned: X', 'learned', false), 'lesson')
    expect(next).toBe(withTool)
    expect(next.some((s) => s.kind === 'activity')).toBe(false)
  })

  it('is a no-op for an ABSENT origin, so a pre-T2.2 frame stamps nothing', () => {
    const prev: Segment[] = []
    const next = stampActivityOrigin(prev, insertActivity(prev, 'Learned: Y', 'learned', false), '')
    const seg = next.find((s) => s.kind === 'activity') as ActivitySegment
    expect(seg).toBeDefined()          // the line still renders…
    expect(seg.origin).toBeUndefined() // …it just isn't routable, which learnedSurface handles
    expect(learnedSurface(seg.origin)).toBeNull()
  })

  it('an unstamped line degrades, a stamped one routes — the two halves meet here', () => {
    // The end-to-end assertion the two contracts exist for: a wire frame carrying `origin`
    // produces a tappable chip, and the same frame without it does not.
    const mk = (origin?: string) => {
      const next = stampActivityOrigin([], insertActivity([], 'Learned: Z', 'learned', false), origin)
      return (next.find((s) => s.kind === 'activity') as ActivitySegment).origin
    }
    expect(learnedSurface(mk('proposal'))?.href).toBe('#/skills?mode=proposals')
    expect(learnedSurface(mk(undefined))).toBeNull()
  })
})

describe('hydrateTurns — skills_used reaches the turn on reload', () => {
  const msg = (role: string, content: string, meta?: HistMsg['meta']): HistMsg =>
    ({ role, content, ts: `t-${content}`, ...(meta ? { meta } : {}) })

  it('carries meta.skills_used onto the assistant turn', () => {
    const turns = hydrateTurns([
      msg('user', 'hi'),
      msg('assistant', 'hello', { skills_used: [admitted('api-design'), reduced('runbook')] }),
    ])
    const a = turns.find((t) => t.role === 'assistant')
    expect(a?.skillsUsed).toEqual([admitted('api-design'), reduced('runbook')])
    expect(skillsUsedLabel(a!.skillsUsed!)).toBe('used 2 skills')
  })

  it('leaves it ABSENT on a turn with no meta — the pre-T2.1 case', () => {
    const turns = hydrateTurns([msg('user', 'hi'), msg('assistant', 'hello')])
    expect(turns.find((t) => t.role === 'assistant')?.skillsUsed).toBeUndefined()
  })

  it('leaves it absent for an empty array too, so no chip renders', () => {
    const turns = hydrateTurns([
      msg('user', 'hi'),
      msg('assistant', 'hello', { skills_used: [] }),
    ])
    expect(turns.find((t) => t.role === 'assistant')?.skillsUsed).toBeUndefined()
  })

  it('does not disturb the citations graft it sits beside', () => {
    // Both grafts read the same `meta`; a turn carrying only citations must not acquire a
    // skills list, and vice versa.
    const turns = hydrateTurns([
      msg('user', 'hi'),
      msg('assistant', 'hello', { memory_citations: [{ n: 1, id: 'e1' }] }),
    ])
    const a = turns.find((t) => t.role === 'assistant')
    expect(a?.citations).toHaveLength(1)
    expect(a?.skillsUsed).toBeUndefined()
  })
})

// ── The call sites ────────────────────────────────────────────────────────────────────────
//
// The helpers above are pure and provable; a control can still ship INERT — correct logic
// that no surface calls. The atom's done_when names two surfaces ("run/loop panel"), so both
// wirings are asserted here. Scanned as JSX ATTRIBUTE/EXPRESSION forms, not bare identifiers:
// this file's own prose and the source comments both mention the helper names, and a bare
// substring scan would pass on a comment alone.
describe('the chip is wired at both surfaces (not an inert helper)', () => {
  const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8')
  const chatPage = read('../ChatPage.tsx')
  const cockpit = read('../loops/LoopCockpitPage.tsx')
  // The ledger moved out of `ChatPage.tsx` so its one-action reach could be mounted and proved
  // (`contextLedgerReach.test.tsx`); these scans follow the code rather than the old address.
  const ledger = read('./ContextLedger.tsx')

  it('the chat run panel renders the label + the names on hover', () => {
    expect(chatPage).toContain('title={skillsUsedTitle(skills)}')
    expect(chatPage).toContain('{skillsUsedLabel(skills)}')
    // Gated on a non-empty list, so a turn that loaded nothing shows no chip.
    expect(chatPage).toContain('skillsUsed.length > 0 && <SkillsUsedChip')
  })

  it('the loop cockpit renders the same two helpers in its status bar', () => {
    expect(cockpit).toContain('text={skillsUsedLabel(skillsUsed)}')
    expect(cockpit).toContain('title={skillsUsedTitle(skillsUsed)}')
    expect(cockpit).toContain('skillsUsed.length > 0 && <MetaPill')
  })

  it('the cockpit reads the meta over the EXISTING session endpoint, adding no channel', () => {
    // The acceptance clause is "zero new WS/SSE channels". The cockpit's own live stream
    // carries no message meta, so it reads the worker transcript through the REST endpoint
    // ChatPage already uses.
    expect(cockpit).toContain('api.chatSessionDetail(workerKey)')
    expect(cockpit).toContain('m.meta?.skills_used')
  })

  it('the learned row routes on origin instead of one hardcoded link', () => {
    // The WS handler must actually stamp the wire's `origin`, and the ledger must read it off
    // the same segment — otherwise `learnedSurface` is only ever called with `undefined` and
    // every chip degrades, which would look exactly like "old messages" forever.
    expect(chatPage).toContain('stampActivityOrigin(segs, insertActivity(')
    expect(chatPage).toContain("ledger.learnedOrigin = (s as ActivitySegment).origin")
    expect(ledger).toContain('learnedSurface(learnedOrigin)')
    expect(ledger).toContain('<TextLink href={surface.href}>')
    // Vacuity floor for this whole block: the pre-LV-2 hardcoded link must be GONE. Without
    // this, the three positive scans above pass while the old unconditional Memory link is
    // still what actually renders.
    expect(ledger).not.toContain('<TextLink href="#/settings/memory">Manage in Memory')
  })

  it('the extracted ledger is still MOUNTED by the page (extraction is not deletion)', () => {
    // Moving `ContextLedger` into its own module made it mountable in a test; it must also
    // still be rendered in production, with the origin the WS handler stamped handed through.
    // Without this, `contextLedgerReach.test.tsx` could stay green over a component no surface
    // renders — the exact inert-control shape this block exists to catch.
    expect(chatPage).toContain('<ContextLedger fed={ledger.fed}')
    expect(chatPage).toContain('learnedOrigin={ledger.learnedOrigin}')
    expect(chatPage).toContain("import { ContextLedger } from './chat/ContextLedger'")
    // And ChatPage no longer carries a second, private copy of it.
    expect(chatPage).not.toContain('function ContextLedger(')
  })
})
