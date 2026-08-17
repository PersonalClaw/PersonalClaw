/** The gap AS-5 closes: a widget action raised OUTSIDE a chat used to be dropped on
 *  the floor. Both non-chat hosts named in the plan are driven here — the
 *  artifact-library preview and the dashboard tile band — and each must land its
 *  `[UI]` turn in a chat session through the ONE `ne:launch-chat` path.
 *
 *  A second way into chat would be the dual path this repo forbids, so the assertion
 *  is deliberately narrow: `launchChat` is called, and the staged turn is exactly the
 *  text the widget produced. */
import { render, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { IframeHtmlPreview } from '../content/renderers'
import { PinnedTiles } from '../../pages/dashboard/PinnedTiles'
import { ReactWidgetFrame } from './ReactWidgetFrame'
import { takePendingWidgetAction, useWidgetActionLauncher } from './useWidgetActionBridge'

const launched: unknown[] = []
vi.mock('../../app/appSdk', () => ({
  launchChat: (opts?: unknown) => { launched.push(opts ?? {}) },
  notify: () => {},
}))

const WIDGET_HTML = '<button data-action="refresh" data-payload=\'{"range":"30d"}\'>Refresh</button>'

vi.mock('../../lib/api', () => ({
  api: {
    artifactExists: vi.fn(async () => false),
    createArtifact: vi.fn(async () => ({})),
    deleteArtifact: vi.fn(async () => ({})),
    pinTile: vi.fn(async () => ({})),
    dashboardViews: vi.fn(async () => [
      { id: 'overview', tiles: [{ ref: 'artifact:sales-view', order: 0, added_by: 'user' }] },
    ]),
    artifact: vi.fn(async () => ({ slug: 'sales-view', name: 'Sales', content: WIDGET_HTML })),
    resolveTile: vi.fn(async () => ({})),
  },
}))

beforeAll(() => {
  if (typeof URL.createObjectURL !== 'function') {
    URL.createObjectURL = () => 'blob:non-chat-host-test'
    URL.revokeObjectURL = () => {}
  }
})

beforeEach(() => { launched.length = 0; takePendingWidgetAction(); localStorage.clear() })

/** The app shell — the only place the non-chat fallback is registered. */
function Shell({ children }: { children: React.ReactNode }) {
  useWidgetActionLauncher()
  return <>{children}</>
}

function postFrom(child: Window | null, data: unknown) {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { data, source: child as MessageEventSource }))
  })
}

const action = { type: 'widget-action', action: 'refresh', payload: { range: '30d' } }

describe('artifact-library preview', () => {
  it('lands the [UI] turn in a chat session via ne:launch-chat', async () => {
    const view = render(
      <Shell><IframeHtmlPreview content={WIDGET_HTML} mode="dark" title="Sales" /></Shell>
    )
    await act(async () => {})
    const child = view.container.querySelector('iframe')?.contentWindow ?? null
    expect(child).not.toBeNull()
    postFrom(child, action)
    expect(launched).toEqual([{}])
    expect(takePendingWidgetAction()).toBe('[UI] refresh: {"range":"30d"}')
  })

  it('still refuses a forged action from another window', async () => {
    render(<Shell><IframeHtmlPreview content={WIDGET_HTML} mode="dark" title="Sales" /></Shell>)
    await act(async () => {})
    const foreign = document.createElement('iframe')
    document.body.appendChild(foreign)
    postFrom(foreign.contentWindow, action)
    expect(launched).toEqual([])
    expect(takePendingWidgetAction()).toBeNull()
  })
})

describe('dashboard tile band', () => {
  it('lands the [UI] turn in a chat session via ne:launch-chat', async () => {
    const view = render(<Shell><PinnedTiles /></Shell>)
    await act(async () => {})
    const iframe = view.container.querySelector('[data-testid="pinned-tiles"] iframe')
    expect(iframe, 'the tile band rendered no widget frame').not.toBeNull()
    postFrom((iframe as HTMLIFrameElement).contentWindow, action)
    expect(launched).toEqual([{}])
    expect(takePendingWidgetAction()).toBe('[UI] refresh: {"range":"30d"}')
  })
})

describe('react widget host', () => {
  it('does not forward actions — its child document has no human-gesture gate', async () => {
    const view = render(<Shell><ReactWidgetFrame jsx="const App = () => null" title="R" /></Shell>)
    await act(async () => {})
    const child = view.container.querySelector('iframe')?.contentWindow ?? null
    postFrom(child, action)
    expect(launched).toEqual([])
    expect(takePendingWidgetAction()).toBeNull()
  })
})
