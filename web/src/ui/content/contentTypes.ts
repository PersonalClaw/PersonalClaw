/** The Content Type Registry — ONE declarative source of truth mapping a piece
 *  of content (by artifact `kind`, file extension, or MIME) to how the system
 *  renders, edits, generates, and secures it.
 *
 *  This replaces the three divergent dispatchers that each re-derived "how do I
 *  show type X" their own way (ArtifactViewer's kind if/else + EDITABLE/IFRAME
 *  Sets, FileViewer's `fileViewType` ext switch, Markdown's widget-block split).
 *  Every surface resolves a `ContentType` here and hands it to <ContentSurface>.
 *
 *  A type is a *capability bundle*: it declares its preview renderer, its edit
 *  renderer, its security posture, and (for agent-producible types) its
 *  generation tool + authoring skill. Adding a type = one `register()` call +
 *  its lazy components — never a sweep across the UI.
 */
import type { ComponentType, LazyExoticComponent } from 'react'
import type { LucideIcon } from 'lucide-react'

/** How a renderer wants its content delivered + isolated. */
export interface PreviewCapability {
  /** Lazy preview component. Receives {content, mode, title, path?, streaming?}. */
  render: LazyExoticComponent<ComponentType<PreviewProps>> | ComponentType<PreviewProps>
  /** Paints incrementally as content streams in (html-widget, infographic DSL).
   *  React/binary do not — they hold until complete. */
  streaming?: boolean
  /** Renders inside a sandboxed iframe (isolated origin) vs in the parent DOM.
   *  Iframe content cannot host the in-DOM comment/selection layer. */
  sandboxed?: boolean
}

export interface EditCapability {
  /** Monaco language id for the source editor (the engine uses Monaco for all
   *  text-editable types — see <ContentSurface>). */
  language: string
  /** Split edit↔preview makes sense (markdown/infographic/document: yes;
   *  a binary image: no). Requires a preview renderer to be set. */
  split?: boolean
}

/** Where this type's content originates (agent-facing), so generation lives
 *  WITH rendering rather than scattered. Both are optional. */
export interface GenerateCapability {
  /** The agent tool that produces this type (e.g. `artifact_save kind=infographic`). */
  tool?: string
  /** The bundled authoring skill auto-surfaced when the agent intends this output. */
  skill?: string
}

/** Security posture travels WITH the type, reviewable in one place. */
export interface SecurityCapability {
  /** Isolate in a sandboxed iframe (scripts allowed but origin-isolated). */
  sandbox?: boolean
  /** Sanitize before injecting into the parent DOM — fail-closed allowlist.
   *  Required for any LLM-authored markup rendered in-DOM (svg, document). */
  sanitize?: boolean
}

/** A target this type's content can be exported to (PNG, standalone HTML, …).
 *  `run` receives the current content + title and performs the export (download /
 *  copy). Declared per type so "what can I export this as?" is one fact. */
export interface ExportTarget {
  id: string
  label: string
  run: (content: string, title: string) => void | Promise<void>
}

/** Props every preview renderer receives from <ContentSurface>. */
export interface PreviewProps {
  content: string
  mode: 'dark' | 'light'
  title: string
  /** Real file/source path when the content is a file (drives some renderers). */
  path?: string
  /** True while the source is still streaming in (progressive render). */
  streaming?: boolean
}

/** Props an INLINE-CHAT embed renderer receives (a `<widget kind=…>` block in a
 *  chat message). Distinct from PreviewProps: the chat embed carries the message
 *  identity it needs for stable save-as-artifact slugs + the action→chat bridge,
 *  and renders its own rich chrome (expand / download / save) — it is NOT the
 *  editable file/artifact surface. */
export interface EmbedProps {
  /** The block body (HTML for a widget, JSX source for a react widget). */
  content: string
  title: string
  /** Explicit `<widget slug="…">`, if supplied. */
  slug?: string
  /** Message ts + ordinal → a stable derived slug when none is explicit. */
  messageTs?: string
  widgetIndex?: number
  /** Still streaming — render the partial body, defer any host script. */
  streaming?: boolean
}

/** How this type renders when embedded inline in a chat message. Optional —
 *  only types an agent can emit as a `<widget kind=…>` block declare it. */
export interface EmbedCapability {
  render: LazyExoticComponent<ComponentType<EmbedProps>> | ComponentType<EmbedProps>
  /** A streaming embed renders its partial body; a non-streaming one (react/Babel)
   *  holds until the closing tag arrives. */
  streaming?: boolean
}

