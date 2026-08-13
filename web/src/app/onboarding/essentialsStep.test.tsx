// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import type { AppCatalogEntry } from '../../lib/api'

// ── OU-2, the essential-apps onboarding step ─────────────────────────────────
//
// This step renders a Store catalog inside a flow the user is being WALKED THROUGH,
// which is exactly the shape where an "install the essentials for me" convenience
// creeps in. Three properties are load-bearing and each is asserted below:
//
//  · NOTHING INSTALLS WITHOUT A CLICK. Mounting the step, expanding a lane, and
//    opening a card's disclosure must produce zero install requests. This is the
//    central rail — falsify it by installing from an effect and this file goes red.
//  · PER-APP CONSENT IS THE STORE'S SURFACE. The disclosure a card shows is the
//    Store's own PermissionList/CronConsentList, so its copy is asserted verbatim:
//    a second, quieter consent path would be a second thing to keep honest.
//  · THE MODEL LANE COMPLETES IN-FLOW over three EXISTING endpoints — install →
//    create provider (the key) → Test → bind — and never a fourth invented one.
//
// It also pins the lane classifier: `providerType: 'model'` alone would put
// faster-whisper (stt-only) in the chat-model lane and dead-end at binding.

const installApp = vi.fn()
const appCatalog = vi.fn()
const modelProviderTypes = vi.fn()
const createModelProvider = vi.fn()
const updateModelProvider = vi.fn()
const testModelProvider = vi.fn()
const chatModels = vi.fn()
const setActiveModel = vi.fn()
const saveOnboardingState = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    installApp: (...a: unknown[]) => installApp(...a),
    appCatalog: () => appCatalog(),
    modelProviderTypes: () => modelProviderTypes(),
    createModelProvider: (...a: unknown[]) => createModelProvider(...a),
    updateModelProvider: (...a: unknown[]) => updateModelProvider(...a),
    testModelProvider: (...a: unknown[]) => testModelProvider(...a),
    chatModels: () => chatModels(),
    setActiveModel: (...a: unknown[]) => setActiveModel(...a),
    saveOnboardingState: (...a: unknown[]) => saveOnboardingState(...a),
  },
}))
vi.mock('../../app/appSdk', () => ({ launchChat: vi.fn(), notify: vi.fn() }))

import { EssentialsStep, laneOf, candidatesByLane } from './EssentialsStep'
import { invalidateCache } from '../../lib/useCachedData'

function entry(over: Partial<AppCatalogEntry> & { name: string }): AppCatalogEntry {
  return {
    displayName: over.name, description: 'desc', version: '1.0.0',
    icon: '', author: 'PersonalClaw', source: `/apps/${over.name}`, sourceKind: 'local',
    isProvider: true, providerType: 'model', tags: [], providerCapabilities: ['chat'],
    permissions: {}, crons: [], ...over,
  }
}

const OPENAI = entry({
  name: 'openai-models', displayName: 'OpenAI', providerType: 'model',
  providerCapabilities: ['chat', 'streaming', 'embedding'],
  permissions: { api: ['/api/models'], network: true },
})
const WHISPER = entry({ name: 'faster-whisper', displayName: 'Faster Whisper', providerType: 'model', providerCapabilities: ['stt'] })
const PIPER = entry({ name: 'piper-tts', displayName: 'Piper TTS', providerType: 'model', providerCapabilities: ['tts'] })
const BRAVE = entry({
  name: 'brave-search', displayName: 'Brave Search', providerType: 'search', providerCapabilities: ['search'],
  permissions: { api: ['/api/search'], cron: true },
  crons: [{ name: 'refresh', every: 3600, agent: 'default', message: 'refresh the index' }],
})
const DISCORD = entry({ name: 'discord-channel', displayName: 'Discord', providerType: 'channel', providerCapabilities: ['messaging'] })
const EMBEDDER = entry({ name: 'sentence-transformers', providerType: 'model', providerCapabilities: ['embedding'] })

const CATALOG = { bundled: [], gitSources: [], localApps: [OPENAI, WHISPER, PIPER, BRAVE, DISCORD, EMBEDDER], remoteApps: [], gitApps: [] }

const FRESH = { needs_model: true, has_model_provider: false, has_chat_binding: false }

function renderStep(over: Partial<Parameters<typeof EssentialsStep>[0]> = {}) {
  const onDone = vi.fn(), onSkip = vi.fn(), onProgress = vi.fn()
  const r = render(<EssentialsStep readiness={FRESH} onDone={onDone} onSkip={onSkip} onProgress={onProgress} {...over} />)
  return { ...r, onDone, onSkip, onProgress }
}

