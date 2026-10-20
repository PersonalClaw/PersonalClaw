import { describe, it, expect } from 'vitest'
import { serializeJsonField, parseJsonField } from './appConfigForm'

// Regression: array/object-typed app config fields (first shipped by the slack-channel
// app — allowed_users/tracking_channels/reactions/channels) rendered as a plain text
// input showing the literal "[object Object]", and any save serialized that string —
// which the backend type-validation then REJECTS. The JSON editor round-trips them.

describe('serializeJsonField', () => {
  it('pretty-prints an array value', () => {
    expect(serializeJsonField([{ channel_id: 'C1', name: 'x' }], 'array'))
      .toBe('[\n  {\n    "channel_id": "C1",\n    "name": "x"\n  }\n]')
  })

  it('renders empty containers for null/undefined (never "[object Object]")', () => {
    expect(serializeJsonField(undefined, 'array')).toBe('[]')
    expect(serializeJsonField(null, 'object')).toBe('{}')
    expect(serializeJsonField({}, 'object')).toBe('{}')
  })

  it('never emits the [object Object] stringification bug', () => {
    expect(serializeJsonField({ a: 1 }, 'object')).not.toContain('[object Object]')
  })
})

describe('parseJsonField', () => {
  it('parses valid array JSON', () => {
    expect(parseJsonField('["C_A", "C_B"]', 'array')).toEqual({ value: ['C_A', 'C_B'] })
  })

  it('parses valid object JSON', () => {
    expect(parseJsonField('{"C1": {"activation": "mention"}}', 'object'))
      .toEqual({ value: { C1: { activation: 'mention' } } })
  })

  it('empty text clears to the empty container', () => {
    expect(parseJsonField('   ', 'array')).toEqual({ value: [] })
    expect(parseJsonField('', 'object')).toEqual({ value: {} })
  })

  it('rejects malformed JSON with an inline error (no corrupt value)', () => {
    const r = parseJsonField('[not json', 'array')
    expect(r).toEqual({ error: 'invalid JSON' })
  })

  it('rejects a wrong-shape value (array where object expected and vice versa)', () => {
    expect(parseJsonField('[]', 'object')).toEqual({ error: 'must be a JSON object' })
    expect(parseJsonField('{}', 'array')).toEqual({ error: 'must be a JSON array' })
    expect(parseJsonField('42', 'array')).toEqual({ error: 'must be a JSON array' })
    expect(parseJsonField('null', 'object')).toEqual({ error: 'must be a JSON object' })
  })
})

describe('a long number in a JSON setting', () => {
  // `JSON.parse` turns the ID 1289011223344556677 into 1289011223344556800, and the form would save
  // that: a different ID, with nothing on screen saying so. Such a number is refused with the fix;
  // in quotes it is text, which keeps every digit.
  it('is refused rather than saved rounded, naming it and how to keep it', () => {
    const r = parseJsonField('{"application_id": 1289011223344556677}', 'object')
    expect('error' in r && r.error).toContain('1289011223344556677')
    expect('error' in r && r.error).toContain('quotes')
  })

  it('in quotes keeps every digit', () => {
    expect(parseJsonField('{"application_id": "1289011223344556677"}', 'object'))
      .toEqual({ value: { application_id: '1289011223344556677' } })
  })

  it('an ordinary number, and digits inside a string, are left alone', () => {
    expect(parseJsonField('[12, -3.5, 1e3]', 'array')).toEqual({ value: [12, -3.5, 1000] })
    expect(parseJsonField('{"note": "call 1289011223344556677"}', 'object'))
      .toEqual({ value: { note: 'call 1289011223344556677' } })
  })
})
