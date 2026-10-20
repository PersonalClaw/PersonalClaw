/**
 * A file the gateway refuses to put in a folder is answered with the gateway's own sentence.
 *
 * 🔴 Before: `api.fileUpload` handed the page `data.error` as it came. A coded refusal
 * (`{error: {code, message}}`, which the content scan and a refused filename answer) is an object
 * there, so the Files page read "Upload failed: [object Object]". It now reads the refusal through
 * the shared envelope reader, so a coded refusal and a plain one both arrive as their sentence.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

function gatewayAnswering(upload: Response) {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    calls.push(url)
    if (url === '/api/uploads/limits') return json({ limits: {}, single_post_threshold: 50 * 1024 * 1024 })
    return upload
  }))
  return calls
}

const shopping = () => new File(['Shopping list for Saturday'], 'shopping.txt', { type: 'text/plain' })

afterEach(() => { vi.unstubAllGlobals() })

describe('api.fileUpload', () => {
  it('says what the content scan said when it refuses the file', async () => {
    const calls = gatewayAnswering(json({
      error: { code: 'upload_content_refused', message: 'upload rejected: content failed the safety scan' },
    }, 422))

    const result = await api.fileUpload('/home/user/workspace', [shopping()])

    expect(result).toEqual({ ok: false, error: 'upload rejected: content failed the safety scan' })
    expect(calls).toContain('/api/file-upload?path=%2Fhome%2Fuser%2Fworkspace')
  })

  it('still says a plain refusal as it is', async () => {
    gatewayAnswering(json({ error: 'already exists: shopping.txt' }, 409))

    expect(await api.fileUpload('/home/user/workspace', [shopping()]))
      .toEqual({ ok: false, error: 'already exists: shopping.txt' })
  })

  it('hands back the stored paths when the upload is kept', async () => {
    gatewayAnswering(json({ ok: true, paths: ['/home/user/workspace/shopping.txt'] }))

    expect(await api.fileUpload('/home/user/workspace', [shopping()]))
      .toEqual({ ok: true, paths: ['/home/user/workspace/shopping.txt'] })
  })
})
