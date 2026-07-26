import type { UiDoc } from './uiDoc'

// Doc object for DegradedChip — the shell-corner no-model degraded-mode indicator
// (PLATFORM-RESILIENCE §5). Self-polls, takes no props, renders nothing when healthy.
const doc: UiDoc = {
  name: 'DegradedChip',
  keywords: ['degraded', 'no-model', 'offline', 'fallback', 'floor', 'resilience', 'model', 'shell'],
  description:
    "A compact shell-corner chip shown only when a model-dependent surface is running on its no-model FLOOR (e.g. search degraded to keyword ranking, inbox classify paused). Collapsed it is a warn-toned pill with the degraded count / worst surface; click it for a popover listing each degraded surface, its deterministic floor statement, and its pending-enrichment backlog. Takes no props; self-polls GET /api/resilience/degraded and renders nothing when every surface has a model.",
  props: [],
  bestPractices: [
    { guidance: true, description: 'Mount it once in the shell corner cluster (ShellCornerRight) — it self-polls via useVisiblePoll, so do not feed it props or wrap it in another poller (it is a shell sibling of IncidentBanner / SystemWidget, NOT a DashboardLive consumer — that provider only wraps the dashboard page).' },
    { guidance: true, description: 'Keep it warn-toned, never error: a degraded surface is doing less honestly, not broken — reserve error styling for a real outage (IncidentBanner / SystemWidget).' },
    { guidance: false, description: 'Do not surface it as a blocking banner — degraded mode never error-walls; the chip is a quiet, dismissible-by-navigation affordance.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — route tone through the warn design tokens.' },
  ],
  anatomy: ['warn-toned pill trigger (CloudOff glyph + count / worst-surface label)', 'click-away scrim', 'popover listing each degraded surface: name, backlog count, floor statement'],
}

export default doc
