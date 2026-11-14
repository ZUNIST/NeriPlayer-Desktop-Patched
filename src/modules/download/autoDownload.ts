import { invoke } from '@tauri-apps/api/core'
import type { TrackInfo } from '@/stores/player'
import { toBackendTrack } from '@/modules/lyrics/syncTrackPayload'
import { useDownloadStore } from '@/stores/download'
import { useSettingsStore } from '@/stores/settings'
import { createLogger } from '@/utils/logger'

const log = createLogger('autoDownload')

const QUOTA_STORAGE_KEY = 'neriplayer.autodownload.quota'

/**
 * 「常听」的平均听完整度门槛。
 * 累计收听时长 ÷ (播放次数 × 单曲时长) 低于这个值，说明大多是开播就切，
 * 不按常听处理。刻意不做成设置项：它属于判定口径而不是偏好，
 * 暴露给用户只会让人困惑该填多少。
 */
const MIN_COMPLETION = 0.6

interface DailyQuota {
  date: string
  count: number
}

interface AutoDownloadCandidate {
  qualified: boolean
  recentPlays: number
  recentListenMs: number
  completion: number
  reason: string
}

function localDateKey(date = new Date()): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function readQuota(): DailyQuota {
  const today = localDateKey()
  try {
    const raw = localStorage.getItem(QUOTA_STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<DailyQuota>
      if (parsed.date === today && typeof parsed.count === 'number' && Number.isFinite(parsed.count)) {
        return { date: today, count: Math.max(0, Math.floor(parsed.count)) }
      }
    }
  } catch (error) {
    // 额度存储脏了按 0 处理：读不出来最多是多下一首，不该让功能整个失效
    log.warn('read auto download quota failed:', error)
  }
  return { date: today, count: 0 }
}

function writeQuota(quota: DailyQuota): void {
  try {
    localStorage.setItem(QUOTA_STORAGE_KEY, JSON.stringify(quota))
  } catch (error) {
    log.warn('write auto download quota failed:', error)
  }
}

/** 今日已自动下载的曲目数，供设置页展示 */
export function getAutoDownloadQuota(): DailyQuota {
  return readQuota()
}

function bumpQuota(): void {
  const quota = readQuota()
  quota.count += 1
  writeQuota(quota)
}

/**
 * 播放结束后判断这首是否够得上「常听」，够就自动下载。
 *
 * 调用点在 playbackStats 上报之后：统计写入成功才有意义，而且那里已经确立了
 * 「统计链路出问题不能影响播放」的约定，这里同样全程静默失败。
 *
 * 只处理网易云：B 站 / YouTube 的 URL 解析和版权路径差异较大，需要单独评估，
 * 混进来会让失败原因变得难以排查。
 */
export async function maybeAutoDownloadFrequent(track: TrackInfo | null): Promise<void> {
  if (!track?.id) return

  const settings = useSettingsStore()
  if (!settings.autoDownloadFrequent) return
  if (!track.id.startsWith('netease:')) return

  const downloads = useDownloadStore()
  if (downloads.isDownloaded(track.id) || downloads.isDownloading(track.id)) return

  const quota = readQuota()
  if (quota.count >= settings.autoDownloadDailyLimit) return

  try {
    const candidate = await invoke<AutoDownloadCandidate>('check_auto_download_candidate', {
      track: toBackendTrack(track),
      windowDays: settings.autoDownloadWindowDays,
      minPlays: settings.autoDownloadMinPlays,
      minCompletion: MIN_COMPLETION,
    })
    if (!candidate.qualified) return
    log.info(
      `auto download "${track.title}": plays=${candidate.recentPlays} completion=${candidate.completion.toFixed(2)}`,
    )
    // 下载本身有自己的去重与失败重试，这里只负责把任务交出去
    await downloads.downloadTrack(track)
    bumpQuota()
  } catch (error) {
    log.warn('auto download check failed:', error)
  }
}
