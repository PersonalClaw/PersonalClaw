/** Register the built-in content types. Importing this module (once, at app
 *  start) populates the registry with every type the three former dispatchers
 *  handled, wrapping today's renderers — behavior parity. New types (infographic,
 *  document) register here too once added.
 *
 *  Each preview renderer is lazy so the bundle stays flat as types grow (Monaco,
 *  Babel, Mermaid, AntV all dynamic-import already). */
import { lazy } from 'react'
import {
  Box, Globe, Hash, Image, Braces, Code2, FileText, Table, FileCode, BarChart3, ScrollText, type LucideIcon,
} from 'lucide-react'
import { registerContentType, type PreviewProps } from './contentTypes'
import type { ComponentType } from 'react'
// Chat embeds are EAGER (chat hot-path — see chatEmbeds.tsx); only the
// file/artifact PREVIEW adapters are lazy-chunked.
import { HtmlWidgetEmbed, ReactWidgetEmbed } from './chatEmbeds'
import { exportDocumentHtml, copyDocumentHtml, exportInfographicSvg } from './exporters'

// Lazy wrappers so each renderer chunk loads on first use of its type.
const lz = (pick: () => Promise<{ [k: string]: ComponentType<PreviewProps> }>, name: string) =>
  lazy(async () => ({ default: (await pick())[name] }))

const Markdown = lz(() => import('./renderers'), 'MarkdownPreview')
const DocumentR = lz(() => import('./renderers'), 'DocumentPreview')
const IframeHtml = lz(() => import('./renderers'), 'IframeHtmlPreview')
const RawHtml = lz(() => import('./renderers'), 'RawHtmlPreview')
const ReactR = lz(() => import('./renderers'), 'ReactPreview')
const Svg = lz(() => import('./renderers'), 'SvgPreview')
const Text = lz(() => import('./renderers'), 'TextPreview')
const JsonTree = lz(() => import('./renderers'), 'JsonTreePreview')
const CsvTable = lz(() => import('./renderers'), 'CsvTablePreview')
const ImageFile = lz(() => import('./renderers'), 'ImageFilePreview')
const PdfFile = lz(() => import('./renderers'), 'PdfFilePreview')
// Infographic pulls the ~8MB AntV engine — its own chunk, loaded only when one renders.
const Infographic = lazy(() => import('./InfographicView').then((m) => ({ default: m.InfographicView })))

const PRIMARY = 'var(--color-primary)'
const tone = (c: string): string => c

let _registered = false

