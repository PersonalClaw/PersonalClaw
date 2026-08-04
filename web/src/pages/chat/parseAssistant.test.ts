import { describe, it, expect } from 'vitest'
import { parseOptions, parseSwitchToAgent, splitFileRefs } from './parseAssistant'

// The legacy `[OPTIONS: …]` suggestion mechanism is RETIRED: nothing instructs the
// model to emit the marker and the chat UI never renders it as buttons (follow-up
// chips from the `chat_followups` event are the single suggestion surface). But
// messages persisted BEFORE the retirement still carry the marker in their text, so
// `parseOptions` must keep working as a STRIPPER — a historical reply has to render
// its prose without leaking a raw `[OPTIONS: a | b]` string into the UI.
describe('parseOptions (legacy-marker stripper)', () => {
  it('strips a trailing marker from a historical message body', () => {
    const { body } = parseOptions('Here are your choices.\n[OPTIONS: Ship it | Hold off]')
    expect(body).toBe('Here are your choices.')
    expect(body).not.toContain('[OPTIONS:')
  })

  it('leaves text without a marker untouched', () => {
    const text = 'Just prose, no marker here.'
    expect(parseOptions(text)).toEqual({ body: text, options: [] })
  })

  it('strips case-insensitively and tolerates loose spacing / the singular form', () => {
    expect(parseOptions('Pick.\n[ options :  A  |  B  ]').body).toBe('Pick.')
    expect(parseOptions('Pick.\n[OPTION: Only one]').body).toBe('Pick.')
  })

  it('only strips a TRAILING marker — a mid-prose mention stays as written', () => {
    const text = 'I used to emit [OPTIONS: a | b] markers, but not anymore.'
    expect(parseOptions(text).body).toBe(text)
  })

  it('still parses the labels so the stripper can be reasoned about, but the chat UI renders none of them as buttons', () => {
    // Retiring the RENDERER (not the parse) is the fix: nothing in the chat render
    // path reads `.options`, so a historical marker produces zero buttons.
    expect(parseOptions('Pick.\n[OPTIONS: A | B | C]').options).toEqual(['A', 'B', 'C'])
  })

  it('composes with the switch-to-agent stripper the render path chains it with', () => {
    // ChatPage renders `parseSwitchToAgent(parseOptions(text).body).body`.
    const raw = 'Done reviewing.\n[OPTIONS: Fix it | Leave it]'
    const body = parseSwitchToAgent(parseOptions(raw).body).body
    expect(body).toBe('Done reviewing.')
    expect(body).not.toContain('OPTIONS')
  })
})

describe('parseSwitchToAgent', () => {
  it('extracts a trailing continuation and strips the marker', () => {
    const { body, switchTo } = parseSwitchToAgent('Here is the plan.\n[SWITCH_TO_AGENT: execute it]')
    expect(body).toBe('Here is the plan.')
    expect(switchTo).toBe('execute it')
  })

  it('reports a bare marker as an empty continuation, not as absent', () => {
    expect(parseSwitchToAgent('Ready.\n[SWITCH_TO_AGENT:]').switchTo).toBe('')
  })

  it('returns null when there is no marker', () => {
    expect(parseSwitchToAgent('No marker.').switchTo).toBeNull()
  })
})

describe('splitFileRefs', () => {
  it('splits an absolute path out of surrounding prose', () => {
    expect(splitFileRefs('edited /tmp/a/main.py today')).toEqual([
      { kind: 'text', value: 'edited ' },
      { kind: 'file', value: '/tmp/a/main.py' },
      { kind: 'text', value: ' today' },
    ])
  })

  it('returns a single text part when there is no path', () => {
    expect(splitFileRefs('no paths here')).toEqual([{ kind: 'text', value: 'no paths here' }])
  })
})
