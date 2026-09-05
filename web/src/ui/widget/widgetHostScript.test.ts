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
type SubmitEvent = { isTrusted: boolean; target: Element; submitter?: Element | null; preventDefault: () => void }
let onSubmit: (e: SubmitEvent) => void

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
    if (type === 'submit') onSubmit = fn as unknown as typeof onSubmit
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


// ── A SUBMIT IS NOT ALLOWED TO VANISH (#2263) ────────────────────────────────────────────────
//
// The frame is sandboxed `allow-scripts` with no `allow-forms`, so a real form submit is blocked
// by the browser and reported only to the frame's own console. Field-reported twice, months
// apart: "Hitting Submit didnt work", and the agent fell back to asking the user to copy 200
// lines of text out of the widget. These drive the SHIPPED child script, so what is asserted is
// what the sandbox actually runs.

function submitFrom(formId: string, opts: { isTrusted?: boolean; submitter?: string } = {}) {
  const preventDefault = vi.fn()
  onSubmit({
    isTrusted: opts.isTrusted ?? true,
    target: el(formId),
    submitter: opts.submitter ? el(opts.submitter) : null,
    preventDefault,
  })
  return preventDefault
}

describe('HOST_SCRIPT — a form submit', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <form id="wired">
        <input name="range" value="30d">
        <button type="submit" id="wired-btn" data-action="save" data-payload='{"from":"form"}'>Save</button>
      </form>
      <form id="bare">
        <input name="pick" value="Dune">
        <button type="submit" id="bare-btn">Submit</button>
      </form>`
    posted.mockClear()
  })

  it('SENDS through the documented channel when the form carries an action', () => {
    // 🔑 This also makes Enter-in-a-text-field work: before, only a mouse click on that same
    // button reached the host, and a keyboard submit was swallowed by the sandbox.
    const preventDefault = submitFrom('wired')
    expect(preventDefault).toHaveBeenCalled()
    expect(posted).toHaveBeenCalledTimes(1)
    expect(posted.mock.calls[0][0]).toEqual({
      type: 'widget-action',
      action: 'save',
      payload: { from: 'form', formData: { range: '30d', pick: 'Dune' } },
    })
  })

  it('prefers the SUBMITTER’s action when the form holds more than one', () => {
    document.body.innerHTML = `
      <form id="multi">
        <button type="submit" id="keep" data-action="keep">Keep</button>
        <button type="submit" id="drop" data-action="drop">Drop</button>
      </form>`
    submitFrom('multi', { submitter: 'drop' })
    expect(posted.mock.calls[0][0].action, 'the button pressed decides, not document order').toBe('drop')
  })

  it('REPORTS an error when no action exists anywhere in the form', () => {
    // The measured field case: a plain `<form>` + `<button type="submit">`. Silence taught the
    // user nothing and taught the agent that authored it nothing either.
    const preventDefault = submitFrom('bare')
    expect(preventDefault).toHaveBeenCalled()
    expect(posted).toHaveBeenCalledTimes(1)
    const msg = posted.mock.calls[0][0]
    expect(msg.type).toBe('widget-error')
    expect(msg.message).toMatch(/data-action/)
    expect(msg.message, 'name the sandbox, or the author cannot tell why').toMatch(/sandbox/i)
  })

  it('never posts an action for an untrusted submit', () => {
    // The same gate the click path has: a widget's own script calling form.submit() must not be
    // able to mint a turn.
    submitFrom('wired', { isTrusted: false })
    expect(posted).not.toHaveBeenCalled()
  })

  it('collects form inputs through the SAME payload rule as a click', () => {
    // One rule, two entry points: a second copy for submit is the thing most likely to disagree
    // about what a widget sent.
    submitFrom('wired')
    const viaSubmit = posted.mock.calls[0][0].payload
    posted.mockClear()
    onClick({ isTrusted: true, target: el('wired-btn'), preventDefault: vi.fn() })
    expect(posted.mock.calls[0][0].payload).toEqual(viaSubmit)
  })
})
