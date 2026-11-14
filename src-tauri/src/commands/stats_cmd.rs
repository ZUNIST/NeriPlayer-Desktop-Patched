// 播放统计命令：与 Android 播放统计页对齐
use tauri::{AppHandle, State};

use crate::error::AppResult;
use crate::state::{AppState, TrackSource};
use crate::stats::{self, PlaybackSession, PlaybackStatsSummary, StatsPeriod};
use crate::sync::manager;

fn now_ms() -> i64 {
    chrono::Utc::now().timestamp_millis()
}

/// 前端上报的一次收听增量
///
/// 身份键由后端从 TrackInfo 派生，前端不需要重复实现各平台的哈希规则，
/// 也就不会出现两端算出不同 key 导致统计分裂。
#[derive(Debug, Clone, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PlaybackSessionInput {
    pub track: crate::state::TrackInfo,
    #[serde(default)]
    pub listened_ms: i64,
    #[serde(default)]
    pub play_count_increment: i32,
    /// 这首歌是从哪个本地歌单开始的队列里播的
    #[serde(default)]
    pub local_playlist_id: Option<String>,
}

/// 计入播放的本地歌单 id（对齐 Android countedLocalPlaylistId：只有算一次播放时才计歌单）
fn counted_local_playlist_id(input: &PlaybackSessionInput) -> Option<i64> {
    if input.play_count_increment <= 0 {
        return None;
    }
    input.local_playlist_id.as_deref()?.trim().parse().ok().filter(|id| *id != 0)
}

/// 本地歌单播放计数写进同步扩展段（对齐 Android LocalPlaylistPlaybackStatsRepository.recordPlayNow）
async fn record_local_playlist_plays(device_id: String, playlist_ids: Vec<i64>, played_at: i64) {
    if playlist_ids.is_empty() {
        return;
    }
    let result = tokio::task::spawn_blocking(move || -> AppResult<()> {
        // 与同步回写共用歌单锁并推进写入版本：进行中的同步会推迟，而不是用旧快照的扩展段覆盖这次计数
        let _guard = crate::library::playlist::lock_io();
        let day_start_at = stats::day_start_at(played_at);
        crate::db::user_db()?.write(|transaction| {
            crate::sync::storage::update_archive_extensions(transaction, |extensions| {
                for &playlist_id in &playlist_ids {
                    crate::sync::merge::record_local_playlist_play(extensions, playlist_id, day_start_at, &device_id, played_at);
                }
                Ok(())
            })
        })?;
        crate::library::playlist::mark_io_changed();
        Ok(())
    })
    .await;
    // 统计失败不能影响播放
    match result {
        Ok(Err(error)) => log::warn!("record local playlist play failed: {error}"),
        Err(error) => log::warn!("record local playlist play task failed: {error}"),
        Ok(Ok(())) => {}
    }
}

fn to_session(input: &PlaybackSessionInput) -> PlaybackSession {
    let track = &input.track;
    // 对齐 Android：本地歌曲在统计里记下文件位置，统计页才能直接播放
    let media_uri = (track.source == TrackSource::Local && !track.url.trim().is_empty())
        .then(|| track.url.clone());
    PlaybackSession {
        identity_key: manager::playback_stats_identity_key_pub(track),
        id: track.id.clone(),
        name: track.title.clone(),
        artist: track.artist.clone(),
        album: track.album.clone(),
        album_id: String::new(),
        cover_url: track.cover_url.clone(),
        media_uri,
        duration_ms: track.duration_ms as i64,
        listened_ms: input.listened_ms,
        play_count_increment: input.play_count_increment,
    }
}

/// 上报一次收听增量（由前端 PlaybackStatsTracker 产出）
#[tauri::command]
pub async fn record_playback_session(
    session: PlaybackSessionInput,
    app: AppHandle,
    state: State<'_, AppState>,
) -> AppResult<()> {
    let device_id = manager::get_or_create_device_id_pub(&app);
    let played_at = now_ms();
    {
        let mut store = state.stats.lock();
        store.record(&to_session(&session), &device_id, played_at);
        stats::save(&store);
    }
    let playlists = counted_local_playlist_id(&session).into_iter().collect();
    record_local_playlist_plays(device_id, playlists, played_at).await;
    Ok(())
}

/// 批量上报，退出前一次性 flush 用
#[tauri::command]
pub async fn record_playback_sessions(
    sessions: Vec<PlaybackSessionInput>,
    app: AppHandle,
    state: State<'_, AppState>,
) -> AppResult<()> {
    if sessions.is_empty() {
        return Ok(());
    }
    let device_id = manager::get_or_create_device_id_pub(&app);
    let played_at = now_ms();
    {
        let mut store = state.stats.lock();
        for session in &sessions {
            store.record(&to_session(session), &device_id, played_at);
        }
        stats::save(&store);
    }
    let playlists = sessions.iter().filter_map(counted_local_playlist_id).collect();
    record_local_playlist_plays(device_id, playlists, played_at).await;
    Ok(())
}