/** A content type: the full declarative bundle. */
export interface ContentType {
  /** Stable id (also the canonical artifact `kind` where one exists). */
  id: string
  /** Display label + icon + accent tone (used in headers / type chips). */
  label: string
  icon: LucideIcon
  tone: string
  /** Recognizers — ALL entry points resolve to the same type through these.
   *  `kinds`: artifact kinds. `exts`: file extensions (no dot). `mimes`: MIME
   *  prefixes. A custom `match` wins over the lists when present. */
  kinds?: string[]
  exts?: string[]
  mimes?: string[]
  match?: (probe: ContentProbe) => boolean
  preview?: PreviewCapability
  /** Inline-chat embed renderer (a `<widget kind=…>` block) — only set on types
   *  an agent emits inline (html-widget, react). De-forks Markdown.tsx's dispatch. */
  embed?: EmbedCapability
  edit?: EditCapability
  generate?: GenerateCapability
  security?: SecurityCapability
  /** Export targets for this type (PNG, standalone HTML, …). Empty/undefined = none. */
  exports?: ExportTarget[]
  /** This type's content can host the in-DOM text-selection comment layer
   *  (false for sandboxed/iframe + binary types). Defaults from preview.sandboxed. */
  commentable?: boolean
  /** The body is BINARY (bytes on the server), so `content` is only a raw-URL ref —
   *  never the bytes. Download/copy/save paths must hit /raw, not treat content as
   *  text. Mirrors the backend's BINARY_KINDS. */
  binary?: boolean
}

/** What we know about a piece of content when resolving its type. */
export interface ContentProbe {
  /** Artifact kind, if it came from the artifact store. */
  kind?: string
  /** File name (for extension dispatch), if it came from the filesystem. */
  name?: string
  /** MIME type, if known. */
  mime?: string
}

// ── registry ────────────────────────────────────────────────────────────────

const _types: ContentType[] = []
const _byId = new Map<string, ContentType>()

/** Register a content type. Last registration for an id wins (clean override). */
export function registerContentType(t: ContentType): void {
  const existing = _byId.get(t.id)
  if (existing) _types.splice(_types.indexOf(existing), 1)
  _types.push(t)
  _byId.set(t.id, t)
}

export function getContentType(id: string): ContentType | undefined {
  return _byId.get(id)
}

export function allContentTypes(): readonly ContentType[] {
  return _types
}

/** Resolve a probe to its ContentType. Resolution order:
 *  1. an explicit `match` predicate (most specific wins, first registered),
 *  2. artifact `kind` exact membership,
 *  3. file-extension membership,
 *  4. MIME-prefix membership,
 *  then a `fallback` id (default 'text'). */
export function resolveContentType(probe: ContentProbe, fallback = 'text'): ContentType {
  for (const t of _types) if (t.match?.(probe)) return t
  if (probe.kind) {
    for (const t of _types) if (t.kinds?.includes(probe.kind)) return t
  }
  if (probe.name) {
    const ext = extOf(probe.name)
    if (ext) for (const t of _types) if (t.exts?.includes(ext)) return t
  }
  if (probe.mime) {
    for (const t of _types) if (t.mimes?.some((m) => probe.mime!.startsWith(m))) return t
  }
  return _byId.get(fallback) ?? _types[0]
}

/** Resolve a chat `<widget kind=…>` block to its embed capability. A widget with
 *  no `kind` (or an unknown one) is the default HTML widget — resolve via the
 *  'widget' type; `kind="react"` resolves the react type. Returns the embed cap
 *  or undefined if the resolved type isn't inline-embeddable. */
export function embedFor(kind: string | undefined): EmbedCapability | undefined {
  const id = kind === 'react' ? 'react' : kind && _byId.has(kind) ? kind : 'widget'
  return _byId.get(id)?.embed
}

/** Whether a resolved type can be edited (has an edit capability). */
export function isEditable(t: ContentType): boolean {
  return !!t.edit
}

/** Whether a resolved type renders inside a sandboxed iframe. */
export function isSandboxed(t: ContentType): boolean {
  return !!t.preview?.sandboxed
}

/** Whether a resolved type can host the in-DOM comment layer. Explicit
 *  `commentable` wins; otherwise any non-sandboxed previewable type can. */
export function isCommentable(t: ContentType): boolean {
  if (t.commentable !== undefined) return t.commentable
  return !!t.preview && !t.preview.sandboxed
}

/** Lowercase file extension (no dot), or '' if none. */
export function extOf(name: string): string {
  const base = name.split('/').pop() || name
  const i = base.lastIndexOf('.')
  return i > 0 ? base.slice(i + 1).toLowerCase() : ''
}
