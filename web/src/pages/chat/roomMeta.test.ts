/**
 * `AGENT-ROOMS` AR-8 — the room surface's derivations, which are where its correctness lives.
 *
 * Every claim a room makes on screen is a claim about attribution: whose words these are, which
 * member is owed a turn, which one cannot speak at all. Those are computed in `roomMeta`, so they
 * are pinned here by feeding a wire payload and reading the verdict — not by asserting against a
 * rendered pixel, which cannot distinguish "correct" from "plausible".
 *
 * Two of these tests exist because the plan NAMES the defect they prevent:
 *
 *  · the turn-collapse merge (`Risks & open questions`: "the likeliest room-specific UI defect") —
 *    the session transcript merges consecutive assistant messages into one turn, and in a room
 *    consecutive assistant messages are usually DIFFERENT members;
 *  · the fabricated value (the swallowed-write family) — a member with no status must read as
 *    unknown, never as healthy, and a member whose binding was deleted must not report the DEFAULT
 *    agent's model as its own.
 */
import { describe, expect, it } from 'vitest'
import {
  listenPolicyMeta,
  memberBudgetLabel,
  memberModelLabel,
  memberReachLabel,
  memberRuntimeLabel,
  memberStateMeta,
  roomLines,
  roomMemberViews,
  roomState,
  roundBudgetLabel,
} from './roomMeta'
import type { RoomDetail, RoomListenPolicy, RoomMemberRecord, RoomRecord } from '../../lib/api'

function member(name: string, extra: Partial<RoomMemberRecord> = {}): RoomMemberRecord {
  return { name, role_blurb: '', listen_policy: 'all', profile_narrowing: {}, ...extra }
}

function room(extra: Partial<RoomRecord> = {}): RoomRecord {
  return {
    id: 'pricing-debate',
    title: 'Should we raise prices?',
    created_at: '2026-09-23T00:00:00',
    archived: false,
    paused: false,
    rounds_used: 0,
    round_budget: 0,
    pending_queue: [],
    speaking: '',
    owed: [],
    round_running: false,
    members: [],
    effective_round_budget: 6,
    max_round_budget: 100,
    max_members: 8,
    transcript_path: '/home/u/.personalclaw/rooms/pricing-debate/transcript.jsonl',
    ...extra,
  }
}

function detail(extra: Partial<RoomDetail> = {}): RoomDetail {
  return {
    room: room(),
    member_posture: [],
    member_bindings: [],
    messages: [],
    ...extra,
  }
}

