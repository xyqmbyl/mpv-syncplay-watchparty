# MPV Syncplay WatchParty

这是一个面向 Windows 与 macOS 便携版 mpv 的无聊天同步观影扩展。它保留现有
Syncplay 播放、暂停、跳转、延迟补偿和 MiniServer 行为，并增加：

- mpv 内置的简洁二级侧边栏控制面板；
- 房主本地路径到 AList `/d/...` 公开播放地址的映射；
- 观看者自动载入远程视频、HTTP Range 检测和 READY 等待；
- Tailscale 私网接入与统一的首次运行向导；
- 运行模式在运行时选择：按 `Ctrl+Shift+S` 打开面板，进入「联机」→「运行模式」
  选房主模式或观看者模式，房主与观看者不再需要分别下载安装包；
- 内置弹幕插件 uosc_danmaku，房主与观看者可各自独立开关（默认字号 35、
  显示范围 0.4，样式修改后自动保存为新的默认值）。

项目不会把 AList 管理员 Token、Cookie 或 Tailscale 登录凭据发送给观看者。
播放器主界面、右键菜单和联机面板沿用 v0.3.0 的定制 UI；新版仅补入
Windows/macOS 所需运行组件，打包时会校验 UI 文件，避免上游 uosc 覆盖。

## 从 GitHub 下载后如何开始两人观看

