// @vitest-environment jsdom
// While YOLO is on, the shell's right corner says so on every page: "YOLO active" in warn from
// 768 px, a glyph named for the whole sentence below, a disclosure at every width that opens the
// sentence and the way to change it. It reads the status again when the gateway rings `yolo`, when
// YOLO is switched here and when a timed YOLO's time is up, and shows nothing while YOLO is off.
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { WsMessage } from '../lib/wsFrames'

const status = vi.fn()
let frame: ((m: WsMessage) => void) | null = null

vi.mock('../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      status: (...a: unknown[]) => status(...a),
      notifications: vi.fn(async () => ({ notifications: [] })),
      degraded: vi.fn(async () => ({ surfaces: [] })),
      onboarding: vi.fn(async () => ({ has_model_provider: true })),
      system: vi.fn(() => new Promise(() => {})),
    },
  }
})
vi.mock('../lib/useNeedsYou', () => ({ useNeedsYou: () => ({ rows: [], data: {}, error: null }) }))
vi.mock('../lib/useChatSocket', () => ({
  useChatSocket: (onMessage: (m: WsMessage) => void) => { frame = onMessage },
}))

const { ShellCornerRight } = await import('./ShellCorners')
const { YoloChip, YOLO_NAME, YOLO_SENTENCE } = await import('./YoloChip')
const { yoloChanged } = await import('../lib/yoloSignal')
const { TOUCH_TARGET } = await import('./Toggle')

function width(px: number) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: /max-width: (\d+)px/.test(q) ? px <= Number(/max-width: (\d+)px/.exec(q)![1]) : false,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    media: q, onchange: null, dispatchEvent: () => false,
  }))
}

const settle = () => act(async () => { await Promise.resolve(); await Promise.resolve() })

beforeEach(() => { width(1280); status.mockReset(); frame = null })
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers() })

describe('the corner says when YOLO is on', () => {
  it('reads "YOLO active" in warn from 768 px, a disclosure named for the whole sentence', async () => {
    status.mockResolvedValue({ yolo: true })
    render(<ShellCornerRight terminalOpen={false} onToggleTerminal={() => {}} navigate={() => {}} />)
    const chip = await screen.findByRole('button', { name: YOLO_NAME })
    expect(chip.textContent).toBe('YOLO active')
    expect(chip.style.color).toBe('var(--color-warn)')
    expect(chip.getAttribute('aria-expanded')).toBe('false')
    expect(chip.hasAttribute('title')).toBe(false)
    for (const cls of TOUCH_TARGET.split(' ')) expect(chip.className.split(/\s+/)).toContain(cls)
  })

  it('is a glyph named for the whole sentence in the corner\'s fold', async () => {
    width(390)
    status.mockResolvedValue({ yolo: true })
    render(<ShellCornerRight terminalOpen={false} onToggleTerminal={() => {}} navigate={() => {}} />)
    const chip = await screen.findByRole('button', { name: YOLO_NAME })
    expect(chip.textContent).toBe('')
  })

  it('opens the sentence and the way to change it on a press, and closes again', async () => {
    status.mockResolvedValue({ yolo: true })
    render(<YoloChip />)
    const chip = await screen.findByRole('button', { name: YOLO_NAME })
    fireEvent.click(chip)
    expect(chip.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText(YOLO_SENTENCE)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Change it in Agent defaults' }).getAttribute('href')).toBe('#/settings/agent')
    fireEvent.click(chip)
    expect(chip.getAttribute('aria-expanded')).toBe('false')
  })

  it('shows nothing while YOLO is off', async () => {
    status.mockResolvedValue({ yolo: false })
    const { container } = render(<YoloChip />)
    await settle()
    expect(container.innerHTML).toBe('')
    expect(screen.queryByRole('button', { name: YOLO_NAME })).toBeNull()
  })

  it('reads the status again when the gateway rings yolo, and on no other ring', async () => {
    status.mockResolvedValue({ yolo: false })
    render(<YoloChip />)
    await settle()
    expect(status).toHaveBeenCalledTimes(1)
    status.mockResolvedValue({ yolo: true })
    act(() => { frame?.({ type: 'refresh', data: { kinds: ['loops'] } }) })
    await settle()
    expect(status).toHaveBeenCalledTimes(1)
    act(() => { frame?.({ type: 'refresh', data: { kinds: ['yolo'] } }) })
    expect(await screen.findByRole('button', { name: YOLO_NAME })).toBeTruthy()
    expect(status).toHaveBeenCalledTimes(2)
  })

  it('reads it again when YOLO is switched here', async () => {
    status.mockResolvedValue({ yolo: true })
    render(<YoloChip />)
    await screen.findByRole('button', { name: YOLO_NAME })
    status.mockResolvedValue({ yolo: false })
    act(() => { yoloChanged() })
    await settle()
    expect(screen.queryByRole('button', { name: YOLO_NAME })).toBeNull()
  })

  it('reads it again once a timed YOLO\'s time is up', async () => {
    vi.useFakeTimers()
    status.mockResolvedValue({ yolo: true, yolo_expires_in: 60 })
    render(<YoloChip />)
    await settle()
    expect(screen.getByRole('button', { name: YOLO_NAME })).toBeTruthy()
    status.mockResolvedValue({ yolo: false })
    await act(async () => { vi.advanceTimersByTime(61_000) })
    await settle()
    expect(status).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('button', { name: YOLO_NAME })).toBeNull()
  })
})
