# AGENTS.md — MPV Syncplay WatchParty

> 本文件是 ZCode、Codex、DeepSeek Harness 在本项目中共同遵守的协作规范。
> 任何 Agent 开工前必须完整阅读；修改本文件需在提交信息中注明。
> 最近更新：2026-09-26。

## 项目目标

面向 Windows 与 macOS 便携版 mpv 的**无聊天同步观影**扩展。保留 Syncplay
播放/暂停/跳转/延迟补偿/MiniServer 行为，在其上增加：

- mpv 内置两级侧边栏联机面板（`Ctrl+Shift+S`），沿用 v0.3.0 定制 UI；
- 房主本地路径 → AList `/d/...` 公开播放地址映射；
- 观看者自动载入远程视频、HTTP Range 检测、READY 等待协调；
- Tailscale Device Sharing 私网接入 + 统一的首次运行向导；

**已完成的重构（2026-09）**：房主包/观看者包已合并为单一 WatchParty 包
（`WatchParty-Windows-x64/x86.zip`、macOS 双架构），包内初始为"未选择模式、
不共享"的安全状态，运行模式（房主/观看者/仅本机）在 mpv 面板
`Ctrl+Shift+S → 联机 → 运行模式` 内切换；切换房主模式时自动完成 Tailscale
地址写入、AList 启动与防火墙放行（单次 UAC）。

## 技术栈

- 播放器：mpv 便携版 0.41.0 基线（Windows x64/x86、macOS Intel/Apple Silicon）
- 播放器脚本：Lua（`syncplay_ui.lua` 联机面板、uosc UI、uosc_danmaku 弹幕）
- 集成层：Python 3.14，**仅标准库**，禁止引入第三方依赖
- 同步：Syncplay 协议，默认服务器 `syncplay.pl:8995`
- 媒体服务：AList v3.64.0（commit `3e49fa46`，SHA-256 固定），端口 5244
- 组网：Tailscale 1.102.3（SHA-256 固定），Device Sharing 直连 `http://100.x.x.x:5244`
- 打包：PowerShell（Windows x64/x86）、bash（macOS arm64/intel）、GitHub Actions CI
- 测试：标准库 `unittest`

## 目录结构

**源码仓库是唯一开发目标**：`D:\MPV-Duke-Enhanced-Extra\WatchParty\repo-latest\`

```
repo-latest/
├── WatchParty/
│   ├── ViewerPackage/
│   │   ├── build-watchparty-package.ps1   # Windows 合并包构建器 (x64/x86)
│   │   ├── build-macos-package.sh         # macOS 合并包构建器 (--arch arm64/intel)
│   │   ├── fetch-artifacts.ps1            # 构建期下载合并 uosc/字体等上游工件
│   │   ├── templates/                     # 包内配置模板（watchparty-*、macos/）
│   │   ├── test_ui_baseline.py            # v0.3.0 UI SHA-256 回归测试
│   │   └── output/                        # 构建产物（不提交）
│   ├── Tailscale/                         # 安装脚本、configure-host-firewall.bat
│   ├── 启动.bat / 首次运行.bat            # 用户入口
│   ├── alist/  media/                     # 运行数据与媒体（不提交）
├── portable_config/
│   ├── mpv.conf  input_uosc.conf  profiles.conf
│   ├── scripts/        # Lua：syncplay_ui.lua 等；uosc/ 为构建期合并产物，不提交
│   ├── script-opts/    # 运行时选项（uosc_danmaku.conf 等）
│   └── syncplay/       # Python 集成层 + 全部单测
│       ├── watchparty_setup.py        # 首启向导、模式切换、防火墙步骤
│       ├── tailscale_integration.py
│       └── test_*.py
├── checksums/
├── .github/workflows/  # release.yml 等；推送 v* 标签触发构建
└── README.md  CHANGELOG.md
```

**运行目录（部署目标，不是源码）**：`D:\MPV-Duke-Enhanced-Extra\`
是 mpv 便携运行环境（mpv.exe、python.exe、portable_config/、WatchParty/ 启动脚本）。
源码改动只能发生在仓库，部署/构建再落到运行目录。不要把运行目录当作修改目标。

## 开发规范

1. **语言与文案**：文档、UI 提示、诊断信息用简体中文，面向普通用户；
   代码标识符与命令保持英文。诊断信息要给出可执行的下一步动作。
2. **编码**：`.bat` 一律 UTF-8 无 BOM + CRLF，开头 `chcp 65001`，注意
   `!` 延迟展开兼容；Python 输出显式 UTF-8；shell 脚本 LF。
3. **UI 基线**：v0.3.0 定制 uosc 共 29 个文件，SHA-256 被
   `test_ui_baseline.py` 钉住。改动 UI 必须是有意行为，并同步更新基线测试。
   `portable_config/scripts/uosc/` 与 `fonts/` 是构建期合并的上游工件，不提交。
4. **弹幕定制**：默认字号 35、显示范围 0.4，样式改动写回
   `script-opts/uosc_danmaku.conf`；更新上游弹幕插件时不得覆盖这些定制。
5. **打包纪律**：白名单复制文件；所有第三方二进制必须有版本 + SHA-256 pin；
   构建后运行审计与冒烟自检；产物带逐文件清单和 SHA-256。
6. **文档同步**：行为变更必须同步 README / CHANGELOG 的 Unreleased 段。
   发布版本号写入 CHANGELOG 顶层。

## 测试命令

在仓库根目录执行（Python 仅需标准库；可用运行目录的 `python.exe`）：

```powershell
# 全部单元测试（提交前必须通过）
python -m unittest discover -s portable_config\syncplay -p "test_*.py" -v

