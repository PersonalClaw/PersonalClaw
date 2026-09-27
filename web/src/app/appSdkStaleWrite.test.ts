// @vitest-environment jsdom
/**
 * An app's own page saves a whole document the way core's pages do: it names the revision of the
 * copy it read, and a save built on a copy that changed since is refused, not written over it.
 *
 * #3690 put every whole-document write behind `If-Match` (`personalclaw/stale_write.py`), and
 * core's pages send it with `basedOn(revision)` (`lib/staleWrite.ts`). The app SDK's client had no
 * way to send it: `put`/`patch`/`post` took a path and a body and nothing else, so an app page
 * saving its own settings (`PUT /api/apps/<name>/config`) got `428 revision_required` every time,
 * whatever it had read. And the refusal it did get read "[object Object]": the client took
 * `body.error` as the message, which is an OBJECT in the platform envelope.
 *
 * The fake gateway below refuses exactly the way `stale_write_refusal` does — no `If-Match` is 428,
 * a revision other than the stored one is 409 — so each test is a real round trip through the
 * client an app bundle imports, two copies of the app's page sharing one store.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createAppApi, installAppSdk, type AppContext } from './appSdk'

const APP: AppContext = { name: 'vector-store-qdrant', permissions: { api: ['/api/apps/vector-store-qdrant'] } }
const CONFIG = '/api/apps/vector-store-qdrant/config'

/** The gateway's half: a stored document, its revision, and the two refusals. */
function fakeGateway() {
  let config: Record<string, unknown> = { url: 'http://localhost:6333', collection: 'notes' }
  let version = 1
  const revision = () => `rev-${version}`
  const seen: { method: string; headers: Record<string, string> }[] = []
  const envelope = (status: number, code: string, message: string) =>
    new Response(JSON.stringify({ error: { code, message } }), { status, headers: { 'Content-Type': 'application/json' } })
  const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    const headers = Object.fromEntries(Object.entries((init?.headers ?? {}) as Record<string, string>))
    if (url === `/api/apps/${APP.name}/token`) return json({ token: 'app-token', expires_in: 3600 })
    seen.push({ method, headers })
    if (url !== CONFIG) return new Response('not found', { status: 404 })
    if (method === 'GET') return json({ name: APP.name, config, revision: revision() })
    const claimed = (headers['If-Match'] ?? '').replace(/^W\//, '').replace(/"/g, '').trim()
    if (!claimed) {
      return envelope(428, 'revision_required',
        "This write is checked against the copy of the settings of 'vector-store-qdrant' it was built from, so it must name that copy: read the settings of 'vector-store-qdrant' and send its revision in If-Match.")
    }
    if (claimed !== revision()) {
      return envelope(409, 'stale_write',
        "This write replaces the settings of 'vector-store-qdrant', which changed after the copy it was built from was read — saving it would have undone that change. Nothing was saved: read it again, re-apply the edit, and save.")
    }
    config = JSON.parse(String(init?.body))
    version += 1
    return json({ ok: true, name: APP.name, config, revision: revision() })
  })
  return { fetch, seen, stored: () => config }
}

type Read = { config: Record<string, unknown>; revision: string }

let gateway: ReturnType<typeof fakeGateway>
const realFetch = globalThis.fetch
beforeEach(() => {
  gateway = fakeGateway()
  globalThis.fetch = gateway.fetch as unknown as typeof fetch
})
afterEach(() => { globalThis.fetch = realFetch })

describe("an app page saves its settings over the copy it read", () => {
  it('names the revision it read, and the save lands', async () => {
    const api = createAppApi(APP)
    const read = await api.get<Read>(CONFIG)
    const saved = await api.put<Read>(CONFIG, { ...read.config, collection: 'journal' }, { basedOn: read.revision })
    expect(saved.config.collection).toBe('journal')
    expect(gateway.stored().collection).toBe('journal')
    // The precondition rides beside the app's own identity, not instead of it.
    const put = gateway.seen.find((r) => r.method === 'PUT')!
    expect(put.headers['If-Match']).toBe(`"${read.revision}"`)
    expect(put.headers.Authorization).toBe('Bearer app-token')
  })

  it('a save from a copy that changed since is refused with 409, and the page can tell', async () => {
    // Two copies of the app's page, both opened on the same settings.
    const first = createAppApi(APP)
    const second = createAppApi(APP)
    const a = await first.get<Read>(CONFIG)
    const b = await second.get<Read>(CONFIG)
    await second.put(CONFIG, { ...b.config, collection: 'journal' }, { basedOn: b.revision })

    const refused = await first.put(CONFIG, { ...a.config, url: 'http://qdrant:6333' }, { basedOn: a.revision })
      .then(() => null, (e: unknown) => e)
    expect(refused).toBeInstanceOf(Error)
    expect((refused as { status?: number }).status).toBe(409)
    expect((refused as { code?: string }).code).toBe('stale_write')
    // The sentence the gateway wrote, not "[object Object]".
    expect((refused as Error).message).toMatch(/^This write replaces the settings of 'vector-store-qdrant', which changed/)
    // The other page's save survived.
    expect(gateway.stored()).toEqual({ url: 'http://localhost:6333', collection: 'journal' })

    // The SDK hands an app the same predicate core's pages branch on.
    installAppSdk()
    const sdk = (window as unknown as { __personalclaw_modules: Record<string, Record<string, unknown>> })
      .__personalclaw_modules['@personalclaw/app-sdk']
    const isStaleWrite = sdk.isStaleWrite as (e: unknown) => boolean
    expect(typeof isStaleWrite).toBe('function')
    expect(isStaleWrite(refused)).toBe(true)
  })

  it('a save that names no revision is 428, in the gateway\'s words', async () => {
    const api = createAppApi(APP)
    const read = await api.get<Read>(CONFIG)
    const refused = await api.put(CONFIG, { ...read.config, collection: 'journal' }).then(() => null, (e: unknown) => e)
    expect((refused as { status?: number }).status).toBe(428)
    expect((refused as { code?: string }).code).toBe('revision_required')
    expect((refused as Error).message).toMatch(/must name that copy/)
    expect(gateway.stored().collection).toBe('notes')
  })

  it('patch and post carry the revision the same way', async () => {
    const api = createAppApi(APP)
    await api.patch(CONFIG, {}, { basedOn: 'rev-7' }).catch(() => {})
    await api.post(CONFIG, {}, { basedOn: 'rev-8' }).catch(() => {})
    const [patch, post] = gateway.seen.filter((r) => r.method !== 'GET')
    expect(patch.headers['If-Match']).toBe('"rev-7"')
    expect(post.headers['If-Match']).toBe('"rev-8"')
  })
})
