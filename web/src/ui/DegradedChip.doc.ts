import type { UiDoc } from './uiDoc'

// Doc object for DegradedChip — the shell-corner no-model degraded-mode indicator
// (PLATFORM-RESILIENCE §5). Self-polls, takes no props, renders nothing when healthy.
const doc: UiDoc = {
  name: 'DegradedChip',
  keywords: ['degraded', 'no-model', 'offline', 'fallback', 'floor', 'resilience', 'model', 'shell'],
  description:
    "A compact shell-corner chip shown only when a model-dependent surface is running on its no-model FLOOR (e.g. search degraded to keyword ranking, inbox classify paused). Collapsed it is a warn-toned pill with the degraded count / worst surface; click it for a popover carrying one TextLink to Settings → Models, then each degraded surface with the model binding it is missing, its deterministic floor statement, and its pending-enrichment backlog. Takes no props; self-polls GET /api/resilience/degraded and renders nothing when every surface has a model.",
  props: [],
  bestPractices: [
    { guidance: true, description: 'Mount it once in the shell corner cluster (ShellCornerRight) — it self-polls via useVisiblePoll, so do not feed it props or wrap it in another poller (it is a shell sibling of IncidentBanner / SystemWidget, NOT a DashboardLive consumer — that provider only wraps the dashboard page).' },
    { guidance: true, description: 'Keep it warn-toned, never error: a degraded surface is doing less honestly, not broken — reserve error styling for a real outage (IncidentBanner / SystemWidget).' },
    { guidance: false, description: 'Do not surface it as a blocking banner — degraded mode never error-walls; the chip is a quiet, dismissible-by-navigation affordance.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — route tone through the warn design tokens.' },
    { guidance: true, description: 'Keep the popover\'s Settings → Models link, keep it ABOVE the surface rows, and keep it closing the popover on activate. The rows name a missing binding ("No model for Speech-to-text") in ModelsPanel\'s own words and some issue instructions outright, so the link is the route to the row that fixes it — copy that instructs and cannot navigate is the defect it closes. Above the rows because the panel has no scroller: at 12 degraded surfaces it renders 1770px tall inside the fixed shell corner, so a footer measures off-screen at 1440x900, 1280x800 and 390x844. Closing on activate because `open` is component state, not route-derived — the full-viewport scrim would otherwise stay mounted over the page the link just opened.' },
    { guidance: false, description: 'Do not put the link in the unknown-state branch: there no diagnosis has been measured, so pointing at a fix would assert a fault the chip explicitly declines to claim.' },
  ],
  anatomy: ['warn-toned pill trigger (CloudOff glyph + count / worst-surface label)', 'click-away scrim', 'popover: warn heading, then a TextLink to Settings → Models (trailing arrow, hairline under it), then one row per degraded surface: name, backlog count, missing use-case binding, floor statement'],
}

export default doc