#[tauri::command]
pub async fn get_playback_stats(
    period: StatsPeriod,
    state: State<'_, AppState>,
) -> AppResult<PlaybackStatsSummary> {
    let store = state.stats.lock();
    Ok(store.summarize(period, now_ms()))
}

/// 一次性返回全部区间，避免切换 tab 时来回 IPC
#[tauri::command]
pub async fn get_playback_stats_overview(
    state: State<'_, AppState>,
) -> AppResult<Vec<PlaybackStatsSummary>> {
    let now = now_ms();
    let store = state.stats.lock();
    Ok([
        StatsPeriod::Day,
        StatsPeriod::Week,
        StatsPeriod::Month,
        StatsPeriod::Year,
        StatsPeriod::All,
    ]
    .into_iter()
    .map(|period| store.summarize(period, now))
    .collect())
}

#[tauri::command]
pub async fn clear_playback_stats(state: State<'_, AppState>) -> AppResult<()> {
    let mut store = state.stats.lock();
    store.clear(now_ms());
    stats::save(&store);
    Ok(())
}

#[tauri::command]
pub async fn remove_playback_stats(
    identity_keys: Vec<String>,
    state: State<'_, AppState>,
) -> AppResult<()> {
    let mut store = state.stats.lock();
    store.remove_tracks(&identity_keys);
    stats::save(&store);
    Ok(())
}

/// 把一条统计还原成可播放的曲目（对齐 Android 统计页点歌即播）
///
/// 本机记下的统计 id 带平台前缀（`netease:123`）；Android 同步来的是同步模型的
/// 裸 id + album/mediaUri 标记，交给同步歌单同一套 SyncSong -> TrackInfo 规则解析
fn stat_to_track(stat: &crate::sync::models::SyncTrackStat) -> Option<crate::state::TrackInfo> {
    use crate::state::TrackInfo;
    let id = stat.id.trim();
    let media_uri = stat.media_uri.clone().unwrap_or_default();
    let prefixed = id.split_once(':').and_then(|(prefix, rest)| {
        let source = match prefix {
            "netease" => TrackSource::Netease,
            "qq" => TrackSource::Qq,
            "bilibili" => TrackSource::Bilibili,
            "youtube" => TrackSource::Youtube,
            "local" => TrackSource::Local,
            _ => return None,
        };
        (!rest.is_empty()).then_some(source)
    });
    if let Some(source) = prefixed {
        let url = if source == TrackSource::Local {
            if media_uri.trim().is_empty() { id.trim_start_matches("local:").to_string() } else { media_uri }
        } else {
            String::new()
        };
        return Some(TrackInfo {
            id: id.to_string(),
            title: stat.name.clone(),
            artist: stat.artist.clone(),
            album: stat.album.clone(),
            duration_ms: stat.duration_ms.max(0) as u64,
            source,
            url,
            cover_url: stat.cover_url.clone(),
            added_at: 0,
            sync_payload: None,
            playlist_key: None,
        });
    }
    if id.is_empty() && media_uri.trim().is_empty() {
        return None;
    }
    let song = crate::sync::models::SyncSong {
        id: id.to_string(),
        name: stat.name.clone(),
        artist: stat.artist.clone(),
        album: stat.album.clone(),
        album_id: stat.album_id.clone(),
        duration_ms: stat.duration_ms,
        cover_url: stat.cover_url.clone().unwrap_or_default(),
        media_uri: media_uri.clone(),
        ..Default::default()
    };
    let mut track = manager::sync_song_to_track_pub(&song);
    if track.source == TrackSource::Local {
        track.url = media_uri;
    }
    Some(track)
}

/// 按传入顺序还原多条统计；还原不了的（无 id 的旧记录等）原位返回 null
#[tauri::command]
pub async fn get_playback_stat_tracks(
    identity_keys: Vec<String>,
    state: State<'_, AppState>,
) -> AppResult<Vec<Option<crate::state::TrackInfo>>> {
    let store = state.stats.lock();
    let by_key: std::collections::HashMap<&str, &crate::sync::models::SyncTrackStat> = store
        .stats
        .iter()
        .map(|stat| (stat.identity_key.as_str(), stat))
        .collect();
    Ok(identity_keys
        .iter()
        .map(|key| by_key.get(key.as_str()).and_then(|stat| stat_to_track(stat)))
        .collect())
}

