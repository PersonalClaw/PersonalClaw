import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { NoModelSetupState, isNoModelSetupError, noModelChosenFor, MODELS_ROUTE } from './NoModelSetupState'

// The trigger is a turn-level error, and the ONLY thing linking this surface to
// the backend is the text of that error: the sentence `AgentError.sentence()` makes of the
// resolver's coded refusal (`resolve_provider_for_use_case` in
// `src/personalclaw/providers/provider_bridge.py`, code ERR_MODEL_UNRESOLVED), which the chat's
// error line says (`llm_helpers.humanize_provider_error`). These fixtures are copied verbatim
// from tests/test_a_send_with_no_model_says_what_to_do.py, which pins the same sentences against
// the real handler. If a reword drifts them, THIS test fails — which is the point: the reframe
// must never silently revert to showing the raw refusal, nor start swallowing a different error.

// The fresh-instance case the screenshot captured: no provider declares the capability.
const NO_MODEL_SAID =
  "No model provider resolves for use case 'chat': no provider in config.json declares the " +
  "capability this use case needs. Add a model provider in Settings → Providers, then bind 'chat' to it."

// A provider IS connected, but no model is chosen for it: an instance saved from the Add-instance
// form with no Default Model (it writes `model: ""`), and nothing bound in Settings → Models. The
// resolver refuses the turn rather than send Ollama an empty model or let a provider pick one, and
// its reason is `no_model_chosen` (src/personalclaw/llm/registry.py).
const NO_MODEL_CHOSEN_SAID =
  "No model provider resolves for use case 'chat': no model is chosen for “local”. " +
  'Choose one of its models in Settings → Models.'

// The stale-pin variant: a model WAS chosen and its provider later went missing. A
// different situation than first-touch setup — it must NOT be reframed as "connect one".
// The reason and fix are DERIVED per cause (`_diagnose_unbuildable_ref`, #3408) rather
// than being one unconditional sentence, so two of its shapes are pinned here: the
// entry-really-is-gone case, and the missing-type-factory case, which used to render as
// the first one and told the user to install their own entry name in the App Store.
const STALE_PIN_SAID =
  "The model pinned for use case 'chat' ('Bedrock:global.anthropic.claude-opus-4-8') cannot be built: " +
  "no provider named 'Bedrock' is in config.json — the entry was renamed or removed, or its app was uninstalled. " +
  "Re-add 'Bedrock' in Settings → Providers, or rebind 'chat' to an available model in Settings → Models."

const MISSING_TYPE_FACTORY_SAID =
  "The model pinned for use case 'chat' ('Ghost Provider:ghost-7b') cannot be built: " +
  "provider 'Ghost Provider' declares type 'vllm', and no installed app registers that type. " +
  "Install an app that provides 'vllm' in the App Store, or change 'Ghost Provider's type in Settings → Providers."

describe('isNoModelSetupError', () => {
  it('matches the no-model-configured refusal', () => {
    expect(isNoModelSetupError(NO_MODEL_SAID)).toBe(true)
  })

  it('does NOT match the stale-pin variant (a model was chosen, config.json is mentioned)', () => {
    // Guards against over-matching on "config.json" alone.
    expect(isNoModelSetupError(STALE_PIN_SAID)).toBe(false)
  })

  it('does NOT match the missing-type-factory variant either', () => {
    // A per-cause WHY must not accidentally land inside the first-run matcher.
    expect(isNoModelSetupError(MISSING_TYPE_FACTORY_SAID)).toBe(false)
  })

  it('matches the no-model-CHOSEN refusal too (a provider is connected, no model picked)', () => {
    expect(isNoModelSetupError(NO_MODEL_CHOSEN_SAID)).toBe(true)
  })

  it('does NOT match unrelated turn errors or empty input', () => {
    expect(isNoModelSetupError('The model returned an error.')).toBe(false)
    expect(isNoModelSetupError('WHAT: a tool argument failed validation\nWHY: …\nFIX: …')).toBe(false)
    expect(isNoModelSetupError('')).toBe(false)
    expect(isNoModelSetupError(null)).toBe(false)
    expect(isNoModelSetupError(undefined)).toBe(false)
  })
})

describe('noModelChosenFor', () => {
  it('reads the provider out of the no-model-chosen reason, and nothing out of any other', () => {
    expect(noModelChosenFor(NO_MODEL_CHOSEN_SAID)).toBe('local')
    expect(noModelChosenFor(NO_MODEL_SAID)).toBeNull()
    expect(noModelChosenFor(STALE_PIN_SAID)).toBeNull()
    expect(noModelChosenFor(MISSING_TYPE_FACTORY_SAID)).toBeNull()
    // The model's own reading of the same refusal (its labelled lines) names the provider too.
    expect(noModelChosenFor('WHY: no model is chosen for “local”')).toBe('local')
    expect(noModelChosenFor('')).toBeNull()
    expect(noModelChosenFor(null)).toBeNull()
  })
})

describe('NoModelSetupState', () => {
  it('leads with a plain sentence and the way forward, not the raw envelope', () => {
    render(<NoModelSetupState detail={NO_MODEL_SAID} onSetup={() => {}} />)
    expect(screen.getByText('No model connected yet')).toBeTruthy()
    expect(screen.getByText(/connect a model to start chatting/i)).toBeTruthy()
    // No bare "Error"/"Something went wrong" and no code/stack in the primary line.
    expect(screen.queryByText(/^error$/i)).toBeNull()
  })

  it('keeps the full refusal behind a collapsed disclosure', () => {
    const { container } = render(<NoModelSetupState detail={NO_MODEL_SAID} onSetup={() => {}} />)
    const details = container.querySelector('details')
    expect(details).not.toBeNull()
    // Collapsed by default (no `open` attribute) …
    expect(details!.hasAttribute('open')).toBe(false)
    // … but the whole refusal is present inside it for anyone who wants it.
    const pre = details!.querySelector('pre')
    expect(pre?.textContent).toContain('No model provider resolves for use case')
    expect(pre?.textContent).toContain('config.json')
  })

  it('offers a CTA that hands navigation to the router (no raw hash write)', () => {
    const onSetup = vi.fn()
    render(<NoModelSetupState detail={NO_MODEL_SAID} onSetup={onSetup} />)
    fireEvent.click(screen.getByRole('button', { name: /set up a model/i }))
    expect(onSetup).toHaveBeenCalledTimes(1)
  })

  it('names the provider when one is connected but no model is chosen for it', () => {
    // 🔴 Red on main: the card said "No model connected yet" / "Connect a model" to a user who had
    // just connected Ollama, because it read only the WHAT line.
    const onSetup = vi.fn()
    render(<NoModelSetupState detail={NO_MODEL_CHOSEN_SAID} onSetup={onSetup} />)
    expect(screen.getByText('No model chosen for local')).toBeTruthy()
    expect(screen.getByText('Choose which of its models to chat with in Settings → Models.')).toBeTruthy()
    expect(screen.queryByText('No model connected yet')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Choose a model' }))
    expect(onSetup).toHaveBeenCalledTimes(1)
  })

  it('agrees with DegradedChip on the destination', () => {
    // DegradedChip's "Bind a model" nudge links to #/settings/models; the CTA's
    // navigate() path must resolve to the same place.
    expect(MODELS_ROUTE).toBe('#/settings/models')
  })
})
