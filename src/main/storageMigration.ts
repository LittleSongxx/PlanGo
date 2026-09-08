// R0 one-time migration. Historical names are accepted only as unopened source paths.
import { closeSync, existsSync, lstatSync, mkdirSync, openSync, readFileSync, readlinkSync, readdirSync, renameSync, rmSync } from 'node:fs'
import { hostname } from 'node:os'
import { dirname, join, resolve } from 'node:path'

function present(path: string): boolean {
  try { lstatSync(path); return true } catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return false; throw error }
}

function checkMove(source: string, target: string): boolean {
  if (!present(source)) return false
  if (present(target)) throw new Error(`PlanGo migration conflict: both ${source} and ${target} exist`)
  if (lstatSync(source).isSymbolicLink()) throw new Error(`PlanGo migration refuses a linked source: ${source}`)
  return true
}

export function migrateConfigFile(directory: string, dot = ''): string {
  const source = join(directory, `${dot}xiaonian-config.json`)
  const target = join(directory, `${dot}plango-config.json`)
  if (checkMove(source, target)) {
    try { const value = JSON.parse(readFileSync(source, 'utf8')); if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error() }
    catch { throw new Error('PlanGo migration stopped: invalid legacy settings; original file preserved') }
    renameSync(source, target)
  }
  return target
}

function checkClosedProfile(directory: string): void {
  const lock = join(directory, 'SingletonLock')
  if (!present(lock)) return
  const value = readlinkSync(lock)
  const match = /^(.*)-(\d+)$/.exec(value)
  if (!match || match[1] !== hostname()) throw new Error('PlanGo migration requires the original desktop to be closed on its host')
  try { process.kill(Number(match[2]), 0) }
  catch (error) { if ((error as NodeJS.ErrnoException).code === 'ESRCH') return; throw error }
  throw new Error('PlanGo migration requires the original desktop to be closed')
}

function checkCookieEncryption(directory: string): void {
  // Renaming OS-keyring identities needs an explicit rekey migration; never let Chromium silently drop unreadable cookies.
  const partitions = join(directory, 'Partitions')
  const roots = [directory, ...(existsSync(partitions) ? readdirSync(partitions).map(name => join(partitions, name)) : [])]
  for (const root of roots) {
    for (const path of ['Cookies', 'Cookies-wal', 'Network/Cookies', 'Network/Cookies-wal'].map(name => join(root, name))) {
      if (!existsSync(path)) continue
      const bytes = readFileSync(path)
      // ponytail: a conservative marker scan may refuse plaintext containing v11; use SQLite encrypted_value queries if that occurs.
      if (process.platform === 'darwin' || bytes.includes(Buffer.from('v11'))) throw new Error('PlanGo migration requires OS keyring migration before opening the existing encrypted browser profile')
    }
  }
}

export function prepareDesktopStorage(appData: string, currentUserData: string): string {
  const target = join(appData, 'plango')
  const defaults = ['plango', 'PlanGo', 'yoyu', 'YOYU', 'xiaonian'].map(name => join(appData, name))
  const custom = !defaults.some(path => resolve(path) === resolve(currentUserData))
  const destination = custom ? currentUserData : target
  const bootstrap = join(appData, 'PlanGo')
  // Electron creates the productName Crashpad folder before loading our entrypoint. Keep that live crash database in place.
  const crashpadOnly = present(bootstrap) && !lstatSync(bootstrap).isSymbolicLink() && lstatSync(bootstrap).isDirectory()
    && readdirSync(bootstrap).length === 1 && readdirSync(bootstrap)[0] === 'Crashpad'
    && lstatSync(join(bootstrap, 'Crashpad')).isDirectory() && !lstatSync(join(bootstrap, 'Crashpad')).isSymbolicLink()
  const sources = custom ? [] : defaults.filter(path => path !== target && present(path) && !(path === bootstrap && crashpadOnly))
  if (sources.length > 1) throw new Error('PlanGo migration found multiple legacy desktop profiles; no profile was selected')
  const source = sources[0]
  if (source) checkMove(source, destination)
  const existing = source || destination
  checkClosedProfile(existing)
  if (source) checkCookieEncryption(source)
  const partitionSource = join(existing, 'Partitions', 'xiaonian')
  const partitionTarget = join(existing, 'Partitions', 'plango')
  const movePartition = checkMove(partitionSource, partitionTarget)
  if (movePartition && !source) checkCookieEncryption(existing)
  checkMove(join(existing, 'xiaonian-config.json'), join(existing, 'plango-config.json'))
  mkdirSync(dirname(destination), { recursive: true, mode: 0o700 })
  const lock = join(dirname(destination), '.plango-migration.lock')
  const fd = openSync(lock, 'wx', 0o600)
  try {
    // Move the entire profile, including Local State, localStorage, identity, and pending/UNKNOWN receipts.
    if (source) renameSync(source, destination)
    else mkdirSync(destination, { recursive: true, mode: 0o700 })
    if (movePartition) renameSync(join(destination, 'Partitions', 'xiaonian'), join(destination, 'Partitions', 'plango'))
    migrateConfigFile(destination)
  } finally { closeSync(fd); rmSync(lock) }
  return destination
}
