import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import type { ModelRatesView } from '../../lib/api'

// ── "Model prices": where a model gets the price its calls are counted at ────────────────────
//
// A price is what the daily dollar cap, this page's dollars and cost-aware routing count a model's
// calls at, and the file that held it had no control anywhere: the page told the owner to edit a
// file in their home to price a model the cap could not count. The section lists each model a use
// is bound to with its price, where that comes from and its date, sets your price for any of them
// (a known one included) one rate at a time, shows the default yours stands in front of and resets
// to it, and every field a price holds is a field here. A price is in the unit its model is billed
// in: per 1M tokens, per image (by size and quality), per second of video, per minute of audio or
// per 1M characters of speech.

const UNPRICED = 'acme-cloud:acme-large'
const LOCAL = 'here:llama3.1'
const SHIPPED = 'bedrock:global.anthropic.claude-sonnet-5-5'
const CANVAS = 'bedrock:amazon.nova-canvas-v1:0'

type Listed = ModelRatesView['models'][number]
const NO_UNIT = { per_unit: null, tiers: [], default_size: '', default_quality: '' }
const NO_TOKENS = { in_per_mtok: null, out_per_mtok: null, cache_read_per_mtok: null, cache_write_per_mtok: null }
const listed = (row: Partial<Listed> & Pick<Listed, 'ref'>): Listed => ({
  priced: false, source: '', vendor: '', recorded: '', priced_as: '', unit: '', default: null,
  ...NO_TOKENS, ...NO_UNIT, ...row,
})

