import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'

// ── Cancelling a knowledge upload stops it, and nothing is added ─────────────────────────────
//
// Measured on Knowledge › Add knowledge › Video, a 60 MB file over a throttled connection: at
// "Uploading… 14%" she pressed the form's Cancel. The form closed into the Library and nothing said
// an upload was still running; the parts went on, the upload was completed, and the file became an
// item with the form's title, processed to done, its frames sent to a model. The upload took no
// cancel at all, and Cancel only left the page.
//
// While a file uploads, the form says so and offers "Cancel upload". It, the form's Cancel, going
// back, and leaving the page all stop the upload: the part in flight is aborted, complete is never
// asked for, the gateway is told to drop what it holds, and no item is made. Once every byte is
// there the gateway finishes it whatever happens, so the form says it is adding it and offers no
// cancel it cannot honour.
//
// The whole chain is real (the form, the knowledge store, the API, the upload protocol); only the
// network is faked.

interface Call { method: string; url: string; signal: AbortSignal | null; body: unknown }

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

/** Files over 10 bytes take the resumable protocol in parts of 4 (a 12-byte file is 3 parts); smaller
 *  ones go in one request. `hold` names the request kept in flight until it is aborted. */
let net: { calls: Call[]; hold: string; release: () => void }

function stubNetwork(hold: string) {
  net = { calls: [], hold, release: () => {} }
  vi.stubGlobal('fetch', vi.fn((url: string, init: RequestInit = {}) => {
    const method = (init.method || 'GET').toUpperCase()
    net.calls.push({ method, url, signal: init.signal ?? null, body: init.body })
    const key = `${method} ${url}`
    if (key === net.hold) {
      return new Promise<Response>((resolve, reject) => {
        net.release = () => resolve(json(url.endsWith('/complete') ? { item_id: 'k-walkthrough', type: 'video', status: 'processing', deduped: false } : { item_id: 'k-small' }))
        init.signal?.addEventListener('abort', () => reject(new DOMException('The operation was aborted.', 'AbortError')))
      })
    }
    if (key === 'GET /api/uploads/limits') return Promise.resolve(json({ limits: { video: 2 ** 31, other: 2 ** 31 }, single_post_threshold: 10 }))
    if (key === 'GET /api/knowledge/tags') return Promise.resolve(json({ tags: [] }))
    if (key === 'POST /api/uploads/init') return Promise.resolve(json({ uploadId: 'u1', partSize: 4, totalParts: 3, category: 'video' }))
    if (method === 'PUT' && url.startsWith('/api/uploads/u1/part')) return Promise.resolve(json({ received: [] }))
    if (key === 'POST /api/uploads/u1/complete') return Promise.resolve(json({ item_id: 'k-walkthrough', type: 'video', status: 'processing', deduped: false }))
    if (key === 'DELETE /api/uploads/u1') return Promise.resolve(json({ uploadId: 'u1', dropped: true }))
    if (method === 'PATCH' && url.startsWith('/api/knowledge/items/')) return Promise.resolve(json({ ok: true, content_revision: 'r1' }))
    return Promise.resolve(json({ error: `unexpected ${key}` }, 500))
  }))
}

const calls = (method: string, prefix: string) => net.calls.filter((c) => c.method === method && c.url.startsWith(prefix))
const completes = () => calls('POST', '/api/uploads/u1/complete')
const drops = () => calls('DELETE', '/api/uploads/u1')
const walkthrough = () => new File(['0123456789ab'], 'release walkthrough.mov', { type: 'video/quicktime' })

const { KnowledgeCreatePage } = await import('./KnowledgeCreatePage')

const onBack = vi.fn()
const onCreated = vi.fn()

beforeEach(() => { onBack.mockReset(); onCreated.mockReset() })
afterEach(() => { vi.unstubAllGlobals() })

/** Open the Video form, choose *file*, press Add, and wait until *hold* is in flight. */
async function startUpload(file: File, hold: string) {
  stubNetwork(hold)
  const view = render(<KnowledgeCreatePage onBack={onBack} onCreated={onCreated} />)
  fireEvent.click(screen.getByRole('button', { name: 'Video' }))
  fireEvent.change(view.container.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [file] } })
  await waitFor(() => expect(screen.getByRole('button', { name: /^Add video/ })).not.toHaveAttribute('aria-disabled'))
  fireEvent.click(screen.getByRole('button', { name: /^Add video/ }))
  await waitFor(() => expect(net.calls.some((c) => `${c.method} ${c.url}` === hold)).toBe(true))
  return view
}

