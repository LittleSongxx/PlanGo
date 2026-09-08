// Main owns browser contents and execution; this bridge only projects trusted notifications.
import { useStore } from '../store'

export function installBrowserBridge(): () => void {
  const offState = window.plango.browser.onState(state => useStore.getState().applyBrowserState(state))
  const offActivity = window.plango.browser.onActivity(activity => {
    if (activity.active) useStore.getState().setView('browser')
    useStore.getState().setAiBrowsing(activity)
  })
  void useStore.getState().browserIntent({ kind: 'state' })
  return () => { offState(); offActivity() }
}
