import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { mdToPlain } from './scheduleMeta'

// ── A history row shows the sentence its action wrote, every character of it ─────────────────────
//
// Measured on a gateway: an inbox-op automation's row read "Archived the message from alice in
// general." and a run-workflow one "Started “tidy notes” as run 9603551d." — the actions wrote
// "#general" and "tidy-notes". The row flattens markdown (an agent's reply is markdown) and its
// last pass turned every `# - _ * ~ >` into a space wherever it stood, so a name lost the very
// characters it is spelled with. Markup is now stripped where it is markup. And the row is one
// line: opened, it now says the whole sentence above the trace.

const SENTENCES = [
  'Archived the message from alice in #general.',
  'Started “tidy-notes” as run 9603551d.',
  'Did not start “nightly_sync_job”: run 3f2a91c0 is still going, and this workflow skips a start while one is.',
  'Fetched 1,109 characters from my-bank.example:8443 (HTTP 200, text/html).',
  'Browse finished in 3 steps at my-bank.example. Noted: p95 > 2s; C# and F# builds are green',
  'Drafted a reply to the message from bob-smith in #eng-oncall; nothing was sent.',
]

describe('mdToPlain', () => {
  it('🔴 keeps a sentence an action wrote exactly as it wrote it', () => {
    for (const said of SENTENCES) expect(mdToPlain(said)).toBe(said)
  })

  it('still flattens an agent’s markdown reply to one plain line', () => {
    const reply = [
      '## Summary',
      '',
      '- **Two** new items',
      '- one `code` span and a [link](https://example.com/x)',
      '',
      '> a quoted line',
      '',
      '---',
      '',
      '1. first',
      '2. _second_ and __third__ and ~~gone~~ and *it*',
    ].join('\n')
    expect(mdToPlain(reply)).toBe(
      'Summary Two new items one code span and a link a quoted line first second and third and gone and it',
    )
  })

  it('drops markup that stands apart from any word, and a table', () => {
    expect(mdToPlain('5 * 3 * 2')).toBe('5 3 2')
    expect(mdToPlain('Title ## trailing hashes ##')).toBe('Title trailing hashes')
    // A reply cut at the row's cap can leave a pair half open.
    expect(mdToPlain('**Summary** of the week. **Key poin')).toBe('Summary of the week. Key poin')
    // Inside a word or a path the same characters are text.
    expect(mdToPlain('5*3=15 at https://example.com/~user/_next/a_b')).toBe('5*3=15 at https://example.com/~user/_next/a_b')
    expect(mdToPlain('| a | b |\n|---|---|\n| 1 | 2 |')).toBe('')
    expect(mdToPlain('***')).toBe('')
    expect(mdToPlain('')).toBe('')
    expect(mdToPlain(null)).toBe('')
  })
})

const RUNS = SENTENCES.map((summary, i) => ({
  run_id: `r${i}`, status: 'success', outcome: 'ran', summary, started_at: '2026-09-27T10:00:00Z',
}))

beforeEach(() => {
  cleanup()
  vi.resetModules()
})

async function mount(runs: Record<string, unknown>[], detail: Record<string, unknown>) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      triggerHistory: () => Promise.resolve({ runs, total: runs.length, supported: true }),
      triggerRunDetail: () => Promise.resolve(detail),
    },
  }))
  const { RunHistory } = await import('./ScheduleDetail')
  render(<RunHistory triggerId="schedule:clock:archive" />)
}

describe('the history row', () => {
  it('🔴 reads the action’s sentence, not a sentence with its names torn out', async () => {
    await mount(RUNS, RUNS[0])
    for (const said of SENTENCES) expect(await screen.findByText(said)).toBeTruthy()
  })

  it('🔴 opened, says the whole sentence above the trace the row cuts it from', async () => {
    // Measured: in the automation panel the row is one line, cut to "Archived the message from
    // alice …", and opening it showed only the trace — the JSON — so the sentence was never read
    // whole without widening the panel.
    const trace = '{"op": "archive", "item_id": "C1_100.5", "changed": true}'
    await mount([RUNS[0]], { ...RUNS[0], trace })
    fireEvent.click(await screen.findByRole('button', { name: /Archived the message/ }))
    await screen.findByText(/"changed": true/)
    // The row's own line, and the whole sentence in the opened row.
    expect(screen.getAllByText(SENTENCES[0])).toHaveLength(2)
  })

  it('does not repeat a line that is only the opening of the trace', async () => {
    // CONTROL: an action that wrote no sentence has the start of what it printed as its line.
    const reply = { run_id: 'r9', status: 'success', summary: 'Fired the digest', started_at: '2026-09-27T10:00:00Z' }
    await mount([reply], { ...reply, trace: 'Fired the digest with 3 new items.' })
    fireEvent.click(await screen.findByRole('button', { name: /Fired the digest/ }))
    await screen.findByText('Fired the digest with 3 new items.')
    expect(screen.getAllByText('Fired the digest')).toHaveLength(1)
  })
})
