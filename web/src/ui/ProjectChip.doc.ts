import type { UiDoc } from './uiDoc'

// Doc object for ProjectChip — the project a piece of work or an item is in, as a chip that opens
// the project or filters by it.
const doc: UiDoc = {
  name: 'ProjectChip',
  keywords: ['project', 'chip', 'pill', 'link', 'filter', 'work', 'scope', 'metadata'],
  description:
    "The project a piece of work or an item is in: MetaChip's 24 px pill with the project's `sm` mark as its lead, then its name, cut to the chip with the whole name in its tooltip. The open form (the default) is a link to #/projects/<id>, named \"Open project Garden\"; the filter form (`onFilter`) is a button that narrows the list it sits in to the project, named \"Filter by project Garden\". Both stop their click, since a chip sits in a row or card whose own click opens the work. From 320 to 768 px a chip inside a row or card that is itself a control is plain text, the mark and the name, so no surface gains a target under 44 px; a chip that is its surface's only way to the project (a header's) keeps a 44 px box. It draws nothing for the project the view is already in, or for an id with no name.",
  props: [
    { name: 'id', description: "The project's id; `\"\"` for work in no project, which draws nothing." },
    { name: 'name', description: "The project's name. `\"\"` while it is not known, or for an id that names no project the reader knows, which draws nothing." },
    { name: 'current', description: 'The project the view is already in: a chip for it draws nothing, since the page already says so.' },
    { name: 'inControl', description: 'The chip sits inside a row or card that is itself a control (a Home card, a list row): plain text from 320 to 768 px. Omit it where the chip is the surface\'s only way to the project, which keeps a 44 px box.' },
    { name: 'onFilter', description: 'The filter form: called with the id when the chip is pressed, to narrow the list it sits in. Omit it for the open form, a link to the project.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Pass `inControl` on any row or card whose own click opens the work, so a phone draws the project as text and the row keeps its one target.' },
    { guidance: true, description: 'Pass `current` on a surface held to one project, so its own rows do not each repeat it.' },
    { guidance: false, description: 'Do not draw it for an id the page cannot name: a chip with an id and no name is a project that is gone, or one the reader has not loaded yet.' },
    { guidance: false, description: "Do not drop the stopPropagation. Opening the project from a card must not also open the card's work." },
  ],
  anatomy: [
    'an h-6 rounded-pill on bg-surface-high: a link (open), a button (filter), or a span (a phone, inside a control); where the chip is its surface\'s only way, the link or button is a 44 px box below 1024 px, unpainted, around the pill',
    "the project's ProjectMark at sm (18 px) as its lead",
    "the name at the caption type role, the chip cut at 14rem (narrower where its row is), with the whole name in the tooltip",
  ],
}

export default doc
