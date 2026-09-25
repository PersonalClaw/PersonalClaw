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
const detectLocalModel = vi.fn()
const scanLocalModels = vi.fn()
const bindLocalModel = vi.fn()
const onboardingModelCheck = vi.fn()

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
    detectLocalModel: () => detectLocalModel(),
    scanLocalModels: () => scanLocalModels(),
    bindLocalModel: (...a: unknown[]) => bindLocalModel(...a),
    onboardingModelCheck: () => onboardingModelCheck(),
  },
}))
vi.mock('../../app/appSdk', () => ({ launchChat: vi.fn(), notify: vi.fn() }))

import { EssentialsStep, laneOf, candidatesByLane, typesMissingFromCatalog } from './EssentialsStep'
import { invalidateKeys } from '../../lib/data'

function entry(over: Partial<AppCatalogEntry> & { name: string }): AppCatalogEntry {
  return {
    displayName: over.name, description: 'desc', version: '1.0.0',
    icon: '', author: 'PersonalClaw', source: `/apps/${over.name}`, sourceKind: 'local',
    isProvider: true, providerType: 'model', tags: [], providerCapabilities: ['chat'],
    // `consentKnown` is what the real wire always carries for a dir-scanned manifest
    // (`CatalogEntry.to_dict` is `asdict`, and the three manifest-backed builders set it
    // True) — omitting it here made the fixture a shape the backend never sends, and the
    // card's disclosure gate keys on exactly this flag (issue 614, extended to this card
    // by #492). A fixture that cannot reach the branch under test passes for the wrong
    // reason.
    consentKnown: true,
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

// #3529 — `ollama-models` ships `native: true` (pre-installed), so it is NEVER in the
// catalog above (`resolve_catalog_entries`'s "Library exclusion") while its type IS
// registered (`GET /api/model-provider-types` walks the loaded provider registry, not the
// catalog). Mirrors the real manifest's settingsSchema (`app.json`), trimmed to the fields
// these tests exercise.
const OLLAMA_TYPE = {
  type: 'ollama', label: 'Ollama', app: 'ollama-models', capabilities: ['chat', 'embedding'], multiInstance: true,
  settingsSchema: {
    properties: {
      endpoint: { type: 'string', default: 'http://localhost:11434', 'x-meta': { label: 'Ollama Endpoint', help: 'Base URL of the Ollama API server.' } },
      default_model: { type: 'string', default: '', 'x-meta': { label: 'Default Model' } },
    },
    required: ['endpoint'],
  },
}

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
  // A COLD cache per test: `useQuery` memoizes module-globally, and a warm entry
  // would hide both the loading and the load-FAILURE branch on every test after the first.
  for (const k of ['onboarding:essentials-catalog', 'onboarding:provider-types', 'onboarding:chat-models', 'onboarding:local-model']) invalidateKeys(k)
  try { sessionStorage.clear() } catch { /* jsdom always has it */ }
  appCatalog.mockResolvedValue(CATALOG)
  // OU-13 default: no local Ollama anywhere. Every existing test therefore renders the
  // model lane exactly as before the on-ramp — no bind card, no scan fired.
  detectLocalModel.mockResolvedValue({ detected: false })
  scanLocalModels.mockResolvedValue({ endpoints: [] })
  bindLocalModel.mockResolvedValue({ ok: true, status: 'bound', model: 'llama3.2:3b', provider: 'Local Ollama' })
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
  // The lane's PROOF: by default the build check passes, so every test above walks the flow
  // exactly as it did before verification existed. The tests that falsify it override this.
  onboardingModelCheck.mockResolvedValue({ ok: true, source: 'binding', bound: ['openai:gpt-5'] })
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

// ── OU-13: the local + LAN Ollama zero-key on-ramp ───────────────────────────
//
// Four falsifiable properties, each with a known-true AND a known-false case:
//  1. NO OLLAMA REACHABLE ⇒ the lane is unchanged: no "Use this model" bind card, the
//     catalog cards render as before, Continue stays disabled. Proven for BOTH the
//     localhost branch (detection empty) and the LAN branch (scan empty).
//  2. NO SCAN ON FIRST BOOT: mounting the step probes localhost (loopback) but fires
//     ZERO network scan until the explicit "Scan my local network" click.
//  3. A DISCOVERED ENDPOINT (localhost or LAN) offers a one-click, NO-API-KEY bind that
//     routes through `bindLocalModel` — never the keyed `createModelProvider` path.
//  4. A card is shown only for an endpoint the backend returned (a live-probed one).

describe('OU-13 — local + LAN Ollama zero-key on-ramp', () => {
  const LOCALHOST = { endpoint: 'http://localhost:11434', model: 'llama3.2:3b' }
  const LAN = { endpoint: 'http://192.168.1.50:11434', model: 'qwen2.5:0.5b' }

  it('known-false localhost: no Ollama ⇒ no bind card, catalog unchanged, Continue disabled', async () => {
    detectLocalModel.mockResolvedValue({ detected: false })
    renderStep()
    // The catalog renders exactly as today.
    const reviews = await screen.findAllByRole('button', { name: /^Review$/ })
    expect(reviews.length).toBeGreaterThan(0)
    await waitFor(() => expect(detectLocalModel).toHaveBeenCalled())
    // No auto-bind card was injected on the no-Ollama path.
    expect(screen.queryByRole('button', { name: /Use this model/ })).toBeNull()
    // Continue is still gated on a real model resolution.
    const cont = screen.getByRole('button', { name: /Continue/ })
    expect(cont.hasAttribute('disabled') || cont.getAttribute('aria-disabled') === 'true').toBe(true)
  })

  it('known-false: NO network scan fires on first boot (localhost probe only)', async () => {
    detectLocalModel.mockResolvedValue({ detected: false })
    renderStep()
    await screen.findAllByRole('button', { name: /^Review$/ })
    await waitFor(() => expect(detectLocalModel).toHaveBeenCalled())
    // The loopback probe ran; the outbound LAN scan did NOT — it needs the explicit click.
    expect(scanLocalModels).not.toHaveBeenCalled()
  })

  it('known-true localhost: one-click bind with NO API-key field reaches the resolved state', async () => {
    detectLocalModel.mockResolvedValue({ detected: true, ...LOCALHOST })
    const { onProgress } = renderStep()
    const use = await screen.findByRole('button', { name: /Use this model/ })
    expect(screen.getAllByText(/no API key/i).length).toBeGreaterThan(0)
    await act(async () => { fireEvent.click(use) })
    // The bind rode the credential-free seed path — never the keyed provider-create path.
    await waitFor(() => expect(bindLocalModel).toHaveBeenCalledWith('http://localhost:11434'))
    expect(createModelProvider).not.toHaveBeenCalled()
    // No key entry was ever shown on this path.
    expect(screen.queryByRole('button', { name: /Save and test/ })).toBeNull()
    // The model lane resolved: it records the bound app and enables Continue.
    await waitFor(() => expect(onProgress).toHaveBeenCalledWith({ essentials: { model: 'ollama-models' } }))
    const cont = screen.getByRole('button', { name: /Continue/ })
    await waitFor(() => expect(cont.hasAttribute('disabled') || cont.getAttribute('aria-disabled') === 'true').toBe(false))
  })

  it('known-false LAN: an empty scan surfaces no card and leaves the catalog unchanged', async () => {
    detectLocalModel.mockResolvedValue({ detected: false })
    scanLocalModels.mockResolvedValue({ endpoints: [] })
    renderStep()
    const scan = await screen.findByRole('button', { name: /Scan my local network/ })
    await act(async () => { fireEvent.click(scan) })
    await waitFor(() => expect(scanLocalModels).toHaveBeenCalledTimes(1))
    expect(await screen.findByText(/No local model found on your network/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Use this model/ })).toBeNull()
    // The catalog Review buttons are still there — the empty scan altered nothing.
    expect((await screen.findAllByRole('button', { name: /^Review$/ })).length).toBeGreaterThan(0)
  })

  it('known-true LAN: a discovered endpoint offers a one-click no-key bind of THAT endpoint', async () => {
    detectLocalModel.mockResolvedValue({ detected: false })
    scanLocalModels.mockResolvedValue({ endpoints: [LAN] })
    const { onProgress } = renderStep()
    const scan = await screen.findByRole('button', { name: /Scan my local network/ })
    await act(async () => { fireEvent.click(scan) })
    const use = await screen.findByRole('button', { name: /Use this model/ })
    expect(screen.getByText(/192\.168\.1\.50/)).toBeTruthy()
    await act(async () => { fireEvent.click(use) })
    await waitFor(() => expect(bindLocalModel).toHaveBeenCalledWith('http://192.168.1.50:11434'))
    expect(createModelProvider).not.toHaveBeenCalled()
    await waitFor(() => expect(onProgress).toHaveBeenCalledWith({ essentials: { model: 'ollama-models' } }))
  })

  it('a failed bind is shown in place, not swallowed', async () => {
    detectLocalModel.mockResolvedValue({ detected: true, ...LOCALHOST })
    bindLocalModel.mockRejectedValue(new Error(JSON.stringify({ error: { code: 'local_model_bind_failed', message: 'the Ollama app is not installed' } })))
    renderStep()
    const use = await screen.findByRole('button', { name: /Use this model/ })
    await act(async () => { fireEvent.click(use) })
    expect(await screen.findByRole('alert')).toBeTruthy()
    // A failed bind does NOT mark the lane resolved.
    const cont = screen.getByRole('button', { name: /Continue/ })
    expect(cont.hasAttribute('disabled') || cont.getAttribute('aria-disabled') === 'true').toBe(true)
  })
})

// ── #3529: an installed-but-uncatalogued provider type is not a dead end ─────
//
// `ollama-models` ships pre-installed, so its type is registered while its app is
// EXCLUDED from the catalog (see `OLLAMA_TYPE` above). Before this fix the ONLY route
// into `ConfigureProvider`'s schema-driven form was a catalog card's Install click, so a
// registered-but-uncatalogued type had NO route there — only the on-ramp's discovery
// (loopback + opt-in LAN scan), which misses anything outside those two checks (a
// different subnet, a hostname, a container/VM bridge). `InstalledProviderTypes` closes
// that gap from the type registry itself, not a hand-picked field, so it also covers
// whatever else ships pre-installed next.

describe('#3529 — a provider type whose app is already installed is not a dead end', () => {
  it('known-false discovery (nothing on loopback, an empty LAN scan) still gets a working route in', async () => {
    // The exact scenario the issue reports: nothing discoverable AND no catalog card
    // (native apps never get one). `detectLocalModel`/`scanLocalModels` already default to
    // "nothing found" in beforeEach.
    modelProviderTypes.mockResolvedValue([OLLAMA_TYPE])
    renderStep()

    // The on-ramp itself dead-ends exactly as the issue describes…
    fireEvent.click(await screen.findByRole('button', { name: /Scan my local network/ }))
    expect(await screen.findByText(/No local model found on your network/)).toBeTruthy()
    // …Ollama never had a catalog card (it's already installed, so the Store's own
    // "Library exclusion" keeps it out of "available to install")…
    expect(screen.queryByRole('button', { name: /Install Ollama/ })).toBeNull()

    // …and the fallback this fix adds is what turns that dead end into a route.
    const configure = await screen.findByRole('button', { name: /Configure Ollama/ })
    fireEvent.click(configure)
    expect(await screen.findByLabelText('Ollama Endpoint')).toBeTruthy()
  })

  it('drives the SAME schema-driven ConfigureProvider the catalog path uses, end to end', async () => {
    modelProviderTypes.mockResolvedValue([OLLAMA_TYPE])
    chatModels.mockResolvedValue([{ name: 'ollama/llama3.2:3b', model_id: 'llama3.2:3b', provider: 'ollama' }])
    const { onDone, onProgress } = renderStep()

    fireEvent.click(await screen.findByRole('button', { name: /Configure Ollama/ }))
    fireEvent.change(await screen.findByLabelText('Ollama Endpoint'), { target: { value: 'http://192.168.5.2:11434' } })
    fireEvent.click(screen.getByRole('button', { name: /Save and test/ }))

    // Created under its TYPE ('ollama'), with only the field actually filled — never a
    // hand-picked "endpoint-only" shape, whatever the schema declares today.
    await waitFor(() => expect(createModelProvider).toHaveBeenCalledWith(
      { name: 'ollama', type: 'ollama', model: '', options: { endpoint: 'http://192.168.5.2:11434' } }))
    expect(onProgress).toHaveBeenCalledWith({ essentials: { model: 'ollama-models' } })
    await waitFor(() => expect(testModelProvider).toHaveBeenCalledWith('ollama'))

    fireEvent.click(await screen.findByRole('button', { name: /llama3\.2:3b/ }))
    await waitFor(() => expect(setActiveModel).toHaveBeenCalledWith('chat', ['ollama:llama3.2:3b']))

    await waitFor(() => expect(screen.getByRole('button', { name: /Continue/ })).not.toHaveAttribute('aria-disabled'))
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }))
    expect(onDone).toHaveBeenCalledWith('llama3.2:3b')
  })

  it('does not duplicate a catalog card: a type whose app IS catalogued gets no extra button', async () => {
    // The default fixture's own 'openai' type (app 'openai-models') IS in the catalog —
    // the filter this fix relies on must not offer a second, redundant "configure it
    // manually" card beside the catalog's own "Install OpenAI" card for the same app.
    renderStep()
    await screen.findByText('OpenAI')
    expect(screen.queryByRole('button', { name: /Configure OpenAI/ })).toBeNull()
  })

  it('says so, with a retry, when the registry itself cannot be read — never a silent gap', async () => {
    modelProviderTypes.mockRejectedValue(new Error(JSON.stringify({ error: 'gateway unreachable' })))
    renderStep()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/installed model providers/i)
    expect(screen.getByRole('button', { name: /Retry|Try again/i })).toBeTruthy()
  })
})

