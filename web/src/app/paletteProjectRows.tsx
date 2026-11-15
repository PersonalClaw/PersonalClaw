import { useEffect } from 'react'
import { FileText, FolderKanban, FolderOpen, FolderPlus, ListChecks, MessageSquare, Repeat, ScrollText, SquarePen, Target, Upload } from 'lucide-react'
import { api, type ProjectItem } from '../lib/api'
import { useQuery } from '../lib/data'
import { useProjectPins } from '../lib/defaultProject'
import { PROJECT_LIST_KEY } from '../lib/loopProject'
import { projectOrder, projectPulse, pulseWords } from '../lib/projectPulse'
import { useNeedsYou } from '../lib/useNeedsYou'
import { useRunningList } from '../lib/useRunningList'
import type { Command } from './CommandPalette'
import { chatLaunchPath } from './linkState'
import { useOpenProjectPage, type ProjectPageActions } from './projectPageActions'

/** The six rows a project's page offers, each by what it does there, then by its words with no
 *  project: listed dimmed with one reason wherever no project's page is open. */
const PAGE_ROWS: { key: keyof Omit<ProjectPageActions, 'id' | 'name' | 'startChat'>; named: (name: string) => string; anywhere: string; icon: Command['icon']; keywords: string }[] = [
  { key: 'newGoalLoop', named: (n) => `New goal loop in ${n}`, anywhere: 'New goal loop in a project', icon: Target, keywords: 'loop goal start project' },
  { key: 'newTask', named: (n) => `New task in ${n}`, anywhere: 'New task in a project', icon: ListChecks, keywords: 'task todo add project' },
  { key: 'editBrief', named: (n) => `Edit ${n}'s brief`, anywhere: "Edit a project's brief", icon: SquarePen, keywords: 'brief goal scope project' },
  { key: 'editInstructions', named: (n) => `Edit ${n}'s instructions`, anywhere: "Edit a project's instructions", icon: ScrollText, keywords: 'instructions rules agents project' },
  { key: 'openFolder', named: (n) => `Open ${n}'s folder`, anywhere: "Open a project's folder", icon: FolderOpen, keywords: 'folder files workspace project' },
  { key: 'told', named: (n) => `What a new chat in ${n} is told`, anywhere: 'What a new chat in a project is told', icon: FileText, keywords: 'context preamble block told project' },
]

/** Why a page-bound row is dimmed away from a project's page: what to do instead. */
export const OPEN_A_PROJECT = 'Open a project to use this.'

/** The palette's rows for projects: one Go to row per unarchived project, its mark in its icon's
 *  place and its pulse after the "Go to" ("Go to · 2 need you · 2 running", "Go to" alone when
 *  nothing is live or the pulse could not be read); one "Start a chat in <project>" row each (the
 *  open page's focuses its start box); then New project, Import a project and All loops; then the
 *  six rows a project's page offers, named for it while it is open (*page*), else dimmed with no
 *  project and "Open a project to use this.". Pure, for its rail. */
export function projectCommands(
  projects: readonly ProjectItem[],
  pins: readonly string[],
  hintOf: (p: ProjectItem) => string,
  navigate: (path: string) => void,
  page: ProjectPageActions | null = null,
): Command[] {
  const open = projectOrder(projects.filter((p) => p.status !== 'archived'), pins, () => 0)
  return [
    ...open.map((p) => ({
      id: `go:project:${p.id}`, label: p.name, hint: hintOf(p), icon: FolderKanban, mark: p.name,
      keywords: 'project', run: () => navigate(`projects/${p.id}`),
    })),
    ...open.map((p) => ({
      id: `act:chat-in:${p.id}`, label: `Start a chat in ${p.name}`, hint: 'Action', icon: MessageSquare,
      keywords: 'new chat project talk',
      run: page && page.id === p.id ? page.startChat : () => navigate(chatLaunchPath({ project: p.id })),
    })),
    // The dialog and the import's file input live on the Projects list, so these reach it through
    // its address, which it reads once and drops.
    { id: 'act:new-project', label: 'New project', hint: 'Action', icon: FolderPlus, keywords: 'project create make', run: () => navigate('projects?new=1') },
    { id: 'act:import-project', label: 'Import a project', hint: 'Action', icon: Upload, keywords: 'project archive zip restore', run: () => navigate('projects?import=1') },
    { id: 'go:all-loops', label: 'All loops', hint: 'Go to', icon: Repeat, keywords: 'loops history goal code research design', run: () => navigate('loops/history') },
    ...PAGE_ROWS.map((r) => (page
      ? { id: `act:project-page:${r.key}`, label: r.named(page.name), hint: 'Action', icon: r.icon, keywords: r.keywords, run: page[r.key] }
      : { id: `act:project-page:${r.key}`, label: r.anywhere, hint: 'Action', icon: r.icon, keywords: r.keywords, run: () => {}, disabledReason: OPEN_A_PROJECT })),
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
  const page = useOpenProjectPage()
  useEffect(() => {
    if (!Array.isArray(projects)) { if (error) onRows(NO_ROWS); return }
    const waiting = needs.data?.waiting
    const read = !!waiting && !!running.data
    onRows(projectCommands(projects, pins, (p) => {
      const words = read ? pulseWords(projectPulse(p.id, waiting, running.data, [], p)) : []
      return ['Go to', ...words].join(' · ')
    }, navigate, page))
  }, [projects, error, pins, needs.data, running.data, onRows, navigate, page])
  return null
}
