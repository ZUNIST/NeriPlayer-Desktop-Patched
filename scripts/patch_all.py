"""NeriPlayer 桌面版本地补丁 —— 一次性全量应用。

用法（在仓库根目录执行）：
    python scripts/patch_all.py

也可以指定源码根目录：
    python scripts/patch_all.py /path/to/np-desktop

脚本对每处补丁输出 OK / SKIP / FAIL，最后汇总；FAIL 说明该处锚点不匹配
（通常是上游改动或已经打过补丁），需要人工确认后再编译。
"""
import sys
from pathlib import Path

# 脚本位于 <repo>/scripts/，默认以仓库根目录为目标
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent


PATCHES = []


def patch(rel, old, new, label):
    PATCHES.append((rel, old, new, label))


CLIENT = "src-tauri/src/api/netease/client.rs"
REC = "src-tauri/src/commands/recommend_cmd.rs"
MAIN = "src-tauri/src/main.rs"
DIALOG = "src/components/AddToPlaylistDialog.vue"
PLAYVIEW = "src/views/NeteasePlaylistView.vue"
SIDENAV = "src/components/SideNav.vue"
SECURITY = "src-tauri/src/security.rs"
RECO_STORE = "src/stores/recommend.ts"
LIKED_STORE = "src/stores/likedSongs.ts"

# ==================== 1. 搜索接口修复 ====================
patch(CLIENT,
      "use std::collections::HashSet;",
      "use std::collections::{HashMap, HashSet};",
      "client: import HashMap")

patch(CLIENT,
      """        let params = json!({
            "s": keyword,
            "type": "1",
            "limit": limit.to_string(),
            "offset": offset.to_string(),
            "total": "true"
        });

        let body = self.weapi_post(
            &format!("{}/weapi/cloudsearch/get/web", BASE_URL),
            &params,
        ).await?;

        let songs = body["result"]["songs"].as_array()
            .ok_or_else(|| AppError::Api("No search results".into()))?;

        let results = songs.iter().filter_map(|s| {
            Some(NeteaseSearchResult {
                id: s["id"].as_u64()?,
                name: s["name"].as_str()?.to_string(),
                artists: s["ar"].as_array()?
                    .iter()
                    .filter_map(|a| a["name"].as_str().map(String::from))
                    .collect(),
                album: s["al"]["name"].as_str().unwrap_or("").to_string(),
                duration_ms: s["dt"].as_u64().unwrap_or(0),
                cover_url: s["al"]["picUrl"].as_str().map(String::from),
            })
        }).collect();

        Ok(results)
    }""",
      """        let params = json!({
            "s": keyword,
            "type": "1",
            "limit": limit.to_string(),
            "offset": offset.to_string()
        });

        // cloudsearch/get/web 对已登录账号返回加密 result、匿名返回 50000005，
        // 两条路径都拿不到 songs；search/get 两种状态都返回明文。
        let body = self.weapi_post(
            &format!("{}/weapi/search/get", BASE_URL),
            &params,
        ).await?;

        let songs = body["result"]["songs"].as_array()
            .ok_or_else(|| AppError::Api("No search results".into()))?;

        // search/get 不带封面，用批量详情接口补一次
        let ids: Vec<u64> = songs.iter().filter_map(|s| s["id"].as_u64()).collect();
        let covers = self.song_cover_map(&ids).await;

        let results = songs.iter().filter_map(|s| {
            let artists = s["ar"].as_array().or_else(|| s["artists"].as_array())?;
            let album = if s["al"].is_object() { &s["al"] } else { &s["album"] };
            let id = s["id"].as_u64()?;
            Some(NeteaseSearchResult {
                id,
                name: s["name"].as_str()?.to_string(),
                artists: artists
                    .iter()
                    .filter_map(|a| a["name"].as_str().map(String::from))
                    .collect(),
                album: album["name"].as_str().unwrap_or("").to_string(),
                duration_ms: s["dt"].as_u64().or_else(|| s["duration"].as_u64()).unwrap_or(0),
                cover_url: album["picUrl"]
                    .as_str()
                    .map(String::from)
                    .or_else(|| covers.get(&id).cloned()),
            })
        }).collect();

        Ok(results)
    }

    /// 批量取歌曲封面（weapi/v3/song/detail），失败时返回空表不影响搜索
    async fn song_cover_map(&self, ids: &[u64]) -> HashMap<u64, String> {
        if ids.is_empty() {
            return HashMap::new();
        }
        let c: Vec<Value> = ids.iter().map(|id| json!({ "id": id })).collect();
        let params = json!({ "c": serde_json::to_string(&c).unwrap_or_default() });
        let body = match self
            .weapi_post(&format!("{}/weapi/v3/song/detail", BASE_URL), &params)
            .await
        {
            Ok(body) => body,
            Err(error) => {
                log::debug!(target: "netease", "song cover lookup failed: {error}");
                return HashMap::new();
            }
        };
        body["songs"]
            .as_array()
            .map(|songs| {
                songs
                    .iter()
                    .filter_map(|s| {
                        let id = s["id"].as_u64()?;
                        let url = s["al"]["picUrl"].as_str()?;
                        if url.is_empty() {
                            return None;
                        }
                        Some((id, url.to_string()))
                    })
                    .collect()
            })
            .unwrap_or_default()
    }""",
      "client: search 改 search/get + 封面补全")

