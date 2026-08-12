import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { errText } from './errText'

// ── What a user is told when something fails, and what must never be said ─────────────────
//
// `errText` is the ONE funnel for every API failure message — `api.ts`'s helpers, its
// hand-rolled fetches, and the chunked uploader. Since #1164 the failure line carries
// `role="alert"`, so whatever comes out of here is also read ALOUD.
//
// Driven on `#/settings/design` with the theme POST forced to 502, one body shape per run:
//
//                                  BEFORE                              AFTER
//   {"error": "name is required"}   "name is required"                  unchanged ✅
//   an nginx 502 HTML page          158 chars of raw markup   ❌         "HTTP 502"
//   an empty body                   "HTTP 502"                          unchanged ✅
//
// 🪤 THE FALSIFICATION THAT MADE THIS CYCLE HONEST. The ledger had "failure copy renders raw
// JSON" ranked first, on the strength of a probe that rendered `{"detail":"…"}` verbatim. That
// body was MY OWN INVENTION: this gateway answers `{"error": "..."}` (239 backend sites; only 6
// use `detail`), which `errText` already unwrapped correctly. I had found a defect in how the app
// handled a wire shape I made up. 🔑 **Before believing a probe's finding, check that the fixture
// matches what the real server sends.**
//
// What survived was the neighbouring case: a body that is not this gateway's JSON at all. A
// tunnel or reverse proxy that is up while the gateway is down answers with an HTML error page,
// and that page was passed through as the user-facing message.
//
// 🔑 AND THE HELPER EXISTED TWICE — `api.ts` and `chunkedUpload.ts` held byte-identical copies,
// so hardening one would have left every upload path behind. One owner now.

const res = (body: string, status = 502, ct = 'application/json') =>
  new Response(body, { status, headers: { 'Content-Type': ct } })

describe('errText', () => {
  it("uses the gateway's own message", async () => {
    expect(await errText(res('{"error": "name is required"}'))).toBe('name is required')
  })

  it("uses Starlette's `detail` too — a handful of routes still answer that way", async () => {
    expect(await errText(res('{"detail": "theme store is read-only"}'))).toBe('theme store is read-only')
  })

  it('never speaks markup: an HTML error page becomes the status', async () => {
    const html = '<!DOCTYPE html><html><head><title>502 Bad Gateway</title></head><body><center><h1>502 Bad Gateway</h1></center><hr><center>nginx/1.24.0</center></body></html>'
    expect(await errText(res(html, 502, 'text/html'))).toBe('HTTP 502')
  })

  it('never speaks a wall: a body past one line becomes the status', async () => {
    expect(await errText(res('x'.repeat(201), 500, 'text/plain'))).toBe('HTTP 500')
  })

  it('still passes a SHORT plain-text body through — some endpoints answer in text', async () => {
    expect(await errText(res('upload part 3 rejected', 400, 'text/plain'))).toBe('upload part 3 rejected')
  })

  it('falls back to the status for an empty body', async () => {
    expect(await errText(res('', 503))).toBe('HTTP 503')
  })

  it('does not render a non-string or blank message', async () => {
    // `{"error": {...}}` would otherwise reach the user as "[object Object]", and `{"error": " "}`
    // as a blank alert — an announcement with nothing in it.
    expect(await errText(res('{"error": {"code": 7}}'))).toBe('HTTP 502')
    expect(await errText(res('{"error": "   "}'))).toBe('HTTP 502')
  })

  it('trims, so an alert does not open with whitespace', async () => {
    expect(await errText(res('{"error": "  disk full  "}'))).toBe('disk full')
  })
})

describe('one owner', () => {
  const SRC = join(process.cwd(), 'src')
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
    })

  it('no file re-declares errText', () => {
    // It lived in two files, byte-identical, which is how a fix to the funnel misses the upload
    // paths. The consumers import it; nobody redefines it.
    const decls = walk(SRC)
      .filter((f) => /function errText\b|const errText\s*=/.test(readFileSync(f, 'utf8')))
      .map((f) => f.slice(SRC.length + 1))
    expect(decls, 'errText must have exactly one home').toEqual(['lib/errText.ts'])
  })

  it('both former copy-holders now import it', () => {
    for (const rel of ['lib/api.ts', 'lib/chunkedUpload.ts']) {
      expect(readFileSync(join(SRC, rel), 'utf8'), `${rel} must use the shared helper`)
        .toMatch(/import \{ errText \} from '\.\/errText'/)
    }
  })
})
