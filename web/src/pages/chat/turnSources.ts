import { withoutFence } from '../../lib/untrustedFence'
import type { ChatTurn, Segment, TextSegment, ToolSegment } from './chatTypes'
import { coreToolName } from './coreToolName'

// ── A turn's sources (#3820) ───────────────────────────────────────────────────────────────────
//
// A source is a link the answer cites that the same turn retrieved: a result of one of its web
// searches, or a page one of its fetches read. A link the model wrote from memory is not a
// source, however sure it sounds: it stays a plain link in the text. Everything here is read from
// the turn's own recorded tool results, so showing the sources fetches nothing again (no preview,
// no favicon), and the title shown is the one the search or the fetch returned.

export interface TurnSource {
  /** The link as the answer cites it, which the row opens. */
  url: string
  /** The title the search or the fetch returned; the host when it returned none. */
  title: string
  host: string
  /** Its number in the answer, in the order the answer first cites it. */
  n: number
}

/** The calls whose results list links (a web search's hits), and the ones that read a page. The
 *  native agent's names and an agent CLI's own web tools. */
const SEARCH_TOOLS = new Set(['web_search', 'web', 'websearch'])
const FETCH_TOOLS = new Set(['web_fetch', 'web_extract', 'webfetch'])

/** Query parameters that say where a click came from, not what the page is. */
const TRACKING = /^(utm_.*|gclid|dclid|gbraid|wbraid|fbclid|msclkid|yclid|mc_cid|mc_eid|igshid|_ga|_gl|ref_src|ref_url)$/i

const URL_IN_TEXT = /\bhttps?:\/\/[^\s)<>"'`\]]+/g
const trimmed = (u: string) => u.replace(/[).,;:!?]+$/, '')

/** The key two spellings of one page share: scheme, host and path, with the query's tracking
 *  parameters dropped (the rest sorted) and no fragment. Null for anything not a web address. */
export function sourceKey(raw: string): string | null {
  let u: URL
  try { u = new URL(raw.trim()) } catch { return null }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return null
  const kept = [...u.searchParams.entries()].filter(([k]) => !TRACKING.test(k)).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
  const query = kept.length ? `?${new URLSearchParams(kept).toString()}` : ''
  const path = u.pathname.length > 1 ? u.pathname.replace(/\/+$/, '') : ''
  return `${u.protocol}//${u.host}${path}${query}`
}

const hostOf = (url: string) => { try { return new URL(url).host.replace(/^www\./, '') } catch { return url } }
const asObj = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {})
const str = (v: unknown) => (typeof v === 'string' ? v : '')
/** The call's input object: the structured one, or the JSON its preview carries. */
function inputOf(seg: ToolSegment): Record<string, unknown> {
  if (seg.inputObj && typeof seg.inputObj === 'object') return asObj(seg.inputObj)
  try { return asObj(JSON.parse(seg.input ?? '')) } catch { return {} }
}

/** Each link a finished call retrieved, with the title it came back with (empty when none). */
function retrievedBy(seg: ToolSegment): { url: string; title: string }[] {
  if (!seg.done || seg.ok === false) return []
  const name = (coreToolName(seg.tool) ?? '').toLowerCase()
  if (SEARCH_TOOLS.has(name)) {
    const text = (seg.output ?? '').trim()
    let data: unknown
    try { data = JSON.parse(text) } catch {
      // A result that is not JSON (an agent CLI's search, or one cut short): the links in it.
      return [...withoutFence(text).matchAll(URL_IN_TEXT)].map((m) => ({ url: trimmed(m[0]), title: '' }))
    }
    const hits = Array.isArray(data) ? data : (asObj(data).results ?? asObj(data).items)
    const out = (Array.isArray(hits) ? hits : []).map((h) => ({ url: str(asObj(h).url), title: withoutFence(str(asObj(h).title)).trim() }))
    // The engine's own list of what its answer drew on.
    const listed = asObj(data).sources
    if (Array.isArray(listed)) for (const s of listed) if (typeof s === 'string') out.push({ url: s, title: '' })
    return out.filter((r) => r.url)
  }
  if (FETCH_TOOLS.has(name)) {
    const url = str(inputOf(seg).url)
    if (!url) return []
    // The page's own title line, where the fetch led with one.
    const first = withoutFence(seg.output ?? '').trim().split('\n', 1)[0] ?? ''
    const title = /^#\s+/.test(first) ? first.replace(/^#\s+/, '') : /^title:\s*/i.test(first) ? first.replace(/^title:\s*/i, '') : ''
    return [{ url, title: title.trim() }]
  }
  return []
}

/** The links the turn's tool calls retrieved, by `sourceKey`, each with the first title it came
 *  back with. */
export function retrievedLinks(segments: Segment[]): Map<string, string> {
  const seen = new Map<string, string>()
  for (const seg of segments) {
    if (seg.kind !== 'tool') continue
    for (const { url, title } of retrievedBy(seg as ToolSegment)) {
      const key = sourceKey(url)
      if (key && (!seen.has(key) || (!seen.get(key) && title))) seen.set(key, title)
    }
  }
  return seen
}

/** The links `text` cites, in the order it first cites them: a written link's address and a bare
 *  one alike. */
export function citedLinks(text: string): string[] {
  const out: string[] = []
  for (const m of text.matchAll(URL_IN_TEXT)) {
    const url = trimmed(m[0])
    if (!out.includes(url)) out.push(url)
  }
  return out
}

/** The turn's sources: each link its prose cites that one of its calls retrieved, once, numbered in
 *  the order the prose first cites them. */
export function turnSources(segments: Segment[]): TurnSource[] {
  const retrieved = retrievedLinks(segments)
  const prose = segments.filter((s): s is TextSegment => s.kind === 'text').map((s) => s.text).join('\n')
  const out: TurnSource[] = []
  const taken = new Set<string>()
  for (const url of citedLinks(prose)) {
    const key = sourceKey(url)
    if (!key || taken.has(key) || !retrieved.has(key)) continue
    taken.add(key)
    const host = hostOf(url)
    out.push({ url, title: retrieved.get(key) || host, host, n: out.length + 1 })
  }
  return out
}

/** The chat's sources, for its pane: each turn's, once, in the order the chat first cites them. */
export function chatSources(turns: ChatTurn[]): TurnSource[] {
  const out: TurnSource[] = []
  const taken = new Set<string>()
  for (const t of turns) {
    if (t.role === 'user') continue
    for (const src of turnSources(t.segments)) {
      const key = sourceKey(src.url)
      if (!key || taken.has(key)) continue
      taken.add(key)
      out.push({ ...src, n: out.length + 1 })
    }
  }
  return out
}
