import type { UiDoc } from './uiDoc'

// SpendCapPause — the one way a run a spend ceiling stopped is shown: paused, with the refusal's own
// sentence, the Settings page that lifts it, and Resume. SpendCapSettingsLink — that page's link,
// for a surface that shows the refusal its own way (a workflow run that stopped at a cap).
const pause: UiDoc = {
  name: 'SpendCapPause',
  keywords: ['spend', 'cap', 'budget', 'paused', 'refused', 'dollars', 'tokens', 'guardrails', 'resume', 'loop', 'planning'],
  description:
    "The notice for a run a spend ceiling (the daily dollar or token cap, or a run's own) stopped before its next model call. It is a pause, not a failure: it shows the gateway's sentence for the refusal — which ceiling, what was spent against it, what the call needed, and where it is lifted — links the Settings page it names, and offers Resume. A loop's cockpit and a planning walkthrough show it in place of a spinner or a question.",
  props: [
    { name: 'reason', description: "The refusal's sentence as the gateway says it (`BudgetExceededError.sentence`), shown verbatim." },
    { name: 'detail', description: 'One line under it: what the pause means for this run (it does not try the refused call again by itself).' },
    { name: 'settings', description: 'The route id of the Settings page the refusal is lifted on (`guardrails`, `usage`); renders the "Open Settings → …" link. Omit for a limit set on the run itself.' },
    { name: 'onResume', description: 'Resumes the run. Omit where another control on the same view already resumes it.' },
    { name: 'busy', description: 'Resume is under way: its button shows `loading` (spins, is disabled, and announces it).' },
    { name: 'resumeLabel', description: 'The Resume button\'s label ("Resume planning"); defaults to "Resume".' },
    { name: 'className', description: 'Outer spacing at the call site.' },
  ],
  bestPractices: [
    { guidance: true, description: "Pass the gateway's sentence unchanged: it is the one wording of the refusal, the same one the chat, the Inbox and the bell show." },
    { guidance: true, description: 'Show it in place of "Drafting…" or an answer box: there is nothing to answer, only a cap to lift or a reset to wait for.' },
    { guidance: false, description: 'Do not re-run the refused work from a poll or a mount: only the user\'s Resume does, so the same refused call is not sent again while nothing has changed.' },
  ],
  anatomy: [
    'role="status" band in the warn tint',
    'CirclePause + "Paused by a spend cap"',
    "the refusal's sentence, then the optional detail line",
    '"Open Settings → <page>" text link and the Resume button',
  ],
}

const link: UiDoc = {
  name: 'SpendCapSettingsLink',
  keywords: ['spend', 'cap', 'budget', 'settings', 'guardrails', 'usage', 'link'],
  description:
    'The link to the Settings page a spend ceiling\'s refusal is lifted on, in the words every cap surface uses ("Open Settings → Guardrails"). Renders nothing for a page it does not know.',
  props: [
    { name: 'settings', description: 'The route id of the Settings page (`guardrails`, `usage`), as the gateway names it (`BudgetExceededError.settings_page`).' },
    { name: 'size', description: 'Text size of the link: `xs` (default) beside a notice, `sm` in a panel\'s action row.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Use it wherever a cap refusal is shown, so every surface sends the user to the same place in the same words.' },
  ],
  anatomy: ['emphasis TextLink "Open Settings → <page>" with a trailing arrow'],
}

export default [pause, link]
