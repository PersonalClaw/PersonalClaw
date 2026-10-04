import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../lib/api'
import { HistoryDayEditor, type DocDraft } from './MemoryPanel'

// ── Saving History changes the day the owner edited, and no other ──────────────────────────────
//
// History used to open as the recent days read as one text and save that whole text into TODAY's
// file, so one save stored every other day's entries a second time in today's. The editor now lists
// the days, holds one day's file as it is kept, and its Save sends that day alone.

const TODAY = '2026-10-03'
const YESTERDAY = '2026-10-02'
const FILES: Record<string, string> = {
  [`history/${TODAY}`]: `# ${TODAY}\n\n#### 09:30 UTC\nOrdered the kitchen tiles.\n`,
  [`history/${YESTERDAY}`]:
    `# ${YESTERDAY}\n\n#### 09:30 UTC\nPlanned the garden shed.\n\n#### 10:30 UTC\nBought the roofing felt.\n`,
}
const REFUSAL = 'Memory writes are not allowed in this session mode.'
const memoryHistoryDays = vi.fn()
const memoryDoc = vi.fn()
const saveMemoryDoc = vi.fn()

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      memoryHistoryDays: () => memoryHistoryDays(),
      memoryDoc: (w: string) => memoryDoc(w),
      saveMemoryDoc: (w: string, c: string, base: string) => saveMemoryDoc(w, c, base),
    },
  }
})

beforeEach(() => {
  memoryHistoryDays.mockReset().mockResolvedValue({
    today: TODAY, days: [{ date: TODAY, entries: 1 }, { date: YESTERDAY, entries: 2 }],
  })
  memoryDoc.mockReset().mockImplementation((w: string) => Promise.resolve({ value: FILES[w] ?? '', revision: `r-${w}` }))
  saveMemoryDoc.mockReset().mockImplementation((_w: string, c: string) => Promise.resolve({ value: c, revision: 'r-saved' }))
})
afterEach(() => vi.restoreAllMocks())

const picker = () => screen.getByRole('combobox', { name: 'Day of history' }) as HTMLSelectElement
const box = () => screen.getByRole('textbox') as HTMLTextAreaElement
const save = () => userEvent.click(screen.getByRole('button', { name: /save/i }))

function mount(drafts = new Map<string, DocDraft>()) {
  return render(<HistoryDayEditor onSaved={() => {}} drafts={drafts} />)
}

async function openDay(day: string) {
  await userEvent.selectOptions(picker(), day)
  await waitFor(() => expect(box().value).toBe(FILES[`history/${day}`]))
}

describe('the History editor holds one day', () => {
  it('lists each day with its entries and opens the newest day anything is recorded for', async () => {
    mount()
    await waitFor(() => expect(box().value).toBe(FILES[`history/${TODAY}`]))
    expect([...picker().options].map((o) => o.textContent)).toEqual([
      `${TODAY} (today) · 1 entry`,
      `${YESTERDAY} · 2 entries`,
    ])
    expect(memoryDoc).toHaveBeenCalledWith(`history/${TODAY}`)
    expect(screen.getByText('Each day is kept in its own file. Saving changes only the day shown.')).toBeTruthy()
    expect(screen.getByRole('textbox', { name: `History of ${TODAY}` })).toBeTruthy()
  })

  it('saves the day edited, alone, over the copy of it that was read', async () => {
    mount()
    await waitFor(() => expect(box().value).toBe(FILES[`history/${TODAY}`]))
    await openDay(YESTERDAY)
    const edited = FILES[`history/${YESTERDAY}`].replace('the garden shed', 'the greenhouse')
    fireEvent.change(box(), { target: { value: edited } })
    await save()
    await waitFor(() => expect(screen.getByText('Saved ✓')).toBeTruthy())
    expect(saveMemoryDoc.mock.calls).toEqual([[`history/${YESTERDAY}`, edited, `r-history/${YESTERDAY}`]])
    // The list is read again, so the counts beside each day are what is stored now.
    await waitFor(() => expect(memoryHistoryDays).toHaveBeenCalledTimes(2))
    expect(picker().value).toBe(YESTERDAY)
  })

  it('keeps an edit to one day its own across a switch of day', async () => {
    const drafts = new Map<string, DocDraft>()
    mount(drafts)
    await waitFor(() => expect(box().value).toBe(FILES[`history/${TODAY}`]))
    fireEvent.change(box(), { target: { value: FILES[`history/${TODAY}`] + 'A note of my own.\n' } })
    await openDay(YESTERDAY)
    expect(box().value).not.toContain('A note of my own.')
    expect([...picker().options].map((o) => o.textContent)[0]).toBe(`${TODAY} (today) · 1 entry · unsaved changes`)
    await userEvent.selectOptions(picker(), TODAY)
    await waitFor(() => expect(box().value).toContain('A note of my own.'))
    expect(saveMemoryDoc).not.toHaveBeenCalled()
  })

  it('lists today before anything is recorded for it, so its note can be written', async () => {
    memoryHistoryDays.mockResolvedValue({ today: TODAY, days: [{ date: TODAY, entries: 0 }] })
    memoryDoc.mockResolvedValue({ value: '', revision: 'r-empty' })
    mount()
    await waitFor(() => expect(picker().value).toBe(TODAY))
    expect([...picker().options].map((o) => o.textContent)).toEqual([`${TODAY} (today) · no entries`])
    await waitFor(() => expect(box().value).toBe(''))
    fireEvent.change(box(), { target: { value: 'A note of my own.\n' } })
    await save()
    expect(saveMemoryDoc).toHaveBeenCalledWith(`history/${TODAY}`, 'A note of my own.\n', 'r-empty')
  })

  it('shows a refused save in the refusal’s own words, and keeps the edit', async () => {
    saveMemoryDoc.mockRejectedValue(new ApiError(REFUSAL, 403))
    const drafts = new Map<string, DocDraft>()
    mount(drafts)
    await waitFor(() => expect(box().value).toBe(FILES[`history/${TODAY}`]))
    fireEvent.change(box(), { target: { value: FILES[`history/${TODAY}`] + 'A note of my own.\n' } })
    await save()
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe(REFUSAL))
    expect(box().value).toContain('A note of my own.')
    expect(drafts.get(`history/${TODAY}`)?.text).toContain('A note of my own.')
  })
})
