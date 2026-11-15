/** Download saves the file and stays on the page.
 *
 *  The gateway serves an uploaded page or image as itself (`text/html`, `image/svg+xml`), so opened in
 *  a tab it is drawn as a page on the dashboard's own origin. Download saves through an anchor with
 *  `download` instead, never `window.open`, and signs the URL first where sign-ins are paired, since
 *  the browser fetches a link without the sign-in's pair. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { row } from '../../test/libraryHarness'

const signing = vi.hoisted(() => ({ on: false }))
vi.mock('../../lib/signedUrl', async (orig) => ({
  ...(await orig<typeof import('../../lib/signedUrl')>()),
  signs: () => signing.on,
  signUrl: (url: string) => Promise.resolve(`${url}?sig=s1`),
}))
const { moreRows } = await import('./libraryActions')

const inputs = {
  navigate: vi.fn(), writeUps: new Map<string, string>(), projects: [], chatTitle: () => '',
  pickChat: vi.fn(), pickProject: vi.fn(),
}

let saved: { href: string; download: string | null }[] = []
let opened: unknown[][] = []
beforeEach(() => {
  signing.on = false
  saved = []; opened = []
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    saved.push({ href: this.getAttribute('href') ?? '', download: this.getAttribute('download') })
  })
  vi.spyOn(window, 'open').mockImplementation((...a: unknown[]) => { opened.push(a); return null })
})
afterEach(() => { vi.restoreAllMocks() })

const download = (item: ReturnType<typeof row>) => moreRows(item, inputs).find((r) => r.label === 'Download')!.onSelect()

describe('Download saves the file in place', () => {
  it.each([
    ['a saved web page', row('knowledge:k1', 'Saved page', { kind: 'document', format: 'html' }), '/api/knowledge/items/k1/file'],
    ['an image', row('knowledge:k2', 'Diagram', { kind: 'image', format: 'svg' }), '/api/knowledge/items/k2/file'],
    ['an artifact', row('artifact:lisbon', 'Lisbon trip plan'), '/api/artifacts/lisbon/raw'],
  ])('🔑 %s is saved through an anchor with download, and no tab opens', async (_, item, url) => {
    download(item)
    await vi.waitFor(() => expect(saved).toHaveLength(1))
    expect(opened, 'nothing opens it as a page').toEqual([])
    expect(saved[0]).toEqual({ href: url, download: '' })
  })

  it('🔑 where sign-ins are paired, it saves at the signed URL', async () => {
    signing.on = true
    download(row('knowledge:k1', 'Saved page', { kind: 'document', format: 'html' }))
    await vi.waitFor(() => expect(saved).toHaveLength(1))
    expect(saved[0].href).toBe('/api/knowledge/items/k1/file?sig=s1')
    expect(opened).toEqual([])
  })
})
