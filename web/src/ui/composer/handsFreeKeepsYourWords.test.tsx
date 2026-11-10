import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'

import { installFakeMicrophone } from '../../test/fakeMicrophone'

// ── Hands-free keeps your words ─────────────────────────────────────────────────────────────
//
// What you typed before you spoke stays in front, and your dictation follows it: a confirmation
// sends both, "cancel" clears only what you said, and a silent piece changes nothing. A draft that
// holds a widget's message is replaced as before, so a spoken turn never carries a widget's words
// as yours.

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: new Proxy(real.api, {
      get: (target, key) => (key === 'dashboardConfig'
        ? () => Promise.resolve({ send_on_enter: true })
        : (target as Record<string | symbol, unknown>)[key]),
    }),
  }
})

const { Composer } = await import('../Composer')

const CONTROLS = { agent: false, model: false, approval: false, reasoning: false, attach: false, mic: true, optimize: false }

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** A host that holds the draft as the chat does: `onReplace` puts the owner's own words in it, and
 *  a sent turn empties it. */
function mount(draft: string, said: string[], opts: { widget?: boolean } = {}) {
  const mic = installFakeMicrophone()
  const sent: string[] = []
  const replaced: string[] = []
  const shown = { value: draft }
  const onTranscribe = vi.fn(async () => said.shift() ?? '')
  function Host() {
    const [value, setValue] = useState(draft)
    const [widget, setWidget] = useState(!!opts.widget)
    shown.value = value
    return (
      <Composer value={value} onChange={setValue} onSend={() => {}} onStop={() => {}} controls={CONTROLS}
        onTranscribe={onTranscribe} onMicError={() => {}}
        onReplace={(v) => { replaced.push(v); setValue(v); setWidget(false) }}
        draftHoldsWidgetMessage={widget}
        onHandsFreeSubmit={(t) => { sent.push(t); setValue('') }}
        handsFree={{ confirmationPhrases: ['go ahead', 'send it'], exitPhrases: ['cancel'] }} />
    )
  }
  render(<Host />)
  return { mic, sent, replaced, shown, onTranscribe }
}

/** Turn hands-free on, then end each of `pieces` pieces, one after the other. */
async function speak(mic: ReturnType<typeof installFakeMicrophone>, onTranscribe: ReturnType<typeof vi.fn>, pieces: number) {
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Hands-free voice/ })) })
  for (let n = 1; n <= pieces; n++) {
    await waitFor(() => expect(mic.recorders.length).toBe(n))
    await act(async () => { mic.recorders[n - 1].stop() })
    await waitFor(() => expect(onTranscribe).toHaveBeenCalledTimes(n))
  }
}

describe('hands-free keeps your words', () => {
  it('keeps a typed draft in front of the dictation, and a confirmation sends both', async () => {
    const { mic, sent, shown, onTranscribe } = mount('Draft the release note.', ['mention the fix', 'go ahead'])
    await speak(mic, onTranscribe, 1)
    await waitFor(() => expect(shown.value).toBe('Draft the release note. mention the fix'))
    await speak2(mic, onTranscribe)
    await waitFor(() => expect(sent).toEqual(['Draft the release note. mention the fix']))
  })

  it('"cancel" clears only what was said', async () => {
    const { mic, sent, shown, onTranscribe } = mount('Draft the note.', ['add the date', 'cancel'])
    await speak(mic, onTranscribe, 1)
    await waitFor(() => expect(shown.value).toBe('Draft the note. add the date'))
    await speak2(mic, onTranscribe)
    await waitFor(() => expect(shown.value).toBe('Draft the note.'))
    expect(sent).toEqual([])
  })

  it('a silent piece changes nothing', async () => {
    const { mic, replaced, shown, onTranscribe } = mount('Draft the note.', [''])
    await speak(mic, onTranscribe, 1)
    await waitFor(() => expect(mic.recorders.length).toBe(2))
    expect(shown.value).toBe('Draft the note.')
    expect(replaced).toEqual([])
  })

  it('sends a typed draft when the confirmation phrase is all that was said', async () => {
    const { mic, sent, onTranscribe } = mount('Draft the note.', ['go ahead'])
    await speak(mic, onTranscribe, 1)
    await waitFor(() => expect(sent).toEqual(['Draft the note.']))
  })

  it('sends nothing for a lone confirmation phrase with nothing typed or said', async () => {
    const { mic, sent, onTranscribe } = mount('', ['go ahead'])
    await speak(mic, onTranscribe, 1)
    await waitFor(() => expect(mic.recorders.length).toBe(2))
    expect(sent).toEqual([])
  })

  it('does not take a sent turn for the draft the next words follow', async () => {
    const { mic, sent, shown } = mount('Draft the note.', ['add the date go ahead', 'next thing'])
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Hands-free voice/ })) })
    await waitFor(() => expect(mic.recorders.length).toBe(1))
    // Both pieces end together, outside act, so React renders the send as a browser does: after
    // both transcripts have folded. The second must not take the sent draft for the owner's.
    mic.recorders[0].stop()
    mic.recorders[1].stop()
    await waitFor(() => expect(sent).toEqual(['Draft the note. add the date']))
    await waitFor(() => expect(shown.value).toBe('next thing'))
  })

  it('replaces a draft holding a widget’s message, and the spoken turn carries none of it', async () => {
    const { mic, sent, shown, onTranscribe } = mount('[UI] refresh', ['check the logs', 'send it'], { widget: true })
    await speak(mic, onTranscribe, 1)
    await waitFor(() => expect(shown.value).toBe('check the logs'))
    await speak2(mic, onTranscribe)
    await waitFor(() => expect(sent).toEqual(['check the logs']))
  })
})

/** End the second piece of a mode already on. */
async function speak2(mic: ReturnType<typeof installFakeMicrophone>, onTranscribe: ReturnType<typeof vi.fn>) {
  await waitFor(() => expect(mic.recorders.length).toBe(2))
  await act(async () => { mic.recorders[1].stop() })
  await waitFor(() => expect(onTranscribe).toHaveBeenCalledTimes(2))
}
