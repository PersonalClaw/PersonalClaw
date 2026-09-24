import type { UiDoc } from './uiDoc'

// Doc object for ScreenShareChip — the in-app half of the screen-sharing
// honest-signalling pair (MULTIMODAL-IO §5.2). Lives in ui/ beside DegradedChip
// because it is a status pill with the same warn skin and the same "mounted only
// while the condition holds" contract.
const doc: UiDoc = {
  name: 'ScreenShareChip',
  keywords: ['screen', 'share', 'capture', 'privacy', 'indicator', 'chip', 'pulse', 'multimodal'],
  description:
    "A warn-toned pulsing pill shown in the chat header for exactly as long as a screen-share stream is live. It is the IN-APP half of a pair: the browser's own capture indicator (tab badge / OS overlay / 'Stop sharing' bar) is the trustworthy signal the app cannot draw or suppress, and this chip adds the one thing the browser cannot say — WHICH chat is being shown frames. Clicking it stops sharing, so the indicator is also the off switch. Mount it from live stream state, never from a 'user pressed share' flag.",
  props: [
    {
      name: 'onStop',
      description:
        'Tear the share down — stops the tracks and tells the server to drop the staged frame. Wire it to the same toggle the composer control uses so there is one stop path.',
    },
  ],
  bestPractices: [
    {
      guidance: true,
      description:
        "Render it off the LIVE stream (a track's readyState / ended event), not off a 'sharing requested' boolean — a chip that outlives its stream tells the user the app can see their screen when it cannot, which is worse than no chip.",
    },
    {
      guidance: true,
      description:
        'Keep it in the header, not the composer: the user must still see it after scrolling the transcript, and an indicator you can scroll away from is not an indicator.',
    },
    {
      guidance: false,
      description:
        "Never present it as the only sign that capture is running, and never build a capture path that suppresses or works around the browser's own indicator — a capture surface the user cannot audit is the pattern this chip exists to avoid.",
    },
    {
      guidance: false,
      description:
        'Do not swap the warn tone for primary/danger: sharing is a live, user-chosen state to stay aware of, not an error, and 16% is the app-wide warn tint depth (20% fails AA).',
    },
  ],
  anatomy: [
    'warn-toned pill',
    'pulsing status dot (shared .status-pulse token, user-tunable cadence)',
    'MonitorUp glyph',
    '"Sharing screen" label',
  ],
}

export default doc