patch(CLIENT,
      """        let body = self.weapi_post(&format!("{}/weapi/cloudsearch/get/web", BASE_URL), &params).await?;
        match json_i64(&body["code"]) {""",
      """        // cloudsearch 对登录账号返回加密 result、匿名返回 50000005，改用 search/get
        let mut body = self.weapi_post(&format!("{}/weapi/search/get", BASE_URL), &params).await?;

        // 歌曲结果补封面
        if search_type == 1 {
            let ids: Vec<u64> = body["result"]["songs"]
                .as_array()
                .map(|arr| arr.iter().filter_map(|s| s["id"].as_u64()).collect())
                .unwrap_or_default();
            let covers = self.song_cover_map(&ids).await;
            if !covers.is_empty() {
                if let Some(arr) = body["result"]["songs"].as_array_mut() {
                    for s in arr.iter_mut() {
                        let Some(id) = s["id"].as_u64() else { continue };
                        let Some(url) = covers.get(&id) else { continue };
                        if s["al"].is_object() {
                            s["al"]["picUrl"] = json!(url);
                        } else if s["album"].is_object() {
                            s["album"]["picUrl"] = json!(url);
                        }
                    }
                }
            }
        }

        match json_i64(&body["code"]) {""",
      "client: cloudsearch 改 search/get + 封面")

# ==================== 2. 歌单增删 ====================
patch(CLIENT,
      """    /// 下载和在线播放使用同一个 EAPI 入口""",
      """    /// 向网易云歌单添加/移除歌曲（op=add / del），需要登录态与 csrf_token
    pub async fn manipulate_playlist_tracks(
        &self,
        playlist_id: u64,
        song_ids: &[u64],
        add: bool,
    ) -> AppResult<Value> {
        self.weapi_post_write(
            &format!("{}/weapi/playlist/manipulate/tracks", BASE_URL),
            &json!({
                "op": if add { "add" } else { "del" },
                "pid": playlist_id.to_string(),
                "trackIds": serde_json::to_string(song_ids).unwrap_or_default(),
                "imme": "true",
                "csrf_token": self.csrf_token(),
            }),
        ).await
    }

    /// 下载和在线播放使用同一个 EAPI 入口""",
      "client: manipulate_playlist_tracks")

