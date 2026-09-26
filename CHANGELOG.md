# Changelog

## 0.5.1 - 2026-09-26

- 修复房主模式在本机存在多份 WatchParty 副本时报「AList 管理员登录失败」的
  问题：5244 端口被另一份副本的 AList 占用时，包会复用它却拿本目录的密码去
  登录。现在会识别占用者、停止它并改用本目录的 AList 与密码；数据目录与密码
  不一致时自动重新设置。同一台机器只保留一份 WatchParty 即可避免冲突。
- 修复面板「点击没反应」：单个操作出错不再终止整个 Lua 脚本，面板保持可用；
  网络配置长时间无响应会在 5 分钟后自动中止并提示，不再锁死后续操作。
- 新增回归测试覆盖多副本占用、密码自愈与面板错误隔离。

## 0.5.0 - 2026-09-26

- 合并房主包与观看者包为单一安装包：同一架构只发布一个包，房主与观看者下载
  同一个 ZIP，构建期不再写入房主地址、也不再区分角色。
- 联机面板二级菜单新增「运行模式」：按 `Ctrl+Shift+S` 打开面板，进入
  「联机」→「运行模式」即可选择房主模式或观看者模式，不需要重启或另外下载
  一个包。
- 选中房主模式后自动完成原房主包的全部配置：检测本机 Tailscale IPv4 地址并
  写入配置、启用并启动随包 AList，并为 AList 程序写入仅放行 Tailscale
  `100.64.0.0/10` 的防火墙规则（触发一次 UAC 授权，已提权时不再弹窗）；选中
  观看者模式则指向房主的 AList 地址并保持本机不共享。
- 修复观看者切回房主时丢失 `alist_root` 的问题：从观看者模式恢复到房主模式
  不再清空已保存的媒体发布目录配置。
- 切换运行模式的过程不再产生第二个 mpv 窗口，配置在内存中即时生效。
- 构建脚本与 CI 同步为 4 个合并包：Windows x64/x86 与 macOS
  AppleSilicon/Intel，每个包附同名 `.sha256` 校验文件。
- 修复 Windows 包解压到一半报「未指定的错误 (0x80004005)」的问题：Windows
  PowerShell 5.1 的 `ZipFile::CreateFromDirectory` 会把归档条目名写成反斜杠
  分隔（`dir\file`），不符合 ZIP 规范，资源管理器与部分解压器会把整串路径当成
  单个非法文件名。现在改为规范的正斜杠条目名，并补上目录条目与文件属性；
  包内文件内容与之前逐字节一致，v0.5.0 的 Windows 附件已就地替换（未另发版本）。
- 修复 Windows 包内置 Python 缺少 `_ctypes.pyd` 与 `libffi-8.dll` 的问题：
  在面板选择「房主模式」触发防火墙提权时，`watchparty_setup.py` 会直接
  `ModuleNotFoundError`/`ImportError` 崩溃（首次运行向导、观看者模式与仅本机
  观看不受影响）。构建脚本改为通配携带源运行时的全部 `.pyd` 与 `libffi-8.dll`，
  并把 `import ctypes` 纳入构建自检，防止再漏带扩展模块。
- 修复随包 AList 启动失败时错误提示本身崩溃的问题：`ensure_alist` 的报错
  文案格式化参数顺序放错，会把 `TypeError` 堆栈甩给面板，用户看不到真实原因。
  现在 AList 的启动输出会落到 `WatchParty/alist/data/log/server-stdout.log`，
  启动失败时提示附带退出码与最近输出；macOS 上启动前还会自动清除 AList 的
  隔离标记（兜底直接双击 mpv.app、没走 `.command` 入口的场景），面板调用
  的设置脚本遇到未预期异常时也改为回传 JSON 错误而不是 Python 堆栈。
- 修复另外两处同类「报错文案自崩」：`ensure_alist` 的端口占用提示与 AList
  管理员登录失败提示都多传了一个格式化参数，真实错误（端口被占用/密码与
  数据目录不匹配）会被 `TypeError: not all arguments converted during string
  formatting` 盖住；两条提示修正后可正常显示。新增 4 个失败分支报错文案的
  回归测试。macOS 清除隔离标记的范围从 AList 目录扩大到整个安装包，
  覆盖 uosc 依赖的 ziggy（被隔离时表现为面板菜单无法打开/无法输入）。
- 修复面板「运行模式」输入框的两个地址陷阱：房主模式切换观看者时输入框
  会预填本机共享地址，直接回车会指向自己；现在仅观看者模式预填房主地址，
  房主模式下点击「切换为观看者模式」会提示先填入对方的 100.x.x.x 地址。

