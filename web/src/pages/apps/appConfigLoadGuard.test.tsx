import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A form that never loaded must not be allowed to save ────────────────────────────────────────
//
// `useAppConfig` reads `api.appConfig(name)` through `useCachedData` and DISCARDED the hook's
// `error`. Two consequences, and the second one writes:
//
//   1. `loading` is `data === undefined`, which stays true forever on a failed read — the Configure
//      modal shows "Loading…" with no error and no retry.
//   2. `cur` falls back to `{...schemaDefaults, ...(data?.config ?? {})}` = `{}` when nothing loaded,
//      and `save()` PUTs exactly that. The backend's `write_config` does NOT merge — it
//      `atomic_write`s `json.dumps(values)` over the file — so a save from an unloaded form REPLACES
//      the app's stored config with `{}`. Secrets are not spared either: the preserve-the-secret
//      branch only fires for keys PRESENT in the payload, and an unloaded form sends none.
//
// Reachable without any failure, too: the modal's footer sits OUTSIDE its loading ternary with
// `disabled={cfg.busy}`, so Save is clickable during the normal load window.
//
// The guard lives in the HOOK, not in the two call sites: every consumer inherits it, and a third
// one cannot forget it.

const config = { schema: { properties: { room: { type: 'string' } } }, config: { room: 'general' }, _secret_set: [] }

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      appConfig: () => Promise.resolve(config),
      saveAppConfig: vi.fn(() => Promise.resolve({ ok: true })),
      ...over,
    },
  }))
}

