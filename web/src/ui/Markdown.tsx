import { createContext, memo, useContext, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { physics } from '../design/motion'
import { fvs } from '../design/fontWeight'
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
import type { MemoryCitation } from '../pages/chat/chatTypes'
import 'katex/dist/katex.min.css'
import { copyText } from '../app/clipboard'

/** The one renderer for markdown this app did not write: a model's reply, a tool's result, a
 *  knowledge item's body (a feed entry, a scraped page, an uploaded file, a mirrored
 *  artifact), an inbox message, an app's or a tool's description, release notes. None of it is
 *  ours, and all of it renders on the dashboard's own origin, so it renders as MARKDOWN ONLY:
 *
 *   · embedded HTML is shown as the text it is, except the attribute-free formatting tags in
 *     `EMBEDDED_FORMATTING` (`<kbd>`, `<br>`, `<sub>` …), which carry no URL, handler or
 *     style. A form, a frame, a style block or an image tag in a body is words on the page;
 *   · a link opens only for http, https and mailto (`webHref`);
 *   · an image loads only from https or the artifact library's own route (`imageSrc`) —
 *     what the page's Content-Security-Policy loads;
 *   · a `<widget>` block runs in its sandboxed frame only where the caller opts in
 *     (`widgets`): the agent's own chat replies, the one place the model is given that
 *     contract. Anywhere else a widget tag is embedded HTML like any other.
 *
 *  react-markdown + remark-gfm (tables, task lists, strikethrough) + remark-math +
 *  rehype-katex (LaTeX) + highlight.js (code), with ```mermaid diagrams and ```diff
 *  highlighting. Component overrides are NE-tokenized. The project's own copy — a label, a
 *  hint, an error sentence — is JSX text and never comes through here. */

// shell-ish languages where "Run in terminal" makes sense.
const SHELL_LANGS = new Set(['shell', 'bash', 'sh', 'zsh', 'console', 'shellsession', 'fish'])

/** Render a unified-diff code block with +/- line tinting. */
function DiffBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => { if (await copyText(code, 'the code')) { setCopied(true); setTimeout(() => setCopied(false), 1500) } }
  return (
    <div className="group/code my-3 overflow-hidden rounded-lg bg-surface-low">
      <div className="flex items-center gap-2 px-m pt-2">
        <span data-type="caption" className="uppercase tracking-wide text-on-surface-low">diff</span>
        <button type="button" onClick={copy} aria-label="Copy diff" title={copied ? 'Copied' : 'Copy'}
          className="ml-auto inline-flex size-6 items-center justify-center rounded text-on-surface-low opacity-0 transition-opacity hover:bg-surface-high hover:text-on-surface group-hover/code:opacity-100 focus-within:opacity-100"
          style={copied ? { color: 'var(--color-success)' } : undefined}>
          <AnimatePresence mode="wait" initial={false}>
            <motion.span key={copied ? 'ok' : 'copy'} initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0, opacity: 0 }} transition={physics.playful} className="grid place-items-center">
              {copied ? <Check size={12} /> : <Copy size={12} />}
            </motion.span>
          </AnimatePresence>
        </button>
      </div>
      {/* 🔴 A horizontally-scrolling box that Chromium puts in the tab order, with no explicit name, is
          announced as its own CONTENT — measured on `#/settings/updates` at 390px: focus landed on this
          `<pre>` and its computed name was 122 characters of the code inside it. The trio
          `tabIndex={0}` + `role="group"` + `aria-label` is this repo's canonical form for a text scroll
          region (`ui/content/ContentSurface` ×2, `DiagnosticsPanel`, `SecurityPanel`), and a named
          `group` stops taking its name from its subtree. */}
      {/* leading-relaxed stays: code blocks read at the airier line-height on
          purpose, and the utility (utilities layer) wins over the role's own. */}
      {/* `focus-visible:-outline-offset-2` completes the canonical scroll-region form — see
          `pages/learning/LearningPage`. A code block fills its rounded wrapper, so the outward ring
          is clipped left, right and bottom. */}
      <pre tabIndex={0} role="group" aria-label="Diff" data-type="body-s"
        className="overflow-x-auto px-m py-2 leading-relaxed font-mono focus-visible:-outline-offset-2">
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

