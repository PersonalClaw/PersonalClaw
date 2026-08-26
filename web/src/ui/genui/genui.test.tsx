import { describe, it, expect, beforeAll } from 'vitest'
import { render } from '@testing-library/react'
import { parseGenUi } from './parse'
import {
  allComponents,
  getComponent,
  library,
  validateInvocation,
  defineComponent,
} from './registry'
import { registerCoreGenUiComponents } from './components'
import { GenUiWidget } from './GenUiWidget'
import type { EmbedProps } from '../content/contentTypes'

beforeAll(() => registerCoreGenUiComponents())

// ── registry + mechanical authoring prompt (§5.1) ──────────────────────────

describe('genui registry', () => {
  it('bundles a core component set spanning every group', () => {
    const groups = new Set(allComponents().map((c) => c.group))
    // `Forms` joined the core set in AS-6: the action-bearing components (§5.4). Enumerated,
    // so a new group has to be argued for here rather than appearing unnoticed.
    expect(groups).toEqual(new Set(['Layout', 'Data', 'Charts', 'Forms', 'Feedback']))
    for (const name of ['Stack', 'StatTile', 'Table', 'List', 'Bar', 'Callout', 'Button', 'Form']) {
      expect(getComponent(name), `${name} registered`).toBeTruthy()
    }
  })

  it('library.prompt() is derived from the registry (lists every component)', () => {
    const prompt = library.prompt()
    for (const def of allComponents()) {
      expect(prompt).toContain(def.name)
      // Every required arg key appears in the signature line.
      for (const a of def.args.filter((x) => x.required)) expect(prompt).toContain(a.key)
    }
    // A newly-registered component shows up WITHOUT touching the prompt code.
    defineComponent({
      name: 'ZZTestOnly', group: 'Feedback', description: 'test',
      args: [{ key: 'x', type: 'string', required: true }],
      component: () => null,
    })
    expect(library.prompt()).toContain('ZZTestOnly')
  })
})

// ── drop-invalid validation (§5.2) — the three typed errors ────────────────

describe('genui validation (drop-invalid, typed errors)', () => {
  it('unknown component → unknown-component', () => {
    const e = validateInvocation('NoSuchThing', [])
    expect(e?.kind).toBe('unknown-component')
    expect(e?.message).toContain('NoSuchThing')
  })

  it('missing required arg → missing-required naming the arg', () => {
    const e = validateInvocation('StatTile', ['label']) // value is required, absent
    expect(e?.kind).toBe('missing-required')
    expect(e?.keys).toContain('value')
  })

  it('excess arg → excess-args naming the offender', () => {
    const e = validateInvocation('Callout', ['text', 'bogus'])
    expect(e?.kind).toBe('excess-args')
    expect(e?.keys).toEqual(['bogus'])
  })

  it('a well-formed invocation validates clean', () => {
    expect(validateInvocation('StatTile', ['label', 'value', 'delta'])).toBeNull()
  })
})

// ── parser (§5.2) ───────────────────────────────────────────────────────────

describe('genui DSL parser', () => {
  it('parses ids, components, scalar/array args, and refs', () => {
    const { lines, parseErrors } = parseGenUi(
      [
        'root = Stack(gap: "m", body: [a, b])',
        'a = StatTile(label: "Rev", value: "$1M", delta: 12)',
        'b = List(items: ["one", "two"])',
      ].join('\n'),
    )
    expect(parseErrors).toEqual([])
    expect(lines).toHaveLength(3)
    const root = lines[0]
    expect(root.component).toBe('Stack')
    expect(root.args.gap).toBe('m')
    expect(root.refs.body).toEqual(['a', 'b']) // forward references
    const a = lines[1]
    expect(a.args.value).toBe('$1M')
    expect(a.args.delta).toBe(12)
    expect(lines[2].args.items).toEqual(['one', 'two'])
  })

  it('records a malformed line as a parse error and keeps the rest', () => {
    const { lines, parseErrors } = parseGenUi('a = StatTile(label: "x", value: "y")\nthis is not a component')
    expect(lines).toHaveLength(1)
    expect(parseErrors).toHaveLength(1)
    expect(parseErrors[0].line).toBe(2)
  })
})

// ── the streaming renderer + the adversarial drop-invalid property ─────────

function renderWidget(content: string) {
  const props: EmbedProps = { content, title: 'Test' }
  return render(<GenUiWidget {...props} />)
}

describe('GenUiWidget rendering', () => {
  it('renders valid components in the host tree', () => {
    const { getByText } = renderWidget(
      [
        'root = Stack(gap: "m", body: [stat, note])',
        'stat = StatTile(label: "Revenue", value: "$1.2M", delta: 12)',
        'note = Callout(tone: "info", text: "Up 12%.")',
      ].join('\n'),
    )
    expect(getByText('Revenue')).toBeTruthy()
    expect(getByText('$1.2M')).toBeTruthy()
    expect(getByText('Up 12%.')).toBeTruthy()
  })

  it('ADVERSARIAL: an unknown component drops that line with a typed error and renders everything else (no null hole)', () => {
    const { getByText, queryByText, getAllByRole } = renderWidget(
      [
        'a = StatTile(label: "Kept", value: "42")',
        'b = Bogus(foo: "bar")', // unknown component — must be dropped
        'c = Callout(tone: "ok", text: "Also kept")',
      ].join('\n'),
    )
    // The two valid siblings still render.
    expect(getByText('Kept')).toBeTruthy()
    expect(getByText('Also kept')).toBeTruthy()
    // The invalid line is surfaced as a typed error, not a crash / blank.
    const alerts = getAllByRole('alert')
    expect(alerts.some((a) => /Bogus/.test(a.textContent || ''))).toBe(true)
    expect(alerts.some((a) => /Unknown component/.test(a.textContent || ''))).toBe(true)
    // And it is NOT rendered as an actual component.
    expect(queryByText('bar')).toBeNull()
  })

  it('drops a missing-required line but keeps its valid siblings', () => {
    const { getByText, getAllByRole } = renderWidget(
      ['a = StatTile(label: "only-label")', 'b = Callout(tone: "info", text: "fine")'].join('\n'),
    )
    expect(getByText('fine')).toBeTruthy()
    expect(getAllByRole('alert').some((a) => /missing required/.test(a.textContent || ''))).toBe(true)
  })
})
