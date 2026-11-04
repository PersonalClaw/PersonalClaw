import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { spawn, type ChildProcess } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createServer, type AddressInfo } from 'node:net'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { INSTALL_PORT, STALL_RECORD, TAIL_BYTES, TAIL_LINES, askTheGatewayWhereItWaits } from '../../e2e/runPlaces'

// Every health probe the record makes, seen here before it goes anywhere: the install's own port is
// never connected to, so a planted-out guard fails here instead of reaching the install's gateway.
const probes = vi.hoisted(() => [] as { host?: string; port?: number; timeout?: number }[])
const throwsFor = vi.hoisted(() => new Set<number>())
vi.mock('node:http', async (importOriginal) => {
  const real = await importOriginal<typeof import('node:http')>()
  type Options = { host?: string; port?: number; timeout?: number }
  return {
    ...real,
    get: (options: Options, ...rest: unknown[]) => {
      probes.push({ host: options.host, port: options.port, timeout: options.timeout })
      if (options.port === 10_000) throw new Error('a probe reached for the install\'s own port')
      if (options.port !== undefined && throwsFor.has(options.port)) throw new Error('a planted fault in the probe')
      return (real.get as (...args: unknown[]) => unknown)(options, ...rest)
    },
  }
})

// ── A spec that finds the harness gateway unanswering asks it where it waits ────────────────────
//
// The harness lost its gateway mid-run five times with a gateway.log of 0 bytes: requests were
// accepted and never answered, and nothing said where the gateway was. Now the first spec whose
// shell guard finds it unanswering records the gateway's process state, its port and the tail of its
// gateway.log, where its stall watch writes every thread's stack (`src/personalclaw/loop_watch.py`),
// in the run's output folder, and prints the record into the job's log. It sends the gateway
// nothing: it once sent SIGUSR1 for faulthandler's dump of every thread, which crashed a gateway
// whose other threads ran Python. These stand a small process in for the gateway: it counts the
// SIGUSR1s it is sent, which must stay none.

let scratch = ''
const children: ChildProcess[] = []
beforeEach(() => { probes.length = 0; throwsFor.clear() })
afterEach(() => {
  for (const child of children.splice(0)) child.kill('SIGKILL')
  if (scratch) rmSync(scratch, { recursive: true, force: true })
  scratch = ''
})

/** A free loopback port: held, then let go, for a process to listen on. */
function aFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as AddressInfo
      server.close(() => resolve(port))
    })
  })
}

/** A process that counts the SIGUSR1s it is sent into *signals*, started with *args*, answering
 *  HTTP on *port* when it is given one. */
async function aProcess(signals: string, args: string[], port = 0): Promise<ChildProcess> {
  const child = spawn(process.execPath, [
    '-e',
    `const fs = require('fs'); ` +
      `process.on('SIGUSR1', () => fs.appendFileSync(${JSON.stringify(signals)}, 'USR1\\n')); ` +
      (port ? `require('http').createServer((q, r) => r.end('ok')).listen(${port}, '127.0.0.1', () => ` : '(() => ') +
      `fs.writeFileSync(${JSON.stringify(signals)}, ''))` + (port ? '' : '()'),
    ...args,
  ])
  children.push(child)
  await expect.poll(() => existsSync(signals), { timeout: 5_000 }).toBe(true)
  return child
}

/** A run's home and output folder, and a process standing in for the gateway, started as
 *  GATEWAY_COMMAND starts it (`gateway --port <n>`) and listening on that port, its pid and port
 *  recorded in the home as the gateway records its own (`gateway.runtime.json`). */
async function aRunWithAGateway(): Promise<{ env: NodeJS.ProcessEnv; out: string; signals: string; pid: number; port: number }> {
  scratch = mkdtempSync(join(tmpdir(), 'pw-stall-'))
  const home = join(scratch, 'home')
  const out = join(scratch, 'out')
  mkdirSync(home)
  mkdirSync(out)
  const signals = join(scratch, 'signals')
  const port = await aFreePort()
  const child = await aProcess(signals, ['gateway', '--port', String(port), '--no-open'], port)
  writeFileSync(join(home, 'gateway.runtime.json'), JSON.stringify({ port, pid: child.pid }))
  // More lines than the record keeps, all inside its byte cap.
  const early = Array.from({ length: TAIL_LINES * 2 }, (_, i) => `an early line ${i}`).join('\n')
  writeFileSync(join(home, 'gateway.log'), `${early}\nthe line before\nthe last line it wrote\n`)
  return { env: { PW_E2E_HOME: home, PW_OUTPUT_DIR: out }, out, signals, pid: child.pid!, port }
}