/** An image from the artifact library (a generated kind:image artifact referenced
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
        <div data-type="body-s" className="flex items-center gap-2 text-on-surface-low">
          <ImageOff size={14} className="shrink-0" />
          <span>This image is no longer available.</span>
        </div>
        {alt && (
          <div data-type="body-s" className="text-on-surface-var">
            <span className="text-on-surface-low">Prompt:</span> {alt}
          </div>
        )}
        {chatSessionKey && slug && (
          <button type="button" onClick={regenerate} disabled={busy}
            data-type="caption"
            className="mt-0.5 inline-flex w-fit items-center gap-1.5 rounded-md bg-surface-high px-2.5 py-1 text-on-surface transition-colors hover:bg-surface-highest disabled:opacity-60">
            <RefreshCw size={12} className={busy ? 'animate-spin' : ''} /> {busy ? 'Regenerating…' : 'Regenerate image'}
          </button>
        )}
        {err && <span data-type="caption" style={{ color: 'var(--color-danger)' }}>{err}</span>}
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
  const copy = async () => { if (await copyText(code, 'the code')) { setCopied(true); setTimeout(() => setCopied(false), 1500) } }
  const run = () => requestRunInTerminal(code.trim())
  return (
    <div className="group/code my-3 overflow-hidden rounded-lg bg-surface-low">
      <div className="flex items-center gap-2 px-m pt-2">
        {lang && <span data-type="caption" className="uppercase tracking-wide text-on-surface-low">{lang}</span>}
        <div className="ml-auto flex items-center gap-0.5 opacity-0 transition-opacity group-hover/code:opacity-100 focus-within:opacity-100">
          {runnable && (
            <button type="button" onClick={run} title="Run in terminal" data-type="caption"
              className="inline-flex h-6 items-center gap-1 rounded px-1.5 text-on-surface-low hover:bg-surface-high hover:text-primary">
              <Play size={11} /> Run
            </button>
          )}
          <button type="button" onClick={copy} title={copied ? 'Copied' : 'Copy'} aria-label="Copy code"
            className="inline-flex size-6 items-center justify-center rounded text-on-surface-low hover:bg-surface-high hover:text-on-surface"
            style={copied ? { color: 'var(--color-success)' } : undefined}>
            {/* copy→check pops on a spring (success bloom) rather than a hard swap */}
            <AnimatePresence mode="wait" initial={false}>
              <motion.span key={copied ? 'ok' : 'copy'} initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0, opacity: 0 }} transition={physics.playful} className="grid place-items-center">
                {copied ? <Check size={12} /> : <Copy size={12} />}
              </motion.span>
            </AnimatePresence>
          </button>
        </div>
      </div>
      {/* Named by the fence's own language when it declares one — the same word this block already
          shows in its header — and "Code" when it does not. */}
      {/* Inset ring, same reason as the diff block above — this is the one the sweep actually caught
          ("bash code", clipped left/right/bottom), and it is the highest-traffic of the family since
          every fenced block in every chat answer renders through it. */}
      <pre tabIndex={0} role="group" aria-label={lang ? `${lang} code` : 'Code'} data-type="body-s"
        className="overflow-x-auto px-m py-2 leading-relaxed focus-visible:-outline-offset-2"><code className="hljs font-mono" dangerouslySetInnerHTML={{ __html: html }} /></pre>
    </div>
  )
}

/** The schemes a link in rendered text may open. */
const WEB_LINK_PROTOCOLS = new Set(['http:', 'https:', 'mailto:'])

/** A link's destination if it is a web or email address, else undefined.
 *
 *  Parsed with the browser's own URL parser rather than matched as text, so this reads the
 *  scheme exactly where navigation will: the parser drops every tab and newline and trims
 *  control characters first, which is how `java<TAB>script:` is the script scheme to the page
 *  while a regex sees no scheme at all. A relative reference (a path on this gateway, a `#/`
 *  route, `//host`, a bare `host/path`) is refused too: it resolves against the dashboard's
 *  own origin, and nothing in a stored body gets to choose where inside this app a click
 *  lands. The returned value is the parser's serialisation — what the browser will follow. */
function webHref(href: string): string | undefined {
  let url: URL
  try { url = new URL(href.trim()) } catch { return undefined }
  return WEB_LINK_PROTOCOLS.has(url.protocol) ? url.href : undefined
}

