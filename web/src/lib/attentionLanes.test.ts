import { describe, it, expect } from 'vitest'
import { LANES, laneFor, isKnownKind, KNOWN_KINDS, toLanes, inboxRaisedBy } from './attentionLanes'
import type { ActivityInput, ApprovalInput, AttentionInput, Lane, LaneCard } from './attentionLanes'
import type { InboxItemKind, InboxItemStatus } from './api'

// ── Lane derivation ─────────────────────────────────────────────────────────────────────────────
//
// Four lanes, each item exactly once. The two things these tests exist to catch are the two failures
// that render perfectly:
//
//   * a DOUBLE-COUNT. The pending-approval registry raises an `agent_request` item with
//     `refs.approval = <registry id>` for every pending approval, and
//     `PendingApproval.id` IS that registry id — so one blocked decision arrives on BOTH endpoints
//     Mission Control reads. Concatenating them shows the same halted tool twice and makes the
//     lane counts a lie. Asserted by total-count + key-uniqueness, not by eyeballing a lane.
//
//   * an UNKNOWN kind swallowed into a real lane. `idle` reads to a user as "nothing is needed from
//     you"; a kind this build has never heard of routed there states the one thing we cannot know.
//     Asserted WITH a vacuity floor — a known kind in the same test must land — so a `null` proves a
//     decision rather than a function that has stopped working.
//
// `working` gets its own test because it is the lane with no wire evidence: `ItemStatus`'s declared
// lifecycle is PENDING → SEEN → HANDLED | DISMISSED with no in-flight state, and `ItemStatus.SENT`
// measures as an enum member nobody writes. So no inbox item may ever produce a `working` card, and
// the assertion sweeps every kind × status pair rather than a sample.

function mkItem(over: Partial<AttentionInput> = {}): AttentionInput {
  return {
    id: 'i1',
    status: 'pending',
    message: 'a message',
    sender_name: 'Someone',
    channel_name: 'general',
    ...over,
  }
}

function mkApproval(over: Partial<ApprovalInput> = {}): ApprovalInput {
  return {
    id: 'req-1', request_id: 'req-1', tool: 'Bash', session: 's1', ts: 1000, risk: '', grant_agent: '',
    source_label: 'chat', ...over,
  }
}

function mkSession(over: Partial<ActivityInput> = {}): ActivityInput {
  return { key: 'sess-1', title: 'Refactor the ledger', running: true, stopping: false, pending_approval: false, ...over }
}

const ALL_KINDS: InboxItemKind[] = [
  'message', 'mention', 'email', 'agent_request', 'proposal', 'needs_input', 'digest', 'system',
  'user_note',
]
const ALL_STATUSES: InboxItemStatus[] = ['pending', 'seen', 'sent', 'dismissed', 'handled', 'filtered']

/** Every card in every lane, for the invariants that must hold globally rather than per lane. */
function allCards(lanes: Record<Lane, LaneCard[]>): LaneCard[] {
  return LANES.flatMap((lane) => lanes[lane])
}

describe('the closed kind vocabulary', () => {
  it('covers exactly the nine ItemKind members inbox.py declares', () => {
    // A tenth kind added to the union must break the exhaustive Record in the module, not arrive
    // here as an untriaged default. This is the runtime half of that guarantee — and it worked:
    // The `user_note` failed the module's `Record<InboxItemKind, …>` to compile until a lane
    // was chosen for it, which is why widening this list is a deliberate act and not drift.
    expect([...KNOWN_KINDS].sort()).toEqual([...ALL_KINDS].sort())
    expect(KNOWN_KINDS).toHaveLength(9)
  })
})

describe('laneFor — one item, one lane', () => {
  it('puts one item of each derivable lane in that lane and in no other', () => {
    // `working` is absent here on purpose: no inbox item can produce it. See its own test below.
    const cases: Array<[Lane, AttentionInput]> = [
      ['needs-approval', mkItem({ id: 'a', item_kind: 'agent_request', refs: { approval: 'req-9' } })],
      ['your-turn', mkItem({ id: 'b', item_kind: 'needs_input' })],
      ['idle', mkItem({ id: 'c', item_kind: 'digest' })],
    ]
    for (const [expected, item] of cases) {
      expect(laneFor(item)).toBe(expected)
    }

    // …and through toLanes, each lands in exactly one lane.
    const lanes = toLanes(cases.map(([, item]) => item), [])
    for (const [expected, item] of cases) {
      expect(lanes[expected].map((c) => c.id)).toContain(item.id)
      for (const other of LANES) {
        if (other === expected) continue
        expect(lanes[other].map((c) => c.id)).not.toContain(item.id)
      }
    }
  })

  it('routes both remaining attention kinds by what they ask for', () => {
    expect(laneFor(mkItem({ item_kind: 'proposal' }))).toBe('your-turn')
    // `system` tells you something; it asks nothing. Idle.
    expect(laneFor(mkItem({ item_kind: 'system' }))).toBe('idle')
  })

  it('treats a missing kind as the backend default (message), not as unknown', () => {
    // inbox.py:71-73 — MESSAGE is the default so pre-attention-store rows stay valid. Those rows
    // really are channel messages, so they are off-surface for the same reason `message` is.
    expect(laneFor(mkItem({ item_kind: undefined }))).toBeNull()
  })
})

