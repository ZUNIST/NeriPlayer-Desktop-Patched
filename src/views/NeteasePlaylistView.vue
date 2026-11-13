<script setup lang="ts">
import { filterAndRank, trackSearchTokens } from '@/modules/search/textMatcher'
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePlayerStore, type TrackInfo } from '@/stores/player'
import { useAuthStore } from '@/stores/auth'
import { useDownloadStore } from '@/stores/download'
import { useToastStore } from '@/stores/toast'
import { createLogger } from '@/utils/logger'
import { useDelayedFlag } from '@/composables/useDelayedFlag'
import { useI18n } from 'vue-i18n'
import { invoke } from '@tauri-apps/api/core'
import AddToPlaylistDialog from '@/components/AddToPlaylistDialog.vue'
import BilibiliCoverImage from '@/components/BilibiliCoverImage.vue'
import CommentsPanel from '@/components/CommentsPanel.vue'
import ContextMenu from '@/components/ui/ContextMenu.vue'
import TrackSelectionToolbar from '@/components/TrackSelectionToolbar.vue'
import LocateTrackFab from '@/components/LocateTrackFab.vue'
import { useTrackSelection } from '@/composables/useTrackSelection'
import { useTrackActivation } from '@/composables/useTrackActivation'
import { useLocateCurrentTrack } from '@/composables/useLocateCurrentTrack'
import { useIncrementalList } from '@/composables/useIncrementalList'
import {
  createContextMenuItem,
  type ContextMenuActionItem,
  type ContextMenuItem,
} from '@/utils/contextMenu'
import {
  playlistDetailCacheKey,
  previewCachedDetail,
  writePlaylistDetailCache,
} from '@/modules/library/playlistDetailCache'
import { recordPlaylistOpen } from '@/modules/library/playlistUsage'
import { formatTrackDuration as formatDuration } from '@/utils/timeFormat'
import { resolveNeteaseCover } from '@/utils/neteaseCover'

const props = defineProps<{ isAlbum?: boolean }>()
const route = useRoute()
const router = useRouter()
const player = usePlayerStore()
const { onTrackClick, onTrackDoubleClick } = useTrackActivation()
const auth = useAuthStore()
const downloadStore = useDownloadStore()
const toast = useToastStore()
const log = createLogger('netease-playlist')
const { t, locale } = useI18n()

const isLoading = ref(true)
// 慢加载才显示 spinner，避免快速加载时一闪而过（UI-016）
const isLoadingSlow = useDelayedFlag(isLoading)
const error = ref<string | null>(null)
const playlistName = ref('')
const coverUrl = ref('')
const trackCount = ref(0)
const playCount = ref(0)
const description = ref('')
const creator = ref('')
/// 专辑艺人 id：只有专辑详情才有，用于「点歌手名跳转歌手页」
const creatorId = ref<number | null>(null)
const showComments = ref(false)
const searchQuery = ref('')

const tracks = ref<TrackInfo[]>([])
/// 当前详情的缓存键，移除曲目后需要就地更新缓存，避免返回时又看到已删的歌
const detailCacheKey = ref('')

interface NeteaseDetailCache {
  playlistName: string
  coverUrl: string
  trackCount: number
  playCount: number
  description: string
  creator: string
  /// 专辑艺人 id（旧缓存没有该字段时为 undefined，不影响读取）
  creatorId?: number | null
  tracks: TrackInfo[]
}

function applyDetailCache(cache: NeteaseDetailCache) {
  playlistName.value = cache.playlistName
  coverUrl.value = cache.coverUrl
  trackCount.value = cache.trackCount
  playCount.value = cache.playCount
  description.value = cache.description
  creator.value = cache.creator
  creatorId.value = cache.creatorId ?? null
  tracks.value = cache.tracks
}

function saveDetailCache(cacheKey: string) {
  writePlaylistDetailCache<NeteaseDetailCache>(cacheKey, {
    playlistName: playlistName.value,
    coverUrl: coverUrl.value,
    trackCount: trackCount.value,
    playCount: playCount.value,
    description: description.value,
    creator: creator.value,
    creatorId: creatorId.value,
    tracks: tracks.value,
  })
}

const filteredTracks = computed(() => filterAndRank(searchQuery.value, tracks.value, trackSearchTokens))

// 大歌单分块渲染（网易云歌单可达上千行，一次性渲染在 WebKitGTK 上卡顿）
const { visibleItems: visibleTracks, onScroll: onTrackListScroll, ensureIndex: ensureTrackIndex } =
  useIncrementalList(() => filteredTracks.value)