/** A generated image in the artifact library: `/api/artifacts/<slug>/raw`, optionally with a
 *  `?version=N`. The one path on this gateway rendered text may load. */
const ARTIFACT_IMAGE = /^\/api\/artifacts\/[^/?#\s]+\/raw(?:\?[^#\s]*)?$/

/** An image's source if the page may load it, else undefined: an artifact image, or an https
 *  address. That is the page's own `img-src` minus what text never needs, so a body cannot
 *  make the dashboard fetch another path of its own API, and a plain-http image — which the
 *  policy refuses anyway — is not painted as a broken frame with a console error beside it. */
function imageSrc(src: string): string | undefined {
  const s = src.trim()
  if (ARTIFACT_IMAGE.test(s)) return s
  let url: URL
  try { url = new URL(s) } catch { return undefined }
  return url.protocol === 'https:' ? url.href : undefined
}

/** react-markdown's URL hook, and the whole URL policy: every `href`/`src` in the rendered tree
 *  passes through here before any component sees it. A value it refuses becomes '', which the
 *  `a` and `img` overrides render as their words. */
function markdownUrl(url: string, key: string, node: { tagName: string }): string {
  if (key === 'href' && node.tagName === 'a') return webHref(url) ?? ''
  if (key === 'src' && node.tagName === 'img') return imageSrc(url) ?? ''
  return ''
}

/** An image in rendered text, once `markdownUrl` has passed its source. */
function MarkdownImage({ src, alt, chatSessionKey }: { src?: unknown; alt?: unknown; chatSessionKey?: string }) {
  const text = typeof alt === 'string' ? alt : ''
  if (typeof src !== 'string' || !src) return <span className="text-on-surface-low italic">{text || 'image'}</span>
  if (ARTIFACT_IMAGE.test(src)) return <InlineArtifactImage src={src} alt={text} chatSessionKey={chatSessionKey} />
  return <WebImage src={src} alt={text} />
}

/** An https image. One that does not load says nothing about why — it may be gone, blocked or
 *  offline — so it falls back to its alt text, the same words a refused source shows. */
function WebImage({ src, alt }: { src: string; alt: string }) {
  const [failed, setFailed] = useState(false)
  if (failed) return <span className="text-on-surface-low italic">{alt || 'image'}</span>
  return (
    <img src={src} alt={alt} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)}
      className="my-2 max-h-[28rem] max-w-full rounded-lg border border-outline-variant/40 object-contain" />
  )
}

/** Heuristic: does an inline-code string look like a clickable file path? A path from the home
 *  starts `~/` (the gateway reads `~` as the home it runs with); `~name` is a name, not a path. */
const FILE_PATH_RE = /^(?:~\/|\.{0,2}\/)?[\w.\-]+(?:\/[\w.\-]+)*\.\w{1,8}\/?$/
function looksLikeFile(s: string): boolean {
  const t = s.trim()
  return t.length <= 200 && !t.includes(' ') && FILE_PATH_RE.test(t)
}

/** Does this `code` element sit inside a `<pre>`? That is the GROUND TRUTH for
 *  block-vs-inline, and the only signal that gets the single-line cases right:
 *  `mdast-util-to-hast` wraps every block code node — fenced with a language,
 *  fenced without one, and 4-space indented — in a `<pre>`, and never wraps an
 *  inline code span (whose value cannot contain a newline at all, since
 *  CommonMark folds line endings inside a span to spaces). Measured for all five
 *  shapes in `fencedCodeIsBlock.test.tsx`. Provided by the `pre` override below;
 *  read by both `code` call sites (COMPONENTS and componentsWith). */
const InPre = createContext(false)

/** The text of a `code` element's children, with react-markdown's trailing newline off.
 *
 *  🔴 An EMPTY fence has NO children at all — react-markdown hands a childless `<code>`
 *  `children === undefined` — and a bare `String(children)` turns that into the string
 *  "undefined", which then flowed into `<CodeBlock>` and PAINTED the word `undefined` in
 *  the transcript. Measured in a browser: zero non-2xx responses, zero console errors and
 *  zero gateway tracebacks, so the only place it was visible was the rendered surface.
 *  Nullish is the empty string; a fence whose content genuinely IS `undefined` is
 *  untouched, because this coerces the ABSENCE of children, never their value.
 *
 *  Shared by both `code` call sites (`COMPONENTS` and `componentsWith`) for the same
 *  reason `InPre` is: chat goes through the second one, so a coercion fixed in only one
 *  leaves the highest-traffic consumer painting the word. */
