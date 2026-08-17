import { afterEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { SnipOverlay } from './SnipOverlay'

// ── The crop overlay must be operable without a mouse (CHAT-CRAFT CC-4) ─────────────
//
// A crop UI is the easiest place in an app to ship a mouse-only surface: "drag a
// rectangle" has no keyboard equivalent unless one is designed in. So the selection
// starts as the WHOLE frame — Enter is a complete action from the first keystroke — and
// arrows move it, Alt+arrows resize it.
//
// What jsdom CANNOT show, stated rather than faked: `getBoundingClientRect` returns
// zeros, so a pointer drag has no geometry to map into source pixels and the drag path
// is verified only by driving a real browser. Everything below is the keyboard contract,
// the dialog contract, and the cancel-leaves-nothing contract — all observable here.

const FRAME = 'data:image/png;base64,AAAA'
const W = 1600
const H = 900

function open(overrides: { onCancel?: () => void; onConfirm?: (r: unknown) => void } = {}) {
  const onCancel = overrides.onCancel ?? vi.fn()
  const onConfirm = overrides.onConfirm ?? vi.fn()
  render(<SnipOverlay frame={FRAME} width={W} height={H} onCancel={onCancel} onConfirm={onConfirm} />)
  const stage = screen.getByRole('group', { name: /captured screen/i })
  return { onCancel, onConfirm, stage }
}

afterEach(cleanup)

describe('SnipOverlay — the dialog contract', () => {
  it('is a modal dialog that owns focus', () => {
    open()
    const dialog = screen.getByRole('dialog', { name: /crop the captured screen/i })
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    // aria-modal is a promise that focus is owned: the trap must have moved focus
    // inside, or a keyboard user tabs into the page behind the scrim.
    expect(dialog.contains(document.activeElement)).toBe(true)
  })

  it('names both of its actions', () => {
    open()
    expect(screen.getByRole('button', { name: /^cancel$/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /attach selection/i })).toBeTruthy()
  })

  it('tells the user how to drive it from the keyboard', () => {
    const { stage } = open()
    const name = stage.getAttribute('aria-label') ?? ''
    expect(name.toLowerCase()).toContain('arrow keys')
    expect(name.toLowerCase()).toContain('escape')
    expect(stage.getAttribute('tabindex')).toBe('0')
  })
})

describe('SnipOverlay — cancelling leaves nothing behind', () => {
  it('Escape cancels and never confirms', () => {
    const { onCancel, onConfirm, stage } = open()
    fireEvent.keyDown(stage, { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onConfirm, 'a cancelled snip must not produce an attachment').not.toHaveBeenCalled()
  })

  it('the Cancel button cancels and never confirms', () => {
    const { onCancel, onConfirm } = open()
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }))
    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
  })
})

describe('SnipOverlay — the keyboard crop', () => {
  it('starts selected on the whole frame, so Enter alone is a complete action', () => {
    const { onConfirm, stage } = open()
    fireEvent.keyDown(stage, { key: 'Enter' })
    expect(onConfirm).toHaveBeenCalledWith({ x: 0, y: 0, width: W, height: H })
  })

  it('Alt+arrows resize and plain arrows move, in source pixels', () => {
    const { onConfirm, stage } = open()
    fireEvent.keyDown(stage, { key: 'ArrowLeft', altKey: true })   // width 1600 → 1576
    fireEvent.keyDown(stage, { key: 'ArrowUp', altKey: true })     // height 900 → 876
    fireEvent.keyDown(stage, { key: 'ArrowRight' })                // x 0 → 24
    fireEvent.keyDown(stage, { key: 'ArrowDown' })                 // y 0 → 24
    fireEvent.keyDown(stage, { key: 'Enter' })
    expect(onConfirm).toHaveBeenCalledWith({ x: 24, y: 24, width: 1576, height: 876 })
  })

  it('Shift travels faster without changing direction', () => {
    const { onConfirm, stage } = open()
    fireEvent.keyDown(stage, { key: 'ArrowLeft', altKey: true, shiftKey: true })  // width -192
    fireEvent.keyDown(stage, { key: 'ArrowRight', shiftKey: true })               // x +192
    fireEvent.keyDown(stage, { key: 'Enter' })
    expect(onConfirm).toHaveBeenCalledWith({ x: 192, y: 0, width: 1408, height: H })
  })

  it('a nudge at the edge stops instead of silently shrinking the crop', () => {
    const { onConfirm, stage } = open()
    fireEvent.keyDown(stage, { key: 'ArrowRight' })  // already flush right at full width
    fireEvent.keyDown(stage, { key: 'ArrowUp' })
    fireEvent.keyDown(stage, { key: 'Enter' })
    expect(onConfirm).toHaveBeenCalledWith({ x: 0, y: 0, width: W, height: H })
  })

  it('never lets the selection collapse to nothing', () => {
    const { onConfirm, stage } = open()
    for (let i = 0; i < 200; i++) fireEvent.keyDown(stage, { key: 'ArrowLeft', altKey: true, shiftKey: true })
    for (let i = 0; i < 200; i++) fireEvent.keyDown(stage, { key: 'ArrowUp', altKey: true, shiftKey: true })
    fireEvent.keyDown(stage, { key: 'Enter' })
    const rect = (onConfirm as ReturnType<typeof vi.fn>).mock.calls[0][0] as { width: number; height: number }
    expect(rect.width).toBeGreaterThanOrEqual(16)
    expect(rect.height).toBeGreaterThanOrEqual(16)
  })

  it('shows the live selection size, so a crop is not a guess', () => {
    const { stage } = open()
    expect(screen.getByText(/1600×900 px/)).toBeTruthy()
    fireEvent.keyDown(stage, { key: 'ArrowLeft', altKey: true })
    expect(screen.getByText(/1576×900 px/)).toBeTruthy()
  })

  it('the Attach button confirms the current selection', () => {
    const { onConfirm, stage } = open()
    fireEvent.keyDown(stage, { key: 'ArrowLeft', altKey: true })
    fireEvent.click(screen.getByRole('button', { name: /attach selection/i }))
    expect(onConfirm).toHaveBeenCalledWith({ x: 0, y: 0, width: 1576, height: H })
  })
})

describe('SnipOverlay — motion + honesty rails', () => {
  const src = readFileSync(join(process.cwd(), 'src/ui/SnipOverlay.tsx'), 'utf8')

  it('honours reduced motion through the app-wide source of truth', () => {
    // Asserted at the source: framer's entrance is not observable in jsdom, and the
    // property IS which knob the component reads.
    expect(src).toMatch(/useReducedMotion \} from 'framer-motion'/)
    expect(src).toMatch(/const enterScale = reduce \? 1 :/)
    expect(src).toMatch(/const enterY = reduce \? 0 :/)
  })

  it('Escape does not also close the layer behind it', () => {
    expect(src).toMatch(/e\.stopPropagation\(\)/)
  })

  it('renders a still, never a live capture', () => {
    // No stream, no video element, no getDisplayMedia: by the time this mounts the
    // capture is already stopped (grabOneFrame's finally), and the overlay must not be
    // able to re-open one.
    expect(src).not.toMatch(/getDisplayMedia|srcObject|MediaStream/)
  })
})
