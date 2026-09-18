import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, it, expect } from 'vitest'
import { OPTIONS_PATTERN, parseOptions, parseSwitchToAgent, splitFileRefs } from './parseAssistant'

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

  // #540: the stripper matched `/\[\s*OPTIONS?\s*:\s*([^\]]+)\]\s*$/i` — case-INsensitive,
  // singular-tolerant, loose-spacing — three widenings past the shape the retired emitter
  // actually produced. Each one pulls in ordinary prose, and because the render path keeps
  // only `text.slice(0, m.index)`, the matched clause AND the marker vanish from the bubble
  // on both hot paths (the transcript and the session-list preview). The cases below are the
  // ones that made it greedy; each was verified to lose its tail before the narrowing.
  const PROSE_KEPT_VERBATIM = [
    ['lowercase plural, trailing', 'Configure it via [options: verbose | quiet]'],
    ['uppercase SINGULAR, trailing', 'See the CLI flags [OPTION: --json]'],
    ['lowercase singular, trailing', 'The array is indexed [option: 0]'],
    ['loose inner spacing, lowercase', 'Pick.\n[ options :  A  |  B  ]'],
    ['mid-prose mention of the real marker', 'I used to emit [OPTIONS: a | b] markers, but not anymore.'],
    ['no marker at all', 'Just prose, no marker here.'],
    ['a fenced block whose CONTENT looks like the marker', 'Run this:\n```\n[OPTIONS: a | b]\n```'],
    ['bracketed prose spanning a newline', 'See [OPTIONS:\nnot a marker]'],
  ] as const

  it.each(PROSE_KEPT_VERBATIM)('keeps %s verbatim', (_label, text) => {
    expect(parseOptions(text)).toEqual({ body: text, options: [] })
  })

  it('still strips a degenerate empty-label marker (leaving it would leak the raw tag)', () => {
    // `[OPTIONS: ]` is marker-SHAPED. The one thing this stripper exists to prevent is a
    // raw `[OPTIONS: …]` reaching the user, and the retired emitter never produced a bare
    // label list for this to collide with — so it is stripped, with no options.
    expect(parseOptions('Nothing to choose [OPTIONS: ]')).toEqual({ body: 'Nothing to choose', options: [] })
  })

  // The general property the case table above cannot see: whatever the input, the body is a
  // PREFIX of it (nothing is rewritten or reordered) and the only text that may be dropped is
  // a trailing run of exact markers. An enumerated rail can't cover inputs nobody thought of;
  // this one holds for all of them.
  it('never drops anything but exact trailing markers, for every case in this file', () => {
    const all = [
      ...PROSE_KEPT_VERBATIM.map(([, t]) => t),
      'Here are your choices.\n[OPTIONS: Ship it | Hold off]',
      'Answer.\n[OPTIONS: A | B]\n[OPTIONS: C | D]',
      'Answer.\n[OPTIONS: A | B]\nMore prose after the marker.',
      '[OPTIONS: only]',
      '',
    ]
    for (const text of all) {
      const { body } = parseOptions(text)
      expect(text.startsWith(body), `body must be a prefix of the input for ${JSON.stringify(text)}`).toBe(true)
      // Everything removed is whitespace + exact `[OPTIONS: …]` markers, nothing else.
      const dropped = text.slice(body.length)
      expect(dropped.replace(/\s+/g, '').replace(/\[OPTIONS:[^\]\n]+\]/g, ''),
        `only markers may be dropped, got ${JSON.stringify(dropped)}`).toBe('')
    }
  })

  it('strips a MID-text marker only from the tail — following prose survives', () => {
    // The backend's mirror-image defect: `re.MULTILINE` let `$` match end-of-LINE, so this
    // input stripped from the marker to the very end and returned just "Answer.".
    const text = 'Answer.\n[OPTIONS: A | B]\nMore prose after the marker.'
    expect(parseOptions(text).body).toBe(text)
  })

  it('strips STACKED trailing markers so no raw marker leaks (idempotent)', () => {
    const text = 'Answer.\n[OPTIONS: A | B]\n[OPTIONS: C | D]'
    const once = parseOptions(text)
    expect(once.body).toBe('Answer.')
    expect(once.body).not.toContain('[OPTIONS:')
    expect(once.options).toEqual(['C', 'D']) // the LAST (operative) marker's labels
    expect(parseOptions(once.body).body).toBe(once.body) // f(f(x)) === f(x)
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

// #540's real defect was not one loose regex — it was TWO hand-maintained copies of one
// contract, which had drifted in opposite directions (the FE ate trailing prose via `/i` +
// `OPTIONS?`; the backend ate FOLLOWING prose via `re.MULTILINE`). Narrowing one copy only
// buys back the drift, so the pattern source text is pinned across the language boundary here.
describe('the OPTIONS stripper is ONE contract, not two implementations', () => {
  // Resolved off THIS file, not off cwd: a rail that only finds its counterpart when the
  // runner happens to start in `web/` is a rail that can vanish silently.
  const TEXTFMT_PY = join(import.meta.dirname, '..', '..', '..', '..', 'src', 'personalclaw', 'textfmt.py')

  it('uses a pattern character-identical to the backend stripper', () => {
    // Throws (loudly) if the module is moved/renamed — this rail must never pass vacuously.
    const py = readFileSync(TEXTFMT_PY, 'utf8')
    const m = py.match(/^_OPTIONS_PATTERN = r"(.+)"$/m)
    expect(m, `_OPTIONS_PATTERN not found in ${TEXTFMT_PY} — did the backend stripper move?`).not.toBeNull()
    const backendPattern = m![1]
    expect(backendPattern.length).toBeGreaterThan(10)
    // `OPTIONS_PATTERN` is a JS string literal, so its runtime value is the regex SOURCE —
    // the same text Python's raw string carries.
    expect(OPTIONS_PATTERN).toBe(backendPattern)
  })

  it('and that shared pattern actually strips the marker (so the rail above cannot pass on a dud)', () => {
    const py = readFileSync(TEXTFMT_PY, 'utf8')
    const backendPattern = py.match(/^_OPTIONS_PATTERN = r"(.+)"$/m)![1]
    expect('Pick.\n[OPTIONS: A | B]'.match(new RegExp(backendPattern))).not.toBeNull()
    expect('Configure it via [options: verbose | quiet]'.match(new RegExp(backendPattern))).toBeNull()
  })

  it('is case-SENSITIVE and plural-only, as the backend has always been', () => {
    const re = new RegExp(OPTIONS_PATTERN)
    expect(re.flags).toBe('') // no `/i`, no `/m`
    expect('x [OPTIONS: a]'.match(re)).not.toBeNull()
    expect('x [options: a]'.match(re)).toBeNull()
    expect('x [OPTION: a]'.match(re)).toBeNull()
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
