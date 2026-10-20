import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { ModelsPanel } from './ModelsPanel'

// ── Every model row has a Test for the use case it is listed under ──
//
// 🔴 The Test used to be a local-model affordance: a row showed it when its model carried a
// `downloaded` flag, and it called the LOCAL selftest route. A hosted image model carries that flag
// too ("no download needed"), so Image · Generation's Bedrock row called
// `/api/models/local/bedrock/selftest` and answered "Unknown provider 'bedrock'". A hosted embedding
// row carries no flag, so it had no Test at all while the local rows beside it did.
//
// Now every row calls the ONE Test (`api.modelTest(useCase, ref)`), which decides the call from the
// use case; a row whose Test can't work says why (`untestable`), and a card says once what its
// Test does, because it is a real call.

const modelsAvailable = vi.fn()
const modelTest = vi.fn()
const localSelftest = vi.fn()

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      hfTokenStatus: () => Promise.resolve({ sources: [] }),
      modelsAvailable: () => modelsAvailable(),
      activeChains: () => Promise.resolve({}),
      modelsHealth: () => Promise.resolve({ providers: [] }),
      embeddingReindexJobs: () => Promise.resolve({ jobs: [], active: null }),
      judgeBench: () => Promise.resolve({ ran: false }),
      modelDownloadCleanupCandidates: () => Promise.resolve({ candidates: [], total_bytes: 0 }),
      modelDownloads: () => Promise.resolve([]),
      modelsLoaded: () => Promise.resolve({
        loaded: [], providers: [],
        pressure: { total_mb: 0, used_mb: 0, available_mb: 0, used_pct: 0, warn_pct: 85, warn: false, source: 'unavailable' },
      }),
      personalclawConfig: () => Promise.resolve({ agent: { prompt_cache_enabled: true } }),
      modelTest: (u: string, m: string) => modelTest(u, m),
      // The route the old Test guessed its way to. Nothing may call it.
      localModelSelftest: (p: string, m?: string) => localSelftest(p, m),
    },
  }
})
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../app/reportingWrite', () => ({ reportingWrite: (_label: string, fn: () => Promise<unknown>) => fn().then(() => true) }))
vi.mock('../../lib/data', () => ({
  useQuery: (_k: string, fn: () => Promise<unknown>) => {
    const [data, setData] = useState<unknown>(null)
    const [error, setError] = useState<unknown>(null)
    useEffect(() => { fn().then(setData).catch(setError) }, [])
    return { data, error, refresh: () => {} }
  },
  invalidateKeys: () => {},
}))

const VIDEO_REASON = 'A Test would have to make a whole video clip, which is slow and costly, so video models have no Test.'

const canvas = {
  id: 'amazon.nova-canvas-v1:0', name: 'amazon.nova-canvas-v1:0', provider: 'bedrock',
  provider_type: 'image_gen', capabilities: ['image_gen'], downloaded: true,
}
const titan = {
  id: 'amazon.titan-embed-text-v2:0', name: 'amazon.titan-embed-text-v2:0', provider: 'bedrock',
  provider_type: 'bedrock', capabilities: ['embedding'],
}
const reel = {
  id: 'amazon.nova-reel-v1:1', name: 'amazon.nova-reel-v1:1', provider: 'bedrock',
  provider_type: 'video_gen', capabilities: ['video_gen'], untestable: { video_gen: VIDEO_REASON },
}

beforeEach(() => {
  modelsAvailable.mockReset().mockResolvedValue([
    { name: 'bedrock', type: 'bedrock', models: [titan] },
    { name: 'bedrock', type: 'image_gen', models: [canvas] },
    { name: 'bedrock', type: 'video_gen', models: [reel] },
  ])
  modelTest.mockReset()
  localSelftest.mockReset()
})

/** Open one use case's card and hand back its body: the element its header's `aria-controls`
 *  names. */
async function open(card: RegExp): Promise<HTMLElement> {
  render(<ModelsPanel />)
  const toggle = await screen.findByRole('button', { name: card })
  fireEvent.click(toggle)
  const body = document.getElementById(toggle.getAttribute('aria-controls') ?? '')
  expect(body, 'the card names its body').not.toBeNull()
  return body as HTMLElement
}

