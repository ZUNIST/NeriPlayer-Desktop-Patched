# 相对上游的修改说明

本仓库基于 [cwuom/NeriPlayer-Desktop](https://github.com/cwuom/NeriPlayer-Desktop) 的 `692ffb3` 版本（`fix(player): correct playback UI and background rendering (#58)`）修改而来。

**修改动机**：上游版本的网易云搜索在接口变更后失效，且「添加到歌单」只能写本地。本版本修复这些问题，并补上若干日常使用中缺失的能力。

---

## 1. 网易云搜索修复（核心）

**问题**：`/weapi/cloudsearch/get/web` 对**已登录**账号返回 `{"result": "<2 万字符 hex 密文>"}`，对**匿名**请求返回 `{"code": 50000005}`。两条路径都取不到 `result.songs`，表现为搜索永远为空。

**修改**（`src-tauri/src/api/netease/client.rs`）：

- `search()` / `cloudsearch()` 改用 `/weapi/search/get`，该接口在登录与匿名状态下均返回明文
- 字段做双向兼容：`ar`/`artists`、`al`/`album`、`dt`/`duration`
- 新增 `song_cover_map()`，通过 `/weapi/v3/song/detail` 批量补齐封面（`search/get` 不带封面）

## 2. 网易云歌单增删

上游的「添加到歌单」只写入本地 `PlaylistStore`，云端歌单只能看不能改。

**新增**（`client.rs` + `recommend_cmd.rs` + `main.rs`）：

- `manipulate_playlist_tracks(pid, ids, add)` → `/weapi/playlist/manipulate/tracks`
  参数：`op`(add/del)、`pid`、`trackIds`(JSON 字符串)、`imme`、`csrf_token`
- 命令 `add_tracks_to_netease_playlist` / `remove_tracks_from_netease_playlist`
- 「添加到歌单」对话框新增「网易云 - 歌单」分组，**只列自建歌单**（过滤 `subscribed` 与 `specialType === 5`）
- 歌单详情页右键菜单新增「从歌单中移除」，删除后同步重写详情缓存

**约束**：`specialType === 5` 是「我喜欢的音乐」，属特殊歌单，普通接口无法写入，只能走 `/song/like`；因此从列表中排除，避免用户点到必然失败的入口。

## 3. 专辑评论

**线程前缀是 `R_AL_3_{id}`，不是 `R_AL_0_`**——写错时接口不报错，只是静默返回 `totalCount: 0`，极易误判成「这张专辑没有评论」。

- `client.rs`：抽出 `get_resource_comments(thread_id, ...)`，歌曲用 `R_SO_4_`、专辑用 `R_AL_3_`
- `comment_cmd.rs`：`get_comments` 的 `target` 支持 `album:<id>` 前缀，新增 `netease_thread_id()` 统一解析；发评论、楼层回复也走同一解析
- 前端：专辑页在「全部播放」右侧新增评论按钮，图标与播放页一致（`chat_bubble`）

## 4. 评论点赞 / 发评论的安全验证

网易云的评论写操作（点赞、发评论、回复）不认普通 cookie，必须带**易盾 checkToken**。

### 踩坑记录

**① 纯 HTTP 取令牌的接口已下线**

```
POST /weapi/comment/checktoken/get
→ {"code": 404, "message": "接口未找到！"}
```

不带令牌直接点赞的返回：

```json
{"code": 250, "msg": "系统检测到您的设备存在安全风险，请更换设备后再尝试操作哦"}
```

**② 只能靠隐藏 WebView 跑易盾脚本**

`comment_token.rs` 的做法：新建一个 WebView → 打开 `music.163.com` → 注入 `acstatic-dun.126.net/tool.min.js` → `initWatchman` → `WM.getToken(businessId)` → 通过导航到保留域名的回调地址把令牌交回 Rust。

**③ 关键：窗口不能 `visible(false)`**

```rust
.visible(false)   // ❌ 这样写永远拿不到令牌
```

隐藏窗口里 `document.visibilityState === 'hidden'`，易盾据此判定为自动化环境，`getToken` 直接回调空字符串，前端表现为**「安全验证未通过」**。

必须让窗口处于"可见"状态（`visibleState === 'visible'`）。

**④ 但窗口又不能真的让用户看到**

按这个顺序试过，记录结果供参考：

| 做法 | 结果 |
|---|---|
| `visible(false)` | 验证失败 |
| `visible(true)` + 创建后 `set_position(-4000,-4000)` | 功能正常，但屏幕中央**闪一下** |
| `visible(false)` → `set_position` → `show()` | 验证失败（`visibilityState` 切换是异步的） |
| `visible(true)` + builder `.position(-32000,-32000)` | 坐标被 Windows 忽略，回落居中 |
| `visible(true)` + builder `.position(-4000,-4000)` | 仍居中（位置参数未生效） |
| `visible(true)` + Win32 `SetWindowPos` 到 -4000 | 仍闪（窗口已在屏幕上画了一帧） |
| **`visible(true)` + `always_on_bottom(true)`** | **当前采用**：被主窗口盖住，功能稳定，仅在主窗口非全屏时可能露出 |

**结论**：功能优先。窗口视觉上的残留需要更底层的处理（比如创建时即 0×0 再 resize），收益不大，暂不深究。



## 5. 行内歌手 / 专辑跳转

曲目行的歌手名与专辑名可点击跳转对应页面。

- `TrackInfo` 新增可选字段 `artistId`、`albumId`、`artists`
- 网易云歌单/专辑页直接使用接口返回的 `ar[]` / `al.id`
- 本地歌单与「我喜欢的音乐」没有预存 id，点击时查询一次 `get_netease_song_detail`
- **多歌手**（`ar` 长度 > 1）弹出选择菜单，单歌手直接跳转

## 6. 歌手页

- **热门排序**：上游用 `/api/v1/artist/songs?order=hot`，但该接口的 `hot` 排序已失效（`pop` 恒为 5.0，实际按专辑顺序返回）。改为先取 `/api/v1/artist/top/song`（`pop` 为真实热度，顺序与官方一致），再接全量分页、按 id 去重
- **专辑墙**：竖排列表改为网格卡片（`auto-fill, minmax(148px, 1fr)`）
- **头部紧凑化**：去掉全宽背景图，改为 120px 圆形头像 + 右侧信息；歌手名、统计、按钮同处第一行，整体高度由约 200px 降至约 140px
- **Tab**：由两个等宽胶囊改为左对齐的紧凑胶囊

## 7. 其他改进

| 项 | 说明 |
|---|---|
| 窗口记忆 | 引入 `tauri-plugin-window-state`；因窗口初始 `visible: false`，Windows 不允许对隐藏窗口最大化，故在 `show()` 之后通过 `apply_window_state` 命令补一次恢复 |
| 启动黑窗 | `security.rs` 原用 `Command::new("reg")` 读取 MachineGuid，Windows 下会闪一下控制台；改为 `winreg` 直接读注册表 |
| 音乐库记忆 | 记住上次停留的分区（本地/收藏/下载/网易云/B站/YouTube），存 key 而非下标 |
| 首页顺序 | 「每日推荐」提到第一位 |
| 侧边栏 | 图标组垂直居中并上移约一个图标高度 |
| 收藏提示 | 失败时透出业务码；`524`/`401` 分流为「该歌曲受版权限制，暂不支持云端同步」 |

## 8. 播放页评论 / 队列浮层

把评论与播放队列从侧边栏改成播放页右侧的就地浮层，覆盖歌词区，视觉上沿用播放页同一套深色玻璃。

| 项 | 实现 |
|---|---|
| 就地渲染 | `NowPlaying.vue` 的 `.np-right` 作为定位基准，两个面板直接渲染；原先 `Teleport to body` 的目标未挂载时会静默失败（按钮有动画、面板不出现） |
| 玻璃底色 | 两个面板统一为播放页同系深色玻璃 `rgb(0 0 0 / .24)` + `blur(20px) saturate(150%)` + `1px` 白边；宽 60%、高 `calc(100% - 12px)`、右侧留 2px、圆角 18px；发评论区去掉底色 |
| 文字 | 深色玻璃上统一白字（面板根 `color:#fff` + 覆盖 `--md-on-surface*` 与容器/胶囊配色变量），否则浅色主题会继承黑字 |
| 进场动画 | 从下往上浮 24px + opacity，`<Transition>`，`transform 260ms` / `opacity 200ms`。**队列的 inline 选择器必须比非 inline 通用规则多一个 `.queue-overlay`**，否则通用规则位置靠后会把它覆盖成「从右侧滑入且无淡入淡出」 |
| 开关 | 面板打开时再次点击同一按钮即关闭（评论、队列各自独立） |
| 文字可读性 | 面板根显式 `color: #fff` 并覆盖 `--md-on-surface*`；否则浅色主题下会继承全局黑字 |
| 当前播放项 | 歌名由主题色改纯白加粗、左侧音频均衡器条改纯白、去掉最左侧竖色条 |
| 清空队列按钮 | 由 `--md-error` 红色纯文字改为**中性细边胶囊**（方案 A）：透明底 + 白 `1px` 细边 + 白字，hover 只做中性加深；非浮层/浅色主题用 `--md-on-surface` + `--md-outline-variant`，避免白底白字 |

## 9. 浅色模式柔光遮罩

浅色主题的 surface 集中在 `#E6E2E8 ~ #FCF8FD`，整屏近白、层级差只有 5~7 个亮度点，长时间看刺眼。参考 iOS「降低白点值」的做法：**不改任何配色变量，只压亮部峰值**。

| 项 | 实现 |
|---|---|
| 唯一可调参数 | `.light-theme` 里的 `--light-dim-strength: 0.08`；改这一个数字即可换强度（0 关闭、0.04 极轻、0.12 上限参考） |
| 遮罩 | `body::after` 全屏 `position: fixed` + `rgba(28, 25, 22, var(--light-dim-strength, 0))`，`pointer-events: none`，`z-index: 2147483647` |
| 只在浅色生效 | `html.light-theme:not(.desktop-lyrics-page):not(.tray-popup-page) body::after { opacity: 1 }`；深色时变量回退 0 且 opacity 0，`opacity .3s` 与主题切换同步 |
| 排除透明窗口 | 桌面歌词 `desktop-lyrics-page`、托盘弹窗 `tray-popup-page` 的 html/body/#app 背景是 `transparent`，叠层会铺出灰块，必须排除 |
| 与动态取色关系 | `themeColor.ts` / `colorExtractor.ts` 的 `applyThemeVars` 在浅色下只写色彩令牌，surface 始终由 `.light-theme` 提供；本遮罩不碰这些变量，换封面 / 换主题色都不受影响 |
| 取舍 | 文字与专辑封面一起被轻压（与 iOS 行为一致）；播放页深色玻璃浮层也会略暗，8% 以内不明显；两个独立透明窗口不受影响 |

## 10. 播放时跟随封面的淡色染色（可一键回退）

- 设置项位于「设置 → 背景」顶部：`跟随封面染色` 开关（**默认关闭**）+ 开启后出现的 `染色强度` 滑块（4%–32%，默认 12%）。关掉开关即完全回到 `v1.3.0` 之后的纯白浅色，柔光遮罩恢复 8%。
- 颜色来源：`src/utils/ambientTint.ts` 独立从当前封面取色，用 `extractPalette(data, 16, true)`（与播放页一致，`isDark=true`）拿到 `accentBg`，再走 `resolveNowPlayingAccentBg()` 压暗。`NowPlaying.vue` 的 `accentBgStyle` 改为调用同一函数，两处颜色严格一致。
- **不要依赖 `NowPlaying.vue` 取色**：它是 `v-if="isNowPlayingOpen"`，播放页收起后组件卸载，首页拿不到颜色。App.vue 常驻 watch 负责刷新 `:root` 的 `--np-ambient-bg`，切歌自动更新。
- **也不要用 `colorExtractor.ts` 的 `accentBg`**：浅色模式下它传 `isDark=false`，`computeAccentBg` 会给出近乎白色的 `targetL=0.90`，和播放页深底不是一回事。
- CSS 生效条件：`html.light-theme.ambient-tint-on.ambient-tint-ready:not(.has-custom-bg)`（`ready` 只在封面取色成功后添加，失败时自动回白并保留 8% 遮罩，不会变成「更白」），另排除 `desktop-lyrics-page` / `tray-popup-page` 两个透明窗口；只覆盖 `--md-background` / `--md-surface` / `--md-surface-dim`，卡片容器保持白色以留层次；`--np-ambient-bg` 缺失时 `color-mix` 回退成 `#FCF8FD`，不会铺出脏色块。
- 染色生效时把 `--light-dim-strength` 置 0，避免与第 9 节的 8% 柔光遮罩叠加压暗。自定义背景图开启时由 `:not(.has-custom-bg)` 自动让位。
- **新增设置必须同步 Rust**：`src-tauri/src/settings/store.rs` 的 `AppSettings` 结构体、`Default` 与 `normalize()` 都要加同名字段，否则 `save_settings` 会把新字段丢掉（结构体有 `#[serde(default)]`，旧配置文件能平滑升级）。

## 11. 播放页工具栏：睡眠定时器改为下载按钮

- `NowPlaying.vue` 工具栏第 3 个按钮（原 `timer` 图标 + 倒计时 badge + 定时菜单）替换为「下载当前歌曲」：图标用已有的 `downloadActionIcon`（`download` / `downloading` / `cancel` / `refresh` / `error`），点击调用已有的 `handleDownloadAction()`，与「更多」面板里的下载项完全同一条逻辑（下载中可取消、已下载可重新下载）。
- tooltip / aria 用 `downloadActionLabel`（下载 / 下载中 / 重新下载 / 取消）；`currentSource === 'local'` 或 `downloadActionDisabled` 时按钮禁用但位置保留，工具栏图标数量不变。
- 顺带清掉组件内全部 sleep 死代码：`showSleepMenu` ref、`sleepOptions` / `handleSleepOption` / `formatSleepRemaining`、`closeToolbarPopovers` 与 `toggleToolbarPanel` 的 `'sleep'` 分支、Escape 关闭判定，以及 `.sleep-wrap` / `.sleep-popover` / `.sleep-option` / `.sleep-badge` 样式（组合选择器只删 sleep 部分，保留 speed 规则）。
- **只改播放页这个入口**：`stores/player.ts` 的睡眠定时器逻辑未动，但播放页原本是 App 内唯一的可视入口，换掉后界面上不再有设置定时的地方；四个语言文件里的 `player.sleep_*` 文案也保留未删。

## 12. 已知限制（非客户端可解）

`/weapi/song/like` 对**数字专辑 / 独家首发**曲目返回：

```json
{"code": 524, "msg": "当前环境异常，已取消喜欢"}
```

`401` 则表示歌曲已下架。经同批次对照测试（普通曲目返回 200），确认是**曲目级版权限制**，不是环境或登录问题。网易云用「环境异常」掩盖了真实原因，第三方客户端无法绕过。

## 13. B 站（哔哩哔哩）源下载修复

B 站 `fnval=272` 返回的是 DASH 音频分片（`.m4s`），原下载链路按「完整 mp3/flac」处理：容器认不出、拿视频时长做严格比对、写标签失败直接判失败、403 只重试同一个带签名的 URL。四件事叠加导致 B 站曲目几乎必然下载失败。本次全部改在 Rust 后端，前端调用方式不变：

- **容器识别**：`downloaded_audio_extension()` 新增 `is_fragmented_mp4_head()`，按 ISO BMFF box 链认出以 `styp` / `sidx` / `moof` / `mdat` 起头的分片 MP4，存成 `.m4a`，而不是 Content-Type 兜底的 `.mp3`。
- **宽松校验**：`validate_download_audio()` 新增 `relaxed_bilibili` 参数（仅 `source == "bilibili"` 打开）。lofty 读不出标签容器时降级用 symphonia 解码器验证；时长扫描不出来时退回来源时长；来源给的是**视频时长**、音轨常短几秒，因此跳过严格时长偏差判定。其它平台仍保持原有的 MD5 / 长度 / 时长 / 取消校验。
- **403 重新解析 + CDN 轮换**：B 站下载改走 `recovery::run`，遇 403 / 401 / 410 / 5xx 由 `refresh_bilibili_download_source()` 重新走 playurl 取新签名直链，并依次轮换 `candidate_urls` 里的 backup CDN。`bili_download_target()` 从 `trackId` / `album` 还原 bvid / avid / cid，保证多 P 视频不会重新解析到第一 P。
- **Range 对齐播放端**：`recovery::request()` 对 B 站 CDN 统一带 `Range: bytes=0-`（播放端本来就是这么拉的），避免边缘节点拒绝不带 Range 的整文件 GET；206 / 200 都按成功处理。
- **附加元数据降级**：B 站下载时歌词/封面 sidecar 与音频标签写入失败只记 warning，状态记为 `EMBED_FAILED`，音频本体照常落盘入清单；其它源仍按原逻辑严格失败。

单测：`download_cmd.rs` 增加分片容器识别、多 P 目标还原、B 站时长偏差放行（同时验证严格模式仍拒绝）；`download_recovery.rs` 用本地 HTTP 服务器验证 Range 头只加在 B 站 URL 上。

**验证边界**：本机网络直连 B 站 playurl 被拦，无法抓真实 DASH 响应做端到端回归；以上按代码路径与仓库已有的 `hls-init.mp4` + `hls-segment.m4s` fixture 验证。若某条流仍失败，需要下载任务的具体报错文案再收窄处理。

---

## 14. 关于页的 GitHub 入口指向本补丁仓库

上游**没有内置自动更新器**：`src-tauri/Cargo.toml` 不含 `tauri-plugin-updater`，`tauri.conf.json` 无 `plugins.updater` 配置，`README_EN.md` 也明确写明「no in-app automatic updater. Install the relevant Release manually.」。因此官方版本不会在后台覆盖本机补丁，此前「必须关闭自动更新」的提醒属于多余 precaution；后续版本仍建议保持 App 更新设置不变，但不存在被静默替换的机制。

App 内唯一指向「去哪获取新版本」的入口是 **设置 → 关于 → GitHub 仓库** 卡片，原值 `https://github.com/cwuom/NeriPlayer-Desktop`。本次改为：

- `src/views/SettingsView.vue` 的 `PROJECT_REPOSITORY_URL` → `https://github.com/ZUNIST/NeriPlayer-Desktop-Patched`（源码与 Release 都在该仓库）。
- `src/i18n/{zh-CN,zh-TW,en,ja}.json` 的 `settings.github_desc`：从「查看源代码」改为提示补丁源码与版本更新，四语言同步。

**刻意未改**的两处官方引用：`src/modules/audioEffects/model.ts` 与 `src-tauri/src/lyrics/external.rs` 的 `USER_AGENT` 中带 `cwuom/NeriPlayer` 的注释/字符串——前者是 GPL-3.0 要求的原始作者署名，必须保留；后者只是外部歌词服务的 User-Agent 标识，改动反而可能让服务端拒绝识别，且与更新源无关。`src-tauri/src/sync/github_api.rs` 的 `GITHUB_API_BASE` 属于云同步备份功能（owner/repo 由用户在设置里自行配置），同样与更新源无关。

如果将来需要真正的「App 内检查更新 / 一键安装」，必须引入 `tauri-plugin-updater`、配置 `latest.json` 与 minisign 公钥，并在自己的 Release 里产出对应签名产物；仅替换链接不等于自动更新。

---

## 15. 修复「启动时窗口突然变小」

`tauri-plugin-window-state` 保存状态时，若窗口处于最大化，记录的 `width/height` 是**最大化时的物理尺寸**（如 1728×1296），同时 `maximized: true`。它的 `restore_state` 恢复时仍会先按该尺寸 `set_size`、再 `maximize()`；而刚 show 的窗口在 Windows 上 maximize 偶发不生效，窗口就停在「最大化的中间尺寸」上——表现为打开窗口时突然比预期小一圈，且带 DPI 换算偏差，偶发且难复现。

修复（`src-tauri/src/main.rs`）：

- 插件初始化加 `.skip_initial_state("main")`，`main` 的初始恢复由 show 后的 `apply_window_state` 接管；其余窗口（`tray-popup`）仍走插件逻辑。
- `apply_window_state` 对 `main` 分支处理：读 `.window-state.json`，`maximized` 时只 `set_position(prev_*)` + `maximize()`，**不再 set_size**；非最大化才恢复保存的位置与尺寸（物理像素）。彻底消除中间尺寸闪现。
- 插件加 `.with_filter(|label| label == "main" || label == "tray-popup")`，一次性验证窗口（`netease-comment-token-<uuid>`，每次点赞/发评都会生成新 uuid）不再写进状态文件；启动时 `cleanup_window_state_file()` 清掉历史遗留条目，文件不再无限膨胀。

---

## 16. 歌手页歌曲行增加「更多」按钮（方案 B）

四个歌手页（网易云 / B 站 / 本地 / YouTube）的歌曲行原先只有右键菜单，没有歌单 / 专辑页那种可见的 ⋮ 按钮。本次补齐，且**复用页面已有的 `TrackContextMenu` 实例**，不新造菜单、不复制菜单项：

- `src/views/NeteaseArtistView.vue`、`LocalArtistView.vue`（行容器是 `div`）：时长后加 `<button class="track-more">`，`@click.stop="trackMenuRef?.open($event, track)"`。
- `src/views/BiliArtistView.vue`、`YouTubeArtistView.vue`（行容器本身是 `<button>`）：用 `<span class="track-more" style="cursor: pointer">` 避免按钮嵌套；YouTube 走页面已有的 `openTrackMenu($event, section, item)`，以便菜单里的「播放」仍能按 section 定位。
- 按钮外观直接复用 `src/styles/detail-view.scss` 里的 `.track-more`（32px 圆形、平时 `opacity: 0`、hover 行 0.6、hover 按钮 1 + 加深底色），四个页面都已 `@use '@/styles/detail-view.scss' as *`，无需新增样式。
- `TrackContextMenu.open()` 本身已区分事件类型：`contextmenu` 在光标处弹出，普通 click 则按 `rect.left / rect.bottom + 4` 贴着按钮弹出，并自行 `preventDefault` + `stopPropagation`。因此点 ⋮ 与右键弹出的是**同一个菜单**（播放 / 下一首播放 / 添加到队列 / 添加到歌单 / 下载 / 复制歌曲名 / 复制歌名与歌手，另有按曲目出现的「管理跳过区间」「在文件夹中显示」），单击整行仍照常播放。

注：歌单 / 专辑页的 ⋮ 用的是另一套 `ContextMenu`，菜单里含「多选」和「从歌单中移除」；歌手页没有多选模式，也不属于任何歌单，故未照搬。

---

## 17. 再修「反复最大化 / 还原后窗口变小」

第 15 节只覆盖「启动时恢复 `maximized: true`」这一条路径。实测短时间内连续最大化 / 最小化 3~4 次仍会触发窗口变小，原因是**另外两层竞态叠加**：

1. **运行时被拉回普通尺寸**：`src/modules/settings/uiScale.ts` 的 `installUiScale()` 监听 `onResized` / `onMoved`，100ms debounce 后串行执行 `apply()`。`apply()` 开头用一次 `Promise.all` 取 `innerSize()` 与 `isMaximized()` 快照，之后又隔着 `setMinSize()` 等多次 IPC 才调用 `setSize(new LogicalSize(width, height))`。快速切换时快照与执行时刻错配；Windows 对**最大化窗口**调用 `setSize` 会直接取消最大化并停在传入的普通尺寸，而 `height` 又被 `Math.min(availableHeight, ...)` 夹到工作区高度，于是窗口稳定停在一个偏小的尺寸且不会自愈。`ui_scale_supported()` 在非 macOS 恒为 `true`，这条链路在 Windows 上始终启用。
2. **状态文件被写脏**：`tauri-plugin-window-state` 的 `Resized` handler 与退出时 `update_state()` 都靠 `is_maximized()` 过滤，快速切换时同样错配，把「最大化时的物理尺寸 + 屏幕原点 (0,0)」写成 `maximized: false`。第 15 节的 `apply_window_state` 只处理 `maximized: true`，遇到这种条目会走 else 分支照常 `set_size(1728×1305)`，于是下次启动窗口依旧异常。

修复：

- `uiScale.ts`：`apply(value, resizeWindow = false)`。resize 触发的 `scheduleRefresh` **只刷新缩放上限与 `setMinSize`**，绝不再 `setSize` / `setPosition`；启动时也传 `false`，把窗口尺寸完全交给原生状态恢复。仅当用户主动修改 UI 缩放（`watch`）时才传 `true` 允许尺寸修正，且执行前**重新查询** `isMaximized` / `isFullscreen` / `innerSize` 二次确认，用此刻尺寸重算；状态已变就放弃尺寸修正（缩放本身照常应用）。另外把 `uiScaleLimitForSize()` 的入参改成**只依赖显示器工作区**，不再掺入 `outer - inner` 边框——最大化时 Windows 的不可见边框会变，会让 `percent` 抖动、改写 `settings.uiScale`、间接触发 `watch → setSize`。`setMinSize` 是原生下限约束，已足以阻止窗口被拖到布局之外。
- `src-tauri/src/main.rs`：新增 `classify_saved_size()`，用 `window.current_monitor()` 的屏幕物理尺寸与 DPI 把持久化条目分为 `Usable` / `LooksMaximized` / `Invalid`。`maximized: false` 但尺寸覆盖约 90% 屏幕且位置贴近原点 → 判为 `LooksMaximized`，只 `maximize()` 不 `set_size`；尺寸小于布局下限 960×720 或超过屏幕 → 判为 `Invalid`，**完全不碰窗口**，保留配置默认尺寸。`SavedWindowState` 全部字段加 `#[serde(default)]`，避免单个条目缺字段导致整份文件解析失败。
- 状态文件路径统一走 `window_state_path()`，优先 `app_config_dir()`（与插件写入一致），回退 `app_data_dir()`；`cleanup_window_state_file()` 复用同一函数。

**验证边界**：本机 `.window-state.json` 在复现后确为 `{"main": {"width": 1728, "height": 1305, "maximized": false, "x": 0, "y": 0, "prev_x": -11, "prev_y": -11, ...}}`，与上述判断吻合；但快速切换属时序问题，仍需按「来回最大化 / 最小化 3~4 次」实际操作回归确认。

---

## 构建

```bash
pnpm install && pnpm build
echo '{"build":{"beforeBuildCommand":""}}' > tauri.buildonly.json
npx tauri build --no-bundle --config tauri.buildonly.json
```

前置要求：Rust（MSVC 工具链）+ Visual Studio Build Tools + Node 18+。

**不要用 `cargo build` 替代**——它不会嵌入前端资源，产物打开是空白页。判断构建是否成功看 `.rdata` 段是否约 39 MB。

### 构建时的坑

1. **必须用 MSVC 工具链**。`x86_64-pc-windows-gnu` 能编过，但链接时报
   `ld.exe: .rsrc merge failure: multiple non-default manifests`，产物嵌了两个同 ID 的 manifest，
   WebView2 自定义协议注册失败，启动即「无法访问此页面」。

2. **`cargo build --release` 不行**，必须 `npx tauri build --no-bundle`。
   前者产物约 48 MB 且 `.rdata` 明显偏小。**别用总体积判断**——MSVC 把代码段压小了，
   正好和缺失的资源量抵消，容易误以为成功。

3. **Vite 清 dist 会被批量删除保护拦截**（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`，阈值 50 文件）。
   正确做法是先向使用者请求授权再清理，不要换命令或换语言去绕开拦截。
   本地 `vite.config.ts` 暂以 `build.emptyOutDir: false` 规避清空；代价是旧 hash 文件累积在
   `dist/assets/`，Tauri 会把它们一并嵌进 exe，体积逐次变大，授权后定期清一次即可。

4. **浅克隆推不上去**。`git clone --depth 1` 后直接 `git push` 会报
   `remote unpack failed: index-pack failed` / `did not receive expected object`。
   需先 `git fetch --unshallow <上游地址>` 补全历史。

5. **编译可能卡死**：cargo/rustc 进程在但 CPU 十分钟不涨，`target/release/deps` 最新文件 mtime 停滞。
   处理：终止这两个进程后重跑，增量通常 2~3 分钟。

6. **部署报 `Device or resource busy`**：进程句柄未释放，等 5~7 秒再覆盖。

7. **`cargo` 不在 PATH**：`npx tauri build` 报
   `failed to run 'cargo metadata' ... program not found`（Git Bash 会话常见）。
   先 `export PATH="/c/Users/zuni/.cargo/bin:$PATH"` 再构建。

### 补丁脚本

`scripts/patch_all.py` 可在上游更新后重新应用 26 处核心补丁（锚点匹配，跨版本比 git diff 稳）：

```bash
python scripts/patch_all.py
```

覆盖范围：搜索修复、歌单增删、收藏错误链路、侧边栏居中、启动黑窗消除。
**不在其中**的（需对照本文档手工补）：专辑评论、行内导航、歌手页改版、窗口状态插件。


## 许可证

上游为 **GPL-3.0-or-later**，本衍生版本沿用同一许可证，原始 `LICENSE` 与版权声明完整保留。
2026-10-07 之前以 MIT 发布的代码保留其原声明（见 `LICENSES/MIT.txt`）。

## 免责声明

本项目仅供个人学习与自用，与网易云音乐、哔哩哔哩、YouTube 官方无关。所有音乐内容版权归各自权利人所有。
