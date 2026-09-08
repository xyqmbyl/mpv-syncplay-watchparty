# Tailscale 远程联机

这个目录把 Tailscale 接入随包 AList 与 mpv Syncplay 面板。Tailscale 只负责
建立加密网络；视频仍由 AList 提供，播放同步仍由现有 Syncplay 客户端完成。
集成脚本不会保存账号、密码、OAuth 信息或 auth key。

## 房主首次设置

1. 从完整房主包运行 `WatchParty\房主首次运行.bat`，不要从 GitHub 自动生成
   的源码 ZIP 运行。允许 Windows 管理员权限后，脚本会为当前 AList 建立仅
   允许 `100.64.0.0/10` 访问 TCP 5244 的防火墙规则，并按需打开 Tailscale
   官方安装程序。
2. 在 Tailscale 托盘程序中使用房主自己的账号登录。向导会读取本机的
   `100.x` IPv4，把面板中的 `alist_server` 更新为
   `http://房主100.x地址:5244`，并完成随包 AList 的匿名 Range 自检。
3. 打开 Tailscale 管理后台 `Machines`，找到房主电脑，选择 `Share`，按提示
   生成设备共享邀请并发给观看者。
4. 把向导显示的完整 `http://100.x.x.x:5244` 媒体地址、Syncplay 服务器和
   房间名发给观看者。不要发送房主的账号密码或
   `WatchParty\ADMIN_PASSWORD.txt`。
5. 日常观影运行 `WatchParty\启动.bat`，然后在 mpv 的 Syncplay 面板中进入
   约定房间。

当前推荐架构是 `Device Sharing + http://100.x:5244`：不使用 Tailscale
Serve，也不要启用 Funnel。`http://100.x...` 的流量仍在 Tailscale 加密隧道
内；Funnel 会把服务公开到互联网，不属于本项目的观看流程。也不要在路由器
上把 5244 映射到公网。

## 观看者首次设置

1. 从 GitHub Release 的 `Assets` 下载 `MPV-Syncplay-Viewer.zip`，完整解压，
   不要下载 `Source code (zip)`，也不要在压缩软件里直接运行。
2. 接受房主发送的设备共享邀请。在自己的电脑安装 Tailscale，并使用自己的
   账号登录；不要多人共用房主账号。
3. 双击包根目录的 `观看者首次运行.bat`。等 Tailscale 显示已连接后，确认
   预设地址与房主发来的 `http://100.x.x.x:5244` 完全一致；不一致时输入房主
   发来的完整地址。
4. 向导会保存纯观看者配置、清除本地媒体映射，并从观看者视角检查房主 AList
   可达性。诊断通过后会打开 mpv。
5. 按 `Ctrl+Shift+S`，进入与房主完全相同的 Syncplay 服务器和房间。以后只
   需运行包根目录的 `启动观看.bat`。

观看者无需 AList 账号、房主密码或本地视频。房主 `100.x` 地址变化时，重新
运行 `WatchParty\Tailscale\configure-viewer.bat` 并输入新的完整地址，然后
重新启动 mpv。

## mpv 面板

按 `Ctrl+Shift+S` 打开面板，再选择左侧 `Tailscale`：

- 可以查看 Tailscale 是否安装、是否已连接及本机 `100.x` 地址；
- 可以启动本目录中已校验的官方 MSI，或打开 Tailscale 登录界面；
- 观看者可以输入房主的完整 `http://100.x.x.x:5244` 地址并保存；
- 可以刷新状态，重新读取安装、登录和地址信息。

房主首次部署请以 `WatchParty\房主首次运行.bat` 为准，不要用旧的
`.ts.net / Serve` 地址替换向导生成的 `http://100.x.x.x:5244`。

更改媒体地址时，已运行的 Syncplay 客户端会先断开。配置完成后重新选择
`播放控制 -> 加入 / 连接房间`。

## 安全边界

- Tailscale 不会绕过 AList 的应用层权限。`/media` 仍需允许匿名只读访问，
  并保持全局签名、存储签名和元信息密码关闭。
- 只把 `WatchParty\media` 暴露给 guest，不要挂载整个磁盘。
- AList 管理员密码必须保持强且唯一，绝不能发给观看者。
- 推荐只共享房主设备，并把访问策略限制到该设备的 TCP 5244。
- 保持 Funnel 关闭，不配置公网端口映射；无需启用 Serve。
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
`Running`、房主已运行 `WatchParty\启动.bat`、地址仍是房主当前的
`http://100.x.x.x:5244`。如果本机能访问但观看者不能访问，再检查 Windows
防火墙是否拦截 AList 的 5244 入站连接；不要用开启 Funnel 来绕过故障。
