import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Markdown } from './Markdown'
import { ReadingView } from '../pages/knowledge/ReadingView'
import { InboxMessageBody } from '../pages/inbox/ForeignContent'
import { ToolOutput } from '../pages/tools/ToolOutput'
import type { InboxItem, KnowledgeItem } from '../lib/api'

// ── An image from another site in shown text loads only when the owner presses Load ─────────
//
// Text the dashboard shows but did not write can name an image on another site. Drawing it as an
// image element makes the browser request that address the moment the text is shown, outside the
// gateway's network settings, carrying whatever its author wrote into it. So such an image is a
// placeholder naming its host, and its Load button asks the gateway to fetch it
// (`POST /api/images/remote`), which shows the bytes from a `blob:` address. jsdom fetches no
// images, so these pin the DOM: no `img` ever points at another host.

const CHART = 'https://images.example.com/chart.png?q=1'
const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0])

/** Every `img` source in *root* the browser would load from another origin: not a `data:` or a
 *  `blob:` one, and not this gateway's own once parsed as the browser parses it. */
function offGateway(root: HTMLElement): string[] {
  return Array.from(root.querySelectorAll('img'))
    .map((img) => img.getAttribute('src') ?? '')
    .filter((src) => {
      const u = new URL(src, window.location.href)
      return u.protocol !== 'data:' && u.protocol !== 'blob:' && u.origin !== window.location.origin
    })
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn(async () => new Response(PNG, { status: 200, headers: { 'Content-Type': 'image/png' } }))
  vi.stubGlobal('fetch', fetchMock)
  URL.createObjectURL = vi.fn(() => 'blob:http://localhost/remote-1')
  URL.revokeObjectURL = vi.fn()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('a remote image in rendered text is a placeholder until Load is pressed', () => {
  it('shows its host and its words, and draws nothing from another site', () => {
    const { container } = render(<Markdown>{`![Revenue chart](${CHART})`}</Markdown>)
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('images.example.com')
    expect(container.textContent).toContain('Revenue chart')
    const load = screen.getByRole('button', { name: /load image.*images\.example\.com/i })
    expect(load.getAttribute('title')).toBe(CHART)
    expect(container.textContent).not.toContain(CHART)
    expect(fetchMock).not.toHaveBeenCalled()
    // Nothing reaches ahead for the host either: no preconnect, prefetch or icon.
    expect(document.head.querySelectorAll('link')).toHaveLength(0)
  })

  it('holds in a chat reply too', () => {
    const { container } = render(
      <Markdown widgets chatSessionKey="s" streaming>{`Here is the chart:\n\n![Revenue chart](${CHART})\n\nAnd more`}</Markdown>,
    )
    expect(offGateway(container)).toEqual([])
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByRole('button', { name: /load image.*images\.example\.com/i })).toBeTruthy()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('Load asks the gateway once and shows the bytes from a blob address', async () => {
    const { container } = render(<Markdown>{`![Revenue chart](${CHART})`}</Markdown>)
    expect(fetchMock).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /load image/i }))
    await waitFor(() => expect(container.querySelector('img')).not.toBeNull())
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [input, init] = fetchMock.mock.calls[0] as [RequestInfo | URL, RequestInit]
    expect(String(input)).toMatch(/\/api\/images\/remote$/)
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ url: CHART })
    expect(container.querySelector('img')?.getAttribute('src')).toBe('blob:http://localhost/remote-1')
    expect(container.querySelector('img')?.getAttribute('alt')).toBe('Revenue chart')
    expect(offGateway(container)).toEqual([])
  })

  it('a refusal says why inside the placeholder, and shows no image', async () => {
    fetchMock.mockImplementation(async () => new Response(
      JSON.stringify({ error: { code: 'remote_image_refused', message: 'blocked.example is on Denied hosts.' } }),
      { status: 403, headers: { 'Content-Type': 'application/json' } },
    ))
    const { container } = render(<Markdown>{'![Logo](https://blocked.example/logo.png)'}</Markdown>)
    fireEvent.click(screen.getByRole('button', { name: /load image/i }))
    await waitFor(() => expect(container.textContent).toContain('blocked.example is on Denied hosts.'))
    expect(container.querySelector('img')).toBeNull()
    // The same press earns the same refusal, so none is offered.
    expect(screen.queryByRole('button', { name: /load image/i })).toBeNull()
  })

  it('a Load the gateway could not take yet says so, and keeps its Load button', async () => {
    fetchMock.mockImplementationOnce(async () => new Response(
      JSON.stringify({ error: { code: 'remote_image_busy', message: 'PersonalClaw was still loading other images. Press Load again.' } }),
      { status: 503, headers: { 'Content-Type': 'application/json' } },
    ))
    const { container } = render(<Markdown>{`![Revenue chart](${CHART})`}</Markdown>)
    fireEvent.click(screen.getByRole('button', { name: /load image/i }))
    await waitFor(() => expect(container.textContent).toContain('still loading other images'))
    fireEvent.click(screen.getByRole('button', { name: /load image/i }))
    await waitFor(() => expect(container.querySelector('img')).not.toBeNull())
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('a site that did not send the image keeps the Load button', async () => {
    fetchMock.mockImplementationOnce(async () => new Response(
      JSON.stringify({ error: { code: 'remote_image_unreachable', message: "The image's site took too long to send it, so nothing was shown." } }),
      { status: 502, headers: { 'Content-Type': 'application/json' } },
    ))
    const { container } = render(<Markdown>{`![Revenue chart](${CHART})`}</Markdown>)
    fireEvent.click(screen.getByRole('button', { name: /load image/i }))
    await waitFor(() => expect(container.textContent).toContain('took too long'))
    expect(screen.getByRole('button', { name: /load image/i })).toBeTruthy()
  })

  it('a Load the gateway never answered says so in words, and keeps its Load button', async () => {
    fetchMock.mockImplementationOnce(async () => { throw new TypeError('Failed to fetch') })
    const { container } = render(<Markdown>{`![Revenue chart](${CHART})`}</Markdown>)
    fireEvent.click(screen.getByRole('button', { name: /load image/i }))
    await waitFor(() => expect(container.textContent).toContain("The gateway didn't answer, so nothing was shown."))
    expect(container.textContent).not.toContain('Failed to fetch')
    expect(screen.getByRole('button', { name: /load image/i })).toBeTruthy()
  })

  it('a Load still in flight is let go when the image leaves the page or its address changes', async () => {
    fetchMock.mockImplementation(() => new Promise<Response>(() => {}))
    const { rerender, unmount } = render(<Markdown>{`![Revenue chart](${CHART})`}</Markdown>)
    fireEvent.click(screen.getByRole('button', { name: /load image/i }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    const first = (fetchMock.mock.calls[0] as [RequestInfo | URL, RequestInit])[1].signal
    expect(first?.aborted).toBe(false)
    rerender(<Markdown>{'![Other chart](https://other.example.com/b.png)'}</Markdown>)
    expect(first?.aborted).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: /load image/i }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    const second = (fetchMock.mock.calls[1] as [RequestInfo | URL, RequestInit])[1].signal
    unmount()
    expect(second?.aborted).toBe(true)
  })

  it('a Load still in flight when the image leaves the page makes no blob address', async () => {
    let answer!: (r: Response) => void
    fetchMock.mockImplementation(() => new Promise<Response>((resolve) => { answer = resolve }))
    const { unmount } = render(<Markdown>{`![Revenue chart](${CHART})`}</Markdown>)
    fireEvent.click(screen.getByRole('button', { name: /load image/i }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    unmount()
    answer(new Response(PNG, { status: 200, headers: { 'Content-Type': 'image/png' } }))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(URL.createObjectURL).not.toHaveBeenCalled()
  })

  it('a Load still in flight when the address changes shows no image under the new one', async () => {
    let answer!: (r: Response) => void
    fetchMock.mockImplementation(() => new Promise<Response>((resolve) => { answer = resolve }))
    const { container, rerender } = render(<Markdown>{`![Revenue chart](${CHART})`}</Markdown>)
    fireEvent.click(screen.getByRole('button', { name: /load image/i }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    rerender(<Markdown>{'![Other chart](https://other.example.com/b.png)'}</Markdown>)
    answer(new Response(PNG, { status: 200, headers: { 'Content-Type': 'image/png' } }))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByRole('button', { name: /load image from other\.example\.com/i })).toBeTruthy()
  })

  it('the blob address is let go when the image leaves the page', async () => {
    const { container, unmount } = render(<Markdown>{`![Revenue chart](${CHART})`}</Markdown>)
    fireEvent.click(screen.getByRole('button', { name: /load image/i }))
    await waitFor(() => expect(container.querySelector('img')).not.toBeNull())
    unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:http://localhost/remote-1')
  })
})

describe('each surface that shows text it did not write draws no image from another site', () => {
  const BODY = `A note.\n\n![Chart](${CHART})\n\nThe end.`

  it('an inbox message', () => {
    const { container } = render(<InboxMessageBody item={{ id: 'i1', message: BODY } as InboxItem} owner="owner" />)
    expect(offGateway(container)).toEqual([])
    expect(container.textContent).toContain('images.example.com')
  })

  it('a knowledge item in the reading view', () => {
    const item = { id: 'k1', title: 'Saved page', content: BODY, item_type: 'bookmark', word_count: 5 } as KnowledgeItem
    const { container } = render(<ReadingView item={item} annotations={[]} onAnnotationsChanged={() => {}} />)
    expect(offGateway(container)).toEqual([])
    expect(container.textContent).toContain('images.example.com')
  })

  it("a tool's output on the Tools page", () => {
    const { container } = render(<ToolOutput text={`# Fetched page\n\n${BODY}`} />)
    expect(offGateway(container)).toEqual([])
    expect(container.textContent).toContain('images.example.com')
  })
})

describe('what loaded before still loads, and what was refused still is', () => {
  it('an artifact image draws at once', () => {
    const { container } = render(<Markdown>{'![Generated](/api/artifacts/sales-chart/raw?version=2)'}</Markdown>)
    expect(container.querySelector('img')?.getAttribute('src')).toBe('/api/artifacts/sales-chart/raw?version=2')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([
    ['an artifact path that climbs out of the library', '/api/artifacts/%2e%2e/raw'],
    ['plain http', 'http://images.example.com/p.png'],
    ['a relative path', 'images/diagram.png'],
    ['a script scheme', 'javascript:alert(1)'],
    ['a data URL that is not an image', 'data:text/html;base64,PHNjcmlwdD4='],
  ])('%s shows its words and offers no Load', (_name, src) => {
    const { container } = render(<Markdown>{`![Quarterly diagram](${src})`}</Markdown>)
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('Quarterly diagram')
    expect(screen.queryByRole('button', { name: /load image/i })).toBeNull()
  })
})
