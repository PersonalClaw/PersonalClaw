import { describe, expect, it, vi, afterEach } from 'vitest'
import { act, render, screen, cleanup, fireEvent } from '@testing-library/react'
import type { ConfirmOptions } from '../../ui/dialog'

// ── Issue #753: five relaxing toggles wrote directly, with no confirm ───────────────────────────
//
// Five controls change security/safety posture in the RELAXING direction on a single click:
// Settings YOLO mode, propose-fix-branches, "Allow all private networks", and turning OFF either
// "Offer password sign-in" or "Require a 2FA code". The app already ships a confirm dialog
// (`ui/dialog`'s `confirm`, precedented at `SecurityPanel`'s credential-store move and
// `AccountPanel`'s "Restart onboarding"); these five just never called it.
//
// Each test below gates on the SAME shape the credential-move rail already proves: a declined
// confirm must leave the write untouched, and the TIGHTENING direction (2FA on, YOLO off, private
// networks off) must stay one-click — a confirm there would be friction with no security value.
//
// Password sign-in is the one whose panel dialog guards the OTHER way. Turning it OFF can lock the
// owner out, so the panel asks then — a lockout warning. Turning it ON is the direction that opens a
// way in, and the gateway asks that consent itself (`config/edit_spec.py`, `securityConsent.ts`), so
// the panel adds no dialog of its own there. A panel that asked its own security question forwards
// it as `patchConfig`'s third argument, so the owner is never asked twice.

const flush = () => new Promise((r) => setTimeout(r, 0))

afterEach(() => { cleanup(); vi.resetModules() })

describe('ToggleRow.confirmOn — the shared opt-in gate', () => {
  const patchFor = () => vi.fn()

  const mountToggle = async (confirmed: boolean) => {
    vi.resetModules()
    const confirmSpy = vi.fn((_req: ConfirmOptions) => Promise.resolve(confirmed))
    vi.doMock('../../ui/dialog', () => ({ confirm: confirmSpy }))
    const { ToggleRow } = await import('./settingsUI')
    return { ToggleRow, confirmSpy }
  }

  it('turning ON confirms first, and a decline writes nothing', async () => {
    const { ToggleRow, confirmSpy } = await mountToggle(false)
    const patch = patchFor()
    const { container } = render(
      <ToggleRow label="YOLO mode" cfg={{ f: false }} field="f" patch={patch as never}
        confirmOn={{ title: 'Turn on?', body: 'Consequence.' }} />,
    )
    await act(async () => {
      fireEvent.click(container.querySelector('[role="switch"]')!)
      await flush()
    })
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(patch, 'declining the dialog must not touch the config').not.toHaveBeenCalled()
  })

  it('turning ON and confirming writes exactly once, carrying the consent', async () => {
    const { ToggleRow, confirmSpy } = await mountToggle(true)
    const patch = patchFor()
    const { container } = render(
      <ToggleRow label="YOLO mode" cfg={{ f: false }} field="f" patch={patch as never}
        confirmOn={{ title: 'Turn on?', body: 'Consequence.' }} />,
    )
    await act(async () => {
      fireEvent.click(container.querySelector('[role="switch"]')!)
      await flush()
    })
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(patch).toHaveBeenCalledWith('f', true, expect.any(Function), 'YOLO mode', true)
  })

  it('turning OFF never confirms — tightening stays one-click', async () => {
    const { ToggleRow, confirmSpy } = await mountToggle(true)
    const patch = patchFor()
    const { container } = render(
      <ToggleRow label="YOLO mode" cfg={{ f: true }} field="f" patch={patch as never}
        confirmOn={{ title: 'Turn on?', body: 'Consequence.' }} />,
    )
    fireEvent.click(container.querySelector('[role="switch"]')!)
    expect(confirmSpy, 'OFF is the tightening direction here — no dialog gates it').not.toHaveBeenCalled()
    expect(patch).toHaveBeenCalledWith('f', false, expect.any(Function), 'YOLO mode')
  })

  it('a row with no confirmOn is untouched — the opt-in default', async () => {
    const { ToggleRow, confirmSpy } = await mountToggle(true)
    const patch = patchFor()
    const { container } = render(
      <ToggleRow label="Poll" cfg={{ f: false }} field="f" patch={patch as never} />,
    )
    fireEvent.click(container.querySelector('[role="switch"]')!)
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(patch).toHaveBeenCalledWith('f', true, expect.any(Function), 'Poll')
  })
})