describe('precedence — an item qualifying twice appears exactly once', () => {
  it('gives needs-approval precedence over your-turn for a mirrored approval', () => {
    // An `agent_request` is your-turn by kind; carrying `refs.approval` makes it a halted tool
    // decision, which is the narrower and more consequential claim.
    const plain = mkItem({ id: 'plain', item_kind: 'agent_request' })
    const mirror = mkItem({ id: 'mirror', item_kind: 'agent_request', refs: { approval: 'req-1' } })
    expect(laneFor(plain)).toBe('your-turn')
    expect(laneFor(mirror)).toBe('needs-approval')
  })

  it('collapses the approval and its inbox mirror into ONE card, and keeps the approval', () => {
    const approvals = [mkApproval({ id: 'req-1', tool: 'Write' })]
    const mirror = mkItem({ id: 'mirror', item_kind: 'agent_request', refs: { approval: 'req-1' } })
    const lanes = toLanes([mirror], approvals)

    // THE assertion that catches double-counting: one blocked decision, one card, total = 1.
    expect(allCards(lanes)).toHaveLength(1)
    expect(lanes['needs-approval']).toHaveLength(1)
    expect(lanes['needs-approval'][0].origin).toBe('approval')
    expect(lanes['needs-approval'][0].title).toBe('Write')
  })

  it('keeps the mirror when its approval is NOT in the same snapshot', () => {
    // The approval was answered while the mirror row stayed open. Losing the row entirely is worse
    // than showing a stale one, so it keeps its own card.
    const mirror = mkItem({ id: 'mirror', item_kind: 'agent_request', refs: { approval: 'gone' } })
    const lanes = toLanes([mirror], [mkApproval({ id: 'req-other' })])
    expect(allCards(lanes)).toHaveLength(2)
    expect(lanes['needs-approval'].map((c) => c.origin).sort()).toEqual(['approval', 'inbox'])
  })

  it('total cards across the four lanes equals the inputs minus the nulls, with no key repeated', () => {
    const items = [
      mkItem({ id: 'k1', item_kind: 'needs_input' }),                 // your-turn
      mkItem({ id: 'k2', item_kind: 'proposal' }),                    // your-turn
      mkItem({ id: 'k3', item_kind: 'digest' }),                      // idle
      mkItem({ id: 'k4', item_kind: 'message' }),                     // null — channel-shaped
      mkItem({ id: 'k5', item_kind: 'needs_input', status: 'handled' }), // null — closed
      mkItem({ id: 'k6', item_kind: 'nonsense' as unknown as InboxItemKind }),   // null — unknown
      mkItem({ id: 'k7', item_kind: 'agent_request' }),               // needs-approval? no: your-turn
    ]
    const approvals = [mkApproval({ id: 'req-1' }), mkApproval({ id: 'req-2' })]
    const activity = [mkSession({ key: 'sess-1' })]

    const placed = items.filter((i) => laneFor(i) !== null)
    expect(placed).toHaveLength(4) // vacuity floor: the nulls are three of seven, not all seven

    const lanes = toLanes(items, approvals, activity)
    const cards = allCards(lanes)
    expect(cards).toHaveLength(placed.length + approvals.length + activity.length)
    expect(new Set(cards.map((c) => c.key)).size).toBe(cards.length)
    // and every card sits under the lane it names
    for (const lane of LANES) for (const c of lanes[lane]) expect(c.lane).toBe(lane)
  })
})

