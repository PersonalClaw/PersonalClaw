/**
 * A chat image that fails to load says whether it is gone, and only then offers Regenerate.
 *
 * A failed load is signed again once (`lib/signedUrl.ts`). When that gives the image nothing new to
 * load (nothing signs on this gateway, or the signature came back the same) or the new one fails
 * too, the page asks once through the request layer: a 404 means the image is gone, and is the
 * only answer that offers Regenerate, a model call; any other answer says it can't be shown now.
 * Driven through a stubbed `fetch`, so the wire is what is asserted.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resetSignedOutForTests } from '../lib/signedOut'
import { SIGN_PATH, resetSignedUrlsForTests } from '../lib/signedUrl'
import { Markdown } from './Markdown'

const RAW = '/api/artifacts/cat/raw?version=2'
const IMAGE = `![a cat on a mat](${RAW})`
const PAIR = 'A'.repeat(43)

let fetchSpy: ReturnType<typeof vi.fn>

beforeEach(() => {
  window.localStorage.clear()
  resetSignedUrlsForTests()
  resetSignedOutForTests()
  fetchSpy = vi.fn()
  vi.stubGlobal('fetch', fetchSpy)
})

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

function answer(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function probes(): unknown[][] {
  return fetchSpy.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === 'HEAD')
}

describe('a chat image that fails, on a gateway that signs nothing', () => {
  it('is asked about at once; a 404 says it is gone and offers Regenerate', async () => {
    fetchSpy.mockResolvedValue(answer(404))
    const { container } = render(<Markdown chatSessionKey="dashboard_a-chat">{IMAGE}</Markdown>)
    const img = container.querySelector('img')
    expect(img?.getAttribute('src')).toBe(RAW)
    fireEvent.error(img!)
    expect(await screen.findByText('This image is no longer available.')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Regenerate image/ })).toBeTruthy()
    expect(probes()).toHaveLength(1)
    expect(fetchSpy.mock.calls.some((c) => String(c[0]) === SIGN_PATH)).toBe(false)
  })

  it('any other answer says it can’t be shown now, and never offers Regenerate', async () => {
    fetchSpy.mockResolvedValue(answer(403, { error: { code: 'session_required', message: 'x' } }))
    const { container } = render(<Markdown chatSessionKey="dashboard_a-chat">{IMAGE}</Markdown>)
    fireEvent.error(container.querySelector('img')!)
    expect(await screen.findByText(/This image can’t be shown right now/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Regenerate image/ })).toBeNull()
  })
})

describe('a chat image waiting for its signature', () => {
  // On a gateway that signs it, the image's address is unknown until the signature comes back. The
  // image is its own element from the first render all the same, with the box it keeps, so a
  // reopened chat lands where it will stay; and nothing asks for the image before then: no source,
  // the address nowhere in the page, and no alt text in the meantime.
  const BOX = ['my-2', 'max-h-[28rem]', 'max-w-full', 'rounded-lg', 'border', 'border-outline-variant/40', 'object-contain']
  const SIGNED = `${RAW}&pc_exp=1&pc_sig=s`

  it("is the image's own element from the first render, and asks for nothing until it is signed", async () => {
    window.localStorage.setItem('pc_pair', PAIR)
    let sign!: (r: Response) => void
    fetchSpy.mockImplementation((input: RequestInfo | URL) =>
      String(input) === SIGN_PATH ? new Promise<Response>((r) => { sign = r }) : Promise.resolve(answer(200)))
    const { container } = render(<Markdown chatSessionKey="dashboard_a-chat">{IMAGE}</Markdown>)
    const held = container.querySelector('[data-image-pending]')
    expect(held, "the image's box is in the first layout").not.toBeNull()
    expect(held!.tagName, "it is the image's own element").toBe('IMG')
    expect([...held!.classList].sort(), 'the classes of the image it becomes, and no other').toEqual([...BOX].sort())
    expect(held!.getAttribute('alt'), 'no alt text while it waits').toBe('')
    expect(held!.textContent).toBe('')
    expect(container.querySelector('[src], [srcset]'), 'nothing that loads').toBeNull()
    expect(document.documentElement.outerHTML, 'the address is nowhere in the page while it waits').not.toContain('/api/artifacts/')
    await waitFor(() => expect(fetchSpy.mock.calls.some((c) => String(c[0]) === SIGN_PATH)).toBe(true))
    expect(fetchSpy.mock.calls.filter((c) => String(c[0]).includes('/api/artifacts/')), 'the image is not asked for').toHaveLength(0)
    expect(document.documentElement.outerHTML, 'nor once its signature is asked for').not.toContain('/api/artifacts/')
    sign(answer(200, { urls: [{ url: RAW, signed: SIGNED, exp: Math.floor(Date.now() / 1000) + 600 }] }))
    const img = await waitFor(() => {
      const el = container.querySelector('img[src]')
      expect(el).not.toBeNull()
      return el!
    })
    expect(img, 'the same element takes its source').toBe(held)
    expect(img.getAttribute('src')).toBe(SIGNED)
    expect(img.getAttribute('alt'), 'and its alt').toBe('a cat on a mat')
    expect([...img.classList].sort()).toEqual([...BOX].sort())
    expect(img.hasAttribute('data-image-pending')).toBe(false)
  })

  it('drawn again with a fresh signature held, is signed from its first render, and the signer is not asked again', async () => {
    window.localStorage.setItem('pc_pair', PAIR)
    fetchSpy.mockImplementation(async (input: RequestInfo | URL) =>
      String(input) === SIGN_PATH
        ? answer(200, { urls: [{ url: RAW, signed: SIGNED, exp: Math.floor(Date.now() / 1000) + 600 }] })
        : answer(200))
    const signings = () => fetchSpy.mock.calls.filter((c) => String(c[0]) === SIGN_PATH).length
    const first = render(<Markdown chatSessionKey="dashboard_a-chat">{IMAGE}</Markdown>)
    await waitFor(() => expect(first.container.querySelector('img')?.getAttribute('src')).toBe(SIGNED))
    first.unmount()
    expect(signings()).toBe(1)
    // A reply's blocks mount again at the end of its stream: the image they mount is the one shown.
    const again = render(<Markdown chatSessionKey="dashboard_a-chat">{IMAGE}</Markdown>)
    const img = again.container.querySelector('img')
    expect(img?.getAttribute('src'), 'signed from its first render').toBe(SIGNED)
    expect(img?.getAttribute('alt')).toBe('a cat on a mat')
    expect(img?.hasAttribute('data-image-pending')).toBe(false)
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
    expect(signings(), 'the signer is not asked again').toBe(1)
    expect(again.container.querySelector('img')?.getAttribute('src')).toBe(SIGNED)
  })
})

describe('a chat image that fails, on a gateway that signs it', () => {
  it('is signed again once; when that brings the same URL back it is asked about at once', async () => {
    window.localStorage.setItem('pc_pair', PAIR)
    const exp = Date.now() / 1000 + 7200
    fetchSpy.mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url) === SIGN_PATH) return answer(200, { urls: [{ url: RAW, signed: `${RAW}&pc_exp=1&pc_sig=s`, exp }] })
      if (init?.method === 'HEAD') return answer(404)
      return answer(500)
    })
    const { container } = render(<Markdown chatSessionKey="dashboard_a-chat">{IMAGE}</Markdown>)
    await waitFor(() => expect(container.querySelector('img')?.getAttribute('src')).toContain('pc_sig='))
    fireEvent.error(container.querySelector('img')!)
    expect(await screen.findByText('This image is no longer available.')).toBeTruthy()
    expect(probes()).toHaveLength(1)
  })

  it('a new signature is tried first; its failure is asked about once', async () => {
    window.localStorage.setItem('pc_pair', PAIR)
    let n = 0
    fetchSpy.mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url) === SIGN_PATH) {
        n += 1
        return answer(200, { urls: [{ url: RAW, signed: `${RAW}&pc_exp=${n}&pc_sig=s${n}`, exp: Date.now() / 1000 + 7200 + n }] })
      }
      if (init?.method === 'HEAD') return answer(403, { error: { code: 'session_required', message: 'x' } })
      return answer(500)
    })
    const { container } = render(<Markdown chatSessionKey="dashboard_a-chat">{IMAGE}</Markdown>)
    await waitFor(() => expect(container.querySelector('img')?.getAttribute('src')).toContain('pc_sig=s1'))
    fireEvent.error(container.querySelector('img')!)
    await waitFor(() => expect(container.querySelector('img')?.getAttribute('src')).toContain('pc_sig=s2'))
    expect(probes()).toHaveLength(0)
    fireEvent.error(container.querySelector('img')!)
    expect(await screen.findByText(/This image can’t be shown right now/)).toBeTruthy()
    expect(probes()).toHaveLength(1)
  })
})
