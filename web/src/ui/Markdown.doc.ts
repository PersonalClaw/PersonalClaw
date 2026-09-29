import type { UiDoc } from './uiDoc'

// Doc object for Markdown — the one renderer for markdown the app did not write. The
// Markdown-only policy (embedded HTML shown as text, web-and-email links, https and artifact
// images), the opt-in widget contract, and the "coerce non-string children instead of
// crashing" defensiveness were all source comments — encoded here as machine-readable data.
const doc: UiDoc = {
  name: 'Markdown',
  keywords: ['markdown', 'renderer', 'chat', 'message', 'code', 'latex', 'mermaid', 'widget', 'tables', 'diff', 'untrusted', 'knowledge'],
  description:
    'The one renderer for markdown the app did not write — model replies, tool results, knowledge bodies, inbox messages, app and tool descriptions, release notes — and it renders it as Markdown only: embedded HTML is shown as the text it is (attribute-free formatting tags such as `<kbd>`, `<br>` and `<sub>` aside), a link opens only for http, https and mailto, and an image loads only from https or the artifact library. react-markdown + remark-gfm (tables, task lists, strikethrough), remark-math + rehype-katex (LaTeX), and highlight.js (code), with ```mermaid diagrams, ```diff highlighting, copy/Run-in-terminal code affordances, inline artifact images (with a Regenerate fallback for deleted ones), and — where the caller opts in — `<widget>` blocks rendered as sandboxed theme-aware iframes. All component overrides are token-driven.',
  props: [
    { name: 'chatSessionKey', description: "Scopes the inline-image history lookup so a deleted image's placeholder can offer Regenerate (re-runs at the same slug; server recovers the prompt from this session); absent, the placeholder is static." },
    { name: 'children', description: 'The markdown source. Non-string input (an object/array an agent emitted) is defensively flattened to readable text instead of crashing.' },
    { name: 'citations', description: "Episodic memory manifest for the turn ({n, id, preview}[]). When supplied, `[Memory N]` tokens in the prose become chips deep-linking to the cited episode; a token with no matching entry (or a null id) degrades to plain text, never a broken link. Absent → tokens render verbatim." },
    { name: 'className', description: 'Extra classes on the wrapper (tokens only — no raw hex/px).' },
    { name: 'inline', description: 'Renders into a `<span>` with every block container flattened (paragraphs, headings, lists, tables, pre, images), keeping only the inline marks — for a sink that cannot legally or visually hold a block: a `line-clamp`-ed card description, a `<p>`-typed field hint, or prose inside a click target. Carries no colour of its own, so the sink keeps its ink. The HTML and URL policy is the same as the block renderer\'s.' },
    { name: 'messageTs', description: 'Stable per-message timestamp → derived widget slugs survive a refresh (with `widgets`).' },
    { name: 'onFileClick', description: 'When supplied, file mentions become clickable — path-like inline code AND bare paths in prose linkify to fire this with the path.' },
    { name: 'streaming', description: 'True while the message is still streaming → an unclosed trailing `<widget>` renders progressively (with `widgets`).' },
    { name: 'widgets', description: 'Runs `<widget>` blocks in their sandboxed frames. Only the agent\'s own chat replies pass it — the dashboard chat is where the model is given the widget contract. Without it a widget tag is embedded HTML like any other and is shown as text.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for Markdown to render any text that came from a model, a tool, an app manifest, a feed, a page, a file or another person — it is the one rich-content path for stored and remote text, and a rail fails a second one.' },
    { guidance: true, description: 'Pass `chatSessionKey` on chat surfaces so a deleted inline image degrades to a Regenerate placeholder instead of a broken image; pass `messageTs` so widget slugs survive refresh.' },
    { guidance: true, description: 'Pass `onFileClick` when file mentions should be interactive — both backticked and bare paths become clickable right where they are read.' },
    { guidance: true, description: 'Pass `citations` on chat surfaces so a memory-backed reply\'s `[Memory N]` markers become deep-link chips; resolution is by record id from the manifest, so the model can never point a citation at the wrong record.' },
    { guidance: true, description: 'Pass `inline` when the sink is a clamped line, a `<p>`, or the inside of a button — a block child escapes a `-webkit-box` line-clamp and a `<div>` inside a `<p>` is hoisted out of it by the parser. Anywhere the sink is a `<div>` that may hold structure, use the block renderer so tables and code blocks still render.' },
    { guidance: false, description: 'Do not pass `widgets` anywhere but the agent\'s own chat reply. A knowledge body, a tool result or an inbox message holding a `<widget>` tag is someone else\'s markup, and it stays text.' },
    { guidance: false, description: 'Do not render markdown WE authored (a hint sentence, a label, an error) through this renderer — our own prose is not markdown, and a `snake_case` identifier in it would come out emphasised. Reach for it when the text came from an app manifest, a tool schema or a model.' },
    { guidance: false, description: 'Do not pre-sanitize, pre-escape or hand-filter the text or its links — the renderer shows embedded HTML as text and opens only http, https and mailto links on its own, and a second filter in front of it only garbles what a reader sees.' },
  ],
  anatomy: ['wrapper div (flow-root when widgets present)', 'MarkdownText (ReactMarkdown with token-driven component overrides)', 'embedded-HTML pass (text, except attribute-free formatting tags)', 'URL policy (web/email links, https and artifact images)', 'CodeBlock (highlight.js + copy / Run-in-terminal)', 'DiffBlock (+/- line tinting)', 'MermaidBlock', 'InlineArtifactImage (404 → Regenerate placeholder)', 'linkified file buttons', 'sandboxed `<widget>` iframe embeds (opt-in)'],
}

export default doc
