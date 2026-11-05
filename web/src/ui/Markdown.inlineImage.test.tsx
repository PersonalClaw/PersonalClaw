/**
 * A chat image that fails to load says whether it is gone, and only then offers Regenerate.
 *
 * A failed load is signed again once (`lib/signedUrl.ts`). When that gives the image nothing new to
 * load (nothing signs on this gateway, or the signature came back the same) or the new one fails
 * too, the page asks once through the request layer: a 404 means the image is gone, and is the
 * only answer that offers Regenerate, a model call; any other answer says it can't be shown now.
 * Driven through a stubbed `fetch`, so the wire is what is asserted.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