// ── #3529 (owner follow-up): discovery's own silence, not just its dead end ──
//
// Per the owner, verified on a real fresh install: "there's an ollama on my host local
// network which should be accessible by finch vm. At least allow me to configure it
// manually." A CONTAINERISED gateway's loopback and LAN are never the host's, so for every
// containerised install, discovery finding nothing is the default outcome — which makes
// "did the check even run, and what do I do next" the common case, not a rare one. Before
// this, the automatic loopback probe said NOTHING when it missed (silent until "Scan my
// local network" was clicked), and an empty LAN scan named no next step. Both now say so
// and point at the manual entry below — but ONLY when `EssentialsStep` says that route
// truthfully exists, never a second guess made here.

describe('#3529 — discovery says so, and points at the manual entry, whenever it truly exists', () => {
  it('the automatic loopback probe: silent no longer — states the fact and the way forward', async () => {
    modelProviderTypes.mockResolvedValue([OLLAMA_TYPE])
    renderStep()
    // Nobody clicked "Scan" — this is the check that already ran on mount.
    expect(await screen.findByText('Nothing found on this machine yet. Enter its address directly below.')).toBeTruthy()
  })

  it('an empty LAN scan: the existing message gains the pointer, verbatim otherwise', async () => {
    modelProviderTypes.mockResolvedValue([OLLAMA_TYPE])
    renderStep()
    fireEvent.click(await screen.findByRole('button', { name: /Scan my local network/ }))
    expect(await screen.findByText('No local model found on your network. Enter its address directly below.')).toBeTruthy()
  })

  it('a scan that could not even run still points at the manual entry', async () => {
    modelProviderTypes.mockResolvedValue([OLLAMA_TYPE])
    scanLocalModels.mockRejectedValue(new Error(JSON.stringify({ error: 'network unreachable' })))
    renderStep()
    fireEvent.click(await screen.findByRole('button', { name: /Scan my local network/ }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/network unreachable/)
    expect(alert.textContent).toMatch(/Enter its address directly below/)
  })

  it('never dangles a pointer at a route that does not actually exist', async () => {
    // The default fixture: 'openai' is the only registered type and its app IS in the
    // catalog, so NO manual fallback renders below. Discovery's own copy must know that.
    renderStep()
    expect(await screen.findByText('Nothing found on this machine yet.')).toBeTruthy()
    expect(screen.queryByText(/Enter its address directly below/)).toBeNull()
  })
})

describe('typesMissingFromCatalog (#3529)', () => {
  it('keeps a type whose app has no catalog card', () => {
    expect(typesMissingFromCatalog([OLLAMA_TYPE], [OPENAI])).toEqual([OLLAMA_TYPE])
  })

  it('drops a type whose app DOES have a catalog card', () => {
    const openaiType = { type: 'openai', label: 'OpenAI', app: 'openai-models', capabilities: ['chat'], multiInstance: true, settingsSchema: {} }
    expect(typesMissingFromCatalog([openaiType, OLLAMA_TYPE], [OPENAI])).toEqual([OLLAMA_TYPE])
  })

  it('is empty when types have not loaded yet', () => {
    expect(typesMissingFromCatalog(undefined, [OPENAI])).toEqual([])
  })
})

// ── the lane is READY only when a build proved it ─────────────────────────────
//
// The defect these rails close, measured on `origin/main`: a home whose `config.json` carries
// `{"name": "my-openai", "type": "openai"}` with no app registering that type, plus a `chat`
// binding to it, answers `can_resolve_use_case("chat") == True` — so `needs_model: false`, so
// this step opened on a green "A chat model is configured — you're ready" — while chat's real
// `resolve_provider_for_use_case("chat")` raises `ERR_MODEL_UNRESOLVED`. `needs_model` is
// derived from a documented NO-INSTANTIATE probe whose first branch returns true as soon as
// `active_models.json` holds a ref, and writing that ref is the LAST thing this step does. So
// the step was reading its own write back as proof.
//
// Three properties, each with a known-true and a known-false case:
//  1. NOTHING READS READY UNVERIFIED — neither the `needs_model:false` entry path nor a
//     successful bind may reach `done` before the build check answers `ok`.
//  2. EVERY CAUSE ARRIVES INTACT — the failing render is the backend's own `why`/`fix`,
//     verbatim, so the number of causes a user can distinguish equals the number the bridge
//     can. A house sentence here would re-create the defect at the presentation layer.
//  3. "WE DON'T KNOW" IS NOT "IT'S BROKEN" — a check that could not run says so instead of
//     inventing a cause.

describe('the model lane reads ready only after a build check', () => {
  const READY = { needs_model: false, has_model_provider: true, has_chat_binding: true }

  function refusal(over: { why: string; fix: string }) {
    return { ok: false, code: 'ERR_MODEL_UNRESOLVED', what: 'the model pinned for use case \'chat\' cannot be built', ...over }
  }

  it('verifies a home the coarse probe already calls ready, instead of trusting it', async () => {
    renderStep({ readiness: READY })
    await waitFor(() => expect(onboardingModelCheck).toHaveBeenCalled())
    expect(await screen.findByText(/A chat model is configured/)).toBeTruthy()
  })

  it('known-false: a claimed-ready home whose provider cannot build is NOT reported ready', async () => {
    onboardingModelCheck.mockResolvedValue(refusal({
      why: "provider 'my-openai' declares type 'openai', and no installed app registers that type",
      fix: "install an app that provides 'openai' in the App Store, or change 'my-openai''s type in Settings → Providers",
    }))
    const { onDone } = renderStep({ readiness: READY })
    // The green claim is gone…
    expect(await screen.findByText(/chat can.t use it yet/i)).toBeTruthy()
    expect(screen.queryByText(/A chat model is configured/)).toBeNull()
    // …Continue is refused, and its reason is the one about verification, not about setup…
    const cont = screen.getByRole('button', { name: /Continue/ })
    fireEvent.click(cont)
    expect(onDone).not.toHaveBeenCalled()
    // …and the step still offers the door out, so a broken home is never trapped here.
    expect(screen.getByRole('button', { name: /Set up later/ })).toBeTruthy()
  })

  it('known-false: a successful bind does not read ready until the check passes', async () => {
    let release: (v: unknown) => void = () => {}
    onboardingModelCheck.mockReturnValue(new Promise((r) => { release = r }))
    const { onDone } = renderStep()
    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    fireEvent.change(await screen.findByLabelText('OpenAI API Key'), { target: { value: 'sk-secret' } })
    fireEvent.click(screen.getByRole('button', { name: /Save and test/ }))
    fireEvent.click(await screen.findByRole('button', { name: /gpt-5/ }))

    // The binding WAS written — and that is precisely not enough.
    await waitFor(() => expect(setActiveModel).toHaveBeenCalledWith('chat', ['openai:gpt-5']))
    await waitFor(() => expect(onboardingModelCheck).toHaveBeenCalled())
    const cont = screen.getByRole('button', { name: /Continue/ })
    expect(cont.getAttribute('aria-disabled')).toBe('true')
    expect(cont.getAttribute('title') || '').toMatch(/still checking/i)
    fireEvent.click(cont)
    expect(onDone).not.toHaveBeenCalled()

    await act(async () => { release({ ok: true, source: 'binding', bound: ['openai:gpt-5'] }) })
    await waitFor(() => expect(screen.getByRole('button', { name: /Continue/ }).getAttribute('aria-disabled')).not.toBe('true'))
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }))
    expect(onDone).toHaveBeenCalledWith('gpt-5')
  })

  it('known-false: a bind the check then refuses leaves the lane unresolved', async () => {
    onboardingModelCheck.mockResolvedValue(refusal({
      why: "provider 'openai' needs credential 'openai', which has no secret in the credential store",
      fix: "set 'openai' in Settings → Providers, or rebind 'chat' to an available model in Settings → Models",
    }))
    const { onDone } = renderStep()
    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    fireEvent.change(await screen.findByLabelText('OpenAI API Key'), { target: { value: 'sk-secret' } })
    fireEvent.click(screen.getByRole('button', { name: /Save and test/ }))
    fireEvent.click(await screen.findByRole('button', { name: /gpt-5/ }))
    expect(await screen.findByText(/has no secret in the credential store/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Continue/ }).getAttribute('aria-disabled')).toBe('true')
    expect(onDone).not.toHaveBeenCalled()
    // A local bind is the one path back that does not need a key, so the form is offered too.
    expect(screen.getByRole('button', { name: /Change its settings/ })).toBeTruthy()
  })

  // ── every cause the backend distinguishes reaches the screen as itself ──────
  //
  // These nine `why`/`fix` pairs are the causes `provider_bridge` derives. The assertion is
  // deliberately VERBATIM rather than a keyword match: a component that summarised them would
  // pass a loose assertion while showing one sentence for nine states.
  const CAUSES: { name: string; why: string; fix: string }[] = [
    { name: 'the use case maps to no capability',
      why: "use case 'chat' maps to no provider capability, so no configured provider can satisfy it",
      fix: "rebind 'chat' to an available model in Settings → Models — and report use case 'chat' as unmappable" },
    { name: 'config.json is unreadable',
      why: "config.json could not be read, so whether 'my-openai' is still configured is unknown",
      fix: "repair config.json (see the gateway log), or rebind 'chat' to an available model in Settings → Models" },
    { name: 'no entry by that name',
      why: "no provider named 'my-openai' is in config.json — the entry was renamed or removed, or its app was uninstalled",
      fix: "re-add 'my-openai' in Settings → Providers, or rebind 'chat' to an available model in Settings → Models" },
    { name: 'in config.json but not registered',
      why: "provider 'my-openai' IS in config.json but is not registered in the running gateway, so nothing can build it",
      fix: "re-save 'my-openai' in Settings → Providers to register it now, or restart the gateway to replay config.json" },
    { name: 'no installed app registers the type',
      why: "provider 'my-openai' declares type 'openai', and no installed app registers that type",
      fix: "install an app that provides 'openai' in the App Store, or change 'my-openai''s type in Settings → Providers" },
    { name: 'the type\'s app is installed but DISABLED',
      why: "provider 'my-openai' declares type 'openai', whose app 'openai-models' is installed but DISABLED, so the type is not registered",
      fix: "enable 'openai-models' on the Apps page" },
    { name: 'the type\'s app failed to load',
      why: "provider 'my-openai' declares type 'openai' and its app 'openai-models' is installed and enabled, but the type never registered — the app failed to load",
      fix: "check the gateway log for 'openai-models''s import error, or rebind 'chat' to an available model in Settings → Models" },
    { name: 'capability mismatch',
      why: "provider 'my-openai' (type 'openai') does not declare the 'chat' capability that use case 'chat' needs",
      fix: "rebind 'chat' to an available model in Settings → Models, or bind 'chat' to a provider that declares 'chat'" },
    { name: 'the credential has no secret',
      why: "provider 'my-openai' needs credential 'openai', which has no secret in the credential store",
      fix: "set 'openai' in Settings → Providers, or rebind 'chat' to an available model in Settings → Models" },
  ]

  it.each(CAUSES)('shows the backend\'s own words for: $name', async ({ why, fix }) => {
    onboardingModelCheck.mockResolvedValue(refusal({ why, fix }))
    renderStep({ readiness: READY })
    // Both halves, verbatim. WHY is what is wrong; FIX is the act that resolves it, and a
    // surface that showed only one of them would leave a diagnosis with no next step.
    expect(await screen.findByText(why)).toBeTruthy()
    expect(screen.getByText(fix)).toBeTruthy()
  })

  it('renders a DISTINCT sentence for every one of those causes', async () => {
    // The count assertion the verbatim checks above imply but do not state: nine causes must
    // produce nine different screens. A component that paraphrased would collapse them.
    const rendered = new Set<string>()
    for (const c of CAUSES) {
      onboardingModelCheck.mockResolvedValue(refusal(c))
      const { container, unmount } = render(
        <EssentialsStep readiness={READY} onDone={vi.fn()} onSkip={vi.fn()} onProgress={vi.fn()} />)
      await screen.findByText(c.why)
      rendered.add(container.textContent || '')
      unmount()
      for (const k of ['onboarding:essentials-catalog', 'onboarding:provider-types', 'onboarding:chat-models', 'onboarding:local-model']) invalidateKeys(k)
    }
    expect(rendered.size).toBe(CAUSES.length)
  })

  it('a check that could not RUN says so, instead of inventing a cause', async () => {
    onboardingModelCheck.mockRejectedValue(new Error(JSON.stringify({ error: 'gateway unreachable' })))
    renderStep({ readiness: READY })
    expect(await screen.findByText(/Couldn.t check whether a chat model resolves: gateway unreachable/)).toBeTruthy()
    // Not a verdict: it must not claim the model is broken, and must not claim it is ready.
    expect(screen.queryByText(/chat can.t use it yet/i)).toBeNull()
    expect(screen.queryByText(/A chat model is configured/)).toBeNull()
    expect(screen.getByRole('button', { name: /Check again/ })).toBeTruthy()
  })

  it('re-runs the check on demand, and a second verdict supersedes the first', async () => {
    onboardingModelCheck.mockResolvedValueOnce(refusal({
      why: "provider 'my-openai' IS in config.json but is not registered in the running gateway, so nothing can build it",
      fix: 'restart the gateway to replay config.json',
    }))
    onboardingModelCheck.mockResolvedValue({ ok: true, source: 'binding', bound: ['my-openai:gpt-4o'] })
    renderStep({ readiness: READY })
    fireEvent.click(await screen.findByRole('button', { name: /Check again/ }))
    expect(await screen.findByText(/A chat model is configured/)).toBeTruthy()
    expect(onboardingModelCheck).toHaveBeenCalledTimes(2)
  })

  it('names the mechanism, not a choice, when nothing is explicitly bound', async () => {
    // `source: 'fallback'` means resolution came from the implicit "first capable configured
    // provider" rule. "Ready to chat" there would imply the user picked something.
    onboardingModelCheck.mockResolvedValue({ ok: true, source: 'fallback', bound: [] })
    const { onDone } = renderStep({ readiness: READY })
    await waitFor(() => expect(screen.getByRole('button', { name: /Continue/ }).getAttribute('aria-disabled')).not.toBe('true'))
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }))
    expect(onDone).toHaveBeenCalledWith('Ready — using a configured provider')
  })
})

