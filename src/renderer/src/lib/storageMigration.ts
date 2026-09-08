// Historical keys are removed only after every new value is durable and verified.
export function migrateLocalStorage(storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>): void {
  const entries = ['sessions', 'hidden_sessions', 'active_run'].map(suffix => {
    const oldKey = `xy_${suffix}`, newKey = `plango_${suffix}`
    const value = storage.getItem(oldKey), current = storage.getItem(newKey)
    if (value !== null && current !== null && value !== current) throw new Error(`PlanGo migration conflict: ${oldKey} and ${newKey}`)
    return { oldKey, newKey, value }
  })
  for (const { newKey, value } of entries) {
    if (value === null) continue
    storage.setItem(newKey, value)
    if (storage.getItem(newKey) !== value) throw new Error('PlanGo storage migration could not persist the existing session')
  }
  for (const { oldKey, value } of entries) if (value !== null) storage.removeItem(oldKey)
}
