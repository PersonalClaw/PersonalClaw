// @vitest-environment jsdom
// Needs you and the corner it opens from draw no native `title=`: a browser's own tooltip can't be
// themed, can't be reached from a keyboard and never opens on touch. Each tip there is the themed
// `Tooltip`, a key label kept in it: the approval's queue form (its choices and its risk and reach
// chips), the panel's heading, Back and Close, and the corner (the rail tab, the terminal, the
// YOLO mark, the badge, the bell, the model chip). The chat's own approval card keeps its titles.
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApprovalSegment } from '../pages/chat/chatTypes'
import { tipOf } from '../test/tipOf'

vi.mock('../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      status: vi.fn(async () => ({ yolo: true })),
      notifications: vi.fn(async () => ({ notifications: [] })),
      degraded: vi.fn(async () => ({ surfaces: [{ surface: 'search_ranking', label: 'Semantic search', available: false, floor: 'Keyword ranking', backlog: 0 }] })),
      onboarding: vi.fn(async () => ({ has_model_provider: true })),
      system: vi.fn(() => new Promise(() => {})),
    },
  }
})
vi.mock('../lib/useNeedsYou', () => ({ useNeedsYou: () => ({ rows: [{ id: 'one' }], data: {}, error: null }) }))
vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const { ApprovalCard } = await import('../pages/chat/ApprovalCard')
const { ListBesideDetail } = await import('../pages/capabilities/ListBesideDetail')
const { ShellCornerLeft, ShellCornerRight } = await import('./ShellCorners')
const { bindingLabel } = await import('../app/keymap')

const titled = (root: ParentNode = document) => [...root.querySelectorAll('[title]')].map((el) => el.outerHTML.slice(0, 120))

function seg(): ApprovalSegment {
  return {
    kind: 'approval', id: 'a1', tool: 'bash', input: 'rm -rf ~/code/site/build', risk: 'destructive', confirms: [],
    blastRadius: { writes: true, network: false, shell: true, saysReadOnly: false, readOnly: false },
  }
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('no native tooltip on Needs you', () => {
  it("the approval's queue form: its choices and chips tip through Tooltip, Allow's with its key", () => {
    const { container } = render(<ApprovalCard seg={seg()} onAct={() => {}} answers="once" form="queue" />)
    expect(titled(container)).toEqual([])
    const allow = screen.getByRole('button', { name: /^Allow/ })
    expect(tipOf(allow)).toContain(bindingLabel('approve-focused'))
  })

  it('the chat card too: its choices, its risk and its reach tip through Tooltip, Allow\'s with its key', () => {
    const { container } = render(<ApprovalCard seg={seg()} onAct={() => {}} answers="once" />)
    expect(titled(container)).toEqual([])
    expect(tipOf(screen.getByRole('button', { name: /^Allow/ }))).toContain(bindingLabel('approve-focused'))
  })

  it('a code block\'s Copy, Open beside and Run tip through Tooltip', async () => {
    const { Markdown } = await import('./Markdown')
    const { container } = render(<Markdown>{'```diff\n+added\n```\n\n```bash\nls\n```'}</Markdown>)
    expect(titled(container)).toEqual([])
  })

  it("the panel's heading, Back and Close", () => {
    render(
      <ListBesideDetail header={<h1>Needs you</h1>} noun="Needs you" listWidth={720} splitWidth={440}
        headingScrolls compactTargets focusOnOpen edgeFade
        detail={{ id: 'a', title: 'Deletes files in ~/code/site', onClose: () => {}, body: <p>the ask</p> }}>
        <button type="button">a card</button>
      </ListBesideDetail>,
    )
    expect(titled()).toEqual([])
  })

  it('the corner, the YOLO mark among it, with each key kept in its tip', async () => {
    render(<>
      <ShellCornerLeft collapsed={false} onToggle={() => {}} />
      <ShellCornerRight terminalOpen={false} onToggleTerminal={() => {}} navigate={() => {}} />
    </>)
    await screen.findByRole('button', { name: /^YOLO active/ })
    await screen.findByRole('button', { description: /running without a model/ })
    expect(titled()).toEqual([])
    expect(tipOf(screen.getByRole('button', { name: 'Collapse sidebar' }))).toContain(bindingLabel('sidebar'))
    expect(tipOf(screen.getByRole('button', { name: 'Open terminal' }))).toContain(bindingLabel('drawer'))
  })
})
