import { ref, watch, type WatchStopHandle } from 'vue'
import { invoke, isTauri } from '@tauri-apps/api/core'
import { getCurrentWebview } from '@tauri-apps/api/webview'
import { currentMonitor, getCurrentWindow, LogicalSize, PhysicalPosition } from '@tauri-apps/api/window'
import { normalizeUiScale, UI_SCALE_DEFAULT, UI_SCALE_MIN, UI_SCALE_MAX, UI_SCALE_STEP } from '@/stores/settings'
import { isMacPlatform } from '@/modules/shortcuts/platform'
import { createLogger } from '@/utils/logger'

const log = createLogger('ui-scale')
export const uiScaleSupported = ref(false)
export const uiScaleError = ref(false)
export const uiScaleMax = ref(UI_SCALE_DEFAULT)

export const UI_LAYOUT_MIN_WIDTH = 960
export const UI_LAYOUT_MIN_HEIGHT = 720
const MAC_TITLEBAR_HEIGHT = 52

export function uiScaleLimitForSize(width: number, height: number, fixedTitlebarHeight = 0): number {
  const heightCapacity = height < UI_LAYOUT_MIN_HEIGHT
    ? (height - fixedTitlebarHeight) / (UI_LAYOUT_MIN_HEIGHT - fixedTitlebarHeight)
    : height / UI_LAYOUT_MIN_HEIGHT
  const capacity = Math.min(width / UI_LAYOUT_MIN_WIDTH, heightCapacity) * 100
  if (!Number.isFinite(capacity)) return UI_SCALE_MIN
  return Math.max(UI_SCALE_MIN, Math.min(UI_SCALE_MAX, Math.floor(capacity / UI_SCALE_STEP) * UI_SCALE_STEP))
}

