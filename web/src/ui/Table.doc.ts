import type { UiDoc } from './uiDoc'

// Doc objects for the Table family — the canonical data table. Encodes the
// accessibility floor (sr-only caption, scope="col" headers) that eleven
// hand-rolled tables kept dropping (audit AB-3), so an app-building agent
// reaches for the family instead of raw <table> markup.
const docs: UiDoc[] = [
  {
    name: 'Table',
    keywords: ['table', 'data table', 'grid', 'rows', 'columns', 'caption', 'tabular'],
    description:
      'The one canonical data-table shell: an overflow-x-auto wrapper around a full-width caption-tier <table> whose sr-only caption is REQUIRED — a screen reader announces what the table holds before its grid. It carries the accessible shape learning/AblationPanel.tsx already had right, so pages stop hand-rolling <table> markup that drops the caption (audit AB-3). Layout decisions (column alignment, row inks, zebra) stay with each consumer.',
    props: [
      { name: 'sized', description: 'Emit the seed type size (text-[0.75rem]). Default true; set false when the table genuinely reads at another size and bring your own text utility — two text-size utilities would race.' },
      { name: 'caption', description: "One sentence naming what the table holds — rendered sr-only so a screen reader announces the table's purpose. Required: an anonymous grid is the drift this family retires." },
      { name: 'className', description: 'Utilities for the <table> element itself (e.g. a larger text size where a page genuinely reads denser). Defaults carry the seed treatment: w-full, caption-tier text.' },
      { name: 'wrapClassName', description: 'Utilities for the overflow-x-auto wrapper (a surface fill, rounding).' },
      { name: 'children', description: 'THead + tbody (bring your own <tr>s; multi-row headers stay expressible).' },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for Table/THead/Th/Td for every data table; the caption and scope="col" ride along for free.' },
      { guidance: true, description: "Write the caption for a listener: name what the table holds ('Per-arm results…'), not its widget-ness ('data table')." },
      { guidance: false, description: 'Do not hand-roll <table> markup in pages; the tableAdoption ratchet holds the remaining count down and a new raw table turns CI red.' },
      { guidance: false, description: 'Do not omit the caption or pass an empty one — the prop exists because eleven tables shipped without it.' },
    ],
    anatomy: ['div.overflow-x-auto (wrapClassName)', 'table.w-full.text-caption (className)', 'caption.sr-only (required)', 'children (THead + tbody)'],
  },
  {
    name: 'THead',
    keywords: ['thead', 'table header', 'header row'],
    description:
      'The table header block: emits <thead> with the muted header-row ink (text-on-surface-low) applied once, instead of each page restyling its header row. Put the <tr> inside so multi-row headers stay expressible.',
    props: [],
    bestPractices: [
      { guidance: true, description: 'Wrap the header <tr>(s) in THead so the muted ink is applied once and consistently.' },
      { guidance: false, description: 'Do not re-declare the header ink on the <tr> — THead already carries it.' },
    ],
    anatomy: ['thead.text-on-surface-low', 'children (tr → Th cells)'],
  },
  {
    name: 'Th',
    keywords: ['th', 'column header', 'header cell', 'scope'],
    description:
      'A column header cell that always emits scope="col" — the header-to-cell association hand-rolled <th>s kept dropping — with the seed padding and an align prop shared with Td so a column\'s header and cells always agree.',
    props: [
      { name: 'align', description: "Text alignment: 'left' (default), 'right' (numeric columns), or 'center'. Mirror the same value on the column's Td cells." },
      { name: 'pad', description: 'Emit the seed cell padding (px-m py-s). Default true; set false when the table genuinely needs different metrics and bring your own padding — cx is a plain joiner, so two padding utilities would race.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Right-align numeric columns via align="right" on both the Th and its Td cells.' },
      { guidance: false, description: 'Do not hand-write <th> without scope in page tables — the association is the point of this cell.' },
    ],
    anatomy: ['th[scope=col].px-m.py-s', 'align class (text-left | text-right | text-center)'],
  },
  {
    name: 'Td',
    keywords: ['td', 'data cell', 'table cell'],
    description:
      'A data cell with the seed padding and the same align prop as Th, so a column reads consistently from header to last row.',
    props: [
      { name: 'align', description: "Text alignment: 'left' (default), 'right', or 'center' — mirroring the column's Th." },
      { name: 'pad', description: "Emit the seed cell padding (px-m py-s). Default true; mirror the column's Th when opting out." },
    ],
    bestPractices: [
      { guidance: true, description: "Keep a column's Td align in lockstep with its Th." },
      { guidance: false, description: 'Do not pad cells ad hoc — px-m/py-s is the shared treatment; pass className only for genuinely local needs (a truncation, a mono figure).' },
    ],
    anatomy: ['td.px-m.py-s', 'align class (text-left | text-right | text-center)'],
  },
]

export default docs
