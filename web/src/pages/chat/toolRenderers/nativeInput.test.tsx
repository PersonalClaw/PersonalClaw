import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { renderToolInput, renderToolOutput } from './registry'
import { NATIVE_RENDERERS } from './native'
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

interface Probe {
  tool: string
  input: Record<string, unknown>
  output: string
  /** The input region's own label — user-visible proof the native override rendered
   *  instead of the generic schema-driven fields. Absent for a tool that registers
   *  no input override: it must fall through to those fields, labelled "Input". */
  inputLabel?: string
  /** The value the input region must lead with (the chip, or the diff line). */
  inputShows: string
  /** A string the rendered OUTPUT must contain: the label of a block-style renderer,
   *  or the chip text for the three label-less chip renderers. */
  outputShows: string
}

/** ONE representative call per registered native renderer. Every property below is
 *  driven from this table, and the coverage rail asserts every entry in
 *  NATIVE_RENDERERS is reached by one of these — so a renderer added without a probe
 *  FAILS instead of quietly shipping uncovered. The coverage is derived from the
 *  registry, never from a hand-kept list of tool names or files. */
const PROBES: Probe[] = [
  { tool: 'edit_file', input: { path: 'src/app.py', old_str: 'before_line', new_str: 'after_line' }, output: 'Edited src/app.py', inputLabel: 'Change', inputShows: '-before_line', outputShows: 'Result' },
  { tool: 'read_file', input: { path: 'src/app.py' }, output: 'def main():\n  return 1', inputLabel: 'File', inputShows: 'src/app.py', outputShows: 'Result' },
  { tool: 'write_file', input: { path: 'notes/out.md', content: 'hello' }, output: 'Wrote 1 line', inputLabel: 'File', inputShows: 'notes/out.md', outputShows: 'Result' },
  { tool: 'glob', input: { pattern: '**/*.tsx' }, output: 'web/src/a.tsx\nweb/src/b.tsx', inputLabel: 'Pattern', inputShows: '**/*.tsx', outputShows: 'Matches' },
  { tool: 'list_dir', input: { path: 'web/src' }, output: 'a.tsx\nb.tsx', inputLabel: 'Pattern', inputShows: 'web/src', outputShows: 'Matches' },
  { tool: 'grep', input: { query: 'resolveInputObj' }, output: 'web/src/x.tsx:26:export function resolveInputObj', inputLabel: 'Query', inputShows: 'resolveInputObj', outputShows: 'Matches' },
  { tool: 'bash', input: { command: 'git diff --stat' }, output: 'diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new', inputLabel: 'Command', inputShows: 'git diff --stat', outputShows: 'Diff' },
  { tool: 'knowledge_search', input: { query: 'tool rendering' }, output: '[{"title":"Tool IO rendering","url":"kb://tool-io"}]', inputShows: 'tool rendering', outputShows: '1 result' },
  { tool: 'web_search', input: { query: 'json string input' }, output: '[{"title":"Parsing JSON","url":"https://ex.test/json"}]', inputShows: 'json string input', outputShows: '1 result' },
  { tool: 'web_fetch', input: { url: 'https://example.test/p' }, output: '# Page\n\nbody', inputLabel: 'URL', inputShows: 'https://example.test/p', outputShows: 'Fetched page' },
  { tool: 'task_create', input: { title: 'Cover the renderers' }, output: '{"id":"t-682","title":"Cover the renderers","status":"open"}', inputShows: 'Cover the renderers', outputShows: 't-682' },
  { tool: 'memory_remember', input: { text: 'history carries a JSON-string input' }, output: 'Recorded that for later.', inputShows: 'history carries a JSON-string input', outputShows: 'Recorded that for later.' },
  { tool: 'project_run_status', input: { id: 'abc12345' }, output: '{"id":"abc12345","status":"running"}', inputShows: 'abc12345', outputShows: 'running' },
]

/** Which registry entry a tool name dispatches to — first match wins, the rule
 *  `findNative` applies. Reads the registry, so it tracks any reordering. */
const entryFor = (tool: string): number =>
  NATIVE_RENDERERS.findIndex((r) => r.match(tool.toLowerCase()))

