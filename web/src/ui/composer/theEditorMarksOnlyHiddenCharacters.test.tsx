import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { MarkdownInput } from './MarkdownInput'

// ── The editor marks the characters a widget's message drops, and no others ─────────────────────
//
// Every MarkdownInput (the composer, the palette, search, the pickers) shows each character a
// widget's action is composed without as a visible mark, so a draft never reads otherwise than it
// sends. Ordinary text carries some of the same code points as part of its writing: the joiners
// in an emoji sequence and in Persian, the three tag flags' tags, and the selectors of a Japanese
// variant. Those show as written.

afterEach(() => cleanup())

/** *s*'s letters and digits as tag characters (U+E0020 to U+E007E). */
const tags = (s: string) => [...s].map((c) => String.fromCodePoint(0xe0000 + (c.codePointAt(0) ?? 0))).join('')

function marks(value: string): number {
  const view = render(<MarkdownInput value={value} onChange={() => {}} onSend={() => {}} canSend minHeight={40} maxHeight={200} />)
  const editor = view.container.querySelector('.cm-content')
  expect(editor, 'the editor mounted').toBeTruthy()
  return view.container.querySelectorAll('.cm-specialChar').length
}

describe('the editor\'s marks for hidden characters', () => {
  it('shows ordinary writing as written: emoji sequences, a flag\'s tags, Persian and Japanese', () => {
    const writing = [
      '\u{1F469}\u200d\u{1F4BB} at work',  // a ZWJ emoji sequence
      '\u{1F3F3}\ufe0f\u200d\u{1F308} pride',  // a flag built with VS16 and ZWJ
      '\u{1F3F4}\u{E0067}\u{E0062}\u{E0065}\u{E006E}\u{E0067}\u{E007F} England',  // a tag run
      '\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645',  // Persian with a ZWNJ
      '\u845b\u{E0100}\u57ce\u{E0101}',  // Japanese with IVS selectors
    ]
    for (const text of writing) {
      cleanup()
      expect(marks(text), text).toBe(0)
    }
  })

  it('🔴 marks what a widget\'s message drops, beyond the editor\'s own defaults', () => {
    // A word joiner, a braille blank, a Hangul filler, a lone tag and a text selector on a letter:
    // none of them in CodeMirror's default set, so the editor marks them only with the class given.
    for (const ch of ['\u2060', '\u2800', '\u3164', '\u{E0041}', '\ufe01', '\u202b', '\u2068']) {
      cleanup()
      expect(marks(`a${ch}b`), `U+${ch.codePointAt(0)?.toString(16).toUpperCase()}`).toBe(1)
    }
  })

  it('shows the three tag flags as written: England, Scotland and Wales', () => {
    for (const id of ['gbeng', 'gbsct', 'gbwls']) {
      cleanup()
      expect(marks(`\u{1F3F4}${tags(id)}\u{E007F} flag`), id).toBe(0)
    }
  })

  it('🔴 marks every tag that isn\'t one of the three flags\': after its CANCEL, unclosed, upper-case, stray or flag-shaped', () => {
    const flag = '\u{1F3F4}\u{E0067}\u{E0062}\u{E0065}\u{E006E}\u{E0067}\u{E007F}'
    const cases: Array<[string, string, number]> = [
      ['two tags after the flag\'s CANCEL', `${flag}\u{E0061}\u{E0062} x`, 2],
      ['a tag run no CANCEL closes', '\u{1F3F4}\u{E0067}\u{E0062} x', 2],
      ['upper-case tags and their CANCEL', '\u{1F3F4}\u{E0047}\u{E0042}\u{E007F} x', 3],
      ['a stray CANCEL', 'a\u{E007F}b', 1],
      ['a flag\'s tags not right after the flag', '\u{1F3F4} \u{E0067}\u{E0062}\u{E007F}', 3],
      ['seven tags and a CANCEL after the black flag', `\u{1F3F4}${tags('abcdefg')}\u{E007F}`, 8],
      ['forty-four tags and a CANCEL after the black flag', `\u{1F3F4}${tags('abcdefghijklmnopqrstuvwxyz0123456789abcdefgh')}\u{E007F}`, 45],
      ['two flag-shaped runs in a row', `\u{1F3F4}${tags('abcdef')}\u{E007F}\u{1F3F4}${tags('ghijkl')}\u{E007F}`, 14],
      ['a subdivision emoji has no flag for', `\u{1F3F4}${tags('ustx')}\u{E007F}`, 5],
    ]
    // Every row is read before the check, so a red names each row that is off, not only the first.
    const got = cases.map(([name, text]) => { cleanup(); return [name, marks(text)] })
    expect(got).toEqual(cases.map(([name, , n]) => [name, n]))
  })
})
