/** Preview renderers for the built-in content types — thin adapters over the
 *  existing best-in-class renderers, each conforming to PreviewProps. The
 *  registry (registerBuiltins.ts) lazy-loads these so the bundle stays flat as
 *  types are added. We WRAP the proven renderers (Markdown, the sandboxed widget
 *  iframe, the React+Babel frame, the file previews), never reinvent them. */
import { memo, useEffect, useMemo, useState } from 'react'
import { Download, ShieldAlert } from 'lucide-react'
import type { PreviewProps } from './contentTypes'
import { Markdown } from '../Markdown'
import { buildSrcdoc, readThemeVars } from '../widget/widgetSrcdoc'
import { ReactWidgetFrame } from '../widget/ReactWidgetFrame'
import { sanitizeInlineHtml } from './sanitize'
import { ImagePreview, PdfPreview, CsvPreview, JsonPreview } from '../../pages/files/browse/FilePreviews'
import { api } from '../../lib/api'

/** Markdown — react-markdown + widget blocks (the chat/file markdown renderer). */
export const MarkdownPreview = memo(function MarkdownPreview({ content }: PreviewProps) {
  return <div className="px-l py-m"><Markdown>{content}</Markdown></div>
})

/** Fail-soft sniff: does this body read as MARKDOWN rather than the editorial HTML
 *  a `kind=document` artifact is meant to hold? Agents sometimes save prose as
 *  kind=document with a markdown body — DocumentPreview would then show '#'/'**'
 *  literally. True only when there are markdown structural markers AND no real HTML
 *  block tags (so genuine editorial HTML is never misrouted). Conservative by design. */