patch(REC,
      """    let client = state.netease();
    client.like_song(song_id, like).await
}""",
      """    let client = state.netease();
    let body = client.like_song(song_id, like).await?;
    // 业务失败要抛出去，否则前端只能显示笼统的「同步失败」，无从定位
    let code = body["code"].as_i64().unwrap_or(-1);
    if code != 200 {
        let message = body["message"].as_str().unwrap_or("netease like failed");
        return Err(AppError::Api(format!("{message} ({code})")));
    }
    Ok(body)
}

/// 向网易云歌单添加歌曲（需登录；走 playlist/manipulate/tracks）
#[tauri::command]
pub async fn add_tracks_to_netease_playlist(
    playlist_id: u64,
    song_ids: Vec<u64>,
    state: State<'_, AppState>,
) -> AppResult<Value> {
    if song_ids.is_empty() {
        return Ok(serde_json::json!({ "code": 200, "count": 0 }));
    }
    {
        let auth = state.auth.lock();
        if auth.netease.is_none() {
            return Err(AppError::Api("Netease not logged in".into()));
        }
    }
    let body = state
        .netease()
        .manipulate_playlist_tracks(playlist_id, &song_ids, true)
        .await?;
    let code = body["code"].as_i64().unwrap_or(-1);
    if code != 200 {
        let message = body["message"]
            .as_str()
            .unwrap_or("netease playlist add failed");
        return Err(AppError::Api(format!("{message} ({code})")));
    }
    Ok(body)
}

/// 从网易云歌单移除歌曲（需登录；走 playlist/manipulate/tracks，op=del）
#[tauri::command]
pub async fn remove_tracks_from_netease_playlist(
    playlist_id: u64,
    song_ids: Vec<u64>,
    state: State<'_, AppState>,
) -> AppResult<Value> {
    if song_ids.is_empty() {
        return Ok(serde_json::json!({ "code": 200, "count": 0 }));
    }
    {
        let auth = state.auth.lock();
        if auth.netease.is_none() {
            return Err(AppError::Api("Netease not logged in".into()));
        }
    }
    let body = state
        .netease()
        .manipulate_playlist_tracks(playlist_id, &song_ids, false)
        .await?;
    let code = body["code"].as_i64().unwrap_or(-1);
    if code != 200 {
        let message = body["message"]
            .as_str()
            .unwrap_or("netease playlist remove failed");
        return Err(AppError::Api(format!("{message} ({code})")));
    }
    Ok(body)
}""",
      "recommend_cmd: like 错误码 + 歌单增删命令")

patch(MAIN,
      "            recommend_cmd::like_song,",
      """            recommend_cmd::like_song,
            recommend_cmd::add_tracks_to_netease_playlist,
            recommend_cmd::remove_tracks_from_netease_playlist,""",
      "main: 注册两个新命令")

# ==================== 3. 添加到歌单对话框 ====================
patch(DIALOG,
      """import { localPlaylistDisplayName } from '@/modules/library/localPlaylists'""",
      """import { localPlaylistDisplayName } from '@/modules/library/localPlaylists'
import { resolveNeteaseCover } from '@/utils/neteaseCover'""",
      "dialog: import resolveNeteaseCover")

patch(DIALOG,
      """const playlists = ref<PlaylistInfo[]>([])
const isLoading = ref(false)""",
      """const playlists = ref<PlaylistInfo[]>([])
const neteasePlaylists = ref<PlaylistInfo[]>([])
const isLoading = ref(false)""",
      "dialog: neteasePlaylists ref")

