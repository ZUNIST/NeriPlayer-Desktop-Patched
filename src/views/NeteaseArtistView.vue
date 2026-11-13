<script setup lang="ts">
import { filterAndRank, searchValue, trackSearchTokens } from '@/modules/search/textMatcher'
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { invoke } from '@tauri-apps/api/core'
import { usePlayerStore, type TrackInfo } from '@/stores/player'
import { useTrackActivation } from '@/composables/useTrackActivation'
import BilibiliCoverImage from '@/components/BilibiliCoverImage.vue'
import TrackContextMenu from '@/components/TrackContextMenu.vue'
import CollectionContextMenu from '@/components/CollectionContextMenu.vue'
import { collectionWebUrl } from '@/utils/collectionLinks'
import {
  playlistDetailCacheKey,
  previewCachedDetail,
  writePlaylistDetailCache,
} from '@/modules/library/playlistDetailCache'
import { formatTrackDuration as formatDuration } from '@/utils/timeFormat'
import { resolveNeteaseCover, resolveNeteaseCoverSized } from '@/utils/neteaseCover'
import { createLogger } from '@/utils/logger'
import { useToastStore } from '@/stores/toast'
import { useArtistFavorite } from '@/modules/library/favoriteArtistState'

const log = createLogger('netease-artist-view')

const route = useRoute()
const router = useRouter()
const player = usePlayerStore()
const { onTrackClick, onTrackDoubleClick } = useTrackActivation()
const { t } = useI18n()
const toast = useToastStore()

interface ArtistHeader {
  name: string
  alias: string
  coverUrl: string
  avatarUrl: string
  briefDesc: string
  musicSize: number
  albumSize: number
}

interface ArtistAlbum {
  id: number
  name: string
  coverUrl: string
  publishYear: string
  size: number
}

interface ArtistDetailCache {
  header: ArtistHeader
  tracks: TrackInfo[]
  albums: ArtistAlbum[]
}

const isLoading = ref(true)
const error = ref<string | null>(null)
const header = ref<ArtistHeader | null>(null)
const tracks = ref<TrackInfo[]>([])
const albums = ref<ArtistAlbum[]>([])
const activeTab = ref<'songs' | 'albums'>('songs')
const query = ref('')
const trackMenuRef = ref<InstanceType<typeof TrackContextMenu> | null>(null)
const search = computed(() => query.value.trim())
const filteredTracks = computed(() => filterAndRank(query.value, tracks.value, trackSearchTokens))
const filteredAlbums = computed(() => filterAndRank(query.value, albums.value, album =>
  [searchValue(album.name, 0), searchValue(album.publishYear, 6)]))

const artistId = computed(() => Number(route.params.id) || 0)
let generation = 0
const { following, changing, toggle } = useArtistFavorite(computed(() => ({
  source: 'neteaseArtist', id: String(artistId.value),
  name: header.value?.name || String(route.query.name || ''),
  coverUrl: header.value?.avatarUrl || header.value?.coverUrl || String(route.query.cover || ''),
  trackCount: header.value?.musicSize || tracks.value.length,
  subtitle: header.value?.alias || String(route.query.subtitle || ''),
})))
async function toggleFollow() {
  try { await toggle() } catch (cause) { toast.error(String(cause)) }
}

// 大列表窗口渲染, 与歌单详情页同策略
const RENDER_CHUNK = 100
const renderCount = ref(RENDER_CHUNK)
const visibleTracks = computed(() => filteredTracks.value.slice(0, renderCount.value))
const hasMoreTracks = computed(() => renderCount.value < filteredTracks.value.length)

watch(query, () => { renderCount.value = RENDER_CHUNK })
watch(activeTab, () => {
  query.value = ''
  renderCount.value = RENDER_CHUNK
})

function onViewScroll(e: Event) {
  const el = e.currentTarget as HTMLElement | null
  if (!el || !hasMoreTracks.value || activeTab.value !== 'songs') return
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 2400) {
    renderCount.value = Math.min(filteredTracks.value.length, renderCount.value + RENDER_CHUNK)
  }
}

function parseHeader(raw: any): ArtistHeader {
  const artist = raw?.data?.artist || raw?.artist || {}
  const aliasList = Array.isArray(artist.alias) ? artist.alias.filter(Boolean) : []
  return {
    name: String(artist.name || route.query.name || ''),
    alias: aliasList.join(' / ') || String(route.query.subtitle || ''),
    coverUrl: resolveNeteaseCoverSized('detail', artist.cover, artist.picUrl) || String(route.query.cover || ''),
    avatarUrl: resolveNeteaseCoverSized('list', artist.avatar, artist.img1v1Url),
    briefDesc: String(artist.briefDesc || ''),
    musicSize: Number(artist.musicSize) || 0,
    albumSize: Number(artist.albumSize) || 0,
  }
}

