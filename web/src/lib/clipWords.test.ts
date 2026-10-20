/** `clipWords` is the server's `textfmt.clip_words` rule, for text the browser shortens itself.
 *
 *  The cases are the server's own (`tests/test_a_subagents_start_says_what_it_runs.py`), so the
 *  two halves cut the same sentence at the same place: a line a person reads never stops mid-word,
 *  and a single run with no space in its second half (a path, a link) is cut inside itself rather
 *  than dropped. */
import { describe, expect, it } from 'vitest'
import { clipWords } from './clipWords'

describe('clipWords', () => {
  it.each([
    ['short enough', 40, 'short enough'],
    ['Task: Draft a short note', 16, 'Task: Draft a…'],
    ['one  line\nonly', 40, 'one line only'],
    ['stop here, then more words follow', 12, 'stop here…'],
  ])('cuts %j at a word (limit %i)', (text, limit, shown) => {
    expect(clipWords(text, limit)).toBe(shown)
  })

  it('cuts a single long run rather than dropping it', () => {
    const path = '/data/workspace/memory/instructions/claude_code/CLAUDE.md'
    expect(clipWords(path, 20)).toBe(`${path.slice(0, 19)}…`)
  })

  it('never returns more than the limit', () => {
    const text = 'find /home/user/src/feedsmith -name "CHANGELOG*" -newer setup.cfg'
    for (let limit = 2; limit < text.length; limit++) expect(clipWords(text, limit).length).toBeLessThanOrEqual(limit)
  })
})
