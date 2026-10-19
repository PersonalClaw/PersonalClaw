import { describe, expect, it } from 'vitest'
import { linkStatePatch, showsNewThread, withLinkState } from './linkState'

// A `personalclaw://` link (and a copied address) names chat state in the link's own words:
// `prompt` for a new thread's draft and `pane=<name>` for the pane it opens. The chat reads its own
// keys, `seed` and each pane's flag. `linkStatePatch` is the one place the first is written as the
// second, as a replace of the address, so the chat then owns the state as if the person had opened
// it. Other pages own their keys, `prompt` and `pane` included, and are never rewritten.

describe('linkStatePatch: a prompt', () => {
  it("becomes the new thread's draft", () => {
    expect(linkStatePatch('chat', 'new', { prompt: 'draft this' })).toEqual({ prompt: null, seed: 'draft this' })
  })

  it("becomes the draft of a new thread bound to a project, and the project stays", () => {
    expect(linkStatePatch('chat', 'new', { project: 'p-1', prompt: 'hi' })).toEqual({ prompt: null, seed: 'hi' })
  })

  it('fills no existing thread and no bare chat route: the key is dropped', () => {
    for (const sub of ['web:abc', '', 'history', 'room/r1']) {
      expect(linkStatePatch('chat', sub, { prompt: 'hi' }), `chat/${sub}`).toEqual({ prompt: null })
    }
  })

  it('never replaces a draft the address already names', () => {
    expect(linkStatePatch('chat', 'new', { seed: 'mine', prompt: 'theirs' })).toEqual({ prompt: null })
  })

  it('drops an empty prompt and writes no draft for it', () => {
    expect(linkStatePatch('chat', 'new', { prompt: '' })).toEqual({ prompt: null })
  })
})

describe('linkStatePatch: a pane', () => {
  it("opens a thread's Activity pane", () => {
    expect(linkStatePatch('chat', 'web:abc', { pane: 'activity' })).toEqual({ pane: null, activity: '1' })
  })

  it("opens a new thread's Chat history pane, on chat/new and on the bare chat route alike", () => {
    expect(linkStatePatch('chat', 'new', { pane: 'history' })).toEqual({ pane: null, history: '1' })
    expect(linkStatePatch('chat', '', { pane: 'history' })).toEqual({ pane: null, history: '1' })
  })

  it('drops a pane the chat does not have', () => {
    expect(linkStatePatch('chat', 'web:abc', { pane: 'review' })).toEqual({ pane: null })
    expect(linkStatePatch('chat', 'web:abc', { pane: 'result' })).toEqual({ pane: null })
    expect(linkStatePatch('chat', 'new', { pane: '' })).toEqual({ pane: null })
  })

  it("drops a pane the page it names cannot show: Activity on a new thread, history on a thread", () => {
    expect(linkStatePatch('chat', 'new', { pane: 'activity' })).toEqual({ pane: null })
    expect(linkStatePatch('chat', '', { pane: 'activity' })).toEqual({ pane: null })
    expect(linkStatePatch('chat', 'web:abc', { pane: 'history' })).toEqual({ pane: null })
    expect(linkStatePatch('chat', 'history', { pane: 'activity' })).toEqual({ pane: null })
    expect(linkStatePatch('chat', 'room/r1', { pane: 'activity' })).toEqual({ pane: null })
  })

  it('writes nothing for a flag already on', () => {
    expect(linkStatePatch('chat', 'web:abc', { pane: 'activity', activity: '1' })).toEqual({ pane: null })
  })

  it('turns the flag on when the address also holds it off: the pane is what the link asks for', () => {
    expect(linkStatePatch('chat', 'web:abc', { pane: 'activity', activity: '0' })).toEqual({ pane: null, activity: '1' })
  })
})

describe("linkStatePatch: another page's own keys", () => {
  it('leaves every page but the chat alone, whatever it holds', () => {
    // The loop cockpit keeps its Prompt card's expander in the address as `?prompt=1`.
    expect(linkStatePatch('loops', 'g1', { prompt: '1' })).toBeNull()
    for (const [route, sub] of [['settings', ''], ['loop', 'new'], ['dashboard', ''], ['code', 'p-1']]) {
      expect(linkStatePatch(route, sub, { prompt: 'hi', pane: 'activity' }), `${route}/${sub}`).toBeNull()
      expect(linkStatePatch(route, sub, { pane: 'history' }), `${route}/${sub}`).toBeNull()
    }
  })
})

describe('linkStatePatch: everything else', () => {
  it('has nothing to write for an address without link keys, so nothing is replaced', () => {
    expect(linkStatePatch('chat', 'new', {})).toBeNull()
    expect(linkStatePatch('chat', 'new', { seed: 'x', activity: '1', file: 'a.py' })).toBeNull()
  })

  it('writes no key that acts, whatever the address holds', () => {
    const patch = linkStatePatch('chat', 'new', { prompt: 'hi', pane: 'history', send: '1', approve: '1', run: '1' })
    expect(patch).toEqual({ prompt: null, seed: 'hi', pane: null, history: '1' })
  })
})

describe('withLinkState and showsNewThread', () => {
  it('applies a patch as the router writes one: a null drops the key, a value sets it', () => {
    expect(withLinkState({ prompt: 'hi', project: 'p-1' }, { prompt: null, seed: 'hi' })).toEqual({ project: 'p-1', seed: 'hi' })
    expect(withLinkState({ pane: 'activity' }, { pane: null, activity: '1' })).toEqual({ activity: '1' })
  })

  it('names the pages that are a new thread: chat/new and the bare chat route, nothing else', () => {
    expect(showsNewThread('chat', 'new')).toBe(true)
    expect(showsNewThread('chat', '')).toBe(true)
    for (const [route, sub] of [['chat', 'web:abc'], ['chat', 'history'], ['chat', 'room/r1'], ['dashboard', ''], ['loop', 'new']]) {
      expect(showsNewThread(route, sub), `${route}/${sub}`).toBe(false)
    }
  })
})
