// @module-tag tree-scan
/** Only a press on a control the page draws may send a widget action at once (`pressed: true`).
 *
 *  The bridge sends an action marked `pressed` as the next turn and puts every other in front of
 *  the owner unsent (useWidgetActionBridge.ts). So the mark is the authority, and this rail holds
 *  where it may be set: the genui router, which runs from a genui Button's click or a Form's
 *  submit, and the correction rail's Send, both in the page's own DOM, where no frame script can
 *  press. The wire a frame's posts arrive on sets none, whatever a frame sends.
 *
 *  The census is read through the one tree reader, and its vacuity leg shows it can see a site. */
import { describe, it, expect } from 'vitest'
import { join, relative } from 'node:path'
import { filesUnder, readSource } from '../../test/sourceTree'

const SRC = join(process.cwd(), 'src')

/** The two places a press in the page publishes from. */
const PRESSED_IN_THE_PAGE = ['src/ui/genui/actions.ts', 'src/ui/widget/useArtifactIteration.ts']

/** `pressed` set to anything that may be truthy: a literal, a variable, an expression, the
 *  shorthand `{ pressed }`, a quoted key, or an assignment to it. Only `false` and `undefined`
 *  set it off. */
const PRESSED = new RegExp([
  // An object key is a name or a quoted string (a template literal is no key).
  /(?:\bpressed|(?<key>['"])pressed\k<key>)\s*:\s*(?!false\b|undefined\b)[^\s,}]/.source,
  /[{,]\s*pressed\s*[,}]/.source,
  /(?:\.pressed\b|\[\s*(?<index>['"`])pressed\k<index>\s*\])\s*=(?!=)\s*(?!false\b|undefined\b)\S/.source,
].join('|'))

/** The one place the mark is READ, not set: the dispatcher taking it off the event the publisher
 *  raised (`dispatchToConsumer`), kept to exactly that. */
const READS_THE_MARK = 'pressed: detail?.pressed === true,'

/** A file that publishes a widget action: it names the one publisher (a call, or an import under
 *  any name), or its event. */
const PUBLISHES = /\bpublishWidgetAction\b|WIDGET_ACTION_EVENT/

/** Whether *source* sets the mark anywhere. */
function setsPressed(source: string): boolean {
  return PRESSED.test(source.split(READS_THE_MARK).join(''))
}

function sources(): string[] {
  return filesUnder(SRC, (name) => /\.(ts|tsx)$/.test(name) && !/\.(test|doc)\.(ts|tsx)$/.test(name))
}

describe('a widget action is sent at once only from a press in the page', () => {
  it('sets `pressed` in the genui router and the correction rail, and nowhere else that publishes', () => {
    const where = sources()
      .filter((path) => { const src = readSource(path); return PUBLISHES.test(src) && setsPressed(src) })
      .map((path) => relative(process.cwd(), path))
      .sort()
    expect(where).toEqual(PRESSED_IN_THE_PAGE)
  })

  it('sets no `pressed` on the wire a frame posts on', () => {
    const bridge = readSource(join(SRC, 'ui/widget/useWidgetActionBridge.ts'))
    const wire = bridge.slice(bridge.indexOf('export function useWidgetWire('), bridge.indexOf('function dropped('))
    expect(wire, 'the wire the census reads').toContain('publishWidgetAction(')
    expect(wire).not.toMatch(/pressed\s*:/)
  })

  it('VACUITY: the census sees a site it is shown, in each shape it counts', () => {
    expect(setsPressed("publishWidgetAction(text, { label, pressed: true })")).toBe(true)
    expect(setsPressed('publishWidgetAction(text, { pressed: !!fromClick })')).toBe(true)
    expect(setsPressed('publishWidgetAction(text, { pressed: yes })')).toBe(true)
    expect(setsPressed('publishWidgetAction(text, { label, pressed })')).toBe(true)
    expect(setsPressed('publishWidgetAction(text, { label })')).toBe(false)
    expect(setsPressed('publishWidgetAction(text, { label, pressed: false })')).toBe(false)
    expect(setsPressed(`const meta = {\n    ${READS_THE_MARK}\n  }`)).toBe(false)
    // A quoted key, an assignment, and the publisher imported under another name.
    expect(setsPressed("publishWidgetAction(text, { 'pressed': true })")).toBe(true)
    expect(setsPressed('publishWidgetAction(text, { "pressed": 1 })')).toBe(true)
    expect(setsPressed('meta.pressed = true')).toBe(true)
    expect(setsPressed("meta['pressed'] = fromClick")).toBe(true)
    expect(setsPressed('meta.pressed = false')).toBe(false)
    expect(setsPressed('if (meta.pressed === true) send()')).toBe(false)
    expect(setsPressed('pressedUnarmed.current = true')).toBe(false)
    const aliased = "import { publishWidgetAction as raise } from './actionTurn'\nraise(text, { pressed: true })"
    expect(PUBLISHES.test(aliased) && setsPressed(aliased)).toBe(true)
    expect(readSource(join(SRC, 'ui/widget/useWidgetActionBridge.ts')), 'the read it skips is still there').toContain(READS_THE_MARK)
  })
})
