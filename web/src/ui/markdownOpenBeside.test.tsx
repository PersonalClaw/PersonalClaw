/**
 * A code block, a diff and a table in rendered text offer Open beside where a surface has a place
 * beside its reading (`OpenBesideProvider`) and the text asks for it (`beside`). A block's address
 * is what it is, not where it sits: its kind and a fingerprint of its parsed content. So a reply
 * names the same block live and after a reload, and a block nested in a list or a quote opens as the
 * block it parsed to (#3820's split). Each block registers under its address while it is mounted,
 * and the surface draws the registered block.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Markdown } from './Markdown'
import { BesideRegistry, OpenBesideProvider } from './OpenBeside'

afterEach(cleanup)

const CODE = '```ts\nconst a = 1\n```'
const TABLE = '| Day | Task |\n|---|---|\n| Mon | Write |'
const DIFF = '```diff\n-old\n+new\n```'
const MERMAID = '```mermaid\ngraph TD; A-->B\n```'
const TEXT = ['Intro with <kbd>K</kbd>.', '', CODE, '', TABLE, '', DIFF, ''].join('\n')

function show(texts: string[], opts: { streaming?: boolean; provided?: boolean; beside?: boolean; widgets?: boolean } = {}) {
  const open = vi.fn()
  const registry = new BesideRegistry()
  const { streaming = false, provided = true, beside = true, widgets = false } = opts
  const md = <>{texts.map((t, i) => <Markdown key={i} beside={beside} streaming={streaming} widgets={widgets}>{t}</Markdown>)}</>
  const view = render(provided ? <OpenBesideProvider open={open} registry={registry}>{md}</OpenBesideProvider> : md)
  return { open, registry, view }
}

/** The address the `index`th call to `open` handed over. */
const handed = (open: ReturnType<typeof vi.fn>, index = 0) => open.mock.calls[index][0] as string

/** What the block registered under `address` draws, as text. */
function drawn(registry: BesideRegistry, address: string): string {
  const block = registry.get(address)
  expect(block, `a block is registered under ${address}`).not.toBeNull()
  const { container, unmount } = render(<div>{block!.render()}</div>)
  const text = container.textContent ?? ''
  unmount()
  return text
}

describe('Open beside names a block by what it is', () => {
  for (const [name, kind, shows] of [['ts code', 'code', 'const a = 1'], ['table', 'table', 'Write'], ['diff', 'diff', '+new']] as const) {
    it(`the ${name} hands over a ${kind} address, and the block registered there draws it`, () => {
      const { open, registry } = show([TEXT])
      fireEvent.click(screen.getByRole('button', { name: `Open ${name} beside` }))
      const address = handed(open)
      expect(address.startsWith(`${kind}-`)).toBe(true)
      expect(drawn(registry, address)).toContain(shows)
    })
  }

  it('names the same block the same wherever it sits, so a reload names it as the live page did', () => {
    const { open } = show([`Live, first:\n\n${CODE}`, `Reloaded, after other words and a list:\n\n- one\n- two\n\n${CODE}\n\nAnd more.`])
    const [first, second] = screen.getAllByRole('button', { name: 'Open ts code beside' })
    fireEvent.click(first)
    fireEvent.click(second)
    expect(handed(open, 0)).toBe(handed(open, 1))
  })

  it('names two different blocks differently', () => {
    const { open } = show([CODE, '```ts\nconst a = 2\n```'])
    for (const b of screen.getAllByRole('button', { name: 'Open ts code beside' })) fireEvent.click(b)
    expect(handed(open, 0)).not.toBe(handed(open, 1))
  })

  it('opens a block nested in a list or a quote as the block it parsed to', () => {
    const listed = '1. Install it:\n\n    ```bash\n    npm i\n    ```'
    const quoted = '> ```js\n> run()\n> ```'
    const { open, registry } = show([listed, quoted])
    fireEvent.click(screen.getByRole('button', { name: 'Open bash code beside' }))
    fireEvent.click(screen.getByRole('button', { name: 'Open js code beside' }))
    const bash = drawn(registry, handed(open, 0))
    const js = drawn(registry, handed(open, 1))
    expect(bash).toContain('npm i')
    expect(js).toContain('run()')
    for (const text of [bash, js]) {
      expect(text).not.toContain('```')
      expect(text).not.toContain('> ')
    }
  })

  it('takes a block back when it unmounts', () => {
    const { open, registry, view } = show([CODE])
    fireEvent.click(screen.getByRole('button', { name: 'Open ts code beside' }))
    const address = handed(open)
    expect(registry.get(address)).not.toBeNull()
    view.unmount()
    expect(registry.get(address)).toBeNull()
  })
})

describe('Open beside is offered only where it can open', () => {
  const offers = () => screen.queryAllByRole('button', { name: /beside$/ })

  it('not while the text streams, since a block still arriving has no end, and nothing registers', () => {
    const add = vi.spyOn(BesideRegistry.prototype, 'add')
    show([TEXT], { streaming: true })
    expect(offers()).toEqual([])
    expect(add).not.toHaveBeenCalled()
    add.mockRestore()
  })

  it('not where no surface provides a place beside the reading', () => {
    show([TEXT], { provided: false })
    expect(offers()).toEqual([])
  })

  it('not on text that does not ask for it', () => {
    show([TEXT], { beside: false })
    expect(offers()).toEqual([])
  })

  it('not on a diagram', () => {
    show([MERMAID])
    expect(offers()).toEqual([])
  })
})
