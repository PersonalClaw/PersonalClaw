import type { UiDoc } from './uiDoc'

// Doc object for RungChip — the earned-autonomy rung indicator (AUTONOMY-GUARDRAILS §6.1).
const doc: UiDoc = {
  name: 'RungChip',
  keywords: ['autonomy', 'rung', 'ladder', 'unattended', 'guardrails', 'undo', 'permission', 'chip'],
  description:
    'A compact chip stating how much an automation may do on its own: drafts only, asks first, runs with undo, or runs on its own. The visible label is the rung in behaviour words; the tooltip carries the server-composed provenance sentence (a declared floor, a promotion the user clicked with the evidence record, or a granted rung the incident kill switch is holding down). Draws the rung the action type ACTUALLY resolves to and appends "· held" while an incident clamps it.',
  props: [
    { name: 'type', description: 'One AutonomyType row from GET /api/autonomy — the chip reads its resolved rung, its held_by_incident flag and its authority sentence.' },
    { name: 'ladder', description: 'The fetched ladder, for the server-owned rung wording. Omit (or pass null) and the chip falls back to the humanized rung key rather than inventing a second phrase.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Feed it an AutonomyType row from GET /api/autonomy (api.autonomyLadder) plus the ladder itself — the rung WORDING is server-owned so a chip, the ladder panel and the promotion proposal cannot disagree.' },
    { guidance: true, description: 'Render nothing when no action type governs the provider: an undeclared action keeps its pre-ladder behaviour, and an absent chip is the honest answer.' },
    { guidance: false, description: 'Do not substitute a rung when the ladder read fails — "runs on its own" is a claim about unattended authority, so a failed read means no chip (the Settings panel is the surface that reports the failure).' },
    { guidance: false, description: 'Do not re-tone it to warn/danger: the tone scale here is permission INTENSITY, and a trigger row already spends warn/danger on whether the automation is failing.' },
  ],
  anatomy: ['tinted pill', 'rung glyph (FileText / ShieldQuestion / Undo2 / Zap)', 'behaviour label', 'optional "· held" suffix while an incident clamps the rung'],
}

export default doc
