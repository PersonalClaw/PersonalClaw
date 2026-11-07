import { describe, it, expect } from 'vitest'
import { lineCount } from './WidgetActionAsk'

// The ask says how much a widget's message holds, "N characters · M lines": the lines it shows as.
describe('the ask counts the lines a message shows as', () => {
  it('counts a final line break as the end of the last line, not a line of its own', () => {
    expect(lineCount('one')).toBe(1)
    expect(lineCount('one\n')).toBe(1)
    expect(lineCount('one\ntwo')).toBe(2)
    expect(lineCount('one\r\ntwo\rthree')).toBe(3)
    expect(lineCount('')).toBe(1)
    expect(lineCount('\n\n')).toBe(2)
  })
})