# v0.3.0 UI 基线回归（改动 UI / 打包时必须通过）
python WatchParty\ViewerPackage\test_ui_baseline.py -v

# macOS 启动脚本测试（改动 macos 模板 / 构建器时）
python WatchParty\ViewerPackage\test_macos_launcher.py -v

# 可选：真实 mpv IPC 集成测试
$env:SYNCPLAY_RUN_MPV_INTEGRATION = "1"
python portable_config\syncplay\test_mpv_ipc_real.py -v
```

改动打包器后：重新构建目标包，核对 `output/` 下 ZIP + `.sha256` +
`PACKAGE-CONTENTS.sha256` 清单。macOS 构建器改动先跑 `bash -n` 语法检查。

## 不允许事项

1. **不改运行目录**：`D:\MPV-Duke-Enhanced-Extra\` 下的 mpv.exe、python.exe、
   DLL、tailscale 等 运行时与部署副本不是源码；改动先进仓库。
2. **不提交运行态与私密数据**：`ADMIN_PASSWORD.txt`、`alist/data/`、
   `media/*`、`connection.json`、`output/`、`*.msi`、日志、缓存、
   `danmaku-history.json`（.gitignore 已覆盖，勿绕过）。
3. **不泄露凭据**：AList 管理员 Token、Cookie、Tailscale 登录凭据绝不写入
   观看者可见的配置、包内容或网络消息。
4. **不暴露公网**：不开启 Tailscale Funnel/Serve，不做路由器端口映射；
   AList 5244 的防火墙规则只放行 `100.64.0.0/10`。
5. **不破坏兼容性**：旧客户端基础同步、`syncplay.pl` 与 MiniServer 行为、
   Syncplay 播放/暂停/跳转/延迟补偿语义不得改变。
6. **不引入第三方 Python 依赖**（集成层仅标准库）；不把未审计的二进制
   塞进包里；不绕过打包审计。
7. **不用 `git add -A` / `git add .` / `git checkout .` /
   `git reset --hard`**：工作区可能存在其他 Agent 的未提交工作。
8. 不 force push、不 rebase 已推送的 main、不删除 `backup/*` 分支。

## API 规则

- **mpv IPC**：JSON IPC；属性写入必须带类型；`--alist-*` 等媒体地址参数只在
  `mpv_syncplay` 启动时读取一次——运行模式切换后必须结束并重启 mpv 客户端
  进程，面板需对此给出提示。
- **子进程输出契约**：setup 脚本 `--json` 模式下，进度日志走 stderr，stdout
  只保留最后一行 JSON，mpv 面板按此解析。新增脚本必须遵守同一契约。
- **AList**：仅匿名只读 `/media`，端口 5244，关闭签名，必须支持 HTTP Range
  206；房主地址只发 `http://100.x.x.x:5244` 形式；管理员 Token 不出房主机。
- **Syncplay**：`media_url` 自动载入仅在服务器声明房间隔离能力时启用；
  弹幕纯本地渲染，只读本机 `time-pos`，不通过 Syncplay 发送任何数据。
- **Tailscale**：优先使用随包 CLI；只走 Device Sharing 直连，不用 Serve/Funnel。
- **新增外部依赖或下载源**：必须固定版本 + SHA-256，并更新
  `THIRD_PARTY_NOTICES`；下载失败要给出可复制的替代命令。

## Git 规则

- 仓库唯一：`WatchParty/repo-latest`，主分支 `main`。
- 提交信息用 Conventional Commits（`fix:` / `feat:` / `ci:` / `docs:` /
  `fix(packaging):` 等），可中英混合，说明面向用户可读。
- 大改动开始前先建本地备份分支（先例：`backup/zcode-local-20260925`）。
- 提交前测试必须通过；只 `git add` 自己明确修改的文件。
- 发布：推送 `v*` 标签触发 CI 构建非 Windows-x64 包；Windows x64 包需从
  已有 64 位便携运行目录本机构建后上传；全部 Release 附件附同名 `.sha256`。

## 多 Agent 协作（ZCode / Codex / DeepSeek Harness）

- **Harness 可能正在同一工作区运行**。开工前：`git status` + 查看目标文件
  修改时间；发现数分钟内有非本人修改，先向用户确认分工，不要抢写。
- 一次只做一件已确认的事；完成并验证后尽快提交，缩短未提交窗口。
- 绝不覆盖/回滚他人的未提交修改；发现冲突（同文件不同方向改动）时停下
  报告，由用户裁决。
- 部署到运行目录（根 `portable_config/`、`WatchParty/*.bat`）属于构建发布
  行为：确认仓库侧已提交或已验证后再同步，并在回复中说明部署了哪些文件。
