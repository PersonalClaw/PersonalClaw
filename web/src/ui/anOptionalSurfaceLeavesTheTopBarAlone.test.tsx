import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { DegradedChip } from './DegradedChip'
import { api } from '../lib/api'
import { tipOf } from '../test/tipOf'

// ── An optional surface with no model chosen leaves the top bar alone ────────────────────────────
//
// Measured before this was written: with Chat and Embedding bound and no speech-to-text app, the
// report listed Transcription down with no model chosen, and the chip read "Choose a model" on every
// page. Transcription needs an app most people never install, so a finished setup read as
// unfinished. The report now marks a surface `optional`; with no model chosen for it, it is off by
// choice, and the chip leaves it out. Once a model is chosen for it and cannot serve, it counts again.

const TRANSCRIPTION = { surface: 'transcription', label: 'Transcription', available: false, floor: 'Speech-to-text is off', backlog: 0, use_cases: ['stt'], model_chosen: false, optional: true }
const SEARCH = { surface: 'search_ranking', label: 'Semantic search', available: false, floor: 'Keyword ranking', backlog: 0, use_cases: ['embedding'], model_chosen: false, optional: false }

beforeEach(() => {
  vi.spyOn(api, 'onboarding').mockResolvedValue({ has_model_provider: true } as never)
})
afterEach(() => { vi.restoreAllMocks() })

function reportSays(surfaces: unknown[]) {
  vi.spyOn(api, 'degraded').mockResolvedValue({ surfaces } as never)
}

describe('the header model chip and an optional surface', () => {
  it('🔴 shows nothing when the only surface without a model is an optional one', async () => {
    reportSays([TRANSCRIPTION, { ...SEARCH, available: true }])
    const { container } = render(<DegradedChip />)
    await waitFor(() => expect(api.degraded).toHaveBeenCalled())
    await waitFor(() => expect(api.onboarding).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 20))
    expect(container.textContent).toBe('')
  })

  it('🔴 names only the surface that needs a model beside it', async () => {
    reportSays([TRANSCRIPTION, SEARCH])
    render(<DegradedChip />)
    const chip = await screen.findByRole('button', { name: /choose a model/i })
    expect(tipOf(chip)).toBe(
      'No model is chosen for Semantic search — click to see what unlocks once you choose one')
  })

  it('counts an optional surface whose chosen model cannot serve', async () => {
    reportSays([{ ...TRANSCRIPTION, model_chosen: true }])
    render(<DegradedChip />)
    const chip = await screen.findByRole('button', { description: /running without a model/i })
    expect(chip.textContent).toContain('Transcription degraded')
  })

  it('still asks for a model for a surface that is not optional', async () => {
    reportSays([SEARCH])
    render(<DegradedChip />)
    expect(await screen.findByRole('button', { name: /choose a model/i })).toBeTruthy()
  })
})
