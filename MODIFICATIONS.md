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

## 9. 已知限制（非客户端可解）

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
