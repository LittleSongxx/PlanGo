// Attachments use Chromium's native blob storage; metadata stays in the existing localStorage composer.
// ponytail: retain blobs so other drafts/recovered requests keep working; add reference GC if disk usage matters.
let database: Promise<IDBDatabase> | undefined
function open(): Promise<IDBDatabase> {
  return database ||= new Promise((resolve, reject) => {
    const request = indexedDB.open('plango_draft_images', 1)
    request.onupgradeneeded = () => request.result.createObjectStore('images')
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => { database = undefined; reject(request.error) }
    request.onblocked = () => { database = undefined; reject(new Error('图片存储正在升级，请关闭旧窗口后重试。')) }
  })
}
let lastSave: { image: string; promise: Promise<string> } | undefined
export function saveDraftImage(image: string): Promise<string> {
  if (lastSave?.image === image) return lastSave.promise
  const promise = save(image).catch(error => { if (lastSave?.promise === promise) lastSave = undefined; throw error })
  lastSave = { image, promise }
  return promise
}
async function save(image: string): Promise<string> {
  const bytes = new TextEncoder().encode(image)
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  const id = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('')
  const db = await open()
  await new Promise<void>((resolve, reject) => {
    const transaction = db.transaction('images', 'readwrite')
    transaction.objectStore('images').put(new Blob([bytes], { type: 'text/plain' }), id)
    transaction.oncomplete = () => resolve()
    transaction.onerror = transaction.onabort = () => reject(transaction.error || new Error('图片未能保存'))
  })
  return id
}
export async function loadDraftImage(id: string): Promise<string> {
  const db = await open()
  const blob = await new Promise<Blob>((resolve, reject) => {
    const request = db.transaction('images', 'readonly').objectStore('images').get(id)
    request.onsuccess = () => request.result instanceof Blob ? resolve(request.result) : reject(new Error('原图片附件不可用，请保留原请求并恢复本地附件。'))
    request.onerror = () => reject(request.error)
  })
  return blob.text()
}