function looksLikeMarkdown(content: string): boolean {
  const s = content.trim()
  if (!s) return false
  // A real HTML BLOCK/structural tag → treat as the intended editorial HTML document.
  // Only block-level tags gate here: genuine editorial HTML always has block structure,
  // while INLINE tags (<a>, <br>, <strong>, <em>, <img>, <hr>) legitimately appear inside
  // markdown prose — gating on them would leave a mostly-markdown doc with one stray link
  // rendering as literal source. (Markdown's own renderer sanitizes any inline HTML safely.)
  if (/<(h[1-6]|p|div|section|article|main|header|footer|nav|aside|ul|ol|li|table|thead|tbody|tr|td|th|blockquote|pre|figure)\b[^>]*>/i.test(s)) {
    return false
  }
  // Markdown structural markers: ATX heading, bold/italic, fenced code, table row,
  // list bullet, blockquote, or a markdown link.
  const markers = [
    /^#{1,6}\s+\S/m,          // # heading
    /\*\*[^*\n]+\*\*/,        // **bold**
    /^```/m,                  // fenced code
    /^\s*\|.+\|\s*$/m,        // | table | row |
    /^\s*[-*+]\s+\S/m,        // - list item
    /^\s*>\s+\S/m,            // > blockquote
    /\[[^\]]+\]\([^)]+\)/,    // [text](url)
  ]
  return markers.some((re) => re.test(s))
}

/** A sandboxed blob-iframe for script-bearing HTML (widget/html artifacts).
 *  Theme vars + mode are injected so the widget matches the app's look. */
export const IframeHtmlPreview = memo(function IframeHtmlPreview({ content, mode, title }: PreviewProps) {
  const srcdoc = useMemo(() => buildSrcdoc({ html: content, themeVars: readThemeVars(), mode }), [content, mode])
  const blobUrl = useMemo(() => URL.createObjectURL(new Blob([srcdoc], { type: 'text/html;charset=utf-8' })), [srcdoc])
  useEffect(() => () => URL.revokeObjectURL(blobUrl), [blobUrl])
  return <iframe src={blobUrl} sandbox="allow-scripts" title={title} className="h-full w-full border-none bg-surface" />
})

/** A plain (no theme injection) sandboxed iframe for raw HTML *files* — matches
 *  the former FileViewer HtmlPreview (renders the file as a real document). */
export const RawHtmlPreview = memo(function RawHtmlPreview({ content }: PreviewProps) {
  const blobUrl = useMemo(() => URL.createObjectURL(new Blob([content], { type: 'text/html;charset=utf-8' })), [content])
  useEffect(() => () => URL.revokeObjectURL(blobUrl), [blobUrl])
  return <iframe src={blobUrl} sandbox="allow-scripts" title="HTML preview" className="h-full w-full border-none bg-white" />
})

/** React artifact/widget — JSX rendered in the Babel-in-iframe frame. */
export const ReactPreview = memo(function ReactPreview({ content, title }: PreviewProps) {
  return <div className="px-l py-m"><ReactWidgetFrame jsx={content} title={title} /></div>
})

/** Shown when a sanitized renderer's source was non-empty but the fail-closed
 *  allowlist stripped everything (e.g. a doc that was only a <script>, or malformed
 *  markup that didn't parse) — so the user sees WHY the pane is empty rather than a
 *  blank void. */
const SanitizedEmpty = memo(function SanitizedEmpty({ what }: { what: string }) {
  return (
    <div className="flex h-full items-center justify-center p-l">
      <div className="flex max-w-sm flex-col items-center gap-1.5 text-center text-on-surface-low">
        <ShieldAlert size={22} className="opacity-40" />
        <p className="text-[0.8125rem]">Nothing to display.</p>
        <p className="text-[0.75rem] opacity-80">The {what} had no renderable content after sanitizing — script, handlers, and unsafe markup are removed. Switch to Edit to see the raw source.</p>
      </div>
    </div>
  )
})

/** SVG — sanitized (fail-closed allowlist) before in-DOM inject. Closes the
 *  former raw-dangerouslySetInnerHTML gap (the type now declares sanitize). */
export const SvgPreview = memo(function SvgPreview({ content }: PreviewProps) {
  const clean = useMemo(() => sanitizeInlineHtml(content, 'svg'), [content])
  if (content.trim() && !clean.trim()) return <SanitizedEmpty what="SVG" />
  return <div className="flex h-full items-center justify-center p-l" dangerouslySetInnerHTML={{ __html: clean }} />
})

/** Document — LLM-authored editorial HTML, sanitized (fail-closed 'document'
 *  allowlist) before in-DOM render so prose styling survives but script/handlers/
 *  unsafe URLs are dropped. Rendered in the parent DOM (NOT an iframe) so the
 *  text-selection comment layer can attach (the editorial doc is commentable).
 *  `.doc` scopes a readable editorial type scale (see tokens.css). */
export const DocumentPreview = memo(function DocumentPreview({ content, ...rest }: PreviewProps) {
  // Fail-soft: a kind=document artifact that actually holds markdown (agent mis-tag)
  // renders as literal '#'/'**' through the HTML path — sniff it and delegate to the
  // Markdown renderer so the prose reads correctly. Genuine editorial HTML (any real
  // block tag) never trips this. The correct long-term fix is saving prose as
  // kind=markdown (artifact_save guidance); this rescues existing mis-tagged rows.
  const asMarkdown = useMemo(() => looksLikeMarkdown(content), [content])
  const clean = useMemo(() => (asMarkdown ? '' : sanitizeInlineHtml(content, 'document')), [content, asMarkdown])
  if (asMarkdown) return <MarkdownPreview content={content} {...rest} />
  if (content.trim() && !clean.trim()) return <SanitizedEmpty what="document" />
  return <div className="doc mx-auto max-w-[72ch] px-l py-xl" dangerouslySetInnerHTML={{ __html: clean }} />
})

/** Plain text / JSON-as-text fallback — preformatted, wrapped. */
export const TextPreview = memo(function TextPreview({ content }: PreviewProps) {
  return <pre className="overflow-auto px-l py-m font-mono text-on-surface text-[0.8125rem] leading-relaxed whitespace-pre-wrap">{content}</pre>
})

/** JSON — collapsible tree (file preview), graceful on invalid JSON. */
export const JsonTreePreview = memo(function JsonTreePreview({ content, path }: PreviewProps) {
  return <JsonPreview content={content} name={path} />
})

/** CSV / TSV — table. */
export const CsvTablePreview = memo(function CsvTablePreview({ content, path }: PreviewProps) {
  return <CsvPreview content={content} name={path || 'data.csv'} />
})

/** Image — a workspace file (by path) OR a kind:image artifact (content is the
 *  /api/artifacts/<slug>/raw URL ref). One renderer, source-agnostic. */
export const ImageFilePreview = memo(function ImageFilePreview({ path, content }: PreviewProps) {
  // An artifact's content is a URL ref or data-URI; a file has no usable content.
  const src = content && /^(https?:|data:|\/)/.test(content) ? content : undefined
  return <div className="h-full overflow-auto"><ImagePreview path={path} src={src} /></div>
})

/** PDF — a workspace file (by path) OR a kind:pdf artifact (content is the
 *  /api/artifacts/<slug>/raw URL ref). One renderer, source-agnostic — the same shape
 *  ImageFilePreview above already uses.
 *
 *  It resolved from `path` ALONE until now, and a generated pdf artifact has no path:
 *  the registry deliberately routes kind:pdf here rather than registering a second pdf
 *  type ("reuses this ALREADY-WORKING PdfFile renderer" — registerBuiltins), but the
 *  renderer only ever worked for the file half, so a pdf the agent generated could not
 *  be previewed at all. */
export const PdfFilePreview = memo(function PdfFilePreview({ path, content }: PreviewProps) {
  const src = content && /^(https?:|\/)/.test(content) ? content : undefined
  if (!src && !path) return <PreviewUnavailable label="PDF" />
  return <PdfPreview path={path} src={src} />
})

/** Generated video (kind:video). Plays from the artifact's raw URL.
 *
 *  These artifacts existed before this renderer did — `video_generate` always passed
 *  kind="video", but the kind wasn't registered, so every generated video was stored
 *  AND rendered as an image (issue #94). */
export const VideoFilePreview = memo(function VideoFilePreview({ path, content }: PreviewProps) {
  const src = content && /^(https?:|data:|\/)/.test(content) ? content : path
  if (!src) return <PreviewUnavailable label="video" />
  return (
    <div className="grid h-full place-items-center overflow-auto p-m">
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- generated clip, no track */}
      <video src={src} controls className="max-h-full max-w-full rounded-md"
        style={{ background: 'var(--color-surface-high)' }} />
    </div>
  )
})

/** Generated office document (docx / xlsx) — an EXTRACTED-TEXT preview plus download.
 *
 *  Deliberately NOT a fidelity renderer, and the UI says so: rendering OOXML faithfully
 *  in the browser is a large dependency for little gain here, and implying WYSIWYG when
 *  the real editable thing is the source markdown/model would be misleading. The text
 *  comes from the same `doc_parser`/readers path that ingests uploaded documents. */
export const OfficeDocPreview = memo(function OfficeDocPreview({ path, content }: PreviewProps) {
  const raw = content && /^(https?:|\/)/.test(content) ? content : path
  return (
    <div className="flex h-full flex-col gap-m overflow-auto p-m">
      <p className="text-on-surface-low text-[0.75rem]">
        Text preview — download for full formatting. The editable source is whatever this
        document was generated from.
      </p>
      {raw
        ? (
          <a href={raw} download
            className="inline-flex w-fit items-center gap-1.5 rounded-md bg-surface-high px-3 h-8 text-on-surface text-[0.8125rem] transition-colors hover:bg-surface-container">
            <Download size={13} /> Download
          </a>
        )
        : <PreviewUnavailable label="document" />}
      <OfficeExtractedText url={raw} />
    </div>
  )
})

/** Fetches the server-extracted text for a generated office document. */
function OfficeExtractedText({ url }: { url: string | undefined }) {
  const [text, setText] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    if (!url) return
    let alive = true
    // Routed through the api client so it inherits the session header + error handling
    // rather than hand-rolling a fetch. The slug is recoverable from the raw URL, so no
    // separate id plumbing is needed.
    const slug = url.match(/\/api\/artifacts\/([^/?]+)\/raw/)?.[1]
    if (!slug) { setFailed(true); return }
    api.artifactExtractedText(decodeURIComponent(slug))
      .then((d) => { if (alive) setText(String(d.text ?? '')) })
      .catch(() => { if (alive) setFailed(true) })
    return () => { alive = false }
  }, [url])
  if (failed) {
    return (
      <p className="text-on-surface-low text-[0.8125rem]">
        Couldn't extract a text preview. The download above is unaffected.
      </p>
    )
  }
  if (text === null) return <p className="text-on-surface-low text-[0.8125rem]">Reading…</p>
  if (!text.trim()) {
    return <p className="text-on-surface-low text-[0.8125rem]">This document has no extractable text.</p>
  }
  return (
    <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap rounded-md bg-surface-container p-m text-on-surface-var text-[0.8125rem]">
      {text}
    </pre>
  )
}

/** Shared "nothing to show" line for a binary preview with no resolvable source. */
function PreviewUnavailable({ label }: { label: string }) {
  return (
    <p className="text-on-surface-low text-[0.8125rem]">
      This {label} is no longer available.
    </p>
  )
}
