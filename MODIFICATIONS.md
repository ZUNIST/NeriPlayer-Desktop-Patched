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

## 4. 行内歌手 / 专辑跳转

曲目行的歌手名与专辑名可点击跳转对应页面。

- `TrackInfo` 新增可选字段 `artistId`、`albumId`、`artists`
- 网易云歌单/专辑页直接使用接口返回的 `ar[]` / `al.id`
- 本地歌单与「我喜欢的音乐」没有预存 id，点击时查询一次 `get_netease_song_detail`
- **多歌手**（`ar` 长度 > 1）弹出选择菜单，单歌手直接跳转

## 5. 歌手页

- **热门排序**：上游用 `/api/v1/artist/songs?order=hot`，但该接口的 `hot` 排序已失效（`pop` 恒为 5.0，实际按专辑顺序返回）。改为先取 `/api/v1/artist/top/song`（`pop` 为真实热度，顺序与官方一致），再接全量分页、按 id 去重
- **专辑墙**：竖排列表改为网格卡片（`auto-fill, minmax(148px, 1fr)`）
- **头部紧凑化**：去掉全宽背景图，改为 120px 圆形头像 + 右侧信息；歌手名、统计、按钮同处第一行，整体高度由约 200px 降至约 140px
- **Tab**：由两个等宽胶囊改为左对齐的紧凑胶囊

## 6. 其他改进

| 项 | 说明 |
|---|---|
| 窗口记忆 | 引入 `tauri-plugin-window-state`；因窗口初始 `visible: false`，Windows 不允许对隐藏窗口最大化，故在 `show()` 之后通过 `apply_window_state` 命令补一次恢复 |
| 启动黑窗 | `security.rs` 原用 `Command::new("reg")` 读取 MachineGuid，Windows 下会闪一下控制台；改为 `winreg` 直接读注册表 |
| 音乐库记忆 | 记住上次停留的分区（本地/收藏/下载/网易云/B站/YouTube），存 key 而非下标 |
| 首页顺序 | 「每日推荐」提到第一位 |
| 侧边栏 | 图标组垂直居中并上移约一个图标高度 |
| 收藏提示 | 失败时透出业务码；`524`/`401` 分流为「该歌曲受版权限制，暂不支持云端同步」 |

## 7. 已知限制（非客户端可解）

`/weapi/song/like` 对**数字专辑 / 独家首发**曲目返回：

```json
{"code": 524, "msg": "当前环境异常，已取消喜欢"}
```

`401` 则表示歌曲已下架。经同批次对照测试（普通曲目返回 200），确认是**曲目级版权限制**，不是环境或登录问题。网易云用「环境异常」掩盖了真实原因，第三方客户端无法绕过。

---

## 构建

```bash
pnpm install && pnpm build
echo '{"build":{"beforeBuildCommand":""}}' > tauri.buildonly.json
npx tauri build --no-bundle --config tauri.buildonly.json
```

前置要求：Rust（MSVC 工具链）+ Visual Studio Build Tools + Node 18+。

**不要用 `cargo build` 替代**——它不会嵌入前端资源，产物打开是空白页。判断构建是否成功看 `.rdata` 段是否约 39 MB。

## 许可证

上游为 **GPL-3.0-or-later**，本衍生版本沿用同一许可证，原始 `LICENSE` 与版权声明完整保留。
2026-10-07 之前以 MIT 发布的代码保留其原声明（见 `LICENSES/MIT.txt`）。

## 免责声明

本项目仅供个人学习与自用，与网易云音乐、哔哩哔哩、YouTube 官方无关。所有音乐内容版权归各自权利人所有。
