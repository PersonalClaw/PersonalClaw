/** The content-to-execution boundary around `<widget>` (#3526).
 *
 *  A widget's body becomes the document of a sandboxed iframe in the app's own
 *  origin. A fenced code block is the one markdown construct whose whole meaning is
 *  "do not interpret this", so a widget written inside a fence is content
 *  DESCRIBING a widget. `parseWidgetBlocks` scanned the raw string with a plain
 *  regex and had no notion of a fence, so it did both wrong things at once: it
 *  mounted the example as a live iframe, and it cut the tag out of the fence the
 *  user asked to read (the orphan fence that left is what surfaced #3525).
 *
 *  This is the SECOND instance of the family — #633 was an unknown artifact kind
 *  coerced to `widget`, the sandboxed-execution kind. Both are content becoming a
 *  program, so this file asserts the boundary at the parser and at ALL THREE of its
 *  consumers, because a boundary that holds on one of three surfaces is not one:
 *
 *  * chat — `Markdown` → `parseWidgetBlocks`
 *  * a workflow gate's prompt — `WorkflowAsk` → `findGenUiBlock`
 *  * a dashboard tile's body — `PinnedTiles` → `findGenUiBlock`
 *
 *  🔴 Every "no widget" assertion here is paired with its UNFENCED counterpart, in
 *  the same describe, asserting the widget still mounts. That pairing is the
 *  vacuity floor: an absence assertion passes trivially against a parser that
 *  extracts nothing at all and against a DOM query with a wrong selector, and this
 *  repo has repeatedly shipped absence assertions that could not fail. Verified by
 *  crippling `parseWidgetBlocks` to return one md segment always — the unfenced
 *  halves red, which is what proves the fenced halves measure something.
 */
