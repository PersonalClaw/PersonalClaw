import type { UiDoc } from './uiDoc'

// Doc object for LibraryCard: one thing in the library as a grid card, shared by the library's
// Grid and the Projects page's Results.
const doc: UiDoc = {
  name: 'LibraryCard',
  keywords: ['library', 'card', 'grid', 'tile', 'artifact', 'thumbnail', 'live preview', 'project', 'results'],
  description:
    'One thing in the library as a card: its face (an artifact\'s live page, at most `IFRAME_CAP` live at once and drawn only near the viewport, or its image; otherwise its kind\'s neutral tile and a few lines of its text), its title and its meta line. Picking it opens the same preview a row does. The previewed card is `selected`, drawn through `TileButton`\'s `current`: `aria-current="true"` (never `aria-pressed`) and the outline forced colours keep. The project form draws the kind\'s icon, the title and "From <work> · <when>", or the day alone when what it came from has no title; no chip, never a live page.',
  props: [
    { name: 'item', description: 'The thing, as `GET /api/library` answers it (`LibraryItem`).' },
    { name: 'ctx', description: 'What the page knows beside the rows (`libraryMeta.LibraryContext`), for the meta line.' },
    { name: 'selected', description: 'The card whose preview is open: `TileButton`\'s `current`, `aria-current="true"` and `CURRENT_OUTLINE`.' },
    { name: 'onOpen', description: 'Picking the card: the page previews it.' },
    { name: 'form', description: '`library` (the default), the library\'s Grid card; `project`, the Projects page\'s Results tile (above).' },
    { name: 'now', description: 'The clock the day is read against, for tests. Omit it.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Pass `selected` for the card whose preview is open, never `TileButton`\'s `active`, whose `aria-pressed` and coral border say a choice is on and forced colours drop.' },
    { guidance: false, description: 'Do not draw a live page in the project form: a project\'s Results are tiles that say what each is.' },
  ],
  anatomy: [
    'a TileButton, named by the title and the meta',
    'a 144 px face: ArtifactFace in one neutral ink, the image, or the kind\'s tile and text',
    'the title (MarkdownLine, one line) and the caption meta line',
  ],
}

export default doc
