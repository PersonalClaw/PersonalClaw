import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The Models tile must speak when nothing is bound ──────────────────────────────────────────────
//
// On a never-configured instance the Models tile rendered 4 bare '—' rows (chat/embed/stt/tts all
// unbound) while every sibling tile on the settings bento speaks a sentence for its empty state
// (Routing: "Per-model success… land here…", Web Search: "DuckDuckGo (keyless) is the default…").
// The one tile that matters most on first run was the only mute one. The fix: ALL-unbound renders
// words echoing the DegradedChip's 'Set up a model' chrome invitation; a PARTIAL binding keeps the
// KVList where a single '—' beside bound rows carries meaning by contrast.

const src = readFileSync(join(__dirname, 'settingsWidgets.tsx'), 'utf-8')

describe('the Models tile speaks when nothing is bound', () => {
  it('renders the sibling-idiom empty sentence for the all-unbound case', () => {
    expect(src).toContain(
      'No models bound yet. Set up a model provider and the bindings for chat, embeddings, and voice appear here.',
    )
  })

  it('the empty sentence is gated on anyBound so a partial binding keeps the KVList', () => {
    // Structural pin: the words branch must be the alternative of an anyBound test,
    // not an unconditional replacement of the KVList.
    const modelsTile = src.slice(src.indexOf("id: 'models'"), src.indexOf("id: 'routing'"))
    expect(modelsTile, 'the tile still derives anyBound from the CORE use cases').toMatch(/anyBound/)
    expect(modelsTile, 'partial bindings still render the KVList').toContain('anyBound ? <KVList')
    expect(modelsTile, 'the per-row dash for a partially-bound list survives').toContain("vText: bound ? shortModel(bound) : '—'")
  })
})
