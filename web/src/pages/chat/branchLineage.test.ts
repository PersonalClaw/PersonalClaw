import { describe, it, expect } from 'vitest'
import { branchIndexOf, branchParentKey } from './branchLineage'
import { hydrateTurns, type ChatTurn, type HistMsg, userTurn, assistantTurn } from './chatTypes'

// ── Branch mechanic (CHAT-CRAFT CC-7): the wire coordinate must be the message
// index, never the turn's array position ─────────────────────────────────────────
//
// `POST /api/chat/sessions/{key}/fork` takes `at_message_index` into the BACKEND's
// visible list — `[m for m in messages if m["role"] in ("user","assistant")]`,
// inclusive (chat_fork.py:119,135). The rendered `turns` array is a LOSSY projection
// of that list: hydrateTurns drops native loop re-injections and merges consecutive
// assistant messages into one turn. So the two indices agree only on the simplest
// transcript — one user message, one assistant message, no tools — and diverge
// further with every collapse.
//
// That is the failure mode the atom names for "either role": a user clicking Branch
// on the last answer of a tool-using conversation got a branch cut several messages
// EARLIER, silently, with a plausible-looking result.

/** The backend's own visible-list filter, so the expected index is derived the same
 *  way chat_fork.py derives it rather than hand-counted. */
const visibleIndexOfLast = (msgs: HistMsg[], predicate: (m: HistMsg, i: number) => boolean): number => {
  const visible = msgs.filter((m) => m.role === 'user' || m.role === 'assistant')
  let found = -1
  visible.forEach((m, i) => { if (predicate(m, i)) found = i })
  return found
}