patch(DIALOG,
      """  try {
    await loadRequest
  } catch { /* 错误已记录 */ }
}""",
      """  try {
    await loadRequest
  } catch { /* 错误已记录 */ }
  void loadNeteasePlaylists()
}

/// 加载登录账号的网易云歌单；只保留自建歌单（收藏的他人歌单不可写）
async function loadNeteasePlaylists() {
  try {
    const data = await invoke<any>('get_user_playlists', { platform: 'netease' })
    const list: any[] = data?.playlist || []
    neteasePlaylists.value = list
      // subscribed：收藏的他人歌单不可写；specialType===5 是「我喜欢的音乐」，只能走红心接口
      .filter(pl => pl?.id && !pl.subscribed && pl.specialType !== 5)
      .map(pl => ({
        id: pl.id,
        name: pl.name,
        track_count: pl.trackCount ?? 0,
        modified_at: 0,
        // 网易云返回 http:// 封面，需转 https 并统一尺寸，否则会被拦成占位图
        cover_url: resolveNeteaseCover(pl.coverImgUrl, pl.picUrl) || null,
      }))
  } catch (e) {
    // 未登录或请求失败：不展示这一组，不影响本地歌单
    neteasePlaylists.value = []
  }
}

/// 把当前选中曲目里网易云来源的部分加入云端歌单
async function addToNeteasePlaylist(playlistId: number) {
  const targets = targetTracks()
  if (targets.length === 0 || isSubmitting.value) return
  const ids = targets
    .map(track => {
      const match = /^netease:(\\d+)$/.exec(track.id)
      return match ? Number(match[1]) : null
    })
    .filter((id): id is number => id !== null)
  if (ids.length === 0) {
    toast.error(t('library.add_to_playlist_failed'))
    return
  }
  isSubmitting.value = true
  try {
    await invoke('add_tracks_to_netease_playlist', { playlistId, songIds: ids })
    toast.success(t('library.added_to_playlist'))
    emit('update:open', false)
  } catch (e) {
    log.error('Add to netease playlist failed:', e)
    toast.error(t('library.add_to_playlist_failed'))
  } finally {
    isSubmitting.value = false
  }
}""",
      "dialog: 加载云端歌单 + 添加函数")

patch(DIALOG,
      """          <!-- 歌单列表 -->""",
      """          <!-- 本地与云端歌单共用一个滚动区，避免两个列表互相挤压 -->
          <div class="atp-scroll">
          <!-- 歌单列表 -->""",
      "dialog: 开滚动容器")

patch(DIALOG,
      """          <div v-else-if="!isLoading" class="atp-empty">
            <p>{{ t('library.playlist_empty_title') }}</p>
          </div>
        </div>""",
      """          <div v-else-if="!isLoading && neteasePlaylists.length === 0" class="atp-empty">
            <p>{{ t('library.playlist_empty_title') }}</p>
          </div>

          <!-- 网易云歌单：登录后可用，仅自建歌单可写 -->
          <template v-if="neteasePlaylists.length > 0">
            <div class="atp-section-label">{{ t('library.tab_netease_playlists') }}</div>
            <div class="atp-list">
              <div
                v-for="pl in neteasePlaylists"
                :key="'ne-' + pl.id"
                class="atp-item"
                :class="{ disabled: isSubmitting }"
                @click="addToNeteasePlaylist(pl.id)"
              >
                <div class="atp-item-icon" :class="{ 'has-cover': coverFor(pl) }">
                  <BilibiliCoverImage
                    v-if="coverFor(pl) && isBilibiliCover(pl.cover_url)"
                    :src="pl.cover_url!"
                    class="atp-item-cover"
                  >
                    <span class="material-symbols-rounded filled" style="font-size: 20px">queue_music</span>
                  </BilibiliCoverImage>
                  <img
                    v-else-if="coverFor(pl)"
                    :src="pl.cover_url!"
                    referrerpolicy="no-referrer"
                    loading="lazy"
                    class="atp-item-cover"
                    alt=""
                    @error="markCoverFailed(pl.id)"
                  />
                  <span v-else class="material-symbols-rounded filled" style="font-size: 20px">cloud</span>
                </div>
                <div class="atp-item-info">
                  <div class="atp-item-name">{{ pl.name }}</div>
                  <div class="atp-item-count">{{ t('library.track_count', { count: pl.track_count }) }}</div>
                </div>
              </div>
            </div>
          </template>
          </div>
        </div>""",
      "dialog: 空态条件 + 云端分组 + 闭合容器")

patch(DIALOG,
      """.atp-list {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: none;
  gap: 2px;

  &::-webkit-scrollbar { display: none; }
}""",
      """/* 本地与云端歌单共用同一个滚动区 */
.atp-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: none;

  &::-webkit-scrollbar { display: none; }
}

.atp-section-label {
  margin: 12px 4px 6px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.4px;
  color: var(--md-on-surface-variant);
}

.atp-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}""",
      "dialog: 滚动区与分组标题样式")

