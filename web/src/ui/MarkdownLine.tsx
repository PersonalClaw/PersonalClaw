import { createElement } from 'react'
import { HIT_PAIR, Markdown } from './Markdown'
import { previewLine } from '../lib/previewText'

/** The elements a one-line field is drawn in. */
export type MarkdownLineSink = 'span' | 'div' | 'p' | 'h2' | 'h3' | 'li'

/** A sink that cuts its text: its tooltip is the whole line, as plain text. */
const CUTS = /(?:^|\s)(?:truncate|line-clamp-\d+)(?=\s|$)/

/** A field written as one line, rendered: a title, a name, a label, a reason, a question, a
 *  purpose, a criterion or a step that an agent, a tool or a person wrote, and that carries
 *  markdown ("Skip `model_calls` in **published.py**").
 *
 *  It is the sink element itself, with `<Markdown line>` inside: the parse starts no block, so a
 *  leading "#", "2024." or ">" is the text it is, and only inline marks render (a code span is the
 *  inline pill, a link is styled text, never an anchor). So the sink's own `truncate` or
 *  `line-clamp` cuts the rendered row, and the line can sit inside a button or a row's hit target.
 *
 *  Where the sink cuts the line, its tooltip is the whole line as plain text (`previewLine`), as
 *  is any accessible name built from it at the call site: no mark reaches an attribute. */
export function MarkdownLine({ text, as = 'span', className, tooltip, hits = false }: {
  /** The field's text, as it was written. Nothing, or only space, draws the sink empty. */
  text: string | null | undefined
  /** The sink element (default `span`). */
  as?: MarkdownLineSink
  /** The sink's own ink, type role and cut (`truncate`, `line-clamp-2`). */
  className?: string
  /** The sink's tooltip. By default the whole line as plain text when `className` cuts it, and
   *  none otherwise; `false` for none. */
  tooltip?: string | false
  /** The search index's `<<…>>` marks become `<mark>`s (`Markdown`'s `hits`). */
  hits?: boolean
}) {
  const source = text ?? ''
  const plain = hits ? source.replace(HIT_PAIR, '$1') : source
  const title = tooltip === false ? undefined : (tooltip ?? (className && CUTS.test(className) ? previewLine(plain) : undefined))
  return createElement(as, { className, title }, <Markdown line hits={hits}>{source}</Markdown>)
}
