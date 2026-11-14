import type { UiDoc } from './uiDoc'

// Doc objects for PhoneSheet and PhoneSheetRows: the phone sheet form of the kit's menus and
// pickers (`phoneSheet` on Popover, ContextMenu and Modal).
const docs: UiDoc[] = [
  {
    name: 'PhoneSheet',
    keywords: ['sheet', 'bottom sheet', 'phone', 'mobile', 'menu', 'picker', 'scrim', 'dialog', 'touch target'],
    description:
      "A menu or picker as a phone draws it: a bottom sheet over a scrim with a grab line, a dialog (aria-modal) named by what opened it, that keeps the focus inside it. Escape and the scrim close it, the focus back on `returnTo`. Every MenuRow and FilterRow in it is a 44 px row. Its hosts open it only from 320 to 768 px, by opt-in (`phoneSheet` on Popover, ContextMenu and Modal, passed through HeaderFilter, HeaderMenu and HeaderActions' overflowPhoneSheet); elsewhere each is as before.",
    props: [
      { name: 'name', description: "The dialog's accessible name: the trigger's own (\"Filter\", \"Add\")." },
      { name: 'onClose', description: 'Close it: Escape, the scrim, or a row that acts.' },
      { name: 'returnTo', description: 'Where the focus goes once it closes: the control that opened it.' },
      { name: 'children', description: 'The menu or picker: the same rows the anchored form draws.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach it through a host\'s `phoneSheet`, so the same component is an anchored menu at a desktop width and a sheet on a phone.' },
      { guidance: false, description: 'Do not draw rows of a height of their own in a sheet: MenuRow and FilterRow take 44 px inside one.' },
    ],
    anatomy: ['scrim (closes it)', 'sheet: grab line, scrolling body of 44 px rows'],
  },
  {
    name: 'PhoneSheetRows',
    keywords: ['sheet', 'phone', 'rows', 'touch target'],
    description: "Draws the MenuRows and FilterRows inside it as a phone sheet's, 44 px, for a host that draws its own sheet (Modal's phoneSheet).",
    props: [
      { name: 'children', description: 'The rows.' },
    ],
    bestPractices: [
      { guidance: false, description: 'Do not wrap a desktop surface in it: it is for a sheet only.' },
    ],
    anatomy: ['a context provider; draws nothing of its own'],
  },
]

export default docs