# ==================== 4. 网易云歌单页移除入口 ====================
patch(PLAYVIEW,
      """import { usePlayerStore, type TrackInfo } from '@/stores/player'
import { useAuthStore } from '@/stores/auth'
import { useDownloadStore } from '@/stores/download'""",
      """import { usePlayerStore, type TrackInfo } from '@/stores/player'
import { useAuthStore } from '@/stores/auth'
import { useDownloadStore } from '@/stores/download'
import { useToastStore } from '@/stores/toast'
import { createLogger } from '@/utils/logger'""",
      "playlist view: import toast/log")

patch(PLAYVIEW,
      """const auth = useAuthStore()
const downloadStore = useDownloadStore()
const { t, locale } = useI18n()""",
      """const auth = useAuthStore()
const downloadStore = useDownloadStore()
const toast = useToastStore()
const log = createLogger('netease-playlist')
const { t, locale } = useI18n()""",
      "playlist view: toast/log 实例")

patch(PLAYVIEW,
      """const tracks = ref<TrackInfo[]>([])""",
      """const tracks = ref<TrackInfo[]>([])
/// 当前详情的缓存键，移除曲目后需要就地更新缓存，避免返回时又看到已删的歌
const detailCacheKey = ref('')""",
      "playlist view: detailCacheKey")

patch(PLAYVIEW,
      """  isLoading.value = true
  error.value = null""",
      """  isLoading.value = true
  error.value = null
  detailCacheKey.value = cacheKey""",
      "playlist view: 记录缓存键")

patch(PLAYVIEW,
      """    createContextMenuItem(
      track ? trackDownloadLabel(track) : t('download.download'),
      {
        id: 'download',
        icon: 'download',
        disabled: !track || isTrackDownloadDisabled(track),
      },
    ),
  ]
})""",
      """    createContextMenuItem(
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
})""",
      "playlist view: 移除菜单项")

patch(PLAYVIEW,
      """    case 'download':
      void handleTrackDownload(track)
      break
  }
}""",
      """    case 'download':
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
  const match = /^netease:(\\d+)$/.exec(track.id)
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
}""",
      "playlist view: 移除处理函数")

# ==================== 5. 收藏错误链路 ====================
patch(RECO_STORE,
      """  async function toggleLikeSong(songId: number, like: boolean): Promise<boolean> {
    try {
      const data = await invoke<any>('like_song', { songId, like })
      if (data?.code === 200) {
        if (like) {
          likedSongIds.value.add(songId)
        } else {
          likedSongIds.value.delete(songId)
        }
        return true
      }
      return false
    } catch (e) {
      log.error('toggleLikeSong:', e)
      return false
    }
  }""",
      """  async function toggleLikeSong(songId: number, like: boolean): Promise<boolean> {
    const data = await invoke<any>('like_song', { songId, like })
    if (data?.code === 200) {
      if (like) {
        likedSongIds.value.add(songId)
      } else {
        likedSongIds.value.delete(songId)
      }
      return true
    }
    // 不再吞异常：把业务码交给调用方，避免上层只剩一句笼统的「同步失败」
    log.error('toggleLikeSong failed:', data)
    throw new Error(`code=${data?.code ?? 'unknown'}`)
  }""",
      "recommend store: 失败抛错")

patch(LIKED_STORE,
      """          .catch(() => {
            useToastStore().show(
              (i18n.global as any).t('player.like_cloud_sync_failed'),
              'info',
            )
          })""",
      """          .catch((e) => {
            const reason = e instanceof Error ? e.message : String(e)
            // 524「环境异常」与 401「下架」都是网易云的版权受限掩码，换个说法避免误导用户
            const limited = /524|401|环境异常|下架/.test(reason)
            useToastStore().show(
              limited
                ? (i18n.global as any).t('player.like_cloud_copyright_limited')
                : `${(i18n.global as any).t('player.like_cloud_sync_failed')} — ${reason}`,
              'info',
            )
          })""",
      "liked store: 错误分流提示")

