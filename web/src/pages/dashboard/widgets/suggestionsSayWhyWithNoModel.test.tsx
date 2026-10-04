import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { RouteProps } from '../../../app/useQueryState'
import { Suggestions } from './Suggestions'

// ── The dashboard's Suggestions say why they are the standard list, as the new-chat page does ────
//
// The list comes from a background call that runs on a model, never on an agent CLI. With no model
// chosen for it the gateway answers the standard list and the sentence that says why
// (`needs_model`); the widget shows it under the list as the way to Settings → Models.

const NEED = 'Suggestions need a model: choose one in Settings → Models.'
let needs_model = NEED

vi.mock('../../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      suggestions: async () => ({
        suggestions: ['Show health-check status', 'Set up a daily briefing'],
        generated_at: 1,
        stale: false,
        refreshing: false,
        needs_model,
      }),
    },
  }
})

const route: RouteProps = { sub: '', navigate: () => {}, navEpoch: 0, query: {}, setQuery: () => {} }

beforeEach(() => { needs_model = NEED })

describe("the dashboard's Suggestions with no model chosen", () => {
  it('lists the standard suggestions and says why, as the way to Settings → Models', async () => {
    render(<Suggestions {...route} />)
    expect(await screen.findByText('Set up a daily briefing')).toBeTruthy()
    const why = await screen.findByRole('link', { name: NEED })
    expect(why.getAttribute('href')).toBe('#/settings/models')
  })

  it('says nothing about a model once one is chosen', async () => {
    needs_model = ''
    render(<Suggestions {...route} />)
    expect(await screen.findByText('Set up a daily briefing')).toBeTruthy()
    expect(screen.queryByRole('link', { name: /need a model/ })).toBeNull()
  })
})
