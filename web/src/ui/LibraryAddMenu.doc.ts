import type { UiDoc } from './uiDoc'

// Doc objects for LibraryAddMenu and LibraryAddForm: the library's one door in.
// Prop type/required are DERIVED from LibraryAddMenu.tsx at build time.
const docs: UiDoc[] = [
  {
    name: 'LibraryAddMenu',
    keywords: ['add', 'upload', 'link', 'note', 'record', 'audio', 'feed', 'intent', 'report', 'library', 'project'],
    description:
      'Add, the library\'s primary: Upload files, Add a link, Write a note and Record audio, then, after a break, Follow a feed or folder, Look out for a topic and Schedule a report. Each hint sits whole on one line (384 px with the set-up rows, 300 without). With a `project` (a project\'s page) it holds the four add rows only, each filing into that project, since no feed, intent or report belongs to a project. Upload files forwards to a file picker and uploads each file, saying how each went.',
    props: [
      { name: 'project', description: 'The project it files into (a project\'s page): its four add rows only, each sending `project_id`.' },
      { name: 'onOpen', description: 'Open an add form (`link`, `note`, `audio`) in the pane, at `?open=<form>:new`.' },
      { name: 'onSetup', description: 'Open a set-up form (`source`, `intent`, `report`) in the pane; the library\'s Add only.' },
      { name: 'phoneSheet', description: 'From 320 to 768 px, open as a phone sheet with 44 px rows, its break kept.' },
      { name: 'disabled', description: 'Unavailable for now (the gateway is not answering): the trigger and its rows.' },
      { name: 'disabledReason', description: 'Why it is off, on the trigger\'s tooltip: "Can\'t reach PersonalClaw."' },
      { name: 'className', description: 'Added to the trigger: the compact widths\' 44 px box (`TOUCH_TARGET`).' },
    ],
    bestPractices: [
      { guidance: true, description: 'Pass `project` on a project\'s page, so what is added files into it and the set-up rows are left out.' },
      { guidance: false, description: 'Do not add a second Add to the page: the drop target and the empty state\'s buttons are the other ways in.' },
    ],
    anatomy: ['HeaderMenu trigger (primary)', 'add group: Upload files, Add a link, Write a note, Record audio', 'break', 'setup group (library only)', 'hidden file input, reached through Upload files'],
  },
  {
    name: 'LibraryAddForm',
    keywords: ['add', 'link', 'note', 'record', 'audio', 'form', 'pane', 'library', 'project'],
    description:
      'An add form in the pane: Add a link (its address, a title, tags), Write a note (its kind, title, text, tags; a code snippet\'s language) or Record audio (`AudioRecorder`, saved through `uploadKnowledgeFile`). With a `project` it says where what it makes goes and files it there. Add answers the new item\'s ref, which the host selects.',
    props: [
      { name: 'form', description: 'Which form: `link`, `note` or `audio`.' },
      { name: 'project', description: 'The project what it makes is filed in, said under its heading.' },
      { name: 'onClose', description: 'Back and Cancel.' },
      { name: 'onAdded', description: 'Added: the new item\'s ref (`knowledge:<id>`), or "" when the upload answered no id.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Key it by its form, so switching from a link to a note starts empty.' },
      { guidance: true, description: 'Select what it made (`?item=<ref>`) when it answers, so the preview shows it.' },
    ],
    anatomy: ['section labelled by its h2', 'Back', 'the form\'s fields', 'Tags (link and note)', 'FormFooter: Cancel, Add (with its error)'],
  },
]

export default docs
