/** Split a markdown string into a sequence of markdown segments and `<widget>`
 *  segments. Agents emit rich HTML inline via `<widget title="…" slug="…">HTML
 *  </widget>` (a sandboxed-iframe contract — see WidgetFrame). Everything else is
 *  ordinary markdown. Streaming-aware: an unclosed `<widget>` during streaming
 *  yields a provisional segment so the iframe can render progressively. */

export interface MdSegment { type: 'md'; content: string }
export interface WidgetSegment { type: 'widget'; title: string; slug?: string; html: string; complete: boolean; kind?: string }
export type ContentSegment = MdSegment | WidgetSegment

// attr blob (group 1, any order of title=/slug=), inner HTML (group 2).
const WIDGET_RE = /<widget((?:\s+\w+="[^"]*")*)\s*>([\s\S]*?)<\/widget>/g
const WIDGET_OPEN_RE = /<widget((?:\s+\w+="[^"]*")*)\s*>/

function attr(attrs: string | undefined, name: string): string | undefined {
  if (!attrs) return undefined
  const m = new RegExp(`\\b${name}="([^"]*)"`).exec(attrs)
  return m ? m[1] : undefined
}
function widgetSeg(attrs: string | undefined, html: string, complete: boolean): WidgetSegment {
  // `kind="react"` selects the React+Babel renderer; default (absent) is the
  // plain HTML widget iframe. The inner body is JSX source for a react widget.
  return { type: 'widget', title: attr(attrs, 'title') || 'Widget', slug: attr(attrs, 'slug'), html: html.trim(), complete, kind: attr(attrs, 'kind') }
}

/** Parse `raw` into ordered md / widget segments. When `streaming`, an unclosed
 *  trailing `<widget>` becomes a provisional (complete:false) segment. */
export function parseWidgetBlocks(raw: string, streaming = false): ContentSegment[] {
  if (!raw.includes('<widget')) return raw.trim() ? [{ type: 'md', content: raw }] : []

  const out: ContentSegment[] = []
  let last = 0
  let m: RegExpExecArray | null
  WIDGET_RE.lastIndex = 0
  while ((m = WIDGET_RE.exec(raw)) !== null) {
    const before = raw.slice(last, m.index)
    if (before.trim()) out.push({ type: 'md', content: before })
    out.push(widgetSeg(m[1], m[2], true))
    last = m.index + m[0].length
  }

  const rest = raw.slice(last)
  // A trailing, not-yet-closed <widget> while streaming → provisional segment.
  if (streaming) {
    const open = WIDGET_OPEN_RE.exec(rest)
    if (open && !rest.slice(open.index).includes('</widget>')) {
      const before = rest.slice(0, open.index)
      if (before.trim()) out.push({ type: 'md', content: before })
      out.push(widgetSeg(open[1], rest.slice(open.index + open[0].length), false))
      return out
    }
    // bare "<widget" partial with no full opening tag yet — hold it back as md
    // (it'll resolve on the next chunk) but still show prose before it.
  }
  if (rest.trim()) out.push({ type: 'md', content: rest })
  return out
}
