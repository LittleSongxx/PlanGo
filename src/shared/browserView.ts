export interface BrowserTabState {
  id: string
  url: string
  title: string
  loading: boolean
  canGoBack: boolean
  canGoForward: boolean
  zoom: number
  popup: boolean
  error?: string
  previewProtected?: boolean
}
export interface BrowserViewState { seq: number; activeTabId: string | null; tabs: BrowserTabState[] }
export interface BrowserLayout { x: number; y: number; width: number; height: number; visible: boolean }
export interface BrowserActivity { active: boolean; action?: string; site?: string }
export type BrowserIntent = { kind: 'state' } | { kind: 'create'; url: string }
  | { kind: 'navigate'; id: string; url: string } | { kind: 'zoom'; id: string; factor: number }
  | { kind: 'activate' | 'close' | 'back' | 'forward' | 'reload' | 'focus'; id: string }
