import { describe, expect, it } from 'vitest'
import { createElement } from 'react'
import { render } from '@testing-library/react'
import { Markdown } from '../ui/Markdown'
import { HOSTILE, HOSTILE_ROWS, MD_BLOCK, MD_LINE, EMBEDDED_HTML, REFUSED_HREFS, inert, plain, rawSinks, rendersBlock, rendersLine } from './markdownSinks'

// ── The checks every markdown surface asserts with, checked themselves ───────────────────────
//
// Each markdown PR plants its sinks with these fixtures and asserts with these helpers, so a helper
// that passes everything would leave every later plant green. Each one is held here to a control
// it must pass and a plant it must name.

/** A detached element holding `html`, as a sink's DOM would. */
function holding(html: string): HTMLElement {
  const el = document.createElement('div')
  el.innerHTML = html
  return el
}

const FIELDS = ['title', 'reason', 'question'] as const

describe('rawSinks', () => {
  it('finds a field drawn as JSX text, with or without a fallback', () => {
    const source = [
      '<span className="truncate">{task.title}</span>',
      '<p>{item?.reason ?? \'\'}</p>',
      '  {gate.question}',
      '<li>{' + "' '" + '}{step!.title}</li>',
      // The `||` sinks the later PRs name: a field with a second field or a literal behind it.
      "<span>{sub.title || sub.task || '(task)'}</span>",
      "<span>{sub.title || sub.task || 'A helper'}</span>",
      '<p>{p?.title || it.message}</p>',
    ].join('\n')
    expect(rawSinks(source, FIELDS)).toEqual([
      '1: {task.title}',
      "2: {item?.reason ?? ''}",
      '3: {gate.question}',
      '4: {step!.title}',
      "5: {sub.title || sub.task || '(task)'}",
      "6: {sub.title || sub.task || 'A helper'}",
      '7: {p?.title || it.message}',
    ])
  })

  it("does not find a field handed to a prop, a template literal, a call, a Markdown's child or another field", () => {
    const source = [
      '<MarkdownLine text={task.title} className="truncate" />',
      '<button title={task.title}>{label}</button>',
      'const name = `${task.title} (${n})`',
      'previewLine(task.reason)',
      '<span>{task.owner}</span>',
      '<span>{title}</span>',
      '<Markdown breaks>{note.title}</Markdown>',
      '<span>{sub.task || sub.owner}</span>',
      'const shown = a.title || b.reason',
    ].join('\n')
    expect(rawSinks(source, FIELDS)).toEqual([])
  })
})

describe('plain', () => {
  it('passes plain words, and names each mark or tag left in a string', () => {
    expect(plain('Skip model_calls in published.py')).toEqual([])
    expect(plain('## Plan `x` **y** <b>z</b>')).toEqual(['a backtick', 'a bold mark', 'a tag', 'a heading mark'])
  })
})

describe('inert', () => {
  it('passes plain words', () => {
    expect(inert(holding('Skip model_calls in <span>published.py</span>'))).toEqual([])
  })

  it('names a planted link, image, input and handler', () => {
    const el = holding('<a href="https://example.com/x">x</a><img src="https://images.example.com/p.png"><input name="q"><span onclick="go()">y</span>')
    expect(inert(el)).toEqual(['a', 'img', 'input', 'span.onclick'])
  })

  it("for a body, passes the app's own controls (a Load button, an icon) and still names a handler on one", () => {
    const el = holding('<button type="button"><svg viewBox="0 0 4 4"></svg> Load image</button>')
    expect(inert(el, { body: true })).toEqual([])
    expect(inert(el)).toEqual(['button', 'svg'])
    expect(inert(holding('<button onclick="go()">x</button>'), { body: true })).toEqual(['button.onclick'])
  })
})

describe('rendersLine', () => {
  it('passes a line with its code and bold rendered', () => {
    expect(rendersLine(holding('<span>Skip <code>model_calls</code> in <strong>published.py</strong></span>'))).toEqual([])
  })

  it('names the marks of the same words as raw text', () => {
    const raw = holding(`<span>${MD_LINE}</span>`)
    expect(rendersLine(raw)).toEqual(['no code element', 'no strong element', 'a literal backtick', 'a literal **'])
  })

  it('names a block inside the line', () => {
    expect(rendersLine(holding('<span><p>Skip <code>x</code> <strong>y</strong></p></span>'))).toEqual(['a block descendant: p'])
  })
})

describe('rendersBlock', () => {
  it('passes a body rendered as blocks', () => {
    const { container } = render(createElement(Markdown, null, MD_BLOCK))
    expect(rendersBlock(container)).toEqual([])
  })

  it('names each block missing when the body is plain text', () => {
    expect(rendersBlock(holding(`<p>${MD_BLOCK}</p>`))).toEqual(['no heading', 'no list', 'no code span', 'no code block', 'a literal **'])
  })
})

describe('HOSTILE', () => {
  it('holds every embedded-HTML row, every refused link and a tracking image, in one paragraph', () => {
    for (const [, markup] of EMBEDDED_HTML) expect(HOSTILE).toContain(markup)
    for (const [, href] of REFUSED_HREFS) expect(HOSTILE).toContain(`(${href})`)
    expect(HOSTILE).toContain('![tracker](https://images.example.com/p.png)')
    expect(HOSTILE_ROWS).toHaveLength(EMBEDDED_HTML.length + REFUSED_HREFS.length + 1)
    // A leading word, so a body reads it as one paragraph, never as an HTML block.
    expect(HOSTILE.startsWith('Notes ')).toBe(true)
  })
})