/** Cards appear in lane order (model, search, speech, channel), each lane sorted by
 *  display name: 0 OpenAI · 1 Brave Search · 2 Faster Whisper · 3 Piper TTS · 4 Discord. */
const CARD = { openai: 0, brave: 1, whisper: 2, piper: 3, discord: 4 } as const

async function openCard(which: keyof typeof CARD) {
  const reviews = await screen.findAllByRole('button', { name: /^Review$/ })
  fireEvent.click(reviews[CARD[which]])
}

beforeEach(() => {
  vi.clearAllMocks()
  // A COLD cache per test: `useCachedData` memoizes module-globally, and a warm entry
  // would hide both the loading and the load-FAILURE branch on every test after the first.
  for (const k of ['onboarding:essentials-catalog', 'onboarding:provider-types', 'onboarding:chat-models']) invalidateCache(k)
  try { sessionStorage.clear() } catch { /* jsdom always has it */ }
  appCatalog.mockResolvedValue(CATALOG)
  modelProviderTypes.mockResolvedValue([{
    type: 'openai', label: 'OpenAI', app: 'openai-models', capabilities: ['chat'], multiInstance: true,
    settingsSchema: { properties: { api_key: { type: 'string', default: '', 'x-meta': { label: 'OpenAI API Key', sensitive: true } } }, required: ['api_key'] },
  }])
  createModelProvider.mockResolvedValue({ ok: true, name: 'openai' })
  testModelProvider.mockResolvedValue({ ok: true, message: 'Reachable' })
  chatModels.mockResolvedValue([{ name: 'openai/gpt-5', model_id: 'gpt-5', provider: 'openai' }])
  setActiveModel.mockResolvedValue({ ok: true })
  saveOnboardingState.mockResolvedValue({ ok: true, state: {} })
  installApp.mockResolvedValue({ ok: true, name: 'openai-models', error: '', needs_consent: false, scan: null })
})

// ── the lane classifier ──────────────────────────────────────────────────────

describe('lane classification reads declared capabilities, not providerType alone', () => {
  it('keeps a speech-only model app OUT of the chat-model lane', () => {
    expect(laneOf(WHISPER)).toBe('speech')
    expect(laneOf(PIPER)).toBe('speech')
    expect(laneOf(OPENAI)).toBe('model')
  })

  it('offers no lane to a model app that can neither chat nor speak', () => {
    // sentence-transformers is providerType 'model' but embedding-only: offering it as
    // a chat provider would install an app and then find nothing to bind.
    expect(laneOf(EMBEDDER)).toBeNull()
  })

  it('routes search and channel apps by their provider type', () => {
    expect(laneOf(BRAVE)).toBe('search')
    expect(laneOf(DISCORD)).toBe('channel')
  })

  it('pools every catalog array, so a first-party source surfaces however it arrived', () => {
    // The same app reaching the Store as both a local dir and a git card must appear once.
    const lanes = candidatesByLane({ bundled: [OPENAI], gitSources: [], localApps: [OPENAI], gitApps: [BRAVE], remoteApps: [DISCORD] })
    expect(lanes.model.map((e) => e.name)).toEqual(['openai-models'])
    expect(lanes.search.map((e) => e.name)).toEqual(['brave-search'])
    expect(lanes.channel.map((e) => e.name)).toEqual(['discord-channel'])
  })
})

// ── the central rail: no auto-install ────────────────────────────────────────

describe('nothing installs without an explicit click', () => {
  it('fires no install request on mount', async () => {
    const { onProgress } = renderStep()
    await screen.findByText('OpenAI')
    // Settle every queued effect/microtask, then assert the absence.
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
    expect(installApp, 'mounting the step must not install anything').not.toHaveBeenCalled()
    expect(onProgress, 'nor record an app the user never chose').not.toHaveBeenCalled()
  })

  it('fires no install request when a card\'s disclosure is opened', async () => {
    renderStep()
    await openCard('openai')
    await screen.findByText('Permissions the gateway enforces')
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
    expect(installApp, 'reviewing an app is not consenting to install it').not.toHaveBeenCalled()
  })

  it('installs exactly one app, once, when its own Install button is clicked', async () => {
    renderStep()
    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    await waitFor(() => expect(installApp).toHaveBeenCalledTimes(1))
    expect(installApp).toHaveBeenCalledWith('/apps/openai-models', false)
  })

  it('leaves the resume-point write to the flow shell', async () => {
    // The step reports what it learned through `onProgress`; the shell owns the single
    // `POST /api/onboarding/state` call site. Two writers for one document is how a
    // partial merge starts clobbering itself.
    renderStep()
    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
    expect(saveOnboardingState).not.toHaveBeenCalled()
  })
})