describe('an unknown kind is a refusal, not an idle row', () => {
  it('returns null for a kind this build does not know — and a known kind in the same breath lands', () => {
    const unknown = mkItem({ id: 'u', item_kind: 'quantum_nudge' as unknown as InboxItemKind })
    const known = mkItem({ id: 'k', item_kind: 'needs_input' })

    // The vacuity floor. Without this line a `laneFor` that returned null for EVERYTHING would pass.
    expect(laneFor(known)).toBe('your-turn')
    expect(laneFor(unknown)).toBeNull()
    expect(isKnownKind('quantum_nudge')).toBe(false)
    expect(isKnownKind('needs_input')).toBe(true)

    const lanes = toLanes([unknown, known], [])
    for (const lane of LANES) expect(lanes[lane].map((c) => c.id)).not.toContain('u')
    // …specifically not swallowed into `idle`, the lane that would claim it needs nothing.
    expect(lanes['idle']).toHaveLength(0)
    expect(lanes['your-turn'].map((c) => c.id)).toEqual(['k'])
  })
})

describe('channel-shaped kinds are excluded from this surface', () => {
  it('drops message, mention and email while the attention kinds land', () => {
    // inbox.py's SOURCE_DECLARABLE_KINDS. They have drafts, reply routing and a send affordance;
    // Mission Control has none of those, so a conversation is a different concern.
    for (const kind of ['message', 'mention', 'email'] as InboxItemKind[]) {
      expect(laneFor(mkItem({ item_kind: kind }))).toBeNull()
    }
    const lanes = toLanes(
      [
        mkItem({ id: 'm', item_kind: 'message' }),
        mkItem({ id: 'n', item_kind: 'mention' }),
        mkItem({ id: 'e', item_kind: 'email' }),
        mkItem({ id: 'p', item_kind: 'proposal' }),
      ],
      [],
    )
    expect(allCards(lanes).map((c) => c.id)).toEqual(['p'])
  })
})

describe('status decides whether an item is still asking', () => {
  it('keeps pending and seen, drops the closed statuses', () => {
    expect(laneFor(mkItem({ item_kind: 'needs_input', status: 'pending' }))).toBe('your-turn')
    expect(laneFor(mkItem({ item_kind: 'needs_input', status: 'seen' }))).toBe('your-turn')
    for (const status of ['handled', 'dismissed', 'filtered', 'sent'] as InboxItemStatus[]) {
      expect(laneFor(mkItem({ item_kind: 'needs_input', status }))).toBeNull()
    }
  })

  it('fails OPEN on a missing or unrecognised status', () => {
    // Opposite direction from an unknown kind, deliberately: the only question here is "is this
    // resolved?", and hiding something still unresolved is the worse failure on this surface.
    expect(laneFor(mkItem({ item_kind: 'needs_input', status: undefined as unknown as InboxItemStatus }))).toBe('your-turn')
    expect(laneFor(mkItem({ item_kind: 'needs_input', status: 'snoozed' as unknown as InboxItemStatus }))).toBe('your-turn')
  })
})

describe('working is the lane the attention store cannot prove', () => {
  it('is never produced by laneFor, for any kind × status pair', () => {
    // 8 × 6 = 48 pairs. `ItemStatus` declares no in-flight state and `SENT` has zero writers, so
    // there is nothing to key it off. A sample would not have said that.
    let derivable = 0
    for (const kind of ALL_KINDS) {
      for (const status of ALL_STATUSES) {
        const lane = laneFor(mkItem({ item_kind: kind, status }))
        expect(lane).not.toBe('working')
        if (lane !== null) derivable += 1
      }
    }
    expect(derivable).toBeGreaterThan(0) // vacuity floor: the sweep did place rows
  })

  it('is populated only from observed session activity', () => {
    const lanes = toLanes([mkItem({ item_kind: 'needs_input' })], [], [
      mkSession({ key: 'run', running: true }),
      mkSession({ key: 'wind', running: false, stopping: true }),
      mkSession({ key: 'quiet', running: false, stopping: false }),
    ])
    // running and stopping both count as working; stopping is winding down, not idle.
    expect(lanes['working'].map((c) => c.id).sort()).toEqual(['run', 'wind'])
    expect(lanes['working'].map((c) => c.subtitle).sort()).toEqual(['running', 'stopping'])
    // a quiet session is not an attention item and does not become an idle card
    expect(allCards(lanes).map((c) => c.id)).not.toContain('quiet')
  })

  it('is empty — not guessed — when no activity is supplied', () => {
    const lanes = toLanes([mkItem({ item_kind: 'needs_input', created_at: 1 })], [mkApproval()])
    expect(lanes['working']).toEqual([])
  })

  it('does not mirror a session pending_approval into needs-approval', () => {
    // A boolean cannot say WHICH tool is waiting; GET /api/approvals carries that row already.
    const lanes = toLanes([], [], [mkSession({ key: 'blocked', running: true, pending_approval: true })])
    expect(lanes['needs-approval']).toEqual([])
    expect(lanes['working'].map((c) => c.id)).toEqual(['blocked'])
  })
})

