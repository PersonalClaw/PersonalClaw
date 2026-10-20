import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { api, type AvailableModel, type DownloadJob, type HostModelFit } from '../../lib/api'
import { LocalModelManager } from './LocalModelManager'
import { OFF_MACHINE_LABEL, OFF_MACHINE_REASON } from './OffMachineChip'

// An Ollama instance's card lists every model its server serves, and some of them the server
// answers from Ollama's cloud rather than running them here. The card judged each one against this
// machine's memory, so a cloud model read "Fits" — a claim about this device, for a model whose
// prompts leave it. The gateway now marks such a row `runs_here: false` and sends no fit for it: the
// card says it runs off this machine instead, and the ordinary model beside it keeps its verdict.

const HOST: HostModelFit = {
  budget_mb: 6000, total_ram_mb: 16384, unified_memory: true,
  gpu_model: 'Apple M2', measured: true, hide_unrunnable: false,
}
const LOCAL: AvailableModel = {
  id: 'llama3.2:1b', name: 'llama3.2:1b', capabilities: ['chat'], provider: 'desk-ollama',
  provider_type: 'desk-ollama', size_mb: 1300, downloaded: true, fit: 'green',
  fit_reason: 'about 1.6 GB of 6.0 GB headroom', fit_need_mb: 1600, host_fit: HOST,
}
const CLOUD: AvailableModel = {
  id: 'gpt-oss:120b-cloud', name: 'gpt-oss:120b-cloud', capabilities: ['chat'],
  provider: 'desk-ollama', provider_type: 'desk-ollama', size_mb: 0, downloaded: true,
  runs_here: false, host_fit: HOST,
}

const NO_JOBS: DownloadJob[] = []

beforeEach(() => {
  // EventSource does not exist in jsdom; `useModelDownloads` only needs it to not throw.
  ;(globalThis as unknown as { EventSource: unknown }).EventSource = class {
    close() {}
    addEventListener() {}
    onerror: unknown = null
  }
  vi.spyOn(api, 'modelDownloads').mockResolvedValue(NO_JOBS)
  vi.spyOn(api, 'downloadStreamUrl').mockReturnValue('/api/models/downloads/x/stream')
})
afterEach(() => vi.restoreAllMocks())

/** The cluster beside a model's name: its downloaded check and its chips. */
const chipsOf = (name: string) => within(screen.getByText(name).parentElement as HTMLElement)

describe("a model server's card", () => {
  it('says a cloud model runs off this machine, by its words and its accessible name', () => {
    render(<LocalModelManager provider="desk-ollama" models={[LOCAL, CLOUD]} onChanged={vi.fn()} />)

    const off = chipsOf('gpt-oss:120b-cloud').getByRole('img', { name: /^Off this machine — / })
    expect(off.textContent).toBe(OFF_MACHINE_LABEL)
    expect(off.getAttribute('aria-label')).toBe(`Off this machine — ${OFF_MACHINE_REASON}`)
    expect(off.getAttribute('title')).toBe(OFF_MACHINE_REASON)
  })

  it('judges no fit for the cloud model, and keeps the ordinary model beside it as it was', () => {
    render(<LocalModelManager provider="desk-ollama" models={[LOCAL, CLOUD]} onChanged={vi.fn()} />)

    expect(chipsOf('gpt-oss:120b-cloud').queryByRole('img', { name: /^(Fits|Tight|Won't fit|Fit unknown)/ }))
      .toBeNull()
    expect(chipsOf('llama3.2:1b').getByRole('img', { name: /^Fits — about 1\.6 GB/ })).toBeTruthy()
    expect(chipsOf('llama3.2:1b').queryByRole('img', { name: /^Off this machine/ })).toBeNull()
    expect(screen.getAllByRole('img', { name: /^Off this machine/ })).toHaveLength(1)
  })
})
