import type { UiDoc } from './uiDoc'

// Doc objects for Markdown — the one renderer for markdown the app did not write — and for the
// RunnableCode context it reads, so this file default-exports an array. The Markdown-only policy
// (embedded HTML shown as text, web-and-email links, artifact images, and a Load placeholder for
// any other image), the opt-in widget and Run contracts, and the "coerce non-string children
// instead of crashing" defensiveness were all source comments — encoded here as machine-readable
// data.
const doc: UiDoc = {
  name: 'Markdown',
  keywords: ['markdown', 'renderer', 'chat', 'message', 'code', 'latex', 'mermaid', 'widget', 'tables', 'diff', 'untrusted', 'knowledge'],
  description:
    'The one renderer for markdown the app did not write — model replies, tool results, knowledge bodies, inbox messages, app and tool descriptions, release notes — and it renders it as Markdown only: embedded HTML is shown as the text it is (attribute-free formatting tags such as `<kbd>`, `<br>` and `<sub>` aside), a link opens only for http, https and mailto, and an image loads only from the artifact library: an https image is a placeholder naming its host, loaded only when the owner presses Load. react-markdown + remark-gfm (tables, task lists, strikethrough), remark-math + rehype-katex (LaTeX), and highlight.js (code), with ```mermaid diagrams, ```diff highlighting, Copy on every code block, and Run on a shell block only under `RunnableCode` (the agent\'s own chat reply), inline artifact images (with a Regenerate fallback for deleted ones), and — where the caller opts in — `<widget>` blocks rendered as sandboxed theme-aware iframes. All component overrides are token-driven.',
  props: [
    { name: 'chatSessionKey', description: "Scopes the inline-image history lookup so a deleted image's placeholder can offer Regenerate (re-runs at the same slug; server recovers the prompt from this session); absent, the placeholder is static." },
    { name: 'children', description: 'The markdown source. Non-string input (an object/array an agent emitted) is defensively flattened to readable text instead of crashing.' },
    { name: 'citations', description: "Episodic memory manifest for the turn ({n, id, preview}[]). When supplied, `[Memory N]` tokens in the prose become chips deep-linking to the cited episode; a token with no matching entry (or a null id) degrades to plain text, never a broken link. Absent → tokens render verbatim." },
    { name: 'className', description: "Extra classes on the wrapper (tokens only — no raw hex/px). The wrapper's overflow, and in Safari its padding and margin, belong to `.md-clip` and no utility changes them: space or scroll a body from a wrapper of your own." },
    { name: 'inline', description: 'Renders into a `<span>` with every block container flattened (paragraphs, headings, lists, tables, pre, footnote sections, task-list checkboxes), keeping only the inline marks, and an image as its alt text (nothing is fetched) — for a sink that cannot legally or visually hold a block: a `line-clamp`-ed card description, a `<p>`-typed field hint, or prose inside a click target. Carries no colour of its own, so the sink keeps its ink. The HTML and URL policy is the same as the block renderer\'s.' },
    { name: 'line', description: 'A field written as one line (a title, a name, a reason, a question, a step): inline mode whose parse starts no block. ATX and setext headings, thematic breaks, lists, block quotes, indented and fenced code, definitions, GFM tables and footnote definitions, math blocks and HTML blocks are turned off at parse time by their micromark names, so a leading "#", "2024.", "1)", "-" or ">" and a "[Bug]: crash" are the text they are. Implies `inline`. Most callers want `MarkdownLine`, which wraps it in the sink element.' },
    { name: 'breaks', description: 'Block mode: a soft line ending renders as a line break, for text written as lines (a message, a note, a question, reasoning, evidence) whose sink showed its line breaks while it was raw. A markdown document (a memory file, a skill, a prompt, a knowledge body) renders without it, as the transcript does.' },
    { name: 'hits', description: "Inline and line modes: the search index's `<<…>>` marks become `<mark>`s. Each paired mark becomes a sentinel before the parse and a mark after the HTML pass, so a hit inside a code span or across a bold run stays marked, and a snippet's own `<` still goes through the HTML pass. An unpaired mark stays text." },
    { name: 'messageTs', description: 'Stable per-message timestamp → derived widget slugs survive a refresh (with `widgets`).' },
    { name: 'onFileClick', description: 'When supplied, file mentions become clickable — path-like inline code AND bare paths in prose linkify to fire this with the path.' },
    { name: 'streaming', description: 'True while the message is still streaming → an unclosed trailing `<widget>` renders progressively (with `widgets`).' },
    { name: 'sourceOf', description: "The number of the source a link's address is, or undefined for a link that is no source. When supplied, each link it numbers carries that number beside it (a `sup` with `data-source-n`); every other link renders as it would without it. A chat reply passes its turn's sources (`pages/chat/turnSources`): the links it cites that its own turn retrieved. Keep the function stable across renders: a new one is a new set of components. Absent → no link is badged." },
    { name: 'beside', description: 'Under an `OpenBesideProvider`, each code block, diff and table offers an Open beside and registers under its address, its kind and a fingerprint of its parsed content (a fence\'s language and text, a table\'s cells), so the same block has the same address wherever it sits and however the text around it is split. Not while `streaming`: a block still arriving has no end, so it neither offers nor registers. Absent → no block offers it.' },
    { name: 'widgets', description: 'Runs `<widget>` blocks in their sandboxed frames. Only the agent\'s own chat replies pass it, never a row the gateway took in from outside (`meta.from_outside`) — the dashboard chat is where the model is given the widget contract. Without it a widget tag is embedded HTML like any other and is shown as text.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for Markdown to render any text that came from a model, a tool, an app manifest, a feed, a page, a file or another person — it is the one rich-content path for stored and remote text, and a rail fails a second one.' },
    { guidance: true, description: 'Pass `chatSessionKey` on chat surfaces so a deleted inline image degrades to a Regenerate placeholder instead of a broken image; pass `messageTs` so widget slugs survive refresh.' },
    { guidance: true, description: 'Pass `onFileClick` when file mentions should be interactive — both backticked and bare paths become clickable right where they are read.' },
    { guidance: true, description: 'Pass `citations` on chat surfaces so a memory-backed reply\'s `[Memory N]` markers become deep-link chips; resolution is by record id from the manifest, so the model can never point a citation at the wrong record.' },
    { guidance: true, description: 'Pass `inline` when the sink is a clamped line, a `<p>`, or the inside of a button — a block child escapes a `-webkit-box` line-clamp and a `<div>` inside a `<p>` is hoisted out of it by the parser. Anywhere the sink is a `<div>` that may hold structure, use the block renderer so tables and code blocks still render.' },
    { guidance: true, description: 'A one-line field (a title, a name, a label, a reason, a question, a step) takes `MarkdownLine`: the sink element with `<Markdown line>` inside, its tooltip the plain line. An attribute, a live region, an OS banner, a select label or a placeholder takes `previewText` (a body) or `previewLine` (a one-line field) instead, since no mark or tag may reach it.' },
    { guidance: true, description: 'A string with nothing markdown acts on renders as itself in inline and line modes, with no parse (`plainLine`), so a long list of plain titles stays cheap; cut a rendered string with `cutMarkdown` or `cutMarkdownLine`, never a bare slice, so no fence, code span or emphasis is left open.' },
    { guidance: false, description: 'Do not pass `widgets` anywhere but the agent\'s own chat reply. A knowledge body, a tool result or an inbox message holding a `<widget>` tag is someone else\'s markup, and it stays text. A row the gateway took in from outside (`meta.from_outside`) is drawn without them.' },
    { guidance: false, description: "Do not provide `RunnableCode` anywhere but the agent's own chat reply. A shell block in a message, a notification, a tool result, a knowledge body or a skill offers Copy." },
    { guidance: false, description: 'Do not render markdown WE authored (a hint sentence, a label, an error) through this renderer — our own prose is not markdown, and a `snake_case` identifier in it would come out emphasised. Reach for it when the text came from an app manifest, a tool schema or a model.' },
    { guidance: false, description: 'Do not put block mode in a `<p>`, a button or a clamped sink: a block child escapes a line-clamp, and a rendered link inside a button is a control in a control. Use `line` or `inline` there.' },
    { guidance: false, description: 'Do not pre-sanitize, pre-escape or hand-filter the text or its links — the renderer shows embedded HTML as text and opens only http, https and mailto links on its own, and a second filter in front of it only garbles what a reader sees.' },
  ],
  anatomy: ['wrapper div (flow-root when widgets present)', 'MarkdownText (ReactMarkdown with token-driven component overrides)', 'embedded-HTML pass (text, except attribute-free formatting tags)', 'URL policy (web/email links, https and artifact images)', 'CodeBlock (highlight.js + Copy; Run under `RunnableCode`)', 'DiffBlock (+/- line tinting)', 'MermaidBlock', 'InlineArtifactImage (404 → Regenerate placeholder)', 'linkified file buttons', 'sandboxed `<widget>` iframe embeds (opt-in)'],
}

const runnableCode: UiDoc = {
  name: 'RunnableCode',
  keywords: ['markdown', 'code', 'shell', 'run', 'terminal', 'chat', 'reply', 'context', 'provider'],
  description:
    "Whether a shell code block offers Run beside Copy. The agent's own reply in a chat (`pages/chat/transcript/AssistantSegments`) provides `true` around its prose, and nothing else does: a code block anywhere else, a tool's card in the same turn included, offers Copy. A row the gateway took in from outside (`meta.from_outside`, such as a scheduled run's result) is drawn with false, as that result, not as the agent's reply. A block opened beside the reading keeps the value it had where it was written. Usage: `<RunnableCode.Provider value>`. Defaults to false.",
  // Empty by contract, as CornersClearedAbove's: this is React's own context, so it declares no
  // props of its own and the extractor derives none. `value` is described above instead.
  props: [],
  bestPractices: [
    { guidance: true, description: "Provide it around the agent's own reply prose only, never a tool's card or the whole turn." },
    { guidance: false, description: 'Do not provide it around a message, a notification, a tool result, a knowledge body, a skill or an app\'s text: their code is copied, not run.' },
  ],
  anatomy: ['React context carrying one boolean (no DOM of its own)'],
}

export default [doc, runnableCode]
