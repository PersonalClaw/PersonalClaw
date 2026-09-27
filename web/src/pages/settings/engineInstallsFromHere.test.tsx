import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { EngineSection } from './EngineSection'
import { ProviderCard } from './ProviderCard'
import { api, type SettingsProvider, type SidecarInstallStatus, type SidecarInstallStep } from '../../lib/api'

// ── A sidecar app's engine installs from its card ──────────────────────────────────────────────
//
// Voice Clone TTS runs its engine in a Python environment of its own. Nothing in the dashboard
// called the install route, so the only way to put the engine there was a shell command, copied
// out of an "unavailable" sentence. The section offers Install engine, shows pip's output while it
// runs (a torch-sized install takes a while), cancels it, and says what went wrong and what to do.

const DIR = '/home/me/.personalclaw/apps/voice-clone-tts/venv'

function step(name: SidecarInstallStep['name'], status: SidecarInstallStep['status'], startedAt = 0): SidecarInstallStep {
  return { name, status, detail: '', started_at: startedAt }
}

function status(over: Partial<SidecarInstallStatus> = {}, job: Partial<SidecarInstallStatus['job']> = {}): SidecarInstallStatus {
  return {
    provider: 'voice-clone-tts', installed: false, managed: false, install_dir: DIR,
    requirements: ['omnivoice>=0.2'],
    ...over,
    job: {
      id: '', state: 'idle', progress: 0,
      steps: [step('venv', 'pending'), step('deps', 'pending'), step('weights', 'pending')],
      log_tail: [], error: '', reason: '', remediation: '', weights_progress: 0,
      ...job,
    },
  }
}

const running = (startedAt: number) => status({ managed: true }, {
  id: 'dl-7', state: 'running', progress: 0.33,
  steps: [step('venv', 'done'), step('deps', 'running', startedAt), step('weights', 'pending')],
  log_tail: ['Collecting omnivoice>=0.2', 'Downloading torch-2.5.1-cp313-none-macosx_11_0_arm64.whl (63.5 MB)'],
})

const text = () => (document.body.textContent ?? '').replace(/\s+/g, ' ')

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers() })