describe('ordering inside a lane', () => {
  it('ranks the lanes that ask you something OLDEST first', () => {
    const lanes = toLanes(
      [
        mkItem({ id: 'mid', item_kind: 'needs_input', created_at: 200 }),
        mkItem({ id: 'newest', item_kind: 'needs_input', created_at: 300 }),
        mkItem({ id: 'oldest', item_kind: 'needs_input', created_at: 100 }),
      ],
      [
        mkApproval({ id: 'a-mid', ts: 20 }),
        mkApproval({ id: 'a-new', ts: 30 }),
        mkApproval({ id: 'a-old', ts: 10 }),
      ],
    )
    // The oldest unanswered question must be at the TOP. Three items, so the comparator is really
    // exercised rather than satisfied by a single swap.
    expect(lanes['your-turn'].map((c) => c.id)).toEqual(['oldest', 'mid', 'newest'])
    expect(lanes['needs-approval'].map((c) => c.id)).toEqual(['a-old', 'a-mid', 'a-new'])
  })

  it('ranks idle NEWEST first — nothing there is overdue', () => {
    const lanes = toLanes(
      [
        mkItem({ id: 'mid', item_kind: 'digest', created_at: 200 }),
        mkItem({ id: 'newest', item_kind: 'system', created_at: 300 }),
        mkItem({ id: 'oldest', item_kind: 'digest', created_at: 100 }),
      ],
      [],
    )
    expect(lanes['idle'].map((c) => c.id)).toEqual(['newest', 'mid', 'oldest'])
  })

  it('sorts an undated row LAST in either direction, and breaks ties deterministically', () => {
    const undatedInAsc = toLanes(
      [
        mkItem({ id: 'dated', item_kind: 'needs_input', created_at: 500 }),
        mkItem({ id: 'undated', item_kind: 'needs_input' }),
      ],
      [],
    )
    expect(undatedInAsc['your-turn'].map((c) => c.id)).toEqual(['dated', 'undated'])

    const undatedInDesc = toLanes(
      [
        mkItem({ id: 'dated', item_kind: 'digest', created_at: 500 }),
        mkItem({ id: 'undated', item_kind: 'digest' }),
      ],
      [],
    )
    expect(undatedInDesc['idle'].map((c) => c.id)).toEqual(['dated', 'undated'])

    // Equal timestamps → key order, so the result is a total order and not sort-stability luck.
    const tied = toLanes(
      [
        mkItem({ id: 'b', item_kind: 'needs_input', created_at: 7 }),
        mkItem({ id: 'a', item_kind: 'needs_input', created_at: 7 }),
      ],
      [],
    )
    expect(tied['your-turn'].map((c) => c.id)).toEqual(['a', 'b'])
  })

  it('falls back to the stringified `ts` tail when created_at is absent', () => {
    // inbox.py mints ids as {kind}_{uuid8}_{ts} and InboxItem.ts is the rsplit tail — a string.
    const lanes = toLanes(
      [
        mkItem({ id: 'later', item_kind: 'needs_input', ts: '900.5' }),
        mkItem({ id: 'earlier', item_kind: 'needs_input', ts: '100.25' }),
      ],
      [],
    )
    expect(lanes['your-turn'].map((c) => c.id)).toEqual(['earlier', 'later'])
    expect(lanes['your-turn'][0].at).toBe(100.25)
  })
})

describe('the shape the component indexes', () => {
  it('returns all four lanes, empty, for empty input', () => {
    const lanes = toLanes([], [])
    expect(Object.keys(lanes)).toEqual([...LANES])
    for (const lane of LANES) expect(lanes[lane]).toEqual([])
  })

  it('returns all four lanes even when only one is populated', () => {
    // A component that indexes a missing lane crashes the whole surface, not one card.
    const lanes = toLanes([], [mkApproval()])
    expect(Object.keys(lanes)).toEqual([...LANES])
    expect(lanes['your-turn']).toEqual([])
    expect(lanes['working']).toEqual([])
    expect(lanes['idle']).toEqual([])
  })
})

