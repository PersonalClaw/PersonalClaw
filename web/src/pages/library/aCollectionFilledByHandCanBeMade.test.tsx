/** A collection you fill by hand can be made.
 *
 *  Driven (dogfood, 11-21): Library › Filter › New collection's second step reads "Search that
 *  fills it (leave empty to add items by hand)", and Create collection with it empty did nothing
 *  on a click and said "Required" on Enter, with no request sent: the prompt took the field as
 *  required, as every prompt does unless told otherwise, so the hand-filled collection the hint
 *  offers could not be made. Now an empty search makes a collection with only its name, and a
 *  search makes a smart one, as before. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { DialogHost } from '../../ui/dialog/DialogHost'
import { closeDialog, subscribeDialogs } from '../../ui/dialog/dialogStore'
import { api } from '../../lib/api'
import { newCollection } from './libraryCollections'

afterEach(() => {
  let open: { id: number }[] = []
  subscribeDialogs((list) => { open = list as typeof open })()
  act(() => { for (const d of open) closeDialog(d.id, null) })
  cleanup()
  vi.restoreAllMocks()
})

async function make(search: string) {
  const create = vi.spyOn(api, 'createKnowledgeCollection').mockResolvedValue({ collection: { id: 'col-1' } } as never)
  render(<DialogHost />)
  let made: string | undefined
  act(() => { void newCollection().then((id) => { made = id }) })
  fireEvent.change(await screen.findByLabelText('Collection name'), { target: { value: 'Trips' } })
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  const query = await screen.findByLabelText('Search that fills it (leave empty to add items by hand)')
  if (search) fireEvent.change(query, { target: { value: search } })
  fireEvent.click(screen.getByRole('button', { name: 'Create collection' }))
  await waitFor(() => expect(made).not.toBeUndefined())
  return { create, made }
}

describe('New collection', () => {
  it('🔴 with the search left empty makes a collection you fill by hand', async () => {
    const { create, made } = await make('')
    expect(create).toHaveBeenCalledWith({ name: 'Trips' })
    expect(made).toBe('col-1')
  })

  it('with a search makes a smart collection', async () => {
    const { create } = await make('rust ownership')
    expect(create).toHaveBeenCalledWith({ name: 'Trips', kind: 'smart', query: 'rust ownership' })
  })
})