describe('the engine section', () => {
  it('offers Install engine and says what it puts where', async () => {
    vi.spyOn(api, 'sidecarInstallStatus').mockResolvedValue(status())
    render(<EngineSection app="voice-clone-tts" displayName="Voice Clone TTS" />)

    expect(await screen.findByRole('button', { name: 'Install engine: Voice Clone TTS' })).toBeTruthy()
    expect(text()).toContain('Voice Clone TTS runs its engine in a Python environment of its own. Install engine puts omnivoice>=0.2 there with pip.')
  })

  it("installs, shows pip's output while it runs, and says when it is done", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const read = vi.spyOn(api, 'sidecarInstallStatus').mockResolvedValue(status())
    const start = vi.spyOn(api, 'startSidecarInstall').mockResolvedValue({} as never)
    const onInstalled = vi.fn()
    render(<EngineSection app="voice-clone-tts" displayName="Voice Clone TTS" onInstalled={onInstalled} />)

    read.mockResolvedValue(running(Date.now() / 1000 - 250))
    fireEvent.click(await screen.findByRole('button', { name: 'Install engine: Voice Clone TTS' }))
    await waitFor(() => expect(start).toHaveBeenCalledWith('voice-clone-tts'))
    expect(await screen.findByText(/Installing omnivoice>=0.2… \(for 4 min\)/)).toBeTruthy()
    expect(screen.getByLabelText('What pip is doing').textContent).toContain('Downloading torch-2.5.1')
    expect(screen.getByRole('button', { name: 'Cancel the engine install: Voice Clone TTS' })).toBeTruthy()

    read.mockResolvedValue(status({ installed: true, managed: true }, {
      id: 'dl-7', state: 'done', progress: 1,
      steps: [step('venv', 'done'), step('deps', 'done'), step('weights', 'skipped')],
    }))
    await act(async () => { await vi.advanceTimersByTimeAsync(1600) })
    expect(text()).toContain("Installed: omnivoice>=0.2, in Voice Clone TTS's own Python environment.")
    expect(onInstalled).toHaveBeenCalledTimes(1)
  })

  it('cancels the running install, and offers it again', async () => {
    const read = vi.spyOn(api, 'sidecarInstallStatus').mockResolvedValue(running(Date.now() / 1000))
    const cancel = vi.spyOn(api, 'cancelModelDownload').mockResolvedValue(undefined as never)
    render(<EngineSection app="voice-clone-tts" displayName="Voice Clone TTS" />)

    read.mockResolvedValue(status({ managed: true }, {
      steps: [step('venv', 'done'), step('deps', 'cancelled'), step('weights', 'pending')],
      reason: 'cancelled', remediation: 'Install engine starts it again, from the step it stopped at.',
    }))
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel the engine install: Voice Clone TTS' }))
    await waitFor(() => expect(cancel).toHaveBeenCalledWith('dl-7'))
    expect((await screen.findByRole('alert')).textContent).toBe(
      'The install was cancelled. Install engine starts it again, from the step it stopped at.')
    expect(screen.getByRole('button', { name: 'Install engine: Voice Clone TTS' })).toBeTruthy()
  })

  it('says why an install stopped and what to do, and tries again', async () => {
    vi.spyOn(api, 'sidecarInstallStatus').mockResolvedValue(status({ managed: true }, {
      state: 'error',
      steps: [step('venv', 'done'), step('deps', 'error'), step('weights', 'pending')],
      error: 'pip exited 1: ERROR: No matching distribution found for omnivoice>=0.2',
      reason: 'pip_failed', remediation: 'Read the log tail for the failing requirement, then re-run.',
    }))
    render(<EngineSection app="voice-clone-tts" displayName="Voice Clone TTS" />)

    expect((await screen.findByRole('alert')).textContent).toBe(
      'The install stopped: pip exited 1: ERROR: No matching distribution found for omnivoice>=0.2. '
      + 'Read the log tail for the failing requirement, then re-run.')
    expect(screen.getByRole('button', { name: 'Try the engine install again: Voice Clone TTS' })).toBeTruthy()
  })

  it('removes an engine it installed, after asking', async () => {
    vi.spyOn(api, 'sidecarInstallStatus').mockResolvedValue(status({ installed: true, managed: true }))
    const remove = vi.spyOn(api, 'deleteSidecarInstall').mockResolvedValue(undefined as never)
    render(<EngineSection app="voice-clone-tts" displayName="Voice Clone TTS" />)

    fireEvent.click(await screen.findByRole('button', { name: 'Remove engine: Voice Clone TTS' }))
    expect(text()).toContain(`Remove it? This deletes ${DIR}.`)
    expect(remove).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Remove the engine: Voice Clone TTS' }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith('voice-clone-tts'))
  })

  it('has nothing to offer for an app that declares no engine packages', async () => {
    const read = vi.spyOn(api, 'sidecarInstallStatus').mockResolvedValue(status({ requirements: [] }))
    const { container } = render(<EngineSection app="voice-clone-tts" displayName="Voice Clone TTS" />)
    await waitFor(() => expect(read).toHaveBeenCalled())
    await waitFor(() => expect(container.textContent).toBe(''))
  })
})

describe('the Providers card', () => {
  const provider = (execution: string): SettingsProvider => ({
    name: 'voice-clone-tts', displayName: 'Voice Clone TTS', enabled: true, managed: true,
    availability: { state: 'unavailable', reason: 'The OmniVoice cloning engine is not installed.', checkedAt: 0 },
    provider: { type: 'model', capabilities: ['tts'], hasConfigSchema: false, execution },
  })

  it("carries a sidecar provider's engine beside the reason it is unavailable", async () => {
    vi.spyOn(api, 'sidecarInstallStatus').mockResolvedValue(status())
    render(<ProviderCard ext={provider('sidecar')} open={false} onOpenChange={() => {}} onChanged={() => {}} />)
    expect(await screen.findByRole('button', { name: 'Install engine: Voice Clone TTS' })).toBeTruthy()
    expect(text()).toContain('The OmniVoice cloning engine is not installed.')
  })

  it('asks nothing about an engine for an in-process provider', () => {
    const read = vi.spyOn(api, 'sidecarInstallStatus').mockResolvedValue(status())
    render(<ProviderCard ext={provider('in-process')} open={false} onOpenChange={() => {}} onChanged={() => {}} />)
    expect(read).not.toHaveBeenCalled()
    expect(screen.queryByRole('region', { name: /engine/ })).toBeNull()
  })
})
