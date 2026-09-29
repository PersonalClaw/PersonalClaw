import { describe, expect, it } from 'vitest'
import { indefiniteArticle, labelNoun, withArticle } from './article'

describe('withArticle', () => {
  it('follows the first sound of a word, not its first letter', () => {
    expect(withArticle('audio file')).toBe('an audio file')
    expect(withArticle('image file')).toBe('an image file')
    expect(withArticle('video file')).toBe('a video file')
    expect(withArticle('user')).toBe('a user')
    expect(withArticle('one-off reminder')).toBe('a one-off reminder')
    expect(withArticle('hour')).toBe('an hour')
    expect(withArticle('obsidian vault')).toBe('an obsidian vault')
    expect(withArticle('unit')).toBe('a unit')
    expect(withArticle('uninstalled app')).toBe('an uninstalled app')
  })

  it('reads an acronym by its letters', () => {
    expect(withArticle('PDF file')).toBe('a PDF file')
    expect(withArticle('HTML page')).toBe('an HTML page')
    expect(withArticle('SMS')).toBe('an SMS')
    expect(withArticle('URL')).toBe('a URL')
  })

  it('reads a number by its spoken name', () => {
    expect(withArticle('8-cycle')).toBe('an 8-cycle')
    expect(withArticle('80% floor')).toBe('an 80% floor')
    expect(withArticle('11-cycle')).toBe('an 11-cycle')
    expect(withArticle('18-cycle')).toBe('an 18-cycle')
    expect(withArticle('110-cycle')).toBe('a 110-cycle')
    expect(withArticle('11000-row table')).toBe('an 11000-row table')
    expect(withArticle('5-cycle')).toBe('a 5-cycle')
    expect(withArticle('100% floor')).toBe('a 100% floor')
  })

  it('never fails on nothing', () => {
    expect(indefiniteArticle('')).toBe('a')
    expect(indefiniteArticle('—')).toBe('a')
  })
})

describe('labelNoun', () => {
  it('lowers a word and keeps an acronym', () => {
    expect(labelNoun('Audio')).toBe('audio')
    expect(labelNoun('Fleeting note')).toBe('fleeting note')
    expect(labelNoun('PDF')).toBe('PDF')
  })
})