const {
  selectionMode,
  selectedIds,
  selectedItems: selectedTracks,
  visibleSelectedCount,
  allVisibleSelected,
  enterSelectionMode,
  leaveSelectionMode,
  toggleSelected,
  toggleSelectAllVisible,
  invertSelectionVisible,
} = useTrackSelection(tracks, filteredTracks)

// 总时长
const totalDuration = computed(() => {
  const totalMs = tracks.value.reduce((sum, t) => sum + (t.durationMs || 0), 0)
  return formatTotalDuration(totalMs)
})

// 定位到当前播放
const viewRef = ref<HTMLElement | null>(null)
const currentRowKey = computed(() => {
  const id = player.currentTrack?.id
  if (!id) return null
  return filteredTracks.value.some(item => item.id === id) ? id : null
})
const { fabVisible: locateFabVisible, locate: locateCurrentTrack } = useLocateCurrentTrack({
  containerRef: viewRef,
  currentKey: currentRowKey,
  suppressed: selectionMode,
  ensureRendered: key => {
    const index = filteredTracks.value.findIndex(item => item.id === key)
    return ensureTrackIndex(index)
  },
})

function formatTotalDuration(ms: number): string {
  const totalMin = Math.floor(ms / 60000)
  if (totalMin >= 60) {
    const h = Math.floor(totalMin / 60)
    const m = totalMin % 60
    return `${h}${t('common.hour_short')} ${m}${t('common.minute_short')}`
  }
  return `${totalMin}${t('common.minute_short')}`
}

async function loadDetail() {
  const id = Number(route.params.id)
  if (!id) return

  const cacheKey = playlistDetailCacheKey(
    props.isAlbum ? 'netease-album' : 'netease-playlist',
    id,
    auth.netease.loggedIn ? auth.netease.nickname : null,
  )
  isLoading.value = true
  error.value = null
  detailCacheKey.value = cacheKey
  const cached = previewCachedDetail<NeteaseDetailCache>(cacheKey, (detail) => {
    applyDetailCache(detail)
    isLoading.value = false
  })

  try {
    if (props.isAlbum) {
      const data = await invoke<any>('get_album_detail', { albumId: id })
      cached.markFresh()
      const album = data?.album || {}
      playlistName.value = album.name || ''
      const albumCover = resolveNeteaseCover(
        album.picUrl,
        album.blurPicUrl,
        album.picUrl_str,
        album.coverImgUrl,
        album.pic,
      )
      coverUrl.value = albumCover
      description.value = album.description || ''
      creator.value = album.artist?.name || ''
      creatorId.value = album.artist?.id ?? null

      const songs = data?.songs || []
      tracks.value = songs.map((s: any) => ({
        id: `netease:${s.id}`,
        title: s.name || '',
        artist: (s.ar || []).map((a: any) => a.name).join(', '),
        album: s.al?.name || album.name || '',
        durationMs: s.dt || 0,
        // 专辑曲目 al.picUrl 常为空，回退到专辑封面
        coverUrl: resolveNeteaseCover(s.al?.picUrl, s.al?.pic, albumCover) || albumCover,
        audioUrl: '',
        artistId: s.ar?.[0]?.id ?? album.artist?.id ?? undefined,
        albumId: s.al?.id ?? albumId.value ?? undefined,
        artists: (s.ar || []).map((a: any) => ({ id: a.id, name: a.name })),
      }))
      trackCount.value = tracks.value.length
    } else {
      const data = await invoke<any>('get_netease_playlist_detail', { playlistId: id })
      cached.markFresh()
      const pl = data?.playlist || {}
      playlistName.value = pl.name || ''
      coverUrl.value = resolveNeteaseCover(pl.coverImgUrl, pl.picUrl, pl.cover)
      trackCount.value = pl.trackCount || 0
      playCount.value = pl.playCount || 0
      description.value = pl.description || ''
      creator.value = pl.creator?.nickname || ''
      // 歌单作者是用户不是歌手，没有歌手页可跳
      creatorId.value = null

      const songs = pl.tracks || []
      tracks.value = songs.map((s: any) => ({
        id: `netease:${s.id}`,
        title: s.name || '',
        artist: (s.ar || []).map((a: any) => a.name).join(', '),
        album: s.al?.name || '',
        durationMs: s.dt || 0,
        coverUrl: resolveNeteaseCover(s.al?.picUrl, s.al?.pic),
        audioUrl: '',
        artistId: s.ar?.[0]?.id ?? undefined,
        albumId: s.al?.id ?? undefined,
        artists: (s.ar || []).map((a: any) => ({ id: a.id, name: a.name })),
      }))
    }
    saveDetailCache(cacheKey)
    recordOpen()
  } catch (e: any) {
    if (await cached.shown()) recordOpen()
    else error.value = e?.toString() || t('player.load_failed')
  } finally {
    isLoading.value = false
  }
}

