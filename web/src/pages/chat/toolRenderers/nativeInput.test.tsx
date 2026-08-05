import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { renderToolInput, renderToolOutput } from './registry'
import type { ToolSegment } from '../chatTypes'

// #682 regression cover for renderer selection on a session loaded from HISTORY.
//
// A persisted tool message carries its args as a JSON *string* on `meta.input`
// (the backend's `input_preview`) and no structured object — `inputObj` is only
// ever populated by a freshly-streamed native `tool_call` frame. So every
// renderer that wants a field out of the call's input has to resolve the input
// rather than read `inputObj`, or it is correct only while the turn is live and
// silently degrades to the raw fallback the moment the page is reloaded.
//
// These drive the real registry entry points (not a copy of the selection order),
// because the bug is *which renderer gets chosen*, and assert on the renderer's
// own LABEL — the user-visible proof that the rich card rendered instead of a
// raw <pre>.

/** A segment as `hydrateTurns` builds it from persisted history: a JSON-string
 *  input and NO `inputObj`. */
const fromHistory = (tool: string, input: unknown, output?: string): ToolSegment => ({
  kind: 'tool', id: 't1', tool, input: JSON.stringify(input), output, done: true,
})

/** A segment as the live `tool_call` WS frame builds it: a structured `inputObj`
 *  alongside the string preview. */
const fromLiveFrame = (tool: string, input: Record<string, unknown>, output?: string): ToolSegment => ({
  kind: 'tool', id: 't1', tool, input: JSON.stringify(input), inputObj: input, output, done: true,
})

const html = (node: React.ReactNode): string => render(<>{node}</>).container.innerHTML

describe('native INPUT overrides resolve the input from persisted history (#682)', () => {
  // pathChipInput backs read_file/write_file/glob/list_dir/grep/bash/web_fetch —
  // the label is what distinguishes its chip from the raw "Input" fallback.
  it.each([
    ['bash', { command: 'ls -la' }, 'Command', 'ls -la'],
    ['read_file', { path: '/a/b.py' }, 'File', '/a/b.py'],
    ['write_file', { path: '/a/b.py', content: 'x' }, 'File', '/a/b.py'],
    ['glob', { pattern: '**/*.ts' }, 'Pattern', '**/*.ts'],
    ['list_dir', { path: '/a' }, 'Pattern', '/a'],
    ['grep', { query: 'needle' }, 'Query', 'needle'],
    ['web_fetch', { url: 'https://example.com/p' }, 'URL', 'https://example.com/p'],
  ])('%s renders its chip label from a JSON-string input', (tool, input, label, primary) => {
    const out = html(renderToolInput(fromHistory(tool, input)))
    expect(out).toContain(label)
    expect(out).toContain(primary)
  })

  it('edit_file renders the old→new mini-diff from a JSON-string input', () => {
    const out = html(renderToolInput(
      fromHistory('edit_file', { path: '/a/b.py', old_str: 'before', new_str: 'after' }),
    ))
    expect(out).toContain('Change')
    expect(out).toContain('-before')
    expect(out).toContain('+after')
  })

  it('a live-frame segment still renders the same chip (no regression)', () => {
    const out = html(renderToolInput(fromLiveFrame('bash', { command: 'make test' })))
    expect(out).toContain('Command')
    expect(out).toContain('make test')
  })

  it('a non-JSON scalar input still falls through to the raw fallback', () => {
    // ACP hands the args over as a bare string; there is no object to chip, so the
    // override must decline and the raw block must render.
    const seg: ToolSegment = { kind: 'tool', id: 't1', tool: 'bash', input: 'ls -la', done: true }
    const out = html(renderToolInput(seg))
    expect(out).toContain('Input')
    expect(out).toContain('ls -la')
    expect(out).not.toContain('Command')
  })
})

describe('native OUTPUT overrides that read the call input resolve it too (#682)', () => {
  // OUTPUT renderers are handed the segment untouched — the input path is the only
  // one that builds a normalized copy — so this is the site that was genuinely
  // broken for every reloaded session, not merely fragile.
  it('web_fetch titles the card with the URL from a JSON-string input', () => {
    const out = html(renderToolOutput(
      fromHistory('web_fetch', { url: 'https://example.com/p' }, '# Page\n\nbody'),
    ))
    expect(out).toContain('Fetched page')
    expect(out).toContain('https://example.com/p')
  })

  it('web_fetch on a live frame still shows the URL (no regression)', () => {
    const out = html(renderToolOutput(
      fromLiveFrame('web_fetch', { url: 'https://example.com/p' }, '# Page\n\nbody'),
    ))
    expect(out).toContain('https://example.com/p')
  })

  it('web_fetch without a resolvable URL still renders the page body', () => {
    const seg: ToolSegment = { kind: 'tool', id: 't1', tool: 'web_fetch', input: 'not json', output: 'body text', done: true }
    const out = html(renderToolOutput(seg))
    expect(out).toContain('Fetched page')
    expect(out).toContain('body text')
  })
})
