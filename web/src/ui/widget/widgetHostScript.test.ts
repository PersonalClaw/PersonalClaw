/** The CHILD half of the widget bridge: the human-gesture gate, executed.
 *
 *  HOST_SCRIPT runs inside the widget's sandboxed frame, and its `e.isTrusted` check
 *  is the invariant that a widget's OWN script cannot synthesize an action — only a
 *  real human click on a `[data-action]` element may reach the host. Grepping for the
 *  line would not prove it: this file runs the shipped source and asserts the refusal.
 *
 *  jsdom cannot mint a trusted event and `isTrusted` is not redefinable on an instance,
 *  so the fixture captures the click handler the script installs and invokes it with
 *  both flag values. That keeps the refusal non-vacuous: the SAME handler, given
 *  `isTrusted: true`, does post. The untrusted leg is additionally driven through the
 *  real dispatch path (`el.click()`), which is exactly what a widget's script has.
 *
 *  The frame's document is the only place this gate can live, which is also why a host
 *  whose child carries no such gate (the react harness) does not opt into action
 *  forwarding at all — see useWidgetActionBridge.ts. */
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { HOST_SCRIPT_SOURCE } from './widgetSrcdoc'

const posted = vi.fn()
let onClick: (e: { isTrusted: boolean; target: Element; preventDefault: () => void }) => void

beforeAll(() => {
  // The script posts to `parent`; at the jsdom top level that is this window.
  vi.spyOn(window, 'postMessage').mockImplementation(((...args: unknown[]) => { posted(...args) }) as never)
  document.body.innerHTML = `
    <form>
      <input name="range" value="30d">
      <input name="live" type="checkbox" checked>
      <input name="mode" type="radio" value="a">
      <input name="mode" type="radio" value="b" checked>
      <button type="button" id="act" data-action="submit" data-payload='{"from":"widget"}'>Submit</button>
      <button type="button" id="plain">Not an action</button>
    </form>`
  const realAdd = document.addEventListener.bind(document)
  vi.spyOn(document, 'addEventListener').mockImplementation(((type: string, fn: never, ...rest: never[]) => {
    if (type === 'click') onClick = fn as unknown as typeof onClick
    realAdd(type, fn, ...rest)
  }) as never)
  new Function(HOST_SCRIPT_SOURCE)()
  posted.mockClear() // the install-time height report is not under test here
})

beforeEach(() => posted.mockClear())

const el = (id: string) => document.getElementById(id) as Element

function deliver(id: string, isTrusted: boolean) {
  const preventDefault = vi.fn()
  onClick({ isTrusted, target: el(id), preventDefault })
  return preventDefault
}

describe('HOST_SCRIPT — the human-gesture gate', () => {
  it('forwards a human click with its payload and auto-collected form inputs', () => {
    const preventDefault = deliver('act', true)
    expect(posted).toHaveBeenCalledTimes(1)
    expect(posted.mock.calls[0][0]).toEqual({
      type: 'widget-action',
      action: 'submit',
      payload: { from: 'widget', formData: { range: '30d', live: true, mode: 'b' } },
    })
    expect(preventDefault).toHaveBeenCalled()
  })

  it('refuses a click the widget synthesized itself — no action leaves the frame', () => {
    deliver('act', false)
    expect(posted).not.toHaveBeenCalled()
    // …and the same through the real dispatch path, which is all a widget's script has.
    el('act').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
    expect(posted).not.toHaveBeenCalled()
  })

  it('ignores a human click that is not on a [data-action] element', () => {
    deliver('plain', true)
    expect(posted).not.toHaveBeenCalled()
  })
})