describe('attribution — every line names its author unambiguously', () => {
  it('resolves the human, a member and the ROOM as three different kinds', () => {
    const d = detail({
      room: room({ members: [member('analyst', { role_blurb: 'argues from the numbers' })] }),
      messages: [
        { role: 'user', content: 'What do you think?', speaker: '', ts: '1' },
        { role: 'assistant', content: 'Margins are thin.', speaker: 'analyst', ts: '2' },
        { role: 'system', content: 'analyst was refused Write — read-only', speaker: 'analyst', ts: '3' },
      ],
    })
    const lines = roomLines(d)
    expect(lines.map((l) => l.kind)).toEqual(['human', 'member', 'room'])
    expect(lines[0].label).toBe('You')
    expect(lines[1].label).toBe('analyst')
    expect(lines[1].blurb).toBe('argues from the numbers')
    // 🔑 The refusal note carries the member's name as its speaker BY DESIGN (that is the
    // attribution a reader needs), so keying off the speaker alone would render the room's own
    // words as that member's position. It is distinguished by `role`, and so is this.
    expect(lines[2].label).toBe('Room')
    expect(lines[2].speaker).toBe('analyst')
  })

  it("a member's text can never be mistaken for the user's", () => {
    // The human's line and a member's line differ in KIND, which is what the renderer branches
    // on — not in content, not in position, and not in anything a stylesheet could collapse.
    const d = detail({
      room: room({ members: [member('skeptic')] }),
      messages: [
        { role: 'assistant', content: 'I disagree.', speaker: 'skeptic', ts: '1' },
        { role: 'user', content: 'I disagree.', speaker: '', ts: '2' },
      ],
    })
    const lines = roomLines(d)
    expect(lines[0].kind).toBe('member')
    expect(lines[1].kind).toBe('human')
    expect(lines[0].label).not.toBe(lines[1].label)
  })

  it('🔴 consecutive member messages do NOT merge — the named room-specific defect', () => {
    // The session transcript's `hydrateTurns` collapses consecutive assistant messages into one
    // turn. Two members answering in a row are consecutive assistant messages, so that collapse
    // would print the skeptic's words under the analyst's name. A room has no merge at all.
    const d = detail({
      room: room({ members: [member('analyst'), member('skeptic')] }),
      messages: [
        { role: 'assistant', content: 'Raise them.', speaker: 'analyst', ts: '1' },
        { role: 'assistant', content: 'Do not.', speaker: 'skeptic', ts: '2' },
        { role: 'assistant', content: 'Consider churn.', speaker: 'analyst', ts: '3' },
      ],
    })
    const lines = roomLines(d)
    expect(lines).toHaveLength(3)
    expect(lines.map((l) => l.label)).toEqual(['analyst', 'skeptic', 'analyst'])
    expect(lines.map((l) => l.content)).toEqual(['Raise them.', 'Do not.', 'Consider churn.'])
  })

  it('keeps a line written by a member who has since LEFT, attributed to that member', () => {
    // The roster changed; what was said did not. Dropping or re-attributing the line would
    // rewrite the record.
    const d = detail({
      room: room({ members: [member('analyst')] }),
      messages: [{ role: 'assistant', content: 'One last word.', speaker: 'departed', ts: '1' }],
    })
    const [line] = roomLines(d)
    expect(line.kind).toBe('member')
    expect(line.label).toBe('departed')
    expect(line.blurb).toBe('')
  })

  it('drops a blank line rather than rendering an empty attribution', () => {
    const d = detail({
      room: room({ members: [member('analyst')] }),
      messages: [
        { role: 'assistant', content: '   ', speaker: 'analyst', ts: '1' },
        { role: 'user', content: 'Still here?', speaker: '', ts: '2' },
      ],
    })
    expect(roomLines(d)).toHaveLength(1)
  })
})

