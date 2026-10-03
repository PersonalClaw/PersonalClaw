import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { insertActivity } from './coalesceReducers'
import {
  hydrateTurns,
  joinedSkillsOf,
  learnedSurface,
  skillsUsedLabel,
  skillsUsedTitle,
  stampActivityOrigin,
  type ActivitySegment,
  type HistMsg,
  type Segment,
  type SkillUsed,
} from './chatTypes'

// ── LV-2 ──────────────────────────────────────────────────────────────────────────────────
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

describe('skillsUsedLabel — the names, and the count', () => {
  it('names every entry the allocator loaded', () => {
    expect(skillsUsedLabel([admitted('a'), admitted('b'), admitted('c')])).toBe('used skills a, b, c')
  })

  it('names a `reduced` skill: it loaded a SUMMARY, not nothing', () => {
    // The falsification target. An `admitted`-only filter drops `c` here, and would silently
    // under-report every turn where the allocator had to shrink a skill to fit.
    expect(skillsUsedLabel([admitted('a'), admitted('b'), reduced('c')])).toBe('used skills a, b, c')
  })

  it('says "skill", singular, for one', () => {
    // Vacuity floor for the label: a hardcoded plural passes every case above.
    expect(skillsUsedLabel([admitted('only')])).toBe('used skill only')
  })

  it('names a skill by the last part of its key; the whole key is in the hover', () => {
    const used = [admitted('imported/claude_code/trip-research')]
    expect(skillsUsedLabel(used)).toBe('used skill trip-research')
    expect(skillsUsedTitle(used)).toContain('imported/claude_code/trip-research')
  })

  it('names three and counts the rest', () => {
    const used = ['a', 'b', 'c', 'd', 'e'].map((n) => admitted(n))
    expect(skillsUsedLabel(used)).toBe('used skills a, b, c +2 more')
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

  it('routes a learned preference to the list that can pin or forget it, at its row', () => {
    // Not the Studio: it lists the same row as a raw fact, with no Forget.
    expect(learnedSurface('facet', 'pref.facet.style.abc')?.href)
      .toBe('#/settings/memory?tab=settings&pref=pref.facet.style.abc')
    expect(learnedSurface('facet')?.href).toBe('#/settings/memory?tab=settings')
    expect(learnedSurface('facet')?.label).toContain('Learned preferences')
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
    it('returns null for an ABSENT origin (every message persisted)', () => {
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
    const next = stampActivityOrigin(withTool, insertActivity(withTool, 'Hook h: injected 9 chars', 'hook', false), 'lesson')
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

describe('joinedSkillsOf — the one reader of the record, for the chat and the loop cockpit', () => {
  it('reads the message that started a turn, whatever started it', () => {
    for (const role of ['user', 'nudge', 'inject', 'subagent']) {
      expect(joinedSkillsOf({ role, meta: { skills_used: [admitted('runbook')] } })?.map((s) => s.name), role)
        .toEqual(['runbook'])
    }
  })

  it('reads nothing off a message that started no turn', () => {
    // An answer's meta is where the record used to be; nothing writes it there now.
    for (const role of ['assistant', 'tool', 'system', 'error']) {
      expect(joinedSkillsOf({ role, meta: { skills_used: [admitted('runbook')] } }), role).toBeUndefined()
    }
  })

  it('is undefined for an absent or an empty list, so no chip renders', () => {
    expect(joinedSkillsOf({ role: 'user' })).toBeUndefined()
    expect(joinedSkillsOf({ role: 'nudge', meta: {} })).toBeUndefined()
    expect(joinedSkillsOf({ role: 'user', meta: { skills_used: [] } })).toBeUndefined()
  })
})

describe('hydrateTurns — skills_used reaches the turn on reload', () => {
  const msg = (role: string, content: string, meta?: HistMsg['meta']): HistMsg =>
    ({ role, content, ts: `t-${content}`, ...(meta ? { meta } : {}) })

  it("carries the user message's meta.skills_used onto the answer that follows it", () => {
    const turns = hydrateTurns([
      msg('user', 'hi', { skills_used: [admitted('api-design'), reduced('runbook')] }),
      msg('assistant', 'hello'),
    ])
    const a = turns.find((t) => t.role === 'assistant')
    expect(a?.skillsUsed).toEqual([admitted('api-design'), reduced('runbook')])
    expect(skillsUsedLabel(a!.skillsUsed!)).toBe('used skills api-design, runbook')
  })

  it('shows it on a turn that only called tools and never said a word', () => {
    // 🔴 The case the record moved for: it rode the assistant message whose TEXT settled, so a
    // turn of nothing but tool calls — or one stopped mid-way — never named the skill that
    // joined it.
    const turns = hydrateTurns([
      msg('user', 'set it up', { skills_used: [admitted('imported/claude_code/trip-research')] }),
      { role: 'tool', content: 'automation_create', ts: 't-tool', meta: { tool_call_id: 'c1', done: true } },
    ])
    const a = turns.find((t) => t.role === 'assistant')
    expect(a?.skillsUsed?.map((s) => s.name)).toEqual(['imported/claude_code/trip-research'])
  })

  it('shows it on a turn read while it is still running, before its answer began', () => {
    const turns = hydrateTurns([msg('user', 'go', { skills_used: [admitted('runbook')] })], true)
    expect(turns[turns.length - 1].role).toBe('assistant')
    expect(turns[turns.length - 1].skillsUsed?.map((s) => s.name)).toEqual(['runbook'])
  })

  it('shows it on a turn stopped before it said anything, however the transcript goes on', () => {
    // A stop before the first word leaves the user's message and the stop's own record — no
    // answer, no tool call. The live page showed the chip on an empty answer; a reload must too.
    const stop = { role: 'system', content: '{"kind": "stop_event", "state": "stopped"}', ts: 't-stop' }
    const last = hydrateTurns([
      msg('user', 'cut it', { skills_used: [admitted('release')] }),
      stop,
    ])
    expect(last.map((t) => t.role)).toEqual(['user', 'assistant'])
    expect(last[1].skillsUsed?.map((s) => s.name)).toEqual(['release'])

    const middle = hydrateTurns([
      msg('user', 'cut it', { skills_used: [admitted('release')] }),
      stop,
      msg('user', 'never mind'),
      msg('assistant', 'ok'),
    ])
    expect(middle.map((t) => t.role)).toEqual(['user', 'assistant', 'user', 'assistant'])
    expect(middle[1].skillsUsed?.map((s) => s.name)).toEqual(['release'])
    expect(middle[3].skillsUsed).toBeUndefined()
  })

  it("shows a turn's skills when a loop's nudge, an automation or a subagent's report started it", () => {
    // Those rows are not rendered and their answer joins the answer before it — which is where
    // the live page put the chip, so a reload puts it there too, with THAT turn's skills.
    for (const role of ['nudge', 'inject', 'subagent']) {
      const turns = hydrateTurns([
        msg('user', 'hi', { skills_used: [admitted('runbook')] }),
        msg('assistant', 'one'),
        msg(role, '[auto-nudge cycle 2]\nkeep going', { skills_used: [admitted('release-notes')] }),
        msg('assistant', 'two'),
      ])
      expect(turns.map((t) => t.role), role).toEqual(['user', 'assistant'])
      expect(turns[1].skillsUsed?.map((s) => s.name), role).toEqual(['release-notes'])
    }
    // First in a chat, it opens the answer itself.
    const first = hydrateTurns([
      msg('nudge', '[auto-nudge cycle 1]\nstart', { skills_used: [admitted('release-notes')] }),
      msg('assistant', 'started'),
    ])
    expect(first.map((t) => t.role)).toEqual(['assistant'])
    expect(first[0].skillsUsed?.map((s) => s.name)).toEqual(['release-notes'])
  })

  it('a stopped turn keeps its skills when a nudge starts the next turn', () => {
    // The stopped turn got no answer, so its skills were still waiting for one when the nudge's
    // row came: they go on the empty answer the live page showed, rather than being dropped.
    const turns = hydrateTurns([
      msg('user', 'cut it', { skills_used: [admitted('release')] }),
      msg('nudge', '[auto-nudge cycle 2]\ngo on'),
      msg('assistant', 'two'),
    ])
    expect(turns.map((t) => t.role)).toEqual(['user', 'assistant'])
    expect(turns[1].skillsUsed?.map((s) => s.name)).toEqual(['release'])
  })

  it("does not carry one turn's skills onto the next turn", () => {
    const turns = hydrateTurns([
      msg('user', 'first', { skills_used: [admitted('runbook')] }),
      msg('assistant', 'one'),
      msg('user', 'second'),
      msg('assistant', 'two'),
    ])
    const answers = turns.filter((t) => t.role === 'assistant')
    expect(answers[0].skillsUsed?.map((s) => s.name)).toEqual(['runbook'])
    expect(answers[1].skillsUsed).toBeUndefined()
  })

  it('leaves it ABSENT on a turn with no meta', () => {
    const turns = hydrateTurns([msg('user', 'hi'), msg('assistant', 'hello')])
    expect(turns.find((t) => t.role === 'assistant')?.skillsUsed).toBeUndefined()
  })

  it('leaves it absent for an empty array too, so no chip renders', () => {
    const turns = hydrateTurns([
      msg('user', 'hi', { skills_used: [] }),
      msg('assistant', 'hello'),
    ])
    expect(turns.find((t) => t.role === 'assistant')?.skillsUsed).toBeUndefined()
  })

  it('does not disturb the citations graft it sits beside', () => {
    // A turn carrying only citations must not acquire a skills list, and vice versa.
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
// that no surface calls. The change's acceptance criterion names two surfaces ("run/loop panel"), so both
// wirings are asserted here. Scanned as JSX ATTRIBUTE/EXPRESSION forms, not bare identifiers:
// this file's own prose and the source comments both mention the helper names, and a bare
// substring scan would pass on a comment alone.
describe('the chip is wired at both surfaces (not an inert helper)', () => {
  const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8')
  const chatPage = read('../ChatPage.tsx')
  const cockpit = read('../loops/LoopCockpitPage.tsx')
  // The activity composition lives with the text-run owner, which takes its live-run decision
  // at dispatch; the WS handler hands it the wire's origin.
  const textRunOwner = read('./coalesceReducers.ts')
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
    // The acceptance clause is "zero new WS/SSE channels". The cockpit reads the worker
    // transcript through the REST endpoint ChatPage already uses — off the message that started
    // each cycle's turn (its nudge), through the same reader the chat uses.
    expect(cockpit).toContain('api.chatSessionDetail(workerKey)')
    expect(cockpit).toContain('const s = joinedSkillsOf(m)')
    // 🪤 A loop cycle has no user message: a cockpit reading only those showed no chip, ever.
    expect(cockpit).not.toContain("if (m.role !== 'user') continue")
  })

  it('the chat names the skills LIVE, from the activity event the turn announces them on', () => {
    expect(chatPage).toContain("if (kind === 'skills') {")
    expect(chatPage).toContain('skillsUsed: joined')
  })

  it('the learned row routes on origin instead of one hardcoded link', () => {
    // The WS handler must actually stamp the wire's `origin`, and the ledger must read it off
    // the same segment — otherwise `learnedSurface` is only ever called with `undefined` and
    // every chip degrades, which would look exactly like "old messages" forever.
    expect(chatPage).toContain('textRun.activity(text, kind, origin, ref)')
    expect(textRunOwner).toContain('stampActivityOrigin(segs, insertActivity(')
    expect(chatPage).toContain("ledger.learnedOrigin = (s as ActivitySegment).origin")
    // …and a learned preference's key, which is what its "Forget it" and its link need.
    expect(chatPage).toContain("ledger.learnedRef = (s as ActivitySegment).ref")
    expect(ledger).toContain('learnedSurface(learnedOrigin, learnedRef)')
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
    expect(chatPage).toContain('learnedRef={ledger.learnedRef}')
    expect(chatPage).toContain("import { ContextLedger } from './chat/ContextLedger'")
    // And ChatPage no longer carries a second, private copy of it.
    expect(chatPage).not.toContain('function ContextLedger(')
  })
})
