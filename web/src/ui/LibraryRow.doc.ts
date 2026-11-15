import type { UiDoc } from './uiDoc'

// Doc object for LibraryRow: one thing in the library as a list row, shared by the library
// page and the Projects page's About › Knowledge.
const doc: UiDoc = {
  name: 'LibraryRow',
  keywords: ['library', 'row', 'list', 'item', 'knowledge', 'artifact', 'report', 'findings', 'project'],
  description:
    'One thing in the library as a row: one neutral icon tile or a thumbnail (its kind is a word, never a colour, `web/DESIGN.md`\'s Kind-Is-A-Word Rule), its title in one line through `MarkdownLine`, the meta line ("PDF · You · Homelab · 38 pages · Still being read": the format, the maker, the project chip in its filter form, the age, the size, the state mark) and, on a desktop, a line of its text, a search\'s passage with the match marked. Its hit target is `RowHitTarget`, so the chip and the ⋯ are not nested in the row\'s button; its name starts with the visible title, then its meta. The state mark is never cut: the size gives way first, then the age. From 320 to 768 px the meta is the phone\'s short form, the project is text, the row is at least 56 px and its ⋯ is always drawn; its ⋯ and its long press open the same menu, a sheet with `phoneSheet`. The compact form, for the Projects page, draws the icon, the title and who it is from.',
  props: [
    { name: 'item', description: 'The row, as `GET /api/library` answers it (`LibraryItem`).' },
    { name: 'ctx', description: 'What the page knows beside the rows (`libraryMeta.LibraryContext`): each intent\'s findings count and whether it is paused, each report\'s schedule and last run, how far a reader got, and the project the list is filtered to, whose rows carry no chip.' },
    { name: 'group', description: 'The group the row is listed under ("Today", "Yesterday"), whose words a phone row\'s age leaves out.' },
    { name: 'selected', description: 'The row whose preview is open: `RowHitTarget`\'s `aria-current`, the selected fill, and `SELECTED_ROW_OUTLINE` under forced colours.' },
    { name: 'onOpen', description: 'The row\'s own press: the library previews it on a desktop and opens it full screen on a phone.' },
    { name: 'onFilterProject', description: 'The project chip\'s filter form: narrows the list to the project, named "Filter by project Homelab". Without it the chip opens the project.' },
    { name: 'menu', description: 'The row\'s menu (`ContextMenuItem`s): its ⋯ on a phone, its long press, its right click and Shift+F10. No menu, no ⋯.' },
    { name: 'checked', description: 'Bulk mode: the row draws a check box, checked or not, and its press checks it in place of opening it. Omitted, no check box.' },
    { name: 'onCheck', description: 'Called with the new checked state in bulk mode.' },
    { name: 'compact', description: 'The Projects page\'s About › Knowledge form: the icon, the title and "From <project>" or "Added by <name>" alone, no meta line, text or chip, in a box of at least 44 px from 320 to 768 px.' },
    { name: 'phoneSheet', description: 'Its ⋯ and its long press open as a phone sheet from 320 to 768 px (`Popover`\'s and `ContextMenu`\'s `phoneSheet`). The library passes it, and so does a project\'s page.' },
    { name: 'now', description: 'The clock the ages are read against, for tests. Omit it.' },
  ],
  bestPractices: [
    { guidance: true, description: 'List rows in a `WindowedList` with `positionOf`, so the group headings between them are list items holding an `h2` and `aria-setsize` counts the rows alone.' },
    { guidance: true, description: 'Pass `ctx.project` on a list filtered to a project: its own rows draw no chip, and a shared item from another project reads "From Garden".' },
    { guidance: false, description: 'Do not colour a row by its kind: one neutral tile and the word.' },
    { guidance: false, description: 'Do not make anything hover-only: the phone row\'s ⋯ is always drawn, and a desktop row\'s menu is its right click, Shift+F10 and the preview\'s More.' },
  ],
  anatomy: [
    'a relative row, its RowHitTarget at -z-10, the focus ring on the wrapper',
    'an optional check box (bulk), then a 36 px neutral tile or thumbnail',
    'the title (MarkdownLine, cut to one line), and a pin when pinned',
    'the caption meta line, its parts joined by " · ", the project as ProjectChip (text on a phone), the state mark at weight 540, never cut',
    'on a desktop, the item\'s text or a search\'s passage in one line; on a phone, the 44 px ⋯',
  ],
}

export default doc
