// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { api, type WorkBoard } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { ProjectsSection } from './ProjectsSection'

// ── A project's `…` is a 44 px target on a phone ─────────────────────────────────────────────────
//
// The shell's corner controls became 44 px targets below 1024 px, and the corners grew with them.
// At 768 px the band between them no longer holds a project's header actions, so its All projects
// moved into the `…` (More actions), which the UI matrix measured under 44 px on
// #/projects/<id>?tab=tasks. The `…` carries the compact widths' floor (`TOUCH_TARGET`) from the
// project page, as the Inbox's does.
//
// jsdom has no layout: the header's widths are stubbed so the cluster has no room and folds into
// the `…`, and the classes are pinned, since they are what set the box.

const EMPTY_WORK: WorkBoard = { board: [], sections: [], completeness: 'complete', attention: 0, loadedAt: 0 }

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  resetDataStore()
})

describe("a project's header on a phone", () => {
  it('folds into a `…` that carries the floor', async () => {
    vi.spyOn(api, 'project').mockResolvedValue({ id: 'p-0a1b2c3d', name: 'Launch notes', status: 'active', brief: '', context_dir: '' } as never)
    vi.spyOn(api, 'taskLists').mockResolvedValue([])
    vi.spyOn(api, 'projectWork').mockResolvedValue(EMPTY_WORK)
    vi.spyOn(api, 'personalclawConfig').mockResolvedValue({ legibility: { context_adapters: false } } as never)
    vi.spyOn(api, 'projectSettings').mockResolvedValue({ default_project_id: '' } as never)
    // Every probe wants 400 px and every box offers 60: no tier fits, so the cluster folds.
    vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(400)
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(60)
    render(<ProjectsSection sub="p-0a1b2c3d" navigate={() => {}} navEpoch={0} query={{ tab: 'tasks' }} setQuery={() => {}} />)
    const more = await screen.findByRole('button', { name: 'More actions' })
    const cls = more.className.toString().split(/\s+/)
    expect(cls.includes('max-lg:min-h-11') && cls.includes('max-lg:min-w-11'), more.className).toBe(true)
  })
})