// ── per-app consent is the Store's surface ───────────────────────────────────

describe('per-app install consent is preserved', () => {
  it('discloses the enforced permissions with the Store\'s own wording', async () => {
    renderStep()
    await openCard('openai')
    // The Store's PermissionList, not a paraphrase of it.
    expect(await screen.findByText('Permissions the gateway enforces')).toBeTruthy()
    expect(screen.getByText(/API: \/api\/models/)).toBeTruthy()
    expect(screen.getByText(/Network access: declared/)).toBeTruthy()
    expect(screen.getByText(/advisory only/)).toBeTruthy()
    expect(screen.getByText(/behind the security scanner/)).toBeTruthy()
  })

  it('discloses the recurring jobs an app will run before it is installed', async () => {
    renderStep()
    // Brave declares a cron: the schedule must be visible pre-install.
    await openCard('brave')
    expect(await screen.findByText('Scheduled jobs')).toBeTruthy()
    expect(screen.getByText(/every hour/)).toBeTruthy()
    expect(installApp).not.toHaveBeenCalled()
  })

  it('routes a scanner WARNING through the Store consent modal and re-attempts only on confirm', async () => {
    installApp.mockResolvedValueOnce({
      ok: false, name: 'openai-models', error: '', needs_consent: true,
      scan: { verdict: 'warning', findings: [{ surface: 'py', severity: 'medium', rule: 'subprocess', path: 'p.py', evidence: 'run()' }] },
    })
    const { onProgress } = renderStep()
    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    const anyway = await screen.findByRole('button', { name: /Install anyway/ })
    expect(installApp).toHaveBeenCalledTimes(1)
    expect(onProgress, 'a blocked install records no progress').not.toHaveBeenCalled()
    fireEvent.click(anyway)
    await waitFor(() => expect(installApp).toHaveBeenCalledTimes(2))
    expect(installApp).toHaveBeenLastCalledWith('/apps/openai-models', true)
  })
})

// ── the model rail: install → key → Test → bind, in-flow ─────────────────────

describe('the model lane completes entirely in-flow', () => {
  async function walkModelLane() {
    const h = renderStep()
    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    const key = await screen.findByLabelText('OpenAI API Key')
    fireEvent.change(key, { target: { value: 'sk-secret' } })
    fireEvent.click(screen.getByRole('button', { name: /Save and test/ }))
    return h
  }

  it('creates the provider with the schema-declared key, then Tests it', async () => {
    await walkModelLane()
    await waitFor(() => expect(testModelProvider).toHaveBeenCalledWith('openai'))
    expect(createModelProvider).toHaveBeenCalledWith({ name: 'openai', type: 'openai', model: '', options: { api_key: 'sk-secret' } })
  })

  it('binds the chosen chat model as a canonical provider:model ref', async () => {
    await walkModelLane()
    fireEvent.click(await screen.findByRole('button', { name: /gpt-5/ }))
    await waitFor(() => expect(setActiveModel).toHaveBeenCalledWith('chat', ['openai:gpt-5']))
  })

  it('shows a failed Test inline and lets the user retry in place', async () => {
    testModelProvider.mockResolvedValue({ ok: false, message: 'invalid_api_key' })
    await walkModelLane()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('invalid_api_key')
    // Still on the key field — the retry happens here, not in Settings.
    expect(screen.getByLabelText('OpenAI API Key')).toBeTruthy()
    expect(chatModels, 'a failed Test must not advance to binding').not.toHaveBeenCalled()
  })

  it('never puts a submitted key in an error message', async () => {
    testModelProvider.mockResolvedValue({ ok: false, message: 'invalid_api_key' })
    const { container } = await walkModelLane()
    await screen.findByRole('alert')
    // The key lives in the masked input's value only; no rendered text repeats it.
    expect(container.textContent).not.toContain('sk-secret')
  })

  it('skips straight to binding when a provider already exists but nothing is bound', async () => {
    renderStep({ readiness: { needs_model: true, has_model_provider: true, has_chat_binding: false } })
    await screen.findByRole('button', { name: /gpt-5/ })
    expect(installApp, 'an existing provider needs no app install').not.toHaveBeenCalled()
    expect(createModelProvider).not.toHaveBeenCalled()
  })

  it('asks for nothing when chat already resolves', async () => {
    renderStep({ readiness: { needs_model: false, has_model_provider: true, has_chat_binding: true } })
    expect(await screen.findByText(/A chat model is configured/)).toBeTruthy()
    expect(chatModels).not.toHaveBeenCalled()
  })

  it('applies a corrected key to the existing instance instead of dead-ending on 409', async () => {
    createModelProvider.mockRejectedValue(new Error(JSON.stringify({ error: "Provider 'openai' already exists" })))
    await walkModelLane()
    await waitFor(() => expect(updateModelProvider).toHaveBeenCalledWith('openai', { options: { api_key: 'sk-secret' } }))
    expect(testModelProvider).toHaveBeenCalledWith('openai')
  })
})

