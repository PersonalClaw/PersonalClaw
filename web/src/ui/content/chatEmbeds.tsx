/** Inline-chat embed renderers — the rich, chat-specific widget chrome a
 *  `<widget kind=…>` block renders to (sandboxed theme-aware iframe, action→chat
 *  bridge, save-as-artifact, expand, height-sync, streaming partial). These are
 *  EAGER (not lazy): chat is the hot path and `WidgetFrame`/`ReactWidgetFrame`
 *  are already in the main bundle via Markdown — lazy-loading them would flash a
 *  Suspense fallback on the first widget in every conversation.
 *
 *  The registry's `embed` capability points here so Markdown.tsx's widget-block
 *  split resolves the renderer through the ONE registry instead of a hardcoded
 *  `kind === 'react' ? … : …` fork. Distinct from `renderers.tsx` (the lazy
 *  file/artifact PREVIEW adapters). */
import { memo } from 'react'
import type { EmbedProps } from './contentTypes'
import { WidgetFrame } from '../widget/WidgetFrame'
import { ReactWidgetFrame } from '../widget/ReactWidgetFrame'

/** HTML widget — the sandboxed, theme-aware, action-bridged chat widget. */
export const HtmlWidgetEmbed = memo(function HtmlWidgetEmbed({ content, title, slug, messageTs, widgetIndex, streaming }: EmbedProps) {
  return <WidgetFrame html={content} title={title} slug={slug} messageTs={messageTs} widgetIndex={widgetIndex} streaming={streaming} />
})

/** React widget — JSX in the Babel-in-iframe frame (no partial render). */
export const ReactWidgetEmbed = memo(function ReactWidgetEmbed({ content, title }: EmbedProps) {
  return <ReactWidgetFrame jsx={content} title={title} />
})
