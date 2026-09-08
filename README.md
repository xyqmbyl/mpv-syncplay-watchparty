# MPV Syncplay WatchParty

这是一个面向 Windows 便携版 mpv 的无聊天同步观影扩展。它保留现有
Syncplay 播放、暂停、跳转、延迟补偿和 MiniServer 行为，并增加：

- mpv 内置的简洁二级侧边栏控制面板；
- 房主本地路径到 AList `/d/...` 公开播放地址的映射；
- 观看者自动载入远程视频、HTTP Range 检测和 READY 等待；
- Tailscale 私网接入与观看者首次运行向导。

项目不会把 AList 管理员 Token、Cookie 或 Tailscale 登录凭据发送给观看者。

## 观看者下载使用

从 [最新 Release](https://github.com/xyqmbyl/mpv-syncplay-watchparty/releases/latest)
下载 `MPV-Syncplay-Viewer.zip` 和同名 `.sha256` 文件。

1. 完整解压 ZIP，双击 `观看者首次运行.bat`。
2. 按界面安装 Tailscale，并用观看者自己的账号登录。
3. 接受房主发出的 Tailscale 设备共享邀请。
4. mpv 打开后按 `Ctrl+Shift+S`，设置与房主相同的 Syncplay 服务器、房间和昵称。
5. 在“播放控制”中加入房间。观看者不需要选择或下载房主的视频。

以后只需双击 `启动观看.bat`。`Ctrl+Y` 不是本项目的面板快捷键。

首次运行仍然需要安装并登录 Tailscale，这是访问控制的一部分，不能通过在
公开压缩包中放入房主账号或密钥来省略。

## 房主流程

1. 启动 AList，并确认专用 `/media` 存储允许游客只读访问且关闭签名。
2. 登录 Tailscale，运行 `WatchParty\Tailscale\configure-host.bat`。
3. 在 Tailscale 管理后台把房主设备共享给观看者账号，保持 Funnel 关闭。
4. 启动 mpv，按 `Ctrl+Shift+S` 进入相同的 Syncplay 房间。
5. 打开已映射到 AList `/media` 的本地视频。观看者会自动载入远程地址；全部
   READY 后继续播放。

完整部署、AList 后台设置和诊断命令见
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
  -TailscaleHost "host.example-tailnet.ts.net"
```

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
