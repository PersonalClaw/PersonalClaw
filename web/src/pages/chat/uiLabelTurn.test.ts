/** A widget action's turn shows the HUMAN message — live and after a reload (AS-6 §5.4).
 *
 *  The clause: *"a chat-born widget action becomes the next user turn showing
 *  humanFriendlyMessage (not raw JSON)"*. Two halves, and the second is the one that gets
 *  forgotten: the turn is honest live and shows raw JSON again after F5, because only the
 *  machine payload was persisted. So `hydrateTurns` — the function that rebuilds a transcript
 *  from what the backend stored — is driven directly here, with a VACUITY leg proving the
 *  assertion can fail (the same message WITHOUT `meta.ui_label` renders the JSON).
 *
 *  🪤 The live-send half is asserted on ChatPage's SOURCE, not by rendering it. `ChatPage.tsx`
 *  is a 4k-line page that owns a socket, a composer and a dozen panels; there is no harness in
 *  this suite that mounts it, and inventing one to check two call sites would be a bigger
 *  fixture than the feature. What is checked is exactly the wiring a render would prove: the
 *  bridge consumer forwards `meta.label` as `uiLabel`, the local turn is built from `uiLabel`
 *  first, and `meta.ui_label` is what gets persisted. Named as a limitation rather than
 *  presented as a render test. */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, it, expect } from 'vitest'
import { hydrateTurns, type HistMsg } from './chatTypes'

const MACHINE = '[UI] log_expense: {"amount":"12.40","vendor":"Acme"}'
const LABEL = 'Log expense'

const chatSource = readFileSync(join(process.cwd(), 'src/pages/ChatPage.tsx'), 'utf8')

function turnsFor(meta?: HistMsg['meta']) {
  return hydrateTurns([{ role: 'user', content: MACHINE, ts: '2026-08-24T00:00:00Z', meta }])
}

function textOf(turn: { segments: { kind: string; text?: string }[] }): string {
  return turn.segments.filter((s) => s.kind === 'text').map((s) => s.text ?? '').join('')
}

describe('a reloaded widget-action turn', () => {
  it('renders the human label and NOT the machine payload', () => {
    const [turn] = turnsFor({ ui_label: LABEL })
    expect(textOf(turn)).toBe(LABEL)
    // The pair. Either assertion alone passes while the defect is present: the label can be
    // there with the JSON beside it, and "no JSON" is satisfied by an empty bubble.
    expect(textOf(turn)).not.toContain('amount')
    expect(textOf(turn)).not.toContain('{')
  })

  it('does not stash the payload in the optimized-prompt disclosure either', () => {
    // `optimized` renders a collapsed "Optimized — revert to original" section. Reusing it
    // here would put the JSON back on screen under a chip that is also untrue.
    const [turn] = turnsFor({ ui_label: LABEL })
    expect(turn.optimized).toBeUndefined()
  })

  it('VACUITY: the same message without the label DOES render the raw payload', () => {
    // Proves the assertions above are about `ui_label` and not about hydrateTurns dropping
    // content in general.
    const [turn] = turnsFor(undefined)
    expect(textOf(turn)).toBe(MACHINE)
    expect(textOf(turn)).toContain('{')
  })

  it('still honors the optimized-prompt provenance when there is no widget label', () => {
    const [turn] = turnsFor({ original: 'log an expense please' })
    expect(textOf(turn)).toBe('log an expense please')
    expect(turn.optimized).toBe(MACHINE)
  })
})

describe('the live send path (source-asserted — ChatPage is not mountable here)', () => {
  it('forwards the bridge label as the turn label', () => {
    expect(chatSource).toContain("useWidgetActionBridge((text, meta) => { void send(text, { uiLabel: meta.label }) })")
  })

  it('drains a staged non-chat action WITH its label', () => {
    expect(chatSource).toContain('void send(pending.text, { uiLabel: pending.label })')
  })

  it('builds the local bubble from the label first', () => {
    expect(chatSource).toContain('userTurn(uiLabel ?? original ?? t,')
  })

  it('persists the label so the reload path above has something to read', () => {
    expect(chatSource).toContain('if (uiLabel) meta.ui_label = uiLabel')
  })

  it('sends the MACHINE text to the model, never the label', () => {
    // `llmText` is the content the backend stores and the model reads. If this became
    // `uiLabel`, the transcript would look right and the agent would receive a bare label.
    expect(chatSource).toContain('await api.sendChat(llmText, sid, meta, undefined, opts?.inputOrigin)')
  })
})
