import { describe, expect, it } from 'vitest'
import type { DocumentBlock, DocumentRun } from '../../lib/api'
import { applyMark, blockText, mergeRuns, selectionHasMark, setBlockText } from './documentModelEdit'

// ── Bolding a WORD, not a paragraph ──────────────────────────────────────────
//
// Runs are the unit of character formatting and a user selects CHARACTERS, so the whole
// question this file answers is whether a selection turns into the right RUN SPLIT. The
// clause it serves — "a user bolds a word, saves, and the downloaded file opens bold in
// Word" — is only true if the split is exact: bolding the containing run instead would
// bold text the user never selected, and Word would show it.
//
// These are pure functions on purpose. The component that hosts them holds no formatting
// logic at all, which is what makes "did bolding change the document?" answerable without
// a DOM, and what lets the browser-side and server-side halves be checked separately.

const run = (text: string, marks: Partial<DocumentRun> = {}): DocumentRun =>
  ({ text, bold: false, italic: false, code: false, link: '', ...marks })

const para = (runs: DocumentRun[], text = ''): DocumentBlock => ({
  kind: 'paragraph', text, level: 1, items: [], rows: [],
  artifact_slug: '', runs, cells: [], style: null,
})

describe('applyMark splits runs at the selection edges', () => {
  it('bolds exactly the selected word out of one run', () => {
    const block = para([run('a plain word')])
    const next = applyMark(block, 2, 7, 'bold', true)
    expect(next.runs.map((r) => [r.text, r.bold])).toEqual([
      ['a ', false], ['plain', true], [' word', false],
    ])
    // The visible text is untouched — a format change must never edit content.
    expect(blockText(next)).toBe('a plain word')
  })

  it('clears the stale plain-text shadow so the model does not override its own runs', () => {
    // `Block.__post_init__` treats a non-empty `text` beside `runs` as a deliberate plain
    // override. Leaving the old text here is how a bold edit gets silently discarded on
    // the way through the writer.
    const next = applyMark(para([run('a plain word')], 'a plain word'), 2, 7, 'bold', true)
    expect(next.text).toBe('')
  })

  it('materializes runs for a block the parser gave only text', () => {
    const next = applyMark(para([], 'plain text'), 0, 5, 'italic', true)
    expect(next.runs.map((r) => [r.text, r.italic])).toEqual([['plain', true], [' text', false]])
  })

  it('spans a selection across two runs', () => {
    const next = applyMark(para([run('one '), run('two', { italic: true })]), 2, 6, 'bold', true)
    expect(next.runs.map((r) => [r.text, r.bold, r.italic])).toEqual([
      ['on', false, false], ['e ', true, false], ['tw', true, true], ['o', false, true],
    ])
  })

  it('unbolds a run without touching its other marks', () => {
    const block = para([run('a '), run('word', { bold: true, italic: true })])
    const next = applyMark(block, 2, 6, 'bold', false)
    expect(next.runs.map((r) => [r.text, r.bold, r.italic])).toEqual([
      ['a ', false, false], ['word', false, true],
    ])
  })

  it('is a no-op for an empty selection — a caret is not a range', () => {
    const block = para([run('a plain word')])
    expect(applyMark(block, 3, 3, 'bold', true)).toBe(block)
  })
})

describe('selectionHasMark answers what a toggle button should show', () => {
  const block = para([run('a '), run('plain', { bold: true }), run(' word')])
  it('is true only when the WHOLE selection carries the mark', () => {
    expect(selectionHasMark(block, 2, 7, 'bold')).toBe(true)
    expect(selectionHasMark(block, 0, 7, 'bold')).toBe(false)
    expect(selectionHasMark(block, 2, 7, 'italic')).toBe(false)
  })
  it('is false for an empty selection', () => {
    expect(selectionHasMark(block, 2, 2, 'bold')).toBe(false)
  })
})

describe('mergeRuns keeps the run list from growing without bound', () => {
  it('folds adjacent runs with identical formatting and drops empties', () => {
    expect(mergeRuns([run('a'), run(''), run('b'), run('c', { bold: true })]).map((r) => r.text))
      .toEqual(['ab', 'c'])
  })
  it('a bold→unbold cycle returns to one run', () => {
    const once = applyMark(para([run('a plain word')]), 2, 7, 'bold', true)
    const back = applyMark(once, 2, 7, 'bold', false)
    expect(back.runs.map((r) => r.text)).toEqual(['a plain word'])
  })
})

describe('setBlockText edits text while keeping formatting where it can be attributed', () => {
  it('rewrites a single-run block and keeps its marks', () => {
    const next = setBlockText(para([run('hello', { bold: true })]), 'hello there')
    expect(next.runs.map((r) => [r.text, r.bold])).toEqual([['hello there', true]])
  })

  it('attributes an edit contained in ONE run of several', () => {
    const block = para([run('a '), run('plain', { bold: true }), run(' word')])
    const next = setBlockText(block, 'a plainer word')
    expect(next.runs.map((r) => [r.text, r.bold])).toEqual([
      ['a ', false], ['plainer', true], [' word', false],
    ])
  })

  it('collapses to one run when the change cannot be attributed to a single run', () => {
    // A rewrite spanning several runs has no honest attribution, and a diff that GUESSES
    // moves formatting the user never touched. Collapsing is predictable and visible.
    const block = para([run('a '), run('plain', { bold: true }), run(' word')])
    const next = setBlockText(block, 'utterly different')
    expect(next.runs.map((r) => r.text)).toEqual(['utterly different'])
  })

  it('is a no-op when the text did not change', () => {
    const block = para([run('same')])
    expect(setBlockText(block, 'same')).toBe(block)
  })
})
