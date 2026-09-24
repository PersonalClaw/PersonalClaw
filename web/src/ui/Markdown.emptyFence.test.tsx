import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { Markdown } from './Markdown'

// An EMPTY fenced code block used to paint the literal word `undefined` into the
// transcript. react-markdown gives a childless `<code>` no `children` at all, and both
// `renderCode` call sites coerced that with a bare `String(children)` — which is the
// string "undefined", not "". The coercion then flowed straight into `<CodeBlock>`, so a
// model that emitted an empty fence produced a code block whose one line read
// `undefined`.
//
// Measured in a browser on a fresh container (0.1.3, ollama/qwen2.5vl:7b): a chat answer
// rendered a red `undefined` inside a code card with zero non-2xx responses, zero console
// errors, zero page errors and zero gateway tracebacks — the failure was visible ONLY on
// the rendered surface, which is why it needs a rail here rather than at the wire.
//
// Both call sites are covered on purpose. `COMPONENTS` serves every plain `<Markdown>`,
// and `componentsWith` serves chat (the highest-traffic consumer) and computes its own
// `str` for the file-path heuristic before delegating — so a fix threaded into only one
// of them leaves the other painting the same word.
describe('Markdown: an empty code fence', () => {
  const EMPTY_FENCE = '```\n```'

  it('never renders the literal word "undefined" (COMPONENTS path)', () => {
    const { container } = render(<Markdown>{EMPTY_FENCE}</Markdown>)
    expect(container.textContent).not.toContain('undefined')
  })

  it('never renders the literal word "undefined" (chat / componentsWith path)', () => {
    // `onFileClick` is what selects `componentsWith` — chat passes it.
    const { container } = render(<Markdown onFileClick={() => {}}>{EMPTY_FENCE}</Markdown>)
    expect(container.textContent).not.toContain('undefined')
  })

  it('still renders it AS a code block, empty rather than absent', () => {
    const { container } = render(<Markdown>{EMPTY_FENCE}</Markdown>)
    const pre = container.querySelector('pre')
    expect(pre).not.toBeNull()
    expect(pre?.textContent).toBe('')
  })

  it('CONTROL: a fence WITH content still renders that content', () => {
    // The vacuity floor for the three assertions above: they all pass against a renderer
    // that drops code blocks entirely, so this proves the path they measure is live.
    const { container } = render(<Markdown>{'```\nkeep me\n```'}</Markdown>)
    expect(container.querySelector('pre')?.textContent).toContain('keep me')
  })

  it('CONTROL: the word "undefined" as real code content is still shown', () => {
    // And the negative assertions must not be satisfiable by scrubbing the string: a
    // fence whose content genuinely IS `undefined` has to keep rendering it.
    const { container } = render(<Markdown>{'```js\nundefined\n```'}</Markdown>)
    expect(container.querySelector('pre')?.textContent).toContain('undefined')
  })
})