describe('per-member status — absent is unknown, never healthy', () => {
  it('reports UNKNOWN for a member with neither a posture nor a binding answer', () => {
    const d = detail({ room: room({ members: [member('ghost')] }) })
    const [view] = roomMemberViews(d)
    expect(view.state).toBe('unknown')
    expect(memberStateMeta(view.state)?.label).toBe('Status unknown')
    // The tell: it must not have fallen through to `ok`, which renders no badge at all and so
    // would read as a configured, working member.
    expect(view.state).not.toBe('ok')
    // Neutral, not warn: nothing is known to be WRONG — something is simply not known.
    expect(memberStateMeta('unknown')?.tone).toBe('neutral')
  })

  it('reports a DELETED agent binding rather than the default agent it would resolve to', () => {
    const d = detail({
      room: room({ members: [member('writer')] }),
      member_posture: [{ name: 'writer', declared: {}, tool_grants: 'read', tool_allowlist: [] }],
      member_bindings: [{ name: 'writer', configured: false, model: '', provider: '' }],
    })
    const [view] = roomMemberViews(d)
    expect(view.state).toBe('unconfigured')
    expect(memberModelLabel(view.binding)).toBe('No agent by this name is configured')
    expect(memberRuntimeLabel(view.binding)).toBe('')
  })

  it('reports a REFUSED posture ahead of everything else, with the backend reason available', () => {
    const d = detail({
      room: room({ members: [member('critic')], pending_queue: ['critic'] }),
      member_posture: [{
        name: 'critic',
        declared: { tool_grants: 'read_write' },
        refused: 'room_member_posture_widens',
        detail: "'critic' declares a posture wider than the room's on tool_grants.",
      }],
      member_bindings: [{ name: 'critic', configured: true, model: 'gemma4:12b', provider: 'native' }],
    })
    const [view] = roomMemberViews({ ...d, room: { ...d.room, owed: ['critic'] } })
    // Refused beats "owed a turn": a queue position is meaningless for a member that cannot take
    // one, and refused beats the binding answer because it is the one with an actionable reason.
    expect(view.state).toBe('refused')
    expect(view.owed).toBe(true)
    expect(memberStateMeta(view.state)?.tone).toBe('danger')
    // A refused member has no resolved reach to report.
    expect(memberReachLabel(view.posture)).toBe('')
  })

  it('reports OWED with its FIFO position, ahead of the listen policy', () => {
    const d = detail({
      room: room({ members: [member('analyst'), member('skeptic')] }),
      member_posture: [
        { name: 'analyst', declared: {}, tool_grants: 'read', tool_allowlist: [] },
        { name: 'skeptic', declared: {}, tool_grants: 'read', tool_allowlist: [] },
      ],
      member_bindings: [
        { name: 'analyst', configured: true, model: 'gemma4:12b', provider: 'native' },
        { name: 'skeptic', configured: true, model: '', provider: 'acp:claude-code' },
      ],
    })
    // The queue order is the product fact: skeptic was enqueued first, so it is #1.
    const views = roomMemberViews({ ...d, room: { ...d.room, owed: ['skeptic', 'analyst'] } })
    expect(memberStateMeta('owed')?.tone).toBe('info')
    const byName = new Map(views.map((v) => [v.member.name, v]))
    expect(byName.get('skeptic')!.queuePosition).toBe(1)
    expect(byName.get('analyst')!.queuePosition).toBe(2)
    expect(views.every((v) => v.state === 'owed')).toBe(true)
  })

  it('reports NOTHING for a healthy member — the listen policy is not a status', () => {
    // A policy is configuration, so it lives in the row's labelled facts beside the model and the
    // tool reach. Rendering it as the status badge printed it twice on every healthy row AND made a
    // pill that says "nothing is wrong", competing for attention with the three that mean something.
    const policies: RoomListenPolicy[] = ['all', 'mention', 'silent']
    const d = detail({
      room: room({ members: policies.map((p, i) => member(`m${i}`, { listen_policy: p })) }),
      member_posture: policies.map((_, i) => ({ name: `m${i}`, declared: {}, tool_grants: 'read', tool_allowlist: [] })),
      member_bindings: policies.map((_, i) => ({ name: `m${i}`, configured: true, model: 'x', provider: 'native' })),
    })
    expect(roomMemberViews(d).map((v) => v.state)).toEqual(['ok', 'ok', 'ok'])
    expect(memberStateMeta('ok')).toBeNull()
    // …and the policy is still recoverable per member, which is what the row renders.
    expect(roomMemberViews(d).map((v) => listenPolicyMeta(v.member.listen_policy).label))
      .toEqual(['Everything', 'When named', 'Observer'])
  })

  it('reports ANSWERING only for the open turn of a RUNNING round', () => {
    // `speaking` with a live round is the member talking now; the same `speaking` with no round
    // running is a turn a stopped gateway cut off, and that member is OWED — first — not answering.
    const base = detail({
      room: room({ members: [member('analyst'), member('skeptic')], speaking: 'analyst', owed: ['analyst', 'skeptic'] }),
      member_posture: [
        { name: 'analyst', declared: {}, tool_grants: 'read', tool_allowlist: [] },
        { name: 'skeptic', declared: {}, tool_grants: 'read', tool_allowlist: [] },
      ],
      member_bindings: [
        { name: 'analyst', configured: true, model: 'x', provider: 'native' },
        { name: 'skeptic', configured: true, model: 'x', provider: 'native' },
      ],
    })
    const live = roomMemberViews({ ...base, room: { ...base.room, round_running: true } })
    expect(live.map((v) => v.state)).toEqual(['answering', 'owed'])
    expect(memberStateMeta('answering')?.label).toBe('Answering')
    const cutOff = roomMemberViews(base)
    expect(cutOff.map((v) => [v.state, v.queuePosition])).toEqual([['owed', 1], ['owed', 2]])
  })
})

