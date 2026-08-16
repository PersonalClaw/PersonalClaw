import { MonitorUp } from 'lucide-react'

/**
 * "Sharing your screen" — the in-app half of the honest-signalling pair (§5.2).
 *
 * Two indicators are lit while a screen is shared and BOTH matter:
 *
 * 1. The **browser's own** capture indicator (tab badge, OS overlay, the floating
 *    "Stop sharing" bar). That one the app cannot draw, suppress, or fake, which is
 *    exactly why it is the trustworthy one.
 * 2. **This chip.** It says which PersonalClaw chat is receiving the frames, which
 *    the browser indicator cannot — the browser knows a page is capturing, not that
 *    a particular conversation will be shown a frame on your next message.
 *
 * So this is an addition to the browser's signal, never a substitute for it. It is
 * mounted from the live stream's state (not from a "user pressed share" flag), so it
 * disappears the moment the track ends however it ended — including via the browser's
 * own stop button. A chip that outlived its stream would be worse than no chip.
 *
 * Clicking it stops sharing: the visible indicator is also the off switch, so a user
 * who notices the chip never has to hunt for the control that clears it.
 *
 * Which is why its HIT TARGET is held at 24px. Measured at 133x22 before `min-h-6` — 2px under
 * WCAG 2.5.8's floor — and this is the control that stops a screen capture, so it is the last
 * one that should be fiddly to hit. `-my-px` absorbs the 2px so the composer row does not grow:
 * the pairing `ui/DegradedChip` (the near-identical status pill), `dashboard/widgets/kit` and
 * `DashboardPage` already use.
 */
export function ScreenShareChip({ onStop }: { onStop: () => void }) {
  return (
    <button
      type="button"
      onClick={onStop}
      aria-label="Sharing your screen with this chat — stop sharing"
      title="Sharing your screen with this chat. Click to stop."
      className="inline-flex min-h-6 -my-px shrink-0 items-center gap-1.5 rounded-pill px-2 py-0.5 text-[0.75rem] transition-colors hover:brightness-110"
      style={{
        background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)',
        color: 'var(--color-warn)',
      }}
    >
      {/* The pulse rides the shared `.status-pulse` token, so its cadence follows the
          user's Design → Motion preference like every other live beacon. */}
      <span className="relative grid size-2.5 place-items-center">
        <span className="size-1.5 rounded-full" style={{ background: 'var(--color-warn)' }} />
        <span
          className="status-pulse absolute inline-flex size-1.5 rounded-full"
          style={{ background: 'var(--color-warn)' }}
        />
      </span>
      <MonitorUp size={12} className="shrink-0" />
      Sharing screen
    </button>
  )
}
