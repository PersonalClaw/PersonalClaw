import { describe, it, expect } from 'vitest'
import { render, act } from '@testing-library/react'
import { useRef, useState } from 'react'
import { EditorView } from '@codemirror/view'
import { MarkdownInput, type KeptEditor } from './MarkdownInput'

// The chat page renders its one composer in three places: under the skeleton while a chat's history
// loads, in the empty chat's hero, and under the thread. Moving between them remounts the composer,
// and with it this editor. On a chat opened fresh and typed into at once, the move landed mid-word:
// the new editor took the draft so far but put the caret at its start and was not focused, so the
// keys typed until something focused it were lost and the rest went in front of what was there.
// Measured on a v0.3.9 gateway, typing a 150-character message at 15 ms a key from the moment the
// composer showed: 4 of 6 takes came out as "parse.py and tests/test_parse.py, … what's wrong.The tes",
// and every one of the 4 had its editor replaced; the 2 intact takes kept theirs.
//
// The host keeps what the editor leaves (`keep`), and the editor built in its place takes it: the
// same caret, and focus when it had it. A host whose value changed meanwhile gets its own value, with
// the caret at the end.

/** The editor's view, from the DOM CodeMirror mounts. */
function viewIn(container: HTMLElement): EditorView {
  const content = container.querySelector<HTMLElement>('.cm-content')
  if (!content) throw new Error('no editor mounted')
  const view = EditorView.findFromDOM(content)
  if (!view) throw new Error('no editor view')
  return view
}

/** One key typed at the caret, as the browser's input does. */
function typeKey(view: EditorView, key: string) {
  const at = view.state.selection.main.head
  view.dispatch({ changes: { from: at, insert: key }, selection: { anchor: at + key.length }, userEvent: 'input.type' })
}

let move: (layout: 'loading' | 'thread') => void = () => {}
let setOutside: (v: string) => void = () => {}

function Host({ keepIt }: { keepIt: boolean }) {
  const [value, setValue] = useState('')
  const [layout, setLayout] = useState<'loading' | 'thread'>('loading')
  const keep = useRef<KeptEditor | null>(null)
  move = setLayout
  setOutside = setValue
  const input = (
    <MarkdownInput value={value} onChange={setValue} onSend={() => {}} canSend={false}
      minHeight={40} maxHeight={200} keep={keepIt ? keep : undefined} />
  )
  // Two different places in the tree, as ChatPage's loading and thread branches are: React remounts.
  return layout === 'loading' ? <section data-layout="loading">{input}</section> : <main data-layout="thread">{input}</main>
}

describe('the editor keeps what is typed when the page moves it', () => {
  it('takes the caret and focus the editor before it left, so typing goes on where it was', () => {
    const { container } = render(<Host keepIt />)
    const first = viewIn(container)
    act(() => first.focus())
    act(() => { for (const key of 'The tes') typeKey(first, key) })
    expect(first.state.doc.toString()).toBe('The tes')

    act(() => move('thread'))
    const second = viewIn(container)
    expect(second).not.toBe(first)
    expect(second.state.doc.toString()).toBe('The tes')
    expect(second.state.selection.main.head).toBe(7)
    expect(second.hasFocus).toBe(true)

    act(() => { for (const key of 'ts fail') typeKey(second, key) })
    expect(second.state.doc.toString()).toBe('The tests fail')
  })

  it('puts the caret at the end of a value the host changed while it was away', () => {
    const { container } = render(<Host keepIt />)
    const first = viewIn(container)
    act(() => first.focus())
    act(() => { for (const key of 'Draft') typeKey(first, key) })
    act(() => first.dispatch({ selection: { anchor: 2 } }))
    act(() => { setOutside('Draft, then a follow-up'); move('thread') })
    const second = viewIn(container)
    expect(second.state.doc.toString()).toBe('Draft, then a follow-up')
    expect(second.state.selection.main.head).toBe('Draft, then a follow-up'.length)
  })

  it('without a keep, an editor builds as it always has', () => {
    const { container } = render(<Host keepIt={false} />)
    const first = viewIn(container)
    act(() => { for (const key of 'abc') typeKey(first, key) })
    act(() => move('thread'))
    const second = viewIn(container)
    expect(second.state.doc.toString()).toBe('abc')
    expect(second.state.selection.main.head).toBe(0)
  })
})