async function load() {
  const id = artistId.value
  if (!id) return
  const request = ++generation

  const cacheKey = playlistDetailCacheKey('netease-artist-v2', id)
  header.value = parseHeader(null)
  tracks.value = []
  albums.value = []
  isLoading.value = true
  error.value = null
  const cached = previewCachedDetail<ArtistDetailCache>(cacheKey, (detail) => {
    if (request !== generation) return false
    header.value = detail.header
    tracks.value = detail.tracks
    albums.value = detail.albums
    isLoading.value = false
  })

  try {
    // 头部/歌曲/专辑并行加载 (对齐 Android loadInitial)
    const [detailRes, songsRes, albumsRes] = await Promise.allSettled([
      invoke<any>('get_netease_artist_detail', { artistId: id }),
      invoke<any>('get_netease_artist_songs', { artistId: id }),
      invoke<any>('get_netease_artist_albums', { artistId: id }),
    ])
    cached.markFresh()
    if (request !== generation) return

    if (detailRes.status === 'fulfilled') {
      header.value = parseHeader(detailRes.value)
    } else if (!header.value) {
      header.value = parseHeader(null)
    }

    if (songsRes.status === 'fulfilled') {
      const songs = songsRes.value?.songs || []
      tracks.value = songs.map((s: any) => ({
        id: `netease:${s.id}`,
        title: s.name || '',
        artist: (s.ar || []).map((a: any) => a.name).join(', '),
        album: s.al?.name || '',
        durationMs: s.dt || 0,
        coverUrl: resolveNeteaseCover(s.al?.picUrl, s.al?.pic),
        audioUrl: '',
      }))
      renderCount.value = Math.min(RENDER_CHUNK, tracks.value.length)
    }

    if (albumsRes.status === 'fulfilled') {
      const rawAlbums = albumsRes.value?.hotAlbums || []
      albums.value = rawAlbums
        .filter((al: any) => al?.id && al?.name)
        .map((al: any) => ({
          id: Number(al.id),
          name: String(al.name),
          coverUrl: resolveNeteaseCover(al.picUrl, al.blurPicUrl, al.pic),
          publishYear: al.publishTime ? String(new Date(al.publishTime).getFullYear()) : '',
          size: Number(al.size) || 0,
        }))
    }

    if (songsRes.status === 'rejected' && albumsRes.status === 'rejected' && !(await cached.shown())) {
      error.value = String(songsRes.reason || albumsRes.reason)
      return
    }

    if (header.value) {
      writePlaylistDetailCache<ArtistDetailCache>(cacheKey, {
        header: header.value,
        tracks: tracks.value,
        albums: albums.value,
      })
    }
  } catch (e: any) {
    if (request !== generation) return
    if (!(await cached.shown())) error.value = e?.toString() || t('player.load_failed')
    log.error('load artist failed:', e)
  } finally {
    if (request === generation) isLoading.value = false
  }
}

function playAll() {
  if (tracks.value.length) player.playAll(tracks.value)
}

function playTrack(track: TrackInfo) {
  player.playAll(filteredTracks.value, track.id)
}

function openAlbum(album: ArtistAlbum) {
  router.push({ name: 'netease-album', params: { id: String(album.id) } })
}

const collectionMenuRef = ref<InstanceType<typeof CollectionContextMenu> | null>(null)

function openAlbumMenu(event: MouseEvent, album: ArtistAlbum) {
  collectionMenuRef.value?.open(event, {
    route: { name: 'netease-album', params: { id: String(album.id) } },
    webUrl: collectionWebUrl('netease', 'album', album.id),
  })
}

const songCountLabel = computed(() =>
  t('player.track_count', { count: header.value?.musicSize || tracks.value.length }))
const albumCountLabel = computed(() =>
  t('player.artist_album_count', { count: header.value?.albumSize || albums.value.length }))

watch(artistId, () => {
  query.value = ''
  activeTab.value = 'songs'
  renderCount.value = RENDER_CHUNK
  void load()
}, { immediate: true })
onUnmounted(() => { generation++ })
</script>

