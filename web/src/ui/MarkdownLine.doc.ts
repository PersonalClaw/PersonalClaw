import type { UiDoc } from './uiDoc'

// Doc object for MarkdownLine: the one sink for a field written as one line that carries markdown.
const doc: UiDoc = {
  name: 'MarkdownLine',
  keywords: ['markdown', 'line', 'title', 'name', 'label', 'reason', 'row', 'truncate', 'clamp', 'inline', 'search', 'hit'],
  description:
    "A field written as one line, rendered: a title, a name, a label, a reason, a question, a purpose, a criterion or a step that an agent, a tool or a person wrote, and that carries markdown. It is the sink element itself (`as`), with `<Markdown line>` inside, so the parse starts no block: a leading \"#\", \"2024.\", \"1)\", \"-\" or \">\" and a \"[Bug]: crash\" are the text they are, and only inline marks render (a code span is the inline pill, a link is styled text and never an anchor). The sink's own `truncate` or `line-clamp` cuts the rendered row, it can sit inside a button or a row's hit target, and where it cuts, its tooltip is the whole line as plain text.",
  props: [
    { name: 'text', description: 'The field as it was written. Nothing, or only space, draws the sink empty.' },
    { name: 'as', description: 'The sink element: span (default), div, p, h2, h3 or li.' },
    { name: 'className', description: "The sink's own ink, type role and cut (`truncate`, `line-clamp-2`): MarkdownLine carries no colour of its own." },
    { name: 'tooltip', description: "The sink's tooltip. By default the whole line as plain text (`previewLine`) when `className` cuts it, and none otherwise; `false` for none." },
    { name: 'hits', description: "The search index's `<<…>>` marks become `<mark>`s, inside a code span or across a bold run too; an unpaired mark stays text. The tooltip drops the marks." },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for MarkdownLine for any one-line field that came from an agent, a tool or a person (a task title, a step, a reason, a question): as one component, its adoption can be counted by its name.' },
    { guidance: true, description: "Put the sink's truncate or line-clamp on MarkdownLine's own className, so the rendered row is what is cut." },
    { guidance: true, description: "Build an accessible name or a tooltip from the same text through `previewLine`, at the call site: no mark reaches an attribute." },
    { guidance: false, description: 'Do not use it for a multi-line body: that is `<Markdown>` in a div (with `breaks` for text written as lines), never inside a p, a button or a clamp.' },
    { guidance: false, description: "Do not pass the app's own copy through it: our labels and hints are not markdown." },
  ],
  anatomy: ['the sink element (`as`) with its own className and tooltip', '`<Markdown line>`: a span of inline marks only'],
}

export default doc
