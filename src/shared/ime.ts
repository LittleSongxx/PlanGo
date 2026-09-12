/** IME composition uses Enter/Space to confirm candidates; those keys must not submit the field. */
export function isImeComposing(event: { isComposing?: boolean; keyCode?: number; nativeEvent?: { isComposing?: boolean; keyCode?: number } }): boolean {
  return event.isComposing === true || event.nativeEvent?.isComposing === true || event.keyCode === 229 || event.nativeEvent?.keyCode === 229
}
