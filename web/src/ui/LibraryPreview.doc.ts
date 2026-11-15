import type { UiDoc } from './uiDoc'

// Doc object for LibraryPreview: one thing in the library, beside its list. Prop
// type/required are DERIVED from LibraryPreview.tsx at build time.
const doc: UiDoc = {
  name: 'LibraryPreview',
  keywords: ['library', 'preview', 'item', 'detail', 'knowledge', 'artifact', 'report', 'findings', 'use in chat', 'pane'],
  description:
    'One thing in the library, beside its list: its heading (an h2), its meta line, then Use in chat ▾, Open, Pin and More, a notice line for each problem it has with its fix (a disagreement with Compare them, an unreadable file with Try again, a report that reads a deleted tag with Choose a tag), its body by kind (the text, the file, the link, the artifact live and sandboxed, a report\'s bar and latest write-up, a findings log\'s bar and its findings 20 at a time), and About this item. It never changes read state. Hostable in a project page\'s detail column from 320 px wide: with `actions.project`, New chat with this opens the chat in that project, Open adds `back=project:<id>`, and for an item in that project About\'s Project row is the name alone.',
  props: [
    { name: 'item', description: 'The thing, as `GET /api/library` answers it (`LibraryItem`).' },
    { name: 'actions', description: 'What its menus do and where they go (`libraryActions.ActionInputs`): the host\'s navigation, pickers, write-ups and project context.' },
    { name: 'phoneSheet', description: 'Its Use in chat and More menus open as phone sheets from 320 to 768 px.' },
    { name: 'foot', description: 'A phone\'s full-screen form: Use in chat, Open and Pin at its foot, 44 px each, and More the ⋯ at its top.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Key it by the item\'s ref, so its bodies and bars read again for another item.' },
    { guidance: true, description: 'Pass the same `ActionInputs` the list\'s rows use, so the row\'s ⋯ and the preview\'s More are the same rows.' },
    { guidance: false, description: 'Do not mark an item read from the preview: only opening it does.' },
  ],
  anatomy: ['section labelled by its h2', 'meta line', 'bar: Use in chat ▾ (tonal menu of groups), Open, Pin, More', 'notice lines (role="note") with their fixes', 'body by kind', 'About this item (dl)', 'phone foot bar (foot)'],
}

export default doc
