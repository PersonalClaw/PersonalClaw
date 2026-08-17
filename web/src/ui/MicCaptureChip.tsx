import { Mic } from 'lucide-react'

/**
 * "Listening" — the in-app half of the microphone-capture indicator (DC-3 T3.1).
 *
 * Modelled on `ScreenShareChip`, deliberately: the two are the same promise about two
 * different sensors, and a user who has learned to read one should not have to learn a
 * second vocabulary for the other.
 *
 * THREE indicators are lit while push-to-talk is capturing, and they are not
 * interchangeable:
 *
 * 1. **macOS's own** orange microphone dot. Drawn by the system, impossible for the app
 *    to suppress or fake — the trustworthy one.
 * 2. **The menu-bar item** (`● Listening`, drawn by the shell). The chord is GLOBAL, so
 *    capture can start while this window is hidden behind a full-screen app; an
 *    in-window indicator alone would be a capture indicator you cannot see.
 * 3. **This chip**, which says capture is feeding *this composer* — the thing neither of
 *    the others can tell you.
 *
 * So it is an addition to the system indicator, never a substitute.
 *
 * Two properties this component is built around:
 *
 * - **It is rendered from the live capture state, not from "the user pressed the
 *   chord".** A chip bound to intent would stay lit when a denied microphone meant no
 *   stream ever opened, which is precisely the lie the always-on clause exists to
 *   prevent.
 * - **The animation never gates the content.** The dot's `.status-pulse` is decoration
 *   riding the shared motion token; the text, the icon and the accessible name are all
 *   present without it. Reduced motion, a dropped stylesheet or a paused animation
 *   changes how this looks and never whether it is *there* — the repo's standing rule
 *   that an entrance must not gate content, applied to a safety indicator where it
 *   matters most.
 *
 * Clicking it stops the capture: the visible indicator is also the off switch, so
 * noticing it never means hunting for the control that clears it. Hit target held at
 * 24px for the same reason `ScreenShareChip` holds it — this is the control that stops a
 * microphone, so it is the last one that should be fiddly to hit.
 */
export function MicCaptureChip({ onStop }: { onStop: () => void }) {
  return (
    <button
      type="button"
      onClick={onStop}
      aria-label="Listening to your microphone — stop recording"
      title="PersonalClaw is listening. Click to stop."
      className="inline-flex min-h-6 -my-px shrink-0 items-center gap-1.5 rounded-pill px-2 py-0.5 text-[0.75rem] transition-colors hover:brightness-110"
      style={{
        background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)',
        color: 'var(--color-warn)',
      }}
    >
      <span className="relative grid size-2.5 place-items-center">
        <span className="size-1.5 rounded-full" style={{ background: 'var(--color-warn)' }} />
        <span
          className="status-pulse absolute inline-flex size-1.5 rounded-full"
          style={{ background: 'var(--color-warn)' }}
        />
      </span>
      <Mic size={12} className="shrink-0" />
      Listening
    </button>
  )
}