/** Idempotent — safe to call from app bootstrap. */
export function registerBuiltinContentTypes(): void {
  if (_registered) return
  _registered = true

  // ── widget: agent-authored rich HTML, sandboxed iframe (theme-injected) ──
  //   preview = the artifact/file render; embed = the inline-chat widget chrome.
  registerContentType({
    id: 'widget', label: 'Widget', icon: Box, tone: PRIMARY,
    kinds: ['widget'],
    preview: { render: IframeHtml, sandboxed: true, streaming: true },
    embed: { render: HtmlWidgetEmbed, streaming: true },
    security: { sandbox: true },
    commentable: false,
  })

  // ── html: a full HTML document. Artifact → theme-injected iframe; file →
  //    raw iframe. Both sandboxed. (The artifact path uses IframeHtml; files
  //    resolve by ext and also want sandboxing — same renderer family.) ──
  registerContentType({
    id: 'html', label: 'HTML', icon: Globe, tone: tone('#e06c4f'),
    kinds: ['html'], exts: ['html', 'htm'], mimes: ['text/html'],
    preview: { render: RawHtml, sandboxed: true },
    edit: { language: 'html' },
    security: { sandbox: true },
    commentable: false,
  })

  // ── react: JSX rendered in the Babel-in-iframe frame; editable as source. ──
  registerContentType({
    id: 'react', label: 'React', icon: Box, tone: tone('#61dafb'),
    kinds: ['react'], exts: ['jsx', 'tsx'],
    preview: { render: ReactR, sandboxed: true },
    embed: { render: ReactWidgetEmbed, streaming: false },
    edit: { language: 'javascript' },
    security: { sandbox: true },
    commentable: false,
  })

  // ── markdown: prose + widget blocks; editable, split-previewable, commentable ──
  registerContentType({
    id: 'markdown', label: 'Markdown', icon: Hash, tone: tone('#4f9be0'),
    kinds: ['markdown'], exts: ['md', 'markdown', 'mdx'],
    preview: { render: Markdown },
    edit: { language: 'markdown', split: true },
  })

  // ── document: LLM-authored editorial HTML, sanitized (fail-closed 'document'
  //    allowlist) before in-DOM render — the load-bearing security control since
  //    the content may echo untrusted crawled pages. Rendered in the parent DOM
  //    (not an iframe) so the text-highlight comment layer attaches → commentable.
  //    generate = artifact_save kind=document + the house-style authoring skill.
  //    exports = a standalone HTML file + copy-as-HTML. ──
  registerContentType({
    id: 'document', label: 'Document', icon: ScrollText, tone: tone('#9d86f5'),
    kinds: ['document'],
    preview: { render: DocumentR },
    edit: { language: 'html', split: true },
    security: { sanitize: true },
    generate: { tool: 'artifact_save kind=document', skill: 'editorial-document' },
    exports: [
      { id: 'html', label: 'Download as HTML', run: (c, t) => exportDocumentHtml(c, t) },
      { id: 'copy-html', label: 'Copy HTML', run: (c) => { void copyDocumentHtml(c) } },
    ],
    commentable: true,
  })

  // ── svg: sanitized (fail-closed) in-DOM render; editable source ──
  registerContentType({
    id: 'svg', label: 'SVG', icon: Image, tone: tone('#3fb950'),
    kinds: ['svg'], exts: ['svg'],
    preview: { render: Svg },
    edit: { language: 'xml', split: true },
    security: { sanitize: true },
    // sanitized in-DOM markup CAN host the comment layer (it's selectable text/markup)
    commentable: true,
  })

  // ── infographic: AntV declarative DSL → SVG. Streaming (the DSL is fault-
  //    tolerant, paints partials live); editable as DSL source; split makes sense.
  //    generate = the agent path: artifact_save kind=infographic + the bundled
  //    AntV authoring skill (auto-surfaced when the agent intends an infographic). ──
  registerContentType({
    id: 'infographic', label: 'Infographic', icon: BarChart3, tone: tone('#5b8cff'),
    kinds: ['infographic'],
    preview: { render: Infographic, streaming: true },
    edit: { language: 'plaintext', split: true },
    generate: { tool: 'artifact_save kind=infographic', skill: 'infographic-syntax' },
    // SVG only — the AntV-native vector format (scalable, lossless). PNG-via-canvas
    // is impossible here: AntV emits <foreignObject>, which taints a canvas so
    // toBlob throws (browser security). SVG is the higher-fidelity export anyway.
    exports: [
      { id: 'svg', label: 'Download as SVG', run: (c, t) => exportInfographicSvg(c, t) },
    ],
    // SVG output rendered in-DOM by AntV; the DSL is data, not markup — no inline
    // comment layer needed (and AntV owns the SVG subtree).
    commentable: false,
  })

  // ── json: collapsible tree preview; editable; split ──
  registerContentType({
    id: 'json', label: 'JSON', icon: Braces, tone: tone('#d4a017'),
    kinds: ['json'], exts: ['json', 'jsonl', 'json5', 'jsonc'], mimes: ['application/json'],
    preview: { render: JsonTree },
    edit: { language: 'json', split: true },
  })

  // ── csv/tsv: table preview; editable; split ──
  registerContentType({
    id: 'csv', label: 'CSV', icon: Table, tone: tone('#8a63d2'),
    exts: ['csv', 'tsv'], mimes: ['text/csv'],
    preview: { render: CsvTable },
    edit: { language: 'plaintext', split: true },
  })

  // ── image: a workspace image file (by ext/mime) OR a kind:image artifact
  //    (generated image; content = the /api/artifacts/<slug>/raw URL ref). Binary,
  //    no edit (an image edit goes through the image_generate tool's edit path). ──
  registerContentType({
    id: 'image', label: 'Image', icon: Image, tone: tone('#3fb950'),
    kinds: ['image'],
    exts: ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'ico', 'tiff'],
    mimes: ['image/'],
    preview: { render: ImageFile },
    generate: { tool: 'image_generate' },
    commentable: false,
    binary: true,
  })

  // ── pdf: binary file preview by path (no edit) ──
  registerContentType({
    id: 'pdf', label: 'PDF', icon: FileText, tone: tone('#e0574f'),
    exts: ['pdf'], mimes: ['application/pdf'],
    preview: { render: PdfFile },
    commentable: false,
  })

  // ── code: the catch-all editable source type (no rendered preview). Matches
  //    any file whose ext resolves to a real Monaco language; falls through to
  //    edit-only. The `match` resolves a broad set so it's the default editor. ──
  registerContentType({
    id: 'code', label: 'Code', icon: FileCode, tone: 'var(--color-on-surface-low)',
    // code has no `kinds`/`exts` list — it's reached via the explicit resolver
    // fallback for editable source. The host passes a Monaco language separately.
    edit: { language: 'plaintext' },
  })

  // ── text: the absolute fallback — preformatted plain text, editable ──
  registerContentType({
    id: 'text', label: 'Text', icon: Code2, tone: 'var(--color-on-surface-low)',
    kinds: ['text'], exts: ['txt', 'log'], mimes: ['text/plain'],
    preview: { render: Text },
    edit: { language: 'plaintext' },
  })
}

// Re-export the icon type so callers needn't reach into lucide directly.
export type { LucideIcon }