const heldRequest = () => net.calls.find((c) => `${c.method} ${c.url}` === net.hold)!

describe('while a file uploads, the form says so and offers to cancel it', () => {
  it('"Cancel upload" aborts the part in flight, never completes, drops it, and adds nothing', async () => {
    await startUpload(walkthrough(), 'PUT /api/uploads/u1/part?index=1')
    expect(screen.getByText(/^Uploading…/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel upload' }))

    await waitFor(() => expect(drops()).toHaveLength(1))
    expect(heldRequest().signal?.aborted).toBe(true)
    expect(calls('PUT', '/api/uploads/u1/part?index=2')).toEqual([])
    expect(completes()).toEqual([])
    expect(calls('PATCH', '/api/knowledge/items/')).toEqual([])
    expect(onCreated).not.toHaveBeenCalled()
    // She stays on the form with her file, free to add it again or choose another; a cancel she
    // asked for is not reported as a failure.
    await waitFor(() => expect(screen.queryByText(/^Uploading…/)).toBeNull())
    expect(screen.getByRole('button', { name: /^Add video/ })).not.toHaveAttribute('aria-busy')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('the form\'s Cancel stops the upload as it leaves', async () => {
    await startUpload(walkthrough(), 'PUT /api/uploads/u1/part?index=1')

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(onBack).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(drops()).toHaveLength(1))
    expect(heldRequest().signal?.aborted).toBe(true)
    expect(completes()).toEqual([])
  })

  it('going back to the types stops it too', async () => {
    await startUpload(walkthrough(), 'PUT /api/uploads/u1/part?index=1')

    fireEvent.click(screen.getByRole('button', { name: 'Back to types' }))

    await waitFor(() => expect(drops()).toHaveLength(1))
    expect(heldRequest().signal?.aborted).toBe(true)
    expect(completes()).toEqual([])
    expect(screen.getByRole('button', { name: 'Video' })).toBeInTheDocument()
  })

  it('leaving the page for another stops it, and does not pull her back later', async () => {
    const view = await startUpload(walkthrough(), 'PUT /api/uploads/u1/part?index=1')

    view.unmount()

    await waitFor(() => expect(drops()).toHaveLength(1))
    expect(heldRequest().signal?.aborted).toBe(true)
    expect(completes()).toEqual([])
    expect(onCreated).not.toHaveBeenCalled()
  })

  it('closing or reloading the tab asks first while it uploads, and not once it is cancelled', async () => {
    await startUpload(walkthrough(), 'PUT /api/uploads/u1/part?index=1')
    const leave = () => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented }

    // The guard is an effect, so it is armed and released just after the render that shows the
    // upload start or stop: each assertion waits for the guard itself, not for that render.
    await waitFor(() => expect(leave()).toBe(true))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel upload' }))
    await waitFor(() => expect(screen.queryByText(/^Uploading…/)).toBeNull())
    await waitFor(() => expect(leave()).toBe(false))
  })

  it('a file small enough for one request is cancelled the same way, and adds nothing', async () => {
    await startUpload(new File(['clip'], 'clip.mov', { type: 'video/quicktime' }), 'POST /api/knowledge/ingest')
    expect(screen.getByText(/^Uploading…/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel upload' }))

    await waitFor(() => expect(screen.queryByText(/^Uploading…/)).toBeNull())
    expect(heldRequest().signal?.aborted).toBe(true)
    expect(onCreated).not.toHaveBeenCalled()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('once every byte is there, it is added whatever happens', () => {
  it('the form says it is adding it, offers no cancel, and finishes with her title', async () => {
    await startUpload(walkthrough(), 'POST /api/uploads/u1/complete')

    // `finishing` is reported just before complete is sent, so its render may land a moment after.
    expect(await screen.findByText(/Adding it to your library/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Cancel upload' })).toBeNull()
    const cancel = screen.getByRole('button', { name: 'Cancel' })
    expect(cancel).toHaveAttribute('aria-disabled', 'true')
    expect(cancel.getAttribute('title')).toMatch(/being added to your library/)
    fireEvent.click(cancel)
    expect(onBack).not.toHaveBeenCalled()

    await act(async () => { net.release() })

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1))
    expect(drops()).toEqual([])
    const titled = calls('PATCH', '/api/knowledge/items/k-walkthrough')
    expect(titled).toHaveLength(1)
    expect(JSON.parse(String(titled[0].body))).toEqual({ title: 'release walkthrough.mov' })
  })
})