- 修复「运行模式」在部分情况下点击后毫无反应的问题：`syncplay_ui.lua` 里
  `current_mode` 的声明位置排在调用点之后，而 Lua 局部变量在声明前不可见，
  于是点击「切换为观看者模式」会抛出
  `attempt to call global 'current_mode' (a nil value)` 并静默中断，面板既不
  跳转也不提示。现已把该函数上移到调用点之前。此缺陷由本次新增的观看者地址
  保护引入，房主模式与仅本机观看不受影响。
- 房主模式不再每次都弹 UAC：写入防火墙规则前先查询是否已有同名规则
  `MPV Syncplay WatchParty AList`，若已放行 `100.64.0.0/10` 则直接复用、不再
  提权，重复切换秒级完成。
- 改善房主模式的进度反馈：配置提示延长到 12 秒并注明「如出现"用户账户控制"
  请点"是"」，网络配置进行中在「运行模式」菜单显示「正在配置网络，请稍候再
  点」，避免把等待授权误判成「点了没反应」。
- 新增回归测试：Lua 面板的「局部变量先声明后使用」静态检查、菜单动作与处理
  分支的一致性检查，以及防火墙规则复用的探测用例。
- 修复同一台机器上有第二份 WatchParty 副本时，点击房主模式报「AList 管理员
  登录失败」的问题：5244 端口已被那份副本的 `alist.exe` 占用时，向导原先会
  直接复用该进程，却把新生成的密码写进本目录，登录必然失败（即使侥幸登录，
  共享的也是那份副本的 `media` 目录）。现在会通过 `netstat` 与进程映像路径
  找到占用者：确认是"另一个 WatchParty 副本"就停掉它并改用本目录的 AList；
  是别的程序则仍然只提示不处理，查不到进程时保守复用，绝不误杀。
- 管理员密码与数据目录不匹配时自动自愈：`ADMIN_PASSWORD.txt` 与当前 AList
  数据目录不一致时，向导会用包内 `alist admin set` 重设一次密码再登录，
  并在仍然失败时明确提示"5244 端口可能由另一个副本提供"及处理步骤；
  登录失败提示也补充了数据目录位置。
- 新增 15 个回归测试：`netstat` 输出与系统语言无关的解析、占用者路径判定、
  "绝不误杀未知进程"的保护、异副本接管流程，以及密码自愈/自愈失败的报错。

## 0.4.3 - 2026-09-25

- macOS 启动脚本（首次设置/日常启动 × 房主/观看者）现在会在运行开始时
  递归清除本包的 `com.apple.quarantine` 隔离标记：浏览器下载的 ZIP 解压后
  所有文件都带隔离属性，未公证的 mpv/Python/ziggy/AList 此前会被 macOS
  直接拒绝运行，表现为双击后没有任何窗口（"UI 不显示"）。
- 启动脚本新增失败诊断：内置 Python 或 mpv 无法运行时，直接给出可复制的
  `xattr -cr` 修复命令，而不是静默失败。
- macOS 使用说明修正 `.command` 的实际位置（解压文件夹顶层，与
  `WatchParty`、`mpv.app` 平级），并补充 macOS 15 的
  系统设置 → 隐私与安全性 → "仍要打开" 放行路径（右键打开在新版已失效）。

## 0.4.2 - 2026-09-25

- Restored the complete customized v0.3.0 uosc player UI, including its
  playback controls, right-click menu, scaling and danmaku entry points. The
  Syncplay side panel and synchronization core remain unchanged.
- Pinned all 29 v0.3.0 uosc UI files in the source tree and added a regression
  test that compares their SHA-256 hashes with the published v0.3.0 package.
- Updated Windows and macOS builders to use that UI baseline while supplying
  only the platform-specific Ziggy helper from the verified upstream archive.
- Added package-level UI checks to CI so a future dependency refresh cannot
  silently replace the customized interface again.

## 0.4.1 - 2026-09-25

- Fixed the invalid Material Icons font that made mpv display icon names such
  as `chevron_right` instead of icons. Windows builds now reject a damaged font.
- Pinned the font download to a verified upstream commit and corrected its
  SHA-256 for both Windows and macOS CI builds.
- Moved the Intel Mac CI job off the retired `macos-13` runner.
- Corrected Windows CI's embedded-Python help check for non-UTF-8 runner locales
  and extracted the nested `mpv.tar.gz` inside official macOS mpv ZIP assets.
- Fixed macOS staging so the uosc files land at the expected path and viewer
  packages do not accidentally include the host-only AList service or media.

## 0.4.0 - 2026-09-24

- Added macOS packages for Apple Silicon and Intel: `build-macos-package.sh`
  builds host and viewer bundles with mpv, the python.org standalone runtime,
  AList, Tailscale, uosc, fonts, first-run setup and the same audit rules as
  the Windows packages; binaries keep their official signatures with an
  ad-hoc fallback so Gatekeeper does not flag unpacked files as damaged.