function codeText(children: unknown): string {
  return (children === undefined || children === null ? '' : String(children)).replace(/\n$/, '')
}

function renderCode({ className, children, inPre }: any) {
  const m = /language-(\w+)/.exec(className || '')
  const str = codeText(children)
  // Block vs inline. `inPre` decides it; the other two are independent fallbacks
  // for a raw-HTML `<code>` that rehype-raw hands us with no `<pre>` around it.
  //
  // 🔴 #2515: this used to be `!!className || str.includes('\n')` alone. A fence with
  // NO language has no className, and react-markdown's trailing newline is stripped
  // one line above — so a SINGLE-LINE no-language fence failed both tests and rendered
  // as an inline chip. Measured in a real browser: one 217-char unbreakable token in
  // such a fence became a 1424px `<code>` with `white-space: normal` and
  // `overflow-x: visible` inside a 338px region, so it could neither wrap nor scroll,
  // and it dragged every unrelated line in that result onto a 1424px canvas. The same
  // bytes plus one newline were already a contained, scrollable `<pre>`.
  const isBlock = inPre || !!className || str.includes('\n')
  if (!isBlock) return <code className="rounded-sm bg-surface-high px-1.5 py-0.5 text-[0.85em] font-mono text-primary-emphasis">{children}</code>
  const lang = m?.[1]
  if (lang === 'mermaid') return <MermaidBlock code={str} />
  if (isDiff(str, lang)) return <DiffBlock code={str} />
  return <CodeBlock code={str} lang={lang} />
}

const COMPONENTS: Record<string, React.ComponentType<any>> = {
  code(props: any) { return renderCode({ ...props, inPre: useContext(InPre) }) },
  // Still an unwrapper — the block renderers below bring their own `<pre>`. It only
  // marks the subtree, so the `code` child can tell a fence from an inline span.
  pre({ children }: any) { return <InPre.Provider value={true}>{children}</InPre.Provider> },
  table({ children }: any) { return <div className="my-3 overflow-x-auto"><table data-type="body-s" className="w-full border-collapse">{children}</table></div> },
  th({ children }: any) { return <th className="border-b border-outline-variant/50 bg-surface-high px-m py-2 text-left text-on-surface-var" style={fvs(500)}>{children}</th> },
  td({ children }: any) { return <td className="border-b border-outline-variant/30 px-m py-2">{children}</td> },
  a({ href, children }: any) {
    // `markdownUrl` has already emptied any href that is not a web or email address. Such a
    // link keeps its words, styled as the link it was written as, and goes nowhere.
    if (!href) return <span className="text-primary underline decoration-primary/40 underline-offset-2" title="Not opened: only web and email links open from here">{children}</span>
    return <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2 decoration-primary/40 hover:decoration-primary">{children}</a>
  },
  // Lazy and capped so an image cannot blow out the column; a refused source shows its alt
  // text, a deleted artifact its Regenerate placeholder. (componentsWith() threads the chat
  // session through for that placeholder.)
  img({ src, alt }: any) { return <MarkdownImage src={src} alt={alt} /> },
  blockquote({ children }: any) { return <blockquote className="my-2 border-l border-outline-variant pl-m italic text-on-surface-var">{children}</blockquote> },
  hr() { return <hr className="my-4 border-outline-variant/40" /> },
  h1({ children }: any) { return <h1 className="mt-4 mb-2 text-on-surface" data-type="headline-s">{children}</h1> },
  h2({ children }: any) { return <h2 className="mt-3 mb-2 text-on-surface text-[1.0625rem]" style={fvs(500)}>{children}</h2> },
  h3({ children }: any) { return <h3 className="mt-3 mb-1.5 text-on-surface text-[1.0625rem]" style={fvs(500)}>{children}</h3> },
  h4({ children }: any) { return <h4 data-type="title-m" className="mt-2 mb-1 text-on-surface">{children}</h4> },
  ul({ children }: any) { return <ul className="my-2 list-disc space-y-1 pl-7 marker:text-on-surface-low">{children}</ul> },
  ol({ children }: any) { return <ol className="my-2 list-decimal space-y-1 pl-7 marker:text-on-surface-low">{children}</ol> },
  li({ children }: any) { return <li data-type="body-m" className="leading-relaxed">{children}</li> },
  p({ children }: any) { return <p data-type="body-m" className="my-1.5 leading-relaxed">{children}</p> },
  strong({ children }: any) { return <strong className="text-on-surface" style={fvs(600)}>{children}</strong> },
  em({ children }: any) { return <em className="italic">{children}</em> },
}

