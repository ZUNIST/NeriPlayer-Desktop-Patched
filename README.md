# NeriPlayer Desktop · 补丁版

<p>
  <a href="https://github.com/ZUNIST/NeriPlayer-Desktop-Patched/releases">
    <img alt="Release" src="https://img.shields.io/github/v/release/ZUNIST/NeriPlayer-Desktop-Patched?label=Release" />
  </a>
  <a href="https://github.com/ZUNIST/NeriPlayer-Desktop-Patched/releases">
    <img alt="Downloads" src="https://img.shields.io/github/downloads/ZUNIST/NeriPlayer-Desktop-Patched/total" />
  </a>
  <img alt="License" src="https://img.shields.io/github/license/ZUNIST/NeriPlayer-Desktop-Patched" />
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-4C8BF5" />
</p>

<p>
  <img src="app-icon.png" width="160" alt="NeriPlayer Desktop" />
</p>

基于 [cwuom/NeriPlayer-Desktop](https://github.com/cwuom/NeriPlayer-Desktop) 的第三方补丁版。
上游是一个用 **Tauri 2 + Vue 3 + Rust** 写的多源在线音乐播放器；本仓库在它的基础上，补回在 Windows 上实际用不了的网易云功能，并修掉几处窗口与下载上的问题。

> [!IMPORTANT]
> **这不是官方版本，也不代表上游立场。**
> 项目原作者的仓库在 [cwuom/NeriPlayer-Desktop](https://github.com/cwuom/NeriPlayer-Desktop)，想要 macOS / Linux 构建、完整功能说明或上游更新，请去那里。
> 本仓库**只提供 Windows 构建**，改动集中在 Windows 与网易云这条链路上。

---

## 为什么有这个版本

上游的功能面很完整，但在 Windows 上日常使用时，有几处会直接卡住：搜索拿不到结果、歌单加不进去、B 站音源下不下来、窗口尺寸在反复最大化后被拉小。

这些问题大多不是上游写得不好，而是平台接口变化和 Windows 窗口消息时序造成的。本仓库把能修的都修了，顺手补齐了几个缺失的交互。

改动逐条记录在 [MODIFICATIONS.md](./MODIFICATIONS.md)，包含根因分析和踩坑记录，共 20 节。

## 与上游的差异

### 修复

| 问题 | 表现 |
| --- | --- |
| 网易云搜索 | 登录账号下搜索结果为空（接口返回加密内容），改用另一套搜索接口并批量补封面 |
| B 站音源下载 | DASH 分片被当成完整音频，校验必然失败；403 后无法续期，只能一直重试同一个失效链接 |
| 窗口尺寸 | ① 启动时偶发变小 ② 反复最大化 / 还原几次后被拉小 ③ 最大化状态关闭后，下次启动先闪一个小窗口再变大 |

### 补回的网易云能力

- **云端歌单增删** —— 歌曲可加入自建歌单，也可从歌单移除，含封面解析与缓存同步
- **专辑评论** —— 专辑页可看评论、发评论、回复楼层
- **评论点赞** —— 走 WebView 完成安全验证后写入，验证窗口不会出现在前台
- **行内跳转** —— 曲目行的歌手名、专辑名可直接点开；多歌手时弹菜单选择
- **歌手页改版** —— 专辑改为网格卡片、头部精简、tab 紧凑化，歌曲行加了「更多」按钮

### 播放页

- **评论 / 队列浮层** —— 改为在播放页内就地覆盖歌词区，深色玻璃底，从下往上浮入
- **浅色模式柔光遮罩** —— 整屏压 8% 亮度，只压亮部、不改任何配色变量，桌面歌词与托盘弹窗不受影响
- **跟随封面淡色染色** —— 按当前封面的主色轻微染色背景，默认关闭，可调强度，关掉即完全回退
- **工具栏下载按钮** —— 原位置的睡眠定时器换成下载，复用已有的下载状态机（下载 / 取消 / 重下 / 失败）

### 新增

- **自动下载常听歌曲** —— 听到一定程度的网易云歌曲自动存到本地，之后离线也能听。
  判定看**最近 10 天内的播放次数**与**平均听完整度**，而不是累计次数——三个月前循环过、现在早就不听的歌不算常听。默认关闭，可在设置里调窗口、门槛与每日上限。

## 下载与安装

到 [Releases](https://github.com/ZUNIST/NeriPlayer-Desktop-Patched/releases/latest) 下载 `NeriPlayer-Desktop-Patched-win64-vX.Y.Z.zip`，解压后得到：

```text
neri-player-desktop.exe
uninstall.exe
ffmpeg/
```

解压到任意目录直接运行，无需安装。覆盖升级时**先退出 App**（托盘菜单里的「退出」，不是点窗口关闭按钮）再替换 exe。

系统要求：Windows 10/11 x64，需要 WebView2 运行时（Win11 自带，Win10 若缺失会自动提示安装）。

首次启动会在 `%APPDATA%\moe.ouom.neriplayer.desktop\` 下生成配置与播放统计。

## 与上游同步

上游更新后，本仓库的合并流程：

```bash
git clone https://github.com/ZUNIST/NeriPlayer-Desktop-Patched
git remote add upstream https://github.com/cwuom/NeriPlayer-Desktop
git fetch upstream && git merge upstream/main
python scripts/patch_all.py        # 重新打 26 处核心补丁
pnpm build && npx tauri build --no-bundle
```

`patch_all.py` 只覆盖核心改动，UI 相关的部分需要按 `MODIFICATIONS.md` 末尾的「构建陷阱清单」逐条核对补回。

## 构建

依赖：Node.js 20+、pnpm、Rust stable、Visual Studio Build Tools（C++ 桌面开发）。

```bash
pnpm install
pnpm build                                              # 含 vue-tsc 类型检查
npx tauri build --no-bundle --config tauri.buildonly.json
```

产物在 `src-tauri/target/release/neri-player-desktop.exe`。

两个容易踩的点：

- **不要用 `cargo build`**，前端资源不会被打包进 exe，运行起来是空白窗口
- `vite.config.ts` 设了 `emptyOutDir: false`，多次构建会在 `dist/assets` 里累积大量旧 hash 文件，exe 会越来越大。清理时直接重建整个 `dist`，**不要按修改时间删文件**——内容没变的 chunk 不会重写，mtime 停留在旧值，按时间删会误删仍在引用的文件

## 已知限制

这些是平台侧限制，客户端改不了：

- **数字专辑 / 独家首发曲目无法收藏**，接口返回 `524 当前环境异常，已取消喜欢`；`401` 表示歌曲已下架。同批次普通曲目正常，属曲目级版权限制
- **数字专辑 / 单曲付费内容无法下载**，即便账号是 VIP 也需要单独购买。普通 VIP 曲目可以正常下载
- **评论点赞依赖 WebView 完成安全验证**，网络异常时会失败，重试即可

## 许可证与致谢

本项目是 [cwuom/NeriPlayer-Desktop](https://github.com/cwuom/NeriPlayer-Desktop) 的衍生作品，原作者为 **cwuom**。上游以 **GPL-3.0-or-later** 发布，本仓库沿用同一许可证，原始 `LICENSE` 与版权声明完整保留；2026-10-07 之前以 MIT 发布的代码保留其原声明（见 `LICENSES/MIT.txt`）。

项目名称与图标灵感来源于《星空鉄道とシロの旅》中的角色「风又音理」，版权归原作者所有。

`README_EN.md` 为上游英文文档，未同步本仓库的改动，仅供参考。

## 免责声明

本项目仅供个人学习与研究使用，与网易云音乐、哔哩哔哩、YouTube 官方无关，所有音乐内容版权归各自权利人所有。

请只在你拥有权利、授权或第三方平台规则允许的范围内访问、播放或保存内容。本项目不提供媒体内容、密钥，也不提供规避付费 / DRM / 地区限制的方案。

本仓库及维护者不接受任何形式的赞助、捐赠或商业资助。
