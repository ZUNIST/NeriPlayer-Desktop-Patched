/**
 * 播放页封面底色 → 全局浅色页面的淡色染色。
 *
 * 单独取色而不是复用 colorExtractor.ts：浅色模式下那条路径传 isDark=false，
 * computeAccentBg 会给出 targetL=0.90 的近白色，和播放页的深色底不是一回事。
 * 这里固定用 isDark=true，并复用 NowPlaying 原先的压暗算法，保证两处颜色一致。
 *
 * NowPlaying.vue 是 v-if="isNowPlayingOpen"，播放页收起后组件卸载，
 * 所以首页不能依赖它取色；由 App.vue 的常驻 watch 调用本模块。
 */
import { extractPalette, type PaletteResult } from '@/utils/paletteExtractor'
import {
  normalizeCoverUrlForDisplay,
  normalizeProxiedCoverUrl,
  peekCoverImage,
  resolveCoverImage,
} from '@/utils/bilibiliCover'

type AccentBg = PaletteResult['accentBg']

const AMBIENT_COVER_SIZE = 320
const AMBIENT_PROP = '--np-ambient-bg'

let requestToken = 0

/** 播放页 accentBgStyle 的原始算法：过亮时向中性深色混合，保留色相 */
export function resolveNowPlayingAccentBg(bg: AccentBg): string {
  const [r, g, b] = bg
  const luma = (r * 0.299 + g * 0.587 + b * 0.114) / 255
  if (luma <= 0.34) return `rgb(${r}, ${g}, ${b})`
  const t = Math.min(1, (luma - 0.34) / 0.4)
  const mix = 0.35 + t * 0.45
  return `rgb(${Math.round(r * (1 - mix) + 18 * mix)}, ${Math.round(g * (1 - mix) + 18 * mix)}, ${Math.round(b * (1 - mix) + 18 * mix)})`
}

function publish(color: string | null) {
  const root = document.documentElement
  if (color) {
    root.style.setProperty(AMBIENT_PROP, color)
    root.classList.add('ambient-tint-ready')
  } else {
    root.style.removeProperty(AMBIENT_PROP)
    root.classList.remove('ambient-tint-ready')
  }
}

/** 关闭开关 / 停止播放 / 无封面时清掉变量，CSS 会回退成纯白 */
export function clearAmbientBg() {
  requestToken++
  publish(null)
}

/** 从当前封面取色并发布到 :root；失败时静默回退，不影响播放 */
export async function refreshAmbientFromCover(rawUrl: string) {
  const token = ++requestToken
  const proxied = normalizeProxiedCoverUrl(rawUrl)
  let src = proxied ? (peekCoverImage(proxied) || '') : ''
  if (proxied && !src) {
    try {
      src = await resolveCoverImage(proxied)
    } catch {
      src = ''
    }
  }
  if (!src) src = normalizeCoverUrlForDisplay(rawUrl)
  if (!src) {
    if (token === requestToken) publish(null)
    return
  }
  if (token !== requestToken) return

  const image = new Image()
  image.crossOrigin = 'anonymous'
  image.src = src
  try {
    await image.decode()
  } catch {
    if (token === requestToken) publish(null)
    return
  }
  if (token !== requestToken) return

  const canvas = document.createElement('canvas')
  canvas.width = AMBIENT_COVER_SIZE
  canvas.height = AMBIENT_COVER_SIZE
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  if (!ctx) {
    publish(null)
    return
  }
  ctx.drawImage(image, 0, 0, AMBIENT_COVER_SIZE, AMBIENT_COVER_SIZE)
  let data: ImageData
  try {
    data = ctx.getImageData(0, 0, AMBIENT_COVER_SIZE, AMBIENT_COVER_SIZE)
  } catch {
    // 跨域污染 canvas：拿不到像素就退回白底
    publish(null)
    return
  }
  const palette = extractPalette(data, 16, true)
  if (token !== requestToken) return
  publish(resolveNowPlayingAccentBg(palette.accentBg))
}
