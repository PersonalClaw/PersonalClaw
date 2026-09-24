import { describe, it, expect } from 'vitest'
import { COMPANION_PATH, deepLinkFor, isPushPayload, notificationFor, shouldFocus, soundMapFromRules, PUSH_CUES } from './pushPolicy'
import { CUES } from '../design/soundCues'

// ── Push policy (MOBILE-COMPANION MC-5 / T3.4) ───────────────────────────────
//
// The browser half of the ids-only promise. The backend refuses to PUT content in a
// payload (`tests/test_mc5_push_to_approval.py` decrypts what it sent and reads the keys);
// this file asserts the other half — that nothing from the wire is ever RENDERED, so a
// future backend edit that started including a title could not surface it.
//
// Both halves are needed. Either one alone is one careless change away from a leak.

describe('the payload gate', () => {
  it('accepts exactly {kind, item_id} of strings', () => {
    expect(isPushPayload({ kind: 'approval', item_id: 'a1' })).toBe(true)
    expect(isPushPayload({ kind: 'approval', item_id: '' })).toBe(true)
  })

  it('rejects a payload carrying a THIRD key', () => {
    // The leak shape. A payload with a title did not come from `personalclaw.push`, so
    // this worker refuses it outright rather than rendering the part it recognises.
    expect(isPushPayload({ kind: 'approval', item_id: 'a1', title: 'Run rm -rf /' })).toBe(false)
  })

  it('rejects a missing key, a wrong type, and a non-object', () => {
    expect(isPushPayload({ kind: 'approval' })).toBe(false)
    expect(isPushPayload({ item_id: 'a1' })).toBe(false)
    expect(isPushPayload({ kind: '', item_id: 'a1' })).toBe(false)
    expect(isPushPayload({ kind: 'approval', item_id: 7 })).toBe(false)
    expect(isPushPayload(null)).toBe(false)
    expect(isPushPayload('approval')).toBe(false)
  })
})

describe('the notification the user sees', () => {
  it('composes its words from the KIND, never from the payload', () => {
    const note = notificationFor({ kind: 'approval', item_id: 'SECRET-payroll.csv' })
    expect(note.title).toBe('Approval needed')
    expect(note.body).toBe('A run is waiting for your decision.')
    // The id appears in the deep link (it has to — that is how the card is found) and in
    // the coalescing tag, and NOWHERE a human reads.
    expect(note.title).not.toContain('SECRET')
    expect(note.body).not.toContain('SECRET')
  })

  it('falls back to fixed generic copy rather than painting an unknown kind', () => {
    // A raw `kind` in the title would let a malformed payload write its own notification.
    const note = notificationFor({ kind: 'not-a-real-kind-<script>', item_id: 'x' })
    expect(note.title).toBe('PersonalClaw')
    expect(note.title).not.toContain('script')
    expect(note.body).not.toContain('not-a-real-kind')
  })

  it('coalesces per ITEM, so a retry replaces but a second approval does not', () => {
    const first = notificationFor({ kind: 'approval', item_id: 'a1' })
    const retry = notificationFor({ kind: 'approval', item_id: 'a1' })
    const other = notificationFor({ kind: 'approval', item_id: 'a2' })
    expect(retry.tag).toBe(first.tag)
    expect(other.tag).not.toBe(first.tag)
  })

  it('makes an approval require interaction and nothing else does', () => {
    expect(notificationFor({ kind: 'approval', item_id: 'a1' }).requireInteraction).toBe(true)
    expect(notificationFor({ kind: 'inbox_alert', item_id: 'i1' }).requireInteraction).toBe(false)
  })
})

