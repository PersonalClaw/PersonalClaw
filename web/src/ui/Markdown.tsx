import { memo, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { bounce } from '../design/motion'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeRaw from 'rehype-raw'
import rehypeKatex from 'rehype-katex'
import hljs from 'highlight.js/lib/common'
import { Play, Copy, Check, ImageOff, RefreshCw } from 'lucide-react'
import type { PluggableList } from 'unified'
import { requestRunInTerminal } from '../pages/terminal/terminalBridge'
import { api } from '../lib/api'
import { createElement } from 'react'
import { parseWidgetBlocks } from './widget/blocks'
import { embedFor } from './content/contentTypes'
import { MermaidBlock } from './widget/MermaidBlock'
import 'katex/dist/katex.min.css'

/** Full markdown renderer: react-markdown + remark-gfm (tables, task lists,
 *  strikethrough) + remark-math + rehype-katex (LaTeX) + rehype-raw (inline
 *  HTML) + highlight.js (code), with ```mermaid diagrams, ```diff highlighting,
 *  and `<widget>` blocks rendered as sandboxed theme-aware iframes (the agent
 *  visualization contract — see widget/). Component overrides are NE-tokenized.
 *  Source is trusted (our own backend); widget HTML is sandboxed regardless. */

// shell-ish languages where "Run in terminal" makes sense.
const SHELL_LANGS = new Set(['shell', 'bash', 'sh', 'zsh', 'console', 'shellsession', 'fish'])

/** Render a unified-diff code block with +/- line tinting. */
function DiffBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => { navigator.clipboard?.writeText(code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }).catch(() => {}) }
  return (
    <div className="group/code my-3 overflow-hidden rounded-lg bg-surface-low">
      <div className="flex items-center gap-2 px-m pt-2">
        <span className="text-[0.7rem] uppercase tracking-wide text-on-surface-low">diff</span>
        <button type="button" onClick={copy} aria-label="Copy diff" title={copied ? 'Copied' : 'Copy'}
          className="ml-auto inline-flex size-6 items-center justify-center rounded text-on-surface-low opacity-0 transition-opacity hover:bg-surface-high hover:text-on-surface group-hover/code:opacity-100"
          style={copied ? { color: 'var(--color-success)' } : undefined}>
          <AnimatePresence mode="wait" initial={false}>
            <motion.span key={copied ? 'ok' : 'copy'} initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0, opacity: 0 }} transition={bounce.playful} className="grid place-items-center">
              {copied ? <Check size={12} /> : <Copy size={12} />}
            </motion.span>
          </AnimatePresence>
        </button>
      </div>
      <pre className="overflow-x-auto px-m py-2 text-[0.8125rem] leading-relaxed font-mono">
        {code.split('\n').map((ln, i) => {
          const add = /^\+(?!\+)/.test(ln), del = /^-(?!-)/.test(ln), hunk = /^@@/.test(ln)
          return (
            <div key={i} style={add ? { background: 'color-mix(in srgb, var(--color-ok) 14%, transparent)', color: 'var(--color-ok)' }
              : del ? { background: 'color-mix(in srgb, var(--color-danger) 14%, transparent)', color: 'var(--color-danger)' }
              : hunk ? { color: 'var(--color-primary)' } : { color: 'var(--color-on-surface-var)' }}>{ln || ' '}</div>
          )
        })}
      </pre>
    </div>
  )
}

function isDiff(code: string, lang?: string): boolean {
  if (lang === 'diff') return true
  const lines = code.split('\n')
  return lines.filter((l) => /^@@|^[+-][^+-]/.test(l)).length >= 2
}

/** Slug from an artifact raw-URL (`/api/artifacts/<slug>/raw?...`), or '' if not one. */
function artifactSlugFromSrc(src: string): string {
  const m = src.match(/\/api\/artifacts\/([^/?]+)\/raw\b/)
  return m ? decodeURIComponent(m[1]) : ''
}

/** An inline image embedded in chat (a generated kind:image artifact referenced
 *  as `![alt](/api/artifacts/<slug>/raw?version=N)`). If the bytes load, shows the
 *  image. If they 404 (the artifact was deleted but the transcript still references
 *  it), degrades to a clean placeholder showing the original prompt (the alt text)
 *  + a Regenerate button. Regenerate re-runs generation AT THE SAME SLUG in the
 *  background (POST /regenerate, prompt recovered server-side from tool history) —
 *  no new chat message — then reloads the <img> in place. `chatSessionKey` scopes
 *  the history lookup; absent it (non-chat surfaces) the placeholder is static. */