describe('malformed input does not blank the surface', () => {
  it('survives nulls, non-objects, missing fields and non-array arguments', () => {
    const junk = [
      null,
      undefined,
      'a string',
      42,
      {},                                                   // no id, no status, no kind
      { id: 'no-status', item_kind: 'needs_input' },         // status absent → fails open
      { id: '', item_kind: 'needs_input', status: 'pending' }, // empty id → skipped
      { id: 'bad-refs', item_kind: 'agent_request', status: 'pending', refs: 'not-an-object' },
      { id: 'null-refs', item_kind: 'agent_request', status: 'pending', refs: null },
      { id: 'nan-time', item_kind: 'needs_input', status: 'pending', created_at: Number.NaN },
      { id: 'junk-ts', item_kind: 'needs_input', status: 'pending', ts: 'not-a-number' },
    ] as unknown as AttentionInput[]
    const badApprovals = [null, {}, { id: 'ok' }, 'x'] as unknown as ApprovalInput[]
    const badActivity = [null, {}, { key: 'k', running: true }, 7] as unknown as ActivityInput[]

    let lanes!: ReturnType<typeof toLanes>
    expect(() => { lanes = toLanes(junk, badApprovals, badActivity) }).not.toThrow()
    expect(Object.keys(lanes)).toEqual([...LANES])

    // Vacuity floor: the well-formed rows in that junk DID land, so "did not throw" is not "did
    // nothing". no-status, bad-refs, null-refs, nan-time and junk-ts are placeable; '' is not.
    const ids = allCards(lanes).map((c) => c.id)
    expect(ids).toContain('no-status')
    expect(ids).toContain('nan-time')
    expect(ids).not.toContain('')
    expect(lanes['needs-approval'].map((c) => c.id)).toContain('ok')
    expect(lanes['working'].map((c) => c.id)).toContain('k')
    // a non-object refs cannot be read as an approval mirror, so the row stays your-turn
    expect(lanes['your-turn'].map((c) => c.id)).toContain('bad-refs')

    // …and the arguments themselves may be the wrong type entirely (a failed fetch).
    expect(() => toLanes(null as unknown as AttentionInput[], undefined as unknown as ApprovalInput[])).not.toThrow()
    expect(Object.keys(toLanes(null as unknown as AttentionInput[], 'nope' as unknown as ApprovalInput[]))).toEqual([...LANES])
    expect(laneFor(null as unknown as AttentionInput)).toBeNull()
    expect(laneFor(undefined as unknown as AttentionInput)).toBeNull()
  })

  it('titles a card from whatever the row actually carried', () => {
    const lanes = toLanes(
      [
        mkItem({ id: 'multi', item_kind: 'needs_input', message: 'first line\nsecond line' }),
        mkItem({ id: 'summary', item_kind: 'needs_input', message: '   ', context_summary: 'from the summary' }),
        mkItem({ id: 'blank', item_kind: 'needs_input', message: '', context_summary: '' }),
      ],
      [],
    )
    const byId = new Map(lanes['your-turn'].map((c) => [c.id, c]))
    expect(byId.get('multi')!.title).toBe('first line')
    expect(byId.get('summary')!.title).toBe('from the summary')
    expect(byId.get('blank')!.title).toBe('(no message)')
  })
})

// ── Who raised a card ───────────────────────────────────────────────────────────────────────────
//
// Shaped the way the backend writes them. A workflow's gate, a trigger's question, a loop's wait and
// the control bridge's confirm all ride the `loop/needs_input` notification pair, so each row's
// sender and channel ARE "loop" (`emit_attention_item` writes the pair's source into both) — the
// one word every one of these cards showed about where it came from.