/** The embedded HTML that may still format a body: tags that carry nothing but their name.
 *  Written with ANY attribute — a class, a style, a handler, an `open` — a tag is not one of
 *  these, and is shown as text like the rest. */
const EMBEDDED_FORMATTING = [
  'b', 'strong', 'i', 'em', 'u', 's', 'del', 'ins', 'mark', 'small', 'sub', 'sup', 'kbd', 'code',
  'br', 'details', 'summary',
] as const

const FORMATTING_TAG = new RegExp(`</?(?:${EMBEDDED_FORMATTING.join('|')})\\s*/?>`, 'gi')
const HTML_COMMENT = /<!--[\s\S]*?-->/g

/** The one hast node shape this pass reads. `mdast-util-to-hast` hands embedded HTML through
 *  as a `raw` node holding its source text, one per tag inline and one per HTML block. */
type HastLike = { type: string; value?: string; children?: HastLike[] }

/** Embedded HTML becomes text, before anything parses it as HTML.
 *
 *  Runs ahead of `rehype-raw`, and decides per `raw` node: a comment is dropped (a comment is
 *  not content); a node made of nothing but `EMBEDDED_FORMATTING` tags and text with no `<`
 *  in it is left for `rehype-raw` to turn into those elements; ANY other `<` makes the whole
 *  node a text node, which is what the page then shows. So the HTML parser only ever sees
 *  attribute-free formatting tags, and no reading of the rest — by this pass or by the
 *  parser — can differ from the other's: the rest never reaches the parser at all. */
function embeddedHtmlAsText() {
  const walk = (node: HastLike): void => {
    const kids = node.children
    if (!kids) return
    for (let i = kids.length - 1; i >= 0; i--) {
      const kid = kids[i]
      if (kid.type !== 'raw') { walk(kid); continue }
      const html = kid.value ?? ''
      const uncommented = html.replace(HTML_COMMENT, '')
      if (!uncommented.trim()) kids.splice(i, 1)
      else if (uncommented.replace(FORMATTING_TAG, '').includes('<')) kids[i] = { type: 'text', value: html }
      else kid.value = uncommented
    }
  }
  return walk
}

const REMARK: PluggableList = [remarkGfm, [remarkMath, { singleDollarTextMath: false }]]
const REHYPE: PluggableList = [embeddedHtmlAsText, [rehypeRaw, { passThrough: ['math', 'inlineMath'] }], rehypeKatex]
/** Inline sinks take the same HTML policy, and no LaTeX (see `inline` mode below). */
const REHYPE_INLINE: PluggableList = [embeddedHtmlAsText, rehypeRaw]

/** ── `inline` mode: the SAME renderer, for a sink that cannot hold a block ──────────────
 *
 *  🔴 SIX PROSE SINKS RENDERED MARKDOWN AS LITERAL TEXT (#2515) — app descriptions on the
 *  Store card and both detail panels, the Tools list row, app-config help, and tool
 *  parameter descriptions. Every one of them carries the SAME authored field the inspector
 *  already renders correctly through `<Markdown>` two lines above, so a description reading
 *  "Sync your **vault** via `rsync`" came out with its asterisks and backticks on screen.
 *
 *  Four of those six cannot take the block renderer, and the reasons are structural, not
 *  cosmetic:
 *   · `line-clamp-2` clamps the element that CARRIES the text flow (it is `-webkit-box`),
 *     so a nested `<p>` breaks the clamp outright and the card grows to the prose.
 *   · `Field`'s hint sink is a `<p>`; a `<div>` inside a `<p>` is invalid markup the
 *     parser closes early.
 *   · the Tools row is a `<button>`, so a rendered `<a>` there is `nested-interactive`
 *     (axe, serious) — links render as styled TEXT here, never anchors.
 *
 *  🪤 NOT A SECOND RENDERER. Same `Markdown` entry point, same remark pipeline, same HTML and
 *  URL policy, same component vocabulary — block containers are unwrapped and flattened to
 *  running text, so a heading or a table in a two-line description degrades to its words
 *  instead of reflowing the grid. Two deliberate differences:
 *   · no `rehype-katex`: these sinks carry manifest prose — `app.json` descriptions, MCP tool
 *     and parameter descriptions — where a `$` is a price, not LaTeX.
 *   · no colour of its own. Each sink owns its ink (`text-on-surface-low`,
 *     `text-on-surface-var`, caption), so inline mode inherits rather than forcing
 *     `text-on-surface` the way the block wrapper does. That is what keeps these six
 *     visually unchanged apart from the formatting they were missing.
 */
