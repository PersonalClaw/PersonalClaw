import type { UiDoc } from './uiDoc'

// Doc object for TurnStatusRow — the running turn's live status, pinned above the composer
// (web/REDESIGN.md, "Live state sits at the bottom").
const doc: UiDoc = {
  name: 'TurnStatusRow',
  keywords: ['turn', 'status', 'live', 'running', 'thinking', 'tool', 'streaming', 'waiting', 'elapsed', 'tokens', 'stop', 'escape', 'orb'],
  description:
    "The running turn's live status in one row, pinned at the bottom of a conversation just above its composer, outside the transcript's scroll: shown a moment after a turn starts, whatever the scroll, and gone when it ends. It holds the agent orb in the turn's own phase; what the turn is doing in the gateway's words (the tool by name), else the phase's word, which is its one polite live region; while it works, a word from PersonalClaw's own list that turns over and is never said; the time since the turn began; the tokens the runtime reported for the turn, in and out, and nothing while none is reported, never an estimate; and Stop, which Esc presses too unless a dialog, a control or a field of its own took the key. Under reduced motion no word turns over and the orb draws one static frame.",
  props: [
    { name: 'running', description: 'Whether a turn runs: the row shows SHOW_DELAY_MS after it starts and goes when it ends.' },
    { name: 'phase', description: "The turn's phase from the phase contract, null until the gateway has said one (drawn as waiting to start)." },
    { name: 'line', description: "The gateway's line for the phase: the tool by name while one runs. Empty falls back to the phase's word." },
    { name: 'startedAt', description: 'When the turn began (epoch ms), from its run state; null counts from when the row first saw it running.' },
    { name: 'lastStepAt', description: "When the turn's runtime last did anything (epoch ms, the run state's last_step_at); after a minute of nothing in a working phase the row says No new activity for 1m. Null counts from when the turn began." },
    { name: 'usage', description: 'The tokens the runtime reported for the turn so far, in and out; null (or zero) while it has reported none, and nothing is shown.' },
    { name: 'onStop', description: 'Stop the turn: the Stop button, and Esc while the shortcut is on.' },
    { name: 'stopShortcut', description: 'Whether Esc presses Stop (default true). Off where another control owns Esc.' },
    { name: 'onShown', description: 'Called when the row appears, so a transcript that follows its end can keep its end in view above the row.' },
    { name: 'className', description: 'Classes for the row (a margin from the composer).' },
  ],
  bestPractices: [
    { guidance: true, description: "Put it outside the transcript's scroll container, just above the composer, so it takes its own height and never covers the last message." },
    { guidance: true, description: "Pass usage only from the runtime's own reports (token_usage frames); leave it null otherwise." },
    { guidance: true, description: 'Mark the composer with data-turn-stop-scope so Esc typed there stops the turn, as it does outside any field.' },
    { guidance: false, description: 'Do not draw a second live indicator at the head of the turn: this row is the running turn\'s one live status.' },
    { guidance: false, description: 'Do not estimate tokens from the text: show what was reported or nothing.' },
  ],
  anatomy: [
    'div[data-turn-status][data-phase] — the row',
    'AgentOrb at 20 in the phase',
    'span[data-turn-state] — what the turn is doing',
    'span[data-turn-word][aria-hidden] — the turning word, while it works and motion is welcome',
    'span[data-turn-elapsed] — the time since it began',
    'span[data-turn-tokens] — the reported tokens, in and out, only when reported',
    'Button "Stop this turn" with an Esc hint — a 44 px target at the compact widths',
    'span[role=status][aria-live=polite].sr-only — the state, said once per change',
  ],
}

export default doc