describe('a hosted model row', () => {
  it('tests a hosted image model through the one Test, for image generation', async () => {
    modelTest.mockResolvedValue({
      use_case: 'image_gen', model: 'bedrock:amazon.nova-canvas-v1:0', ok: true,
      detail: 'Made one 1280×720 image.', reason: '', duration_ms: 4200,
    })
    await open(/image · generation/i)

    fireEvent.click(await screen.findByRole('button', { name: 'Test amazon.nova-canvas-v1:0' }))

    expect(await screen.findByText('Made one 1280×720 image. (4.2 s)')).toBeInTheDocument()
    expect(modelTest).toHaveBeenCalledWith('image_gen', 'bedrock:amazon.nova-canvas-v1:0')
    expect(localSelftest).not.toHaveBeenCalled()
  })

  it('offers a hosted embedding model a Test, which it had none of', async () => {
    modelTest.mockResolvedValue({
      use_case: 'embedding', model: 'bedrock:amazon.titan-embed-text-v2:0', ok: false,
      detail: 'Your account has no access to amazon.titan-embed-text-v2:0 yet.', reason: 'no_vector', duration_ms: 310,
    })
    await open(/^embedding/i)

    fireEvent.click(await screen.findByRole('button', { name: 'Test amazon.titan-embed-text-v2:0' }))

    expect(await screen.findByText(
      'Your account has no access to amazon.titan-embed-text-v2:0 yet. (no_vector, 310 ms)',
    )).toBeInTheDocument()
    expect(modelTest).toHaveBeenCalledWith('embedding', 'bedrock:amazon.titan-embed-text-v2:0')
  })

  it('says what a Test does, once, because it is a real call', async () => {
    await open(/image · generation/i)

    expect(await screen.findByText(
      'Each model’s Test makes one small real call: it makes one image at the smallest size the model lists, or at its default size.',
    )).toBeInTheDocument()
  })

  it('a chat routing sub-use tests its rows as chat models', async () => {
    modelsAvailable.mockResolvedValue([{ name: 'bedrock', type: 'bedrock', models: [{
      id: 'sonnet-5', name: 'sonnet-5', provider: 'bedrock', provider_type: 'bedrock', capabilities: ['chat'],
    }] }])
    modelTest.mockResolvedValue({
      use_case: 'background', model: 'bedrock:sonnet-5', ok: true, detail: 'Replied “OK”.', reason: '', duration_ms: 800,
    })
    await open(/^background/i)

    fireEvent.click(await screen.findByRole('button', { name: 'Test sonnet-5' }))

    expect(await screen.findByText('Replied “OK”. (800 ms)')).toBeInTheDocument()
    expect(modelTest).toHaveBeenCalledWith('background', 'bedrock:sonnet-5')
  })
})

describe('a row whose Test cannot work', () => {
  it('says why instead of offering a Test, once for a card whose rows all share the reason', async () => {
    const card = await open(/video · generation/i)

    expect(await within(card).findByText(`No Test: ${VIDEO_REASON}`)).toBeInTheDocument()
    expect(within(card).getAllByText(/No Test:/)).toHaveLength(1)
    expect(within(card).queryByRole('button', { name: /^Test /i })).toBeNull()
    expect(within(card).queryByText(/Each model’s Test makes/)).toBeNull()
  })

  it('says it on the row when the rows of a card differ', async () => {
    modelsAvailable.mockResolvedValue([{ name: 'lab', type: 'image_gen', models: [
      { ...canvas, provider: 'lab', id: 'cheap-1', name: 'cheap-1' },
      { ...canvas, provider: 'dear', id: 'dear-1', name: 'dear-1', untestable: { image_gen: 'Every image this service makes is billed at full price.' } },
    ] }])
    await open(/image · generation/i)

    expect(await screen.findByText('No Test: Every image this service makes is billed at full price.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Test cheap-1' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Test dear-1' })).toBeNull()
  })

  it('offers no Test for a model not on this machine yet', async () => {
    modelsAvailable.mockResolvedValue([{ name: 'faster-whisper', type: 'faster-whisper', local: true, models: [
      { id: 'small', name: 'small', provider: 'faster-whisper', provider_type: 'faster-whisper', capabilities: ['stt'], downloaded: false },
      { id: 'base', name: 'base', provider: 'faster-whisper', provider_type: 'faster-whisper', capabilities: ['stt'], downloaded: true },
    ] }])
    await open(/speech-to-text/i)

    expect(await screen.findByRole('button', { name: 'Test base' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Test small' })).toBeNull()
  })

  it('shows the gateway’s refusal when a Test is refused on the click', async () => {
    modelTest.mockRejectedValue(new Error('A Test of one of bedrock’s models is already running. Try again when it has finished.'))
    await open(/image · generation/i)

    fireEvent.click(await screen.findByRole('button', { name: 'Test amazon.nova-canvas-v1:0' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('already running')
  })
})