const INLINE_UNWRAP = [
  'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'hr', 'br', 'img', 'pre',
  'ul', 'ol', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'details', 'summary',
]

const INLINE_COMPONENTS: Record<string, React.ComponentType<any>> = {
  // Always the inline pill — `renderCode` resolves a fenced block to a `<div>`/`<pre>`
  // surface (and a ```mermaid fence to a diagram), which is precisely what must not
  // appear inside a clamped one-liner.
  // `px-xs`, not the block renderer's raw `px-1.5 py-0.5`: this chip is new code, so it takes the
  // tokenised rung and rides the density slider (`design/spacingTokenRamp.test.ts`). It pads tighter
  // than block mode deliberately — these sinks are one or two clamped lines of running prose, and
  // there is no vertical padding because a taller chip is what would push a clamped row around.
  code({ children }: any) { return <code className="rounded-sm bg-surface-high px-xs text-[0.85em] font-mono text-primary-emphasis">{children}</code> },
  strong({ children }: any) { return <strong style={fvs(600)}>{children}</strong> },
  em({ children }: any) { return <em className="italic">{children}</em> },
  del({ children }: any) { return <del className="opacity-70">{children}</del> },
  // The list wrapper is unwrapped, so the items land directly in the flow — a trailing
  // space keeps "onetwothree" from happening.
  li({ children }: any) { return <span>{children}{' '}</span> },
  // Inert by design: three of the four inline sinks sit inside a click target.
  a({ children }: any) { return <span className="underline underline-offset-2 decoration-current/40">{children}</span> },
}

// Bare file paths inside prose (not just inline-code): /a/b.ext, ~/a/b.ext, ~/b.ext, or
// workspace-relative a/b.ext with an extension. Conservative to avoid prose.
const BARE_FILE_RE = /((?:~\/|\.{0,2}\/)?[\w.\-]+(?:\/[\w.\-]+)+\.\w{1,8}|~\/[\w.\-]+\.\w{1,8})/g

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

// `[Memory N]` / `[Lesson N]` citation tokens the model emits when it used an injected
// episodic memory or a recalled lesson. Bounded index so a stray "[Memory 999999]"
// can't match something absurd; resolution against the manifest is what actually
// gates whether a chip renders.
const MEMORY_CITE_RE = /\[(Memory|Lesson) (\d{1,4})\]/g

/** The token a manifest entry answers to: an episode is `Memory N`, a lesson `Lesson N`. The two
 *  are numbered apart, so the kind is part of the key. */
const citeLabel = (c: MemoryCitation) => `${c.kind === 'lesson' ? 'Lesson' : 'Memory'} ${c.n}`

/** Where a chip opens its source in the Memory studio: the episode, or the lesson by its rule. */
const citeSel = (c: MemoryCitation) => (c.kind === 'lesson' ? `lesson:${c.id}` : `epi:${c.id}`)

/** Turn `[Memory N]` and `[Lesson N]` tokens in a markdown text node into deep-link chips to
 *  the cited episode or lesson. `N` resolves against the turn's citation manifest; an
 *  unresolvable index (hallucinated, or a memory with no record id) degrades to
 *  the plain text so a bad citation is never a broken link. Mirrors linkifyFiles:
 *  operates only on plain strings, leaving already-rendered React children alone. */
