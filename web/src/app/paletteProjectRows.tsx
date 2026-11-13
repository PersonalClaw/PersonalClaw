import { useEffect } from 'react'
import { FolderKanban, FolderPlus, MessageSquare, Repeat, Upload } from 'lucide-react'
import { api, type ProjectItem } from '../lib/api'
import { useQuery } from '../lib/data'
import { useProjectPins } from '../lib/defaultProject'
import { PROJECT_LIST_KEY } from '../lib/loopProject'
import { projectOrder, projectPulse, pulseWords } from '../lib/projectPulse'
import { useNeedsYou } from '../lib/useNeedsYou'
import { useRunningList } from '../lib/useRunningList'
import type { Command } from './CommandPalette'

/** The palette's rows for projects: one Go to row per unarchived project, its mark in its icon's
 *  place and its pulse after the "Go to" ("Go to · 2 need you · 2 running", "Go to" alone when
 *  nothing is live or the pulse could not be read); one "Start a chat in <project>" row each; then
 *  New project, Import a project and All loops. Pure, for its rail. */
export function projectCommands(
  projects: readonly ProjectItem[],
  pins: readonly string[],
  hintOf: (p: ProjectItem) => string,
  navigate: (path: string) => void,
): Command[] {
  const open = projectOrder(projects.filter((p) => p.status !== 'archived'), pins, () => 0)
  return [
    ...open.map((p) => ({
      id: `go:project:${p.id}`, label: p.name, hint: hintOf(p), icon: FolderKanban, mark: p.name,
      keywords: 'project', run: () => navigate(`projects/${p.id}`),
    })),
    ...open.map((p) => ({
      id: `act:chat-in:${p.id}`, label: `Start a chat in ${p.name}`, hint: 'Action', icon: MessageSquare,
      keywords: 'new chat project talk', run: () => navigate(`chat/new?project=${encodeURIComponent(p.id)}`),
    })),
    // The dialog and the import's file input live on the Projects list, so these reach it through
    // its address, which it reads once and drops.
    { id: 'act:new-project', label: 'New project', hint: 'Action', icon: FolderPlus, keywords: 'project create make', run: () => navigate('projects?new=1') },
    { id: 'act:import-project', label: 'Import a project', hint: 'Action', icon: Upload, keywords: 'project archive zip restore', run: () => navigate('projects?import=1') },
    { id: 'go:all-loops', label: 'All loops', hint: 'Go to', icon: Repeat, keywords: 'loops history goal code research design', run: () => navigate('loops/history') },
  ]
}

/** The rows a list that could not be read hands up: one array, so the palette's state is set once
 *  for it, not on each render. */
const NO_ROWS: Command[] = []

/** Reads what the project rows say, only while the palette is open, and hands them up. Draws
 *  nothing. It reads Home's own keys, so an open palette over Home reads nothing twice. */
export function PaletteProjectRows({ onRows, navigate }: { onRows: (rows: Command[]) => void; navigate: (path: string) => void }) {
  // A list that could not be read lists no project rows: the palette's other rows still open.
  const { data: projects, error } = useQuery<ProjectItem[]>(PROJECT_LIST_KEY, async () => api.projects(), { persist: true })
  const { pins } = useProjectPins()
  const needs = useNeedsYou()
  const running = useRunningList()
  useEffect(() => {
    if (!Array.isArray(projects)) { if (error) onRows(NO_ROWS); return }
    const waiting = needs.data?.waiting
    const read = !!waiting && !!running.data
    onRows(projectCommands(projects, pins, (p) => {
      const words = read ? pulseWords(projectPulse(p.id, waiting, running.data, [], p)) : []
      return ['Go to', ...words].join(' · ')
    }, navigate))
  }, [projects, error, pins, needs.data, running.data, onRows, navigate])
  return null
}