function InlineArtifactImage({ src, alt, chatSessionKey }: {
  src: string; alt: string; chatSessionKey?: string
}) {
  const [failed, setFailed] = useState(false)
  // Cache-buster appended on a successful regenerate so the browser refetches the
  // same (now-immutable-cached) /raw URL instead of serving the 404 from cache.
  const [bust, setBust] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const slug = artifactSlugFromSrc(src)

  const regenerate = async () => {
    if (!slug) return
    setBusy(true); setErr('')
    try {
      await api.regenerateArtifactImage(slug, { session: chatSessionKey, prompt: alt })
      // Re-show the <img>, busting cache so the freshly-written bytes load.
      setBust(`${src.includes('?') ? '&' : '?'}_r=${Date.now()}`)
      setFailed(false)
    } catch (e) {
      setErr((e as Error)?.message || 'Regenerate failed')
    } finally { setBusy(false) }
  }

  if (failed) {
    return (
      <div className="my-2 flex max-w-md flex-col gap-2 rounded-lg border border-outline-variant/40 bg-surface-low px-4 py-3">
        <div className="flex items-center gap-2 text-on-surface-low text-[0.8125rem]">
          <ImageOff size={14} className="shrink-0" />
          <span>This image is no longer available.</span>
        </div>
        {alt && (
          <div className="text-on-surface-var text-[0.8125rem]">
            <span className="text-on-surface-low">Prompt:</span> {alt}
          </div>
        )}
        {chatSessionKey && slug && (
          <button type="button" onClick={regenerate} disabled={busy}
            className="mt-0.5 inline-flex w-fit items-center gap-1.5 rounded-md bg-surface-high px-2.5 py-1 text-on-surface text-[0.75rem] transition-colors hover:bg-surface-highest disabled:opacity-60">
            <RefreshCw size={12} className={busy ? 'animate-spin' : ''} /> {busy ? 'Regenerating…' : 'Regenerate image'}
          </button>
        )}
        {err && <span className="text-[0.72rem]" style={{ color: 'var(--color-danger)' }}>{err}</span>}
      </div>
    )
  }
  return (
    <img src={src + bust} alt={alt} loading="lazy" onError={() => setFailed(true)}
      className="my-2 max-h-[28rem] max-w-full rounded-lg border border-outline-variant/40 object-contain" />
  )
}

function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  let html = ''
  try {
    html = lang && hljs.getLanguage(lang)
      ? hljs.highlight(code, { language: lang }).value
      : hljs.highlightAuto(code).value
  } catch { html = code.replace(/[&<>]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[ch]!)) }
  const [copied, setCopied] = useState(false)
  const runnable = !!lang && SHELL_LANGS.has(lang.toLowerCase())
  const copy = () => { navigator.clipboard?.writeText(code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }).catch(() => {}) }
  const run = () => requestRunInTerminal(code.trim())
  return (
    <div className="group/code my-3 overflow-hidden rounded-lg bg-surface-low">
      <div className="flex items-center gap-2 px-m pt-2">
        {lang && <span className="text-[0.7rem] uppercase tracking-wide text-on-surface-low">{lang}</span>}
        <div className="ml-auto flex items-center gap-0.5 opacity-0 transition-opacity group-hover/code:opacity-100 focus-within:opacity-100">
          {runnable && (
            <button type="button" onClick={run} title="Run in terminal"
              className="inline-flex h-6 items-center gap-1 rounded px-1.5 text-[0.7rem] text-on-surface-low hover:bg-surface-high hover:text-primary">
              <Play size={11} /> Run
            </button>
          )}
          <button type="button" onClick={copy} title={copied ? 'Copied' : 'Copy'} aria-label="Copy code"
            className="inline-flex size-6 items-center justify-center rounded text-on-surface-low hover:bg-surface-high hover:text-on-surface"
            style={copied ? { color: 'var(--color-success)' } : undefined}>
            {/* copy→check pops on a spring (success bloom) rather than a hard swap */}
            <AnimatePresence mode="wait" initial={false}>
              <motion.span key={copied ? 'ok' : 'copy'} initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0, opacity: 0 }} transition={bounce.playful} className="grid place-items-center">
                {copied ? <Check size={12} /> : <Copy size={12} />}
              </motion.span>
            </AnimatePresence>
          </button>
        </div>
      </div>
      <pre className="overflow-x-auto px-m py-2 text-[0.8125rem] leading-relaxed"><code className="hljs font-mono" dangerouslySetInnerHTML={{ __html: html }} /></pre>
    </div>
  )
}