function recordOpen() {
  recordPlaylistOpen({
    source: props.isAlbum ? 'neteaseAlbum' : 'netease',
    id: String(route.params.id),
    name: playlistName.value,
    coverUrl: coverUrl.value,
    trackCount: trackCount.value || tracks.value.length,
  })
}

function playAll() {
  if (tracks.value.length === 0) return
  player.playAll(tracks.value)
}

/// 专辑评论用的资源 id（歌单没有评论入口，故仅在专辑页给出）
const albumId = computed(() => (props.isAlbum ? Number(route.params.id) || null : null))

/// 专辑艺人页跳转
function openCreator() {
  if (!creatorId.value) return
  router.push({ name: 'netease-artist', params: { id: creatorId.value } })
}

/// 曲目行歌手名：单歌手直接跳转，多歌手弹出选择菜单（与播放页交互一致）
function openTrackArtistMenu(event: MouseEvent, track: TrackInfo) {
  const artists = (track.artists || []).filter(a => a.id)
  if (artists.length === 0) {
    if (track.artistId) router.push({ name: 'netease-artist', params: { id: track.artistId } })
    return
  }
  if (artists.length === 1) {
    router.push({ name: 'netease-artist', params: { id: artists[0].id } })
    return
  }
  artistMenu.value = {
    show: true,
    x: event.clientX,
    y: event.clientY,
    items: artists.map(artist =>
      createContextMenuItem(artist.name, { id: String(artist.id), icon: 'person' }),
    ),
  }
}

function handleArtistMenuClick(item: ContextMenuActionItem) {
  artistMenu.value.show = false
  const id = Number(item.id)
  if (Number.isSafeInteger(id) && id > 0) {
    router.push({ name: 'netease-artist', params: { id } })
  }
}

/// 曲目行专辑名跳转
function openAlbumPage(id: number) {
  router.push({ name: 'netease-album', params: { id } })
}

function shufflePlay() {
  if (tracks.value.length === 0) return
  player.shufflePlay(tracks.value)
}

function playTrack(track: TrackInfo, index: number) {
  if (selectionMode.value) {
    toggleSelected(track.id)
    return
  }
  player.playAll(filteredTracks.value, track.id)
}

function playSelected() {
  if (selectedTracks.value.length === 0) return
  player.playAll(selectedTracks.value)
  leaveSelectionMode()
}

function queueSelected() {
  for (const track of selectedTracks.value) player.addToQueueEnd(track)
  leaveSelectionMode()
}

// 紧凑计数随界面语言：中文 1.5亿 / 1.2万，英文 150M / 12K
const playCountFormat = computed(() => new Intl.NumberFormat(locale.value, { notation: 'compact', maximumFractionDigits: 1 }))
function formatPlayCount(count: number): string {
  return playCountFormat.value.format(count)
}

// 曲目右键菜单
const trackMenu = ref<{ show: boolean; x: number; y: number; track: TrackInfo | null }>({
  show: false, x: 0, y: 0, track: null,
})
/// 多歌手选择菜单：点曲目行的歌手名时弹出（单歌手直接跳转）
const artistMenu = ref<{ show: boolean; x: number; y: number; items: ContextMenuItem[] }>({
  show: false, x: 0, y: 0, items: [],
})
const showAddToPlaylist = ref(false)
const addToPlaylistTarget = ref<TrackInfo | null>(null)
const addToPlaylistTargets = ref<TrackInfo[]>([])

function openTrackMenu(e: MouseEvent, track: TrackInfo) {
  if (selectionMode.value) {
    toggleSelected(track.id)
    return
  }
  const btn = e.currentTarget as HTMLElement
  const rect = btn.getBoundingClientRect()
  let x = rect.left - 204
  if (x < 8) x = rect.right + 4
  trackMenu.value = { show: true, x, y: rect.top, track }
}

