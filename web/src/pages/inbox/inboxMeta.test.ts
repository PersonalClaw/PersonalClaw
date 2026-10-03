import { describe, it, expect } from 'vitest'
import {
  CHANNEL_ITEM_KINDS, ITEM_KINDS, OPEN_STATUSES,
  isChannelItem, isOpenWithVerdict, itemKindOf, kindMeta, statusMeta, isOpen, refTarget, refLabel,
} from './inboxMeta'
import type { InboxItemKind } from '../../lib/api'

describe('item kinds', () => {
  it('reads a missing kind as a message, the server default for rows that predate kinds', () => {
    expect(kindMeta(undefined).key).toBe('message')
    expect(kindMeta('').key).toBe('message')
    expect(itemKindOf({})).toBe('message')
  })

  it('reads a kind it does not know as a system notice, never as a message', () => {
    // A kind this build does not know came through the one door that raises notices; reading it
    // as a message gave an app's update notice a Reply arrow, a triage verdict and a draft box.
    // It still renders: the meta it gets has an icon.
    expect(kindMeta('some-future-kind').key).toBe('system')
    expect(kindMeta('some-future-kind').icon).toBeTruthy()
    expect(itemKindOf({ item_kind: 'update' as InboxItemKind })).toBe('system')
  })

  it('resolves each declared kind to its own meta', () => {
    for (const k of ITEM_KINDS) expect(kindMeta(k.key).key).toBe(k.key)
  })

  it('gives every kind a label, icon and tone', () => {
    for (const k of ITEM_KINDS) {
      expect(k.label).toBeTruthy()
      expect(k.icon).toBeTruthy()
      expect(k.tone).toMatch(/^var\(--/)
    }
  })

  it('gives the reply machinery to the three channel kinds and to nothing else', () => {
    // Mirrors SOURCE_DECLARABLE_KINDS in inbox.py. An allowlist: a kind outside it — known or
    // not — has no channel, so a Send button on it would have nowhere to send.
    expect([...CHANNEL_ITEM_KINDS].sort()).toEqual(['email', 'mention', 'message'])
    for (const k of ITEM_KINDS) {
      expect(isChannelItem({ item_kind: k.key }), k.key).toBe(CHANNEL_ITEM_KINDS.includes(k.key))
    }
    expect(isChannelItem({})).toBe(true)
    expect(isChannelItem({ item_kind: 'update' as InboxItemKind })).toBe(false)
  })

  it('files a row under a triage verdict only when it is an open channel message', () => {
    // Every row is stored with the verdict `needs_reply` until something triages it, and only a
    // channel message is ever triaged.
    const row = (item_kind: string, status = 'pending') =>
      ({ item_kind: item_kind as InboxItemKind, classification: 'needs_reply' as const, status: status as 'pending' })
    expect(isOpenWithVerdict(row('email'), 'needs_reply')).toBe(true)
    expect(isOpenWithVerdict(row('message', 'seen'), 'needs_reply')).toBe(true)
    expect(isOpenWithVerdict(row('email', 'handled'), 'needs_reply')).toBe(false)
    for (const k of ['user_note', 'system', 'needs_input', 'agent_request', 'proposal', 'digest', 'update']) {
      expect(isOpenWithVerdict(row(k), 'needs_reply'), k).toBe(false)
    }
  })
})

describe('status', () => {
  it('knows seen', () => {
    expect(statusMeta('seen').key).toBe('seen')
    expect(statusMeta('seen').label).toBe('Seen')
  })

  it('treats pending and seen as open, everything else as resolved', () => {
    // The whole point of the 'open' filter: an item does not vanish from the user's list
    // just because they looked at it.
    expect(isOpen('pending')).toBe(true)
    expect(isOpen('seen')).toBe(true)
    expect(isOpen('handled')).toBe(false)
    expect(isOpen('dismissed')).toBe(false)
    expect(isOpen('sent')).toBe(false)
  })

  it('treats a missing status as open', () => {
    expect(isOpen(undefined)).toBe(true)
    expect(isOpen('')).toBe(true)
  })

  it('keeps OPEN_STATUSES and isOpen in agreement', () => {
    for (const s of OPEN_STATUSES) expect(isOpen(s)).toBe(true)
  })

  it('falls back to pending for an unknown status', () => {
    expect(statusMeta('nonsense').key).toBe('pending')
  })
})

describe('refTarget', () => {
  it('returns a BARE path, never a hash — navigate() owns hash mutation', () => {
    // Pages assigning location.hash directly is a doctrine violation with its own test;
    // refTarget feeds RouteProps.navigate(), which takes a path.
    const refs: Array<Record<string, string>> = [{ loop: 'L1' }, { session: 's' }, { workflow: 'w' }]
    for (const r of refs) {
      expect(refTarget({ refs: r }).startsWith('#')).toBe(false)
    }
  })

  it('routes a code loop to the code cockpit, not the loops cockpit', () => {
    // A code loop lives at code/<id>; sending it to loops/<id> lands on a page that
    // cannot render it.
    expect(refTarget({ refs: { loop: 'L1', loop_kind: 'code' } })).toBe('code/L1')
  })

  it('routes a non-code loop to the loops cockpit', () => {
    expect(refTarget({ refs: { loop: 'L1', loop_kind: 'goal' } })).toBe('loops/L1')
    expect(refTarget({ refs: { loop: 'L1' } })).toBe('loops/L1')
  })

  it('routes sessions and workflows', () => {
    expect(refTarget({ refs: { session: 'chat-1' } })).toBe('chat/chat-1')
    // The run's own route: `workflows/<id>` is no route of the Workflows section, which showed
    // its list instead of the run the row is about.
    expect(refTarget({ refs: { workflow: 'wf-1' } })).toBe('workflows/runs/wf-1')
  })

  it('encodes a session key that needs it', () => {
    expect(refTarget({ refs: { session: 'a/b c' } })).toBe('chat/a%2Fb%20c')
  })

  it('routes an identity-report row to its artifact, and never ahead of an older ref', () => {
    // The inbox row carries refs.artifact and nothing else, so this is the only branch
    // that can produce its link. The second assertion is the vacuity floor: the artifact
    // branch is LAST, so a row that also names a session must still go to the session —
    // without it, adding this branch would silently re-route existing rows.
    expect(refTarget({ refs: { artifact: 'learning-identity-report' } }))
      .toBe('artifacts/learning-identity-report')
    expect(refTarget({ refs: { artifact: 'a', session: 's1' } })).toBe('chat/s1')
  })

  it("routes a paused ROOM to its room, and never ahead of an older ref", () => {
    // AGENT-ROOMS' pause item (`agent/room_paused`) stamps `refs.room` and nothing else, so this is
    // that row's only link — and it is what makes the pause CARD the destination of the inbox row
    // rather than a second notice about one event. Same vacuity floor as the artifact branch above:
    // `room` is LAST, so a row that also names a session must still go to the session.
    expect(refTarget({ refs: { room: 'pricing-debate' } })).toBe('chat/room/pricing-debate')
    expect(refTarget({ refs: { room: 'r', session: 's1' } })).toBe('chat/s1')
    // A room id is a strict slug, but the encode is kept for the same reason every sibling has one:
    // the path segment is built, not trusted.
    expect(refTarget({ refs: { room: 'a b' } })).toBe('chat/room/a%20b')
  })

  it('sends the Inbox row of a pending approval to where the approval is ANSWERED', () => {
    // Derived by `approvalDestination`, the one parser of an approval's session key: a chat
    // opens the chat, and a workflow stage's synthetic key opens its run at the node — never a
    // `chat/workflow:…` route that 404s (#258). The label says where it goes.
    const chat = { refs: { approval: 'chat-a:1', session: 'chat-a' } }
    expect(refTarget(chat)).toBe('chat/chat-a')
    expect(refLabel(chat)).toBe('Open the chat')
    const stage = { refs: { approval: 'spawn:1', session: 'workflow:11b9a34c:synthesize' } }
    expect(refTarget(stage)).toBe('workflows/runs/11b9a34c?node=synthesize')
    expect(refLabel(stage)).toBe('Open the workflow run')
  })

  it("sends a room member's approval row to its room, never to a chat route that 404s", () => {
    // A member asks under its own session (`room:<room>:<member>`), which no chat route renders.
    const member = { refs: { approval: 'room:demo-room:talk-editor:c1', session: 'room:demo-room:talk-editor' } }
    expect(refTarget(member)).toBe('chat/room/demo-room')
    expect(refLabel(member)).toBe('Open the room')
    // An unanswered ask's note names the same session, so it goes to the same room.
    const note = { refs: { auto_denied: 'x', session: 'room:demo-room:talk-editor' } }
    expect(refTarget(note)).toBe('chat/room/demo-room')
    // A chat whose key merely starts with the word is still a chat.
    expect(refTarget({ refs: { approval: 'c:1', session: 'room-notes' } })).toBe('chat/room-notes')
  })

  it('sends the review item for triggers an upgrade brought over to the Triggers page', () => {
    // `legacy_import.announce` lists the waiting triggers in `refs.triggers`; they are reviewed and
    // switched on where they are listed. LAST in the chain, so an older ref still wins; an empty
    // list is nowhere to go.
    const review = { refs: { triggers: ['event:deploy-hook'], dedup_key: 'legacy_import:x' } }
    expect(refTarget(review)).toBe('triggers')
    expect(refLabel(review)).toBe('Go to Triggers')
    expect(refTarget({ refs: { triggers: ['t'], session: 's1' } })).toBe('chat/s1')
    expect(refTarget({ refs: { triggers: [] } })).toBe('')
  })

  it("sends a trigger's filed failure where its note leads: the trigger, or the run it started", () => {
    // `triggers.delivery` files a failure routed to the Inbox with its note's `statusUrl`. Before,
    // the row had nowhere to go, so a failed automation's Inbox item led back to nothing.
    const failed = { refs: { statusUrl: '#/triggers?open=clock:feed-digest', trigger: 'clock:feed-digest' } }
    expect(refTarget(failed)).toBe('triggers?open=clock:feed-digest')
    expect(refLabel(failed)).toBe('Open trigger')
    const run = { refs: { statusUrl: '#/workflows/runs/3f2a91c0', trigger: 'clock:research' } }
    expect(refTarget(run)).toBe('workflows/runs/3f2a91c0')
    expect(refLabel(run)).toBe('Open run')
    // An older ref still wins, and only an in-app route is ever followed.
    expect(refTarget({ refs: { statusUrl: '#/triggers?open=t', session: 's1' } })).toBe('chat/s1')
    expect(refTarget({ refs: { statusUrl: 'https://example.com/elsewhere' } })).toBe('')
    expect(refLabel({ refs: { statusUrl: 'https://example.com/elsewhere' } })).toBe('Go to source')
  })

  it('returns empty when there is nowhere to go', () => {
    // The row then renders no deep-link affordance at all, rather than a dead link.
    expect(refTarget({ refs: {} })).toBe('')
    expect(refTarget({})).toBe('')
    expect(refTarget({ refs: { dedup_key: 'k' } })).toBe('')
  })
})

describe('refLabel', () => {
  it('names the referent, not the item kind', () => {
    // "Go to needs you" is what you get from de-pluralizing a chip label; the button
    // should name where it takes you.
    expect(refLabel({ refs: { loop: 'L1' } })).toBe('Go to loop')
    expect(refLabel({ refs: { session: 's1' } })).toBe('Go to chat')
    expect(refLabel({ refs: { workflow: 'w1' } })).toBe('Open the workflow run')
    expect(refLabel({ refs: { artifact: 'learning-identity-report' } })).toBe('Open the report')
    expect(refLabel({ refs: { room: 'pricing-debate' } })).toBe('Go to the room')
  })

  it('falls back to a generic label', () => {
    expect(refLabel({ refs: {} })).toBe('Go to source')
    expect(refLabel({})).toBe('Go to source')
  })

  it('agrees with refTarget about whether there is a destination', () => {
    // A button that says "Go to loop" while refTarget returns '' would be a dead control.
    const cases: Array<{ refs: Record<string, string> }> = [
      { refs: { loop: 'L1' } }, { refs: { session: 's' } }, { refs: {} },
    ]
    for (const c of cases) {
      const hasTarget = refTarget(c) !== ''
      expect(refLabel(c) !== 'Go to source').toBe(hasTarget)
    }
  })
})
