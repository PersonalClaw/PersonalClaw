/**
 * What the browser loads by itself (media, documents, downloads, new tabs) is signed on a gateway
 * that pairs its sign-ins, and is exactly the URL it was everywhere else.
 *
 * Driven through a stubbed `fetch`, so the wire is what is asserted: whether the signing route is
 * asked at all, which URLs it is sent, and which URL each element finally loads.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createAppApi } from '../app/appSdk'
import { SignedImg } from '../ui/SignedMedia'
import { downloadFrom } from './download'
import { resetSignedOutForTests } from './signedOut'
import { keepPair } from './pair'
import { SIGN_PATH, bindSignedSource, resetSignedUrlsForTests, signUrl, signUrls, useSignedUrl } from './signedUrl'

const A = 'A'.repeat(43)
const RAW = '/api/artifacts/cat/raw?version=2'

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const signedRows = (urls: string[], exp = Date.now() / 1000 + 7200) =>
  json(200, { urls: urls.map((url) => ({ url, signed: `${url}${url.includes('?') ? '&' : '?'}pc_exp=1&pc_sig=s`, exp })) })

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
  delete (window as { pclawDesktop?: unknown }).pclawDesktop
  window.localStorage.clear()
})

const mintCalls = () => fetchSpy.mock.calls.filter((c) => String(c[0]) === SIGN_PATH)

describe('signUrls', () => {
  it('with no pair, outside the desktop: the URLs as they are, and no request', async () => {
    expect(await signUrls([RAW])).toEqual([RAW])
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('with a pair: signs each gateway URL, and never sends a data:, blob: or other site URL', async () => {
    window.localStorage.setItem('pc_pair', A)
    fetchSpy.mockImplementation(async (_u: string, init: RequestInit) => signedRows(JSON.parse(String(init.body)).urls))
    const out = await signUrls([RAW, 'data:image/png;base64,AA', 'https://example.com/x.png'])
    expect(out[0]).toContain('pc_sig=')
    expect(out.slice(1)).toEqual(['data:image/png;base64,AA', 'https://example.com/x.png'])
    expect(JSON.parse(String(mintCalls()[0][1].body)).urls).toEqual([RAW])
    expect(new Headers(mintCalls()[0][1].headers).get('X-PersonalClaw-Pair')).toBe(A)
  })

  it("on a gateway without the signing route (404 or 405): the URL as it is, and the tab stops asking", async () => {
    window.localStorage.setItem('pc_pair', A)
    fetchSpy.mockResolvedValue(json(404, { error: { code: 'not_found', message: 'no' } }))
    expect(await signUrls([RAW])).toEqual([RAW])
    expect(await signUrls(['/api/file-raw?path=a'])).toEqual(['/api/file-raw?path=a'])
    expect(mintCalls()).toHaveLength(1)
  })

  it('a URL the gateway refuses to sign comes back as it is; the rest are still signed', async () => {
    window.localStorage.setItem('pc_pair', A)
    fetchSpy.mockResolvedValue(json(200, { urls: [{ url: RAW, error: 'not_signable' }, { url: '/api/file-raw?path=b', signed: '/api/file-raw?path=b&pc_sig=s', exp: Date.now() / 1000 + 60 }] }))
    expect(await signUrls([RAW, '/api/file-raw?path=b'])).toEqual([RAW, '/api/file-raw?path=b&pc_sig=s'])
  })

  it('in the desktop app it asks even with no pair stored: the main process adds the header', async () => {
    ;(window as { pclawDesktop?: unknown }).pclawDesktop = {}
    fetchSpy.mockImplementation(async (_u: string, init: RequestInit) => signedRows(JSON.parse(String(init.body)).urls))
    expect((await signUrls([RAW]))[0]).toContain('pc_sig=')
  })
})

describe('the elements', () => {
  function Probe({ url }: { url: string }) {
    const shown = useSignedUrl(url)
    return <span data-testid="shown">{shown ?? '(waiting)'}</span>
  }

  it('without signing, the first render already has the URL as it is', () => {
    render(<Probe url={RAW} />)
    expect(screen.getByTestId('shown').textContent).toBe(RAW)
  })

  it('with signing, an image waits for its signature and never loads the unsigned URL', async () => {
    window.localStorage.setItem('pc_pair', A)
    let answer!: (r: Response) => void
    fetchSpy.mockImplementation(() => new Promise<Response>((r) => { answer = r }))
    const { container } = render(<SignedImg src={RAW} alt="a cat" />)
    expect(container.querySelector('img')).toBeNull()
    await waitFor(() => expect(mintCalls()).toHaveLength(1))
    await act(async () => { answer(signedRows([RAW])) })
    await waitFor(() => expect(container.querySelector('img')?.getAttribute('src')).toContain('pc_sig='))
  })

  it('a download is clicked at once as it is, or after signing when signing applies', async () => {
    const clicked: string[] = []
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { clicked.push(this.getAttribute('href') ?? '') })
    downloadFrom('/api/projects/p/export')
    expect(clicked).toEqual(['/api/projects/p/export'])
    window.localStorage.setItem('pc_pair', A)
    fetchSpy.mockImplementation(async (_u: string, init: RequestInit) => signedRows(JSON.parse(String(init.body)).urls))
    downloadFrom('/api/rooms/r/export?format=md')
    await waitFor(() => expect(clicked[1]).toContain('pc_sig='))
    click.mockRestore()
  })
})

describe('an app page', () => {
  const app = { name: 'minutes', permissions: { api: ['/api/knowledge/items/*'], events: [] } } as never

  it("mediaUrl against today's gateway is the plain URL, through the host's signer", async () => {
    window.localStorage.setItem('pc_pair', A)
    fetchSpy.mockResolvedValue(json(404, { error: { code: 'not_found', message: 'no' } }))
    const url = '/api/knowledge/items/k1/file'
    expect(await createAppApi(app).mediaUrl(url)).toBe(url)
    // The host signer: never an app token on the signing request.
    for (const call of mintCalls()) expect(new Headers(call[1].headers).get('Authorization')).toBeNull()
  })

  it('mediaUrl refuses a path the app did not declare, with no request', async () => {
    await expect(createAppApi(app).mediaUrl('/api/terminal/sessions')).rejects.toThrow(/not declared/)
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})

describe('how the signer asks', () => {
  it('a 405 is a gateway without the route too: the URL as it is, and the tab stops asking', async () => {
    window.localStorage.setItem('pc_pair', A)
    fetchSpy.mockResolvedValue(json(405, { error: { code: 'method_not_allowed', message: 'no' } }))
    expect(await signUrls([RAW])).toEqual([RAW])
    expect(await signUrls([RAW])).toEqual([RAW])
    expect(mintCalls()).toHaveLength(1)
  })

  it('every URL one render wants goes in one request, and a gateway without the route is asked once', async () => {
    window.localStorage.setItem('pc_pair', A)
    fetchSpy.mockResolvedValue(json(404, { error: { code: 'not_found', message: 'no' } }))
    render(
      <>
        <SignedImg src="/api/artifacts/a/raw" alt="a" />
        <SignedImg src="/api/artifacts/b/raw" alt="b" />
        <SignedImg src="/api/artifacts/c/raw" alt="c" />
      </>,
    )
    await waitFor(() => expect(mintCalls()).toHaveLength(1))
    expect(JSON.parse(String(mintCalls()[0][1].body)).urls).toHaveLength(3)
    await waitFor(() => expect(document.querySelectorAll('img')).toHaveLength(3))
    render(<SignedImg src="/api/artifacts/d/raw" alt="d" />)
    expect(mintCalls()).toHaveLength(1)
  })

  it('a URL already on its way waits for that request instead of sending its own', async () => {
    window.localStorage.setItem('pc_pair', A)
    fetchSpy.mockImplementation(async (_u: string, init: RequestInit) => signedRows(JSON.parse(String(init.body)).urls))
    const [one, two] = await Promise.all([signUrl(RAW), signUrl(RAW)])
    expect(one).toBe(two)
    expect(mintCalls()).toHaveLength(1)
  })

  it('a failed request leaves the URL as it is and is asked again later, waiting longer each time', async () => {
    vi.useFakeTimers()
    try {
      window.localStorage.setItem('pc_pair', A)
      fetchSpy.mockResolvedValue(json(500, { error: { code: 'internal_error', message: 'no' } }))
      function Probe() {
        return <span data-testid="shown">{useSignedUrl(RAW) ?? '(waiting)'}</span>
      }
      render(<Probe />)
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(mintCalls()).toHaveLength(1)
      expect(screen.getByTestId('shown').textContent).toBe(RAW)
      await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
      expect(mintCalls()).toHaveLength(2)
      await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
      expect(mintCalls()).toHaveLength(2)
      fetchSpy.mockImplementation(async (_u: string, init: RequestInit) => signedRows(JSON.parse(String(init.body)).urls))
      await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
      expect(mintCalls()).toHaveLength(3)
      expect(screen.getByTestId('shown').textContent).toContain('pc_sig=')
    } finally {
      vi.useRealTimers()
    }
  })

  it('a re-sign that ends no later means the session ends there: kept until then, asked once past it', async () => {
    vi.useFakeTimers()
    try {
      vi.setSystemTime(1_800_000_000_000)
      window.localStorage.setItem('pc_pair', A)
      fetchSpy.mockImplementation(async (_u: string, init: RequestInit) => signedRows(JSON.parse(String(init.body)).urls, 1_800_000_100))
      function Probe() {
        return <span>{useSignedUrl(RAW) ?? ''}</span>
      }
      render(<Probe />)
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(mintCalls()).toHaveLength(1)
      await act(async () => { await vi.advanceTimersByTimeAsync(80_500) })
      expect(mintCalls()).toHaveLength(2)
      await act(async () => { await vi.advanceTimersByTimeAsync(19_000) })
      expect(mintCalls()).toHaveLength(2)
      await act(async () => { await vi.advanceTimersByTimeAsync(1_000) })
      expect(mintCalls()).toHaveLength(3)
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
      expect(mintCalls()).toHaveLength(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('the signatures are this sign-in’s: a new pair forgets them', async () => {
    window.localStorage.setItem('pc_pair', A)
    fetchSpy.mockImplementation(async (_u: string, init: RequestInit) => signedRows(JSON.parse(String(init.body)).urls))
    await signUrl(RAW)
    await signUrl(RAW)
    expect(mintCalls()).toHaveLength(1)
    keepPair('B'.repeat(43))
    await signUrl(RAW)
    expect(mintCalls()).toHaveLength(2)
  })

  it('an element whose signed load fails is signed again once before its own onError hears of it', async () => {
    window.localStorage.setItem('pc_pair', A)
    let n = 0
    fetchSpy.mockImplementation(async (_u: string, init: RequestInit) => {
      n += 1
      return json(200, { urls: (JSON.parse(String(init.body)).urls as string[]).map((url) => ({ url, signed: `${url}&pc_sig=s${n}`, exp: Date.now() / 1000 + 7200 + n })) })
    })
    const heard = vi.fn()
    const { container } = render(<SignedImg src={RAW} alt="a cat" onError={heard} />)
    await waitFor(() => expect(container.querySelector('img')).not.toBeNull())
    fireEvent.error(container.querySelector('img')!)
    await waitFor(() => expect(mintCalls()).toHaveLength(2))
    expect(heard).not.toHaveBeenCalled()
    fireEvent.error(container.querySelector('img')!)
    await waitFor(() => expect(heard).toHaveBeenCalledTimes(1))
    expect(mintCalls()).toHaveLength(2)
  })
})

describe('an element an app holds', () => {
  it('is kept on its signed URL, signed again once when it fails, and let go when asked', async () => {
    window.localStorage.setItem('pc_pair', A)
    let n = 0
    fetchSpy.mockImplementation(async (_u: string, init: RequestInit) => {
      n += 1
      return json(200, { urls: (JSON.parse(String(init.body)).urls as string[]).map((url) => ({ url, signed: `${url}&pc_sig=s${n}`, exp: Date.now() / 1000 + 7200 + n })) })
    })
    const img = document.createElement('img')
    const stop = bindSignedSource(img, RAW)
    await waitFor(() => expect(img.getAttribute('src')).toContain('pc_sig=s1'))
    img.dispatchEvent(new Event('error'))
    await waitFor(() => expect(img.getAttribute('src')).toContain('pc_sig=s2'))
    img.dispatchEvent(new Event('error'))
    stop()
    await new Promise((r) => setTimeout(r, 0))
    expect(mintCalls()).toHaveLength(2)
  })

  it('on a gateway that signs nothing, it is the URL as it is, with no request', async () => {
    const img = document.createElement('img')
    const stop = bindSignedSource(img, RAW)
    await waitFor(() => expect(img.getAttribute('src')).toBe(RAW))
    stop()
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})

describe('what the element’s own onError hears', () => {
  it('with nothing signing, at once, inside the event, with currentTarget still the element', () => {
    function hide(e: React.SyntheticEvent<HTMLImageElement>) { (e.currentTarget as HTMLImageElement).style.display = 'none' }
    const { container } = render(<SignedImg src={RAW} alt="" onError={hide} />)
    const img = container.querySelector('img')!
    fireEvent.error(img)
    expect(img.style.display).toBe('none')
  })

  it('after a retry that brought nothing new, with currentTarget still the element', async () => {
    window.localStorage.setItem('pc_pair', A)
    fetchSpy.mockImplementation(async (_u: string, init: RequestInit) => signedRows(JSON.parse(String(init.body)).urls, Math.floor(Date.now() / 1000) + 7200))
    const seen: (EventTarget | null)[] = []
    const { container } = render(<SignedImg src={RAW} alt="" onError={(e) => { seen.push(e.currentTarget) }} />)
    await waitFor(() => expect(container.querySelector('img')).not.toBeNull())
    const img = container.querySelector('img')!
    fireEvent.error(img)
    await waitFor(() => expect(seen).toHaveLength(1))
    expect(seen[0]).toBe(img)
  })
})

describe('what the signer never asks every second', () => {
  function Probe({ url = RAW }: { url?: string }) {
    return <span>{useSignedUrl(url) ?? ''}</span>
  }

  it('a re-sign that fails past the 80% mark is asked again with the growing wait', async () => {
    vi.useFakeTimers()
    try {
      // A whole second, so the re-sign falls exactly at 80 s and each wait is measured from it.
      vi.setSystemTime(1_800_000_000_000)
      window.localStorage.setItem('pc_pair', A)
      fetchSpy.mockImplementationOnce(async (_u: string, init: RequestInit) => signedRows(JSON.parse(String(init.body)).urls, 1_800_000_100))
      fetchSpy.mockResolvedValue(json(503, { error: { code: 'unavailable', message: 'no' } }))
      render(<Probe />)
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      await act(async () => { await vi.advanceTimersByTimeAsync(80_500) })
      expect(mintCalls()).toHaveLength(2)
      await act(async () => { await vi.advanceTimersByTimeAsync(1_000) })
      expect(mintCalls()).toHaveLength(3)
      await act(async () => { await vi.advanceTimersByTimeAsync(1_000) })
      expect(mintCalls()).toHaveLength(3)
      await act(async () => { await vi.advanceTimersByTimeAsync(1_000) })
      expect(mintCalls()).toHaveLength(4)
    } finally {
      vi.useRealTimers()
    }
  })

  it('a URL the gateway declines in its answer is used as it is and never asked again', async () => {
    vi.useFakeTimers()
    try {
      window.localStorage.setItem('pc_pair', A)
      fetchSpy.mockResolvedValue(json(200, { urls: [{ url: RAW, error: 'not_signable' }] }))
      render(<Probe />)
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
      expect(mintCalls()).toHaveLength(1)
      expect(await signUrl(RAW)).toBe(RAW)
      expect(mintCalls()).toHaveLength(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('two elements on one URL re-sign it in one request, and the whole-second end moves on', async () => {
    vi.useFakeTimers()
    try {
      window.localStorage.setItem('pc_pair', A)
      let n = 0
      fetchSpy.mockImplementation(async (_u: string, init: RequestInit) => {
        n += 1
        return signedRows(JSON.parse(String(init.body)).urls, Math.floor(Date.now() / 1000) + 100)
      })
      render(<><Probe /><Probe /></>)
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(n).toBe(1)
      await act(async () => { await vi.advanceTimersByTimeAsync(81_000) })
      expect(n).toBe(2)
      await act(async () => { await vi.advanceTimersByTimeAsync(81_000) })
      expect(n).toBe(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('a forced re-sign in the same second reads as the end, and past that end a new mint is asked', async () => {
    vi.useFakeTimers()
    try {
      // The gateway's end is whole seconds and its signing is the same for the same second, so a
      // load that fails right away gets the same end back.
      vi.setSystemTime(1_800_000_000_000)
      window.localStorage.setItem('pc_pair', A)
      let n = 0
      fetchSpy.mockImplementation(async (_u: string, init: RequestInit) => {
        n += 1
        const exp = Math.floor(Date.now() / 1000) + 100
        return json(200, { urls: (JSON.parse(String(init.body)).urls as string[]).map((url) => ({ url, signed: `${url}&pc_exp=${exp}&pc_sig=s`, exp })) })
      })
      const heard = vi.fn()
      const { container } = render(<SignedImg src={RAW} alt="" onError={heard} />)
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      fireEvent.error(container.querySelector('img')!)
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(n).toBe(2)
      expect(heard).toHaveBeenCalledTimes(1)
      await act(async () => { await vi.advanceTimersByTimeAsync(99_000) })
      expect(n).toBe(2)
      await act(async () => { await vi.advanceTimersByTimeAsync(1_500) })
      expect(n).toBe(3)
      expect(await signUrl(RAW)).toContain('pc_exp=1800000200')
      expect(container.querySelector('img')!.getAttribute('src')).toContain('pc_exp=1800000200')
    } finally {
      vi.useRealTimers()
    }
  })

  it('a failed forced re-sign while the signature is fresh waits for the 80% mark, not every second', async () => {
    vi.useFakeTimers()
    try {
      vi.setSystemTime(1_800_000_000_000)
      window.localStorage.setItem('pc_pair', A)
      fetchSpy.mockImplementationOnce(async (_u: string, init: RequestInit) => signedRows(JSON.parse(String(init.body)).urls, 1_800_000_100))
      fetchSpy.mockResolvedValueOnce(json(503, { error: { code: 'unavailable', message: 'no' } }))
      fetchSpy.mockImplementation(async (_u: string, init: RequestInit) => signedRows(JSON.parse(String(init.body)).urls, 1_800_000_200))
      const sign = vi.fn(signUrl)
      const img = document.createElement('img')
      const stop = bindSignedSource(img, RAW, sign)
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      img.dispatchEvent(new Event('error'))
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(mintCalls()).toHaveLength(2)
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
      expect(sign).toHaveBeenCalledTimes(2)
      await act(async () => { await vi.advanceTimersByTimeAsync(70_500) })
      expect(sign).toHaveBeenCalledTimes(3)
      expect(mintCalls()).toHaveLength(3)
      stop()
    } finally {
      vi.useRealTimers()
    }
  })

  it('an answer that is not the route’s shape signs nothing, rejects no one, and counts as failed', async () => {
    vi.useFakeTimers()
    try {
      vi.setSystemTime(1_800_000_000_000)
      window.localStorage.setItem('pc_pair', A)
      fetchSpy.mockResolvedValueOnce(json(200, { urls: 5 }))
      fetchSpy.mockResolvedValueOnce(json(200, { urls: [null] }))
      fetchSpy.mockResolvedValueOnce(json(200, null))
      fetchSpy.mockImplementation(async (_u: string, init: RequestInit) => signedRows(JSON.parse(String(init.body)).urls))
      await expect(signUrl(RAW)).resolves.toBe(RAW)
      render(<Probe />)
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(mintCalls()).toHaveLength(2)
      await act(async () => { await vi.advanceTimersByTimeAsync(2_000) })
      expect(mintCalls()).toHaveLength(3)
      await act(async () => { await vi.advanceTimersByTimeAsync(4_000) })
      expect(mintCalls()).toHaveLength(4)
      expect(screen.getByText(/pc_sig=s/)).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })
})
