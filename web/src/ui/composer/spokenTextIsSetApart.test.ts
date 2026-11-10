import { describe, it, expect } from 'vitest'
import { spacedInsertion } from './spokenText'

// ── A dictation is set apart from the text around it ────────────────────────────────────────
//
// What the composer inserts for a transcript, given the text before the caret (or the selection) and
// after it: a space on a side whose neighbour is not whitespace, none at either end of the draft,
// inside an opening bracket, quote or Markdown marker run, or before closing punctuation, a closing
// quote or a closing marker run.

describe('a dictation is set apart from the text around it', () => {
  it('adds nothing in an empty draft', () => {
    expect(spacedInsertion('', '', 'second')).toEqual({ insert: 'second', caret: 6 })
  })

  it('adds a space after a word or a full stop', () => {
    expect(spacedInsertion('.', '', 'second')).toEqual({ insert: ' second', caret: 7 })
    expect(spacedInsertion('y', '', 'second')).toEqual({ insert: ' second', caret: 7 })
  })

  it('adds none after a space, a line break or an opening bracket', () => {
    expect(spacedInsertion(' ', '', 'second').insert).toBe('second')
    expect(spacedInsertion('\n', '', 'second').insert).toBe('second')
    expect(spacedInsertion('(', '', 'second').insert).toBe('second')
  })

  it('adds a space before a word, and none before a space or closing punctuation', () => {
    expect(spacedInsertion('', 'd', 'spoken words')).toEqual({ insert: 'spoken words ', caret: 12 })
    expect(spacedInsertion('', ' ', 'spoken words').insert).toBe('spoken words')
    expect(spacedInsertion('', '.', 'spoken words').insert).toBe('spoken words')
    expect(spacedInsertion('', ')', 'spoken words').insert).toBe('spoken words')
  })

  it('sets a dictation into the middle of a sentence with one space on each side', () => {
    expect(spacedInsertion('o', 't', 'and')).toEqual({ insert: ' and ', caret: 4 })
  })

  it('adds no space inside an opening quote, nor before a closing one', () => {
    // say "|  — an opening quote: what follows it is the quotation.
    expect(spacedInsertion(' "', '', 'words').insert).toBe('words')
    // "hello|"  — a closing quote: the dictation joins the quotation and the quote closes it.
    expect(spacedInsertion('lo', '"', 'words').insert).toBe(' words')
    // |'quoted' at the draft's start — the quote opens what follows, so the dictation stands apart.
    expect(spacedInsertion('', "'", 'words').insert).toBe('words ')
  })

  it('adds no space inside Markdown markers, so a dictation into **|** stays bold', () => {
    expect(spacedInsertion('**', '*', 'words').insert).toBe('words')
    expect(spacedInsertion(' `', '`', 'code').insert).toBe('code')
    expect(spacedInsertion('_', '_', 'words').insert).toBe('words')
  })

  // **bold**|  and  `ls`|  — the markers close what they wrap, so the next words stand apart; and
  // |_this_  and  |**bold**  — the markers open what follows, so the dictation stands apart.
  it.each([
    ['**bold**', '', 'next', ' next'],
    ['run `ls`', '', 'then', ' then'],
    ['see ', '_this_', 'words', 'words '],
    ['', '**bold**', 'words', 'words '],
  ])('sets a dictation apart from closed Markdown, as from a word: %j|%j', (before, after, text, insert) => {
    expect(spacedInsertion(before, after, text).insert).toBe(insert)
  })

  it('still sets a dictation apart after a closing quote', () => {
    expect(spacedInsertion('i"', '', 'next').insert).toBe(' next')
  })

  it('trims the transcript’s own whitespace, and inserts nothing for a blank one', () => {
    expect(spacedInsertion('.', 'w', '  second  ')).toEqual({ insert: ' second ', caret: 7 })
    expect(spacedInsertion('.', 'w', '   ')).toEqual({ insert: '', caret: 0 })
  })
})