describe('what a member row says about its reach — three different facts, not collapsed', () => {
  it('distinguishes a missing binding, an inherited model and a named one', () => {
    expect(memberModelLabel(undefined)).toBe('')
    expect(memberModelLabel({ name: 'a', configured: false, model: '', provider: '' }))
      .toBe('No agent by this name is configured')
    expect(memberModelLabel({ name: 'a', configured: true, model: '', provider: 'native' }))
      .toBe('Default model')
    expect(memberModelLabel({ name: 'a', configured: true, model: 'gemma4:12b', provider: 'native' }))
      .toBe('gemma4:12b')
  })

  it('renders an ACP runtime as the CLI the user picked, and an inherited one as nothing', () => {
    expect(memberRuntimeLabel({ name: 'a', configured: true, model: '', provider: 'acp:claude-code' }))
      .toBe('claude-code')
    expect(memberRuntimeLabel({ name: 'a', configured: true, model: '', provider: 'native' })).toBe('Native')
    expect(memberRuntimeLabel({ name: 'a', configured: true, model: '', provider: '' })).toBe('')
  })

  it('says which member is read-only and which may write — the whole point of a room', () => {
    expect(memberReachLabel({ name: 'a', declared: {}, tool_grants: 'read' })).toBe('Read-only tools')
    expect(memberReachLabel({ name: 'a', declared: {}, tool_grants: 'read_write' })).toBe('Read and write tools')
    // `custom` reports the allowlist SIZE, because 0 and 2 are different postures and the row has
    // no space for the list itself.
    expect(memberReachLabel({ name: 'a', declared: {}, tool_grants: 'custom', tool_allowlist: [] })).toBe('No tools')
    expect(memberReachLabel({ name: 'a', declared: {}, tool_grants: 'custom', tool_allowlist: ['Read', 'Grep'] }))
      .toBe('2 allowed tools')
  })

  it('omits a budget dimension left at 0, because 0 means "no ceiling of my own"', () => {
    expect(memberBudgetLabel({ name: 'a', declared: {}, budget: { max_tokens: 0, max_dollars: 0 } })).toBe('')
    expect(memberBudgetLabel({ name: 'a', declared: {}, budget: { max_tokens: 50000, max_dollars: 0 } }))
      .toBe('Up to 50,000 tokens')
    expect(memberBudgetLabel({ name: 'a', declared: {}, budget: { max_tokens: 0, max_dollars: 2.5 } }))
      .toBe('Up to $2.50')
  })

  it('spells out what each listen policy MEANS, not just its name', () => {
    // "mention" alone does not tell a reader that peers can summon this member and nobody else can.
    expect(listenPolicyMeta('mention').hint).toMatch(/another member/)
    expect(listenPolicyMeta('silent').hint).toMatch(/Peers cannot summon it/)
    // An unknown policy resolves to the declared default rather than throwing.
    expect(listenPolicyMeta('whisper').key).toBe('all')
  })
})

describe("the room's own state and its budget", () => {
  it('always reads the budget against the RESOLVED ceiling, never the declared 0', () => {
    // A room that inherits the configured default declares `round_budget: 0`. Reading that field
    // would render "3 of 0" — the inert reading this atom exists to remove.
    expect(roundBudgetLabel(room({ rounds_used: 3, round_budget: 0, effective_round_budget: 6 })))
      .toBe('3 of 6 exchanges')
    expect(roundBudgetLabel(room({ rounds_used: 1, round_budget: 1, effective_round_budget: 1 })))
      .toBe('1 of 1 exchange')
  })

  it('reports an archived room as archived even when it is also paused', () => {
    // An archived room refuses messages, so "paused waiting for you" would invite the user to do
    // something the backend will refuse.
    expect(roomState(room({ archived: true, paused: true }))).toBe('archived')
    expect(roomState(room({ paused: true }))).toBe('paused')
    expect(roomState(room())).toBe('active')
  })

  it('reads INTERRUPTED from two backend facts: turns owed, and no round running them', () => {
    // The restart case. No flag says "interrupted" — the room owes turns and the live task is gone,
    // so a round that died any way at all (restart, crash, a drain bug) reads the same.
    expect(roomState(room({ owed: ['analyst'], round_running: false }))).toBe('interrupted')
    expect(roomState(room({ owed: ['analyst'], round_running: true }))).toBe('active')
    expect(roomState(room({ owed: [], round_running: false }))).toBe('active')
    // A pause is not an interruption: the budget stopped it on purpose, and a reply resumes it.
    expect(roomState(room({ paused: true, owed: ['analyst'] }))).toBe('paused')
    expect(roomState(room({ archived: true, owed: ['analyst'] }))).toBe('archived')
  })
})
