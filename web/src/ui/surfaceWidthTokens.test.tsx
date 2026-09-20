import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { Loop } from '../lib/api'

const { LOOP } = vi.hoisted(() => ({
  LOOP: {
    id: 'width-token-loop',
    kind: 'goal',
    name: 'Width token loop',
    task: 'Keep the cockpit cap independent of page width',
    execution: 'solo',
    agent: 'test-agent',
    model: 'test-model',
    attended: true,
    max_cycles: 3,
    idle_secs: 60,
    success_criteria: null,
    status: 'ready',
    total_cycles: 0,
    error_message: null,
    created_at: 1_780_000_000,
    started_at: null,
    completed_at: null,
    kind_config: { goal_type: 'verifiable', granularity: 'balanced' },
  } as Loop,
}))

vi.mock('../lib/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../lib/api')>()
  return {
    ...original,
    api: {
      ...original.api,
      themes: () => new Promise(() => {}),
      theme: () => new Promise(() => {}),
      uLoop: () => Promise.resolve(LOOP),
      uLoopAction: () => Promise.resolve(LOOP),
      uLoopReport: () => Promise.resolve({ report: '', log: '' }),
      artifacts: () => Promise.resolve([]),
      task: () => Promise.resolve(null),
      project: () => Promise.resolve({ name: 'Test project' }),
    },
  }
})

vi.mock('../pages/loops/useRunStream', () => ({
  useRunStream: () => ({ connected: false }),
}))

vi.mock('../lib/useChatSocket', () => ({
  useChatSocket: () => {},
}))

import {
  AppearanceProvider,
  DEFAULT_WIDTH_PRESET,
  SURFACE_WIDTHS,
  WIDTH_PRESETS,
} from '../app/appearance'
import { LoopCockpitPage } from '../pages/loops/LoopCockpitPage'
import { Modal } from './Modal'
import { SidePanel } from './SidePanel'
import { SnipOverlay } from './SnipOverlay'

function mount(ui: ReactElement) {
  return render(<AppearanceProvider>{ui}</AppearanceProvider>)
}

function cappedAncestor(node: HTMLElement): HTMLElement {
  let current: HTMLElement | null = node
  while (current && !current.style.maxWidth) current = current.parentElement
  if (!current) throw new Error('surface cap not found in rendered ancestors')
  return current
}

function expectRealCap(node: HTMLElement, expected: string) {
  expect(DEFAULT_WIDTH_PRESET).toBe('full')
  expect(WIDTH_PRESETS[DEFAULT_WIDTH_PRESET]).toBe('100%')
  expect(document.documentElement.style.getPropertyValue('--content-width')).toBe('100%')
  expect(node.style.maxWidth).toBe(expected)
  expect(node.style.maxWidth).toMatch(/^\d+px$/)
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('appearance', JSON.stringify({ widthPreset: 'full' }))
  document.documentElement.removeAttribute('style')
})

describe('dialogs and overlays keep real caps under the shipped full-width page preset', () => {
  it('caps Modal with its own width token', () => {
    mount(<Modal title="Token dialog" onClose={() => {}}><span>Modal body</span></Modal>)
    expectRealCap(screen.getByRole('dialog', { name: 'Token dialog' }), SURFACE_WIDTHS.modal)
  })

  it('caps the expanded SidePanel with its own width token', () => {
    mount(
      <SidePanel title="Token panel" onClose={() => {}}>
        <span>Expanded panel body</span>
      </SidePanel>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Expand to full width' }))
    expectRealCap(
      cappedAncestor(screen.getByText('Expanded panel body')),
      SURFACE_WIDTHS.expandedSidePanel,
    )
  })

  it('caps SnipOverlay with its own width token', () => {
    mount(
      <SnipOverlay
        frame="data:image/png;base64,AAAA"
        width={1600}
        height={900}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    )
    expectRealCap(
      screen.getByRole('dialog', { name: /crop the captured screen/i }),
      SURFACE_WIDTHS.snipOverlay,
    )
  })

  it('caps LoopCockpitPage with its own width token', async () => {
    mount(
      <LoopCockpitPage
        id={LOOP.id}
        onBack={() => {}}
        query={{}}
        setQuery={() => {}}
      />,
    )
    expectRealCap(
      cappedAncestor(await screen.findByText('Prompt')),
      SURFACE_WIDTHS.loopCockpit,
    )
  })
})
