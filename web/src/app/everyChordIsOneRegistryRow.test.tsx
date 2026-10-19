// @module-tag tree-scan
/** One keymap holds every shortcut, and every surface that shows a key reads it from there.
 *
 *  - Every chord is one row's, and only one row's, focus mode's and reading mode's included.
 *  - No file under `web/src` outside the keymap reads `metaKey`/`ctrlKey`, except the ones listed
 *    below, which still do. The list only shrinks: a file that stops reading them must leave it,
 *    and a new file that reads them fails here.
 *  - The one handler runs a held key once, and a desktop menu runs only the ids it may send.
 *  - The palette lists every row with its keys, once; a row the focused control handles (an
 *    approval's ⌘Enter) is listed greyed out with its reason and never runs from the palette. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { join, relative } from 'node:path'
import { Terminal } from 'lucide-react'
import { COMMAND_EVENT, KEYMAP, bindingLabel, chordLabel, matchChord, useKeyBinding, type CommandId } from './keymap'
import { CommandPalette, keymapCommands } from './CommandPalette'
import { filesUnder, readSource } from '../test/sourceTree'

afterEach(cleanup)

const SRC = join(__dirname, '..')

/** The files that still read the modifiers themselves. Remove a line when its file moves onto the
 *  keymap; never add one. The code cockpit's two are one file split in two: its editor hooks still
 *  read the keys the old page did. */
const STILL_READ_MODIFIERS = [
  'lib/pushToTalk.ts',
  'pages/chat/ChatFilePanel.tsx',
  'pages/chat/PromptPalette.tsx',
  'pages/chat/RoomView.tsx',
  'pages/chat/composer/useChatComposer.ts',
  'pages/chat/transcript/UserEditor.tsx',
  'pages/chat/transcript/useTranscript.ts',
  'pages/code/cockpit/useCenterEditor.ts',
  'pages/code/cockpit/useCodeCockpit.ts',
  'pages/dashboard/widgets/ActiveWork.tsx',
  'pages/files/FilesSection.tsx',
  'pages/knowledge/KnowledgeCreatePage.tsx',
  'pages/knowledge/ReadingView.tsx',
  'pages/loops/LoopCockpitPage.tsx',
  'pages/tasks/TaskDetail.tsx',
  'ui/PlanningWalkthrough.tsx',
  'ui/chat/ChatPlanGate.tsx',
]

const sources = (dir: string): string[] => filesUnder(dir, (n) => /\.tsx?$/.test(n) && !/\.test\./.test(n))

describe('the keymap', () => {
  it('gives every chord to exactly one row', () => {
    const chords = KEYMAP.flatMap((b) => b.chords)
    const twice = chords.filter((c, i) => chords.indexOf(c) !== i)
    expect(twice).toEqual([])
    expect(new Set(KEYMAP.map((b) => b.id)).size).toBe(KEYMAP.length)
  })

  it('is the only place outside the listed files that reads the modifier keys', () => {
    const reading = sources(SRC)
      .filter((p) => /\b(metaKey|ctrlKey)\b/.test(readSource(p)))
      .map((p) => relative(SRC, p).split('\\').join('/'))
      .filter((p) => p !== 'app/keymap.ts')
      .sort()
    expect(reading).toEqual([...STILL_READ_MODIFIERS].sort())
    expect(STILL_READ_MODIFIERS.length).toBeLessThanOrEqual(17)
  })

  it('matches by the physical key, so ⌥ and other layouts still match', () => {
    const ev = (over: Partial<KeyboardEvent>) => ({ key: '', code: '', metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, ...over })
    expect(matchChord(ev({ key: 'å', code: 'KeyA', metaKey: true, altKey: true }), 'mod+alt+a')).toBe(true)
    expect(matchChord(ev({ key: 'a', code: 'KeyA', metaKey: true }), 'mod+alt+a')).toBe(false)
    expect(matchChord(ev({ key: 'P', code: 'KeyP', ctrlKey: true, shiftKey: true }), 'mod+shift+p')).toBe(true)
    expect(matchChord(ev({ key: 'Enter', code: 'Enter' }), 'mod+enter')).toBe(false)
    expect(chordLabel('mod+shift+p')).toMatch(/⌘⇧P|Ctrl\+Shift\+P/)
    // Punctuation by its physical key too: shifted, `.` types `>`, and AltGr never runs one.
    expect(matchChord(ev({ key: '>', code: 'Period', ctrlKey: true, shiftKey: true }), 'mod+shift+.')).toBe(true)
    expect(matchChord(ev({ key: '\\', code: 'Backslash', metaKey: true }), 'mod+\\')).toBe(true)
    expect(matchChord(ev({ key: '\\', code: 'Backslash', ctrlKey: true, altKey: true }), 'mod+\\')).toBe(false)
  })

  it('holds focus mode\'s and reading mode\'s keys as rows of its own', () => {
    const chordsOf = (id: string) => KEYMAP.find((b) => b.id === id)?.chords
    expect(chordsOf('focus-mode')).toEqual(['mod+\\'])
    expect(chordsOf('reading-mode')).toEqual(['mod+shift+.'])
    expect(KEYMAP.filter((b) => b.id === 'focus-mode' || b.id === 'reading-mode').map((b) => b.scope)).toEqual(['global', 'global'])
  })
})