<template>
  <div class="detail-view" @scroll.passive="onViewScroll">
    <header class="detail-header">
      <button class="back-btn" @click="router.back()">
        <span class="material-symbols-rounded">arrow_back</span>
      </button>
      <div class="artist-header-title">{{ header?.name || String(route.query.name || '') }}</div>
      <div class="header-search">
        <span class="material-symbols-rounded search-icon">search</span>
        <input v-model="query" class="search-input" :placeholder="t('library.tab_search_hint')" :aria-label="t('library.tab_search_hint')" />
      </div>
    </header>

    <div v-if="isLoading && !header" class="state-center">
      <span class="material-symbols-rounded spinning">progress_activity</span>
    </div>

    <div v-else-if="error && !tracks.length && !albums.length" class="state-center">
      <span class="material-symbols-rounded" style="font-size: 48px; opacity: 0.3">error</span>
      <p>{{ error }}</p>
      <button class="retry-btn" @click="() => load()">{{ t('player.retry') }}</button>
    </div>

    <template v-else>
      <!-- 歌手头部：无背景图，圆形头像 + 右侧信息并排（对齐网易云） -->
      <div class="artist-hero-card">
        <div class="artist-avatar">
          <BilibiliCoverImage
            v-if="header?.avatarUrl || header?.coverUrl"
            :src="header.avatarUrl || header.coverUrl"
          />
          <span v-else class="material-symbols-rounded">account_circle</span>
        </div>
        <div class="artist-hero-info">
          <div class="artist-hero-titleline">
            <h1 class="artist-name">{{ header?.name || String(route.query.name || '') }}</h1>
            <div class="artist-stat-chips">
              <span class="artist-stat-chip">{{ songCountLabel }}</span>
              <span class="artist-stat-chip">{{ albumCountLabel }}</span>
            </div>
            <div class="artist-hero-actions">
              <button class="play-all-btn" :disabled="!tracks.length" @click="playAll">
                <span class="material-symbols-rounded filled">play_arrow</span>
                {{ t('player.play_all') }}
              </button>
              <button class="artist-follow-btn" :class="{ active: following }" :disabled="changing || !header?.name" @click="toggleFollow">
                <span class="material-symbols-rounded">{{ following ? 'check' : 'person_add' }}</span>
                {{ t(following ? 'player.artist_unsubscribe' : 'player.artist_subscribe') }}
              </button>
            </div>
          </div>
          <p v-if="header?.alias" class="artist-alias">{{ header.alias }}</p>
          <p v-if="header?.briefDesc" class="artist-brief">{{ header.briefDesc }}</p>
        </div>
      </div>

      <!-- 歌曲 / 专辑 Tab (对齐 Android PrimaryTabRow) -->
      <div class="artist-tabs">
        <button
          class="artist-tab"
          :class="{ active: activeTab === 'songs' }"
          @click="activeTab = 'songs'"
        >
          <span class="material-symbols-rounded">music_note</span>
          <span>{{ t('player.artist_tab_songs') }}</span>
        </button>
        <button
          class="artist-tab"
          :class="{ active: activeTab === 'albums' }"
          @click="activeTab = 'albums'"
        >
          <span class="material-symbols-rounded">library_music</span>
          <span>{{ t('player.artist_tab_albums') }}</span>
        </button>
      </div>

      <Transition name="fade" mode="out-in">
      <!-- 歌曲列表 -->
      <div v-if="activeTab === 'songs'" key="artist-songs" class="track-list">
        <div v-if="filteredTracks.length === 0" class="state-center">
          <p>{{ t(search ? 'player.no_results' : 'player.artist_songs_empty') }}</p>
        </div>
        <div
          v-for="(track, index) in visibleTracks"
          :key="track.id"
          class="track-item"
          :class="{ active: player.currentTrack?.id === track.id }"
          @click="onTrackClick($event, () => playTrack(track))"
          @dblclick="onTrackDoubleClick($event, () => playTrack(track))"
          @contextmenu="trackMenuRef?.open($event, track)"
          @pointerenter="player.prefetchIntent(track)"
          @focusin="player.prefetchIntent(track)"
        >
          <div class="track-index">
            <div
              v-if="player.currentTrack?.id === track.id && player.isPlaying"
              class="equalizer-bars"
            >
              <span class="bar" /><span class="bar" /><span class="bar" />
            </div>
            <span v-else class="index-num">{{ index + 1 }}</span>
          </div>
          <div class="track-cover">
            <BilibiliCoverImage v-if="track.coverUrl" :src="track.coverUrl" loading="lazy">
              <span class="material-symbols-rounded filled">music_note</span>
            </BilibiliCoverImage>
            <span v-else class="material-symbols-rounded filled">music_note</span>
          </div>
          <div class="track-info">
            <div class="track-title">{{ track.title }}</div>
            <div class="track-meta">{{ track.album || track.artist }}</div>
          </div>
          <div class="track-duration">{{ formatDuration(track.durationMs) }}</div>
        </div>
      </div>

      <!-- 专辑列表：网格卡片，对齐网易云专辑墙 -->
      <div v-else key="artist-albums" class="artist-album-grid">
        <div v-if="filteredAlbums.length === 0" class="state-center">
          <p>{{ t(search ? 'player.no_results' : 'player.artist_albums_empty') }}</p>
        </div>
        <div
          v-for="album in filteredAlbums"
          :key="album.id"
          class="artist-album-card"
          role="button"
          tabindex="0"
          @click="openAlbum(album)"
          @contextmenu="openAlbumMenu($event, album)"
          @keydown.enter="openAlbum(album)"
        >
          <div class="artist-album-cover">
            <BilibiliCoverImage v-if="album.coverUrl" :src="album.coverUrl" loading="lazy">
              <span class="material-symbols-rounded filled">album</span>
            </BilibiliCoverImage>
            <span v-else class="material-symbols-rounded filled">album</span>
          </div>
          <div class="artist-album-name" :title="album.name">{{ album.name }}</div>
          <div class="artist-album-meta">
            {{ [album.publishYear, album.size ? t('player.track_count', { count: album.size }) : '']
              .filter(Boolean).join(' · ') }}
          </div>
        </div>
      </div>
      </Transition>
    </template>
    <TrackContextMenu ref="trackMenuRef" @play="playTrack" />
    <CollectionContextMenu ref="collectionMenuRef" />
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/detail-view.scss' as *;
@use '@/modules/library/artistTabs.scss' as *;

