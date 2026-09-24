import { describe, it, expect } from 'vitest'
import { sessionMapMarks, SESSION_MARK_KINDS } from './sessionMap'
import { hydrateTurns, type ChatTurn, type Segment, type SubagentCard, type HistMsg } from './chatTypes'

// ── SSM-1 — the typed session-map mark model + derivation (contract owner) ──────────────────
//
// `sessionMapMarks` is the ONE place the transcript becomes an index. Its done_when is a
// count ("one mark per turn plus one typed sub-event mark per listed segment"), and a count
// is the easiest thing in the codebase to fake:
//
//   VACUITY FLOOR. `turns.map((t, i) => ({ markIndex: i, kind: t.role, … }))` emits exactly
//   one mark per turn and passes any naive length/`kind ∈ vocab` check while emitting ZERO
//   sub-event marks — the whole point of the map. So every positive assertion here is paired
//   with a discriminator that a per-turn-only (or a mark-every-segment) impl fails:
//     · the exact ORDERED kind sequence, incl. all six sub-events (defeats per-turn-only);
//     · a `text` / `thinking` segment produces NO mark (defeats "mark every segment");
//     · the two tool segments (ok + fail) yield TWO distinct tool marks (defeats collapsing);
//     · `visibleIndex` is the OWNING-TURN coordinate and differs from `markIndex` (defeats
//       faking the jump coordinate with the array position);
//     · a bare `activity` with no `activityKind` produces NO mark (§A.2 keys on the kind).

const USER_TS = '2026-09-16T10:00:00.000Z'
const ASST_TS = '2026-09-16T10:00:05.000Z'

/** An assistant turn carrying every listed sub-event, plus a `text` and a `thinking`
 *  segment that must NOT be marked. Order matters: the derivation preserves segment order. */
const assistantSegments: Segment[] = [
  { kind: 'text', text: 'Running the build and reading the failing file now.' },
  { kind: 'tool', id: 't-ok', tool: 'Terminal', detail: 'npm run build', done: true }, // ok absent = success
  { kind: 'tool', id: 't-fail', tool: 'Read', detail: 'src/missing.ts', done: true, ok: false,
    agentError: { code: 'file_not_found', what: 'no such file', why: 'path typo', fix: 'check the path' } },
  { kind: 'approval', id: 'a-1', tool: 'Terminal', input: 'rm -rf build', risk: 'destructive' },
  { kind: 'error', text: 'Bedrock ValidationException: input is too long for the model.' },
  { kind: 'activity', text: 'cost $0.02 · 1.2k tokens · 4.1s', activityKind: 'stats' },
  { kind: 'thinking', text: 'Considering whether the build cache is stale…' }, // MUST NOT be marked
]

const fixtureTurns: ChatTurn[] = [
  { role: 'user', ts: USER_TS, visibleIndex: 0, segments: [{ kind: 'text', text: 'Please **run** the build and check status.' }] },
  { role: 'assistant', ts: ASST_TS, visibleIndex: 1, segments: assistantSegments },
]

const fixtureSubagents: SubagentCard[] = [
  { id: 's-1', task: 'Investigate the flaky snapshot test', agent: 'general-purpose', done: true },
]

const REQUIRED_FIELDS = ['markIndex', 'kind', 'role', 'visibleIndex', 'ts', 'preview'] as const