// ── an empty model list is not a fact about the provider ──────────────────────
//
// `GET /api/models/chat` gathers each provider's catalog with `return_exceptions=True` and
// drops a raising provider silently, and the entry this flow creates carries no pinned `model`
// to fall back to — so a wrong key, an unreachable endpoint and a provider that genuinely has
// no chat model all arrive as the same empty array. The step used to report that array as
// "No chat-capable models were discovered for this provider", which is a claim it cannot make.

describe('an empty discovery result is disambiguated, not asserted', () => {
  /** Walk to the bind step with discovery empty, and make the SECOND `testModelProvider`
   *  call — the one the empty list fires to disambiguate — answer `probe`. The first call is
   *  the form's own pre-bind test, which must pass or the flow never reaches the list; both
   *  are queued up front because the probe runs from a mount effect, so a mock swapped after
   *  the render has already lost the race. */
  async function reachBindWithNoModels(probe: unknown) {
    chatModels.mockResolvedValue([])
    testModelProvider.mockResolvedValueOnce({ ok: true, status: 'connected', message: 'Reachable' })
    testModelProvider.mockResolvedValue(probe as never)
    const h = renderStep()
    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    fireEvent.change(await screen.findByLabelText('OpenAI API Key'), { target: { value: 'sk-secret' } })
    fireEvent.click(screen.getByRole('button', { name: /Save and test/ }))
    await waitFor(() => expect(chatModels).toHaveBeenCalled())
    return h
  }

  it('never asserts the old claim about the provider', async () => {
    await reachBindWithNoModels({ ok: true, status: 'connected', message: 'Connected — 0 model(s) available' })
    await screen.findByText(/offered no chat-capable model/)
    expect(screen.queryByText(/No chat-capable models were discovered for this provider/)).toBeNull()
  })

  it('says the provider could not be REACHED when that is what the probe found', async () => {
    await reachBindWithNoModels({ ok: false, status: 'error', message: 'invalid_api_key' })
    const said = await screen.findByText(/could not be reached: invalid_api_key/)
    expect(said.textContent).toMatch(/connection problem, not a provider without models/)
  })

  it('says an empty list proves nothing for a provider with no connectivity test', async () => {
    await reachBindWithNoModels({ ok: true, status: 'no_probe', message: 'No connectivity probe available for this provider type' })
    expect(await screen.findByText(/cannot be told apart from a provider that is not answering/)).toBeTruthy()
  })

  it('states both possibilities when it does not know which provider is configured', async () => {
    // The lane arrived at `bind` from readiness, so no provider name was learned here and
    // there is nothing to probe by name. Guessing one of the two answers would be a lie.
    chatModels.mockResolvedValue([])
    renderStep({ readiness: { needs_model: true, has_model_provider: true, has_chat_binding: false } })
    expect(await screen.findByText(/offers no chat-capable model, or it could not be reached/)).toBeTruthy()
    expect(testModelProvider, 'there is no provider name to test').not.toHaveBeenCalled()
  })
})

