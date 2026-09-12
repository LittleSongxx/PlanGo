import { existsSync, readFileSync } from 'node:fs'

export function desktopInputHint(): string {
  if (process.platform !== 'linux') return ''
  const wsl = !!(process.env.WSL_DISTRO_NAME || process.env.WSL_INTEROP)
    || (existsSync('/proc/version') && /microsoft/i.test(readFileSync('/proc/version', 'utf8')))
  if (!wsl) return ''
  if (process.env.GTK_IM_MODULE || process.env.QT_IM_MODULE || process.env.XMODIFIERS || process.env.IBUS_ADDRESS) return ''
  return '这个窗口通过 WSL 显示，Windows 输入法不会弹出来。可以粘贴中文；若要直接打字，请在 WSL 安装并启动 ibus 或 fcitx。'
}