进入 [最新 Release](https://github.com/xyqmbyl/mpv-syncplay-watchparty/releases/latest)
的 `Assets`，按系统和架构下载同一个安装包：

| 系统 | 安装包 |
| --- | --- |
| Windows 64 位 | `WatchParty-Windows-x64.zip` |
| Windows 32 位 | `WatchParty-Windows-x86.zip` |
| Apple Silicon（M 系列芯片） | `WatchParty-macOS-AppleSilicon.zip` |
| Intel Mac | `WatchParty-macOS-Intel.zip` |

房主与观看者下载的是同一个包，角色在安装后于 mpv 面板里现场选择（见下一节），
不需要区分房主包和观看者包。需要校验下载时，同时取得各自同名的 `.sha256`
文件。不确定 Windows 位数时，64 位系统一律选 x64；只有很老的 32 位系统才
需要 x86。不要下载 GitHub 自动生成的 `Source code (zip)`；源码包不含 mpv、
Python、AList 和首次运行所需组件。

### 1. 首次设置（房主与观看者相同）

1. 把 ZIP 完整解压到一个普通文件夹，不要在压缩软件里直接运行。
2. Windows 双击 `WatchParty\首次运行.bat`；macOS 双击解压文件夹顶层的
   `首次设置.command`。向导只为联机做准备，角色留到面板里选择。
3. 按提示安装 Tailscale，并用各自的账号登录。观看者使用自己的 Tailscale
   账号，不要与房主共用账号。
4. 向导结束后 mpv 会自动打开。以后日常启动只需双击
   `WatchParty\启动.bat`（macOS 为 `启动.command`）。

### 2. 选择运行模式

按 `Ctrl+Shift+S` 打开面板，进入「联机」→「运行模式」，选择这台机器本次要
扮演的角色。选择会立即在内存中生效，不会打开第二个 mpv 窗口；随时可以重新
选择。

#### 房主模式

选择房主模式后会自动完成原房主包的全部配置：检测本机 Tailscale IPv4 地址
并写入配置、启用并启动随包 AList（`alist_enabled`），并为 AList 程序写入一条
仅允许 Tailscale `100.64.0.0/10` 访问 AList 5244 的防火墙规则。防火墙这一步
会触发一次 UAC 管理员授权（`runas`）；如果 mpv 已经在管理员权限下运行就不会
弹窗。不要在路由器上给 5244 做端口映射。

打开 Tailscale 管理后台的 `Machines`，找到房主电脑，选择 `Share`，把设备共享
邀请发给观看者，并把面板显示的完整媒体地址、Syncplay 服务器和房间名发给
观看者。只发送形如 `http://100.x.x.x:5244` 的地址，不要发送
`WatchParty\ADMIN_PASSWORD.txt`。

#### 观看者模式

1. 先接受房主发来的设备共享邀请。
2. 在面板里进入「联机」→「运行模式」→「观看者模式」，按提示输入房主发来的
   完整 `http://100.x.x.x:5244`。面板会清除本地发布映射、保持本机不对外
   共享，并从观看者视角检测房主 AList 是否可达。
3. 诊断通过后即可加入房间。

观看者不需要 AList 账号、房主密码或房主的视频文件。地址无法访问时不要跳过
诊断：先确认 Tailscale 已连接、共享邀请已接受、房主电脑在线且面板处于房主
模式，再核对 `100.x` 地址。

当前发布流程使用 Tailscale Device Sharing 后直接访问房主的 `100.x:5244`，
不需要 Tailscale Serve，也不要开启 Funnel。这里的 HTTP 流量仍在 Tailscale
加密隧道内传输；Funnel 则会把服务暴露到公网，不属于本项目的两人观看流程。

### 3. 两人进入同一房间

1. 房主把要共享的视频放入 `WatchParty\media`，两边都双击
   `WatchParty\启动.bat`（macOS 为 `启动.command`）。
2. 两边都在 mpv 中按 `Ctrl+Shift+S` 打开面板，填写完全相同的 Syncplay
   服务器和房间名，并分别填写昵称。默认服务器为 `syncplay.pl:8995`。
3. 两边都在“播放控制”中选择“加入 / 连接房间”。建议观看者先连接完成，
   房主再从 `WatchParty\media` 打开视频。
4. 观看者无需选择本地文件；mpv 会自动载入房主的 AList 地址。观看者加载
   完成并发送 READY 后，两边即可同步播放、暂停和跳转。

`Ctrl+Y` 不是本项目的面板快捷键。完整部署、AList 后台设置和诊断命令见
[`portable_config/syncplay/README.md`](portable_config/syncplay/README.md)。

## 弹幕（房主与观看者各自独立）

安装包内置弹幕插件 `uosc_danmaku`，入口在 mpv 下方的 uosc 控制栏：
`弹幕开关`、`搜索弹幕`、`从源添加弹幕`、`弹幕设置`，也可以按 `j` 直接开关，
按 `Ctrl+d` 打开搜索菜单。

- 弹幕完全在本地渲染：插件只读取本机 mpv 的播放进度，不通过 Syncplay 发送
  任何数据。谁打开弹幕，就只有谁看到；两端互不影响。
- 弹幕跟随本机播放进度：暂停时弹幕冻结，继续播放或跳转后会重新对齐当前
  时间点，因此始终与房主的视频进度一致。
- 默认样式为字号 `35`、显示范围 `0.4`。在 `弹幕设置` 中修改任意样式后，
  改动会立刻写入 `portable_config\script-opts\uosc_danmaku.conf` 并成为新的
  默认值，下次启动仍然生效；“恢复默认”会删除该项并回到内置默认值。
- 本插件是从上游定制的版本（默认值与自动保存行为是本项目的改动）。如果
  在插件菜单里使用“检查更新”，上游版本会覆盖这些定制改动，覆盖后默认字号
  会回到上游数值，需要重新设置一次。

## 验证

运行标准库测试：

```powershell
python -m unittest discover -s portable_config\syncplay -p "test_*.py" -v
python WatchParty\ViewerPackage\test_ui_baseline.py -v
```

可选的真实 mpv IPC 测试：

```powershell
$env:SYNCPLAY_RUN_MPV_INTEGRATION = "1"
python portable_config\syncplay\test_mpv_ipc_real.py -v
```

## 构建分享包

构建器只适用于已有合法 mpv 便携发行版的完整目录（Windows 需 mpv x64/x86
便携版，macOS 需 mpv.app），并使用明确的允许列表复制文件。房主与观看者共用
同一个包：构建期不区分角色、不写入房主地址，包内保持 `server=syncplay.pl:8995`、
`alist_enabled=no`、`tailscale_mode=off`，解压后在面板里选择运行模式之前不会
共享任何内容。

Windows（PowerShell，房主/观看者合并包）：

```powershell
.\WatchParty\ViewerPackage\build-watchparty-package.ps1              # x64 合并包
.\WatchParty\ViewerPackage\build-watchparty-package.ps1 -Arch x86    # x86 合并包
```

默认包名为 `WatchParty-Windows-<Arch>`，输出 `<包名>.zip` 与
`<包名>.zip.sha256`；可用 `-PackageName` 与 `-OutputDirectory` 覆盖包名和
输出目录。源码检出与本机 mpv 运行目录分开时，Windows x64 构建可加
`-NativeRoot "D:\已有的mpv目录"`；x86 可指向 `fetch-artifacts.ps1` 准备的
`artifacts\win-x86`。构建器仍从源码检出复制 v0.3.0 UI。

macOS（bash，区分 Apple Silicon 与 Intel）：

```bash
./WatchParty/ViewerPackage/build-macos-package.sh --arch arm64 [--output-dir DIR]
./WatchParty/ViewerPackage/build-macos-package.sh --arch intel [--output-dir DIR]
```

生成目录、ZIP、逐文件清单及 SHA-256 位于
`WatchParty\ViewerPackage\output`。构建器会拒绝房主配置、AList 数据、媒体、
历史、日志、缓存和凭据类文件，并做运行时冒烟自检。GitHub Actions 会在
推送 `v*` 标签时自动构建合并包的 Windows x86 与两个 macOS 版本；Windows x64
合并包需从已有的 64 位便携运行目录在本机构建，最后把 4 个合并包
（`WatchParty-Windows-x64/x86.zip` 与 `WatchParty-macOS-AppleSilicon/Intel.zip`，
各带同名 `.sha256`）一起上传到 Release。

## 兼容性

旧客户端可继续参与基础播放同步，但只有安装此扩展的观看者才能识别
`media_url` 并自动播放房主共享的视频。AList 扩展只会在服务器声明房间隔离
能力时启用；`syncplay.pl` 和本项目 MiniServer 支持该要求。

## 许可

本仓库原创整合代码采用根目录所示的保留权利声明。mpv、Python、OpenSSL、
uosc、uosc_danmaku、Material Icons、Tailscale 等第三方组件不受该声明覆盖；
版本、来源和许可证见
`WatchParty/ViewerPackage/templates/watchparty-THIRD_PARTY_NOTICES.txt`。