describe('who raised a card', () => {
  const onThePair = (id: string, refs: Record<string, unknown>): AttentionInput =>
    mkItem({ id, item_kind: 'needs_input', sender_name: 'loop', channel_name: 'loop', refs })

  const asked: Array<[string, AttentionInput]> = [
    ['Workflow · release', onThePair('gate', { workflow: 'run-1', workflow_name: 'release', workflow_node: 'approve', resume_token: 't1' })],
    ['Trigger · Check my balance', onThePair('park', { trigger_park: 'balance', trigger: 'balance', trigger_name: 'Check my balance', resume_token: 't2' })],
    ['Loop', onThePair('wait', { loop: 'abc123', loop_kind: 'general' })],
    // A loop that runs as a workflow run stamps both: the loop is what asked.
    ['Loop', onThePair('escalated', { loop: 'r1', loop_kind: 'general', workflow: 'r1' })],
    ['Control bridge', onThePair('bridge', { source: 'control_bridge', action: 'restart', confirmation: 'c1' })],
  ]

  it('names the work an Inbox card is for, never the notification pair it rode', () => {
    for (const [label, item] of asked) expect(inboxRaisedBy(item)).toBe(label)
    const cards = toLanes(asked.map(([, item]) => item), [])['your-turn']
    expect(new Map(cards.map((c) => [c.id, c.raisedBy]))).toEqual(
      new Map(asked.map(([label, item]) => [item.id, label])),
    )
    // The sender is not on the card twice: `raisedBy` took over the line it was.
    expect(cards.map((c) => c.subtitle)).toEqual(cards.map(() => undefined))
  })

  it('names an app\'s row by the name the app goes by, never its `app:` notification source', () => {
    const fromApp = (refs: Record<string, unknown>) =>
      mkItem({ id: 'p', item_kind: 'proposal', sender_name: 'app:demo-proposer', channel_name: 'app:demo-proposer', refs })
    expect(inboxRaisedBy(fromApp({ app: 'demo-proposer', app_display_name: 'Demo Proposer' }))).toBe('Demo Proposer')
    // A row raised before the platform kept the app's name on it names the app, not the source.
    expect(inboxRaisedBy(fromApp({ app: 'demo-proposer' }))).toBe('demo-proposer')
  })

  it('keeps the sender on a card whose refs name no work', () => {
    const digest = mkItem({ id: 'd', item_kind: 'digest', sender_name: 'learning', channel_name: 'learning', refs: { session: 'dashboard:abc' } })
    expect(inboxRaisedBy(digest)).toBe('learning')
    expect(inboxRaisedBy(mkItem({ sender_name: '', channel_name: 'general' }))).toBe('general')
  })

  it('names who an approval is for in the registry\'s words, from the start of a line', () => {
    // The registry names where every approval came from (`approval_source.approval_source_label`);
    // the card used to guess it from the session key, and a loop's worker read as a chat elsewhere.
    const cases: Array<[string, ApprovalInput]> = [
      ['Chat “Trip planning”', mkApproval({ source_label: 'chat “Trip planning”' })],
      ['Workflow “deep-research” · step “sweep”', mkApproval({ source_label: 'workflow “deep-research” · step “sweep”', session: 'workflow:df5827ca:sweep' })],
      ['Trigger “Check my balance”', mkApproval({ source_label: 'trigger “Check my balance”' })],
      ['Loop “Fix the README”', mkApproval({ source_label: 'loop “Fix the README”', session: 'loop-0a1b2c3d' })],
      ['MCP server “github”', mkApproval({ source_label: 'MCP server “github”', session: '' })],
    ]
    const cards = toLanes([], cases.map(([, a], i) => ({ ...a, id: `a${i}` })))['needs-approval']
    expect(new Map(cards.map((c) => [c.id, c.raisedBy]))).toEqual(
      new Map(cases.map(([label], i) => [`a${i}`, label])),
    )
  })

  it('keeps what the tool is for as the approval card\'s line, and no longer a bare session key', () => {
    const at = { session: 'workflow:r1:sweep', source_label: 'workflow · step “sweep”' }
    const [withPurpose] = toLanes([], [mkApproval({ ...at, tool_purpose: 'List the build folder' })])['needs-approval']
    expect([withPurpose.raisedBy, withPurpose.subtitle]).toEqual(['Workflow · step “sweep”', 'List the build folder'])
    const [bare] = toLanes([], [mkApproval({ ...at, tool_purpose: '' })])['needs-approval']
    expect([bare.raisedBy, bare.subtitle]).toEqual(['Workflow · step “sweep”', undefined])
    // An approval that names nobody says nothing about who asked, rather than guessing.
    const [unnamed] = toLanes([], [mkApproval({ source_label: '', session: '' })])['needs-approval']
    expect(unnamed.raisedBy).toBeUndefined()
  })

  it('names an approval\'s own Inbox row by the words the registry kept on it once the approval has left the list', () => {
    const row = mkItem({ item_kind: 'agent_request', sender_name: 'system', channel_name: 'system', refs: { approval: 'gone', session: 'workflow:r1:sweep', source_label: 'workflow “nightly” · step “sweep”' } })
    expect(inboxRaisedBy(row)).toBe('Workflow “nightly” · step “sweep”')
  })
})
