import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

// ── A feature that is off says so, with the way to turn it on ──────────────────────────────────────
//
// The reads a page loads to render AI feedback and the Doctor used to answer 404 while their switch
// was off, so every visit logged failed requests and the pages drew those as load failures:
// "Couldn't load the doctor report", a failed Doctor tile with a Retry that could never succeed, and
// — worse, for feedback — an empty table that said "No feedback yet" and a thumbs pair that hid only
// because the read errored. They now answer the decided `200 {"enabled": false}`
// (`docs/reference/api-overview.md`), and each surface renders that as "off" with the control that
// turns it back on.
//
// Every surface here is mounted over a stubbed `fetch` and the REAL api client, so the body asserted
// on is the wire's own and the requests counted are the ones the page really makes: once a read says
// off, the page does not go on to ask the questions whose answer it now knows.

let routes: Record<string, unknown> = {}
let requested: string[] = []
const writes: { url: string; method: string; body: unknown }[] = []
const ok = (v: unknown) => new Response(JSON.stringify(v), { status: 200, headers: { 'Content-Type': 'application/json' } })
const OFF = { enabled: false }

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  routes = {}
  requested = []
  writes.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (method !== 'GET') {
      writes.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      return ok({})
    }
    requested.push(url)
    return ok(routes[url] ?? {})
  }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

async function mountTile(id: string) {
  const { SETTINGS_WIDGETS } = await import('./settingsWidgets')
  const tile = SETTINGS_WIDGETS.find((w) => w.id === id)
  if (!tile) throw new Error(`the ${id} tile is gone`)
  function Host() { return <>{tile!.render('', () => {})}</> }
  return render(<Host />)
}

describe('AI feedback, switched off', () => {
  beforeEach(() => {
    routes = {
      '/api/feedback/producers': OFF,
      '/api/config/personalclaw': { feedback: { enabled: false, min_n: 5, window_days: 90, retire_threshold: 0.4 } },
    }
  })

  it('the panel says it is off and where the switch is — not "No feedback yet", not a failure', async () => {
    const { FeedbackPanel } = await import('./FeedbackPanel')
    render(<FeedbackPanel />)
    expect(await screen.findByText(/Feedback is off, so no 👍\/👎 are shown/)).toBeTruthy()
    expect(screen.getByText(/Turn on Collect feedback under Tuning below/)).toBeTruthy()
    expect(screen.queryByText(/No feedback yet/), 'off is not "nothing rated"').toBeNull()
    expect(screen.queryByRole('alert'), 'a decided answer is not a load failure').toBeNull()
    expect(await screen.findByRole('switch', { name: 'Collect feedback' })).toBeTruthy()
  })

  it('the hub tile says it is off and carries the switch that turns it on', async () => {
    const { container } = await mountTile('feedback')
    await waitFor(() => expect(container.textContent).toContain('Off — no 👍/👎 are shown on AI judgments'))
    expect(screen.queryByRole('alert')).toBeNull()
    fireEvent.click(screen.getByRole('switch', { name: 'Collect feedback' }))
    await waitFor(() => expect(writes).toContainEqual(expect.objectContaining({
      url: '/api/config/personalclaw', body: { path: 'feedback.enabled', value: true },
    })))
  })

  it('a thumbs pair renders nothing, because the read answered off', async () => {
    routes['/api/feedback/target/inbox_classification/i1'] = OFF
    const { FeedbackThumbs } = await import('../../ui/FeedbackThumbs')
    render(<FeedbackThumbs targetKind="inbox_classification" targetId="i1" />)
    await waitFor(() => expect(requested).toContain('/api/feedback/target/inbox_classification/i1'))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Mark accurate' })).toBeNull())
  })

  it('and with feedback on, the same pair does render — the vacuity control', async () => {
    routes['/api/feedback/target/inbox_classification/i1'] = { verdict: null }
    const { FeedbackThumbs } = await import('../../ui/FeedbackThumbs')
    render(<FeedbackThumbs targetKind="inbox_classification" targetId="i1" />)
    expect(await screen.findByRole('button', { name: 'Mark accurate' })).toBeTruthy()
  })
})

describe('the Doctor, switched off', () => {
  beforeEach(() => { routes = { '/api/doctor': OFF } })

  it('the page says it is off, asks nothing else, and turns it on from here', async () => {
    const { DoctorPanel } = await import('./DoctorPanel')
    render(<DoctorPanel />)
    expect(await screen.findByText('The Doctor is off')).toBeTruthy()
    expect(screen.queryByText(/Couldn't load the doctor report/), 'off is not a failed probe').toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
    // The report said off, so maintenance, the simulators and the fix catalog are not asked for.
    expect(requested.filter((u) => u !== '/api/doctor'), 'a request whose answer the page already knows').toEqual([])

    routes['/api/doctor?fresh=1'] = { ok: true, core_ok: true, worst: '', capabilities: {}, skipped_capabilities: [], checked_at: 0 }
    fireEvent.click(screen.getByRole('button', { name: 'Turn the Doctor on' }))
    await waitFor(() => expect(writes).toContainEqual(expect.objectContaining({
      url: '/api/config/personalclaw', body: { path: 'resilience.doctor_enabled', value: true },
    })))
    await waitFor(() => expect(requested).toContain('/api/doctor?fresh=1'))
    await waitFor(() => expect(screen.queryByText('The Doctor is off')).toBeNull())
  })

  it('the hub tile says it is off rather than failing', async () => {
    const { container } = await mountTile('doctor')
    await waitFor(() => expect(container.textContent).toContain('Off — nothing is probing health'))
    expect(screen.queryByRole('alert'), 'no failed tile for a switch that is merely off').toBeNull()
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
  })
})
