import type { UiDoc } from './uiDoc'

// Doc object for Markdown — the full chat/content markdown renderer. The safe-href
// allowlist rationale, the trusted-source-but-sandboxed-widget contract, and the
// "coerce non-string children instead of crashing" defensiveness were all source
// comments — encoded here as machine-readable data.
const doc: UiDoc = {
  name: 'Markdown',
  keywords: ['markdown', 'renderer', 'chat', 'message', 'code', 'latex', 'mermaid', 'widget', 'tables', 'diff'],
  description:
    'The full markdown renderer for agent/message content: react-markdown + remark-gfm (tables, task lists, strikethrough), remark-math + rehype-katex (LaTeX), rehype-raw (inline HTML), and highlight.js (code), with ```mermaid diagrams, ```diff highlighting, copy/Run-in-terminal code affordances, safe-href-gated links, inline artifact images (with a Regenerate fallback for deleted ones), and `<widget>` blocks rendered as sandboxed theme-aware iframes. All component overrides are token-driven. Reach for it to render any trusted markdown body in the app.',
  props: [
    { name: 'chatSessionKey', description: "Scopes the inline-image history lookup so a deleted image's placeholder can offer Regenerate (re-runs at the same slug; server recovers the prompt from this session); absent, the placeholder is static." },
    { name: 'children', description: 'The markdown source. Non-string input (an object/array an agent emitted) is defensively flattened to readable text instead of crashing.' },
    { name: 'citations', description: "Episodic memory manifest for the turn ({n, id, preview}[]). When supplied, `[Memory N]` tokens in the prose become chips deep-linking to the cited episode; a token with no matching entry (or a null id) degrades to plain text, never a broken link. Absent → tokens render verbatim." },
    { name: 'className', description: 'Extra classes on the wrapper (tokens only — no raw hex/px).' },
    { name: 'inline', description: 'Renders into a `<span>` with every block container flattened (paragraphs, headings, lists, tables, pre, images), keeping only the inline marks — for a sink that cannot legally or visually hold a block: a `line-clamp`-ed card description, a `<p>`-typed field hint, or prose inside a click target. Carries no colour of its own, so the sink keeps its ink.' },
    { name: 'messageTs', description: 'Stable per-message timestamp → derived widget slugs survive a refresh.' },
    { name: 'onFileClick', description: 'When supplied, file mentions become clickable — path-like inline code AND bare paths in prose linkify to fire this with the path.' },
    { name: 'streaming', description: 'True while the message is still streaming → an unclosed trailing `<widget>` renders progressively.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for Markdown to render any agent/message content rather than hand-rolling react-markdown — the GFM/math/code/mermaid/diff/widget/link-safety stack and token-driven styling are all wired in.' },
    { guidance: true, description: 'Pass `chatSessionKey` on chat surfaces so a deleted inline image degrades to a Regenerate placeholder instead of a broken image; pass `messageTs` so widget slugs survive refresh.' },
    { guidance: true, description: 'Pass `onFileClick` when file mentions should be interactive — both backticked and bare paths become clickable right where they are read.' },
    { guidance: true, description: 'Pass `citations` on chat surfaces so a memory-backed reply\'s `[Memory N]` markers become deep-link chips; resolution is by record id from the manifest, so the model can never point a citation at the wrong record.' },
    { guidance: true, description: 'Pass `inline` when the sink is a clamped line, a `<p>`, or the inside of a button — a block child escapes a `-webkit-box` line-clamp and a `<div>` inside a `<p>` is hoisted out of it by the parser. Anywhere the sink is a `<div>` that may hold structure, use the block renderer so tables and code blocks still render.' },
    { guidance: false, description: 'Do not render markdown WE authored (a hint sentence, a label, an error) through this renderer — our own prose is not markdown, and a `snake_case` identifier in it would come out emphasised. Reach for it when the text came from an app manifest, a tool schema or a model.' },
    { guidance: false, description: 'Do not pre-sanitize or hand-filter hrefs — the renderer allowlists safe schemes and neutralizes javascript:/data:/vbscript: to inert text (source is trusted, but worker-authored content can echo malicious links).' },
    { guidance: false, description: 'Do not render untrusted third-party HTML through Markdown — inline HTML passes through rehype-raw unsanitized; only `<widget>` blocks are sandboxed in iframes.' },
  ],
  anatomy: ['wrapper div (flow-root when widgets present)', 'MarkdownText (ReactMarkdown with token-driven component overrides)', 'CodeBlock (highlight.js + copy / Run-in-terminal)', 'DiffBlock (+/- line tinting)', 'MermaidBlock', 'InlineArtifactImage (404 → Regenerate placeholder)', 'safe-href link / linkified file buttons', 'sandboxed `<widget>` iframe embeds'],
}

export default doc
