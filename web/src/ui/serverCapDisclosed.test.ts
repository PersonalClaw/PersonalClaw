import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { PartialCount } from './MoreRow'

// ── A cap the SERVER applied must reach the screen that shows the remainder ───────────────────────
//
// `cappedListDisclosed.test.tsx` next door owns the other half of this class: a `.slice(0, N)` the
// CALLER wrote, under a label stating the full count, disclosed with `MoreRow`. This file owns the
// half that primitive cannot see — the payload arrives already short, and the true total arrives
// beside it in the same response.
//
// 🔴 MEASURED ON THIS BRANCH POINT: the gateway declares a truncation on TEN routes, and three of
// them reached a surface that said nothing at all.
//
//   /api/knowledge/graph        `thinning.edges_total 1540 / edges_kept 768` — the Knowledge header
//                              chip read "relations 1540", the canvas drew 768 lines, and its only
//                              caption was "100%". Issue 808. (The node cap the issue reports was
//                              already gone: 154 entities shipped, 154 drew.)
//   /api/file-content-search    `truncated: true` at the 500-match cap, summarised as "500 matches".
//                              The sharpest of the three: a capped search looks like a finished one.
//   /api/durability/conflicts   `truncated: true` at the 50-row cap, on rows the panel is asking the
//                              user to decide about.
//
// The other seven were already honest (`AuditPanel`, `WeekGridView`, `CodeCockpitPage`, `DiffView`)
// or are unreachable from any web surface — see `BY_DESIGN` below, which is verified rather than
// asserted.
//
// 🪤 THE CENSUS IS DERIVED FROM THE GATEWAY, NOT FROM A LIST OF SURFACES. It reads the route table,
// walks each handler (plus the same-module helpers it calls, which is where `_graph_payload_shell`
// hides `edges_total`), and only then asks the frontend whether anyone reads the flag. A new capped
// endpoint therefore joins this rail the moment it lands, without anyone remembering to add it.

const SRC = join(process.cwd(), 'src')
const GATEWAY = join(process.cwd(), '..', 'src', 'personalclaw', 'dashboard')

const walk = (d: string, re: RegExp): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p, re)
    return re.test(n) ? [p] : []
  })

/** JSON keys by which the gateway says "this answer is not all of it". Deliberately closed: `total`
 *  and `count` are honest counts on dozens of complete payloads, and a vocabulary that matched them
 *  would drown the census it exists to keep sharp. */
const SIGNAL = /"(truncated|has_more|edges_total|edges_kept)"\s*:/g

const ROUTE = /add_(?:get|post|put|patch|delete)\(\s*"([^"]+)"\s*,\s*([\w.]+)/g

/** name → source of every `def`/`async def` in the gateway package, so a handler can be read
 *  together with the helpers it delegates its payload shape to. */
