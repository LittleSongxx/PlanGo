import { randomBytes, randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { delimiter, join, resolve } from 'node:path'
import { spawn, type ChildProcess } from 'node:child_process'
import { app } from 'electron'
import { getConfig, getHarnessEnvironment } from './config'
import { executeBrowserCommand, releaseBrowserRun, activateBrowserRun, cancelBrowserRun } from './browser-bridge'
import { getMainWindow } from './index'
import { HarnessClient } from './harnessClient'
import { IPC } from '../shared/ipc'
import { listSkills } from './skills/loader'
import { getLocation } from './location'

let client: HarnessClient | undefined
let startup: Promise<HarnessClient> | undefined
let child: ChildProcess | undefined
let shuttingDown = false

function dataDir(): string {
  return getHarnessEnvironment().YOYU_DATA_DIR || join(app.getPath('userData'), 'harness')
}

function identity(): { token: string; browserSessionId: string } {
  const dir = dataDir()
  mkdirSync(dir, { recursive: true, mode: 0o700 })
  const file = join(dir, 'desktop-identity.json')
  if (existsSync(file)) {
    const saved = JSON.parse(readFileSync(file, 'utf8'))
    if (typeof saved.token !== 'string' || typeof saved.browserSessionId !== 'string') throw new Error('Invalid desktop identity')
    return { ...saved, token: getHarnessEnvironment().YOYU_BACKEND_TOKEN || saved.token }
  }
  const saved = { token: getHarnessEnvironment().YOYU_BACKEND_TOKEN || randomBytes(32).toString('hex'), browserSessionId: randomUUID() }
  writeFileSync(file, JSON.stringify(saved), { mode: 0o600 })
  return saved
}

function pythonPath(root: string): string {
  const configured = getHarnessEnvironment().YOYU_PYTHON
  if (configured) return configured
  const suffix = process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python']
  const local = join(root, '.venv', ...suffix)
  if (existsSync(local)) return local
  throw new Error('YOYU Python 环境尚未安装，请在本项目执行 npm run setup:backend。')
}

export async function getHarness(): Promise<HarnessClient> {
  if (shuttingDown) throw new Error('Application is closing')
  if (startup) return startup
  if (client) return client
  startup = connect().finally(() => { startup = undefined })
  return startup
}

async function connect(): Promise<HarnessClient> {
  const root = app.isPackaged ? join(process.resourcesPath, 'backend') : resolve(process.cwd())
  const id = identity()
  const environment = getHarnessEnvironment()
  const baseURL = environment.YOYU_BACKEND_URL || 'http://127.0.0.1:8011'
  const candidate = new HarnessClient({
    baseURL, ...id, dataDir: dataDir(), execute: executeBrowserCommand,
    onTerminal: releaseBrowserRun,
    onActivate: (runId, supersede) => { if (supersede) cancelBrowserRun(runId); activateBrowserRun(runId) },
    enabledSkills: () => listSkills().filter(skill => skill.enabled).map(skill => skill.id),
    location: () => {
      const config = getConfig()
      const coordinates = config.coords.split(',').map(Number)
      const valid = config.coords && coordinates.length === 2 && coordinates.every(Number.isFinite) && Math.abs(coordinates[0]) <= 180 && Math.abs(coordinates[1]) <= 90
      const source = getLocation().source
      return { city: config.city, ...(valid ? { longitude: coordinates[0], latitude: coordinates[1] } : {}), source: source === 'manual' ? 'manual' : source === 'config' ? 'config' : 'device' }
    },
    remind: (notification) => {
      const window = getMainWindow()
      if (!window || window.isDestroyed() || window.webContents.isLoading()) return false
      try { window.webContents.send(IPC.proactivePush, notification); return true } catch { return false }
    },
    emit: (event) => {
      const window = getMainWindow()
      if (window && !window.isDestroyed()) window.webContents.send(IPC.harnessEvent, event)
    }
  })
  // Liveness is transport readiness; a missing model key should remain configurable in the UI.
  const isYoyuService = () => candidate.request<Record<string, unknown>>('/api/v1/health/live')
    .then(value => value.app === 'YOYU' && value.world_provider === 'browser').catch(() => false)
  let live = await isYoyuService()
  if (!live && environment.YOYU_BACKEND_AUTOSTART !== 'false') {
    const url = new URL(baseURL)
    if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) throw new Error('Remote Harness must be started separately')
    const cfg = getConfig()
    const moduleRoot = join(root, 'backend')
    const vendorRoot = join(root, 'vendor', 'planora', 'backend')
    if (!existsSync(join(moduleRoot, 'yoyu', 'app.py'))) throw new Error('YOYU backend source is missing')
    let launchError = ''
    child = spawn(pythonPath(root), ['-m', 'uvicorn', 'yoyu.app:create_app', '--factory', '--host', '127.0.0.1', '--port', url.port || '8011', '--no-access-log'], {
      cwd: root,
      env: {
        ...process.env,
        ...environment,
        PYTHONPATH: [moduleRoot, vendorRoot].join(delimiter),
        PYTHONDONTWRITEBYTECODE: '1',
        YOYU_BACKEND_TOKEN: id.token,
        YOYU_DATA_DIR: dataDir(),
        YOYU_RUNTIME_PROFILE: environment.YOYU_RUNTIME_PROFILE || 'desktop',
        YOYU_SKILLS_DIR: join(root, 'skills'),
        OPENAI_API_KEY: cfg.llm.apiKey,
        OPENAI_BASE_URL: cfg.llm.baseURL,
        OPENAI_MODEL: cfg.llm.model,
        AMAP_WEBSERVICE_KEY: cfg.amap.key
      },
      stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true
    })
    // Do not pipe provider output/prompts/credentials into renderer logs.
    child.stderr?.on('data', () => {})
    child.on('error', (error) => { launchError = error.message })
    child.on('exit', (code) => { if (code && !shuttingDown) launchError = `Backend exited (${code}); install backend dependencies or check the configured port` })
    for (let attempt = 0; attempt < 30 && !shuttingDown; attempt++) {
      if (launchError) break
      await new Promise((r) => setTimeout(r, 500))
      live = await isYoyuService()
      if (live) break
    }
    if (!live) {
      candidate.stop()
      if (child && child.exitCode === null) child.kill('SIGTERM')
      child = undefined
      throw new Error(launchError || 'Harness startup timed out; install the Python dependencies first')
    }
  }
  if (!live) { candidate.stop(); throw new Error('Harness unavailable; check YOYU_BACKEND_URL and authentication') }
  if (shuttingDown) { candidate.stop(); child?.kill('SIGTERM'); throw new Error('Application is closing') }
  client = candidate
  client.startBrowserPolling()
  return client
}

export async function harnessStatus(): Promise<{ ready: boolean; error?: string }> {
  try { return await (await getHarness()).status() }
  catch (error) { return { ready: false, error: (error as Error).message } }
}

export async function restartHarness(): Promise<void> {
  const pending = startup
  startup = (async () => {
    await pending?.catch(() => {})
    const previous = client
    client = undefined
    await previous?.close()
    if (child && child.exitCode === null) {
      const exiting = child
      await new Promise<void>((resolveExit) => {
        const timer = setTimeout(() => { exiting.kill('SIGKILL'); resolveExit() }, 5000)
        exiting.once('exit', () => { clearTimeout(timer); resolveExit() })
        exiting.kill('SIGTERM')
      })
    }
    child = undefined
    return connect()
  })().finally(() => { startup = undefined })
  await startup
}

export function stopHarness(): void {
  shuttingDown = true
  client?.stop()
  if (child && child.exitCode === null) child.kill('SIGTERM')
}