function openTrackContextMenu(e: MouseEvent, track: TrackInfo) {
  if (selectionMode.value) {
    toggleSelected(track.id)
    return
  }
  trackMenu.value = {
    show: true,
    x: e.clientX,
    y: e.clientY,
    track,
  }
}

function closeTrackMenu() {
  trackMenu.value.show = false
}

function openAddToPlaylist(track: TrackInfo) {
  closeTrackMenu()
  addToPlaylistTarget.value = track
  addToPlaylistTargets.value = []
  showAddToPlaylist.value = true
}

function openBatchAddToPlaylist() {
  if (selectedTracks.value.length === 0) return
  closeTrackMenu()
  addToPlaylistTarget.value = null
  addToPlaylistTargets.value = [...selectedTracks.value]
  leaveSelectionMode()
  showAddToPlaylist.value = true
}

function downloadSelected() {
  const targets = [...selectedTracks.value]
  leaveSelectionMode()
  for (const track of targets) void downloadStore.downloadTrack(track)
}

function addToQueueNext(track: TrackInfo) {
  closeTrackMenu()
  player.addToQueueNext(track)
}

function addToQueueEnd(track: TrackInfo) {
  closeTrackMenu()
  player.addToQueueEnd(track)
}

function downloadTaskStatusText(status?: string) {
  switch (status) {
    case 'resolving': return t('download.resolving')
    case 'downloading': return t('download.downloading')
    case 'cancelling': return t('download.cancelling')
    case 'cancelled': return t('download.cancelled')
    case 'error': return t('download.download_failed')
    case 'already_exists': return t('download.already_exists')
    default: return t('download.downloading')
  }
}

function trackDownloadLabel(track: TrackInfo) {
  const task = downloadStore.downloading.get(track.id)
  if (task) return downloadTaskStatusText(task.status)
  if (downloadStore.isDownloaded(track.id)) return t('download.redownload')
  return t('download.download')
}

function isTrackDownloadDisabled(track: TrackInfo) {
  return downloadStore.isDownloading(track.id)
}

async function handleTrackDownload(track: TrackInfo) {
  closeTrackMenu()
  if (isTrackDownloadDisabled(track)) return
  if (downloadStore.isDownloaded(track.id)) {
    await downloadStore.redownloadTrack(track)
  } else {
    await downloadStore.downloadTrack(track)
  }
}

const trackMenuItems = computed<ContextMenuItem[]>(() => {
  const track = trackMenu.value.track
  return [
    createContextMenuItem(t('common.multi_select'), { id: 'select', icon: 'checklist' }),
    createContextMenuItem(t('player.play_next'), { id: 'play-next', icon: 'queue_play_next' }),
    createContextMenuItem(t('player.add_to_queue'), { id: 'add-to-queue', icon: 'add_to_queue' }),
    createContextMenuItem(t('player.add_to_playlist'), { id: 'add-to-playlist', icon: 'playlist_add' }),
    createContextMenuItem(
      track ? trackDownloadLabel(track) : t('download.download'),
      {
        id: 'download',
        icon: 'download',
        disabled: !track || isTrackDownloadDisabled(track),
      },
    ),
    // 专辑页没有「从歌单移除」的概念，只在自己的歌单页提供
    ...(props.isAlbum
      ? []
      : [
          createContextMenuItem(t('library.remove_from_playlist'), {
            id: 'remove-from-playlist',
            icon: 'delete',
            danger: true,
          }),
        ]),
  ]
})

function handleTrackMenuClick(item: ContextMenuActionItem) {
  const track = trackMenu.value.track
  if (!track) return

  switch (item.id) {
    case 'select':
      closeTrackMenu()
      enterSelectionMode(track)
      break
    case 'play-next':
      addToQueueNext(track)
      break
    case 'add-to-queue':
      addToQueueEnd(track)
      break
    case 'add-to-playlist':
      openAddToPlaylist(track)
      break
    case 'download':
      void handleTrackDownload(track)
      break
    case 'remove-from-playlist':
      void removeTrackFromPlaylist(track)
      break
  }
}

/// 从当前网易云歌单移除单曲（仅自己的歌单可写，接口会校验归属）
async function removeTrackFromPlaylist(track: TrackInfo) {
  closeTrackMenu()
  const playlistId = Number(route.params.id)
  const match = /^netease:(\d+)$/.exec(track.id)
  if (!playlistId || !match) return
  try {
    await invoke('remove_tracks_from_netease_playlist', {
      playlistId,
      songIds: [Number(match[1])],
    })
    tracks.value = tracks.value.filter(item => item.id !== track.id)
    trackCount.value = Math.max(0, trackCount.value - 1)
    if (detailCacheKey.value) saveDetailCache(detailCacheKey.value)
    toast.success(t('library.removed_tracks', { count: 1 }))
  } catch (e) {
    log.error('Remove track from netease playlist failed:', e)
    toast.error(t('library.remove_tracks_failed'))
  }
}