// ── "ok" from the provider test is not "connected" ────────────────────────────

describe('an untested connection is not reported as a passed test', () => {
  it('does not promise a real connection test the button cannot always run', async () => {
    renderStep()
    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    await screen.findByLabelText('OpenAI API Key')
    // The earlier copy promised "test the connection for real before moving on" — untrue for a
    // provider type whose test answers `no_probe` (nothing ran).
    expect(screen.queryByText(/test the connection for real/)).toBeNull()
    expect(screen.getByText(/checks that chat can really use it/)).toBeTruthy()
  })

  it('tells the user the model list is the first evidence when nothing could be tested', async () => {
    testModelProvider.mockResolvedValue({ ok: true, status: 'no_probe', message: 'No connectivity probe available for this provider type' })
    renderStep()
    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    fireEvent.change(await screen.findByLabelText('OpenAI API Key'), { target: { value: 'sk-secret' } })
    fireEvent.click(screen.getByRole('button', { name: /Save and test/ }))
    // A function matcher, because the provider name is its own text node: `{provider} has no…`
    // renders two children and a string matcher spans neither.
    expect(await screen.findByText((_t, el) =>
      el?.tagName === 'P' && /^openai has no connectivity test, so these models are the first evidence it answers\.$/.test(el.textContent || ''),
    )).toBeTruthy()
  })

  it('creates the provider under its TYPE, never the app name', async () => {
    // `ollama-models` is the app; `ollama` is the provider key a `provider:model` ref, the
    // test route and every diagnosis speak. An app name here binds something that never
    // resolves — and the failure surfaces far from this screen.
    renderStep()
    await openCard('openai')
    fireEvent.click(await screen.findByRole('button', { name: /Install OpenAI/ }))
    fireEvent.change(await screen.findByLabelText('OpenAI API Key'), { target: { value: 'sk-secret' } })
    fireEvent.click(screen.getByRole('button', { name: /Save and test/ }))
    await waitFor(() => expect(createModelProvider).toHaveBeenCalled())
    const [body] = createModelProvider.mock.calls[0]
    expect(body.name).toBe('openai')
    expect(body.name).not.toBe('openai-models')
    expect(testModelProvider).toHaveBeenCalledWith('openai')
  })
})
