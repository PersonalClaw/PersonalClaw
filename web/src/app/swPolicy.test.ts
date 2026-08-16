import { describe, expect, it } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  APP_SHELL,
  CACHEABLE_PREFIXES,
  SHELL_DOCUMENT,
  isApiPath,
  mayCache,
  strategyFor,
} from './swPolicy'

// ── The service worker's caching policy (MOBILE-COMPANION T3.1 / §C2) ────────
//
// These are the FAST rails on the never-cache rule. They are not the proof — a
// test asserting `/api` is absent from a list cannot show that a browser refuses
// to serve an API response from disk. That proof is behavioural and lives in
// `web/e2e/pwa.spec.ts`, which installs the real worker, kills the server, and
// asserts the shell still loads offline while `/api` fails.
//
// What these rails DO buy: they fail in milliseconds on every `npm test`, and the
// last block below asserts the CALL SITES in sw.ts rather than the policy's
// return values — a correct predicate that nothing consults is worth nothing.

const WEB_DIR = join(__dirname, '..', '..')
const ORIGIN = 'http://localhost:10000'
const url = (path: string, origin = ORIGIN) => new URL(path, origin)

describe('APP_SHELL — the precache list', () => {
  it('precaches the app shell ONLY: no /api entry', () => {
    for (const entry of APP_SHELL) expect(isApiPath(entry)).toBe(false)
  })

  it('holds nothing user-specific', () => {
    // Anything keyed by a user, session, entity or device would be the same leak
    // as caching /api, just written by hand.
    const userScoped = /session|approval|inbox|entity|device|user|token|credential|file/i
    for (const entry of APP_SHELL) expect(entry).not.toMatch(userScoped)
  })

  it('is exactly the documented shell — every entry is real build input', () => {
    // `install` uses cache.addAll, which is atomic: ONE missing entry fails the
    // whole registration and the app silently loses offline support. A typo here
    // must fail in CI, not on a phone.
    expect(APP_SHELL).toContain(SHELL_DOCUMENT)
    for (const entry of APP_SHELL) {
      const source =
        entry === SHELL_DOCUMENT
          ? join(WEB_DIR, 'index.html') // '/' is served from the built index.html
          : join(WEB_DIR, 'public', entry)
      expect(existsSync(source), `${entry} has no source file at ${source}`).toBe(true)
    }
  })

  it('declares the same icons the manifest does', () => {
    const manifest = JSON.parse(
      readFileSync(join(WEB_DIR, 'public', 'manifest.webmanifest'), 'utf8'),
    ) as { icons: { src: string }[] }
    // An icon the manifest names but the shell does not precache is an icon the
    // installed app cannot draw offline.
    for (const icon of manifest.icons) expect(APP_SHELL).toContain(icon.src)
  })
})

describe('isApiPath', () => {
  it('matches the API namespace including the bare prefix', () => {
    expect(isApiPath('/api')).toBe(true)
    expect(isApiPath('/api/approvals')).toBe(true)
    expect(isApiPath('/api/ws/terminal/x')).toBe(true)
  })

  it('does not over-match a path that merely starts with the letters', () => {
    expect(isApiPath('/apidocs')).toBe(false)
    expect(isApiPath('/assets/api-DEADBEEF.js')).toBe(false)
  })
})

describe('mayCache — the one gate before any cache read or write', () => {
  it('REFUSES every /api path', () => {
    for (const path of [
      '/api',
      '/api/approvals',
      '/api/dashboard/config',
      '/api/sessions/abc',
      '/api/files?path=/etc/passwd',
    ]) {
      expect(mayCache(url(path), ORIGIN), path).toBe(false)
    }
  })

  it('refuses cross-origin responses', () => {
    expect(mayCache(url('/assets/index.js', 'https://cdn.example.com'), ORIGIN)).toBe(false)
  })

  it('refuses an unrecognised same-origin path — the default is fail-closed', () => {
    // The point of this case: a route added to the gateway tomorrow is NOT cached
    // just because nobody remembered to deny-list it.
    expect(mayCache(url('/some/future/route'), ORIGIN)).toBe(false)
    expect(mayCache(url('/login'), ORIGIN)).toBe(false)
    expect(mayCache(url('/mcp'), ORIGIN)).toBe(false)
  })

  it('allows the shell and the immutable build-output prefixes', () => {
    for (const entry of APP_SHELL) expect(mayCache(url(entry), ORIGIN), entry).toBe(true)
    for (const prefix of CACHEABLE_PREFIXES) {
      expect(mayCache(url(`${prefix}anything-HASH.js`), ORIGIN), prefix).toBe(true)
    }
  })
})