const VIEW: ModelRatesView = {
  rates: [],
  models: [
    listed({ ref: UNPRICED }),
    listed({ ref: LOCAL, priced: true, source: 'local', unit: 'token', in_per_mtok: 0, out_per_mtok: 0 }),
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
    // A model this machine runs is free in whatever unit it is billed in: no unit to name.
    expect(text).toContain('$0 — free: it runs on this machine')
    expect(screen.getByRole('button', { name: `Set a price for ${UNPRICED}` })).toBeTruthy()
  })

  it('sets a price for a model nothing prices, every field of the rate included', async () => {
    const priced: ModelRatesView = {
      ...VIEW,
      rates: [{ key: UNPRICED, unit: 'token', in_per_mtok: 3, out_per_mtok: 15, cache_read_per_mtok: 0.3, cache_write_per_mtok: null, recorded: '2026-10-01' }],
      models: [{ ...VIEW.models[0], priced: true, source: 'overlay', recorded: '2026-10-01', unit: 'token', in_per_mtok: 3, out_per_mtok: 15, cache_read_per_mtok: 0.3 }, VIEW.models[1]],
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
      unit: 'token',
      in_per_mtok: 3,
      out_per_mtok: 15,
      cache_read_per_mtok: 0.3,
      cache_write_per_mtok: null,
    })
    expect(container.textContent).toContain('$3 in · $15 out · $0.3 cache read per 1M tokens — your price, set 2026-10-01')
    // Nothing else prices it, so removing yours leaves it unpriced, and the row says so.
    expect(container.textContent).toContain('No default: without your price nothing prices it.')
    expect(screen.getByRole('button', { name: `Remove your price for ${UNPRICED}` })).toBeTruthy()
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
      rates: [{ key: 'anthropic:claude-*', unit: 'token', in_per_mtok: 1, out_per_mtok: 5, cache_read_per_mtok: null, cache_write_per_mtok: null, recorded: '' }],
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

  it('says whose list price a shipped price is, the day it was recorded and the row it is', async () => {
    const { container } = await mount({
      ...VIEW,
      models: [listed({
        ref: SHIPPED, priced: true, source: 'builtin', vendor: 'Anthropic', recorded: '2026-10-01',
        priced_as: 'claude-sonnet-5.5', unit: 'token', in_per_mtok: 2, out_per_mtok: 10,
        cache_read_per_mtok: 0.2, cache_write_per_mtok: 2.5,
      })],
    })
    expect(container.textContent).toContain(
      '$2 in · $10 out · $0.2 cache read · $2.5 cache write per 1M tokens — Anthropic’s list price, '
      + 'recorded 2026-10-01, as claude-sonnet-5.5 (PersonalClaw’s price table)',
    )
    // Prices change, and whoever runs a model may bill differently: the page says so, and where.
    expect(container.textContent).toContain('whoever runs the model may bill differently, so set your own if it does')
  })

  it('shows an image model’s price per image, by its size and quality', async () => {
    const { container } = await mount({
      ...VIEW,
      models: [listed({
        ref: CANVAS, priced: true, source: 'builtin', vendor: 'Amazon', recorded: '2026-10-01',
        priced_as: 'amazon.nova-canvas-v1:0', unit: 'image', default_size: '1024x1024', default_quality: 'standard',
        tiers: [
          { size: '1024x1024', quality: 'standard', per_image: 0.04 },
          { size: '2048x2048', quality: 'premium', per_image: 0.08 },
        ],
      })],
    })
    expect(container.textContent).toContain(
      'up to 1024x1024 standard $0.04 · up to 2048x2048 premium $0.08 per image (made at 1024x1024 '
      + 'standard unless asked) — Amazon’s list price, recorded 2026-10-01 (PersonalClaw’s price table)',
    )
  })

  it('sets a price per image for a model billed by the image', async () => {
    const setModelRate = vi.fn(() => Promise.resolve(VIEW))
    await mount(VIEW, { setModelRate })

    fireEvent.click(screen.getByRole('button', { name: `Set a price for ${UNPRICED}` }))
    typeInto('Billed', 'image')
    typeInto('Price, $ per image', '0.05')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save' }))
      await new Promise((res) => setTimeout(res, 0))
    })

    expect(setModelRate).toHaveBeenCalledWith({ key: UNPRICED, unit: 'image', per_image: 0.05 })
  })

  it('sets an image model’s prices by size and quality', async () => {
    const setModelRate = vi.fn(() => Promise.resolve(VIEW))
    await mount(VIEW, { setModelRate })

    fireEvent.click(screen.getByRole('button', { name: `Set a price for ${UNPRICED}` }))
    typeInto('Billed', 'image')
    typeInto('Image price', 'tiers')
    typeInto('Size up to, price 1', '1024x1024')
    typeInto('$ per image, price 1', '0.04')
    fireEvent.click(screen.getByRole('button', { name: /Add a size or quality/ }))
    typeInto('Size up to, price 2', 'big')
    const save = () => screen.getByRole('button', { name: 'Save' })
    expect(save().getAttribute('title')).toBe('A size is a width x height in pixels, such as 1024x1024.')
    typeInto('Size up to, price 2', '2048x2048')
    typeInto('$ per image, price 2', '0.06')
    typeInto('Default size', '1024x1024')
    await act(async () => {
      fireEvent.click(save())
      await new Promise((res) => setTimeout(res, 0))
    })

    expect(setModelRate).toHaveBeenCalledWith({
      key: UNPRICED,
      unit: 'image',
      tiers: [
        { size: '1024x1024', quality: '', per_image: 0.04 },
        { size: '2048x2048', quality: '', per_image: 0.06 },
      ],
      default_size: '1024x1024',
      default_quality: '',
    })
  })

  it.each([
    ['second', 'Price, $ per second of video', { per_second: 0.08 }, '0.08'],
    ['minute', 'Price, $ per minute of audio', { per_minute: 0.006 }, '0.006'],
    ['character', 'Price, $ per 1M characters', { per_mchar: 15 }, '15'],
  ])('sets a price per %s', async (unit, label, fields, typed) => {
    const setModelRate = vi.fn(() => Promise.resolve(VIEW))
    await mount(VIEW, { setModelRate })

    fireEvent.click(screen.getByRole('button', { name: `Set a price for ${UNPRICED}` }))
    typeInto('Billed', unit)
    typeInto(label, typed)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save' }))
      await new Promise((res) => setTimeout(res, 0))
    })

    expect(setModelRate).toHaveBeenCalledWith({ key: UNPRICED, unit, ...fields })
  })

  it('says when the configuration your prices are kept in cannot be read', async () => {
    const { container } = await mount({ ...VIEW, unreadable: 'config.json is not valid JSON (line 3)' })
    expect(container.textContent).toContain(
      'Unreadable — config.json is not valid JSON (line 3). No price you set is in effect',
    )
  })

  it('overrides a known price with yours, shows the default beneath it, and resets to it', async () => {
    const LIST = {
      source: 'builtin' as const, vendor: 'Anthropic', recorded: '2026-10-01', priced_as: 'claude-sonnet-5.5',
      unit: 'token' as const, ...NO_TOKENS, ...NO_UNIT, in_per_mtok: 2, out_per_mtok: 10,
    }
    const known: ModelRatesView = { ...VIEW, models: [listed({ ref: SHIPPED, priced: true, ...LIST })] }
    const mine: ModelRatesView = {
      ...VIEW,
      rates: [{ key: SHIPPED, unit: 'token', in_per_mtok: 2.5, out_per_mtok: 12, cache_read_per_mtok: null, cache_write_per_mtok: null, recorded: '2026-10-02' }],
      models: [listed({
        ref: SHIPPED, priced: true, source: 'overlay', recorded: '2026-10-02', unit: 'token',
        in_per_mtok: 2.5, out_per_mtok: 12, default: LIST,
      })],
    }
    const setModelRate = vi.fn(() => Promise.resolve(mine))
    const clearModelRate = vi.fn(() => Promise.resolve())
    const modelRates = vi.fn().mockResolvedValueOnce(known).mockResolvedValue(known)
    const { container } = await mount(known, { setModelRate, clearModelRate, modelRates })

    // A known price can be given your own; the form starts from the list price.
    fireEvent.click(screen.getByRole('button', { name: `Set your own price for ${SHIPPED}` }))
    expect((screen.getByLabelText('Input, $ per 1M tokens') as HTMLInputElement).value).toBe('2')
    typeInto('Input, $ per 1M tokens', '2.5')
    typeInto('Output, $ per 1M tokens', '12')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save' }))
      await new Promise((res) => setTimeout(res, 0))
    })
    expect(setModelRate).toHaveBeenCalledWith(expect.objectContaining({ key: SHIPPED, in_per_mtok: 2.5, out_per_mtok: 12 }))
    const text = container.textContent ?? ''
    expect(text).toContain('$2.5 in · $12 out per 1M tokens — your price, set 2026-10-02')
    expect(text).toContain(
      'Default: $2 in · $10 out per 1M tokens — Anthropic’s list price, recorded 2026-10-01, '
      + 'as claude-sonnet-5.5 (PersonalClaw’s price table)',
    )

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: `Reset the price for ${SHIPPED} to its default` }))
      await new Promise((res) => setTimeout(res, 0))
    })
    expect(clearModelRate).toHaveBeenCalledWith(SHIPPED)
    expect(container.textContent).toContain('$2 in · $10 out per 1M tokens — Anthropic’s list price')
  })

  it('names the app a provider app’s price comes from', async () => {
    const { sourceText } = await import('./ModelPricesSection')
    expect(sourceText({ ref: 'work:m', source: 'app_default', vendor: 'bedrock', recorded: '', priced_as: '' }))
      .toBe('the bedrock app’s price')
    expect(sourceText({ ref: 'work:m', source: 'overlay', vendor: '', recorded: '2026-10-02', priced_as: '' }))
      .toBe('your price, set 2026-10-02')
  })
})

describe('Usage counts a call billed by its unit', () => {
  it('says how many images, seconds, minutes or characters a row was billed for', async () => {
    const { usedText } = await import('./UsagePanel')
    expect(usedText({ input_tokens: 0, output_tokens: 0, units: { image: 3 } })).toBe('3 images')
    expect(usedText({ input_tokens: 0, output_tokens: 0, units: { image: 1 } })).toBe('1 image')
    expect(usedText({ input_tokens: 0, output_tokens: 0, units: { second: 6, minute: 2.5 } }))
      .toBe('6 s of video · 2.5 min of audio')
    expect(usedText({ input_tokens: 1500, output_tokens: 500, units: { character: 2000 } }))
      .toBe('2k · 2,000 characters')
    expect(usedText({ input_tokens: 1500, output_tokens: 500 })).toBe('2k')
  })
})
