// Main owns browser contents and execution; this bridge only projects trusted notifications.
import { useStore } from '../store'

export function installBrowserBridge(): () => void {
  const offState = window.plango.browser.onState(state => useStore.getState().applyBrowserState(state))
  const offActivity = window.plango.browser.onActivity(activity => {
    // Attention instead of a forced switch: the AI working is worth a marker on the
    // browser entry, but pulling the view away from whoever is reading is not.
    if (activity.active) useStore.getState().setBrowserAttention(true)
    useStore.getState().setAiBrowsing(activity)
  })
  void useStore.getState().browserIntent({ kind: 'state' })
  return () => { offState(); offActivity() }
}