function linkifyMemory(children: any, citations: MemoryCitation[]): any {
  const byLabel = new Map(citations.map((c) => [citeLabel(c), c]))
  return (Array.isArray(children) ? children : [children]).flatMap((child, ci) => {
    if (typeof child !== 'string') return [child]
    const parts: any[] = []
    let last = 0, m: RegExpExecArray | null
    MEMORY_CITE_RE.lastIndex = 0
    while ((m = MEMORY_CITE_RE.exec(child)) !== null) {
      const label = `${m[1]} ${Number(m[2])}`
      const cite = byLabel.get(label)
      if (m.index > last) parts.push(child.slice(last, m.index))
      // No manifest entry, or an entry with no record id → leave the literal token
      // (honest: we can't point anywhere). A resolvable one becomes a compact chip.
      if (!cite || !cite.id) {
        parts.push(m[0])
      } else {
        const href = `#/settings/memory?tab=studio&sel=${encodeURIComponent(citeSel(cite))}`
        parts.push(
          <a key={`${ci}-${m.index}`} href={href} title={cite.preview || label}
            className="mx-0.5 inline-flex items-baseline rounded-sm bg-surface-high px-1.5 align-baseline text-[0.8em] text-primary-emphasis no-underline decoration-primary/40 transition-colors hover:bg-surface-highest hover:underline">
            {label}
          </a>,
        )
      }
      last = m.index + m[0].length
    }
    if (last < child.length) parts.push(child.slice(last))
    return parts.length ? parts : [child]
  })
}

/** When `onFileClick` is supplied, file mentions become clickable: inline-code
 *  that looks like a path renders as a link, AND bare paths inside prose
 *  (paragraphs / list items) are linkified — so file mentions are interactive
 *  right where they're read, whether the model used backticks or not. When
 *  `citations` is supplied, `[Memory N]` tokens become deep-link chips too. */
