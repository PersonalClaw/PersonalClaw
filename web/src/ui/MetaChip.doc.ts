import type { UiDoc } from './uiDoc'

// Doc object for MetaChip — the metadata pill that narrows a list to its own value
// (a task's project or one of its tags, on a row or a card).
const doc: UiDoc = {
  name: 'MetaChip',
  keywords: ['chip', 'pill', 'tag', 'label', 'project', 'metadata', 'filter', 'facet', 'badge', 'task'],
  description:
    "A small (h-6) metadata pill on a list row or card that reports one value — a task's project, one of its tags — and, when given a handler, filters the list to that value. It exists as one component because these were two implementations of the same control that disagreed about being interactive: the project pill was a real <button> while the tag chips beside it were inert <span>s with the same pill shape, size and hover styling, so they read as clickable and were not (issue 477). Renders a <span> instead of a <button> when no onClick is supplied, so a surface that cannot offer the filter never shows a control that does nothing.",
  props: [
    { name: 'label', description: 'The value shown, and the value handed to onClick — a tag or a project name.' },
    { name: 'title', description: 'Native tooltip, in the pointer affordance\'s own words (e.g. "Filter by tag “wedding”"). Also the default source of the accessible name.' },
    { name: 'ariaLabel', description: 'Accessible name override. Defaults to `title` with its curly quotes stripped, falling back to the bare label — pass this only when the tooltip is the wrong sentence for a screen reader.' },
    { name: 'icon', description: 'Optional leading lucide glyph at 10px (the project pill uses FolderKanban; tags carry none).' },
    { name: 'tone', description: '`neutral` (default) for the surface-high tag skin, `accent` for the project pill\'s accent skin.' },
    { name: 'onClick', description: 'Called with `label` when activated. OMIT IT to render the inert span form — that is the switch between "reports a value" and "narrows to a value".' },
  ],
  bestPractices: [
    { guidance: true, description: 'Pass onClick wherever the list can actually filter by the value. A chip that looks like its interactive sibling and is not is the exact defect this component was extracted to end.' },
    { guidance: true, description: 'Omit onClick on a surface that cannot host a control rather than rendering a dead button. The kanban card does this deliberately: its wrapper is the ONLY drag source, and TaskBoard records that a control across a `draggable` element gets between the pointer and the drag.' },
    { guidance: true, description: 'Keep it h-6 so it sits level with the priority/due chips it shares a meta row with. Reach for ui/Button only when you want stock button chrome — its smallest size is h-7 px-m with a centred label, which is a different visual object.' },
    { guidance: false, description: 'Do not confuse it with pages/knowledge\'s local FilterChip: that is an h-8 TOGGLE carrying aria-pressed, one of a rail of filter states you switch between. This reports a value and narrows to it. Merging them would force one height and one semantic onto both.' },
    { guidance: false, description: 'Do not drop the stopPropagation in the button form. Every one of these sits inside a row or card whose own click opens the record, so filtering by a tag would also open whichever task the tag happened to hang on.' },
    { guidance: false, description: 'Do not hardcode colors — `tone` routes through the accent skin and the surface tokens so the chip stays themable in both modes.' },
  ],
  anatomy: [
    'rounded-pill container, h-6 px-2, gap-1 (a <button> when onClick is given, a <span> otherwise)',
    'optional leading 10px lucide icon',
    'the value label at the caption type role',
  ],
}

export default doc