function gatewayFunctions() {
  const out = new Map<string, string>()
  for (const f of walk(GATEWAY, /\.py$/)) {
    let name: string | null = null
    let buf: string[] = []
    for (const line of readFileSync(f, 'utf8').split('\n')) {
      const m = line.match(/^(?:async )?def (\w+)\(/)
      if (m) {
        if (name) out.set(name, (out.get(name) ?? '') + buf.join('\n'))
        name = m[1]
        buf = [line]
      } else if (name) buf.push(line)
    }
    if (name) out.set(name, (out.get(name) ?? '') + buf.join('\n'))
  }
  return out
}

interface Capped { path: string; handler: string; keys: string[] }

/** Every route whose handler (or a helper it calls) puts a truncation key on the wire. */
function cappedRoutes(): { routes: number; capped: Capped[] } {
  const fns = gatewayFunctions()
  const seen = new Map<string, Capped>()
  let routes = 0
  for (const f of walk(GATEWAY, /\.py$/)) {
    for (const m of readFileSync(f, 'utf8').matchAll(ROUTE)) {
      routes++
      const path = m[1]
      const handler = m[2].split('.').pop() as string
      const own = fns.get(handler)
      if (!own) continue
      // One hop into module-private helpers: `get_full_graph` never writes `edges_total` itself,
      // `_graph_payload_shell` does. A census that read only the handler would have missed 808.
      const helpers = [...new Set([...own.matchAll(/\b(_[a-z]\w*)\(/g)].map((h) => h[1]))]
      const text = own + '\n' + helpers.map((h) => fns.get(h) ?? '').join('\n')
      const keys = [...new Set([...text.matchAll(SIGNAL)].map((s) => s[1]))]
      if (keys.length) seen.set(`${path} ${handler}`, { path, handler, keys })
    }
  }
  return { routes, capped: [...seen.values()].sort((a, b) => a.path.localeCompare(b.path)) }
}

const webFiles = new Map(
  walk(SRC, /\.tsx?$/)
    .filter((p) => !/\.(test|doc)\.tsx?$/.test(p))
    .map((p) => [p.replace(SRC + '/', ''), readFileSync(p, 'utf8')] as const),
)
const API = 'lib/api.ts'
const apiLines = (webFiles.get(API) ?? '').split('\n')

/** The route's path as the frontend spells it: `{param}` becomes a template hole, and the match must
 *  end at a real boundary — a quote, a query, or an interpolation (`/api/durability/conflicts${q…}`).
 *  🪤 THE BOUNDARY IS THE WHOLE POINT: without it `/api/sessions` matches `/api/sessions/search`,
 *  and a route nobody fetches reads as fetched by the surface next door. */
function pathLiteral(path: string): RegExp {
  const body = path
    .split(/\{[^}]+\}/)
    .map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('\\$\\{[^}]*\\}')
  return new RegExp(body + `(?=['"\`?&$])`)
}

/** Who fetches it, and who reads what they fetched. `lib/api.ts` is the typing seam, not a surface:
 *  a flag declared there and dropped by every caller is exactly the defect, so the api file is
 *  resolved to its METHOD and the method to its callers. */
function consumersOf(path: string): { fetched: string[]; consumers: string[] } {
  const re = pathLiteral(path)
  const fetched = [...webFiles].filter(([, t]) => re.test(t)).map(([f]) => f)
  const methods = new Set<string>()
  if (fetched.includes(API)) {
    apiLines.forEach((line, i) => {
      if (!re.test(line)) return
      for (let j = i; j >= 0 && i - j < 16; j--) {
        const m = apiLines[j].match(/^ {2}(\w+):\s*\(/)
        if (m) { methods.add(m[1]); return }
      }
    })
  }
  const callers = [...webFiles]
    .filter(([f, t]) => f !== API && [...methods].some((m) => t.includes(`.${m}(`)))
    .map(([f]) => f)
  return { fetched, consumers: [...new Set([...fetched.filter((f) => f !== API), ...callers])] }
}

const discloses = (file: string, keys: string[]) => {
  const t = webFiles.get(file) ?? ''
  return keys.some((k) => new RegExp(`\\b${k}\\b`).test(t)) || /PartialCount|MoreRow/.test(t)
}

/** The signals no web surface can act on, each with the reason — and each reason is CHECKED below,
 *  so an exclusion cannot outlive the fact that earned it. */
const BY_DESIGN: Record<string, string> = {
  '/api/sessions':
    'no web surface fetches it — the session list the app actually uses is /api/sessions/search',
  '/api/chat/sessions/{session}/resume':
    'no web caller at all; the 200-message cap it reports is for API clients',
  '/api/chat/sessions/{session}':
    'the transcript fetch sends neither limit nor before, and with neither the handler returns the WHOLE chained history with has_more hard-coded false — the flag is unreachable from this client',
}

describe('PartialCount', () => {
  it('states the scope when the view is partial', () => {
    render(PartialCount({ shown: 768, of: 1540, noun: 'relations' }) as never)
    expect(screen.getByText(`768 of ${(1540).toLocaleString()} relations`)).toBeTruthy()
  })

  it('drops the caveat, not the count, when the view is complete', () => {
    const { container } = render(PartialCount({ shown: 12, of: 12, noun: 'relations' }) as never)
    expect(container.textContent, 'a complete view still says how much it holds').toBe('12 relations')
    expect(container.textContent).not.toContain('of')
  })

  it('never invents a denominator for a cap that stopped counting', () => {
    const { container } = render(PartialCount({ shown: 500, of: 'more', noun: 'matches' }) as never)
    expect(container.textContent).toBe('first 500 matches')
  })

  it('agrees with the number the noun belongs to', () => {
    expect(render(PartialCount({ shown: 1, of: 1, noun: 'entities', singular: 'entity' }) as never)
      .container.textContent).toBe('1 entity')
    // The total governs when there IS one: "1 of 40 relations", never "1 of 40 relation".
    expect(render(PartialCount({ shown: 1, of: 40, noun: 'relations', singular: 'relation' }) as never)
      .container.textContent).toBe('1 of 40 relations')
  })

  it('is rendered, not read out of the source — the sentence lives in one file', () => {
    const owners = [...webFiles].filter(([f, t]) => f !== 'ui/MoreRow.tsx' && /\bof \$\{[^}]*\} (relations|matches|conflicts)/.test(t))
    expect(owners.map(([f]) => f), 'a second site spelling "N of M" is how MoreRow became three wordings').toEqual([])
  })
})

describe('every truncation the gateway declares reaches a surface that says so', () => {
  const { routes, capped } = cappedRoutes()

  it('the sweep reads the real route table', () => {
    expect(routes, 'the gateway registers hundreds of routes').toBeGreaterThan(400)
    expect(capped.length, 'and a handful of them declare a truncation').toBeGreaterThanOrEqual(8)
    expect(capped.map((c) => c.path), 'the three fixed here must be found by the sweep, not assumed')
      .toEqual(expect.arrayContaining(['/api/knowledge/graph', '/api/file-content-search', '/api/durability/conflicts']))
  })

  it('finds the helper-declared signal a handler-only scan would miss', () => {
    const graph = capped.find((c) => c.path === '/api/knowledge/graph')
    expect(graph?.keys.sort(), 'edges_total/edges_kept live in _graph_payload_shell, not in the handler')
      .toEqual(['edges_kept', 'edges_total'])
  })

  it('none of them is silent on the client', () => {
    const silent: string[] = []
    for (const c of capped) {
      if (c.path in BY_DESIGN) continue
      const { consumers } = consumersOf(c.path)
      if (!consumers.some((f) => discloses(f, c.keys))) silent.push(`${c.path} (${c.keys}) → ${consumers.join(', ') || 'no consumer'}`)
    }
    expect(silent, 'a payload that says it is partial must reach a screen that says it too').toEqual([])
  })

  it('the excluded routes really are unreachable — the reasons are checked, not trusted', () => {
    for (const path of ['/api/sessions', '/api/chat/sessions/{session}/resume']) {
      expect(consumersOf(path).fetched, `${path}: ${BY_DESIGN[path]}`).toEqual([])
    }
    // The transcript route IS fetched, so its exclusion rests on the pair of facts below. Either
    // side changing (a paged fetch, or a default cap) puts it back in the census.
    const detail = consumersOf('/api/chat/sessions/{session}')
    expect(detail.fetched, 'the transcript route is fetched').toContain(API)
    const line = apiLines.find((l) => /chatSessionDetail:/.test(l)) ?? ''
    expect(line, 'and it asks for no page').not.toMatch(/limit=|before=/)
    const chat = readFileSync(join(GATEWAY, 'chat_handlers.py'), 'utf8')
    expect(chat, 'while the handler caps only when asked').toContain('if limit_raw is None and before is None:')
  })

  it('every exclusion is a route the sweep actually found', () => {
    const found = new Set(capped.map((c) => c.path))
    const stale = Object.keys(BY_DESIGN).filter((p) => !found.has(p))
    expect(stale, 'an exclusion for a route that no longer declares a truncation is dead weight').toEqual([])
  })
})

describe('the three surfaces fixed for issue 808', () => {
  it('the entity canvas counts what it DREW against the payload total', () => {
    const src = webFiles.get('pages/knowledge/KnowledgeGraph.tsx') ?? ''
    // `graph.edges.length` and not `thinning.edges_kept`: the caption has to describe the render,
    // so a later filter cannot leave it stating the server's number for a picture that changed.
    expect(src).toMatch(/<PartialCount shown=\{graph\.edges\.length\} of=\{graph\.thinning\?\.edges_total \?\? graph\.edges\.length\}/)
    expect(src, 'entities are complete, and the count says so from the same render').toMatch(
      /<PartialCount shown=\{graph\.nodes\.length\} of=\{graph\.nodes\.length\}/,
    )
    expect(src, 'and the caption points at where the rest is reachable').toMatch(/Click an entity to see all of its relations/)
  })

  it('the file search says "first N" rather than a total it never counted', () => {
    const src = webFiles.get('pages/files/FilesSection.tsx') ?? ''
    expect(src).toMatch(/setSearchCapped\(!!r\.truncated\)/)
    expect(src).toMatch(/of=\{searchCapped \? 'more' : results\.length\}/)
    // The remedy, gated on the flag — pinned separately, because a mutant that stubs the gate to
    // `false` leaves the `of=` expression above untouched and would otherwise ship green.
    expect(src).toMatch(/\{searchCapped && !searchBusy && <span>· narrow the search to see the rest<\/span>\}/)
    expect(src, 'the old inline count is gone').not.toMatch(/\$\{results\.length\} match\$\{/)
    // The live region hears the caveat too — otherwise the screen-reader user is the only one still
    // told the search finished. Its own doc's rule ("the announced noun must be the visible noun").
    expect(src).toMatch(/<ResultAnnouncement count=\{results\.length\}[^/]*partial=\{searchCapped\}/)
  })

  it('the conflict queue names the rows it is holding back', () => {
    const src = webFiles.get('pages/settings/DurabilityPanel.tsx') ?? ''
    expect(src).toMatch(/const \{ conflicts, counts, sync, truncated \} = read\.value/)
    expect(src).toMatch(/<PartialCount shown=\{conflicts\.length\} of=\{counts\.selected\}/)
    // Same reason as the search pin above: the gate is the thing a mutation can quietly disarm.
    expect(src).toMatch(/\{truncated && \(/)
    expect(src, 'and it says what to do about it').toMatch(/the most recent are kept\. Resolve some to see the rest\./)
  })
})