function componentsWith(
  onFileClick?: (path: string) => void,
  chatSessionKey?: string,
  citations?: MemoryCitation[],
): Record<string, React.ComponentType<any>> {
  if (!onFileClick && !chatSessionKey && !(citations && citations.length)) return COMPONENTS
  const base: Record<string, React.ComponentType<any>> = { ...COMPONENTS }
  // Scope the inline-image renderer to the chat session so a deleted image's
  // placeholder can offer "Regenerate" (re-runs at the same slug, server recovers
  // the prompt from this session's tool history).
  if (chatSessionKey) {
    base.img = ({ src, alt }: any) => <MarkdownImage src={src} alt={alt} chatSessionKey={chatSessionKey} />
  }
  // Combined text transform: linkify file paths (when enabled) THEN resolve
  // `[Memory N]` citation chips (when a manifest is present). Order is safe —
  // linkifyMemory only touches remaining plain strings, leaving the file-link
  // buttons linkifyFiles produced untouched. No file-click and no citations →
  // identity, so p/li/td stay byte-identical to COMPONENTS.
  const cites = citations && citations.length ? citations : null
  const L = (children: any) => {
    let out = onFileClick ? linkifyFiles(children, onFileClick) : children
    if (cites) out = linkifyMemory(out, cites)
    return out
  }
  return {
    ...base,
    code({ className, children }: any) {
      // Read unconditionally (Rules of Hooks) — the file-path branch below returns early.
      // This is the SECOND `renderCode` call site; a fix that only threads the signal into
      // COMPONENTS would leave chat, the highest-traffic consumer, on the old predicate.
      const inPre = useContext(InPre)
      const str = codeText(children)
      if (onFileClick && !className && looksLikeFile(str)) {
        return (
          <button type="button" onClick={() => onFileClick(str.trim())} title={`Open ${str.trim()}`}
            className="rounded-sm bg-surface-high px-1.5 py-0.5 align-baseline text-[0.85em] font-mono text-primary-emphasis underline decoration-primary/40 underline-offset-2 transition-colors hover:bg-surface-highest hover:decoration-primary">
            {children}
          </button>
        )
      }
      return renderCode({ className, children, inPre })
    },
    p({ children }: any) { return <p data-type="body-m" className="my-1.5 leading-relaxed">{L(children)}</p> },
    li({ children }: any) { return <li data-type="body-m" className="leading-relaxed">{L(children)}</li> },
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
function MarkdownText({ children, onFileClick, chatSessionKey, citations }: {
  children: string; onFileClick?: (path: string) => void; chatSessionKey?: string; citations?: MemoryCitation[]
}) {
  return <ReactMarkdown remarkPlugins={REMARK} rehypePlugins={REHYPE} urlTransform={markdownUrl} components={componentsWith(onFileClick, chatSessionKey, citations)}>{children}</ReactMarkdown>
}

export const Markdown = memo(function Markdown({ children, className, inline, widgets, onFileClick, chatSessionKey, messageTs, streaming, citations }: {
  children: unknown; className?: string; onFileClick?: (path: string) => void
  /** Render into a `<span>` with block containers flattened, for a sink that cannot hold a
   *  block — a `line-clamp`-ed card description, a `<p>`-typed field hint, or prose inside a
   *  click target. See `INLINE_UNWRAP` above for what it costs and why. */
  inline?: boolean
  /** Run `<widget>` blocks in their sandboxed frames. For the agent's own chat replies only —
   *  the dashboard chat is where the model is given the widget contract. Without it a widget
   *  tag is embedded HTML like any other, and is shown as text. */
  widgets?: boolean
  /** Chat session key — enables "Regenerate" on a deleted inline image's placeholder
   *  (re-runs at the same slug; server recovers the prompt from this session). */
  chatSessionKey?: string
  /** stable per-message timestamp → derived widget slugs survive refresh. */
  messageTs?: string
  /** still streaming → render an unclosed trailing `<widget>` progressively. */
  streaming?: boolean
  /** episodic memory manifest for THIS turn — resolves `[Memory N]` citation chips.
   *  Absent → tokens render as plain text. */
  citations?: MemoryCitation[]
}) {
  // Defensive: callers occasionally pass agent/tool-authored content that isn't
  // a clean string (an object/array where a doc was expected). Coerce so a stray
  // shape renders as readable text instead of crashing React (#31).
  const text = typeof children === 'string' ? children : stringifyChildren(children)
  if (!text.trim()) return null
  // An inline sink is a clamp/hint/click-target, never a widget host, so it takes the
  // flattening pass and no `<div>` and no colour of its own (the sink owns its ink).
  if (inline) {
    return (
      <span className={className}>
        <ReactMarkdown remarkPlugins={REMARK} rehypePlugins={REHYPE_INLINE} urlTransform={markdownUrl} disallowedElements={INLINE_UNWRAP} unwrapDisallowed components={INLINE_COMPONENTS}>{text}</ReactMarkdown>
      </span>
    )
  }
  // Split out `<widget>` blocks where the caller runs them; render each as a sandboxed
  // iframe, prose as MD.
  const segments = widgets ? parseWidgetBlocks(text, streaming) : null
  if (!segments || (segments.length === 1 && segments[0].type === 'md')) {
    return <div className={`text-on-surface ${className ?? ''}`}><MarkdownText onFileClick={onFileClick} chatSessionKey={chatSessionKey} citations={citations}>{text}</MarkdownText></div>
  }
  let wi = 0
  return (
    // flow-root: widgets float left when their natural width is narrow enough
    // (the WidgetFrame measures the host column itself); the formatting context
    // keeps the float contained inside THIS turn.
    <div className={`flow-root text-on-surface ${className ?? ''}`}>
      {segments.map((seg, i) => {
        if (seg.type !== 'widget') return <MarkdownText key={i} onFileClick={onFileClick} chatSessionKey={chatSessionKey} citations={citations}>{seg.content}</MarkdownText>
        // Resolve the block's renderer through the ONE content registry (was a
        // hardcoded react/widget fork): adding an inline-embeddable type is now a
        // registry entry, not an edit here.
        const embed = embedFor(seg.kind)
        if (!embed) return null
        // A non-streaming embed (react: its JSX compiles whole) has no partial-render mode — hold it
        // until the closing tag arrives; a streaming one paints its partial body.
        if (!embed.streaming && !seg.complete) return null
        const widgetIndex = wi++
        return createElement(embed.render, { key: i, content: seg.html, title: seg.title, slug: seg.slug, messageTs, widgetIndex, streaming: !seg.complete })
      })}
    </div>
  )
})
