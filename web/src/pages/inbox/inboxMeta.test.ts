import { describe, it, expect } from 'vitest'
import {
  ITEM_KINDS, NON_CHANNEL_ITEM_KINDS, OPEN_STATUSES,
  kindMeta, statusMeta, isOpen, refTarget, refLabel,
} from './inboxMeta'

describe('item kinds', () => {
  it('falls back to message for an unknown or missing kind', () => {
    // An item written by a NEWER build may carry a kind this build doesn't know; it must
    // still render as a row rather than crash on an undefined icon.
    expect(kindMeta(undefined).key).toBe('message')
    expect(kindMeta('').key).toBe('message')
    expect(kindMeta('some-future-kind').key).toBe('message')
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

  it('lists non-channel kinds that must not render reply affordances', () => {
    // Mirrors NON_CHANNEL_KINDS in inbox.py. A drift here means the UI shows a Send
    // button on a row with nowhere to send.
    expect(NON_CHANNEL_ITEM_KINDS).toContain('needs_input')
    expect(NON_CHANNEL_ITEM_KINDS).toContain('proposal')
    expect(NON_CHANNEL_ITEM_KINDS).not.toContain('message')
    expect(NON_CHANNEL_ITEM_KINDS).not.toContain('mention')
    expect(NON_CHANNEL_ITEM_KINDS).not.toContain('email')
  })

  it('declares a meta row for every non-channel kind', () => {
    const known = new Set(ITEM_KINDS.map((k) => k.key))
    for (const k of NON_CHANNEL_ITEM_KINDS) expect(known.has(k)).toBe(true)
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
    expect(refTarget({ refs: { workflow: 'wf-1' } })).toBe('workflows/wf-1')
  })

  it('encodes a session key that needs it', () => {
    expect(refTarget({ refs: { session: 'a/b c' } })).toBe('chat/a%2Fb%20c')
  })

  it('routes an identity-report row to its artifact, and never ahead of an older ref', () => {
    // LV-4's inbox row carries refs.artifact and nothing else, so this is the only branch
    // that can produce its link. The second assertion is the vacuity floor: the artifact
    // branch is LAST, so a row that also names a session must still go to the session —
    // without it, adding this branch would silently re-route existing rows.
    expect(refTarget({ refs: { artifact: 'learning-identity-report' } }))
      .toBe('artifacts/learning-identity-report')
    expect(refTarget({ refs: { artifact: 'a', session: 's1' } })).toBe('chat/s1')
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
    expect(refLabel({ refs: { workflow: 'w1' } })).toBe('Go to workflow')
    expect(refLabel({ refs: { artifact: 'learning-identity-report' } })).toBe('Open the report')
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