describe('sessionMapMarks — one mark per turn + one typed sub-event mark per listed segment', () => {
  const marks = sessionMapMarks(fixtureTurns, fixtureSubagents)

  it('emits the exact ordered kind sequence over the full fixture', () => {
    // user turn, assistant turn, then the assistant turn's sub-events in segment order
    // (tool-ok, tool-fail, approval, error, activity), then the subagent. The `text` and
    // `thinking` segments contribute NO mark.
    expect(marks.map((m) => m.kind)).toEqual([
      'user', 'assistant', 'tool', 'tool', 'approval', 'error', 'activity', 'subagent',
    ])
  })

  it('produces one mark per TURN plus one per listed SUB-EVENT — and nothing else', () => {
    // 2 turns + (2 tool + 1 approval + 1 error + 1 activity) sub-events + 1 subagent = 8.
    expect(marks).toHaveLength(8)
    // Per-kind census — a per-turn-only impl reads {user:1, assistant:1} and 0 of the rest.
    const census = marks.reduce<Record<string, number>>((a, m) => ({ ...a, [m.kind]: (a[m.kind] ?? 0) + 1 }), {})
    expect(census).toEqual({ user: 1, assistant: 1, tool: 2, approval: 1, error: 1, activity: 1, subagent: 1 })
  })

  it('gives every mark all six required fields, non-null and correctly typed', () => {
    for (const m of marks) {
      for (const f of REQUIRED_FIELDS) {
        expect(m[f], `${m.kind} mark missing ${f}`).not.toBeNull()
        expect(m[f], `${m.kind} mark undefined ${f}`).not.toBeUndefined()
      }
      expect(Number.isFinite(m.markIndex)).toBe(true)
      expect(Number.isFinite(m.visibleIndex)).toBe(true)
      expect(typeof m.ts).toBe('string')
      expect(typeof m.preview).toBe('string')
      expect(m.role === 'user' || m.role === 'assistant').toBe(true)
    }
  })

  it('draws every kind from the closed vocabulary and nothing outside it', () => {
    for (const m of marks) expect(SESSION_MARK_KINDS).toContain(m.kind)
    // Vacuity floor: the vocabulary is exactly seven — a stray/extra kind can't hide.
    expect([...SESSION_MARK_KINDS].sort()).toEqual(
      ['activity', 'approval', 'assistant', 'error', 'subagent', 'tool', 'user'],
    )
  })

  it('markIndex is the array position; it is NOT the jump coordinate', () => {
    marks.forEach((m, i) => expect(m.markIndex).toBe(i))
  })

  it('visibleIndex is the OWNING-TURN coordinate, not the mark index', () => {
    // The user mark jumps to turn 0; the assistant mark AND all of its sub-events jump to
    // turn 1 — so several marks (markIndex 1..7) share one visibleIndex (1). A jump
    // coordinate faked from the array position would read 1,2,3,4,5,6,7 here instead.
    expect(marks[0].visibleIndex).toBe(0) // user
    for (let i = 1; i < marks.length; i++) expect(marks[i].visibleIndex).toBe(1)
    // And the coordinate genuinely differs from the index for the sub-events.
    const tool = marks.find((m) => m.kind === 'tool')!
    expect(tool.markIndex).not.toBe(tool.visibleIndex)
  })

  it('inherits the owning turn role + timestamp onto its sub-events', () => {
    expect(marks[0].role).toBe('user')
    expect(marks[0].ts).toBe(USER_TS)
    for (const m of marks.slice(1)) {
      expect(m.role).toBe('assistant')
      expect(m.ts).toBe(ASST_TS)
    }
  })

  it('strips markdown from the preview', () => {
    // `**run**` must not survive into a plain-text index label.
    expect(marks[0].preview).toBe('Please run the build and check status.')
    expect(marks[0].preview).not.toContain('**')
  })
})