onMounted(() => {
  downloadStore.initEvents()
  void downloadStore.loadDownloads()
  void loadDetail()
})
</script>

<template>
  <div ref="viewRef" class="detail-view" @scroll="onTrackListScroll">
    <header class="detail-header">
      <button class="back-btn" @click="router.back()">
        <span class="material-symbols-rounded">arrow_back</span>
      </button>
      <div class="header-search" v-if="!isLoading && tracks.length > 0">
        <span class="material-symbols-rounded search-icon">search</span>
        <input
          v-model="searchQuery"
          :placeholder="t('player.search_tracks')"
          class="search-input"
        />
      </div>
    </header>

    <!-- 加载状态：仅慢加载显示 spinner，短加载留空白避免闪现（UI-016） -->
    <div v-if="isLoading" class="state-center">
      <template v-if="isLoadingSlow">
        <span class="material-symbols-rounded spinning">progress_activity</span>
        <p>{{ t('player.loading') }}</p>
      </template>
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="state-center">
      <span class="material-symbols-rounded" style="font-size: 48px; opacity: 0.3">error</span>
      <p>{{ error }}</p>
      <button class="retry-btn" @click="loadDetail">{{ t('player.retry') }}</button>
    </div>

    <template v-else>
      <!-- 歌单 / 专辑 信息头 -->
      <div class="detail-hero">
        <div class="hero-cover">
          <BilibiliCoverImage v-if="coverUrl" :src="coverUrl">
            <span class="material-symbols-rounded filled" style="font-size: 48px; opacity: 0.3">queue_music</span>
          </BilibiliCoverImage>
          <span v-else class="material-symbols-rounded filled" style="font-size: 48px; opacity: 0.3">queue_music</span>
        </div>
        <div class="hero-info">
          <h1 class="hero-title">{{ playlistName }}</h1>
          <p v-if="creator" class="hero-creator">
            <span
              v-if="creatorId"
              class="creator-link"
              role="link"
              tabindex="0"
              :title="t('player.view_artist', { name: creator })"
              @click="openCreator"
              @keydown.enter="openCreator"
            >{{ creator }}</span>
            <template v-else>{{ creator }}</template>
          </p>
          <p class="hero-meta">
            {{ t('player.track_count', { count: trackCount }) }} · {{ totalDuration }}
            <span v-if="playCount"> · {{ formatPlayCount(playCount) }}</span>
          </p>
          <p v-if="description" class="hero-desc">{{ description }}</p>
          <div class="hero-actions">
            <button class="play-all-btn" @click="playAll">
              <span class="material-symbols-rounded filled">play_arrow</span>
              {{ t('player.play_all') }}
            </button>
            <button class="hero-icon-btn" :title="t('player.shuffle_play')" @click="shufflePlay">
              <span class="material-symbols-rounded">shuffle</span>
            </button>
            <button class="hero-icon-btn" :title="t('common.multi_select')" @click="enterSelectionMode()">
              <span class="material-symbols-rounded">checklist</span>
            </button>
            <!-- 专辑评论入口：图标与播放页评论按钮保持一致 -->
            <button
              v-if="isAlbum"
              class="hero-icon-btn"
              :title="t('player.comments')"
              :aria-label="t('player.comments')"
              @click="showComments = true"
            >
              <span class="material-symbols-rounded">chat_bubble</span>
            </button>
          </div>
        </div>
      </div>

      <!-- 歌曲列表 -->
      <div v-if="filteredTracks.length === 0" class="state-center">
        <p>{{ searchQuery.trim() && tracks.length > 0 ? t('player.no_results') : t('player.empty_playlist') }}</p>
      </div>
      <div v-else>
        <TrackSelectionToolbar
          v-if="selectionMode"
          :selected-count="selectedTracks.length"
          :visible-selected-count="visibleSelectedCount"
          :all-visible-selected="allVisibleSelected"
          @select-all="toggleSelectAllVisible"
          @invert-selection="invertSelectionVisible"
          @play="playSelected"
          @queue="queueSelected"
          @playlist="openBatchAddToPlaylist"
          @download="downloadSelected"
          @exit="leaveSelectionMode"
        />
        <div class="track-list">
        <div
          v-for="(track, index) in visibleTracks"
          :key="track.id"
          class="track-item"
          :class="{ active: player.currentTrack?.id === track.id, selected: selectionMode && selectedIds.has(track.id), 'selection-mode': selectionMode }"
          :data-track-key="track.id"
          @click="onTrackClick($event, () => playTrack(track, index), selectionMode)"
          @dblclick="onTrackDoubleClick($event, () => playTrack(track, index), selectionMode)"
          @pointerenter="player.prefetchIntent(track)"
          @focusin="player.prefetchIntent(track)"
          @contextmenu.prevent.stop="openTrackContextMenu($event, track)"
        >
          <button v-if="selectionMode" class="track-select" @click.stop="onTrackClick($event, () => toggleSelected(track.id), true)">
            <span class="material-symbols-rounded filled">{{ selectedIds.has(track.id) ? 'check_circle' : 'radio_button_unchecked' }}</span>
          </button>
          <div v-else class="track-index">
            <div v-if="player.currentTrack?.id === track.id && player.isPlaying" class="equalizer-bars"><span class="bar"/><span class="bar"/><span class="bar"/></div>
            <span v-else class="index-num">{{ index + 1 }}</span>
          </div>
          <div class="track-cover">
            <BilibiliCoverImage
              v-if="track.coverUrl || (props.isAlbum && coverUrl)"
              :src="track.coverUrl || coverUrl"
              loading="lazy"
            />
            <span v-else class="material-symbols-rounded filled">music_note</span>
          </div>
          <div class="track-info">
            <div class="track-title">{{ track.title }}</div>
            <div class="track-meta">
              <span
                class="meta-link"
                :class="{ 'meta-link--static': !track.artistId }"
                @click.stop="openTrackArtistMenu($event, track)"
              >{{ track.artist }}</span>
              <template v-if="track.album">
                <span class="meta-sep"> · </span>
                <span
                  class="meta-link"
                  :class="{ 'meta-link--static': !track.albumId }"
                  @click.stop="track.albumId && openAlbumPage(track.albumId)"
                >{{ track.album }}</span>
              </template>
            </div>
          </div>
          <div class="track-duration">{{ formatDuration(track.durationMs) }}</div>
          <button v-if="!selectionMode" class="track-more" @click.stop="openTrackMenu($event, track)">
            <span class="material-symbols-rounded">more_vert</span>
          </button>
        </div>
        </div>
      </div>
    </template>

    <LocateTrackFab
      :visible="locateFabVisible"
      :label="t('player.locate_current')"
      @click="locateCurrentTrack"
    />

    <ContextMenu
      :open="trackMenu.show"
      :x="trackMenu.x"
      :y="trackMenu.y"
      :items="trackMenuItems"
      @update:open="trackMenu.show = $event"
      @click="handleTrackMenuClick"
    />

    <ContextMenu
      :open="artistMenu.show"
      :x="artistMenu.x"
      :y="artistMenu.y"
      :items="artistMenu.items"
      @update:open="artistMenu.show = $event"
      @click="handleArtistMenuClick"
    />

    <AddToPlaylistDialog v-model:open="showAddToPlaylist" :track="addToPlaylistTarget" :tracks="addToPlaylistTargets" />
    <CommentsPanel v-model:open="showComments" :track="null" :album-id="albumId" />
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/detail-view.scss' as *;

.creator-link {
  cursor: pointer;
  transition: color var(--duration-short);

  &:hover,
  &:focus-visible {
    color: var(--md-primary);
    text-decoration: underline;
  }
}

/* 曲目行的歌手/专辑：有 id 才可点，否则保持纯文本外观 */
.meta-link {
  cursor: pointer;
  transition: color var(--duration-short);

  &:hover {
    color: var(--md-primary);
    text-decoration: underline;
  }

  &.meta-link--static {
    cursor: inherit;
    pointer-events: none;
  }
}

.meta-sep {
  white-space: pre;
}

.track-more {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-full);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--md-on-surface-variant);
  opacity: 0;
  transition: opacity var(--duration-short), background var(--duration-short);

  .track-item:hover & { opacity: 0.6; }
  &:hover { opacity: 1 !important; background: var(--md-surface-container-high); }
  .material-symbols-rounded { font-size: 18px; }
}
</style>