- Split the Windows packages into x64 and x86 builds: x64 reuses the proven
  64-bit runtime, x86 bundles the official mpv 0.41.0 i686 runtime plus the
  python.org 3.14.2 embedded distribution; every packaged binary is audited
  for its PE machine type and every shipped installer has a matching
  per-architecture SHA-256 pin.
- Simplified release download names to `WatchParty-Host-Windows-x64.zip`,
  `WatchParty-Viewer-Windows-x86.zip`, `WatchParty-Host-macOS-AppleSilicon.zip`
  and so on, each accompanied by a `.sha256` checksum file and a
  `PACKAGE-CONTENTS.sha256` manifest inside the archive.
- Release viewer packages now ship without a preset host address: the
  first-run wizard asks for the host's Tailscale address until one is
  entered. `-TailscaleHost` / `--tailscale-host` still bakes a preconfigured
  package for private distribution.
- Made the Tailscale integration cross-platform: Windows keeps the bundled
  per-architecture MSI, macOS uses the official Standalone pkg and
  `/Applications/Tailscale.app` CLI, and the first-run wizards now accept the
  host address interactively.
- Added a GitHub Actions workflow that builds and audits the six non-x64
  packages from a `v*` tag push.
- Fixed viewers being treated as not ready while still buffering without a
  `time-pos` during the paused loading phase, which left them on a black
  screen after the host started playback; readiness now also checks the
  media duration and established audio/video tracks.

## 0.3.0 - 2026-09-23

- Bundled the danmaku plugin uosc_danmaku 2.2.0 (MIT) into both the host and
  the viewer package, together with its license text and third-party notices.
- Changed the danmaku defaults to a font size of 35 and a display area of 0.4,
  and every style change made in the danmaku settings menu is now written to
  `portable_config/script-opts/uosc_danmaku.conf` as the new default, so it
  survives a restart; “restore default” removes the entry again.
- Kept danmaku a purely local OSD overlay driven by the local `time-pos`
  observer: the host and each viewer toggle it independently, it freezes while
  the video is paused and realigns on seek. Syncplay playback, pause, seek,
  delay compensation and MiniServer behaviour are unchanged.
- Extended both packaging audits to require the bundled danmaku scripts and
  license while still rejecting `danmaku-history.json` and machine state.

## 0.2.0 - 2026-09-09

- Added a complete portable host package with mpv, embedded Python, Syncplay,
  AList v3.64.0, first-run setup, launch helpers, licenses, and reproducible
  package manifests.
- Standardized remote media access on Tailscale Device Sharing and direct
  `http://100.x.x.x:5244` URLs while keeping Funnel and Serve disabled.
- Added host automation for AList startup, `0.0.0.0:5244`, `/media`, anonymous
  guest access, signing removal, Tailscale address discovery, and Range 206
  verification.
- Added viewer-side diagnostics which distinguish installation, login, Device
  Share, TCP 5244, `/ping`, guest, signing, and HTTP Range failures in Chinese.
- Added clean-install and repeat-run coverage, including preservation of an
  existing administrator password and `alist_map` configuration.
- Fixed the AList v3 setting-save request used by a completely new database.
- Updated the two-person quick-start documentation and host/viewer packaging
  audits.

## 0.1.2 - 2026-09-09

- Added clear diagnostics when the host/viewer scripts are run from a source
  checkout that does not contain the full mpv/Python runtime.
- Hardened the helper scripts for paths containing `!` when delayed expansion
  is enabled in the parent `cmd.exe` process.
- Refreshed the viewer package and checksum after the batch compatibility fixes.

## 0.1.1 - 2026-09-09

- Fixed Windows `cmd.exe` path and encoding errors in the Tailscale batch helpers;
  they are now UTF-8 without a BOM and use CRLF line endings for reliable
  Explorer/double-click execution; the embedded Python output encoding is
  explicitly set to UTF-8 so status text is readable in the Windows console.
- Host, viewer, and status helpers now prefer the matching Tailscale CLI beside
  the portable player instead of relying on a stale inherited PATH.
- Viewer startup now finds the current official `Tailscale IPN` installation
  through the bundled helper, while retaining compatibility with older paths.
- Added a regression test for bundled Tailscale CLI discovery.

## 0.1.0 - 2026-09-08

- Added the compact two-level Syncplay control panel for mpv.
- Added AList local-path to public-media URL mapping.
- Added remote media probing, HTTP Range diagnostics, and actionable errors.
- Added automatic remote loading with cache and readiness coordination.
- Added Tailscale host/viewer setup helpers.
- Added a minimal, audited Windows viewer package builder.
- Fixed typed mpv IPC property writes for pause, speed, cache, and read-ahead.