describe('AgentDefaultsPanel — YOLO mode and propose-fix-branches', () => {
  // YOLO no longer rides the generic `patchConfig`: the panel and the hub tile both write it through
  // `agentYolo.setAgentYolo`, which asks first and sends the consent flag (`yoloOneWriter.test.ts`).
  const mountPanel = async (confirmed: boolean) => {
    vi.resetModules()
    sessionStorage.clear()
    const patchConfig = vi.fn(() => Promise.resolve({}))
    const setAgentYolo = vi.fn(() => Promise.resolve({}))
    const confirmSpy = vi.fn((_req: ConfirmOptions) => Promise.resolve(confirmed))
    vi.doMock('../../ui/dialog', () => ({ confirm: confirmSpy }))
    vi.doMock('../../lib/api', () => ({
      api: {
        personalclawConfig: () => Promise.resolve({
          agent: { yolo: false, self_qa: { enabled: true, fix_branch_enabled: false } },
        }),
        agents: () => Promise.resolve({ default_agent: '' }),
        setDefaultAgent: () => Promise.resolve({}),
        agentRunners: () => Promise.resolve([]),
        patchConfig,
        setAgentYolo,
      },
    }))
    vi.doMock('../../lib/agents', () => ({
      useAgentCatalog: () => ({ options: [], loading: false, discovered: [] }),
      ensureBindableAgentName: (v: string) => Promise.resolve(v),
    }))
    vi.doMock('../../lib/persistClaim', () => ({
      durableWorkersHint: () => '', usePersistAvailable: () => true,
    }))
    const { AgentDefaultsPanel } = await import('./AgentDefaultsPanel')
    await act(async () => { render(<AgentDefaultsPanel />); await flush() })
    return { patchConfig, setAgentYolo, confirmSpy }
  }

  it('turning on YOLO mode confirms; a decline leaves the config untouched', async () => {
    const { patchConfig, setAgentYolo, confirmSpy } = await mountPanel(false)
    await act(async () => {
      screen.getByRole('switch', { name: 'YOLO mode' }).click()
      await flush()
    })
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(setAgentYolo, 'declining must not write').not.toHaveBeenCalled()
    expect(patchConfig, 'declining must not PATCH').not.toHaveBeenCalled()
  })

  it('confirming turns YOLO mode on — through the one writer, and the switch follows the server', async () => {
    const { patchConfig, setAgentYolo, confirmSpy } = await mountPanel(true)
    const sw = screen.getByRole('switch', { name: 'YOLO mode' })
    await act(async () => {
      sw.click()
      await flush()
    })
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(setAgentYolo).toHaveBeenCalledWith(true)
    expect(patchConfig, 'never the generic patch, which carries no consent').not.toHaveBeenCalled()
    expect(sw.getAttribute('aria-checked')).toBe('true')
  })

  it('turning YOLO mode OFF never confirms — tightening stays one click', async () => {
    vi.resetModules()
    sessionStorage.clear()
    const setAgentYolo = vi.fn(() => Promise.resolve({}))
    const confirmSpy = vi.fn((_req: ConfirmOptions) => Promise.resolve(true))
    vi.doMock('../../ui/dialog', () => ({ confirm: confirmSpy }))
    vi.doMock('../../lib/api', () => ({
      api: {
        personalclawConfig: () => Promise.resolve({ agent: { yolo: true, self_qa: {} } }),
        agents: () => Promise.resolve({ default_agent: '' }),
        setDefaultAgent: () => Promise.resolve({}),
        agentRunners: () => Promise.resolve([]),
        patchConfig: vi.fn(() => Promise.resolve({})),
        setAgentYolo,
      },
    }))
    vi.doMock('../../lib/agents', () => ({
      useAgentCatalog: () => ({ options: [], loading: false, discovered: [] }),
      ensureBindableAgentName: (v: string) => Promise.resolve(v),
    }))
    vi.doMock('../../lib/persistClaim', () => ({
      durableWorkersHint: () => '', usePersistAvailable: () => true,
    }))
    const { AgentDefaultsPanel } = await import('./AgentDefaultsPanel')
    await act(async () => { render(<AgentDefaultsPanel />); await flush() })
    await act(async () => {
      screen.getByRole('switch', { name: 'YOLO mode' }).click()
      await flush()
    })
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(setAgentYolo).toHaveBeenCalledWith(false)
  })

  it('the YOLO dialog names the consequence, not a generic "are you sure"', async () => {
    const { confirmSpy } = await mountPanel(false)
    await act(async () => {
      screen.getByRole('switch', { name: 'YOLO mode' }).click()
      await flush()
    })
    const req = confirmSpy.mock.calls[0][0]
    expect(req.title.toLowerCase()).toContain('yolo')
    expect(req.body).toMatch(/skip/i)
    expect(req.body).toMatch(/tool-approval/i)
    expect(req.danger).toBe(true)
  })

  it('turning on "Propose fix branches" confirms and names the branch behaviour', async () => {
    const { patchConfig, confirmSpy } = await mountPanel(true)
    await act(async () => {
      screen.getByRole('switch', { name: 'Propose fix branches' }).click()
      await flush()
    })
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    const req = confirmSpy.mock.calls[0][0]
    expect(req.body).toMatch(/branch/i)
    // The row's own dialog is forwarded, so the gateway does not ask a second time.
    expect(patchConfig).toHaveBeenCalledWith('agent.self_qa.fix_branch_enabled', true, true)
  })
})