import { render, act, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { parseWidgetBlocks, findGenUiBlock, widgetlessText } from './blocks'
import { Markdown } from '../Markdown'
import { registerBuiltinContentTypes } from '../content/registerBuiltins'
import { WorkflowAsk } from '../../pages/workflows/WorkflowAsk'
import { PinnedTiles } from '../../pages/dashboard/PinnedTiles'
import { registerCoreGenUiComponents } from '../genui/components'
import type { WorkflowContinuation } from '../../lib/api'

const TILE_BODY = { current: '' }

vi.mock('../../lib/api', () => ({
  api: {
    artifactExists: vi.fn(async () => false),
    createArtifact: vi.fn(async () => ({})),
    deleteArtifact: vi.fn(async () => ({})),
    pinTile: vi.fn(async () => ({})),
    resolveTile: vi.fn(async () => ({})),
    refreshTile: vi.fn(async () => ({ refreshed: false })),
    tileWidgetAction: vi.fn(async () => ({ ok: true })),
    resumeWorkflowRun: vi.fn(async () => ({ ok: true, approved: true })),
    dashboardViews: vi.fn(async () => [
      { id: 'overview', tiles: [{ ref: 'artifact:sales', order: 0, added_by: 'user' }] },
    ]),
    artifact: vi.fn(async () => ({ slug: 'sales', name: 'Sales', content: TILE_BODY.current })),
  },
}))
vi.mock('../../app/appSdk', () => ({ launchChat: vi.fn(), notify: vi.fn() }))

// jsdom ships no blob-URL factory and every widget iframe renders off one.
beforeAll(() => {
  if (typeof URL.createObjectURL !== 'function') {
    URL.createObjectURL = () => 'blob:fenced-widget-test'
    URL.revokeObjectURL = () => {}
  }
  registerBuiltinContentTypes()
  registerCoreGenUiComponents()
})
beforeEach(() => { localStorage.clear() })

const widgets = (raw: string, streaming = false) =>
  parseWidgetBlocks(raw, streaming).filter((s) => s.type === 'widget')
const md = (raw: string, streaming = false) =>
  parseWidgetBlocks(raw, streaming).filter((s) => s.type === 'md').map((s) => s.content).join('')

const TAG = '<widget title="Release Stamp">Stamp</widget>'

describe('parseWidgetBlocks: a fenced widget is code, not a program', () => {
  it('CONTROL: an UNFENCED widget is still extracted, unchanged', () => {
    // The vacuity floor for every negative in this file. Without it, all of them
    // pass against a parser that extracts nothing.
    const segs = parseWidgetBlocks(`Here you go.\n\n${TAG}`)
    expect(segs).toEqual([
      { type: 'md', content: 'Here you go.\n\n' },
      { type: 'widget', title: 'Release Stamp', slug: undefined, html: 'Stamp', complete: true, kind: undefined },
    ])
  })

  it('the #3526 case: one fenced + one unfenced tag mounts exactly ONE widget', () => {
    // The persisted assistant message from the container drive, verbatim in shape:
    // a ```diff fence whose one content line IS the tag, plus a second copy outside.
    const raw = [
      '```diff',
      'diff --git a/c4-stamp.md b/c4-stamp.md',
      '@@ -0,0 +1 @@',
      `+${TAG}`,
      '```',
      '',
      TAG,
    ].join('\n')
    expect(widgets(raw)).toHaveLength(1)
    // …and the diff keeps the line it was about. This is the second half of the
    // defect: the fence was mutilated, not merely accompanied by a stray iframe.
    expect(md(raw)).toContain(`+${TAG}`)
    expect(md(raw)).toContain('```diff')
  })

  it('a fence-only message extracts nothing and keeps every character', () => {
    const raw = `Like this:\n\n\`\`\`html\n${TAG}\n\`\`\`\n`
    expect(widgets(raw)).toEqual([])
    expect(md(raw)).toBe(raw)
  })

  it('~~~ fences count too', () => {
    expect(widgets(`~~~\n${TAG}\n~~~`)).toEqual([])
    expect(widgets(`~~~html\n${TAG}\n~~~`)).toEqual([])
  })

  it('a fence whose info string is itself `widget` does not smuggle a tag', () => {
    // The opening line is metadata, not prose, so the whole fence is inert —
    // including a tag written on the opener itself.
    expect(widgets(`\`\`\`widget\n${TAG}\n\`\`\``)).toEqual([])
    expect(widgets(`\`\`\`${TAG}\n</widget>`)).toEqual([])
  })

  it('a longer fence contains shorter ones (CommonMark nesting)', () => {
    // ```` closes only on a run of >= 4, so the inner ``` does not end it and the
    // tag between them stays code.
    const raw = `\`\`\`\`md\n\`\`\`html\n${TAG}\n\`\`\`\n\`\`\`\`\n\n${TAG}`
    expect(widgets(raw)).toHaveLength(1)
  })

  it('an UNCLOSED fence runs to the end of the input', () => {
    expect(widgets(`\`\`\`html\n${TAG}`)).toEqual([])
    expect(widgets(`\`\`\`html\n${TAG}\n${TAG}`)).toEqual([])
  })

  it('up to 3 spaces of indent still opens a fence; the closer may be indented too', () => {
    expect(widgets(`   \`\`\`\n${TAG}\n   \`\`\``)).toEqual([])
  })

  it('an inline `<widget>` span is code — how this repo documents the tag', () => {
    // `prompt_snippets/widget-instructions.md` writes the contract exactly this way.
    expect(widgets('Use `<widget title="T">HTML</widget>` for rich output.')).toEqual([])
    expect(widgets(`Prose \`\`${TAG}\`\` more prose.`)).toEqual([])
  })

  it('an UNMATCHED backtick does not silence a real widget after it', () => {
    // The dangerous direction of a span rule: a stray backtick must stay literal
    // text rather than turning the rest of the line — or the document — into code.
    expect(widgets(`5 \` 6 ${TAG}`)).toHaveLength(1)
    expect(widgets(`a \`b\` c\n${TAG}`)).toHaveLength(1)
  })

  it('a tag that OPENS inside a fence and closes outside it extracts nothing', () => {
    const raw = `\`\`\`\n<widget title="X">\n\`\`\`\n</widget>`
    expect(widgets(raw)).toEqual([])
    expect(md(raw)).toBe(raw)
  })

  it("a widget's own body is opaque: three backticks in its HTML open no fence", () => {
    // Without this, a widget whose markup contains a ``` line would open a phantom
    // fence and silence every widget after it — a false negative introduced BY the
    // fix, which is the failure mode worth guarding.
    const raw = `<widget title="A">\n\`\`\`\n</widget>\n${TAG}`
    expect(widgets(raw)).toHaveLength(2)
  })

  it('DECISION: a 4-space indented block is NOT a code region, so it still executes', () => {
    // Recorded as an assertion so the scope is a decision rather than an accident.
    // Indentation is structure, not a marker: a tile body is pretty-printed HTML and
    // a widget under a list item sits at its item's content column, so an
    // indentation rule would stop rendering REAL widgets. Recognizing one correctly
    // needs a full markdown parser's block structure, which this splitter is not.
    expect(widgets(`Example:\n\n    ${TAG}\n`)).toHaveLength(1)
  })

  it('streaming: a widget being typed inside an open fence never flashes as an iframe', () => {
    expect(widgets('```html\n<widget title="X">partial', true)).toEqual([])
    // …while an unfenced one still paints progressively.
    const provisional = widgets('<widget title="X">partial', true)
    expect(provisional).toHaveLength(1)
    expect(provisional[0]).toMatchObject({ complete: false, html: 'partial' })
  })

  it('streaming: a closed fence does not swallow the widget after it', () => {
    const segs = widgets(`\`\`\`html\n${TAG}\n\`\`\`\n\n<widget title="X">partial`, true)
    expect(segs).toHaveLength(1)
    expect(segs[0]).toMatchObject({ title: 'X', complete: false })
  })

  it('CRLF input is handled: a \\r before the newline still closes a fence', () => {
    expect(widgets(`\`\`\`html\r\n${TAG}\r\n\`\`\`\r\n\r\n${TAG}`)).toHaveLength(1)
  })
})

const GENUI_TREE = 'f = Form(fields: ["amount"], action: "log_expense", submit: "Log expense")'
const GENUI = `<widget kind="genui" title="Expense">\n${GENUI_TREE}\n</widget>`

describe('findGenUiBlock / widgetlessText inherit the rule', () => {
  it('CONTROL: an unfenced genui block is still found', () => {
    expect(findGenUiBlock(`Log it.\n\n${GENUI}`)?.html).toBe(GENUI_TREE)
    expect(widgetlessText(`Log it.\n\n${GENUI}`)).toBe('Log it.')
  })

  it('a fenced genui block is not a gate/tile tree', () => {
    const raw = `Log it.\n\n\`\`\`\n${GENUI}\n\`\`\``
    expect(findGenUiBlock(raw)).toBeNull()
    // …and its text is preserved whole, so a host that falls back to prose shows it.
    expect(widgetlessText(raw)).toBe(raw.trim())
  })
})

describe('consumer 1 — chat (Markdown)', () => {
  it('CONTROL: an unfenced widget still mounts its sandboxed iframe', async () => {
    const { container } = render(<Markdown>{`Here:\n\n${TAG}`}</Markdown>)
    await act(async () => {})
    expect(container.querySelector('iframe')).not.toBeNull()
  })

  it('a fenced widget mounts NO iframe and shows its text as code', async () => {
    const { container } = render(<Markdown>{`Like this:\n\n\`\`\`html\n${TAG}\n\`\`\``}</Markdown>)
    await act(async () => {})
    expect(container.querySelector('iframe')).toBeNull()
    const pre = container.querySelector('pre')
    expect(pre).not.toBeNull()
    expect(pre?.textContent).toContain('<widget title="Release Stamp">Stamp</widget>')
  })

  it('both at once: one iframe, and the fence keeps its content', async () => {
    const raw = `\`\`\`html\n${TAG}\n\`\`\`\n\n${TAG}`
    const { container } = render(<Markdown>{raw}</Markdown>)
    await act(async () => {})
    expect(container.querySelectorAll('iframe')).toHaveLength(1)
    expect(container.querySelector('pre')?.textContent).toContain('Stamp</widget>')
  })
})

function continuation(prompt: string): WorkflowContinuation {
  return {
    resume_token: 'tok-abc',
    node_id: 'ask',
    instance_path: 'root/ask',
    ask: { kind: 'form', prompt, fields: [{ name: 'amount' }] },
    handoff: {},
    expires_at: Date.now() / 1000 + 600,
    expired: false,
  }
}

describe('consumer 2 — a workflow gate prompt (WorkflowAsk)', () => {
  it('CONTROL: an unfenced genui prompt still renders the tree', () => {
    const { getByText, container } = render(
      <WorkflowAsk continuation={continuation(`Log it.\n\n${GENUI}`)} runId="run-7" busy={false} onAnswer={vi.fn()} />,
    )
    expect(getByText('Log expense')).toBeInTheDocument()
    expect(container.textContent).not.toContain('<widget')
  })

  it('a fenced genui prompt renders no tree; the markup stays visible text', () => {
    const { queryByText, container } = render(
      <WorkflowAsk
        continuation={continuation(`Log it.\n\n\`\`\`\n${GENUI}\n\`\`\``)}
        runId="run-7"
        busy={false}
        onAnswer={vi.fn()}
      />,
    )
    expect(queryByText('Log expense')).toBeNull()
    expect(container.querySelector('iframe')).toBeNull()
    expect(container.textContent).toContain('<widget kind="genui"')
  })
})

describe('consumer 3 — a dashboard tile body (PinnedTiles)', () => {
  it('CONTROL: an unfenced genui body still renders in the host tree', async () => {
    TILE_BODY.current = `Sales are up.\n${GENUI}`
    const { findByText } = render(<PinnedTiles />)
    expect(await findByText('Log expense')).toBeInTheDocument()
  })

  it('a fenced genui body renders no host tree', async () => {
    TILE_BODY.current = `Sales are up.\n\`\`\`\n${GENUI}\n\`\`\``
    const { container, queryByText } = render(<PinnedTiles />)
    await waitFor(() => expect(container.querySelector('[data-testid="pinned-tiles"]')).not.toBeNull())
    await act(async () => {})
    expect(queryByText('Log expense')).toBeNull()
  })
})