# ==================== 6. 侧边栏与启动优化 ====================
patch(SIDENAV,
      """.side-nav {
  width: 80px;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  background: var(--md-surface-container-low);
  border-right: 1px solid var(--md-surface-container-high);
  flex-shrink: 0;
  padding: 12px 0;
}""",
      """.side-nav {
  width: 80px;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: var(--md-surface-container-low);
  border-right: 1px solid var(--md-surface-container-high);
  flex-shrink: 0;
  /* 居中后整体上移约一个图标高度：底部多留 120px，居中时相当于上移 60px */
  padding: 12px 0 132px;
}""",
      "side nav: 垂直居中并上移")

patch(SECURITY,
      """        #[cfg(target_os = "windows")]
        {
            // 主机名/用户名可变，Windows MachineGuid 才是跨启动稳定的本机标识
            if let Ok(output) = std::process::Command::new("reg")
                .args([
                    "query",
                    r"HKLM\\SOFTWARE\\Microsoft\\Cryptography",
                    "/v",
                    "MachineGuid",
                ])
                .output()
            {
                if output.status.success() {
                    let text = String::from_utf8_lossy(&output.stdout);
                    if let Some(value) = text
                        .lines()
                        .find(|line| line.to_ascii_lowercase().contains("machineguid"))
                        .and_then(|line| line.split_whitespace().last())
                        .filter(|value| !value.is_empty())
                    {
                        return value.as_bytes().to_vec();
                    }
                }
            }
        }""",
      """        #[cfg(target_os = "windows")]
        {
            // 直接读注册表：spawn reg.exe 会让启动时闪一下控制台窗口，也白白多一次进程创建
            use winreg::enums::HKEY_LOCAL_MACHINE;
            if let Ok(key) = winreg::RegKey::predef(HKEY_LOCAL_MACHINE)
                .open_subkey(r"SOFTWARE\\Microsoft\\Cryptography")
            {
                if let Ok(guid) = key.get_value::<String, _>("MachineGuid") {
                    if !guid.is_empty() {
                        return guid.into_bytes();
                    }
                }
            }
        }""",
      "security: winreg 直读 MachineGuid（消除启动黑窗）")

# ==================== 7. i18n ====================
I18N = {
    "zh-CN.json": "已在本地收藏；该歌曲受版权限制，暂不支持云端同步",
    "zh-TW.json": "已在本機收藏；該歌曲受版權限制，暫不支援雲端同步",
    "en.json": "Liked locally; this track is copyright-restricted and cannot be synced",
    "ja.json": "ローカルでお気に入り登録しました。この曲は著作権の制限によりクラウド同期できません",
}


def patch_i18n():
    ok = 0
    for name, value in I18N.items():
        path = ROOT / "src/i18n" / name
        if not path.exists():
            print(f"[MISS] i18n/{name}")
            continue
        content = path.read_text(encoding="utf-8")
        if "like_cloud_copyright_limited" in content:
            print(f"[SKIP] i18n/{name}")
            ok += 1
            continue
        lines = content.splitlines(keepends=True)
        out, inserted = [], False
        for line in lines:
            out.append(line)
            if not inserted and '"like_cloud_sync_failed"' in line:
                indent = line[: len(line) - len(line.lstrip())]
                out.append(f'{indent}"like_cloud_copyright_limited": "{value}",\n')
                inserted = True
        if inserted:
            path.write_text("".join(out), encoding="utf-8")
            print(f"[OK] i18n/{name}")
            ok += 1
        else:
            print(f"[FAIL] i18n/{name} 未找到锚点")
    return ok


def main():
    if not (ROOT / "src-tauri").exists():
        print(f"源码目录无效: {ROOT}")
        return 2
    ok = fail = 0
    for rel, old, new, label in PATCHES:
        path = ROOT / rel
        if not path.exists():
            print(f"[MISS FILE] {rel}")
            fail += 1
            continue
        text = path.read_text(encoding="utf-8")
        if new in text and old not in text:
            print(f"[SKIP] {label}")
            ok += 1
            continue
        if old not in text:
            print(f"[FAIL] {label} ({rel})")
            fail += 1
            continue
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        print(f"[OK] {label}")
        ok += 1

    ok += patch_i18n()
    print(f"\n== 完成: {ok} 成功 / {fail} 失败 ==")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