describe('sessionMapMarks — the vacuity-floor discriminators', () => {
  it('does NOT mark a `text` or `thinking` segment (the turn mark stands for the body)', () => {
    const turns: ChatTurn[] = [{
      role: 'assistant', ts: ASST_TS, visibleIndex: 3,
      segments: [
        { kind: 'text', text: 'Just some prose.' },
        { kind: 'thinking', text: 'private reasoning' },
      ],
    }]
    const marks = sessionMapMarks(turns)
    expect(marks).toHaveLength(1) // only the assistant turn mark
    expect(marks[0].kind).toBe('assistant')
    expect(marks.some((m) => m.preview.includes('private reasoning'))).toBe(false)
  })

  it('does NOT mark a bare `activity` segment that carries no activityKind (§A.2 keys on it)', () => {
    const withKind: ChatTurn[] = [{ role: 'assistant', segments: [{ kind: 'activity', text: 'stats', activityKind: 'stats' }] }]
    const withoutKind: ChatTurn[] = [{ role: 'assistant', segments: [{ kind: 'activity', text: 'Thinking…' }] }]
    expect(sessionMapMarks(withKind).some((m) => m.kind === 'activity')).toBe(true)
    // The discriminator: a coarse activity line with no kind adds only the turn mark.
    expect(sessionMapMarks(withoutKind).some((m) => m.kind === 'activity')).toBe(false)
    expect(sessionMapMarks(withoutKind)).toHaveLength(1)
  })

  it('emits TWO distinct tool marks for an ok + a failed tool call, and carries ok===false', () => {
    const tools = sessionMapMarks(fixtureTurns).filter((m) => m.kind === 'tool')
    expect(tools).toHaveLength(2)
    // Distinct previews — the map does not collapse two calls into one row.
    expect(tools[0].preview).not.toBe(tools[1].preview)
    expect(tools[0].preview).toContain('Terminal')
    expect(tools[1].preview).toContain('Read')
    // The failure outcome survives into mark-space (§A.2 danger tone). Only the failed one.
    expect(tools[0].ok).toBeUndefined()
    expect(tools[1].ok).toBe(false)
  })
})

describe('sessionMapMarks — subagents, fallbacks, and the real hydration path', () => {
  it('adds a subagent mark ONLY when subagent cards are supplied (they are not turn segments)', () => {
    const withoutSubs = sessionMapMarks(fixtureTurns)
    expect(withoutSubs.some((m) => m.kind === 'subagent')).toBe(false)
    expect(withoutSubs).toHaveLength(7)
    // Supplied → one subagent mark, jumping to the most recent turn, assistant role.
    const withSubs = sessionMapMarks(fixtureTurns, fixtureSubagents)
    const sub = withSubs.find((m) => m.kind === 'subagent')!
    expect(sub.visibleIndex).toBe(1) // the last turn's coordinate
    expect(sub.role).toBe('assistant')
    expect(sub.preview).toContain('flaky snapshot test')
  })

  it('falls back to the array index for visibleIndex and to "" for ts when a turn lacks them', () => {
    // A live-built turn has neither `visibleIndex` nor `ts` stamped yet.
    const turns: ChatTurn[] = [{ role: 'user', segments: [{ kind: 'text', text: 'hi' }] }]
    const [m] = sessionMapMarks(turns)
    expect(m.visibleIndex).toBe(0) // array position fallback
    expect(m.ts).toBe('') // non-null even when absent
  })

  it('is a no-op on an empty transcript', () => {
    expect(sessionMapMarks([])).toEqual([])
  })

  it('visibleIndex round-trips at_message_index over the REAL hydrateTurns output', () => {
    // Ties SSM-1 to its declared dep: the derivation must read the coordinate hydrateTurns
    // stamps, not the turn's array position (they diverge on tool-using transcripts).
    const history: HistMsg[] = [
      { role: 'user', content: 'run the build', ts: USER_TS },
      { role: 'tool', content: 'Terminal', ts: ASST_TS, meta: { tool_call_id: 'c1', tool: 'Terminal', detail: 'npm run build', done: true } },
      { role: 'assistant', content: 'Build is green.', ts: ASST_TS },
    ]
    const turns = hydrateTurns(history)
    const marks = sessionMapMarks(turns)
    const user = marks.find((m) => m.kind === 'user')!
    const assistant = marks.find((m) => m.kind === 'assistant')!
    const tool = marks.find((m) => m.kind === 'tool')!
    expect(user.visibleIndex).toBe(turns.find((t) => t.role === 'user')!.visibleIndex)
    expect(assistant.visibleIndex).toBe(turns.find((t) => t.role === 'assistant')!.visibleIndex)
    // The tool segment merged into the assistant turn → it inherits that turn's coordinate.
    expect(tool.visibleIndex).toBe(assistant.visibleIndex)
  })
})
