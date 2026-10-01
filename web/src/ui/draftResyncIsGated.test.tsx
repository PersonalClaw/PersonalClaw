// @module-tag tree-scan
import { describe, it, expect } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { useEffect, useLayoutEffect, useState } from 'react'
import { join } from 'node:path'
import { useSyncedDraft } from './forms'
import { filesUnder, readSource } from '../test/sourceTree'

// ── A draft of a stored value is re-seeded when the value moves — never on mount ────────────────
//
// `evalsRoundTrip`'s benchmark row flaked once under load: its Save sent nothing. The row kept a
// draft of the stored ref (`useState(stored)`) and re-seeded it with `useEffect(() =>
// setDraft(stored), [stored])`. That effect's MOUNT run writes the initial value back, which is
// harmless — unless an edit lands between the mount commit and the effect's flush, a window React's
// scheduler leaves open whenever a panel's load resolves outside `act`. Then the stale initial value
// overwrites the edit, and a Save that compares the draft with the stored value sees nothing to send.
// `NumberField` had the same flake and its own fix; ten other controls had the same effect.
//
// The window is opened here deterministically: a layout effect edits the draft, which is exactly an
// update that lands after the mount commit and before the passive effects flush.

function useUngatedDraft(value: string) {
  const [draft, setDraft] = useState(value)
  useEffect(() => { setDraft(value) }, [value])
  return [draft, setDraft] as const
}

function Gated({ value }: { value: string }) {
  const [draft, setDraft] = useSyncedDraft(value)
  useLayoutEffect(() => { setDraft('typed before the first flush') }, [setDraft])
  return <output>{draft}</output>
}

function Ungated({ value }: { value: string }) {
  const [draft, setDraft] = useUngatedDraft(value)
  useLayoutEffect(() => { setDraft('typed before the first flush') }, [setDraft])
  return <output>{draft}</output>
}

describe('an edit that lands before the first flush', () => {
  it('🔴 survives a synced draft', () => {
    render(<Gated value="stored" />)
    expect(screen.getByRole('status').textContent).toBe('typed before the first flush')
  })

  it('is what the ungated effect overwrote — the control that shows the window is really open', () => {
    render(<Ungated value="stored" />)
    expect(screen.getByRole('status').textContent).toBe('stored')
  })
})

function Echo({ value, which }: { value: string; which?: string }) {
  const [draft, setDraft] = useSyncedDraft(value, which ?? value)
  return (
    <>
      <output>{draft}</output>
      <button type="button" onClick={() => setDraft('edited')}>edit</button>
    </>
  )
}

describe('the draft follows its value', () => {
  it('re-seeds when the value moves after mount — a save, a rollback, another tab', () => {
    const { rerender } = render(<Echo value="a" />)
    fireEvent.click(screen.getByRole('button', { name: 'edit' }))
    expect(screen.getByRole('status').textContent).toBe('edited')
    rerender(<Echo value="b" />)
    expect(screen.getByRole('status').textContent).toBe('b')
  })

  it('with a key, re-seeds when the thing edited is another one, from the value it has then', () => {
    const { rerender } = render(<Echo value="first draft" which="item-1" />)
    fireEvent.click(screen.getByRole('button', { name: 'edit' }))
    rerender(<Echo value="first draft, revised" which="item-1" />)
    expect(screen.getByRole('status').textContent, 'the same item: the edit stands').toBe('edited')
    rerender(<Echo value="second item's draft" which="item-2" />)
    expect(screen.getByRole('status').textContent).toBe("second item's draft")
  })
})

// ── No control re-seeds its own draft with its own effect ────────────────────────────────────────

/** Drafts seeded from an expression and re-seeded from THE SAME expression by an effect of their own
 *  — `useState(x)` + `useEffect(() => setDraft(x), …)`. A reset to a literal (`setOpen(false)` on a
 *  new session) is not a draft of a value and is not counted, and neither is prose: both halves are
 *  read only where a statement starts, so a comment that quotes the pattern (the hook's own doc
 *  does) is not an offender. */
function selfReseededDrafts(src: string): string[] {
  const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const out: string[] = []
  const seeds = /^[ \t]*const \[(\w+), (set\w+)\] = useState(?:<[^>\n]*>)?\((.+)\)\s*$/gm
  for (const m of src.matchAll(seeds)) {
    const [, name, setter, init] = m
    if (/^(?:true|false|null|undefined|''|""|\[\]|\{\}|-?\d+(?:\.\d+)?)$/.test(init.trim())) continue
    const reseed = new RegExp(`^[ \\t]*useEffect\\(\\(\\) => \\{?\\s*${setter}\\(${escape(init.trim())}\\)`, 'm')
    if (reseed.test(src)) out.push(name)
  }
  return out
}

function sources(dir: string): string[] {
  return filesUnder(dir, (n) => /\.tsx?$/.test(n) && !/\.test\.tsx?$/.test(n))
}

describe('the census', () => {
  it('finds the pattern it is looking for, and not the hook', () => {
    expect(selfReseededDrafts([
      'const [draft, setDraft] = useState(stored)',
      'useEffect(() => { setDraft(stored) }, [stored])',
    ].join('\n'))).toEqual(['draft'])
    expect(selfReseededDrafts([
      "const [pathDraft, setPathDraft] = useState(settings.vault_path ?? '')",
      "useEffect(() => { setPathDraft(settings.vault_path ?? '') }, [settings.vault_path])",
    ].join('\n'))).toEqual(['pathDraft'])
    expect(selfReseededDrafts('const [draft, setDraft] = useSyncedDraft(stored)')).toEqual([])
    expect(selfReseededDrafts([
      'const [findOpen, setFindOpen] = useState(false)',
      'useEffect(() => { setFindOpen(false) }, [sessionId])',
    ].join('\n')), 'a reset to a literal is not a draft').toEqual([])
    expect(selfReseededDrafts([
      ' *  so the mount run of the usual `useEffect(() => setDraft(value), [value])` writes it back',
      '  const [draft, setDraft] = useState<T>(value)',
    ].join('\n')), 'a comment that quotes the pattern is not the pattern').toEqual([])
    expect(selfReseededDrafts([
      '  const [draft, setDraft] = useState<T>(value)',
      '  useEffect(() => setDraft(value), [value])',
    ].join('\n')), 'and the same lines as code are').toEqual(['draft'])
  })

  it('🔴 leaves no control re-seeding its own draft', () => {
    const files = sources(join(process.cwd(), 'src'))
    expect(files.length, 'vacuity floor: the walk must reach the source tree').toBeGreaterThan(200)
    const adopters = files.filter((f) => readSource(f).includes('useSyncedDraft(')).length
    expect(adopters, 'and the controls that use the hook').toBeGreaterThanOrEqual(10)
    const offenders = files.flatMap((f) =>
      selfReseededDrafts(readSource(f)).map((name) => `${f.slice(process.cwd().length + 1)}: ${name}`))
    expect(offenders, 'use `useSyncedDraft` — its mount run can overwrite an edit').toEqual([])
  })
})