/// 计算曲目的统计身份键，供前端上报时使用
#[tauri::command]
pub async fn playback_stats_identity_key(
    track: crate::state::TrackInfo,
) -> AppResult<String> {
    Ok(manager::playback_stats_identity_key_pub(&track))
}

/// 自动下载「常听」歌曲的判定结果
#[derive(Debug, Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AutoDownloadCandidate {
    /// 是否够得上「常听」
    pub qualified: bool,
    /// 统计窗口内的播放次数
    pub recent_plays: i32,
    /// 统计窗口内的累计收听时长
    pub recent_listen_ms: i64,
    /// 平均听完整度（0~1），按「累计收听 /（次数 × 单曲时长）」估算
    pub completion: f64,
    /// 未达标原因，供前端提示与排查
    pub reason: String,
}

/// 汇总某首歌在 `[start, ∞)` 窗口内的播放次数与累计收听时长。
///
/// 单独抽出来是为了能脱离 `AppState` 直接测：窗口边界与单位（毫秒时间戳）
/// 是这段逻辑最容易写错的地方。
fn recent_window_totals(
    buckets: &[crate::sync::models::SyncPlaybackStatBucket],
    identity_key: &str,
    start: i64,
) -> (i32, i64) {
    let mut plays = 0i32;
    let mut listen_ms = 0i64;
    for bucket in buckets
        .iter()
        .filter(|bucket| bucket.identity_key == identity_key && bucket.day_start_at >= start)
    {
        plays = plays.saturating_add(bucket.play_count.max(0));
        listen_ms = listen_ms.saturating_add(bucket.total_listen_ms.max(0));
    }
    (plays, listen_ms)
}

/// 平均听完整度：累计收听 ÷（次数 × 单曲时长），结果夹到 0~1。
/// 单曲时长缺失或次数为 0 时返回 1.0（视为通过），避免因为缺元数据永远不下载。
fn completion_ratio(listen_ms: i64, plays: i32, duration_ms: i64) -> f64 {
    let expected = duration_ms.max(0) as f64 * f64::from(plays.max(0));
    if expected <= 0.0 {
        return 1.0;
    }
    (listen_ms.max(0) as f64 / expected).clamp(0.0, 1.0)
}