/** Drives the real hook through a probe, so no component needs exporting for a test. */
async function mountProbe() {
  const { useAppConfig } = await import('./appConfigForm')
  const seen: { loading: boolean; error: unknown; cur: Record<string, unknown> }[] = []
  function Probe() {
    const cfg = useAppConfig('slack-channel') as ReturnType<typeof useAppConfig> & { error?: unknown }
    seen.push({ loading: cfg.loading, error: cfg.error, cur: cfg.cur })
    return (
      <div>
        <span data-testid="loading">{String(cfg.loading)}</span>
        <span data-testid="error">{cfg.error ? 'yes' : 'no'}</span>
        <span data-testid="cur">{JSON.stringify(cfg.cur)}</span>
        <button onClick={() => cfg.save()}>save</button>
        <span data-testid="err">{cfg.err ?? ''}</span>
      </div>
    )
  }
  render(<Probe />)
  return { seen }
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('a failed app-config read is reported, not hidden behind "Loading…"', () => {
  it('surfaces the error instead of loading forever', async () => {
    mockApi({ appConfig: () => Promise.reject(new Error('gateway down')) })
    await mountProbe()
    await waitFor(() => expect(screen.getByTestId('error').textContent).toBe('yes'))
    expect(screen.getByTestId('loading').textContent, 'a failed read is not still loading').toBe('false')
  })

  it('reports the load failure to the user when a save is attempted anyway', async () => {
    const saveAppConfig = vi.fn(() => Promise.resolve({ ok: true }))
    mockApi({ appConfig: () => Promise.reject(new Error('gateway down')), saveAppConfig })
    await mountProbe()
    await waitFor(() => expect(screen.getByTestId('error').textContent).toBe('yes'))
    fireEvent.click(screen.getByRole('button', { name: 'save' }))
    // 🔑 The whole point: NO write. `{}` would have replaced the stored config wholesale.
    await waitFor(() => expect(screen.getByTestId('err').textContent).toMatch(/could ?n[o']t|failed|not loaded/i))
    expect(saveAppConfig, 'an unloaded form must not write').not.toHaveBeenCalled()
  })

  it('refuses the same write DURING the normal load window — no failure required', async () => {
    const saveAppConfig = vi.fn(() => Promise.resolve({ ok: true }))
    // A read that never settles is exactly the first paint of a healthy load.
    mockApi({ appConfig: () => new Promise(() => {}), saveAppConfig })
    await mountProbe()
    expect(screen.getByTestId('loading').textContent).toBe('true')
    expect(JSON.parse(screen.getByTestId('cur').textContent!), 'nothing has loaded yet').toEqual({})
    fireEvent.click(screen.getByRole('button', { name: 'save' }))
    await waitFor(() => expect(saveAppConfig).not.toHaveBeenCalled())
  })

  it('still saves normally once the config has loaded', async () => {
    const saveAppConfig = vi.fn((_name: string, _cfg: Record<string, unknown>) => Promise.resolve({ ok: true }))
    mockApi({ saveAppConfig })
    await mountProbe()
    await waitFor(() => expect(screen.getByTestId('loading').textContent).toBe('false'))
    expect(JSON.parse(screen.getByTestId('cur').textContent!)).toEqual({ room: 'general' })
    fireEvent.click(screen.getByRole('button', { name: 'save' }))
    await waitFor(() => expect(saveAppConfig).toHaveBeenCalledTimes(1))
    expect(saveAppConfig.mock.calls[0][1], 'the loaded values, not an empty object').toEqual({ room: 'general' })
  })
})

describe('the Save affordance matches the guard', () => {
  // The hook refuses the write, so behaviour is safe either way — but a live-looking button that
  // answers a click with "nothing to save yet" is a worse affordance than one that is plainly off.
  // 🪤 Mutation-found: un-gating this button passed every behavioural test above, because the modal
  // is an internal component. Pinned structurally instead, scoped to the ELEMENT (balanced-tag
  // extraction) rather than a character window near it.
  // 🪤 THE FIRST DRAFT SEARCHED THE WHOLE FILE and `indexOf('>Save</Button>')` landed on a DIFFERENT
  // Save button — AppsSection has several — so un-gating the modal's button passed. Scope to the
  // component first (balanced braces from its `function` line), then find the button inside it.
  // 🪤 SECOND BUG IN THIS HELPER: taking `indexOf('{')` after the function name lands on the
  // DESTRUCTURED PARAMETER object, not the body — so the scan closed early and the JSX was never in
  // range. Paren-match the signature first, and only then open the body brace.
  const componentBody = (src: string, name: string) => {
    const start = src.indexOf(`function ${name}(`)
    expect(start, `${name} must exist`).toBeGreaterThan(-1)
    let i = src.indexOf('(', start)
    let parens = 1
    i++
    while (i < src.length && parens > 0) {
      if (src[i] === '(') parens++
      else if (src[i] === ')') parens--
      i++
    }
    i = src.indexOf('{', i)
    let depth = 1
    const from = ++i
    while (i < src.length && depth > 0) {
      if (src[i] === '{') depth++
      else if (src[i] === '}') depth--
      i++
    }
    return src.slice(from, i - 1)
  }
  const buttonNamed = (body: string, label: string) => {
    const idx = body.indexOf(`>${label}</Button>`)
    expect(idx, `a Button labelled ${label} must exist in this component`).toBeGreaterThan(-1)
    const open = body.lastIndexOf('<Button', idx)
    return body.slice(open, idx + 1)
  }

  it('the Configure modal cannot offer Save before the config it would replace has loaded', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/apps/AppsSection.tsx'), 'utf8')
    const save = buttonNamed(componentBody(src, 'ConfigModal'), 'Save')
    // 🪤 THIRD BUG, mutation-found: asserting these substrings anywhere in the ELEMENT passed when the
    // `disabled=` attribute was gutted, because the sibling `disabledReason={cfg.error ? … cfg.loading
    // ? …}` still mentioned both. Read the `disabled=` expression ITSELF.
    const disabled = /disabled=\{([^}]*(?:\{[^}]*\})?[^}]*)\}/.exec(save)?.[1] ?? ''
    expect(disabled, 'the disabled expression must exist').not.toBe('')
    expect(disabled, 'gated on the read being in flight').toContain('cfg.loading')
    expect(disabled, 'and on the read having failed').toContain('cfg.error')
    expect(save, 'and it says why it is off').toMatch(/disabledReason=/)
  })

  it('the failed read renders a retry, not an eternal "Loading…"', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/apps/AppsSection.tsx'), 'utf8')
    expect(src).toMatch(/cfg\.error \?[\s\S]{0,400}?<LoadError what="app configuration"[^>]*onRetry=\{cfg\.reload\}/)
  })

  it('the settings panel answers the same failure the same way', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/AppsPanel.tsx'), 'utf8')
    expect(src, 'one family, one form').toMatch(/cfg\.error \?[\s\S]{0,400}?<LoadError what="app configuration"/)
  })
})
