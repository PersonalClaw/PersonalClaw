import type { UiDoc } from './uiDoc'

// Doc object for InlineConfirm, the one way a card asks once more.
const doc: UiDoc = {
  name: 'InlineConfirm',
  keywords: ['confirm', 'second confirmation', 'ask once more', 'deliberate', 'inline', 'card', 'yes'],
  description:
    "The one way a card asks once more before an answer it can't take back (a call that deletes, a merge, trusting a folder), the inline twin of a deliberate dialog. It opens under the control that asked for it, in the panel's own words, and nothing is sent before it. Yes answers only a press begun after it has been in view and quiet for ARM_MS (1 s); any key (but a modifier pressed alone) or press anywhere starts that wait again, a keyboard press counts only from a Yes that has held the focus that long, and ⌘Enter answers only from Yes. Escape and Back step back without closing the panel behind the card.",
  props: [
    { name: 'sentence', description: "What answering does, in the panel's own words (\"It deletes files. Run it?\"). It names the group." },
    { name: 'verb', description: "The answer after \"Yes, \" (\"run it\", \"trust it\"), never the label of the control that opened it." },
    { name: 'onYes', description: 'Sends the answer. Called only for a press begun after the quiet second.' },
    { name: 'onBack', description: 'Steps back: the card shows its answers again. Escape calls it too.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Keep the control that opened it where it is, and let every press of it only ask again: a double click must land on it twice, never on Yes.' },
    { guidance: true, description: "Word Yes as the verb and its subject (\"Yes, redo it\"), so it says what it does." },
    { guidance: false, description: 'Do not move the focus onto Yes when it opens: a key already held down would answer it.' },
    { guidance: false, description: 'Do not open a dialog for a card answer that asks once more: the confirmation stays on the card, under the pressed control.' },
  ],
  anatomy: [
    'group (named by its sentence)',
    'sentence (body-s, on-surface)',
    'Yes, <verb> (secondary look, wraps, 44 px below 1024 px, transparent edge)',
    'Back (ghost look, the same box)',
    'a polite line saying why a press did nothing',
  ],
}

export default doc