describe('the deep link', () => {
  it('addresses the specific approval card', () => {
    expect(deepLinkFor('approval', 'ap-1')).toBe('/#/companion?approval=ap-1')
  })

  it('percent-encodes the id', () => {
    // An unescaped `&` would split the query and the card would never be found.
    expect(deepLinkFor('approval', 'a&b=c')).toBe('/#/companion?approval=a%26b%3Dc')
  })

  it('falls back to the bare companion route when there is nothing to address', () => {
    expect(deepLinkFor('approval', '')).toBe(COMPANION_PATH)
    expect(deepLinkFor('inbox_alert', 'i1')).toBe(COMPANION_PATH)
  })
})

describe('client reuse on click', () => {
  it('focuses an existing same-origin window instead of opening another', () => {
    // Matched on ORIGIN, not path: the companion is a hash route, so `?approval=a` and
    // `?approval=b` are the same document. A path comparison would open one window per
    // approval.
    expect(shouldFocus('https://gw.example/#/companion?approval=a', 'https://gw.example')).toBe(true)
    expect(shouldFocus('https://gw.example/#/settings', 'https://gw.example')).toBe(true)
  })

  it('refuses a different origin and an unparseable url', () => {
    expect(shouldFocus('https://evil.example/#/companion', 'https://gw.example')).toBe(false)
    expect(shouldFocus('not a url', 'https://gw.example')).toBe(false)
  })
})

// ── The per-kind push voice (MOBILE-COMPANION MC-6) ──────────────────────────
//
// The VOICE is the one part of a push notification that is a user preference, so it is read
// from the per-kind rules map — never from the wire (that would reopen the leak the rest of
// this file guards) and never from a fixed table (that would make it un-configurable).

describe('the closed push voice set', () => {
  it('matches soundCues CUES exactly — the two must not drift', () => {
    // The vocabulary lives in `design/soundCues.ts`; `pushPolicy` mirrors it because the
    // service-worker program cannot import that DOM-heavy module. This is the pin.
    expect([...PUSH_CUES].sort()).toEqual(Object.keys(CUES).sort())
  })
})

describe('notificationFor resolves the voice from the rules, not the wire or a fixed table', () => {
  it('plays the voice the rules configured for that kind', () => {
    const note = notificationFor({ kind: 'approval', item_id: 'a1' }, { approval: 'coin_blip' })
    expect(note.sound).toBe('coin_blip')
  })

  it('is silent when the kind has no configured voice — absent is the default', () => {
    expect(notificationFor({ kind: 'approval', item_id: 'a1' }, {}).sound).toBeUndefined()
    expect(notificationFor({ kind: 'approval', item_id: 'a1' }).sound).toBeUndefined()
  })

  it('drops an unknown voice rather than handing the client an unplayable one', () => {
    const note = notificationFor({ kind: 'approval', item_id: 'a1' }, { approval: 'ka-ching' })
    expect(note.sound).toBeUndefined()
  })

  it('reads the voice per kind while the WORDS still come from the fixed table', () => {
    const note = notificationFor({ kind: 'inbox_alert', item_id: 'i1' }, { inbox_alert: 'terminal_bell' })
    expect(note.sound).toBe('terminal_bell')
    expect(note.title).toBe('Inbox alert')
  })
})

describe('soundMapFromRules', () => {
  it('keys the map by WIRE kind and keeps only known, present voices', () => {
    const doc = {
      rules: [
        { key: 'approval/requested', wire: 'approval', sound: 'coin_blip' },
        { key: 'inbox/alert', wire: 'inbox_alert', sound: null },
        { key: 'x/y', wire: 'xy', sound: 'ka-ching' },
        { key: 'z/z', sound: 'error' },
      ],
    }
    // Only the row with a string `wire` AND a registered voice survives.
    expect(soundMapFromRules(doc)).toEqual({ approval: 'coin_blip' })
  })

  it('is defensive against a malformed document — degrades to no cue', () => {
    expect(soundMapFromRules(null)).toEqual({})
    expect(soundMapFromRules({})).toEqual({})
    expect(soundMapFromRules({ rules: 'nope' })).toEqual({})
  })
})