/** Allow only safe link schemes. Markdown here is rendered in the APP's own origin
 *  (not a sandboxed iframe), and rehype-raw passes inline HTML through unsanitized —
 *  so a `javascript:`/`data:`/`vbscript:` href in worker-authored content (LLM output,
 *  potentially echoing a malicious file the worker read in a brownfield repo) would
 *  execute on click with the app's cookies/storage. Permit http(s), mailto, tel,
 *  relative, and #anchors; neutralize everything else to a non-navigable link. */
function safeHref(href: unknown): string | undefined {
  if (typeof href !== 'string') return undefined
  const h = href.trim()
  if (!h) return undefined
  // Relative paths, anchors, and protocol-relative URLs are safe.
  if (/^(\/|\.|#|mailto:|tel:)/i.test(h)) return h
  if (/^https?:\/\//i.test(h)) return h
  // A scheme-less host-ish link (example.com/x) — let it through as-is (the browser
  // treats it relative; harmless). Anything with an explicit dangerous scheme is dropped.
  if (/^[a-z][a-z0-9+.-]*:/i.test(h)) return undefined  // some other explicit scheme → block
  return h
}

/** Heuristic: does an inline-code string look like a clickable file path? */
const FILE_PATH_RE = /^(?:~|\.{0,2}\/)?[\w.\-]+(?:\/[\w.\-]+)*\.\w{1,8}\/?$/
function looksLikeFile(s: string): boolean {
  const t = s.trim()
  return t.length <= 200 && !t.includes(' ') && FILE_PATH_RE.test(t)
}

function renderCode({ className, children }: any) {
  const m = /language-(\w+)/.exec(className || '')
  const str = String(children).replace(/\n$/, '')
  // Block vs inline: react-markdown only tags fenced code with a `language-*`
  // class when a language is given — a fenced block with NO language has no
  // className and would otherwise be mistaken for inline code. Treat anything
  // with a className OR a newline (i.e. a real multi-line fence) as a block.
  const isBlock = !!className || str.includes('\n')
  if (!isBlock) return <code className="rounded-sm bg-surface-high px-1.5 py-0.5 text-[0.85em] font-mono text-primary-emphasis">{children}</code>
  const lang = m?.[1]
  if (lang === 'mermaid') return <MermaidBlock code={str} />
  if (isDiff(str, lang)) return <DiffBlock code={str} />
  return <CodeBlock code={str} lang={lang} />
}

const COMPONENTS: Record<string, React.ComponentType<any>> = {
  code: renderCode,
  pre({ children }: any) { return <>{children}</> },
  table({ children }: any) { return <div className="my-3 overflow-x-auto"><table className="w-full border-collapse text-[0.875rem]">{children}</table></div> },
  th({ children }: any) { return <th className="border-b border-outline-variant/50 bg-surface-high px-m py-2 text-left text-on-surface-var" style={{ fontVariationSettings: '"wght" 500' }}>{children}</th> },
  td({ children }: any) { return <td className="border-b border-outline-variant/30 px-m py-2">{children}</td> },
  a({ href, children }: any) {
    const safe = safeHref(href)
    // A dangerous/blocked href renders as styled-but-inert text (no navigation), so the
    // link text is still readable but can't execute a javascript:/data: payload.
    if (!safe) return <span className="text-primary underline decoration-primary/40 underline-offset-2" title="Link removed (unsafe URL)">{children}</span>
    return <a href={safe} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2 decoration-primary/40 hover:decoration-primary">{children}</a>
  },
  img({ src, alt }: any) {
    // Render an inline image (e.g. a generated kind:image artifact referenced as
    // ![](/api/artifacts/<slug>/raw)) — safe-src gated (same allowlist as links,
    // plus data:image), styled to the surface, lazy + capped so it can't blow out
    // the message column. A blocked src degrades to its alt text; a 404 (deleted
    // artifact still referenced by the transcript) degrades to a placeholder via
    // InlineArtifactImage. (componentsWith() overrides this to thread onRegenerate.)
    const safe = typeof src === 'string' && (/^(\/|https?:\/\/)/.test(src.trim()) || /^data:image\//i.test(src.trim()))
      ? src.trim() : undefined
    if (!safe) return <span className="text-on-surface-low italic">{alt || 'image'}</span>
    return <InlineArtifactImage src={safe} alt={alt || ''} />
  },
  blockquote({ children }: any) { return <blockquote className="my-2 border-l-[3px] border-primary pl-m italic text-on-surface-var">{children}</blockquote> },
  hr() { return <hr className="my-4 border-outline-variant/40" /> },
  h1({ children }: any) { return <h1 className="mt-4 mb-2 text-on-surface" data-type="headline-s">{children}</h1> },
  h2({ children }: any) { return <h2 className="mt-3 mb-2 text-on-surface text-[1.15rem]" style={{ fontVariationSettings: '"wght" 500' }}>{children}</h2> },
  h3({ children }: any) { return <h3 className="mt-3 mb-1.5 text-on-surface text-[1rem]" style={{ fontVariationSettings: '"wght" 500' }}>{children}</h3> },
  h4({ children }: any) { return <h4 className="mt-2 mb-1 text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 500' }}>{children}</h4> },
  ul({ children }: any) { return <ul className="my-2 list-disc space-y-1 pl-7 marker:text-on-surface-low">{children}</ul> },
  ol({ children }: any) { return <ol className="my-2 list-decimal space-y-1 pl-7 marker:text-on-surface-low">{children}</ol> },
  li({ children }: any) { return <li className="text-[0.9375rem] leading-relaxed">{children}</li> },
  p({ children }: any) { return <p className="my-1.5 leading-relaxed text-[0.9375rem]">{children}</p> },
  strong({ children }: any) { return <strong className="text-on-surface" style={{ fontVariationSettings: '"wght" 600' }}>{children}</strong> },
  em({ children }: any) { return <em className="italic">{children}</em> },
}

const REMARK: PluggableList = [remarkGfm, [remarkMath, { singleDollarTextMath: false }]]
const REHYPE: PluggableList = [[rehypeRaw, { passThrough: ['math', 'inlineMath'] }], rehypeKatex]

// Bare file paths inside prose (not just inline-code): /a/b.ext, ~/a/b.ext, or
// workspace-relative a/b.ext with an extension. Conservative to avoid prose.
const BARE_FILE_RE = /((?:~|\.{0,2}\/)?[\w.\-]+(?:\/[\w.\-]+)+\.\w{1,8})/g

/** Linkify bare file paths inside a markdown text node. Returns the children
 *  unchanged unless a path is found, in which case the string is split into
 *  text + clickable file-link spans. Only operates on plain strings (leaves
 *  already-rendered React children — bold, code, etc. — alone). */
function linkifyFiles(children: any, onFileClick: (path: string) => void): any {
  return (Array.isArray(children) ? children : [children]).flatMap((child, ci) => {
    if (typeof child !== 'string') return [child]
    const parts: any[] = []
    let last = 0, m: RegExpExecArray | null
    BARE_FILE_RE.lastIndex = 0
    while ((m = BARE_FILE_RE.exec(child)) !== null) {
      const path = m[1]
      if (m.index > last) parts.push(child.slice(last, m.index))
      parts.push(
        <button key={`${ci}-${m.index}`} type="button" onClick={() => onFileClick(path)} title={`Open ${path}`}
          className="align-baseline font-mono text-[0.95em] text-primary underline decoration-primary/40 underline-offset-2 transition-colors hover:decoration-primary">
          {path}
        </button>,
      )
      last = m.index + path.length
    }
    if (last < child.length) parts.push(child.slice(last))
    return parts.length ? parts : [child]
  })
}

/** When `onFileClick` is supplied, file mentions become clickable: inline-code
 *  that looks like a path renders as a link, AND bare paths inside prose
 *  (paragraphs / list items) are linkified — so file mentions are interactive
 *  right where they're read, whether the model used backticks or not. */
function componentsWith(
  onFileClick?: (path: string) => void,
  chatSessionKey?: string,
): Record<string, React.ComponentType<any>> {
  if (!onFileClick && !chatSessionKey) return COMPONENTS
  const base: Record<string, React.ComponentType<any>> = { ...COMPONENTS }
  // Scope the inline-image renderer to the chat session so a deleted image's
  // placeholder can offer "Regenerate" (re-runs at the same slug, server recovers
  // the prompt from this session's tool history).
  if (chatSessionKey) {
    base.img = ({ src, alt }: any) => {
      const safe = typeof src === 'string' && (/^(\/|https?:\/\/)/.test(src.trim()) || /^data:image\//i.test(src.trim()))
        ? src.trim() : undefined
      if (!safe) return <span className="text-on-surface-low italic">{alt || 'image'}</span>
      return <InlineArtifactImage src={safe} alt={alt || ''} chatSessionKey={chatSessionKey} />
    }
  }
  if (!onFileClick) return base
  const L = (children: any) => linkifyFiles(children, onFileClick)
  return {
    ...base,
    code({ className, children }: any) {
      const str = String(children).replace(/\n$/, '')
      if (!className && looksLikeFile(str)) {
        return (
          <button type="button" onClick={() => onFileClick(str.trim())} title={`Open ${str.trim()}`}
            className="rounded-sm bg-surface-high px-1.5 py-0.5 align-baseline text-[0.85em] font-mono text-primary underline decoration-primary/40 underline-offset-2 transition-colors hover:bg-surface-highest hover:decoration-primary">
            {children}
          </button>
        )
      }
      return renderCode({ className, children })
    },
    p({ children }: any) { return <p className="my-1.5 leading-relaxed text-[0.9375rem]">{L(children)}</p> },
    li({ children }: any) { return <li className="text-[0.9375rem] leading-relaxed">{L(children)}</li> },
    td({ children }: any) { return <td className="border-b border-outline-variant/30 px-m py-2">{L(children)}</td> },
  }
}

/** Flatten a non-string `children` (object/array an agent emitted where a doc
 *  was expected) into readable text, so it renders instead of crashing React. */
function stringifyChildren(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  if (Array.isArray(v)) return v.map(stringifyChildren).filter(Boolean).join('\n')
  if (typeof v === 'object') {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, val]) => `- **${k}:** ${stringifyChildren(val)}`).join('\n')
  }
  return String(v)
}

/** Plain markdown (no widget split) — the inner renderer. */
function MarkdownText({ children, onFileClick, chatSessionKey }: {
  children: string; onFileClick?: (path: string) => void; chatSessionKey?: string
}) {
  return <ReactMarkdown remarkPlugins={REMARK} rehypePlugins={REHYPE} components={componentsWith(onFileClick, chatSessionKey)}>{children}</ReactMarkdown>
}

export const Markdown = memo(function Markdown({ children, className, onFileClick, chatSessionKey, messageTs, streaming }: {
  children: unknown; className?: string; onFileClick?: (path: string) => void
  /** Chat session key — enables "Regenerate" on a deleted inline image's placeholder
   *  (re-runs at the same slug; server recovers the prompt from this session). */
  chatSessionKey?: string
  /** stable per-message timestamp → derived widget slugs survive refresh. */
  messageTs?: string
  /** still streaming → render an unclosed trailing `<widget>` progressively. */
  streaming?: boolean
}) {
  // Defensive: callers occasionally pass agent/tool-authored content that isn't
  // a clean string (an object/array where a doc was expected). Coerce so a stray
  // shape renders as readable text instead of crashing React (#31).
  const text = typeof children === 'string' ? children : stringifyChildren(children)
  if (!text.trim()) return null
  // Split out `<widget>` blocks; render each as a sandboxed iframe, prose as MD.
  const segments = parseWidgetBlocks(text, streaming)
  if (segments.length === 1 && segments[0].type === 'md') {
    return <div className={`text-on-surface ${className ?? ''}`}><MarkdownText onFileClick={onFileClick} chatSessionKey={chatSessionKey}>{text}</MarkdownText></div>
  }
  let wi = 0
  return (
    // flow-root: widgets float left when their natural width is narrow enough
    // (the WidgetFrame measures the host column itself); the formatting context
    // keeps the float contained inside THIS turn.
    <div className={`flow-root text-on-surface ${className ?? ''}`}>
      {segments.map((seg, i) => {
        if (seg.type !== 'widget') return <MarkdownText key={i} onFileClick={onFileClick} chatSessionKey={chatSessionKey}>{seg.content}</MarkdownText>
        // Resolve the block's renderer through the ONE content registry (was a
        // hardcoded react/widget fork): adding an inline-embeddable type is now a
        // registry entry, not an edit here.
        const embed = embedFor(seg.kind)
        if (!embed) return null
        // A non-streaming embed (react/Babel) has no partial-render mode — hold it
        // until the closing tag arrives; a streaming one paints its partial body.
        if (!embed.streaming && !seg.complete) return null
        const widgetIndex = wi++
        return createElement(embed.render, { key: i, content: seg.html, title: seg.title, slug: seg.slug, messageTs, widgetIndex, streaming: !seg.complete })
      })}
    </div>
  )
})
