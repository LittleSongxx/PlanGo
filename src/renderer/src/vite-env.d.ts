/// <reference types="vite/client" />
import type { AgentReply, OutcomeCard, AgentStep, HarnessApi, HarnessEvent, ReminderApi } from '@shared/types'
import type { LocationInfo, GeoLocationResult } from '@shared/location'
import type { BrowserIntent, BrowserLayout, BrowserViewState, BrowserActivity } from '@shared/browserView'

interface PlangoApi {
  desktopReady: () => Promise<void>
  harness: HarnessApi
  reminders: ReminderApi
  onHarnessEvent: (cb: (event: HarnessEvent) => void) => () => void
  chat: (message: string, history: unknown[]) => Promise<AgentReply>
  confirm: (token: string, ok: boolean) => Promise<AgentReply>
  onStep: (cb: (s: AgentStep & { patch?: boolean }) => void) => () => void
  onCard: (cb: (c: OutcomeCard) => void) => () => void
  onProactive: (cb: (p: { id: string; ts: number; text: string; kind: string }) => void) => () => void
  onImIncoming: (cb: (m: { from: string; text: string; ts: number }) => void) => () => void
  browser: { request: (intent: BrowserIntent) => Promise<BrowserViewState>; layout: (value: BrowserLayout) => Promise<void>;
    onState: (cb: (value: BrowserViewState) => void) => () => void; onActivity: (cb: (value: BrowserActivity) => void) => () => void }
  getConfig: () => Promise<{ config: any; cities: { city: string; count: number }[] }>
  setConfig: (patch: any) => Promise<any>
  setSource: (source: string) => Promise<string>
  pingLlm: () => Promise<{ ok: boolean; message: string }>
  listSkills: () => Promise<{ id: string; name: string; description: string; enabled: boolean }[]>
  toggleSkill: (id: string, enabled: boolean) => Promise<{ id: string; name: string; description: string; enabled: boolean }[]>
  imStatus: () => Promise<{ connected: boolean; note: string }>
  imLoginQr: () => Promise<{ dataUrl: string; note: string }>
  imSimulate: (text: string, from?: string) => Promise<{ ok: boolean }>
  proactiveList: () => Promise<{ reminders: unknown[]; history: unknown[] }>
  proactiveTrigger: () => Promise<{ id: string; ts: number; text: string; kind: string }>
  getMemory: () => Promise<any>
  memoryGreeting: () => Promise<{ text: string }>
  memoryDelete: (payload: { kind: 'pref' | 'fav' | 'episode'; value: string }) => Promise<any>
  memorySave: (text: string, polarity: 'like' | 'dislike') => Promise<any>
  memoryClear: () => Promise<any>
  shareCreate: (payload: { plan?: any; city?: string }) => Promise<{ ok: boolean; error?: string; id?: string; url?: string; qr?: string }>
  shareFeedback: (id: string) => Promise<{ found: boolean; views: number; tally: { up: number; meh: number; down: number }; prefs: { member: string; idea: string; budget?: number; ts: number }[]; mergeInstruction: string }>
  guideSetImage: (dataUrl: string) => Promise<{ ok: boolean }>
  discoverFetch: (request?: { city?: string; refresh?: boolean }) => Promise<{ city: string; groups: import('@shared/types').DiscoverGroup[]; source: import('@shared/types').SourceTag; scope: 'around' | 'city'; observed_at: string; expires_at: string; cache_hit: boolean }>
  dealsFetch: (city?: string) => Promise<{ city: string; items: { poi: import('@shared/types').POISummary; deal: import('@shared/types').DealRow }[]; source: import('@shared/types').SourceTag }>
  getLocation: () => Promise<LocationInfo>
  detectLocation: () => Promise<LocationInfo>
  setCity: (city: string) => Promise<LocationInfo>
  onLocation: (cb: (l: LocationInfo) => void) => () => void
  reportLocation: (p: LocationInfo & { userInitiated?: boolean }) => Promise<LocationInfo>
  geo: { geocode: (request: { address: string; city?: string }) => Promise<GeoLocationResult>; reverse: (request: { longitude: number; latitude: number }) => Promise<GeoLocationResult> }
  getAmapJsConfig: () => Promise<{ jsKey: string; jsSecurity: string }>
  openExternal: (url: string) => Promise<boolean>
}

declare global {
  interface Window {
    plango: PlangoApi
    AMap?: any
    _AMapSecurityConfig?: { securityJsCode: string }
  }
}
