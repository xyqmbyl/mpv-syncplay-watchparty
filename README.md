# MPV Syncplay WatchParty

这是一个面向 Windows 便携版 mpv 的无聊天同步观影扩展。它保留现有
Syncplay 播放、暂停、跳转、延迟补偿和 MiniServer 行为，并增加：

- mpv 内置的简洁二级侧边栏控制面板；
- 房主本地路径到 AList `/d/...` 公开播放地址的映射；
- 观看者自动载入远程视频、HTTP Range 检测和 READY 等待；
- Tailscale 私网接入与观看者首次运行向导。

项目不会把 AList 管理员 Token、Cookie 或 Tailscale 登录凭据发送给观看者。

## 从 GitHub 下载后如何开始两人观看

进入 [最新 Release](https://github.com/xyqmbyl/mpv-syncplay-watchparty/releases/latest)
的 `Assets`，房主下载 `MPV-Syncplay-Host.zip`，观看者下载
`MPV-Syncplay-Viewer.zip`。需要校验下载时，同时取得各自同名的 `.sha256`
文件。不要下载 GitHub 自动生成的 `Source code (zip)`；源码包不含 mpv、
Python、AList 和首次运行所需组件。

### 1. 房主首次设置

1. 把房主 ZIP 完整解压到一个普通文件夹，不要在压缩软件里直接运行。
2. 双击 `WatchParty\房主首次运行.bat`，在 Windows 询问时允许管理员权限。
   脚本会自动建立一条仅允许 Tailscale `100.64.0.0/10` 访问 AList 5244 的
   防火墙规则；不要在路由器上给 5244 做端口映射。
3. 按提示安装 Tailscale，并用房主自己的账号登录。向导会启动随包 AList，
   配置 `/media` 匿名只读访问、关闭签名、验证 HTTP Range，并把媒体地址写成
   `http://房主的100.x地址:5244`。
4. 打开 Tailscale 管理后台的 `Machines`，找到房主电脑，选择 `Share`，把
   设备共享邀请发给观看者。观看者使用自己的 Tailscale 账号接受邀请，不要
   与房主共用账号。
5. 把向导显示的完整媒体地址、Syncplay 服务器和房间名发给观看者。只发送
   形如 `http://100.x.x.x:5244` 的地址，不要发送
   `WatchParty\ADMIN_PASSWORD.txt`。

当前发布流程使用 Tailscale Device Sharing 后直接访问房主的 `100.x:5244`，
不需要 Tailscale Serve，也不要开启 Funnel。这里的 HTTP 流量仍在 Tailscale
加密隧道内传输；Funnel 则会把服务暴露到公网，不属于本项目的两人观看流程。

### 2. 观看者首次设置

1. 先接受房主发来的设备共享邀请，再完整解压观看者 ZIP。
2. 双击 `观看者首次运行.bat`，按提示安装 Tailscale，并用观看者自己的账号
   登录。等 Tailscale 显示已连接后回到向导。
3. 确认预设媒体地址与房主发来的一致；若不一致，输入完整的
   `http://100.x.x.x:5244`。向导会清除本地发布映射，并从观看者视角检测
   房主 AList 是否可达。
4. 诊断通过后 mpv 会自动打开。以后观看者只需双击 `启动观看.bat`。

观看者不需要 AList 账号、房主密码或房主的视频文件。地址无法访问时不要
跳过诊断：先确认 Tailscale 已连接、共享邀请已接受、房主电脑在线且已经运行
`WatchParty\启动.bat`，再核对 `100.x` 地址。

### 3. 两人进入同一房间

1. 房主把要共享的视频放入 `WatchParty\media`，双击
   `WatchParty\启动.bat`；观看者双击 `启动观看.bat`。
2. 两边都在 mpv 中按 `Ctrl+Shift+S` 打开面板，填写完全相同的 Syncplay
   服务器和房间名，并分别填写昵称。默认服务器为 `syncplay.pl:8995`。
3. 两边都在“播放控制”中选择“加入 / 连接房间”。建议观看者先连接完成，
   房主再从 `WatchParty\media` 打开视频。
4. 观看者无需选择本地文件；mpv 会自动载入房主的 AList 地址。观看者加载
   完成并发送 READY 后，两边即可同步播放、暂停和跳转。

`Ctrl+Y` 不是本项目的面板快捷键。完整部署、AList 后台设置和诊断命令见
[`portable_config/syncplay/README.md`](portable_config/syncplay/README.md)。

## 验证

运行标准库测试：

```powershell
python -m unittest discover -s portable_config\syncplay -p "test_*.py" -v
```

可选的真实 mpv IPC 测试：

```powershell
$env:SYNCPLAY_RUN_MPV_INTEGRATION = "1"
python portable_config\syncplay\test_mpv_ipc_real.py -v
```

## 构建观看者包

构建器只适用于已有合法 mpv Windows 便携发行版的完整目录，并使用明确的
允许列表复制文件：

```powershell
.\WatchParty\ViewerPackage\build-viewer-package.ps1 `
  -TailscaleHost "100.100.20.30" # 示例；替换为向导显示的实际地址
```

参数填写房主首次向导显示的 Tailscale `100.x` IPv4；构建器会在观看者包中
预设 `http://该地址:5244`。地址变化后应重新构建，或让观看者在首次向导中
输入新地址。

生成目录、ZIP、逐文件清单及 SHA-256 位于
`WatchParty\ViewerPackage\output`。构建器会拒绝房主配置、AList 数据、媒体、
历史、日志、缓存和凭据类文件。

## 兼容性

旧客户端可继续参与基础播放同步，但只有安装此扩展的观看者才能识别
`media_url` 并自动播放房主共享的视频。AList 扩展只会在服务器声明房间隔离
能力时启用；`syncplay.pl` 和本项目 MiniServer 支持该要求。

## 许可

本仓库原创整合代码采用根目录所示的保留权利声明。mpv、Python、OpenSSL、
uosc、Material Icons、Tailscale 等第三方组件不受该声明覆盖；版本、来源和
许可证见 `WatchParty/ViewerPackage/templates/THIRD_PARTY_NOTICES.txt`。
