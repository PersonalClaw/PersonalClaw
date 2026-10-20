import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The chat's reply style: restored from the backend's resolution, never derived here ─────────
//
// The hook holds what the composer's picker shows for the open chat. It adopts the backend's
// RESOLVED style and source (from the session detail and from each PATCH's answer) instead of
// working one out from the conversation's choice and the agent's style, and a pick made before
// the chat exists is held as a choice and persisted when the chat is created.

const h = vi.hoisted(() => ({
  setSessionReplyStyle: vi.fn(),
  replyStyles: vi.fn(),
  notify: vi.fn(),
}))

vi.mock('../../../lib/api', () => ({
  api: { setSessionReplyStyle: h.setSessionReplyStyle, replyStyles: h.replyStyles },
}))
vi.mock('../../../app/appSdk', () => ({ notify: h.notify }))

import { useReplyStyle } from './useReplyStyle'
import type { ReplyStylePreview, ReplyStyleState } from '../../../lib/api'

const STYLES = [
  { id: 'default', title: 'Default', description: 'No style instruction.' },
  { id: 'terse', title: 'Terse', description: 'Short and direct.' },
]

const PREVIEW: ReplyStylePreview = {
  style: 'terse', source: 'conversation', persona: '', applies: 'style',
  instruction: '[REPLY STYLE: Terse]', first_turn_only: false, refused: '',
}

function resolved(over: Partial<ReplyStyleState> = {}): { ok: boolean } & ReplyStyleState {
  return {
    ok: true, reply_style: 'terse', reply_style_agent_default: 'conversational',
    reply_style_effective: 'terse', reply_style_source: 'conversation',
    reply_style_preview: PREVIEW, ...over,
  }
}

beforeEach(() => {
  h.setSessionReplyStyle.mockReset()
  h.replyStyles.mockReset().mockResolvedValue({ styles: STYLES, precedence: ['conversation', 'agent', 'platform'] })
  h.notify.mockReset()
})

describe('useReplyStyle', () => {
  it('starts with no choice and no source, before a chat exists', async () => {
    const { result } = renderHook(() => useReplyStyle(() => null))
    expect(result.current.pill.choice).toBe('')
    expect(result.current.pill.source).toBe('')
    await waitFor(() => expect(result.current.pill.styles).toEqual(STYLES))
  })

  it('restores the RESOLVED state the session detail carries', async () => {
    const { result } = renderHook(() => useReplyStyle(() => 's1'))
    act(() => result.current.restore(resolved({ reply_style: '', reply_style_source: 'agent', reply_style_effective: 'conversational' })))
    expect(result.current.pill).toMatchObject({ choice: '', effective: 'conversational', source: 'agent', agentDefault: 'conversational' })
    expect(result.current.pill.preview?.applies).toBe('style')
  })

  it('a pick on an open chat PATCHes it and adopts the answer, not the click', async () => {
    h.setSessionReplyStyle.mockResolvedValue(resolved({ reply_style: 'terse', reply_style_effective: 'terse' }))
    const { result } = renderHook(() => useReplyStyle(() => 's1'))
    await act(async () => { await result.current.pill.onSelect('terse') })
    expect(h.setSessionReplyStyle).toHaveBeenCalledWith('s1', 'terse')
    expect(result.current.pill).toMatchObject({ choice: 'terse', effective: 'terse', source: 'conversation' })
  })

  it('reset sends "" and shows what the chat follows again', async () => {
    h.setSessionReplyStyle.mockResolvedValue(resolved({ reply_style: '', reply_style_effective: 'conversational', reply_style_source: 'agent' }))
    const { result } = renderHook(() => useReplyStyle(() => 's1'))
    await act(async () => { await result.current.pill.onSelect('') })
    expect(h.setSessionReplyStyle).toHaveBeenCalledWith('s1', '')
    expect(result.current.pill).toMatchObject({ choice: '', effective: 'conversational', source: 'agent' })
  })

  it('a failed PATCH is told, and the picker keeps what it showed', async () => {
    h.setSessionReplyStyle.mockRejectedValue(new Error('gateway refused'))
    const { result } = renderHook(() => useReplyStyle(() => 's1'))
    act(() => result.current.restore(resolved({ reply_style: 'terse' })))
    await act(async () => { await result.current.pill.onSelect('default') })
    expect(h.notify).toHaveBeenCalledWith("Couldn't apply this reply style to this session: gateway refused", 'error')
    expect(result.current.pill.choice).toBe('terse')
  })

  it('a pick before the chat exists is held, then persisted when it is created', async () => {
    h.setSessionReplyStyle.mockResolvedValue(resolved())
    const { result } = renderHook(() => useReplyStyle(() => null))
    await act(async () => { await result.current.pill.onSelect('terse') })
    expect(h.setSessionReplyStyle).not.toHaveBeenCalled()
    expect(result.current.pill).toMatchObject({ choice: 'terse', source: '' })
    await act(async () => { await result.current.persistOnCreate('s-new') })
    expect(h.setSessionReplyStyle).toHaveBeenCalledWith('s-new', 'terse')
  })

  it('a chat created with no pick writes nothing', async () => {
    const { result } = renderHook(() => useReplyStyle(() => null))
    await act(async () => { await result.current.persistOnCreate('s-new') })
    expect(h.setSessionReplyStyle).not.toHaveBeenCalled()
  })
})

// ── The chat composer hands the hook's state to the picker ──────────────────────────────────────

/** The attribute names an element's opening tag carries at its own level, so a prop of a nested
 *  element (inside `{…}`) is not counted as one of its own. */
function propsOf(src: string, tag: string): string[] {
  const at = src.indexOf(`<${tag} `)
  if (at < 0) return []
  let depth = 0
  let quote = ''
  let top = ''
  for (let i = at + tag.length + 1; i < src.length; i++) {
    const ch = src[i]
    if (quote) { if (ch === quote) quote = ''; if (depth === 0) top += ' '; continue }
    if (ch === '{') depth++
    else if (ch === '}') depth--
    else if (depth === 0 && (ch === '"' || ch === "'")) quote = ch
    else if (depth === 0 && ch === '>') break
    if (depth === 0 && ch !== '}') top += ch
  }
  return [...top.matchAll(/(?:^|\s)([A-Za-z]\w*)=/g)].map((m) => m[1])
}

describe('the chat composer hands the reply style to the picker', () => {
  const CHAT_COMPOSER = readFileSync(join(process.cwd(), 'src/pages/chat/composer/ChatComposer.tsx'), 'utf8')

  it('ComposerStage is given the hook\'s state, so the chat shows its picker', () => {
    const props = propsOf(CHAT_COMPOSER, 'ComposerStage')
    expect(props, 'ComposerStage moved or lost its props, so this reads nothing').toContain('onSend')
    expect(props).toContain('replyStyle')
  })
})
