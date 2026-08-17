/** ONE bridge, ONE way into chat — asserted against the tree, not against intent.
 *
 *  Two dual paths are cheap to reintroduce and expensive to find later: a host that
 *  listens to raw `message` and interprets `widget-action` itself (bypassing the
 *  provenance validator), and a non-chat host that navigates to chat its own way
 *  (bypassing `ne:launch-chat`). Both would look like ordinary local code in review.
 *  This rail names the single owner of each. */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, it, expect } from 'vitest'
import { readWidgetMessage } from './useWidgetActionBridge'

const SRC = join(process.cwd(), 'src')
const BRIDGE = join('ui', 'widget', 'useWidgetActionBridge.ts')

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) { sourceFiles(full, out); continue }
    if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full)
  }
  return out
}

const files = sourceFiles(SRC).map((path) => ({
  rel: path.slice(SRC.length + 1),
  text: readFileSync(path, 'utf8'),
}))

describe('widget bridge: single path', () => {
  it('scanned a real tree', () => {
    // Vacuity floor: a rail that matches nothing reads exactly like a clean rail.
    expect(files.length).toBeGreaterThan(200)
    expect(files.some((f) => f.rel === BRIDGE)).toBe(true)
  })

  it('gives the ne:widget-action event exactly one owner', () => {
    const owners = files.filter((f) => f.text.includes("'ne:widget-action'")).map((f) => f.rel)
    expect(owners).toEqual([BRIDGE])
  })

  it('lets nobody but the bridge interpret a widget-action off a raw message listener', () => {
    const raw = files.filter((f) => f.rel !== BRIDGE && /'widget-action'|"widget-action"/.test(f.text))
    expect(raw.map((f) => f.rel)).toEqual([join('ui', 'widget', 'widgetSrcdoc.ts')]) // the child that POSTS it
  })

  it('routes non-chat hosts through the ONE ne:launch-chat path', () => {
    const bridge = files.find((f) => f.rel === BRIDGE)?.text ?? ''
    expect(bridge).toContain('launchChat')
    // The fallback must not grow its own navigation: a `navigate(`/hash write here
    // would be a second way into chat that no other launcher shares.
    expect(bridge).not.toMatch(/navigate\(|location\.hash\s*=|history\.(push|replace)State/)
    // …and `ne:launch-chat` itself stays owned by the SDK helper the bridge calls.
    const emitters = files.filter((f) => f.text.includes("new CustomEvent('ne:launch-chat'")).map((f) => f.rel)
    expect(emitters).toEqual([join('app', 'appSdk.tsx')])
  })

  it('keeps the chat host a consumer of the hook, never a second listener', () => {
    const chat = files.find((f) => f.rel === join('pages', 'ChatPage.tsx'))?.text ?? ''
    expect(chat).toContain('useWidgetActionBridge(')
    expect(chat).toContain('takePendingWidgetAction()')
    expect(chat).not.toContain("addEventListener('message'")
  })
})

/** The contract as a FIXTURE: one entry per child→parent message. The validator and the
 *  architecture doc are both checked against it, so the table a widget author reads
 *  cannot drift from the code that enforces it. */
const WIRE_CONTRACT = [
  { type: 'widget-height', sample: { type: 'widget-height', height: 120, width: 300 } },
  { type: 'widget-action', sample: { type: 'widget-action', action: 'refresh', payload: { a: 1 } } },
  { type: 'widget-error', sample: { type: 'widget-error', message: 'boom' } },
] as const
const RESERVED_PARENT_PREFIX = '__edit_mode_'

describe('widget wire contract: doc and validator agree', () => {
  const doc = readFileSync(join(process.cwd(), '..', 'docs', 'architecture', 'widgets.md'), 'utf8')

  it('accepts every message in the fixture', () => {
    const frame = document.createElement('iframe')
    document.body.appendChild(frame)
    for (const { sample } of WIRE_CONTRACT) {
      const e = new MessageEvent('message', { data: sample, source: frame.contentWindow })
      expect(readWidgetMessage(e, frame), `${sample.type} must validate`).not.toBeNull()
    }
  })

  it('documents every message, the reserved namespace, and the payload cap', () => {
    for (const { type } of WIRE_CONTRACT) expect(doc).toContain(`\`${type}\``)
    expect(doc).toContain(RESERVED_PARENT_PREFIX)
    expect(doc).toContain('16 KiB')
    expect(doc).toContain('isTrusted')
    expect(doc).toContain('ne:launch-chat')
  })
})
