# Tailscale 远程联机

这个目录把 Tailscale 接入随包 AList 与 mpv Syncplay 面板。Tailscale 只负责
建立加密网络；视频仍由 AList 提供，播放同步仍由现有 Syncplay 客户端完成。
集成脚本不会保存账号、密码、OAuth 信息或 auth key。

## 一个安装包，两种角色

房主和观看者下载的是同一个 Release 包（Windows 为
`WatchParty-Windows-x64.zip` / `-x86.zip`，macOS 为
`WatchParty-macOS-arm64.zip` / `-intel.zip`）。角色不在下载时决定，而是装好
之后在 mpv 面板中现场选择：

```text
按 Ctrl+Shift+S  →  联机  →  运行模式  →  房主模式 / 观看者模式 / 仅本机观看
```

包内同时带有 AList，所以观看者也可以随时改用房主模式，无需再下载别的包。
下载后请完整解压，不要下载 `Source code (zip)`，也不要在压缩软件里直接运行。

## 房主首次设置

1. 完整解压后运行 `WatchParty\首次运行.bat`（macOS 为包根目录的
   `首次设置.command`）。允许一次 Windows 管理员权限后，脚本会为当前 AList
   建立仅允许 `100.64.0.0/10` 访问 TCP 5244 的防火墙规则，并按需打开
   Tailscale 官方安装程序。
2. 在 Tailscale 托盘程序中使用房主自己的账号登录（不要多人共用房主账号）。
3. 回到 mpv，按 `Ctrl+Shift+S` 进入 `联机 -> 运行模式`，选择 `房主模式`。
   向导会读取本机的 `100.x` IPv4，把面板中的 `alist_server` 更新为
   `http://房主100.x地址:5244`，并完成随包 AList 的匿名 Range 自检。
4. 打开 Tailscale 管理后台 `Machines`，找到房主电脑，选择 `Share`，按提示
   生成设备共享邀请并发给观看者。
5. 把向导显示的完整 `http://100.x.x.x:5244` 媒体地址、Syncplay 服务器和
   房间名发给观看者。不要发送房主的账号密码或
   `WatchParty\ADMIN_PASSWORD.txt`。
6. 日常观影运行 `WatchParty\启动.bat`（macOS 为 `启动.command`），然后在
   mpv 的 Syncplay 面板中进入约定房间。

当前推荐架构是 `Device Sharing + http://100.x:5244`：不使用 Tailscale
Serve，也不要启用 Funnel。`http://100.x...` 的流量仍在 Tailscale 加密隧道
内；Funnel 会把服务公开到互联网，不属于本项目的观看流程。也不要在路由器
上把 5244 映射到公网。

## 观看者首次设置

1. 解压同一个 Release 包，不要下载 `Source code (zip)`，也不要在压缩软件里
   直接运行。
2. 接受房主发送的设备共享邀请。在自己的电脑安装 Tailscale，并使用自己的
   账号登录；不要多人共用房主账号。
3. 按 `Ctrl+Shift+S` 进入 `联机 -> 运行模式`，选择 `观看者模式`。面板会先
   启动已校验的官方 Tailscale 安装程序（如尚未安装），再提示确认房主发来的
   完整 `http://100.x.x.x:5244` 地址；不一致时输入房主发来的地址。
4. 向导会保存纯观看者配置、清除本地媒体映射，并从观看者视角检查房主 AList
   可达性。
5. 在面板中选择与房主完全相同的 Syncplay 服务器和房间，按
   `播放控制 -> 加入 / 连接房间` 进入。日常只需运行
   `WatchParty\启动.bat`（macOS 为 `启动.command`）。

观看者无需 AList 账号、房主密码或本地视频。房主 `100.x` 地址变化时，重新
在 `联机 -> 运行模式` 里选择一次 `观看者模式` 并输入新的完整地址，然后按
面板提示重新连接房间。

## 切换角色

- 改角色后必须重新选择 `播放控制 -> 加入 / 连接房间`：媒体地址参数只在
  Syncplay 客户端启动时读取，面板会先停掉正在运行的客户端。
- 想恢复成不联机、只用本机播放，选择 `仅本机观看`。它会把配置改回
  `alist_enabled=no` 并关闭 AList 映射。
- 三种模式都可以反复切换，不会重新下载任何东西。

## mpv 面板

按 `Ctrl+Shift+S` 打开面板：

- `联机 -> 运行模式`：选择房主模式 / 观看者模式 / 仅本机观看；
- 左侧 `Tailscale` 栏可以查看 Tailscale 是否安装、是否已连接及本机
  `100.x` 地址；
- 可以启动本目录中已校验的官方安装包，或打开 Tailscale 登录界面；
- 观看者可以输入房主的完整 `http://100.x.x.x:5244` 地址并保存；
- 可以刷新状态，重新读取安装、登录和地址信息。

房主首次部署请以面板的 `房主模式` 为准，不要用旧的 `.ts.net / Serve` 地址
替换向导生成的 `http://100.x.x.x:5244`。

## 安全边界

- Tailscale 不会绕过 AList 的应用层权限。`/media` 仍需允许匿名只读访问，
  并保持全局签名、存储签名和元信息密码关闭。
- 只把 `WatchParty\media` 暴露给 guest，不要挂载整个磁盘。
- AList 管理员密码必须保持强且唯一，绝不能发给观看者。
- 推荐只共享房主设备，并把访问策略限制到该设备的 TCP 5244。
- 保持 Funnel 关闭，不配置公网端口映射；无需启用 Serve。
- 防火墙规则固定限制在 `100.64.0.0/10`，不会向局域网或公网开放 5244。
- 观影结束后可在 Tailscale 管理后台撤销设备共享。

## 排查

运行 `status.bat` 可查看本机状态。房主配置成功后，应看到：

```text
状态：Running
本机地址：100.x.x.x（以向导实际显示为准）
媒体地址：http://100.x.x.x:5244
```

如果双击批处理时出现“文件名、目录名或卷标语法不正确”、`echo` 未找到或乱码，
请重新解压 Release 中的完整包，不要用文本编辑器重新保存 `.bat`。当前脚本固定
为 UTF-8 无 BOM、CRLF 换行的 Windows `cmd.exe` 格式，并会优先调用项目根目录的
`tailscale.exe`；观看者向导会通过内置检测兼容官方当前的 `Tailscale IPN` 安装目录，
不需要手动修改 PATH。源码仓库不包含完整的 mpv/Python 运行时，房主应在已有的
mpv 便携目录中运行脚本；观看者应使用 Release 的完整 ZIP。

观看者最终仍需用项目中的 `[AList Media Test]` 验证实际视频地址返回
`HTTP 206` 和有效 `Content-Range`。若 Tailscale 显示 `relay`，视频可能经
DERP 中继而速度较慢；这不是 Syncplay 同步故障。

观看者连不上时依次确认：已接受设备共享邀请、双方 Tailscale 均为
`Running`、房主已运行 `WatchParty\启动.bat` 且面板处于 `房主模式`、地址仍是
房主当前的 `http://100.x.x.x:5244`。如果本机能访问但观看者不能访问，再检查
Windows 防火墙是否拦截 AList 的 5244 入站连接；不要用开启 Funnel 来绕过故障。