describe('strategyFor', () => {
  it('gives every /api request network-only — never read, never written', () => {
    for (const path of ['/api/approvals', '/api/status', '/api/inbox']) {
      expect(strategyFor(url(path), ORIGIN, false), path).toBe('network-only')
    }
  })

  it('never caches an /api request even when the browser calls it a navigation', () => {
    // A navigation is network-first, so it MAY fall back to the cached shell. That
    // fallback is keyed on SHELL_DOCUMENT, and the write is gated by mayCache —
    // so even here nothing under /api can be stored under its own key.
    expect(mayCache(url('/api/approvals'), ORIGIN)).toBe(false)
  })

  it('serves navigations network-first so a deploy cannot be pinned', () => {
    expect(strategyFor(url('/'), ORIGIN, true)).toBe('network-first')
    expect(strategyFor(url('/#/companion'), ORIGIN, true)).toBe('network-first')
  })

  it('serves content-hashed assets cache-first', () => {
    expect(strategyFor(url('/assets/index-DR9ii6_w.js'), ORIGIN, false)).toBe('cache-first')
    expect(strategyFor(url('/icons/icon-192.png'), ORIGIN, false)).toBe('cache-first')
  })

  it('falls through to network-only for anything the gate rejects', () => {
    expect(strategyFor(url('/some/future/route'), ORIGIN, false)).toBe('network-only')
  })
})

describe('sw.ts consults the policy at every cache site', () => {
  // A correct predicate nobody calls proves nothing. These assertions read the
  // worker's SOURCE, because that is where a future edit would bypass the gate.
  //
  // COMMENTS ARE STRIPPED FIRST. sw.ts documents at length why it does NOT call
  // skipWaiting(), so a raw-text scan for `skipWaiting()` matches the prose and
  // the rail reports a violation that does not exist — measuring the explanation
  // instead of the code.
  const raw = readFileSync(join(WEB_DIR, 'src', 'sw.ts'), 'utf8')
  const source = raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('the comment stripper left real code behind', () => {
    // Vacuity floor: every `not.toMatch` below passes trivially against an empty
    // string, so prove the stripper kept the code and dropped only the prose.
    expect(source).toContain("addEventListener('fetch'")
    expect(raw).toContain('NO `skipWaiting()`') // the prose the raw scan tripped on
    expect(source).not.toContain('NO `skipWaiting()`')
  })

  it('writes to a cache in exactly one place, and that place calls mayCache', () => {
    const puts = source.match(/\.put\(/g) ?? []
    expect(puts, 'a second cache.put() call site would bypass the gate').toHaveLength(1)
    const store = source.slice(source.indexOf('async function store'), source.indexOf('.put('))
    expect(store).toContain('mayCache(')
  })

  it('keys the offline navigation fallback on the shell, not the requested URL', () => {
    expect(source).toContain('match(SHELL_DOCUMENT)')
  })

  it('holds the documented update strategy: no skipWaiting, no clients.claim', () => {
    // Deliberate (see the header comment in sw.ts): navigations are already
    // network-first, and claiming clients while purging the old cache would swap
    // the asset cache out from under a live tab mid-lazy-import.
    expect(source).not.toMatch(/skipWaiting\(\)/)
    expect(source).not.toMatch(/clients\.claim\(\)/)
  })

  it('leaves non-GET requests entirely alone', () => {
    expect(source).toContain("request.method !== 'GET'")
  })
})