describe('AccountPanel — password sign-in and the 2FA requirement', () => {
  const AUTH_STATE = {
    login_enabled: true,
    credential_configured: true,
    username: 'owner',
    totp_enabled: true,
    totp_required: true,
    lockout_threshold: 5,
    lockout_window: '15 minutes',
  }

  const mountPanel = async (confirmed: boolean, state: Partial<typeof AUTH_STATE> = {}) => {
    vi.resetModules()
    sessionStorage.clear()
    const patchConfig = vi.fn(() => Promise.resolve({}))
    const confirmSpy = vi.fn((_req: ConfirmOptions) => Promise.resolve(confirmed))
    vi.doMock('../../ui/dialog', () => ({ confirm: confirmSpy }))
    vi.doMock('../../lib/api', () => ({
      api: {
        dashboardConfig: () => Promise.resolve({ username: 'owner' }),
        saveDashboardConfig: () => Promise.resolve({ ok: true }),
        personalclawConfig: () => Promise.resolve({ agent: { bot_name: '' } }),
        authSession: () => Promise.resolve({ ...AUTH_STATE, ...state }),
        setLoginPassword: () => Promise.resolve({}),
        patchConfig,
      },
    }))
    const { AccountPanel } = await import('./AccountPanel')
    await act(async () => { render(<AccountPanel />); await flush() })
    return { patchConfig, confirmSpy }
  }

  it('turning OFF password sign-in confirms; declining leaves it enabled', async () => {
    const { patchConfig, confirmSpy } = await mountPanel(false)
    await act(async () => {
      screen.getByRole('switch', { name: 'Offer password sign-in' }).click()
      await flush()
    })
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(patchConfig, 'a decline must not PATCH login_enabled').not.toHaveBeenCalled()
  })

  it('confirming turns password sign-in off', async () => {
    const { patchConfig, confirmSpy } = await mountPanel(true)
    await act(async () => {
      screen.getByRole('switch', { name: 'Offer password sign-in' }).click()
      await flush()
    })
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(patchConfig).toHaveBeenCalledWith('auth.login_enabled', false)
  })

  it('turning password sign-in ON opens no panel dialog — its consent is the gateway’s', async () => {
    const { patchConfig, confirmSpy } = await mountPanel(true, { login_enabled: false })
    await act(async () => {
      screen.getByRole('switch', { name: 'Offer password sign-in' }).click()
      await flush()
    })
    expect(confirmSpy, 'the panel dialog is the lockout warning, which only OFF needs').not.toHaveBeenCalled()
    // No consent flag: the panel asked nothing, so `patchConfig`'s consent step asks the owner.
    expect(patchConfig).toHaveBeenCalledWith('auth.login_enabled', true)
  })

  it('turning OFF the 2FA requirement confirms; declining leaves it required', async () => {
    const { patchConfig, confirmSpy } = await mountPanel(false)
    await act(async () => {
      screen.getByRole('switch', { name: 'Require a 2FA code' }).click()
      await flush()
    })
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(patchConfig, 'a decline must not PATCH require_totp').not.toHaveBeenCalled()
  })

  it('confirming turns the 2FA requirement off', async () => {
    const { patchConfig, confirmSpy } = await mountPanel(true)
    await act(async () => {
      screen.getByRole('switch', { name: 'Require a 2FA code' }).click()
      await flush()
    })
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    // The panel's dialog IS the consent, forwarded so the gateway does not ask again.
    expect(patchConfig).toHaveBeenCalledWith('auth.require_totp', false, true)
  })

  it('turning the 2FA requirement ON never confirms — tightening stays one-click', async () => {
    const { patchConfig, confirmSpy } = await mountPanel(true, { totp_required: false })
    await act(async () => {
      screen.getByRole('switch', { name: 'Require a 2FA code' }).click()
      await flush()
    })
    expect(confirmSpy, 'requiring 2FA is not the relaxing direction').not.toHaveBeenCalled()
    expect(patchConfig).toHaveBeenCalledWith('auth.require_totp', true, false)
  })
})

