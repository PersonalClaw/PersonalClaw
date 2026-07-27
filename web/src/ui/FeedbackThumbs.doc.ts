import type { UiDoc } from './uiDoc'

// Doc object for FeedbackThumbs — the quiet 👍/👎 pair on AI judgment outputs
// (FEEDBACK-SIGNAL plan 58). Judgment surfaces ONLY — never chat messages.
const doc: UiDoc = {
  name: 'FeedbackThumbs',
  keywords: ['feedback', 'thumbs', 'accuracy', 'verdict', 'judgment', 'up', 'down', 'signal', 'learning'],
  description:
    "A quiet 👍/👎 pair mounted on AI JUDGMENT outputs (inbox classification/draft/digest cards, loop finding rows). 👍 is silent-positive ('Mark accurate' — recorded only for the accuracy denominator, it never implies 'I'll learn from this'); 👎 opens an optional one-line 'why' popover (Enter/click-away records without a reason, Esc cancels). State hydrates from GET /api/feedback/target on mount, reflects as a filled thumb, and is reversible (re-thumbing supersedes). Renders nothing when the feedback kill-switch is off (backend 404 → hide).",
  props: [
    { name: 'targetKind', type: "FeedbackTargetKind", required: true, description: "The judgment kind — a member of the CLOSED vocabulary (inbox_classification | inbox_draft | inbox_digest | loop_finding | routing_suggestion | proposal_content | app_judgment). A new surface must extend the backend vocabulary first." },
    { name: 'targetId', type: 'string', required: true, description: 'The judged thing (inbox item id, finding path, suggestion id…).' },
    { name: 'producer', type: 'FeedbackProducer', required: false, description: "The producing artifact the verdict attributes to — {producer_kind, producer_id} carried in the card's API payload (feedback_producers / feedback_producer meta)." },
    { name: 'snapshot', type: 'Record<string, unknown>', required: false, description: 'The judgment AS SHOWN (e.g. {classification, confidence}) so accuracy survives later edits.' },
    { name: 'className', type: 'string', required: false, description: 'Extra classes on the wrapping span.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Mount ONLY on discrete AI judgment outputs. Chat messages are explicitly not a target surface (MessageActions.tsx\'s "no decorative thumbs" stance stands — the after-turn review owns that signal).' },
    { guidance: true, description: "Pass the producer meta from the card's payload (feedback_producers on inbox items, feedback_producer on loop views) — a verdict without provenance can't feed per-producer accuracy." },
    { guidance: true, description: "Keep the affordance quiet: it sits inline at the judgment block's edge, never as a call-to-action. Thumbs fatigue kills the signal." },
    { guidance: false, description: "Do not add copy implying 👍 teaches anything — it is silent-positive by owner ruling; only 👎 (with an optional why) feeds learning." },
  ],
  anatomy: ['ThumbsUp button (aria-pressed, fill on selection)', 'ThumbsDown button', "optional 'why?' popover: one-line input + skip hints + click-away scrim"],
}

export default doc
