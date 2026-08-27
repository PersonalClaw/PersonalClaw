import { useState } from 'react'

/**
 * A keyboard-shortcut recorder: click it, press the combination, done.
 *
 * The alternative — a text field holding `CommandOrControl+Shift+Space` — asks the user
 * to know and spell an accelerator grammar, and lets them save a string that cannot be
 * bound. Recording the real key press removes both problems: what the user pressed is
 * what gets stored.
 *
 * It lives in `ui/` because the KEYBOARD semantics are the reusable part, and they are
 * fiddly enough to be worth having in exactly one place:
 *
 *  - while armed it swallows every key (`preventDefault` + `stopPropagation`), so
 *    recording `⌘S` does not also save the page and `Tab` does not move focus away
 *    mid-recording;
 *  - `Escape` cancels rather than being recorded — the one key a recorder must not
 *    capture, or there is no way out without setting a shortcut;
 *  - a press that yields no chord (modifiers only) leaves it armed and listening, so a
 *    half-pressed combination is never stored;
 *  - blur disarms it, because a recorder left armed invisibly would swallow the next key
 *    the user pressed somewhere else.
 *
 * `parse` is injected: this component owns the interaction, and the CALLER owns what
 * counts as a valid chord (which modifiers are required, how keys are named). Returning
 * `''` from `parse` means "not a chord yet" and keeps the recorder listening.
 *
 * It renders its value through `format`, so the stored accelerator and the displayed
 * `⌘⇧Space` never have to be the same string.
 */
export function ShortcutRecorder({
  value,
  format,
  parse,
  onRecord,
  label,
}: {
  /** The stored shortcut (an accelerator string). */
  value: string
  /** Stored form → the form a user reads. */
  format: (chord: string) => string
  /** A key event → a stored chord, or `''` for "not a chord yet". */
  parse: (e: React.KeyboardEvent) => string
  /** A complete chord was recorded. */
  onRecord: (chord: string) => void
  /** What this shortcut is for, e.g. "Push-to-talk shortcut" — used in the accessible
   *  name so several recorders on one page are not identically named. */
  label: string
}) {
  const [recording, setRecording] = useState(false)

  const onKeyDown = (e: React.KeyboardEvent) => {
    // Swallow everything while armed: an unhandled ⌘S here would save the page.
    e.preventDefault()
    e.stopPropagation()
    if (e.key === 'Escape') { setRecording(false); return }
    const chord = parse(e)
    // Modifiers only (or a combination the caller rejects) — stay armed.
    if (!chord) return
    setRecording(false)
    onRecord(chord)
  }

  return (
    <button
      type="button"
      onClick={() => setRecording(true)}
      onBlur={() => setRecording(false)}
      onKeyDown={recording ? onKeyDown : undefined}
      aria-label={recording
        ? `Press the new ${label.toLowerCase()}, or Escape to cancel`
        : `${label}: ${format(value)} — activate to change`}
      className={`inline-flex h-9 min-w-32 items-center justify-center rounded-md px-3 font-mono text-[0.8125rem] transition-colors ${
        recording
          ? 'bg-surface-high text-on-surface-low ring-2 ring-inset ring-primary'
          : 'bg-surface-high text-on-surface hover:brightness-110'}`}
    >
      {recording ? 'Press keys…' : format(value)}
    </button>
  )
}