const sent = (signals: string) => readFileSync(signals, 'utf8').split('\n').filter(Boolean).length

describe.skipIf(process.platform === 'win32')('a spec that finds the gateway unanswering', () => {
  it('records the gateway\'s process state, its port and its log\'s tail, and sends it nothing, once a run', async () => {
    const run = await aRunWithAGateway()
    await askTheGatewayWhereItWaits(run.env)
    const record = readFileSync(join(run.out, STALL_RECORD), 'utf8')
    expect(record).toContain(`the harness gateway (pid ${run.pid}, port `)
    expect(record, 'its process state, from ps').toMatch(new RegExp(`STAT[^\\n]*\\n\\s*${run.pid}\\s`))
    expect(record, 'whether it answers on its port').toMatch(/Its port: answers \/api\/healthz \(HTTP 200\) in \d+ ms\./)
    expect(record, 'the tail of its gateway.log').toContain('the line before\nthe last line it wrote')
    const tail = record.slice(record.indexOf('of its gateway.log:\n'))
    expect(tail, 'only its last lines').not.toContain(`an early line ${TAIL_LINES - 1}\n`)
    expect(tail.trimEnd().split('\n').length - 1, 'no more than the line cap').toBeLessThanOrEqual(TAIL_LINES)
    expect(record, 'the record says so').toContain('Nothing was sent to it')
    expect(sent(run.signals), '🔴 red before: it was sent SIGUSR1').toBe(0)
    expect(probes, 'one probe, to loopback, on the recorded port, with a short timeout').toEqual([
      { host: '127.0.0.1', port: run.port, timeout: 2_000 },
    ])

    await askTheGatewayWhereItWaits(run.env)
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(sent(run.signals), 'nor by a later spec').toBe(0)
    expect(readFileSync(join(run.out, STALL_RECORD), 'utf8'), 'and leaves the first record').toBe(record)
  }, 15_000)

  it('says a stopped gateway is stopped, and takes connections it never answers', async () => {
    // The planted stall #5853 asks for: what the harness's lost runs looked like from outside, the
    // port still bound and nothing answering on it.
    const run = await aRunWithAGateway()
    const child = children[children.length - 1]
    child.kill('SIGSTOP')
    try {
      await askTheGatewayWhereItWaits(run.env)
    } finally {
      child.kill('SIGCONT')
    }
    const record = readFileSync(join(run.out, STALL_RECORD), 'utf8')
    expect(record, 'its process state is stopped').toMatch(new RegExp(`\\n\\s*${run.pid}\\s+T`))
    expect(record).toContain('Its port: takes the connection, and sends no answer within 2 s.')
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(sent(run.signals), 'nothing was sent, stopped or not').toBe(0)
  }, 15_000)

  it('never signals a gateway whose home is the default one or inside it, nor one on the install\'s port', async () => {
    const run = await aRunWithAGateway()
    const realHome = process.env.HOME
    // A stand-in for the user's home, holding a default home with a live gateway's record in it:
    // the real `~/.personalclaw` is neither read nor written.
    const user = join(scratch, 'user')
    const defaultHome = join(user, '.personalclaw')
    mkdirSync(join(defaultHome, 'inside'), { recursive: true })
    const record = JSON.stringify({ port: 1234, pid: run.pid })
    writeFileSync(join(defaultHome, 'gateway.runtime.json'), record)
    writeFileSync(join(defaultHome, 'inside', 'gateway.runtime.json'), record)
    process.env.HOME = user
    try {
      await askTheGatewayWhereItWaits({ ...run.env, PW_E2E_HOME: defaultHome })
      await askTheGatewayWhereItWaits({ ...run.env, PW_E2E_HOME: join(defaultHome, 'inside') })
    } finally {
      process.env.HOME = realHome
    }
    writeFileSync(join(run.env.PW_E2E_HOME!, 'gateway.runtime.json'), JSON.stringify({ port: INSTALL_PORT, pid: run.pid }))
    await askTheGatewayWhereItWaits(run.env)
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(sent(run.signals), 'the install\'s gateway is never the one signalled').toBe(0)
    expect(probes, 'nor probed: no health probe is made at all').toEqual([])
    expect(existsSync(join(run.out, STALL_RECORD))).toBe(false)
  })

  it('signals nothing and writes nothing where the run names no home, its home no gateway, or a port that is no port', async () => {
    const run = await aRunWithAGateway()
    await askTheGatewayWhereItWaits({ PW_OUTPUT_DIR: run.out })
    await askTheGatewayWhereItWaits({ ...run.env, PW_E2E_HOME: join(scratch, 'no-such-home') })
    writeFileSync(join(run.env.PW_E2E_HOME!, 'gateway.runtime.json'), '{"port": 1234, "pid": 1}')
    await askTheGatewayWhereItWaits(run.env)
    writeFileSync(join(run.env.PW_E2E_HOME!, 'gateway.runtime.json'), 'not json')
    await askTheGatewayWhereItWaits(run.env)
    for (const port of ['"x"', '0', '65536', '1.5', 'null']) {
      writeFileSync(join(run.env.PW_E2E_HOME!, 'gateway.runtime.json'), `{"port": ${port}, "pid": ${run.pid}}`)
      await askTheGatewayWhereItWaits(run.env)
    }
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(sent(run.signals)).toBe(0)
    expect(probes).toEqual([])
    expect(existsSync(join(run.out, STALL_RECORD))).toBe(false)
  })

  it('never signals a process that is not the harness gateway now, and says so without naming it', async () => {
    // The gateway died and its pid went to another process: the record still names the pid, and
    // nothing of that process is probed or written.
    const run = await aRunWithAGateway()
    const other = join(scratch, 'other-signals')
    const plain = await aProcess(other, ['a-plain-process-of-someone-else'])
    writeFileSync(join(run.env.PW_E2E_HOME!, 'gateway.runtime.json'), JSON.stringify({ port: run.port, pid: plain.pid }))
    await askTheGatewayWhereItWaits(run.env)
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(sent(other), 'the process now holding the pid was sent nothing').toBe(0)
    expect(sent(run.signals)).toBe(0)
    expect(probes, 'nor its port probed').toEqual([])
    const record = readFileSync(join(run.out, STALL_RECORD), 'utf8')
    expect(record).toContain(`pid ${plain.pid} isn't the harness gateway now; nothing was sent`)
    expect(record, 'the other process\'s command line is not written').not.toContain('a-plain-process-of-someone-else')
  }, 15_000)

  it('never throws: a fault inside it leaves the spec\'s own failure to be reported', async () => {
    const run = await aRunWithAGateway()
    throwsFor.add(run.port)
    await expect(askTheGatewayWhereItWaits(run.env)).resolves.toBeUndefined()
  }, 15_000)

  it('prints its record into the job\'s log, with gateway.log\'s tail capped in lines and bytes', async () => {
    const run = await aRunWithAGateway()
    // A log longer than the cap both ways: many short lines, then a few very long ones.
    const short = Array.from({ length: TAIL_LINES * 3 }, (_, i) => `short line ${i}`)
    const long = Array.from({ length: 8 }, (_, i) => `long line ${i} ${'x'.repeat(TAIL_BYTES / 4)}`)
    writeFileSync(join(run.env.PW_E2E_HOME!, 'gateway.log'), [...short, ...long, 'the last line'].join('\n') + '\n')
    const printed: string[] = []
    const error = vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => { printed.push(args.join(' ')) })
    try {
      await askTheGatewayWhereItWaits(run.env)
    } finally {
      error.mockRestore()
    }
    const record = readFileSync(join(run.out, STALL_RECORD), 'utf8')
    expect(printed.join('\n'), 'the job\'s log gets the record').toContain(record)
    const tail = record.slice(record.indexOf('of its gateway.log:\n') + 'of its gateway.log:\n'.length)
    expect(tail).toContain('the last line')
    expect(Buffer.byteLength(tail), 'no more than the byte cap').toBeLessThanOrEqual(TAIL_BYTES + 1)
    expect(tail.split('\n').length, 'no more than the line cap').toBeLessThanOrEqual(TAIL_LINES + 2)
    expect(tail, 'the short lines are past the byte cap').not.toContain('short line')
  }, 15_000)
})
