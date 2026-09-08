import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs'
import { hostname, tmpdir } from 'node:os'
import { join } from 'node:path'
import { migrateConfigFile, prepareDesktopStorage } from '../src/main/storageMigration'
import { migrateLocalStorage } from '../src/renderer/src/lib/storageMigration'
import { getConfig, getHarnessEnvironment, parseEnv, setConfig } from '../src/main/config'

const work = mkdtempSync(join(tmpdir(), 'plango-storage-check-'))
try {
  assert.deepEqual(parseEnv(' export PLANGO_BACKEND_TOKEN = "unchanged=fixture" # comment\nPLANGO_CITY=\'重庆\'\nOPENAI_API_KEY=literal#value # comment\nPLANGO_CITY=重庆\n'), {
    PLANGO_BACKEND_TOKEN: 'unchanged=fixture', PLANGO_CITY: '重庆', OPENAI_API_KEY: 'literal#value'
  })
  assert.deepEqual(parseEnv(String.raw`SINGLE='literal\n and \'quote'` + '\n' + String.raw`DOUBLE="line\nnext\tend\\"`), { SINGLE: "literal\\n and 'quote", DOUBLE: 'line\nnext\tend\\' })
  assert.equal(parseEnv('PLANGO_BACKEND_TOKEN= "${UNCHANGED}"\n').PLANGO_BACKEND_TOKEN, '${UNCHANGED}', 'Never expand or execute configuration text')
  for (const invalid of ['PLANGO_BACKEND_TOKEN="unclosed', 'PLANGO_BACKEND_TOKEN=one\nPLANGO_BACKEND_TOKEN=two', 'invalid assignment']) assert.throws(() => parseEnv(invalid), /configuration/)
  const old = join(work, 'yoyu'), target = join(work, 'plango')
  mkdirSync(join(old, 'harness'), { recursive: true })
  mkdirSync(join(old, 'Partitions/xiaonian'), { recursive: true })
  const original = {
    'harness/desktop-identity.json': JSON.stringify({ token: 'fixture-token', browserSessionId: 'original-session' }),
    'harness/browser-receipts.json': JSON.stringify([['unknown', { command: { command_id: 'unknown' }, result: { outcome: 'unknown' } }], ['interrupted', { command: { command_id: 'interrupted' } }]]),
    'Partitions/xiaonian/Cookies': 'fixture-cookie-bytes',
    'xiaonian-config.json': JSON.stringify({ city: '重庆', llm: { apiKey: 'fixture-settings-key' } })
  }
  for (const [path, value] of Object.entries(original)) writeFileSync(join(old, path), value, { mode: 0o600 })
  mkdirSync(join(work, 'PlanGo/Crashpad'), { recursive: true })
  writeFileSync(join(work, 'PlanGo/Crashpad/settings.dat'), 'bootstrap-crashpad')
  symlinkSync(`${hostname()}-${process.pid}`, join(old, 'SingletonLock'))
  assert.throws(() => prepareDesktopStorage(work, target), /closed/)
  assert(existsSync(old) && !existsSync(target))
  rmSync(join(old, 'SingletonLock'))
  assert.equal(prepareDesktopStorage(work, target), target)
  assert.equal(readFileSync(join(work, 'PlanGo/Crashpad/settings.dat'), 'utf8'), 'bootstrap-crashpad')
  assert(!existsSync(old))
  for (const [path, value] of Object.entries(original)) {
    const current = join(target, path.replace('xiaonian', 'plango'))
    assert.equal(readFileSync(current, 'utf8'), value)
    assert.equal(statSync(current).mode & 0o777, 0o600)
  }
  assert.equal(prepareDesktopStorage(work, target), target, 'Second launch is idempotent')
  writeFileSync(join(work, 'PlanGo/Preferences'), '{}')
  assert.throws(() => prepareDesktopStorage(work, target), /conflict/)
  rmSync(join(work, 'PlanGo/Preferences'))
  mkdirSync(old)
  assert.throws(() => prepareDesktopStorage(work, target), /conflict/)
  assert(existsSync(old))
  rmSync(old, { recursive: true })
  writeFileSync(join(target, 'xiaonian-config.json'), '{}')
  assert.throws(() => migrateConfigFile(target), /conflict/)
  assert(existsSync(join(target, 'xiaonian-config.json')))
  const fresh = join(work, 'fresh')
  assert.equal(prepareDesktopStorage(fresh, join(fresh, 'plango')), join(fresh, 'plango'))
  const encrypted = join(work, 'encrypted')
  mkdirSync(join(encrypted, 'yoyu/Partitions/xiaonian'), { recursive: true })
  writeFileSync(join(encrypted, 'yoyu/Partitions/xiaonian/Cookies'), 'v11-fixture')
  assert.throws(() => prepareDesktopStorage(encrypted, join(encrypted, 'plango')), /keyring/)
  assert(existsSync(join(encrypted, 'yoyu')))

  const values = new Map([['xy_sessions', '[{"runId":"original-run"}]'], ['xy_hidden_sessions', '["hidden-run"]'], ['xy_active_run', 'original-run']])
  const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value) }, removeItem: (key: string) => { values.delete(key) } }
  values.set('plango_active_run', 'conflict')
  const before = [...values]
  assert.throws(() => migrateLocalStorage(storage), /conflict/)
  assert.deepEqual([...values], before, 'Conflict preflight must not change any keys')
  values.delete('plango_active_run')
  assert.throws(() => migrateLocalStorage({ ...storage, setItem: (key, value) => { if (key.endsWith('hidden_sessions')) throw new Error('quota'); storage.setItem(key, value) } }), /quota/)
  assert.equal(values.get('xy_active_run'), 'original-run', 'A partial write retains all source keys')
  migrateLocalStorage(storage)
  migrateLocalStorage(storage)
  assert.deepEqual([...values.keys()].sort(), ['plango_active_run', 'plango_hidden_sessions', 'plango_sessions'])
  assert.equal(values.get('plango_active_run'), 'original-run')
  const cwd = process.cwd()
  try {
    process.chdir(fresh)
    writeFileSync('.env', 'YOYU_BACKEND_TOKEN=old-fixture\nPLANGO_BACKEND_TOKEN=new-fixture\n')
    assert.throws(() => getHarnessEnvironment(), /one-time migration/)
    writeFileSync('.env', 'PLANGO_BACKEND_TOKEN=fixture-token\nPLANGO_CITY=重庆\n')
    assert.deepEqual(getHarnessEnvironment(), { PLANGO_BACKEND_TOKEN: 'fixture-token', PLANGO_CITY: '重庆' })
    assert.equal(getConfig().city, '重庆')
    setConfig({ city: '重庆', coords: '106.5,29.5' })
    assert.equal(statSync('.plango-config.json').mode & 0o777, 0o600)
    assert.equal(JSON.parse(readFileSync('.plango-config.json', 'utf8')).coords, '106.5,29.5')
  } finally { process.chdir(cwd) }
  console.log('Desktop migration checks passed: closed profile, fresh/upgrade, exact identity/UNKNOWN bytes, permissions, conflicts, encryption guard, interrupted localStorage write and idempotency')
} finally { rmSync(work, { recursive: true, force: true }) }