export async function installUiScale(settings: { uiScale: number }): Promise<WatchStopHandle> {
  if (!isTauri()) return () => {}
  try {
    // 旧版 macOS 没有页面缩放 API，调用之前先由原生层检查
    uiScaleSupported.value = await invoke<boolean>('ui_scale_supported')
  } catch (error) {
    uiScaleError.value = true
    log.warn('Could not check UI scaling support:', error)
    return () => {}
  }
  if (!uiScaleSupported.value) return () => {}

  let active = true
  let appliedScale = UI_SCALE_DEFAULT
  let hasAppliedScale = false
  let pending = Promise.resolve()
  let minimumScale = 0
  let minimumDpi = 0
  let minimumInset = -1
  let titlebarInset = 0
  let refreshTimer: ReturnType<typeof setTimeout> | undefined
  const appWindow = getCurrentWindow()
  const unlisten: Array<() => void> = []

  // resizeWindow 只给「用户主动改缩放」用。窗口 resize 触发的刷新绝不能 setSize：
  // 最大化/还原快速切换时，查询到的 maximized 与真正执行 setSize 时的窗口状态会错配，
  // Windows 对最大化窗口 setSize 会直接取消最大化并停在普通尺寸（表现为窗口突然变小）。
  function apply(value: number, resizeWindow = false): Promise<void> {
    pending = pending.then(async () => {
      if (!active) return
      const [inner, outer, dpi, monitor, maximized, fullscreen] = await Promise.all([
        appWindow.innerSize(), appWindow.outerSize(), appWindow.scaleFactor(), currentMonitor(), appWindow.isMaximized(), appWindow.isFullscreen(),
      ])
      if (!active || inner.width <= 0 || inner.height <= 0 || dpi <= 0) return
      const frameWidth = Math.max(0, outer.width - inner.width)
      const frameHeight = Math.max(0, outer.height - inner.height)
      const availableWidth = monitor ? (monitor.workArea.size.width - frameWidth) / dpi : inner.width / dpi
      const availableHeight = monitor ? (monitor.workArea.size.height - frameHeight) / dpi : inner.height / dpi
      // 缩放上限只看显示器工作区，不要掺入 outer-inner：最大化时 Windows 的不可见边框
      // 会让 frameWidth/frameHeight 变化，进而改变 percent、改写 settings.uiScale，
      // 触发 watch → resizeWindow → setSize，又回到窗口被拉回普通尺寸的问题。
      const limitWidth = monitor ? monitor.workArea.size.width / dpi : availableWidth
      const limitHeight = monitor ? monitor.workArea.size.height / dpi : availableHeight
      titlebarInset = isMacPlatform && !fullscreen ? MAC_TITLEBAR_HEIGHT : 0
      uiScaleMax.value = uiScaleLimitForSize(limitWidth, limitHeight, titlebarInset)
      const percent = Math.min(normalizeUiScale(value), uiScaleMax.value)
      if (settings.uiScale === value && value !== percent) settings.uiScale = percent
      const factor = percent / 100
      const minWidth = UI_LAYOUT_MIN_WIDTH * factor
      const minHeight = UI_LAYOUT_MIN_HEIGHT * factor + Math.max(0, titlebarInset * (1 - factor))
      // 缩放后仍保留完整布局，禁止把原生窗口拖小到控件之外
      if (minimumScale !== percent || minimumDpi !== dpi || minimumInset !== titlebarInset) {
        await appWindow.setMinSize(new LogicalSize(minWidth, minHeight))
        if (!active) return
        minimumScale = percent
        minimumDpi = dpi
        minimumInset = titlebarInset
      }
      if (resizeWindow && !maximized && !fullscreen) {
        // 二次确认：上面一组快照与这里之间隔着多次 IPC 往返，窗口状态可能已经变了。
        // 用此刻的状态与尺寸重算，状态变了就放弃这次尺寸修正（缩放本身仍继续应用）。
        const [stillMaximized, stillFullscreen, currentInner] = await Promise.all([
          appWindow.isMaximized().catch(() => true),
          appWindow.isFullscreen().catch(() => true),
          appWindow.innerSize(),
        ])
        if (!active) return
        if (!stillMaximized && !stillFullscreen && currentInner.width > 0 && currentInner.height > 0) {
          const width = Math.min(availableWidth, Math.max(currentInner.width / dpi, minWidth))
          const height = Math.min(availableHeight, Math.max(currentInner.height / dpi, minHeight))
          if (Math.abs(width - currentInner.width / dpi) >= 1 || Math.abs(height - currentInner.height / dpi) >= 1) {
            await appWindow.setSize(new LogicalSize(width, height))
            if (!active) return
            if (monitor) {
              const position = await appWindow.outerPosition()
              if (!active) return
              const left = monitor.workArea.position.x
              const top = monitor.workArea.position.y
              const x = Math.max(left, Math.min(position.x, left + monitor.workArea.size.width - width * dpi - frameWidth))
              const y = Math.max(top, Math.min(position.y, top + monitor.workArea.size.height - height * dpi - frameHeight))
              if (x !== position.x || y !== position.y) await appWindow.setPosition(new PhysicalPosition(x, y))
              if (!active) return
            }
          }
        }
      }
      if (hasAppliedScale && percent === appliedScale) return
      await getCurrentWebview().setZoom(factor)
      if (!active) return
      appliedScale = percent
      hasAppliedScale = true
      document.documentElement.style.setProperty('--app-ui-scale', String(factor))
      uiScaleError.value = false
    }).catch(async error => {
      if (!active) return
      uiScaleError.value = true
      log.warn('Could not apply UI scale:', error)
      try {
        const factor = appliedScale / 100
        await appWindow.setMinSize(new LogicalSize(UI_LAYOUT_MIN_WIDTH * factor, UI_LAYOUT_MIN_HEIGHT * factor + Math.max(0, titlebarInset * (1 - factor))))
        if (!active) return
        minimumScale = appliedScale
        minimumDpi = 0
        minimumInset = -1
      } catch (restoreError) {
        log.warn('Could not restore UI scaling constraints:', restoreError)
      }
      if (!active) return
      if (settings.uiScale === value) settings.uiScale = appliedScale
    })
    return pending
  }

  function scheduleRefresh() {
    if (refreshTimer) clearTimeout(refreshTimer)
    refreshTimer = setTimeout(() => {
      refreshTimer = undefined
      // 只刷新缩放上限与 minSize；窗口尺寸交给原生状态恢复/用户拖动
      void apply(settings.uiScale, false)
    }, 100)
  }

  // 启动时不要碰窗口尺寸：此时 Rust 侧正在恢复持久化的窗口状态
  await apply(settings.uiScale, false)
  const stop = watch(() => settings.uiScale, value => { void apply(value, true) })
  try {
    unlisten.push(await appWindow.onResized(scheduleRefresh))
    unlisten.push(await appWindow.onMoved(scheduleRefresh))
    unlisten.push(await appWindow.onScaleChanged(scheduleRefresh))
  } catch (error) {
    uiScaleError.value = true
    log.warn('Could not watch UI scaling constraints:', error)
  }
  return () => {
    active = false
    if (refreshTimer) clearTimeout(refreshTimer)
    for (const release of unlisten) release()
    stop()
  }
}