describe('the native renderer registry is covered by the parity probes (#682)', () => {
  it('every registered renderer is reached by a probe', () => {
    // The vacuity floor for everything below: parity and firing are asserted
    // per-probe, so a renderer no probe reaches is a renderer nothing checks.
    const unreached = NATIVE_RENDERERS
      .map((_, i) => i)
      .filter((i) => !PROBES.some((p) => entryFor(p.tool) === i))
    expect(unreached).toEqual([])
  })

  it('every probe names a tool that some renderer claims', () => {
    expect(PROBES.filter((p) => entryFor(p.tool) < 0).map((p) => p.tool)).toEqual([])
  })
})

describe.each(PROBES)('$tool renders the same from history as it does live (#682)', (probe) => {
  const { tool, input, output, inputLabel, inputShows, outputShows } = probe
  const hist = fromHistory(tool, input, output)
  const live = fromLiveFrame(tool, input, output)
  const entry = NATIVE_RENDERERS[entryFor(tool)]

  it('the input region is byte-identical', () => {
    expect(html(renderToolInput(hist))).toBe(html(renderToolInput(live)))
  })

  it('the output region is byte-identical', () => {
    expect(html(renderToolOutput(hist))).toBe(html(renderToolOutput(live)))
  })

  // Parity alone is satisfied by BOTH sides degrading together, so pin that the
  // registered renderer actually FIRED on the history-shaped segment: one reading
  // `seg.inputObj` raw returns undefined there while parity still holds.
  it('the registered renderer fires on the history-shaped segment', () => {
    expect({
      input: entry.input ? entry.input(hist) !== undefined : 'none registered',
      output: entry.output ? entry.output(hist) !== undefined : 'none registered',
    }).toEqual({
      input: entry.input ? true : 'none registered',
      output: entry.output ? true : 'none registered',
    })
  })

  it('the input region reads from the JSON-string input', () => {
    const out = html(renderToolInput(hist))
    // A tool with no input override must reach the schema-driven fields, which need
    // the same resolution — so the assertion is the same shape either way.
    expect(out).toContain(inputLabel ?? 'Input')
    expect(out).toContain(inputShows)
  })

  it('the output region reads from the JSON-string input where it needs to', () => {
    expect(html(renderToolOutput(hist))).toContain(outputShows)
  })
})

describe('resolveInputObj is the ONE owner of the call input (#682)', () => {
  it('a native override is handed the caller segment, not a normalized copy', () => {
    // renderToolInput used to patch a resolved `inputObj` onto a copy before calling
    // an input override. That mask is why this issue read as "the input renderers
    // never fire" while they did: the raw read was covered on the input path and
    // failed only on the output path, where no copy is built. With one owner a raw
    // read fails on both, and the parity rail above is what reports it.
    const seen: ToolSegment[] = []
    NATIVE_RENDERERS.unshift({
      match: (n) => n === '__parity_probe__',
      input: (s) => { seen.push(s); return <i>probe</i> },
    })
    try {
      const seg = fromHistory('__parity_probe__', { path: 'src/app.py' })
      renderToolInput(seg)
      expect(seen).toHaveLength(1)
      expect(seen[0]).toBe(seg)
      expect(seen[0].inputObj).toBeUndefined()
    } finally {
      NATIVE_RENDERERS.shift()
    }
  })
})

describe('inputs with no object to resolve still fall through (#682)', () => {
  it('a non-JSON scalar input still renders the raw fallback', () => {
    // ACP hands the args over as a bare string; there is no object to chip, so the
    // override must decline and the raw block must render.
    const seg: ToolSegment = { kind: 'tool', id: 't1', tool: 'bash', input: 'ls -la', done: true }
    const out = html(renderToolInput(seg))
    expect(out).toContain('Input')
    expect(out).toContain('ls -la')
    expect(out).not.toContain('Command')
  })

  it('web_fetch without a resolvable URL still renders the page body', () => {
    const seg: ToolSegment = { kind: 'tool', id: 't1', tool: 'web_fetch', input: 'not json', output: 'body text', done: true }
    const out = html(renderToolOutput(seg))
    expect(out).toContain('Fetched page')
    expect(out).toContain('body text')
  })
})