/// 判断某首歌在统计窗口内是否够得上「常听」，用于播放达标后自动下载。
///
/// 参考网易云「仅存常听歌曲」的意图（只留真正会反复听的，省空间），判定用两条：
/// 窗口内重复收听次数、平均听完整度。「本地未下载」由调用方自行检查。
///
/// 不用全量累计次数是关键：那样会把几个月前循环过、现在早就不听的歌也当成常听。
/// 窗口口径与统计页一致，按天 bucket 聚合，因此能表达「最近」而不只是「曾经」。
#[tauri::command]
pub async fn check_auto_download_candidate(
    track: crate::state::TrackInfo,
    window_days: i64,
    min_plays: i32,
    min_completion: f64,
    state: State<'_, AppState>,
) -> AppResult<AutoDownloadCandidate> {
    // 身份键必须由后端派生，否则两端算法不一致会查不到统计
    let identity_key = manager::playback_stats_identity_key_pub(&track);
    let window_days = window_days.clamp(1, 365);
    let start = now_ms() - window_days * 24 * 60 * 60 * 1000;
    let store = state.stats.lock();

    let (recent_plays, recent_listen_ms) =
        recent_window_totals(&store.buckets, &identity_key, start);

    let duration_ms = store
        .stats
        .iter()
        .find(|stat| stat.identity_key == identity_key)
        .map_or(0, |stat| stat.duration_ms.max(0));
    let completion = completion_ratio(recent_listen_ms, recent_plays, duration_ms);

    let (qualified, reason) = if recent_plays < min_plays {
        (
            false,
            format!("plays in window {recent_plays} < required {min_plays}"),
        )
    } else if completion < min_completion {
        (
            false,
            format!("completion {completion:.2} < required {min_completion:.2}"),
        )
    } else {
        (true, String::new())
    };

    Ok(AutoDownloadCandidate {
        qualified,
        recent_plays,
        recent_listen_ms,
        completion,
        reason,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_bucket(
        day_start_at: i64,
        identity_key: &str,
        play_count: i32,
        total_listen_ms: i64,
    ) -> crate::sync::models::SyncPlaybackStatBucket {
        crate::sync::models::SyncPlaybackStatBucket {
            day_start_at,
            identity_key: identity_key.to_string(),
            play_count,
            total_listen_ms,
            ..Default::default()
        }
    }

    /// 窗口外的天数、别的歌曲都不能计入，否则「常听」会被很久以前
    /// 循环过的歌撑起来，正是这个功能要避免的。
    #[test]
    fn auto_download_window_excludes_days_and_tracks_outside_the_window() {
        let day = 24 * 60 * 60 * 1000;
        let now = 1_700_000_000_000i64;
        let start = now - 10 * day;
        let buckets = vec![
            make_bucket(now - 9 * day, "track-a", 2, 300_000),
            make_bucket(now - 1 * day, "track-a", 1, 180_000),
            make_bucket(now - 11 * day, "track-a", 5, 900_000),
            make_bucket(now - 2 * day, "track-b", 9, 900_000),
        ];

        let (plays, listen_ms) = recent_window_totals(&buckets, "track-a", start);

        assert_eq!(plays, 3, "只累计窗口内的 2 + 1 次");
        assert_eq!(listen_ms, 480_000, "只累计窗口内的 300s + 180s");
    }

    /// 完整度用来区分「真的在听」和「开播就切」，两种极端都要落在预期值上。
    #[test]
    fn completion_ratio_distinguishes_full_and_skipped_plays() {
        // 3 次各听满 200s
        assert!((completion_ratio(600_000, 3, 200_000) - 1.0).abs() < 1e-9);
        // 3 次每次只听 20s，属于跳过
        assert!((completion_ratio(60_000, 3, 200_000) - 0.1).abs() < 1e-9);
        // 重复计入导致超过 100% 时夹到 1.0
        assert!((completion_ratio(2_000_000, 3, 200_000) - 1.0).abs() < 1e-9);
    }

    /// 缺时长或次数为 0 时按通过处理：宁可多下一首，也不要因为
    /// 早期记录没有时长就永远不下歌。
    #[test]
    fn completion_ratio_passes_when_metadata_is_missing() {
        assert!((completion_ratio(60_000, 3, 0) - 1.0).abs() < 1e-9);
        assert!((completion_ratio(0, 0, 200_000) - 1.0).abs() < 1e-9);
    }

    /// 入参契约：track 必须是 toBackendTrack 产出的 snake_case 结构
    /// （TrackInfo 的 duration_ms/url 必填且无 camelCase 别名）
    #[test]
    fn session_input_accepts_to_backend_track_shape() {
        let payload = serde_json::json!({
            "track": {
                "id": "netease:2161589xxx",
                "title": "Song",
                "artist": "Artist",
                "album": "Album",
                "duration_ms": 200_000,
                "cover_url": "https://cover",
                "url": "",
                "source": "netease",
                "added_at": 0,
                "sync_payload": null,
                "playlist_key": null,
            },
            "listenedMs": 15_000,
            "playCountIncrement": 1,
        });

        let input: PlaybackSessionInput =
            serde_json::from_value(payload).expect("snake_case track 必须可反序列化");
        let session = to_session(&input);
        assert!(!session.identity_key.trim().is_empty(), "身份键不得为空");
        assert_eq!(session.listened_ms, 15_000);
        assert_eq!(session.play_count_increment, 1);
        assert_eq!(session.duration_ms, 200_000);
        assert_eq!(session.media_uri, None, "在线歌曲不记录播放地址");
    }

    #[test]
    fn local_session_keeps_file_path_for_replay() {
        let payload = serde_json::json!({
            "track": {
                "id": "local:D:\\Music\\song.flac",
                "title": "Song",
                "artist": "Artist",
                "album": "",
                "duration_ms": 180_000,
                "cover_url": null,
                "url": "D:\\Music\\song.flac",
                "source": "local",
            },
            "listenedMs": 30_000,
            "playCountIncrement": 1,
        });
        let input: PlaybackSessionInput = serde_json::from_value(payload).unwrap();
        assert_eq!(to_session(&input).media_uri.as_deref(), Some("D:\\Music\\song.flac"));
    }

    /// 回归：前端曾直接上送 camelCase TrackInfo，反序列化失败被前端
    /// catch 静默吞掉，导致播放统计从未落盘（全 0）。此测试钉死该形状
    /// 必须被拒绝——若未来给 TrackInfo 加别名放行，请同步删除本测试
    #[test]
    fn session_input_rejects_frontend_camelcase_track() {
        let payload = serde_json::json!({
            "track": {
                "id": "netease:2161589xxx",
                "title": "Song",
                "artist": "Artist",
                "album": "Album",
                "durationMs": 200_000,
                "coverUrl": "https://cover",
                "audioUrl": "",
                "source": "netease",
            },
            "listenedMs": 15_000,
            "playCountIncrement": 1,
        });

        let error = serde_json::from_value::<PlaybackSessionInput>(payload)
            .expect_err("camelCase track 形状必须被拒绝");
        assert!(
            error.to_string().contains("missing field"),
            "失败原因应为缺字段, 实际: {error}"
        );
    }
}