/** Mounts runners for *ids*, each counting its runs. */
function Runners({ runs }: { runs: Partial<Record<CommandId, () => void>> }) {
  useKeyBinding('palette', runs.palette)
  useKeyBinding('new-chat', runs['new-chat'])
  useKeyBinding('sidebar', runs.sidebar)
  return null
}

describe('the one handler', () => {
  it('runs a held key once', () => {
    const sidebar = vi.fn()
    render(<Runners runs={{ sidebar }} />)
    const press = (repeat: boolean) => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'b', code: 'KeyB', ctrlKey: true, repeat, cancelable: true }))
    act(() => { press(false); press(true); press(true) })
    expect(sidebar).toHaveBeenCalledTimes(1)
  })

  it('leaves a key with no runner to the browser', () => {
    render(<Runners runs={{}} />)
    const ev = new KeyboardEvent('keydown', { key: 'b', code: 'KeyB', ctrlKey: true, cancelable: true })
    act(() => { window.dispatchEvent(ev) })
    expect(ev.defaultPrevented).toBe(false)
  })

  it('runs from a desktop menu only the ids a menu may send', () => {
    const runs = { palette: vi.fn(), 'new-chat': vi.fn() }
    render(<Runners runs={runs} />)
    const send = (detail: unknown) => window.dispatchEvent(new CustomEvent(COMMAND_EVENT, { detail }))
    act(() => { send('palette'); send('approve-focused'); send('restore-last-prompt'); send({ id: 'new-chat' }) })
    expect(runs.palette).not.toHaveBeenCalled()
    expect(runs['new-chat']).not.toHaveBeenCalled()
    act(() => { send('new-chat') })
    expect(runs['new-chat']).toHaveBeenCalledTimes(1)
  })
})

describe('the palette lists every row', () => {
  it('shows each row with its keys, and runs none of the focused ones', async () => {
    const runs = { palette: vi.fn(), sidebar: vi.fn() }
    const commands = [
      { id: 'act:terminal-drawer', label: 'Toggle terminal drawer', hint: 'Action', icon: Terminal, run: vi.fn() },
      ...keymapCommands({ sidebar: runs.sidebar }),
    ]
    render(<CommandPalette commands={commands} navigate={vi.fn()} />)
    act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', code: 'KeyK', metaKey: true })) })
    for (const row of KEYMAP) {
      const option = screen.getByRole('option', { name: new RegExp(row.label) })
      expect(option.textContent).toContain(bindingLabel(row.id))
      if (row.scope === 'focused') {
        expect(option.getAttribute('aria-disabled')).toBe('true')
        expect(option.textContent).toContain(row.disabledReason)
      }
    }
    // Each row once: a key is written down in one place, so it is listed in one place.
    const labels = screen.getAllByRole('option').map((o) => o.textContent)
    for (const row of KEYMAP) expect(labels.filter((t) => t?.includes(row.label)), row.id).toHaveLength(1)
    fireEvent.click(screen.getByRole('option', { name: /Approve the focused request/ }))
    // Still open once any exit animation would have run: nothing ran.
    await act(() => new Promise((r) => setTimeout(r, 400)))
    expect(screen.getByRole('listbox')).toBeTruthy()
    fireEvent.click(screen.getByRole('option', { name: /Show or hide the sidebar/ }))
    expect(runs.sidebar).toHaveBeenCalledTimes(1)
  })
})
