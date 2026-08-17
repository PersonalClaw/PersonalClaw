import type { UiDoc } from './uiDoc'

// Doc object for ShortcutRecorder — the click-then-press keyboard-shortcut control
// (DESKTOP-CAPABILITIES S3, first used by the push-to-talk chord in Settings →
// Speech & Transcription). Lives in ui/ because the keyboard semantics are the
// reusable, fiddly part; the caller keeps the policy about what a valid chord is.
const doc: UiDoc = {
  name: 'ShortcutRecorder',
  keywords: ['shortcut', 'hotkey', 'keybinding', 'accelerator', 'chord', 'keyboard', 'record', 'settings'],
  description:
    "A button that records a key combination: click it, press the keys, and the chord is captured. Replaces the text field that would otherwise ask a user to know and spell an accelerator grammar (`CommandOrControl+Shift+Space`) and let them save a string that cannot be bound. While armed it swallows every key so recording ⌘S does not also save the page, Escape cancels instead of being recorded, and a press that is only modifiers leaves it listening rather than storing half a chord. Display and storage are separate: `format` renders the stored accelerator as ⌘⇧Space.",
  props: [
    { name: 'value', description: 'The currently stored shortcut, in its stored (accelerator) form.' },
    { name: 'format', description: 'Stored form → the form a user reads, e.g. `CommandOrControl+Shift+Space` → `⌘⇧Space`. Keep it total: an unknown token should stay visible rather than being dropped, or the control displays fewer keys than it binds.' },
    { name: 'parse', description: "A key event → the chord to store, or '' for \"not a chord yet\" (which keeps the recorder armed). This is where the caller enforces its own rules — e.g. requiring a modifier because a bare global key would be taken from every app on the machine." },
    { name: 'onRecord', description: 'A complete chord was recorded. Validate/bind BEFORE persisting: a shortcut that cannot be bound must not become the stored value, or the setting claims a shortcut that does nothing.' },
    { name: 'label', description: 'What the shortcut is for ("Push-to-talk shortcut"). Used in the accessible name, so several recorders on one page are not identically named.' },
  ],
  bestPractices: [
    {
      guidance: true,
      description: 'Bind (or otherwise verify) the chord in `onRecord` before saving it, and on failure show the reason and KEEP the previous shortcut. Discovering a clash at the next launch with the old chord already discarded is the outcome that costs the user their working setup.',
    },
    {
      guidance: true,
      description: "Return '' from `parse` for anything incomplete rather than guessing. The recorder stays armed, which is what makes pressing a three-key chord feel continuous instead of recording the first two keys.",
    },
    {
      guidance: false,
      description: 'Do not offer a bare (modifier-less) key for a GLOBAL shortcut. It is taken from every other app on the machine, and any sane binder will refuse it — so offering it means offering a shortcut that cannot be saved.',
    },
    {
      guidance: false,
      description: 'Do not swap this for a text input to "let power users type it". The stored form is an accelerator grammar; a typed value can be syntactically valid and still unbindable, and the error then arrives at the next launch rather than at the keystroke.',
    },
  ],
  anatomy: [
    'monospace button showing the formatted chord',
    'armed state: "Press keys…" with an inset primary focus ring',
    'accessible name carrying both the label and the current value',
  ],
}

export default doc
