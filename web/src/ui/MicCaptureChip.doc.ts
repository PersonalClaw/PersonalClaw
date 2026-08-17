import type { UiDoc } from './uiDoc'

// Doc object for MicCaptureChip — the in-app half of the microphone-capture
// indicator (DESKTOP-CAPABILITIES S3). Deliberately the same shape and skin as
// ScreenShareChip: two sensors, one promise, one vocabulary for the user to learn.
const doc: UiDoc = {
  name: 'MicCaptureChip',
  keywords: ['microphone', 'mic', 'capture', 'recording', 'privacy', 'indicator', 'chip', 'pulse', 'push-to-talk', 'voice'],
  description:
    "A warn-toned pulsing pill shown for exactly as long as the microphone is live. One of THREE indicators during a push-to-talk capture, and the only one that says capture is feeding THIS composer: macOS draws its own orange mic dot (the trustworthy one — the app cannot suppress it), the desktop shell puts '● Listening' in the menu bar (visible even when the window is hidden, which matters because the chord is global), and this chip names the destination. Clicking it stops the capture, so the indicator is also the off switch.",
  props: [
    {
      name: 'onStop',
      description:
        'End the capture — stops the microphone tracks and transcribes what was recorded. Wire it to the same stop path the chord uses so there is exactly one way capture ends.',
    },
  ],
  bestPractices: [
    {
      guidance: true,
      description:
        "Render it from the LIVE capture state, never from 'the user pressed the chord'. A denied microphone means no stream ever opened, and a chip bound to intent would claim the app is listening when it is not — the exact lie the always-on-indicator clause exists to prevent.",
    },
    {
      guidance: true,
      description:
        'Keep the pulse purely decorative: the label, glyph and accessible name must all be present with the animation removed. Reduced motion or a paused animation may change how this looks and must never change whether it is there.',
    },
    {
      guidance: false,
      description:
        "Never present it as the only sign the microphone is on, and never build a capture path that works around the OS indicator — a capture the user cannot audit is what this chip exists to prevent.",
    },
    {
      guidance: false,
      description:
        'Do not swap the warn tone for primary/danger: a live microphone is a user-chosen state to stay aware of, not an error, and 16% is the app-wide warn tint depth (20% fails AA).',
    },
  ],
  anatomy: [
    'warn-toned pill',
    'pulsing status dot (shared .status-pulse token, user-tunable cadence)',
    'Mic glyph',
    '"Listening" label',
  ],
}

export default doc