.artist-header-title {
  flex: 1;
  min-width: 0;
  font-size: 18px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-search {
  flex: 0 1 320px;
  max-width: 50%;
  min-width: 0;

  .search-input { min-width: 0; }
}

// 歌手头部：无背景图，头像与信息左右并排（对齐网易云）
.artist-hero-card {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 4px 2px 18px;
  margin-bottom: 4px;
}

.artist-avatar {
  width: 120px;
  height: 120px;
  border-radius: 50%;
  overflow: hidden;
  flex-shrink: 0;
  background: var(--md-surface-variant);
  display: flex;
  align-items: center;
  justify-content: center;

  :deep(img) {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .material-symbols-rounded { font-size: 64px; opacity: 0.35; }
}

.artist-hero-info {
  flex: 1;
  min-width: 0;
}

/* 名字/统计/按钮同处第一行，按钮靠右；空间不足时自动换行而不是挤压名字 */
.artist-hero-titleline {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px 12px;
}

.artist-name {
  margin: 0;
  font-size: 26px;
  font-weight: 700;
  line-height: 1.2;
  color: var(--md-on-surface);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.artist-alias {
  margin: 5px 0 0;
  font-size: 12px;
  color: var(--md-on-surface-variant);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.artist-stat-chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.artist-stat-chip {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 10px;
  border-radius: var(--radius-full);
  border: 1px solid var(--md-outline-variant);
  color: var(--md-on-surface-variant);
  font-size: 11px;
  font-weight: 600;
}

.artist-brief {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--md-on-surface-variant);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.artist-hero-actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
  flex-shrink: 0;
  align-self: center;

  /* 局部收窄按钮，避免把第一行撑高（不影响其他页面的 play-all-btn） */
  .play-all-btn {
    margin-top: 0;
    padding: 8px 18px;
    font-size: 13px;
  }
  .play-all-btn:disabled { opacity: 0.4; pointer-events: none; }

  .artist-follow-btn {
    padding: 8px 16px;
    font-size: 13px;
  }
}

.artist-follow-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 10px 18px;
  border: 1px solid var(--md-outline-variant);
  border-radius: var(--radius-full);
  font-size: 13px;
  color: var(--md-primary);

  &.active { background: var(--md-secondary-container); color: var(--md-on-secondary-container); }
  &:disabled { opacity: 0.5; }
  .material-symbols-rounded { font-size: 18px; }
}

// 专辑网格：卡片式，对齐网易云专辑墙
.artist-album-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(148px, 1fr));
  gap: 26px 18px;
  padding: 4px 2px 10px;
  align-content: start;
}

/* 空态/错误态占满整行，不占一个格子 */
.artist-album-grid .state-center {
  grid-column: 1 / -1;
}

.artist-album-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  cursor: pointer;
  transition: transform var(--duration-short, 150ms);

  &:hover .artist-album-cover {
    box-shadow: 0 8px 22px rgba(0, 0, 0, 0.18);
  }

  &:hover .artist-album-name {
    color: var(--md-primary);
  }

  &:active {
    transform: scale(0.98);
  }
}

.artist-album-cover {
  width: 100%;
  aspect-ratio: 1 / 1;
  border-radius: 10px;
  overflow: hidden;
  background: var(--md-surface-variant);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: box-shadow var(--duration-short, 150ms);

  :deep(img) {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .material-symbols-rounded { font-size: 32px; opacity: 0.35; }
}

.artist-album-name {
  font-size: 14px;
  font-weight: 500;
  line-height: 1.35;
  color: var(--md-on-surface);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  transition: color var(--duration-short, 150ms);
}

.artist-album-meta {
  font-size: 12px;
  color: var(--md-on-surface-variant);
  margin-top: -4px;
}
</style>
