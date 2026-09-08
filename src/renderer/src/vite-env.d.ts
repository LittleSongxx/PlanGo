/// <reference types="vite/client" />
import type { AgentReply, OutcomeCard, AgentStep, HarnessApi, HarnessEvent, ReminderApi } from '@shared/types'

interface XiaonianApi {
  harness: HarnessApi
  reminders: ReminderApi
  onHarnessEvent: (cb: (event: HarnessEvent) => void) => () => void
  chat: (message: string, history: unknown[]) => Promise<AgentReply>
  confirm: (token: string, ok: boolean) => Promise<AgentReply>
  onStep: (cb: (s: AgentStep & { patch?: boolean }) => void) => () => void
  onCard: (cb: (c: OutcomeCard) => void) => () => void
  onProactive: (cb: (p: { id: string; ts: number; text: string; kind: string }) => void) => () => void
  onImIncoming: (cb: (m: { from: string; text: string; ts: number }) => void) => () => void
  onBrowserExec: (cb: (p: { id: number; action: string; args: Record<string, unknown> }) => void) => void
  browserExecResult: (id: number, result: unknown) => void
  browserEval: (contentsId: number, code: string) => Promise<unknown>
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
  memoryDelete: (payload: { kind: 'pref' | 'fav'; value: string }) => Promise<any>
  memoryClear: () => Promise<any>
  shareCreate: (payload: { plan?: any; city?: string }) => Promise<{ ok: boolean; error?: string; id?: string; url?: string; qr?: string }>
  shareFeedback: (id: string) => Promise<{ found: boolean; views: number; tally: { up: number; meh: number; down: number }; prefs: { member: string; idea: string; budget?: number; ts: number }[]; mergeInstruction: string }>
  guideSetImage: (dataUrl: string) => Promise<{ ok: boolean }>
  discoverFetch: (city?: string) => Promise<{ city: string; groups: import('@shared/types').DiscoverGroup[]; source: import('@shared/types').SourceTag }>
  dealsFetch: (city?: string) => Promise<{ city: string; items: { poi: import('@shared/types').POISummary; deal: import('@shared/types').DealRow }[]; source: import('@shared/types').SourceTag }>
  getLocation: () => Promise<{ city: string; province?: string; source: string; coords?: string; district?: string }>
  detectLocation: () => Promise<{ city: string; province?: string; source: string; coords?: string; district?: string }>
  setCity: (city: string) => Promise<{ city: string; source: string; coords?: string; district?: string }>
  onLocation: (cb: (l: { city: string; province?: string; source: string; coords?: string; district?: string }) => void) => () => void
  reportLocation: (p: { city?: string; coords?: string }) => Promise<unknown>
  getAmapJsConfig: () => Promise<{ jsKey: string; jsSecurity: string; webKey: string }>
  openExternal: (url: string) => Promise<boolean>
}

declare global {
  interface Window {
    xiaonian: XiaonianApi
    AMap?: any
    _AMapSecurityConfig?: { securityJsCode: string }
  }
  namespace JSX {
    interface IntrinsicElements {
      webview: React.DetailedHTMLProps<
        React.HTMLAttributes<HTMLElement> & {
          src?: string
          partition?: string
          allowpopups?: string
          useragent?: string
          ref?: React.Ref<HTMLElement>
        },
        HTMLElement
      >
    }
  }
}
