import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { NoModelSetupState, isNoModelSetupError, MODELS_ROUTE } from './NoModelSetupState'

// WT-04. The trigger is a turn-level error, and the ONLY thing linking this surface to
// the backend is the text of `AgentError.render()`. These fixtures are copied verbatim
// from `resolve_provider_for_use_case` in
// `src/personalclaw/providers/provider_bridge.py` (code ERR_MODEL_UNRESOLVED). If a
// reword drifts them, THIS test fails — which is the point: the reframe must never
// silently revert to dumping the raw envelope, nor start swallowing a different error.

// The fresh-instance case the screenshot captured: no provider declares the capability.
const NO_MODEL_ENVELOPE = [
  "WHAT: no model provider resolves for use case 'chat'",
  'WHY: no provider in config.json declares the capability this use case needs',
  "FIX: add a model provider in Settings → Providers, then bind 'chat' to it",
].join('\n')

// The stale-pin variant: a model WAS chosen and its provider later went missing. A
// different situation than first-touch setup — it must NOT be reframed as "connect one".
// The WHY/FIX pair is now DERIVED per cause (`_diagnose_unbuildable_ref`, #3408) rather
// than being one unconditional sentence, so two of its shapes are pinned here: the
// entry-really-is-gone case, and the missing-type-factory case, which used to render as
// the first one and told the user to install their own entry name in the App Store.
const STALE_PIN_ENVELOPE = [
  "WHAT: the model pinned for use case 'chat' ('Bedrock:global.anthropic.claude-opus-4-8') cannot be built",
  "WHY: no provider named 'Bedrock' is in config.json — the entry was renamed or removed, or its app was uninstalled",
  "FIX: re-add 'Bedrock' in Settings → Providers, or rebind 'chat' to an available model in Settings → Models",
].join('\n')

const MISSING_TYPE_FACTORY_ENVELOPE = [
  "WHAT: the model pinned for use case 'chat' ('Ghost Provider:ghost-7b') cannot be built",
  "WHY: provider 'Ghost Provider' declares type 'vllm', and no installed app registers that type",
  "FIX: install an app that provides 'vllm' in the App Store, or change 'Ghost Provider's type in Settings → Providers",
].join('\n')

describe('isNoModelSetupError', () => {
  it('matches the no-model-configured envelope', () => {
    expect(isNoModelSetupError(NO_MODEL_ENVELOPE)).toBe(true)
  })

  it('does NOT match the stale-pin variant (a model was chosen, config.json is mentioned)', () => {
    // Guards against over-matching on "config.json" alone.
    expect(isNoModelSetupError(STALE_PIN_ENVELOPE)).toBe(false)
  })

  it('does NOT match the missing-type-factory variant either', () => {
    // A per-cause WHY must not accidentally land inside the first-run matcher.
    expect(isNoModelSetupError(MISSING_TYPE_FACTORY_ENVELOPE)).toBe(false)
  })

  it('does NOT match unrelated turn errors or empty input', () => {
    expect(isNoModelSetupError('The model returned an error.')).toBe(false)
    expect(isNoModelSetupError('WHAT: a tool argument failed validation\nWHY: …\nFIX: …')).toBe(false)
    expect(isNoModelSetupError('')).toBe(false)
    expect(isNoModelSetupError(null)).toBe(false)
    expect(isNoModelSetupError(undefined)).toBe(false)
  })
})

describe('NoModelSetupState', () => {
  it('leads with a plain sentence and the way forward, not the raw envelope', () => {
    render(<NoModelSetupState detail={NO_MODEL_ENVELOPE} onSetup={() => {}} />)
    expect(screen.getByText('No model connected yet')).toBeTruthy()
    expect(screen.getByText(/connect a model to start chatting/i)).toBeTruthy()
    // No bare "Error"/"Something went wrong" and no code/stack in the primary line.
    expect(screen.queryByText(/^error$/i)).toBeNull()
  })

  it('keeps the full WHAT/WHY/FIX detail behind a collapsed disclosure', () => {
    const { container } = render(<NoModelSetupState detail={NO_MODEL_ENVELOPE} onSetup={() => {}} />)
    const details = container.querySelector('details')
    expect(details).not.toBeNull()
    // Collapsed by default (no `open` attribute) …
    expect(details!.hasAttribute('open')).toBe(false)
    // … but the raw envelope is present inside it for anyone who wants it.
    const pre = details!.querySelector('pre')
    expect(pre?.textContent).toContain('no model provider resolves for use case')
    expect(pre?.textContent).toContain('config.json')
  })

  it('offers a CTA that hands navigation to the router (no raw hash write)', () => {
    const onSetup = vi.fn()
    render(<NoModelSetupState detail={NO_MODEL_ENVELOPE} onSetup={onSetup} />)
    fireEvent.click(screen.getByRole('button', { name: /set up a model/i }))
    expect(onSetup).toHaveBeenCalledTimes(1)
  })

  it('agrees with DegradedChip on the destination', () => {
    // DegradedChip's "Bind a model" nudge links to #/settings/models; the CTA's
    // navigate() path must resolve to the same place.
    expect(MODELS_ROUTE).toBe('#/settings/models')
  })
})