describe('SecurityPanel — allow all private networks', () => {
  const mountPanel = async (confirmed: boolean, allowPrivate = false) => {
    vi.resetModules()
    sessionStorage.clear()
    const setSecurityEgress = vi.fn(() => Promise.resolve({}))
    const confirmSpy = vi.fn((_req: ConfirmOptions) => Promise.resolve(confirmed))
    vi.doMock('../../ui/dialog', () => ({ confirm: confirmSpy }))
    vi.doMock('../../lib/api', () => ({
      api: {
        securityStats: () => Promise.resolve({
          denied_commands: 0, suspicious_patterns: 0, tool_schemas: 0, redaction_paths: 0,
        }),
        deniedCommands: () => Promise.resolve({
          builtin: [], user: [], user_additions: 0,
          baseline: { version: '1', pattern_count: 0, sha256: 'a'.repeat(64), verified: true, user_additions: 0 },
        }),
        securityEgress: () => Promise.resolve({ allow_hosts: [], deny_hosts: [], allow_private: allowPrivate }),
        setSecurityEgress,
        desktopState: () => Promise.resolve({
          connected: false, shell: null, capabilities: {}, registered_at: '', last_seen: '',
        }),
        credentialStore: () => Promise.resolve(null),
        setUserDeniedCommands: () => Promise.resolve({}),
        personalclawConfig: () => Promise.resolve({ sandbox: {} }),
        patchConfig: () => Promise.resolve({}),
      },
    }))
    const { SecurityPanel } = await import('./SecurityPanel')
    await act(async () => { render(<SecurityPanel />); await flush() })
    return { setSecurityEgress, confirmSpy }
  }

  it('checking it confirms first, and a decline saves nothing', async () => {
    const { setSecurityEgress, confirmSpy } = await mountPanel(false, false)
    const box = screen.getByRole('checkbox', { name: /allow all private networks/i })
    await act(async () => { fireEvent.click(box); await flush() })
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(setSecurityEgress, 'declining must not save the relaxed policy').not.toHaveBeenCalled()
  })

  it('confirming saves allow_private: true', async () => {
    const { setSecurityEgress, confirmSpy } = await mountPanel(true, false)
    const box = screen.getByRole('checkbox', { name: /allow all private networks/i })
    await act(async () => { fireEvent.click(box); await flush() })
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    // …carrying the consent, so the gateway does not ask a second time.
    expect(setSecurityEgress).toHaveBeenCalledWith(
      expect.objectContaining({ allow_private: true }), true,
    )
  })

  it('the dialog names the SSRF consequence', async () => {
    const { confirmSpy } = await mountPanel(false, false)
    const box = screen.getByRole('checkbox', { name: /allow all private networks/i })
    await act(async () => { fireEvent.click(box); await flush() })
    const req = confirmSpy.mock.calls[0][0]
    expect(req.body).toMatch(/SSRF/i)
    expect(req.danger).toBe(true)
  })

  it('unchecking it never confirms — tightening stays one-click', async () => {
    const { setSecurityEgress, confirmSpy } = await mountPanel(true, true)
    const box = screen.getByRole('checkbox', { name: /allow all private networks/i })
    await act(async () => { fireEvent.click(box); await flush() })
    expect(confirmSpy, 'turning it off is not the relaxing direction').not.toHaveBeenCalled()
    expect(setSecurityEgress).toHaveBeenCalledWith(
      expect.objectContaining({ allow_private: false }), false,
    )
  })
})
