import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import type { ModelRatesView } from '../../lib/api'

// ── "Model prices": where a model gets the price its calls are counted at ────────────────────
//
// A price is what the daily dollar cap, this page's dollars and cost-aware routing count a model's
// calls at, and `model_rates.json` held it with no control anywhere: the page told the owner to
// edit a file in their home to price a model the cap could not count. The section lists each model
// a use is bound to with its price and where that comes from, sets one rate at a time, and every
// field the file holds is a field here.

const UNPRICED = 'acme-cloud:acme-large'
const LOCAL = 'here:llama3.1'

const VIEW: ModelRatesView = {
  rates: [],
  models: [
    { ref: UNPRICED, priced: false, source: '', in_per_mtok: null, out_per_mtok: null, cache_read_per_mtok: null, cache_write_per_mtok: null },
    { ref: LOCAL, priced: true, source: 'local', in_per_mtok: 0, out_per_mtok: 0, cache_read_per_mtok: null, cache_write_per_mtok: null },
  ],
  unreadable: '',
}

const mount = async (view: ModelRatesView, writes: Record<string, (...a: unknown[]) => unknown> = {}) => {
  vi.resetModules()
  vi.doMock('../../lib/api', () => ({
    api: {
      usageTotals: () => Promise.resolve({ totals: null }),
      usageRollup: () => Promise.resolve({ rows: [] }),
      usageFold: () => Promise.resolve(null),
      usageBudget: () => Promise.resolve(null),
      modelRates: () => Promise.resolve(view),
      personalclawConfig: () => Promise.resolve(null),
      system: () => Promise.resolve({ stats: null }),
      ...writes,
    },
  }))
  const { UsagePanel } = await import('./UsagePanel')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<UsagePanel query={{ period: 'today' }} setQuery={() => {}} />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

const typeInto = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } })

describe('Model prices', () => {
  it('lists each bound model with its price and where it comes from, and a model with none', async () => {
    const { container } = await mount(VIEW)
    const text = container.textContent ?? ''
    expect(text).toContain('Model prices')
    // A dollar cap refuses a call it has no price for, and the row says so and how to lift it.
    expect(text).toContain(
      `${UNPRICED}No price: its calls count toward no dollar figure, and a daily dollar cap refuses `
      + 'them. Set its price, or $0 if it costs nothing.',
    )
    expect(text).toContain('$0 in · $0 out per 1M tokens — free: it runs on this machine')
    expect(screen.getByRole('button', { name: `Set a price for ${UNPRICED}` })).toBeTruthy()
  })

  it('sets a price for a model nothing prices, every field of the rate included', async () => {
    const priced: ModelRatesView = {
      ...VIEW,
      rates: [{ key: UNPRICED, in_per_mtok: 3, out_per_mtok: 15, cache_read_per_mtok: 0.3, cache_write_per_mtok: null }],
      models: [{ ...VIEW.models[0], priced: true, source: 'overlay', in_per_mtok: 3, out_per_mtok: 15, cache_read_per_mtok: 0.3 }, VIEW.models[1]],
    }
    const setModelRate = vi.fn(() => Promise.resolve(priced))
    const { container } = await mount(VIEW, { setModelRate })

    fireEvent.click(screen.getByRole('button', { name: `Set a price for ${UNPRICED}` }))
    typeInto('Input, $ per 1M tokens', '3')
    typeInto('Output, $ per 1M tokens', '15')
    typeInto('Cache read, $ per 1M tokens', '0.3')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save' }))
      await new Promise((res) => setTimeout(res, 0))
    })

    expect(setModelRate).toHaveBeenCalledWith({
      key: UNPRICED,
      in_per_mtok: 3,
      out_per_mtok: 15,
      cache_read_per_mtok: 0.3,
      cache_write_per_mtok: null,
    })
    expect(container.textContent).toContain('$3 in · $15 out · $0.3 cache read per 1M tokens — your price')
    expect(screen.getByRole('button', { name: `Remove the price for ${UNPRICED}` })).toBeTruthy()
  })

  it('says what is wrong with a rate before it is sent', async () => {
    const setModelRate = vi.fn()
    await mount(VIEW, { setModelRate })

    fireEvent.click(screen.getByRole('button', { name: `Set a price for ${UNPRICED}` }))
    typeInto('Input, $ per 1M tokens', '3')
    // The reason rides the button (its tooltip and accessible description), as the kit's do.
    const save = () => screen.getByRole('button', { name: 'Save' })
    expect(save().getAttribute('aria-disabled')).toBe('true')
    expect(save().getAttribute('title')).toBe('Give the output rate, in dollars per 1M tokens.')
    typeInto('Output, $ per 1M tokens', '-2')
    expect(save().getAttribute('title')).toBe('The output rate must be zero or more dollars.')
    fireEvent.click(save())
    expect(setModelRate).not.toHaveBeenCalled()
  })

  it('adds a price for a pattern, and removes one', async () => {
    const withPattern: ModelRatesView = {
      ...VIEW,
      rates: [{ key: 'anthropic:claude-*', in_per_mtok: 1, out_per_mtok: 5, cache_read_per_mtok: null, cache_write_per_mtok: null }],
    }
    const setModelRate = vi.fn(() => Promise.resolve(withPattern))
    const clearModelRate = vi.fn(() => Promise.resolve())
    const { container } = await mount(VIEW, { setModelRate, clearModelRate })

    fireEvent.click(screen.getByRole('button', { name: /Add a price/ }))
    typeInto('Model', 'anthropic:claude-*')
    typeInto('Input, $ per 1M tokens', '1')
    typeInto('Output, $ per 1M tokens', '5')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save' }))
      await new Promise((res) => setTimeout(res, 0))
    })
    expect(setModelRate).toHaveBeenCalledWith(expect.objectContaining({ key: 'anthropic:claude-*' }))
    const row = within(container).getByText('anthropic:claude-*').closest('div.rounded-lg') as HTMLElement
    expect(row.textContent).toContain('$1 in · $5 out per 1M tokens — your price')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Remove the price for anthropic:claude-*' }))
      await new Promise((res) => setTimeout(res, 0))
    })
    expect(clearModelRate).toHaveBeenCalledWith('anthropic:claude-*')
  })

  it('says when the price file cannot be read', async () => {
    const { container } = await mount({ ...VIEW, unreadable: 'model_rates.json is not valid JSON (line 3)' })
    expect(container.textContent).toContain(
      'Unreadable — model_rates.json is not valid JSON (line 3). No price in it is in effect',
    )
  })
})