// ── progress writes ──────────────────────────────────────────────────────────

describe('each lane records only its own progress field', () => {
  it('records the model app by name the moment it installs', async () => {
    const { onProgress } = renderStep()
    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    await waitFor(() => expect(onProgress).toHaveBeenCalledWith({ essentials: { model: 'openai-models' } }))
  })

  it('records a search install as a flag, naming no other lane', async () => {
    installApp.mockResolvedValue({ ok: true, name: 'brave-search', error: '', needs_consent: false, scan: null })
    const { onProgress } = renderStep()
    await openCard('brave')
    fireEvent.click(await screen.findByRole('button', { name: /Install Brave Search/ }))
    await waitFor(() => expect(onProgress).toHaveBeenCalledWith({ essentials: { search: true } }))
    // A partial patch at BOTH levels: this lane must not echo back model/speech/channel.
    for (const [patch] of onProgress.mock.calls) expect(Object.keys(patch.essentials)).toEqual(['search'])
  })

  it('records a speech install as a flag', async () => {
    installApp.mockResolvedValue({ ok: true, name: 'faster-whisper', error: '', needs_consent: false, scan: null })
    const { onProgress } = renderStep()
    await openCard('whisper')
    fireEvent.click(await screen.findByRole('button', { name: /Install Faster Whisper/ }))
    await waitFor(() => expect(onProgress).toHaveBeenCalledWith({ essentials: { speech: true } }))
  })

  it('records a channel install by app name', async () => {
    installApp.mockResolvedValue({ ok: true, name: 'discord-channel', error: '', needs_consent: false, scan: null })
    const { onProgress } = renderStep()
    await openCard('discord')
    fireEvent.click(await screen.findByRole('button', { name: /Install Discord/ }))
    await waitFor(() => expect(onProgress).toHaveBeenCalledWith({ essentials: { channel: 'discord-channel' } }))
  })
})

// ── skipping everything but the model ────────────────────────────────────────

describe('skipping every optional lane still reaches the next step', () => {
  it('Continue is unavailable until the model lane resolves, then advances', async () => {
    const { onDone, onProgress } = renderStep()
    const cont = await screen.findByRole('button', { name: /Continue/ })
    fireEvent.click(cont)
    expect(onDone, 'the required rail is not yet satisfied').not.toHaveBeenCalled()

    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    fireEvent.change(await screen.findByLabelText('OpenAI API Key'), { target: { value: 'sk-secret' } })
    fireEvent.click(screen.getByRole('button', { name: /Save and test/ }))
    fireEvent.click(await screen.findByRole('button', { name: /gpt-5/ }))

    await waitFor(() => expect(screen.getByRole('button', { name: /Continue/ })).not.toHaveAttribute('aria-disabled'))
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }))
    expect(onDone).toHaveBeenCalledWith('gpt-5')
    // Search, speech and channel were never touched — no install, no progress field.
    expect(installApp).toHaveBeenCalledTimes(1)
    const named = onProgress.mock.calls.flatMap(([p]) => Object.keys(p.essentials ?? {}))
    expect(named).toEqual(['model'])
  })

  it('offers a "Set up later" escape so the step never traps a user', async () => {
    const { onSkip } = renderStep()
    fireEvent.click(await screen.findByRole('button', { name: /Set up later/ }))
    expect(onSkip).toHaveBeenCalled()
  })
})

// ── a dead catalog is not an empty one ───────────────────────────────────────

describe('a failed catalog fetch says so', () => {
  it('announces the load failure and offers a retry instead of "no apps"', async () => {
    appCatalog.mockRejectedValue(new Error('gateway unreachable'))
    renderStep()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/app catalog/i)
    // The trap this avoids: `.catch(() => [])` would render four empty lanes, which
    // reads as "there is nothing to install" — a lie about a reachable catalog.
    expect(screen.queryByText(/No model provider app is available/)).toBeNull()
    expect(screen.getByRole('button', { name: /Retry|Try again/i })).toBeTruthy()
  })

  it('names the first-party source mechanism when a lane is genuinely empty', async () => {
    appCatalog.mockResolvedValue({ bundled: [], gitSources: [], localApps: [OPENAI], remoteApps: [], gitApps: [] })
    renderStep()
    expect(await screen.findByText(/No web search app is available/)).toBeTruthy()
    expect(screen.getAllByText(/first-party source/)[0]).toBeTruthy()
  })
})