describe('branchIndexOf — the turn position is not the message index', () => {
  it('agrees with the turn index on a plain alternating transcript', () => {
    const msgs: HistMsg[] = [
      { role: 'user', content: 'one' },
      { role: 'assistant', content: 'first answer' },
      { role: 'user', content: 'two' },
      { role: 'assistant', content: 'second answer' },
    ]
    const turns = hydrateTurns(msgs)
    expect(turns.map((t) => t.role)).toEqual(['user', 'assistant', 'user', 'assistant'])
    expect(turns.map((_, i) => branchIndexOf(turns, i))).toEqual([0, 1, 2, 3])
  })

  it('branches at the RIGHT message when consecutive assistant messages merged', () => {
    // A native multi-part answer persists as several assistant messages; hydrateTurns
    // renders them as ONE turn. The backend still counts three.
    const msgs: HistMsg[] = [
      { role: 'user', content: 'analyse this' },
      { role: 'assistant', content: 'part one' },
      { role: 'assistant', content: 'part two' },
      { role: 'assistant', content: 'part three' },
      { role: 'user', content: 'now the other direction' },
      { role: 'assistant', content: 'ok' },
    ]
    const turns = hydrateTurns(msgs)
    expect(turns).toHaveLength(4)  // user, merged assistant, user, assistant
    // The merged answer's inclusive coordinate is its LAST message (index 3), so a
    // branch carries the whole answer. The turn position (1) would have cut it after
    // "part one" and thrown away two thirds of the reply.
    expect(branchIndexOf(turns, 1)).toBe(3)
    expect(branchIndexOf(turns, 1)).not.toBe(1)
    // …and every later turn stays aligned rather than drifting.
    expect(branchIndexOf(turns, 2)).toBe(4)
    expect(branchIndexOf(turns, 3)).toBe(5)
    expect(branchIndexOf(turns, 3)).toBe(visibleIndexOfLast(msgs, (m) => m.content === 'ok'))
  })

  it('branches at the RIGHT message when loop re-injections were collapsed', () => {
    // The native ReAct loop re-injects the same prompt each cycle:
    // user, tool, user, tool, assistant. hydrateTurns collapses the repeats into one
    // user bubble; the backend's visible list keeps BOTH user messages.
    const msgs: HistMsg[] = [
      { role: 'user', content: 'fix the build' },
      { role: 'tool', content: 'Terminal', meta: { tool_call_id: 't1', done: true } },
      { role: 'user', content: 'fix the build' },
      { role: 'tool', content: 'Read', meta: { tool_call_id: 't2', done: true } },
      { role: 'assistant', content: 'done' },
      { role: 'user', content: 'thanks' },
      { role: 'assistant', content: 'welcome' },
    ]
    const turns = hydrateTurns(msgs)
    expect(turns.map((t) => t.role)).toEqual(['user', 'assistant', 'user', 'assistant'])
    // 'thanks' is visible index 2 (user, user, thanks) — NOT turn index 2's naive 2…
    // it happens to coincide here, so assert the ANSWER, where the drift shows:
    // 'welcome' is visible index 3 while its turn index is also 3 — the collapse cost
    // one slot and the assistant merge gave none back. Derive both from the backend's
    // own filter so this test cannot encode the same mistake it is guarding.
    expect(branchIndexOf(turns, 2)).toBe(visibleIndexOfLast(msgs, (m) => m.content === 'thanks'))
    expect(branchIndexOf(turns, 3)).toBe(visibleIndexOfLast(msgs, (m) => m.content === 'welcome'))
    expect(branchIndexOf(turns, 1)).toBe(visibleIndexOfLast(msgs, (m) => m.content === 'done'))
  })

  it('a re-injection AND a merged answer compound — the naive index is 2 short', () => {
    const msgs: HistMsg[] = [
      { role: 'user', content: 'go' },
      { role: 'tool', content: 'Terminal', meta: { tool_call_id: 't1', done: true } },
      { role: 'user', content: 'go' },                 // re-injection: no turn, one slot
      { role: 'assistant', content: 'step one' },
      { role: 'assistant', content: 'step two' },      // merged: no turn, one slot
      { role: 'user', content: 'next' },
      { role: 'assistant', content: 'sure' },
    ]
    const turns = hydrateTurns(msgs)
    expect(turns.map((t) => t.role)).toEqual(['user', 'assistant', 'user', 'assistant'])
    const want = visibleIndexOfLast(msgs, (m) => m.content === 'sure')
    expect(want).toBe(5)
    expect(branchIndexOf(turns, 3)).toBe(want)
    // The bug this closes: the turn position is 3, two messages behind the truth.
    expect(want - 3).toBe(2)
  })

  it('branching the SAME message twice yields the same coordinate both times', () => {
    // Repeat-branching is a property of the endpoint (a fork is just another fork), so
    // the only way the FE could break it is by returning a different index on the
    // second click. The translation is pure — assert it is stable.
    const turns = hydrateTurns([
      { role: 'user', content: 'q' },
      { role: 'assistant', content: 'a1' },
      { role: 'assistant', content: 'a2' },
    ])
    expect(branchIndexOf(turns, 1)).toBe(2)
    expect(branchIndexOf(turns, 1)).toBe(branchIndexOf(turns, 1))
  })

  it('derives a coordinate for LIVE turns appended after a hydrated history', () => {
    // Turns built from WS frames carry no stamp. They must continue from the last
    // stamped turn, not restart at the array position.
    const turns: ChatTurn[] = hydrateTurns([
      { role: 'user', content: 'old q' },
      { role: 'assistant', content: 'old a part 1' },
      { role: 'assistant', content: 'old a part 2' },
    ])
    expect(turns[1].visibleIndex).toBe(2)
    turns.push(userTurn('new q'))            // live: no visibleIndex
    turns.push(assistantTurn('new a'))       // live: no visibleIndex
    expect(turns[2].visibleIndex).toBeUndefined()
    expect(branchIndexOf(turns, 2)).toBe(3)
    expect(branchIndexOf(turns, 3)).toBe(4)
  })

  it('a tool-only assistant turn holds no message slot, so it does not consume one', () => {
    const turns: ChatTurn[] = hydrateTurns([
      { role: 'user', content: 'q' },
      { role: 'assistant', content: 'a' },
    ])
    // A live turn carrying only a tool card (no text yet) sits between the stamped
    // answer and the next live turn; it must not shift the next coordinate.
    turns.push({ role: 'assistant', segments: [{ kind: 'tool', id: 'x', tool: 'Read', done: true }] })
    turns.push(assistantTurn('later text'))
    expect(branchIndexOf(turns, 3)).toBe(2)
  })

  it('falls back to the turn index when nothing is stamped (today’s behaviour)', () => {
    const turns: ChatTurn[] = [userTurn('a'), assistantTurn('b'), userTurn('c')]
    expect(branchIndexOf(turns, 2)).toBe(2)
  })

  it('is safe on an out-of-range index', () => {
    expect(branchIndexOf([], 4)).toBe(4)
  })
})

describe('branchParentKey — forked_from is a HISTORY key, routes take the bare key', () => {
  it('strips the dashboard: namespace the backend persists', () => {
    // chat_fork.py sets `new_session.forked_from = _history_key_for(session.key)`,
    // which is always `dashboard:<key>`. Linking to it unstripped would route to
    // `#/chat/dashboard:abc` and resolve to nothing.
    expect(branchParentKey('dashboard:abc123')).toBe('abc123')
  })

  it('tolerates the dashboard_ filename form and an already-bare key', () => {
    expect(branchParentKey('dashboard_abc123')).toBe('abc123')
    expect(branchParentKey('abc123')).toBe('abc123')
  })

  it('is empty for a session that was never branched', () => {
    expect(branchParentKey('')).toBe('')
    expect(branchParentKey(null)).toBe('')
    expect(branchParentKey(undefined)).toBe('')
  })

  it('round-trips a branch-of-a-branch key (nesting adds no encoding)', () => {
    // A leaf's forked_from points at the INTERMEDIATE branch, not the root
    // (test_dashboard_chat.py::test_fork_of_fork_chains_forked_from), so the
    // breadcrumb walks one hop at a time — each hop is the same plain translation.
    expect(branchParentKey('dashboard:mid-branch')).toBe('mid-branch')
  })
})
