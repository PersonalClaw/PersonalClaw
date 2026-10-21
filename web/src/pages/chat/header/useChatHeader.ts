import { useEffect, useState } from 'react'
import { reportActionFailure, reportingWrite } from '../../../app/reportingWrite'
import { workingDirectoryPrompt, workingDirectoryToast } from '../workingDirectory'
import { confirm, promptInput } from '../../../ui/dialog'
import { notify } from '../../../app/appSdk'
import { api, type ChannelRuntime } from '../../../lib/api'
import { useQuery, invalidateKeys } from '../../../lib/data'
import { copyText } from '../../../app/clipboard'
import type { ChatStream } from '../useChatStream'
import type { useChatComposer } from '../composer/useChatComposer'

// ── The chat header's controller ───────────────────────────────────────────────────────────────
//
// What the header above an open chat keeps and does: the bound project's name for its context
// line, renaming the chat inline and regenerating its title, and the actions in its cluster —
// copy the link and continue on a chat channel (its Share), and save the setup as a starter. Two
// more the composer offers: brief the agent (a row of its `+` menu) and set the working directory
// (its Execution Boundary control). `ChatHeader.tsx` draws the header.

export function useChatHeader({ stream, composer }: {
  stream: Pick<ChatStream, 'sessionRef' | 'title' | 'setTitle' | 'setTitleNeed' | 'setAutoChain' | 'workingDir' | 'setWorkingDir'>
  composer: Pick<ReturnType<typeof useChatComposer>, 'projectId' | 'selection' | 'setToast'>
}) {
  const { sessionRef, title, setTitle, setTitleNeed, setAutoChain, workingDir, setWorkingDir } = stream
  const { projectId, selection, setToast } = composer
  // Name of the project this chat is bound to — shown as a header chip so the user knows
  // the chat is scoped to that project's workspace.
  const [projectName, setProjectName] = useState('')
  useEffect(() => {
    if (!projectId) { setProjectName(''); return }
    let alive = true
    api.project(projectId).then((p) => { if (alive) setProjectName(p?.name || '') }).catch(() => {})
    return () => { alive = false }
  }, [projectId])

  // session title + header actions: rename inline, LLM-regenerate, copy link.
  const [renaming, setRenaming] = useState(false)
  const [renameVal, setRenameVal] = useState('')
  const [regenningTitle, setRegenningTitle] = useState(false)
  // The chat channels this chat can continue on — each connected channel with an owner to reach
  // (the Web UI has none). Not Settings → Providers' `settings:channels` key: that read keeps its
  // catch, so the `[]` it caches on a failure would reach this one as a success. A pairing on either
  // page busts both with `invalidateKeys('settings:channels', true)`. A failed read offers no channel.
  const { data: channelList, error: channelListError } = useQuery('settings:channels-owners', () => api.channels(), { persist: true })
  const handoffChannels = channelListError ? [] : (channelList ?? []).filter((c) => c.owner)

  /** Save the current chat's setup as a reusable starter. Captures the SETUP only —
   *  never the transcript — so sharing or reusing a starter can't leak a conversation. */
  async function saveAsTemplate() {
    const name = await promptInput({
      title: 'Save as starter',
      body: 'Saves this chat\'s agent, model and reasoning effort — not its messages.',
      label: 'Starter name',
      placeholder: 'e.g. Research deep dive',
      confirmLabel: 'Save',
    })
    if (!name) return
    try {
      await api.createSessionTemplate({
        name,
        agent: selection.agent || '',
        model: selection.model && selection.model !== 'Auto' ? selection.model : '',
        reasoning_effort: selection.reasoning || '',
        first_prompt: '',
      })
      invalidateKeys('chat:starters')
      notify(`Saved "${name}" — it'll appear on the new-chat screen.`, 'success')
    } catch (e) {
      notify(`Couldn't save this starter: ${String((e as Error)?.message || e)}`, 'error')
    }
  }

  // ── session title actions ──
  function beginRename() { setRenameVal(title || ''); setRenaming(true) }
  async function commitRename() {
    const s = sessionRef.current
    const v = renameVal.trim()
    setRenaming(false)
    if (!s || !v || v === title) return
    setTitle(v)
    setTitleNeed('')
    // Optimistic, so a swallowed rejection left the header showing a name the server refused — it
    // reverted on the next load with no explanation. Reported, not reverted: this family's remedy for
    // an optimistic write is to TELL, and fighting the header while the user may still be editing is
    // the move `chat/selectionPersistReported` already rejected.
    await api.renameSession(s, v).catch(reportActionFailure('rename this chat'))
    // The header updated optimistically, but the session LIST is a different reader — three of them,
    // in fact (the sidebar, the history page, and the dashboard's recent-sessions). Nothing here
    // busted any of them, so a renamed chat kept its old title everywhere but the header it was
    // renamed from, and on the dashboard that survived a reload (`persist: true`).
    invalidateKeys('chat:sessions', true)
  }
  async function regenTitle() {
    const s = sessionRef.current
    if (!s || regenningTitle) return  // guard against concurrent clicks during the AI call
    setRegenningTitle(true)
    try {
      const r = await api.generateTitle(s).catch(reportActionFailure('regenerate the title'))
      if (r?.title) setTitle(r.title)
      // Nothing was asked: no model is chosen for the chores. The chat keeps its name, and the
      // gateway's sentence says what titles are waiting on and where to choose it.
      if (r?.needs_model) notify(r.needs_model, 'info')
    } finally {
      setRegenningTitle(false)
    }
  }
  async function copyLink() {
    const s = sessionRef.current
    if (!s) return
    const url = `${location.origin}${location.pathname}#/chat/${encodeURIComponent(s)}`
    // Gated, because it was NOT: the catch swallowed the failure and "Copied" was set anyway, so
    // a blocked write left the button claiming a link the clipboard did not hold.
    if (!(await copyText(url, 'the chat link'))) return
    // A toast, like this header's other actions ("Save as starter", a branch): the control can be
    // a row of the `…` menu, which has closed by the time the copy lands, so a confirmation drawn
    // on the control itself would say nothing there.
    notify('Chat link copied.', 'success')
  }
  // Continue this chat on a channel: it opens as a thread in your DM there. A channel that does not
  // know who you are cannot reach you, so it says where to fix that instead of sending into nowhere.
  async function handOff(c: ChannelRuntime) {
    const s = sessionRef.current
    if (!s) return
    if (!c.owner?.id) {
      notify(`${c.display_name} doesn't know who you are yet. Pair its owner in Settings → Providers → ${c.display_name} → Configure, then try again.`, 'error')
      return
    }
    const ok = await confirm({
      title: `Continue on ${c.display_name}?`,
      body: `This chat opens as a thread in your ${c.display_name} direct messages, with its latest message. Reply there to keep going.`,
      confirmLabel: 'Continue there',
    })
    if (!ok) return
    if (await reportingWrite(`continue this chat on ${c.display_name}`, () => api.handoffSession(s, c.name))) {
      notify(`This chat is in your ${c.display_name} messages now.`, 'success')
    }
  }
  // Silently prime the next turn with background context — no visible message, no
  // turn triggered; consumed + prepended on the next user send.
  async function briefAgent() {
    const s = sessionRef.current
    if (!s) return
    const content = await promptInput({
      title: 'Brief the agent', type: 'textarea',
      label: 'Background context to prime the next reply (not shown in the transcript)',
      placeholder: 'Paste a spec, reference, or correction…', confirmLabel: 'Add context',
    })
    if (!content?.trim()) return
    try { await api.briefSession(s, content.trim()); setToast('Context added — it primes your next message.') }
    catch (e) { setToast((e as Error).message || 'Failed to add context') }
    window.setTimeout(() => setToast(null), 2600)
  }
  // Set the live session's working directory (agent cwd + memory-partition scope), or clear it.
  async function setWorkspaceDir() {
    const s = sessionRef.current
    if (!s) return
    const dir = await promptInput(workingDirectoryPrompt(workingDir))
    if (dir == null) return
    let shownFor = 2600
    try {
      const r = await api.setSessionWorkspaceDir(s, dir.trim())
      if (typeof r.auto_chain === 'string') setAutoChain(r.auto_chain)
      if (typeof r.workspace_dir === 'string') setWorkingDir(r.workspace_dir)
      setToast(workingDirectoryToast(r, !dir.trim()))
      if (r.moved) shownFor = 6000
    }
    catch (e) { setToast((e as Error).message || 'Failed to set working directory') }
    window.setTimeout(() => setToast(null), shownFor)
  }
  return {
    projectName, renaming, setRenaming, renameVal, setRenameVal, regenningTitle, handoffChannels,
    saveAsTemplate, beginRename, commitRename, regenTitle, copyLink, handOff, briefAgent, setWorkspaceDir,
  }
}

/** What the header's view reads from its controller. */
export type ChatHeaderState = ReturnType<typeof useChatHeader>
