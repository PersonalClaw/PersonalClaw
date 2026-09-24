import { describe, it, expect } from 'vitest'
import {
  initSliderState,
  sliderReducer,
  isAnswered,
  canAdvance,
  canSubmit,
  answerRecord,
  OTHER_CHOICE,
  type SliderQuestion,
} from './sliderState'

const q = (over: Partial<SliderQuestion> = {}): SliderQuestion => ({
  id: 'k', prompt: 'why?', kind: 'text', required: true, ...over,
})

describe('initSliderState — seeding', () => {
  it('seeds a required question from its recommendation (accept, do not retype)', () => {
    const s = initSliderState([q({ id: 'a', recommended: 'go fast' })])
    expect(s.answers.a).toBe('go fast')
    expect(s.index).toBe(0)
  })

  it('a prior answer store wins over the recommendation (resumed review)', () => {
    const s = initSliderState([q({ id: 'a', recommended: 'rec' })], { a: 'mine' })
    expect(s.answers.a).toBe('mine')
  })

  it('a choice answer not in the option set starts in custom mode', () => {
    const s = initSliderState(
      [q({ id: 'c', kind: 'choice', choices: ['x', 'y'], recommended: 'z' })],
    )
    expect(s.custom.c).toBe(true)
    const s2 = initSliderState([q({ id: 'c', kind: 'choice', choices: ['x', 'y'], recommended: 'x' })])
    expect(s2.custom.c).toBeUndefined()
  })
})

describe('sliderReducer — advance / back / goto are one-at-a-time and clamped', () => {
  it('advances forward but never past the last question', () => {
    let s = initSliderState([q({ id: 'a' }), q({ id: 'b' })])
    s = sliderReducer(s, { type: 'next', total: 2 })
    expect(s.index).toBe(1)
    s = sliderReducer(s, { type: 'next', total: 2 })
    expect(s.index).toBe(1) // clamped
  })

  it('goes back but never before the first', () => {
    let s = { index: 1, answers: {}, custom: {} }
    s = sliderReducer(s, { type: 'back' })
    expect(s.index).toBe(0)
    s = sliderReducer(s, { type: 'back' })
    expect(s.index).toBe(0) // clamped
  })

  it('goto clamps into range', () => {
    let s = initSliderState([q({ id: 'a' }), q({ id: 'b' })])
    s = sliderReducer(s, { type: 'goto', index: 9, total: 2 })
    expect(s.index).toBe(1)
    s = sliderReducer(s, { type: 'goto', index: -3, total: 2 })
    expect(s.index).toBe(0)
  })
})

describe('sliderReducer — custom-answer escape hatch', () => {
  it('leaving custom mode clears the stale freeform answer', () => {
    let s = initSliderState([q({ id: 'c', kind: 'choice', choices: ['x'] })])
    s = sliderReducer(s, { type: 'toggleCustom', id: 'c', on: true })
    s = sliderReducer(s, { type: 'answer', id: 'c', value: 'freeform text' })
    expect(s.answers.c).toBe('freeform text')
    s = sliderReducer(s, { type: 'toggleCustom', id: 'c', on: false })
    expect(s.custom.c).toBe(false)
    expect(s.answers.c).toBe('') // cleared so it can't linger as the value
  })

  it('is pure — never mutates the input state', () => {
    const s0 = initSliderState([q({ id: 'a' })])
    const s1 = sliderReducer(s0, { type: 'answer', id: 'a', value: 'v' })
    expect(s0.answers.a).toBeUndefined()
    expect(s1).not.toBe(s0)
  })
})

describe('gating — advance + submit', () => {
  it('a required question blocks advance until answered; non-required is skippable', () => {
    const req = q({ id: 'a', required: true, recommended: '' })
    const opt = q({ id: 'b', required: false, kind: 'boundary' })
    let s = initSliderState([req, opt])
    expect(canAdvance(req, s)).toBe(false)
    expect(canAdvance(opt, s)).toBe(true) // boundary is never required
    s = sliderReducer(s, { type: 'answer', id: 'a', value: 'x' })
    expect(canAdvance(req, s)).toBe(true)
  })

  it('Submit unlocks only when every required question is answered', () => {
    const qs = [q({ id: 'a', recommended: '' }), q({ id: 'b', required: false, kind: 'boundary' })]
    let s = initSliderState(qs)
    expect(canSubmit(qs, s)).toBe(false)
    s = sliderReducer(s, { type: 'answer', id: 'a', value: 'done' })
    expect(canSubmit(qs, s)).toBe(true)
  })

  it('a walk with no required questions is submittable immediately', () => {
    const qs = [q({ id: 'b', required: false, kind: 'boundary' })]
    expect(canSubmit(qs, initSliderState(qs))).toBe(true)
  })

  it('whitespace does not count as answered', () => {
    const only = q({ id: 'a', recommended: '' })
    let s = initSliderState([only])
    s = sliderReducer(s, { type: 'answer', id: 'a', value: '   ' })
    expect(isAnswered(only, s)).toBe(false)
  })
})

describe('answerRecord — the typed answer the walk returns', () => {
  it('collects trimmed answers and drops skipped (empty) ones', () => {
    const qs = [q({ id: 'a' }), q({ id: 'b', required: false }), q({ id: 'c', required: false })]
    let s = initSliderState(qs, { a: '  keep ', c: '' })
    s = sliderReducer(s, { type: 'answer', id: 'b', value: '   ' })
    expect(answerRecord(qs, s)).toEqual({ a: 'keep' })
  })

  it('records a custom "Other" answer as its freeform text, not the OTHER sentinel', () => {
    const qs = [q({ id: 'c', kind: 'choice', choices: ['x', OTHER_CHOICE] })]
    let s = initSliderState(qs)
    s = sliderReducer(s, { type: 'toggleCustom', id: 'c', on: true })
    s = sliderReducer(s, { type: 'answer', id: 'c', value: 'my own answer' })
    expect(answerRecord(qs, s)).toEqual({ c: 'my own answer' })
  })
})
