import type { ModelItem, SwitchPrice } from '../../lib/api'

// ── The one ask before a mid-chat switch to a dearer model ────────────────────────────────────────
//
// A chat's next turn re-reads the whole conversation, so switching its model is paid for at once, at
// the new model's input price. On a long chat (the gateway's threshold, 50,000 tokens) the
// agent-and-model pill asks once before a switch that may cost more, with both prices: "The next
// reply re-reads about 120,000 tokens on large-1: about $0.30, against about $0.06 on small-1." The
// gateway decides whether to ask and prices both models (`GET /api/chat/switch-price`), so the
// prices the owner set apply. Once the owner answers Switch in a chat, that chat is not asked again.
// Context and cost stay out of sight otherwise: the numbers appear only at a switch that may cost
// more.

/** What the pill asks before a switch of model, or `null` to switch without asking. */
export interface SwitchQuestion {
  /** The sentence with both prices, or why they could not be read. */
  said: string
  /** Called once the owner answers Switch. */
  switched: () => void
}

/** Asked with the model row picked; resolves the question to put, or `null` for none. */
export type AskBeforeSwitch = (model: ModelItem) => Promise<SwitchQuestion | null>

/** A price as the question says it: "about $0.30", or "price not set" for a model nothing prices. */
export function priceSaid(dollars: number | null): string {
  if (dollars === null) return 'price not set'
  if (dollars > 0 && dollars < 0.01) return 'under $0.01'
  return `about $${dollars.toFixed(2)}`
}

/** "about 120,000": a measured context, to the nearest thousand from a thousand up. */
function tokensSaid(tokens: number): string {
  const shown = tokens >= 1000 ? Math.round(tokens / 1000) * 1000 : tokens
  return `about ${shown.toLocaleString('en-US')}`
}

/** The question for a switch the gateway asked about, with both models named by their ids. */
export function switchSentence(price: SwitchPrice): string {
  const tokens = price.tokens ?? 0
  return `The next reply re-reads ${tokensSaid(tokens)} tokens on ${price.next.model}: `
    + `${priceSaid(price.next.dollars)}, against ${priceSaid(price.current.dollars)} on ${price.current.model}.`
}

const ANSWERED = 'composer:dearer-switch-answered'
/** The chats remembered as answered, newest last: enough for every chat a person keeps open. */
const KEEP = 200

function answeredChats(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(ANSWERED) ?? '[]')
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
  } catch { return [] }
}

/** Whether the owner already answered Switch in the chat `session`, in this browser. */
export function switchAnswered(session: string): boolean {
  return answeredChats().includes(session)
}

/** Remember that the owner answered Switch in the chat `session`: later switches there do not ask. */
export function rememberSwitchAnswered(session: string): void {
  const kept = [...answeredChats().filter((k) => k !== session), session].slice(-KEEP)
  try { localStorage.setItem(ANSWERED, JSON.stringify(kept)) } catch { /* a full or blocked store: the chat asks again */ }
}
